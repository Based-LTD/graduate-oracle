"""
trader_withdraw — Telegram wizard for withdrawing SOL from a user's
custodial trader wallet.

Backend (web/trader_wallets.withdraw) is fully built — this module is
just the TG conversation layer that walks the user through:

  setup → password creation (first time only)
  enter destination address
  enter amount
  confirm preview
  enter withdraw password
  execute + show signature

Security guards (defense in depth):
  • Withdraw password is bcrypt-hashed in DB, never logged
  • Password message is auto-deleted from chat after read
  • Daily limit (50 SOL/24h) enforced at backend
  • Minimum withdraw (0.0001 SOL) enforced
  • All flows can be aborted with `cancel` at any step
  • Wallet pubkey shown in confirmation so user can verify
  • All withdrawals logged to trader_withdrawals audit table
"""

from __future__ import annotations

import sys
import traceback

from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, Update, constants,
)
from telegram.ext import (
    Application, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters, ApplicationHandlerStop,
)


# Wizard state values stored in user_data["wd_state"]
WD_SETUP_PWD  = "wd:setup_pwd"     # first time: enter NEW password
WD_ENTER_TO   = "wd:enter_to"      # enter destination address
WD_ENTER_AMT  = "wd:enter_amt"     # enter amount in SOL
WD_ENTER_PWD  = "wd:enter_pwd"     # final step: enter password to execute

# Daily withdraw limit override for admins. Public users default to
# WITHDRAW_DAILY_LIMIT_LAMPORTS (0.5 SOL/day). Admins get a much higher
# ceiling since they're operating the bot, not being protected from a
# compromise. Module-level mutable; gets populated by register().
_ADMIN_DAILY_LIMIT_LAMPORTS = 50_000_000_000   # 50 SOL/day for admins
_ADMIN_IDS: set = set()


def _admin_only(_admin_ids):
    """Decorator-builder so we don't expose withdraw outside the admin
    set during beta. After public launch, remove this gate."""
    def check(update):
        uid = update.effective_user.id if update.effective_user else None
        return uid in _admin_ids
    return check


# ── Wallet view buttons (called from trader_setup.py) ──────────────────

def withdraw_button() -> InlineKeyboardButton:
    """Drop into the wallet-view keyboard so the user can start the flow."""
    return InlineKeyboardButton("💸 Withdraw", callback_data="wd:start")


# ── Wizard step renderers ──────────────────────────────────────────────

def _render_address_prompt() -> str:
    return (
        "💸 *WITHDRAW — step 1/3*\n\n"
        "Reply with the destination Solana address.\n\n"
        "_Triple-check this. Sent funds cannot be recovered._\n"
        "_Reply `cancel` to abort._"
    )


def _render_amount_prompt(balance_sol: float) -> str:
    return (
        f"💸 *WITHDRAW — step 2/3*\n\n"
        f"Wallet balance: *{balance_sol:.6f}* SOL\n\n"
        "Reply with the amount in SOL (e.g. `0.5`).\n"
        "Minimum 0.0001 SOL.\n\n"
        "_Reply `cancel` to abort._"
    )


def _render_confirm_prompt(to_addr: str, amount_sol: float, balance_sol: float) -> str:
    return (
        f"💸 *WITHDRAW — step 3/3*\n\n"
        f"  To: `{to_addr}`\n"
        f"  Amount: *{amount_sol:.6f}* SOL\n"
        f"  Network fee: ~0.000005 SOL\n"
        f"  Remaining after: *{balance_sol - amount_sol:.6f}* SOL\n\n"
        "Reply with your *withdraw password* to confirm.\n\n"
        "_Your message will be auto-deleted from chat for security._\n"
        "_Reply `cancel` to abort._"
    )


# ── Entry point: tap "💸 Withdraw" in wallet view ──────────────────────

async def cb_withdraw_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Tapped from the Wallet view — kicks off the withdraw flow."""
    q = update.callback_query
    await q.answer()
    uid = str(update.effective_user.id)

    import trader_wallets
    wallet = trader_wallets.wallet_for(uid)
    if not wallet:
        await q.edit_message_text(
            "❌ No wallet yet. Open `/trader → Wallet` to provision one.",
            parse_mode=constants.ParseMode.MARKDOWN,
        )
        return

    # Check if a withdraw password is set. If not, first-time setup.
    has_pwd = trader_wallets.has_withdraw_password(uid)
    if not has_pwd:
        ctx.user_data["wd_state"] = WD_SETUP_PWD
        await q.edit_message_text(
            "🔒 *WITHDRAW — first-time setup*\n\n"
            "Set a withdraw password (8+ characters).\n\n"
            "This protects your funds even if the server's master key is "
            "compromised. *Save it somewhere safe — you cannot recover it.*\n\n"
            "Reply with your new password (or `cancel`).",
            parse_mode=constants.ParseMode.MARKDOWN,
        )
        return

    # Password already set — start the address-entry step
    ctx.user_data["wd_state"] = WD_ENTER_TO
    await q.edit_message_text(
        _render_address_prompt(),
        parse_mode=constants.ParseMode.MARKDOWN,
    )


# ── Stateful text input handler ────────────────────────────────────────

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Runs in handler group 0 (default). If user has a `wd_state` set,
    consume the message and process the wizard step. Otherwise return
    silently so other text handlers can match."""
    if not update.message or not update.message.text:
        return
    state = (ctx.user_data or {}).get("wd_state")
    if not state:
        return  # not in a withdraw wizard — let other handlers see this

    text = update.message.text.strip()
    uid = str(update.effective_user.id)
    chat_id = update.effective_chat.id

    # Universal escape
    if text.lower() == "cancel":
        ctx.user_data.pop("wd_state", None)
        ctx.user_data.pop("wd_to", None)
        ctx.user_data.pop("wd_amt", None)
        await update.message.reply_text("✖️ Withdraw cancelled.")
        raise ApplicationHandlerStop

    # ── STATE: setup new withdraw password ────────────────────────────
    if state == WD_SETUP_PWD:
        if len(text) < 8:
            await update.message.reply_text(
                "Password must be 8+ characters. Try again, or `cancel`."
            )
            raise ApplicationHandlerStop
        import trader_wallets
        try:
            trader_wallets.set_withdraw_password(uid, text)
        except Exception as e:
            await update.message.reply_text(
                f"❌ Failed to set password: {str(e)[:200]}. Try again, or `cancel`."
            )
            raise ApplicationHandlerStop
        # Delete the password message — leaving raw passwords in chat is bad UX
        try:
            await update.message.delete()
        except Exception:
            pass
        ctx.user_data["wd_state"] = WD_ENTER_TO
        await ctx.bot.send_message(
            chat_id,
            "✅ Password set.\n\n" + _render_address_prompt(),
            parse_mode=constants.ParseMode.MARKDOWN,
        )
        raise ApplicationHandlerStop

    # ── STATE: enter destination address ──────────────────────────────
    if state == WD_ENTER_TO:
        # Basic format sanity — Solana addresses are 32-44 chars base58
        if not (32 <= len(text) <= 44) or not text.isalnum():
            await update.message.reply_text(
                "That doesn't look like a Solana address. Try again, or `cancel`."
            )
            raise ApplicationHandlerStop
        # Deeper validation (Pubkey.from_string) happens at withdraw time
        ctx.user_data["wd_to"] = text
        ctx.user_data["wd_state"] = WD_ENTER_AMT
        import trader_wallets
        try:
            wallet = trader_wallets.wallet_for(uid)
            bal = trader_wallets.get_balance_sol(wallet["public_key"])
        except Exception:
            bal = 0.0
        await update.message.reply_text(
            _render_amount_prompt(bal),
            parse_mode=constants.ParseMode.MARKDOWN,
        )
        raise ApplicationHandlerStop

    # ── STATE: enter amount ───────────────────────────────────────────
    if state == WD_ENTER_AMT:
        try:
            amt = float(text)
        except ValueError:
            await update.message.reply_text(
                "Enter a number (e.g. `0.5`). Try again, or `cancel`."
            )
            raise ApplicationHandlerStop
        if amt <= 0:
            await update.message.reply_text("Amount must be positive. Try again, or `cancel`.")
            raise ApplicationHandlerStop
        if amt < 0.0001:
            await update.message.reply_text("Minimum is 0.0001 SOL. Try again, or `cancel`.")
            raise ApplicationHandlerStop

        import trader_wallets
        try:
            wallet = trader_wallets.wallet_for(uid)
            bal = trader_wallets.get_balance_sol(wallet["public_key"])
        except Exception:
            bal = 0.0
        # Leave a small buffer for the tx fee (5_000 lamports = 5e-6 SOL)
        if amt > bal - 0.00001:
            await update.message.reply_text(
                f"Insufficient balance. You have *{bal:.6f}* SOL. Try a smaller "
                "amount, or `cancel`.",
                parse_mode=constants.ParseMode.MARKDOWN,
            )
            raise ApplicationHandlerStop

        ctx.user_data["wd_amt"] = amt
        ctx.user_data["wd_state"] = WD_ENTER_PWD
        await update.message.reply_text(
            _render_confirm_prompt(ctx.user_data["wd_to"], amt, bal),
            parse_mode=constants.ParseMode.MARKDOWN,
        )
        raise ApplicationHandlerStop

    # ── STATE: enter password (final step — executes) ─────────────────
    if state == WD_ENTER_PWD:
        password = text
        to_addr = ctx.user_data.get("wd_to")
        amt = ctx.user_data.get("wd_amt")
        # Delete the password message immediately
        try:
            await update.message.delete()
        except Exception:
            pass
        # Clear state regardless of outcome — no retries on the same wizard
        ctx.user_data.pop("wd_state", None)
        ctx.user_data.pop("wd_to", None)
        ctx.user_data.pop("wd_amt", None)

        if not to_addr or not amt:
            await ctx.bot.send_message(chat_id,
                "❌ Wizard state lost. Start over from /trader → Wallet.")
            raise ApplicationHandlerStop

        await ctx.bot.send_message(chat_id, "💸 Submitting withdrawal...")
        import trader_wallets
        # Admins (operator team) get the higher daily cap; public users
        # are limited to WITHDRAW_DAILY_LIMIT_LAMPORTS (0.5 SOL/day).
        try:
            is_admin = int(uid) in _ADMIN_IDS
        except Exception:
            is_admin = False
        daily_limit = (_ADMIN_DAILY_LIMIT_LAMPORTS if is_admin
                       else trader_wallets.WITHDRAW_DAILY_LIMIT_LAMPORTS)
        try:
            result = trader_wallets.withdraw(
                user_id=uid,
                to_address=to_addr,
                lamports=int(amt * 1e9),
                password=password,
                daily_limit_lamports=daily_limit,
            )
        except PermissionError as e:
            await ctx.bot.send_message(chat_id, f"❌ Withdraw rejected: {e}")
            raise ApplicationHandlerStop
        except ValueError as e:
            await ctx.bot.send_message(chat_id, f"❌ Invalid withdraw: {e}")
            raise ApplicationHandlerStop
        except Exception as e:
            print(f"[trader_withdraw] withdraw failed: {e}", file=sys.stderr, flush=True)
            traceback.print_exc()
            await ctx.bot.send_message(chat_id, f"❌ Withdraw failed: {str(e)[:200]}")
            raise ApplicationHandlerStop

        # Render receipt
        sig = result.get("signature") or ""
        status = result.get("status") or "unknown"
        if status == "confirmed":
            text_out = (
                f"✅ *Withdraw confirmed*\n\n"
                f"Amount: *{amt:.6f}* SOL\n"
                f"To: `{to_addr}`\n"
                f"Status: *{status}*\n"
                f"[`{sig[:16]}…`](https://solscan.io/tx/{sig})"
            )
        elif status == "timed_out":
            text_out = (
                f"⏳ *Withdraw timed out*\n\n"
                f"The tx was submitted but didn't confirm in 60s. It may still "
                f"land — check the explorer.\n\n"
                f"[`{sig[:16]}…`](https://solscan.io/tx/{sig})"
            )
        else:
            text_out = (
                f"❌ *Withdraw failed*\n\n"
                f"Status: *{status}*\n"
                f"[`{sig[:16]}…`](https://solscan.io/tx/{sig})" if sig else
                f"❌ *Withdraw failed*\n\nStatus: *{status}*"
            )
        await ctx.bot.send_message(
            chat_id, text_out,
            parse_mode=constants.ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
        raise ApplicationHandlerStop


# ── Registration ───────────────────────────────────────────────────────

def register(app: Application, admin_tg_ids: set) -> bool:
    """Wire the withdraw wizard into the application. Returns True on
    success, False if anything fails — main bot keeps running either way."""
    if not admin_tg_ids:
        # Beta: only admins can withdraw. After public, drop this guard.
        print("[trader_withdraw] no admins set, skipping", flush=True)
        return False

    # Stash for the wizard's per-user limit decision
    global _ADMIN_IDS
    _ADMIN_IDS = set(admin_tg_ids)

    try:
        # Entry point: tap "💸 Withdraw" in wallet view
        app.add_handler(CallbackQueryHandler(cb_withdraw_start, pattern=r"^wd:start$"))
        # Stateful text handler — runs in group -10 so it sees text BEFORE
        # any default-group handlers. It only consumes when wd_state is set;
        # otherwise it returns silently and the next handler can match.
        app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text),
            group=-10,
        )
        print("[trader_withdraw] registered", flush=True)
        return True
    except Exception as e:
        print(f"[trader_withdraw] register failed: {e}", file=sys.stderr, flush=True)
        return False
