"""
bot/trader_commands.py — operator-gated TG commands for the trader.

This module is INTENTIONALLY isolated from the rest of bot/main.py.
Trader bugs must not crash the alert dispatcher or other prod paths.
Two guarantees:

  1. EVERY handler is gated by `_require_admin(update)` — non-admins
     get silence (no leak that the commands exist), admins get the
     real flow.
  2. EVERY handler wraps the trader call in try/except. Any exception
     is logged and the user sees a friendly message — never a Python
     traceback in TG.

To enable: set TRADER_ENABLED=1 and ADMIN_TG_IDS=<telegram_id>[,...] on
the bot's runtime. Without TRADER_ENABLED, the registration is a no-op
and these commands won't be reachable even by admins (kill-switch).

Public API used by bot/main.py:
    trader_commands.register(app, admin_ids: set[int])

That call:
  - returns immediately if TRADER_ENABLED != "1"
  - registers /buy /sell /portfolio /balance /deposit /settings /tradehelp
  - registers the buy-button callback handler
  - returns True if registered, False if skipped
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path
from typing import Optional

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    constants,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# Make web/ importable from the bot — trader modules live there
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
sys.path.insert(0, str(_REPO / "web"))

# Lazy-import inside handlers so import errors at boot don't kill the bot.
# These are only resolved when the operator actually runs a /buy etc.

_admin_ids: set[int] = set()


def is_enabled() -> bool:
    return os.environ.get("TRADER_ENABLED", "").strip() == "1"


def _is_admin(update: Update) -> bool:
    u = update.effective_user
    return bool(u and u.id in _admin_ids)


async def _require_admin(update: Update) -> bool:
    """Gate for trader commands. Two checks:
      1. User is in ADMIN_TG_IDS (beta only — drop this gate when public)
      2. User has accepted the current TOS version

    Returns False silently for non-admins (we don't want to advertise
    these commands exist to non-operators during beta).

    For admins who haven't accepted TOS, we DO reply with the prompt
    so they can complete the gate. They already know the commands exist."""
    if not _is_admin(update):
        return False
    # TOS check — applies even to admins. Bumping TOS_VERSION forces
    # re-acceptance on the next interaction.
    try:
        import tos_gate
        tg_id = update.effective_user.id
        if not tos_gate.is_accepted(tg_id):
            # Render the prompt inline so they can accept and retry.
            msg = update.message or (update.callback_query and update.callback_query.message)
            if msg:
                try:
                    await msg.reply_text(
                        tos_gate.TOS_TEXT,
                        parse_mode=constants.ParseMode.MARKDOWN,
                        reply_markup=tos_gate.tos_keyboard(),
                        disable_web_page_preview=True,
                    )
                except Exception as e:
                    print(f"[trader_commands] TOS prompt failed: {e}", flush=True)
            return False
    except Exception as e:
        print(f"[trader_commands] TOS gate check failed: {e}", flush=True)
        # Fail-closed — better to block than skip the legal record.
        return False
    return True


def _operator_user_id(update: Update) -> str:
    """The trader uses a stable user_id string per wallet. We use the
    operator's telegram_id so each admin has their own wallet."""
    return str(update.effective_user.id)


# ── Formatting helpers ──────────────────────────────────────────────────

def _fmt_sol(lamports: Optional[int]) -> str:
    if lamports is None:
        return "—"
    return f"{lamports/1e9:.4f}"


def _fmt_pct(frac: Optional[float]) -> str:
    if frac is None:
        return "—"
    return f"{frac*100:+.1f}%"


# pump.fun mints use 6 decimals (the SPL Token convention for these
# launches). Token amounts from Jupiter come back in raw smallest units;
# we divide by 1e6 to display whole tokens. The variable could be made
# dynamic by reading the mint's decimals, but 6 is universal for pump.fun.
PUMP_TOKEN_DECIMALS = 6


def _fmt_tokens(raw: int) -> str:
    """Render a raw-unit token amount as a human-readable whole-token count.
    Adds K/M/B suffix for large amounts so the receipt stays readable."""
    if raw is None:
        return "?"
    whole = raw / (10 ** PUMP_TOKEN_DECIMALS)
    if whole >= 1_000_000_000:
        return f"{whole/1_000_000_000:.2f}B"
    if whole >= 1_000_000:
        return f"{whole/1_000_000:.2f}M"
    if whole >= 1_000:
        return f"{whole/1_000:.2f}K"
    return f"{whole:,.2f}"


def _fmt_mcap(mcap_lamports: int | None, sol_usd: float | None = None) -> str:
    """Render market cap as 'X.YZ SOL ($N,NNN)'. SOL display rounds to
    2dp; USD uses K/M for compactness."""
    if mcap_lamports is None or mcap_lamports <= 0:
        return "—"
    sol = mcap_lamports / 1e9
    if sol_usd and sol_usd > 0:
        usd = sol * sol_usd
        if usd >= 1_000_000:
            usd_str = f"${usd/1_000_000:.2f}M"
        elif usd >= 1_000:
            usd_str = f"${usd/1_000:.1f}K"
        else:
            usd_str = f"${usd:.0f}"
        return f"{sol:.2f} SOL ({usd_str})"
    return f"{sol:.2f} SOL"


def _get_sol_usd_cached() -> float | None:
    """Look up live SOL/USD via web/jupiter_price.get_sol_usd. That module
    has its own in-process cache; we just delegate. Returns None on any
    failure so the receipt renders without USD."""
    try:
        import jupiter_price
        return jupiter_price.get_sol_usd()
    except Exception:
        return None


def _format_buy_receipt(r: dict) -> str:
    """Markdown receipt for a successful buy."""
    mint = r["mint"]
    short = mint[:6] + "…" + mint[-4:]
    sol = r["sol"]
    tokens_raw = r.get("expected_tokens_out", 0)
    phase = r["phase"]
    sig = r.get("buy_signature") or ""
    sig_link = f"[`{sig[:12]}…`](https://solscan.io/tx/{sig})" if sig else "—"
    fee = (r.get("fee") or {}).get("total_fee_lamports", 0)
    pid = r.get("position_id")

    # Market-cap snapshot at entry — re-read the position row since the
    # orchestrator stamps it AFTER the result envelope is built.
    mc_line = ""
    try:
        import trader_positions
        sol_usd = _get_sol_usd_cached()
        row = trader_positions.get_position(pid) if pid else None
        mcap = (row or {}).get("entry_mcap_lamports")
        if mcap:
            mc_line = f"📈 MC at entry: *{_fmt_mcap(mcap, sol_usd)}*\n"
    except Exception:
        pass

    return (
        f"✅ *Bought* `{short}`\n"
        f"`{mint}`\n\n"
        f"💰 Spent: *{sol:.4f}* SOL"
        + (f"  (+ {fee/1e9:.5f} fee)" if fee else "") + "\n"
        f"🪙 Got:   *{_fmt_tokens(tokens_raw)}* tokens\n"
        f"{mc_line}"
        f"📍 Position #{pid}\n"
        f"📊 Phase: _{phase}_  ·  {sig_link}"
    )


def _format_sell_receipt(r: dict) -> str:
    """Honest sell receipt: shows cost basis, gross received, fees paid,
    and the NET PnL with explicit win/loss indicator. Previous version
    showed only gross received → users mistook small wins for losses
    (and vice-versa) once the 1% × 2 fee skim was applied."""
    mint = r["mint"]
    short = mint[:6] + "…" + mint[-4:]
    tokens_raw = r.get("tokens_sold", 0)
    sol_out = r.get("expected_sol_out_lamports", 0) / 1e9
    pid = r["position_id"]
    new_status = r.get("new_status", "?")
    sig = r.get("sell_signature") or ""
    sig_link = f"[`{sig[:12]}…`](https://solscan.io/tx/{sig})" if sig else "—"
    pct = int(r.get("sell_pct", 1) * 100)

    # Honest accounting block + MC snapshots. Pull the just-written
    # position row so we see all the final stored values.
    pnl_block = ""
    mc_block = ""
    wallet_block = ""
    try:
        import trader_positions
        row = trader_positions.get_position(pid)
        if row and row.get("status") == "sold":
            buy = row["buy_sol_lamports"] / 1e9
            sell_total = (row.get("sell_sol_lamports") or 0) / 1e9
            fees = ((row.get("buy_fee_lamports") or 0) +
                    (row.get("sell_fee_lamports") or 0)) / 1e9
            net = (row.get("net_pnl_lamports") or 0) / 1e9
            net_pct = (net / buy * 100) if buy else 0
            sign = "🟢" if net >= 0 else "🔴"
            verdict = "*PROFIT*" if net >= 0 else "*LOSS*"
            pnl_block = (
                f"\n\n📊 *PnL on this trade:*\n"
                f"  Cost basis: *{buy:.4f}* SOL\n"
                f"  Got back:   *{sell_total:.4f}* SOL\n"
                + (f"  Fees:      −*{fees:.5f}* SOL\n" if fees > 0 else "")
                + f"  ───────────────────\n"
                f"  {sign} Net: *{net:+.4f}* SOL  ({net_pct:+.2f}%)  ← {verdict}"
            )

            # Wallet-truth: ACTUAL on-chain SOL delta for the buy + sell
            # txs, minus the recorded fee-skim. Captures tx fees, Jito
            # tip, compute-budget, ATA rent, and slippage — none of which
            # are in the swap-leg math above. Best-effort: never blocks.
            try:
                import wallet_truth
                buy_sig = row.get("buy_signature") or ""
                sell_sig = row.get("sell_signature") or ""
                buy_delta = wallet_truth.fetch_payer_sol_delta_lamports(buy_sig)
                sell_delta = wallet_truth.fetch_payer_sol_delta_lamports(sell_sig)
                if buy_delta is not None and sell_delta is not None:
                    fee_skim_total = ((row.get("buy_fee_lamports") or 0) +
                                      (row.get("sell_fee_lamports") or 0))
                    # buy_delta is negative (wallet lost SOL on the buy)
                    # sell_delta is positive (wallet gained SOL on the sell)
                    # fee_skim_total is positive (already left wallet too)
                    wallet_net_lamports = buy_delta + sell_delta - fee_skim_total
                    wallet_net_sol = wallet_net_lamports / 1e9
                    wallet_net_pct = (wallet_net_sol / buy * 100) if buy else 0
                    sign_w = "🟢" if wallet_net_lamports >= 0 else "🔴"
                    # buy_delta is already negative, no extra minus needed
                    wallet_block = (
                        f"\n\n💼 *Wallet-truth:*\n"
                        f"  Wallet out (buy):  *{buy_delta/1e9:+.5f}* SOL\n"
                        f"  Wallet in (sell):  *{sell_delta/1e9:+.5f}* SOL\n"
                        f"  Fee skim:         −*{fee_skim_total/1e9:.5f}* SOL\n"
                        f"  ───────────────────\n"
                        f"  {sign_w} Wallet net: *{wallet_net_sol:+.5f}* SOL  "
                        f"({wallet_net_pct:+.2f}%)"
                    )
            except Exception as we:
                print(f"[trader_commands] wallet_truth failed: {we}", flush=True)

            # MC snapshots — only shown when both entry + exit are stored
            entry_mc = row.get("entry_mcap_lamports")
            exit_mc  = row.get("exit_mcap_lamports")
            if entry_mc and exit_mc:
                sol_usd = _get_sol_usd_cached()
                mc_change = ((exit_mc - entry_mc) / entry_mc * 100) if entry_mc else 0
                mc_arrow = "📈" if mc_change >= 0 else "📉"
                mc_block = (
                    f"\n\n📊 *Market cap:*\n"
                    f"  Entry: *{_fmt_mcap(entry_mc, sol_usd)}*\n"
                    f"  Exit:  *{_fmt_mcap(exit_mc, sol_usd)}*  "
                    f"{mc_arrow} *{mc_change:+.1f}%*"
                )
    except Exception:
        pass

    return (
        f"✅ *Sold {pct}%* of `{short}`\n"
        f"🪙 Tokens:   *{_fmt_tokens(tokens_raw)}*\n"
        f"💰 Received: *{sol_out:.4f}* SOL  (pre-fees)\n"
        f"📍 Position #{pid} → _{new_status}_\n"
        f"{sig_link}"
        f"{mc_block}"
        f"{pnl_block}"
        f"{wallet_block}"
    )


def _kb_position_actions(pid: int) -> InlineKeyboardMarkup:
    """The sell-button row used on buy receipts + portfolio rows."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Sell 25%", callback_data=f"ts:{pid}:25"),
        InlineKeyboardButton("Sell 50%", callback_data=f"ts:{pid}:50"),
        InlineKeyboardButton("Sell ALL", callback_data=f"ts:{pid}:100"),
    ]])


def _format_portfolio(summary: dict) -> str:
    n = summary["n_open"]
    if n == 0:
        return "📊 *Portfolio*\n\n_No open positions._\n\nTap a buy button on an alert, or `/buy 0.05 <mint>`"
    head_pnl = summary["total_unrealized_pnl_lamports"] / 1e9
    head_pct = summary["total_unrealized_pnl_pct"] * 100
    head_cost = summary["total_cost_basis_lamports"] / 1e9
    head_val = summary["total_current_value_lamports"] / 1e9
    out = [
        f"📊 *Portfolio* — {n} open ({summary['n_with_valuation']} priced)",
        f"💵 Cost: *{head_cost:.4f}* SOL  ·  Now: *{head_val:.4f}* SOL",
        f"📈 Unrealized: *{head_pnl:+.4f}* SOL  ({head_pct:+.1f}%)",
        "",
    ]
    for p in summary["positions"][:15]:
        mint_short = p["mint"][:6] + "…" + p["mint"][-4:]
        pid = p["id"]
        cost = p["buy_sol_lamports"] / 1e9
        if p["current_sol_value_lamports"] is None:
            out.append(f"`#{pid}` {mint_short}  ·  *{cost:.4f}* SOL  ·  _no quote_")
        else:
            now = p["current_sol_value_lamports"] / 1e9
            pnl = p["unrealized_pnl_lamports"] / 1e9
            pct = p["unrealized_pnl_pct"] * 100
            arrow = "📈" if pnl > 0 else "📉"
            out.append(
                f"`#{pid}` {mint_short}  {arrow} *{pct:+.0f}%*  ·  "
                f"{cost:.3f}→{now:.3f} SOL ({pnl:+.4f})"
            )
    return "\n".join(out)


# ── Argument parser ────────────────────────────────────────────────────

def _parse_overrides(args: list[str]) -> dict:
    """Parse kwarg-style overrides: tp=2x sl=40 tsl=30 be=20.
    Accepts shorthand 'x' suffix on TP (2x → +100%, 5x → +400%).
    Returns dict of {tp_ladder?, sl_pct?, tsl_pct?, breakeven_pct?}."""
    out: dict = {}
    for arg in args:
        if "=" not in arg:
            continue
        key, val = arg.split("=", 1)
        key = key.strip().lower()
        val = val.strip().lower()
        try:
            if key == "tp":
                # 2x → 100% gain → sell 100% at that level (single-rung ladder)
                if val.endswith("x"):
                    mult = float(val[:-1])
                    pct = int((mult - 1) * 100)
                else:
                    pct = int(float(val))
                out["tp_ladder"] = [{"pct": pct, "sell_pct": 100}]
            elif key == "sl":
                # User says "sl=40" meaning -40%
                out["sl_pct"] = -abs(float(val))
            elif key == "tsl":
                out["tsl_pct"] = abs(float(val))
            elif key == "be":
                out["breakeven_pct"] = abs(float(val))
        except (ValueError, IndexError):
            continue
    return out


# ── Command handlers ───────────────────────────────────────────────────

async def cmd_tradehelp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    text = (
        "*🤖 GRADUATE TRADER*\n\n"
        "*The easy way:*\n"
        "`/setup` — interactive button menu to configure everything\n\n"
        "*Trading:*\n"
        "`/portfolio` — open positions with live PnL + Sell buttons\n"
        "`/balance` — wallet SOL\n"
        "`/deposit` — wallet pubkey for funding\n"
        "`/closeall` — sell every open position (panic close)\n\n"
        "*Power-user CLI (optional):*\n"
        "`/buy <sol> <mint> [tp=2x sl=40 tsl=30 be=20]`\n"
        "`/sell <position_id> [pct]`\n"
        "`/settings tp=2x sl=40 ...`\n\n"
        "_Tap [Buy] under any ACT/WATCH alert to one-tap buy with your "
        "configured presets + auto-exits._"
    )
    await update.message.reply_text(text, parse_mode=constants.ParseMode.MARKDOWN)


async def cmd_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    if len(ctx.args) < 2:
        await update.message.reply_text(
            "usage: `/buy <sol> <mint> [tp=2x] [sl=40] [tsl=30] [be=20]`",
            parse_mode=constants.ParseMode.MARKDOWN,
        )
        return
    try:
        sol = float(ctx.args[0])
        mint = ctx.args[1].strip()
        overrides = _parse_overrides(ctx.args[2:])
    except ValueError:
        await update.message.reply_text("Bad sol amount. Try: `/buy 0.05 <mint>`",
                                        parse_mode=constants.ParseMode.MARKDOWN)
        return
    await _run_buy(update, sol, mint, overrides)


async def _run_buy(update: Update, sol: float, mint: str, overrides: dict,
                   signal_source: str = "tg_manual"):
    """Shared logic between /buy and the inline buy-button callback."""
    user_id = _operator_user_id(update)
    try:
        import trader_orchestrator
        result = trader_orchestrator.buy(
            user_id, mint, sol,
            signal_source=signal_source, live=True,
            **overrides,
        )
        pid = result.get("position_id")
        kb = _kb_position_actions(pid) if pid else None
        await update.message.reply_text(
            _format_buy_receipt(result),
            parse_mode=constants.ParseMode.MARKDOWN,
            disable_web_page_preview=True,
            reply_markup=kb,
        )
    except Exception as e:
        # OrchestratorError + anything else. Try to use user_facing_msg.
        msg = getattr(e, "user_facing_msg", None) or "Trade failed. Check logs."
        await update.message.reply_text(f"❌ {msg}")
        print(f"[trader_commands] /buy failed: {e}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)


async def cmd_sell(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    if not ctx.args:
        await update.message.reply_text(
            "usage: `/sell <position_id> [pct]` (default 100%)",
            parse_mode=constants.ParseMode.MARKDOWN,
        )
        return
    try:
        pid = int(ctx.args[0])
        pct = float(ctx.args[1]) / 100 if len(ctx.args) > 1 else 1.0
        if not (0 < pct <= 1.0):
            raise ValueError("pct must be 1-100")
    except ValueError:
        await update.message.reply_text("Bad args. Try: `/sell 42` or `/sell 42 50`",
                                        parse_mode=constants.ParseMode.MARKDOWN)
        return
    user_id = _operator_user_id(update)
    try:
        import trader_orchestrator
        result = trader_orchestrator.sell(user_id, pid, sell_pct=pct, live=True)
        await update.message.reply_text(
            _format_sell_receipt(result),
            parse_mode=constants.ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
    except Exception as e:
        msg = getattr(e, "user_facing_msg", None) or str(e)[:200]
        await update.message.reply_text(f"❌ {msg}")
        print(f"[trader_commands] /sell failed: {e}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)


async def cmd_portfolio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    user_id = _operator_user_id(update)
    try:
        import trader_portfolio
        summary = trader_portfolio.portfolio_summary(user_id)
        # Header
        await update.message.reply_text(
            _format_portfolio(summary),
            parse_mode=constants.ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
        # Per-position sell buttons — separate message per position so
        # the buttons stay attached to that specific row. Keeps it scrollable
        # and avoids the 8-buttons-per-row Telegram cap.
        for pos in summary["positions"][:10]:
            pid = pos["id"]
            mint_short = pos["mint"][:6] + "…" + pos["mint"][-4:]
            cost = pos["buy_sol_lamports"] / 1e9
            if pos["current_sol_value_lamports"] is not None:
                now = pos["current_sol_value_lamports"] / 1e9
                pnl = pos["unrealized_pnl_lamports"] / 1e9
                pct = pos["unrealized_pnl_pct"] * 100
                arrow = "📈" if pnl > 0 else "📉"
                line = (f"`#{pid}` {mint_short}  {arrow} *{pct:+.0f}%*  "
                        f"({cost:.3f}→{now:.3f} SOL)")
            else:
                line = f"`#{pid}` {mint_short}  _no quote_"
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("Sell 25%", callback_data=f"ts:{pid}:25"),
                InlineKeyboardButton("Sell 50%", callback_data=f"ts:{pid}:50"),
                InlineKeyboardButton("Sell ALL", callback_data=f"ts:{pid}:100"),
            ]])
            await update.message.reply_text(
                line, parse_mode=constants.ParseMode.MARKDOWN,
                reply_markup=kb, disable_web_page_preview=True,
            )
        if summary["n_open"] > 10:
            await update.message.reply_text(
                f"_…+{summary['n_open']-10} more not shown. Use /sell <id> manually._",
                parse_mode=constants.ParseMode.MARKDOWN,
            )
    except Exception as e:
        await update.message.reply_text(f"❌ Portfolio failed: {str(e)[:200]}")
        print(f"[trader_commands] /portfolio failed: {e}", file=sys.stderr, flush=True)


async def cmd_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    user_id = _operator_user_id(update)
    try:
        import trader_wallets
        wallet = trader_wallets.wallet_for(user_id)
        if wallet is None:
            await update.message.reply_text(
                "No wallet yet. Use `/deposit` to provision one.",
                parse_mode=constants.ParseMode.MARKDOWN,
            )
            return
        bal_sol = trader_wallets.get_balance_sol(wallet["public_key"])
        await update.message.reply_text(
            f"💰 *{bal_sol:.6f} SOL*\n\n`{wallet['public_key']}`",
            parse_mode=constants.ParseMode.MARKDOWN,
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Balance failed: {str(e)[:200]}")
        print(f"[trader_commands] /balance failed: {e}", file=sys.stderr, flush=True)


async def cmd_deposit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _require_admin(update):
        return
    user_id = _operator_user_id(update)
    try:
        import trader_wallets
        wallet = trader_wallets.get_or_create_wallet(user_id)
        pubkey = wallet["public_key"]
        await update.message.reply_text(
            f"📥 *Your trader wallet*\n\n"
            f"`{pubkey}`\n\n"
            f"Send SOL here to fund trades. Check balance with `/balance`.\n"
            f"_The private key is encrypted on the server — never type it here._",
            parse_mode=constants.ParseMode.MARKDOWN,
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Deposit failed: {str(e)[:200]}")
        print(f"[trader_commands] /deposit failed: {e}", file=sys.stderr, flush=True)


async def cmd_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """View or set per-user auto-exit defaults."""
    if not await _require_admin(update):
        return
    user_id = _operator_user_id(update)
    try:
        import trader_positions
        if ctx.args:
            overrides = _parse_overrides(ctx.args)
            trader_positions.set_user_settings(user_id, **overrides)
        s = trader_positions.get_user_settings(user_id)
        ladder_lines = "\n".join(
            f"  TP{i+1}: at +{r['pct']:.0f}% sell {r['sell_pct']:.0f}%"
            for i, r in enumerate(s["tp_ladder"])
        )
        text = (
            f"⚙ *Your default auto-exit rules*\n\n"
            f"{ladder_lines}\n"
            f"  SL:  {s['sl_pct']:+.0f}%\n"
            f"  TSL: {s['tsl_pct']:.0f}% off high\n"
            f"  BE:  +{s['breakeven_pct']:.0f}% (flips SL to entry)\n\n"
            f"_Change with: `/settings tp=2x sl=40 tsl=30 be=20`_\n"
            f"_Per-buy override via `/buy 0.05 <mint> tp=5x sl=60`_"
        )
        await update.message.reply_text(text, parse_mode=constants.ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ Settings failed: {str(e)[:200]}")
        print(f"[trader_commands] /settings failed: {e}", file=sys.stderr, flush=True)


# ── Inline buy-button callback ─────────────────────────────────────────

# Callback data format: "tb:<sol>:<mint>" — short to fit Telegram's 64-byte
# data limit. "tb" = trader buy.
CALLBACK_PREFIX_BUY  = "tb:"
CALLBACK_PREFIX_SELL = "ts:"   # ts:<position_id>:<pct>  (pct ∈ 25/50/100)


def build_buy_buttons(mint: str, amounts_sol: list[float] = None,
                       user_id: Optional[str | int] = None) -> InlineKeyboardMarkup:
    """Build the inline keyboard with buy buttons for `mint`.

    If `user_id` is provided and no explicit `amounts_sol`, we read the
    user's saved buy_presets_sol from trader_user_settings. Falls back
    to the package default [0.01, 0.05, 0.25] if the lookup fails
    (which means alerts keep working even if the trader DB is down).
    """
    if amounts_sol is None and user_id is not None:
        try:
            import trader_positions
            s = trader_positions.get_user_settings(user_id)
            amounts_sol = s.get("buy_presets_sol") or None
        except Exception:
            amounts_sol = None
    if amounts_sol is None:
        amounts_sol = [0.01, 0.05, 0.25]
    row = []
    for a in amounts_sol:
        row.append(InlineKeyboardButton(
            f"Buy {a} SOL",
            callback_data=f"{CALLBACK_PREFIX_BUY}{a}:{mint}",
        ))
    return InlineKeyboardMarkup([row])


async def cb_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Callback handler for inline buy buttons."""
    query = update.callback_query
    if not query:
        return
    # Acknowledge the tap so the spinner stops
    try:
        await query.answer()
    except Exception:
        pass

    if not _is_admin(update):
        # Silent — don't reveal the commands exist
        return

    data = (query.data or "").strip()
    if not data.startswith(CALLBACK_PREFIX_BUY):
        return
    try:
        # "tb:<sol>:<mint>" — split on the FIRST two colons only since
        # mint addresses contain colons in theory (they don't, but defensive).
        rest = data[len(CALLBACK_PREFIX_BUY):]
        sol_str, mint = rest.split(":", 1)
        sol = float(sol_str)
        mint = mint.strip()
    except (ValueError, IndexError):
        await query.message.reply_text("❌ Bad button data.")
        return

    # Run the buy. We send the receipt as a NEW message under the alert
    # so the original alert stays intact. The receipt carries sell buttons
    # so the operator can exit without going back to /portfolio.
    user_id = _operator_user_id(update)
    try:
        import trader_orchestrator
        result = trader_orchestrator.buy(
            user_id, mint, sol,
            signal_source="tg_button", live=True,
        )
        pid = result.get("position_id")
        kb = _kb_position_actions(pid) if pid else None
        await query.message.reply_text(
            _format_buy_receipt(result),
            parse_mode=constants.ParseMode.MARKDOWN,
            disable_web_page_preview=True,
            reply_markup=kb,
        )
    except Exception as e:
        msg = getattr(e, "user_facing_msg", None) or str(e)[:200]
        await query.message.reply_text(f"❌ {msg}")
        print(f"[trader_commands] buy button failed: {e}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)


async def cb_sell(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Inline-button sell handler. Pattern: ts:<position_id>:<pct>."""
    q = update.callback_query
    if not q:
        return
    try:
        await q.answer()
    except Exception:
        pass
    if not _is_admin(update):
        return

    data = (q.data or "").strip()
    if not data.startswith(CALLBACK_PREFIX_SELL):
        return
    try:
        _, pid_str, pct_str = data.split(":", 2)
        pid = int(pid_str)
        pct = float(pct_str) / 100.0
        if not (0 < pct <= 1.0):
            raise ValueError
    except (ValueError, IndexError):
        await q.message.reply_text("❌ Bad sell button data.")
        return

    user_id = _operator_user_id(update)
    try:
        import trader_orchestrator
        result = trader_orchestrator.sell(user_id, pid, sell_pct=pct, live=True)
        await q.message.reply_text(
            _format_sell_receipt(result),
            parse_mode=constants.ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
    except Exception as e:
        msg = getattr(e, "user_facing_msg", None) or str(e)[:200]
        await q.message.reply_text(f"❌ {msg}")
        print(f"[trader_commands] sell button failed: {e}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)


async def cmd_closeall(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Sell 100% of every open position. Convenience for panic-close."""
    if not await _require_admin(update):
        return
    user_id = _operator_user_id(update)
    try:
        import trader_positions, trader_orchestrator
        opens = trader_positions.list_open_positions(user_id)
        if not opens:
            await update.message.reply_text("_No open positions to close._",
                parse_mode=constants.ParseMode.MARKDOWN)
            return
        await update.message.reply_text(
            f"🚪 Closing *{len(opens)}* positions…",
            parse_mode=constants.ParseMode.MARKDOWN,
        )
        results = []
        for pos in opens:
            try:
                r = trader_orchestrator.sell(user_id, pos["id"], sell_pct=1.0, live=True)
                results.append((pos["id"], True, r.get("sell_signature", "")))
            except Exception as e:
                results.append((pos["id"], False, str(e)[:100]))
        lines = ["*closeall results:*"]
        for pid, ok, msg in results:
            icon = "✅" if ok else "❌"
            short = (msg[:16] + "…") if ok else msg
            lines.append(f"{icon} `#{pid}` {short}")
        await update.message.reply_text(
            "\n".join(lines), parse_mode=constants.ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
    except Exception as e:
        await update.message.reply_text(f"❌ /closeall failed: {str(e)[:200]}")


# ── Registration entry point ───────────────────────────────────────────

def register(app: Application, admin_ids: set[int]) -> bool:
    """Wire up trader commands + callbacks on the given Application.

    Returns True if registered, False if TRADER_ENABLED is not "1".
    """
    if not is_enabled():
        print("[trader_commands] TRADER_ENABLED!=1, skipping registration", flush=True)
        return False
    global _admin_ids
    _admin_ids = set(admin_ids)
    if not _admin_ids:
        print("[trader_commands] ADMIN_TG_IDS empty — registering anyway "
              "but no one will be able to use the commands", flush=True)

    app.add_handler(CommandHandler("buy",       cmd_buy))
    app.add_handler(CommandHandler("sell",      cmd_sell))
    app.add_handler(CommandHandler("portfolio", cmd_portfolio))
    app.add_handler(CommandHandler("balance",   cmd_balance))
    app.add_handler(CommandHandler("deposit",   cmd_deposit))
    app.add_handler(CommandHandler("settings",  cmd_settings))
    app.add_handler(CommandHandler("closeall",  cmd_closeall))
    app.add_handler(CommandHandler("tradehelp", cmd_tradehelp))
    # Inline-button callbacks
    app.add_handler(CallbackQueryHandler(cb_buy,  pattern=f"^{CALLBACK_PREFIX_BUY}"))
    app.add_handler(CallbackQueryHandler(cb_sell, pattern=f"^{CALLBACK_PREFIX_SELL}"))

    print(f"[trader_commands] registered with {len(_admin_ids)} admin(s)", flush=True)
    return True
