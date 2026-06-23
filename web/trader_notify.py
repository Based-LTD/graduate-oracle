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


def _resolve_token() -> str:
    """The bot uses TELEGRAM_BOT_TOKEN in main.py:61. We check that first,
    then BOT_TOKEN / TG_BOT_TOKEN for back-compat with older deploys."""
    for name in ("TELEGRAM_BOT_TOKEN", "BOT_TOKEN", "TG_BOT_TOKEN"):
        v = (os.environ.get(name) or "").strip()
        if v:
            return v
    return ""


_BOT_TOKEN = _resolve_token()
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
        "stale": "⏱",
    }.get(kind, "ℹ️")
    label = {
        "sl":    "Stop-loss",
        "tsl":   "Trailing-stop",
        "breakeven_arm": "Breakeven armed",
        "stale": "Stagnation timeout",
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


def notify_position_closed(telegram_id: str | int, position_id: int) -> bool:
    """Send a full PnL summary when a position has finished closing
    (status='sold'). Reads the accumulated totals from trader_positions
    so multi-leg exits are reported honestly (Day 4.35 fix).

    Called after the FINAL leg's auto-exit. The per-leg
    notify_auto_exit message above this gives the leg-specific tx;
    this gives the overall trade outcome.
    """
    try:
        import trader_positions
        row = trader_positions.get_position(int(position_id))
        if not row or row.get("status") != "sold":
            return False
        mint = row.get("mint") or ""
        short_mint = mint[:6] + "…" + mint[-4:] if mint else "?"
        buy        = (row.get("buy_sol_lamports") or 0) / 1e9
        sell_total = (row.get("sell_sol_lamports") or 0) / 1e9
        fees       = ((row.get("buy_fee_lamports") or 0) +
                      (row.get("sell_fee_lamports") or 0)) / 1e9
        net        = (row.get("net_pnl_lamports") or 0) / 1e9
        net_pct    = (net / buy * 100) if buy else 0
        sign       = "🟢" if net >= 0 else "🔴"
        verdict    = "*PROFIT*" if net >= 0 else "*LOSS*"

        # Optional MC delta block
        mc_block = ""
        entry_mc = row.get("entry_mcap_lamports")
        exit_mc  = row.get("exit_mcap_lamports")
        if entry_mc and exit_mc:
            mc_change = (exit_mc - entry_mc) / entry_mc * 100
            mc_arrow  = "📈" if mc_change >= 0 else "📉"
            mc_block  = (f"\n\n📊 *MC:* {entry_mc/1e9:.1f} SOL → "
                         f"{exit_mc/1e9:.1f} SOL  {mc_arrow} *{mc_change:+.1f}%*")

        text = (
            f"✅ *Position #{position_id} closed* — {short_mint}\n\n"
            f"📊 *PnL summary (all legs):*\n"
            f"  Cost basis: *{buy:.4f}* SOL\n"
            f"  Got back:   *{sell_total:.4f}* SOL\n"
            + (f"  Fees:      −*{fees:.5f}* SOL\n" if fees > 0 else "")
            + f"  ───────────────────\n"
            f"  {sign} Net: *{net:+.4f}* SOL  ({net_pct:+.2f}%)  ← {verdict}"
            + mc_block
        )
        return send_message(telegram_id, text)
    except Exception as e:
        print(f"[trader_notify] notify_position_closed failed: {e}", flush=True)
        return False
