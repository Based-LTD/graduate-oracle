"""
tos_gate — Terms-of-Service acceptance for trading features.

Goal: every user must explicitly accept a clear, plain-English TOS
before they can use any trading functionality. This gives us a:
  • Legal record of acceptance (timestamp + version)
  • Forcing function to communicate custodial / risk model
  • Re-acceptance path if we update terms (bump TOS_VERSION)

Storage: separate table `tg_tos_acceptance` in the same DB as tg_users
(data.sqlite via web/db.DB_PATH). No external service.

Gate: callers check `is_accepted(tg_id)`. If False, render `tos_prompt`
which includes the inline [I Accept] button. Tap routes through
cb_accept which records + replies.
"""

from __future__ import annotations

import contextlib
import sqlite3
import sys
import time

from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, Update, constants,
)
from telegram.ext import (
    Application, CallbackQueryHandler, ContextTypes,
)

# Bump this when the TOS text materially changes — forces re-acceptance.
TOS_VERSION = 1


# DB path is shared with tg_users (web/db.py). Lazy import to avoid
# touching that module at import time.
def _db_path() -> str:
    import db
    return db.DB_PATH


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_db_path(), timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init_schema():
    with contextlib.closing(_conn()) as c, c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS tg_tos_acceptance (
            telegram_id  INTEGER NOT NULL,
            tos_version  INTEGER NOT NULL,
            accepted_at  INTEGER NOT NULL,
            PRIMARY KEY (telegram_id, tos_version)
        );
        """)


def is_accepted(telegram_id: int, version: int = TOS_VERSION) -> bool:
    """Has this user accepted the current TOS version?"""
    try:
        init_schema()
        with contextlib.closing(_conn()) as c:
            row = c.execute(
                "SELECT 1 FROM tg_tos_acceptance "
                "WHERE telegram_id = ? AND tos_version = ?",
                (int(telegram_id), int(version)),
            ).fetchone()
        return bool(row)
    except Exception as e:
        print(f"[tos_gate] is_accepted failed: {e}", file=sys.stderr, flush=True)
        # Fail-closed: if the DB check breaks, REQUIRE acceptance.
        # Better to show the prompt than skip a legal record.
        return False


def record_acceptance(telegram_id: int, version: int = TOS_VERSION):
    """Idempotent — same (tg_id, version) only logs once."""
    init_schema()
    with contextlib.closing(_conn()) as c, c:
        c.execute(
            "INSERT OR IGNORE INTO tg_tos_acceptance "
            "(telegram_id, tos_version, accepted_at) VALUES (?, ?, ?)",
            (int(telegram_id), int(version), int(time.time())),
        )


# ── User-facing TOS text ──────────────────────────────────────────────
# Plain-English, no legalese cosplay. Covers the actual real risks of
# this product and our actual obligations. Keep this honest — that's
# the point of having a TOS at all.

TOS_TEXT = (
    "🔒 *Before you can trade*\n\n"
    "Please read & accept:\n\n"
    "1️⃣ *This is a custodial wallet bot.* We hold your private keys "
    "on our server, encrypted with a master key only we have access to. "
    "If our server is compromised or unavailable, your funds could be "
    "at risk. You can mitigate this by setting a withdraw password "
    "(2nd factor) and withdrawing winnings promptly.\n\n"
    "2️⃣ *Pump.fun trading is extremely high-risk.* Most tokens lose "
    "value. Past wins on this bot do not predict future returns. Only "
    "trade what you can afford to lose entirely.\n\n"
    "3️⃣ *We take a 1% fee per side* (buy + sell). Receipts always "
    "show this honestly.\n\n"
    "4️⃣ *Signals are not financial advice.* We share a probability + "
    "context for each alert. The decision to act is yours.\n\n"
    "5️⃣ *We may pause or shut down the service* at any time. We will "
    "give notice and an opportunity to withdraw funds when possible, "
    "but no SLA is guaranteed.\n\n"
    "6️⃣ *Auto-exits (TP/SL/TSL) are best-effort.* Network conditions, "
    "rug pulls, or liquidity collapse can cause auto-exits to fail or "
    "execute at worse prices than quoted.\n\n"
    "7️⃣ *Not legal or tax advice.* Crypto regulations vary by "
    "jurisdiction. You are responsible for compliance and taxes.\n\n"
    "Tapping *I Accept* below records your acceptance and lets you use "
    "all trading features. You can withdraw at any time."
)


def tos_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ I Accept", callback_data="tos:accept"),
        InlineKeyboardButton("✖️ Decline",  callback_data="tos:decline"),
    ]])


# ── Inline button callback ─────────────────────────────────────────────

async def cb_tos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    try:
        await q.answer()
    except Exception:
        pass
    data = (q.data or "").strip()
    tg_id = update.effective_user.id if update.effective_user else None
    if tg_id is None:
        return

    if data == "tos:accept":
        try:
            record_acceptance(tg_id)
        except Exception as e:
            print(f"[tos_gate] record_acceptance failed: {e}",
                  file=sys.stderr, flush=True)
            await q.edit_message_text(
                "❌ Couldn't record your acceptance. Try again from /start.",
            )
            return
        await q.edit_message_text(
            "✅ *Thanks — you're all set.*\n\n"
            "You can now use all trading features. Open the trader hub:\n\n"
            "`/trader`",
            parse_mode=constants.ParseMode.MARKDOWN,
        )
        return

    if data == "tos:decline":
        await q.edit_message_text(
            "✖️ *Declined.*\n\n"
            "Trading features remain locked. You can still receive "
            "signals (free during the promo window). Send /start "
            "anytime to reconsider.",
            parse_mode=constants.ParseMode.MARKDOWN,
        )
        return


# ── Registration ───────────────────────────────────────────────────────

def register(app: Application) -> bool:
    try:
        init_schema()
        app.add_handler(CallbackQueryHandler(cb_tos, pattern=r"^tos:(accept|decline)$"))
        print("[tos_gate] registered", flush=True)
        return True
    except Exception as e:
        print(f"[tos_gate] register failed: {e}", file=sys.stderr, flush=True)
        return False
