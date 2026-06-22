"""
trader_notify — send Telegram messages from server-side trader code.

The monitor daemon runs as a separate process from the bot, so it can't
just call PTB handlers directly. Instead it hits Telegram's HTTP API
directly using the bot's BOT_TOKEN (same one the bot already uses).

Used for:
  • Auto-exit fired notifications ("✅ TP1 hit on $X, sold 50%")
  • Auto-exit failed notifications ("❌ SL trigger but sell failed")

Best-effort: failures are logged but never raised. Trading must work
whether or not Telegram is reachable.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


_BOT_TOKEN = (os.environ.get("BOT_TOKEN") or os.environ.get("TG_BOT_TOKEN") or "").strip()
_TG_BASE = f"https://api.telegram.org/bot{_BOT_TOKEN}" if _BOT_TOKEN else ""


def is_enabled() -> bool:
    """Notifications only work when we have the bot token."""
    return bool(_BOT_TOKEN)


def send_message(telegram_id: str | int, text: str, *,
                 parse_mode: str = "Markdown",
                 disable_web_page_preview: bool = True) -> bool:
    """Send a TG message. Returns True on 200 OK, False on any failure
    (logged to stderr). Never raises."""
    if not is_enabled():
        return False
    try:
        body = json.dumps({
            "chat_id": int(telegram_id),
            "text":    text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_web_page_preview,
        }).encode()
        req = urllib.request.Request(
            f"{_TG_BASE}/sendMessage", data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=6) as r:
            ok = (r.status == 200)
        return ok
    except Exception as e:
        print(f"[trader_notify] sendMessage to {telegram_id} failed: {e}", flush=True)
        return False


def notify_auto_exit(telegram_id: str | int, *,
                     position_id: int, mint: str, kind: str,
                     applied: bool, sell_signature: str = "",
                     sol_out_lamports: int = 0, error: str = "") -> bool:
    """Render + send a structured auto-exit notification.

    kind ∈ {"tp1","tp2","tp3","tp4","sl","tsl","breakeven_arm"}
    applied: did the sell actually go through (or was it skipped / failed)
    """
    short_mint = mint[:6] + "…" + mint[-4:] if mint else "?"
    emoji = {
        "tp1": "🎯", "tp2": "🎯", "tp3": "🎯", "tp4": "🎯",
        "sl": "🛑", "tsl": "📉", "breakeven_arm": "🔒",
    }.get(kind, "ℹ️")
    label = {
        "sl":  "Stop-loss",
        "tsl": "Trailing-stop",
        "breakeven_arm": "Breakeven armed",
    }.get(kind, f"Take-profit {kind.upper()}")

    if kind == "breakeven_arm":
        # No sell — just a state flip
        text = (
            f"{emoji} *Breakeven armed* on position `#{position_id}`\n"
            f"`{mint}`\n\n"
            "Stop-loss is now at entry — you can't lose on this trade anymore."
        )
        return send_message(telegram_id, text)

    if applied:
        sig_line = ""
        if sell_signature:
            sig_line = (f"\n[`{sell_signature[:16]}…`]"
                        f"(https://solscan.io/tx/{sell_signature})")
        sol_line = ""
        if sol_out_lamports > 0:
            sol_line = f"\n💰 Received: *{sol_out_lamports/1e9:.4f}* SOL"
        text = (
            f"{emoji} *{label}* fired on `#{position_id}` ({short_mint})\n"
            f"{sol_line}{sig_line}"
        )
    else:
        text = (
            f"⚠️ *{label}* triggered on `#{position_id}` ({short_mint}) "
            f"but the sell failed.\n\n"
            f"Error: `{(error or 'unknown')[:200]}`\n"
            f"_Run /portfolio to inspect; sell manually if needed._"
        )
    return send_message(telegram_id, text)
