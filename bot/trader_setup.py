"""
bot/trader_setup.py — button-driven /setup menu for the trader.

Replaces the CLI-style /settings command with a real product-grade UI:

  /setup → main menu (current values displayed)
  Tap a category → sub-menu with preset buttons
  Tap a preset → value commits, menu re-renders with new value
  [← Back] returns to parent menu
  [✕ Close] dismisses the menu

Each tap edits the SAME message (no spam). All state lives in
trader_user_settings — no per-session in-memory state to worry about.

Callback data scheme (Telegram caps at 64 bytes per callback):
  s:m                       — main menu
  s:b                       — buy-presets sub-menu
  s:bs:<slot>:<sol>         — set slot N to <sol>
  s:t                       — TP ladder sub-menu
  s:tr:<i>                  — TP rung editor for index i
  s:trg:<i>:<pct>           — set rung i gain to pct (signed int)
  s:trs:<i>:<pct>           — set rung i sell% to pct
  s:tx:<i>                  — remove rung i
  s:ta                      — append new rung
  s:l:<pct>                 — set stop-loss to -pct
  s:p:<pct>                 — set trailing-stop pct
  s:e:<pct>                 — set breakeven pct
  s:close                   — dismiss menu
"""

from __future__ import annotations

import json
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

# Make web/ importable
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
sys.path.insert(0, str(_REPO / "web"))

_admin_ids: set[int] = set()


def _is_admin(update: Update) -> bool:
    u = update.effective_user
    return bool(u and u.id in _admin_ids)


def _uid(update: Update) -> str:
    return str(update.effective_user.id)


# ── Preset value sets (the "button bank" for each setting) ──────────────

BUY_AMOUNT_PRESETS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
TP_GAIN_PRESETS = [25, 50, 100, 200, 500, 1000]   # +percentage gain
TP_SELL_PRESETS = [25, 50, 75, 100]               # % of remaining to sell
SL_PRESETS = [10, 20, 30, 40, 50, 75]             # negative — exit at -X%
TSL_PRESETS = [10, 15, 20, 30, 50]                # trail off HWM
BE_PRESETS = [5, 10, 20, 50]                      # breakeven gain threshold

MAX_TP_RUNGS = 4   # ladders longer than this hurt UX (too many buttons)


# ── Rendering helpers ──────────────────────────────────────────────────

def _fmt_settings(s: dict) -> str:
    """Render the current settings as a markdown summary."""
    presets = "  ".join(f"{a}" for a in s["buy_presets_sol"])
    ladder_lines = []
    for i, r in enumerate(s["tp_ladder"]):
        ladder_lines.append(
            f"  TP{i+1}: +{r['pct']:.0f}% → sell {r['sell_pct']:.0f}%"
        )
    ladder = "\n".join(ladder_lines) if ladder_lines else "  _(none)_"
    return (
        "*🛒 Buy amounts (3 inline buttons):*\n"
        f"  {presets} SOL\n\n"
        f"*🎯 Take-profit ladder:*\n{ladder}\n\n"
        f"*🛑 Stop loss:* {s['sl_pct']:+.0f}%\n"
        f"*📈 Trailing stop:* {s['tsl_pct']:.0f}% off high\n"
        f"*🔒 Breakeven:* at +{s['breakeven_pct']:.0f}% gain → SL flips to entry"
    )


def _kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 Buy Amounts", callback_data="s:b"),
         InlineKeyboardButton("🎯 TP Ladder",   callback_data="s:t")],
        [InlineKeyboardButton("🛑 Stop Loss",    callback_data="s:l"),
         InlineKeyboardButton("📈 Trailing",     callback_data="s:p")],
        [InlineKeyboardButton("🔒 Breakeven",    callback_data="s:e"),
         InlineKeyboardButton("✕ Close",        callback_data="s:close")],
    ])


def _kb_buy_presets(s: dict) -> InlineKeyboardMarkup:
    """Sub-menu: edit the 3 buy preset slots."""
    rows = []
    for slot in range(3):
        current = s["buy_presets_sol"][slot]
        # Row 1 per slot: header (non-button) is rendered in text
        # Row of preset buttons:
        slot_buttons = []
        for amt in BUY_AMOUNT_PRESETS:
            label = f"{amt}" + (" ✓" if amt == current else "")
            slot_buttons.append(InlineKeyboardButton(
                label, callback_data=f"s:bs:{slot}:{amt}",
            ))
        # 4 per row for tighter layout
        rows.append(slot_buttons[:4])
        rows.append(slot_buttons[4:])
    rows.append([InlineKeyboardButton("← Back", callback_data="s:m")])
    return InlineKeyboardMarkup(rows)


def _fmt_buy_presets(s: dict) -> str:
    p = s["buy_presets_sol"]
    return (
        "*🛒 BUY AMOUNTS*\n\n"
        "The 3 inline buttons shown under every alert.\n"
        "Tap one of the 8 amounts in each row to assign it to that slot.\n\n"
        f"  Slot 1: *{p[0]}* SOL\n"
        f"  Slot 2: *{p[1]}* SOL\n"
        f"  Slot 3: *{p[2]}* SOL"
    )


def _kb_tp_ladder(s: dict) -> InlineKeyboardMarkup:
    rows = []
    for i, r in enumerate(s["tp_ladder"]):
        rows.append([
            InlineKeyboardButton(
                f"TP{i+1}: +{r['pct']:.0f}% sell {r['sell_pct']:.0f}%",
                callback_data=f"s:tr:{i}",
            ),
            InlineKeyboardButton("✕", callback_data=f"s:tx:{i}"),
        ])
    if len(s["tp_ladder"]) < MAX_TP_RUNGS:
        rows.append([InlineKeyboardButton("➕ Add rung", callback_data="s:ta")])
    rows.append([InlineKeyboardButton("← Back", callback_data="s:m")])
    return InlineKeyboardMarkup(rows)


def _fmt_tp_ladder(s: dict) -> str:
    ladder = s["tp_ladder"]
    if not ladder:
        body = "_No rungs set — auto-TP is disabled._"
    else:
        body = "\n".join(
            f"  *TP{i+1}*: at +{r['pct']:.0f}% gain → sell {r['sell_pct']:.0f}% of remaining"
            for i, r in enumerate(ladder)
        )
    return (
        "*🎯 TAKE-PROFIT LADDER*\n\n"
        "Each rung auto-sells a percentage when its gain target hits. "
        "Rungs fire in order — later rungs only after earlier ones.\n\n"
        f"{body}\n\n_Tap a rung to edit, ✕ to remove._"
    )


def _kb_tp_rung_editor(s: dict, idx: int) -> InlineKeyboardMarkup:
    if idx >= len(s["tp_ladder"]):
        return _kb_tp_ladder(s)
    rung = s["tp_ladder"][idx]
    # Gain row
    gain_row1, gain_row2 = [], []
    for j, g in enumerate(TP_GAIN_PRESETS):
        tag = " ✓" if g == int(rung["pct"]) else ""
        btn = InlineKeyboardButton(f"+{g}%{tag}",
            callback_data=f"s:trg:{idx}:{g}")
        (gain_row1 if j < 3 else gain_row2).append(btn)
    # Sell row
    sell_row = []
    for s_pct in TP_SELL_PRESETS:
        tag = " ✓" if s_pct == int(rung["sell_pct"]) else ""
        sell_row.append(InlineKeyboardButton(
            f"{s_pct}%{tag}",
            callback_data=f"s:trs:{idx}:{s_pct}",
        ))
    return InlineKeyboardMarkup([
        gain_row1, gain_row2, sell_row,
        [InlineKeyboardButton("🗑 Remove", callback_data=f"s:tx:{idx}"),
         InlineKeyboardButton("← Back",   callback_data="s:t")],
    ])


def _fmt_tp_rung_editor(s: dict, idx: int) -> str:
    if idx >= len(s["tp_ladder"]):
        return "_That rung doesn't exist anymore._"
    r = s["tp_ladder"][idx]
    return (
        f"*🎯 EDIT TP{idx+1}*\n\n"
        f"Currently: at *+{r['pct']:.0f}%* gain → sell *{r['sell_pct']:.0f}%* of remaining.\n\n"
        "Tap a gain target then a sell percentage to update.\n"
        "_Both update immediately — no save button._"
    )


def _kb_simple_picker(prefix: str, presets: list, current: float,
                      signed_negative: bool = False) -> InlineKeyboardMarkup:
    """Generic 1-line picker. Used for SL, TSL, breakeven."""
    btns = []
    for v in presets:
        display = f"{-v}%" if signed_negative else f"{v}%"
        tag = " ✓" if v == abs(int(current)) else ""
        btns.append(InlineKeyboardButton(
            f"{display}{tag}", callback_data=f"{prefix}:{v}",
        ))
    rows = [btns[:4], btns[4:]] if len(btns) > 4 else [btns]
    rows.append([InlineKeyboardButton("← Back", callback_data="s:m")])
    return InlineKeyboardMarkup(rows)


def _fmt_sl(s: dict) -> str:
    return (
        "*🛑 STOP LOSS*\n\n"
        f"Currently: exit at *{s['sl_pct']:+.0f}%* gain (i.e. {-s['sl_pct']:.0f}% loss).\n\n"
        "Tap a value to update. Lower = tighter risk."
    )


def _fmt_tsl(s: dict) -> str:
    return (
        "*📈 TRAILING STOP*\n\n"
        f"Currently: exit if price drops *{s['tsl_pct']:.0f}%* below the high "
        "seen since buying.\n\n"
        "Only triggers AFTER price has risen above entry. Higher = more room "
        "for volatility; lower = locks profits faster."
    )


def _fmt_be(s: dict) -> str:
    return (
        "*🔒 BREAKEVEN*\n\n"
        f"Currently: when gain reaches *+{s['breakeven_pct']:.0f}%*, the "
        "stop-loss flips to entry price (0% loss).\n\n"
        "One-shot — only flips once. Lower threshold = more aggressive "
        "risk removal."
    )


# ── Command handler ────────────────────────────────────────────────────

async def cmd_setup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    try:
        import trader_positions
        s = trader_positions.get_user_settings(_uid(update))
        text = "*⚙️ TRADER SETUP*\n\n" + _fmt_settings(s)
        await update.message.reply_text(
            text, parse_mode=constants.ParseMode.MARKDOWN, reply_markup=_kb_main(),
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Setup failed: {str(e)[:200]}")
        print(f"[trader_setup] /setup failed: {e}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)


# ── Callback router ────────────────────────────────────────────────────

async def cb_setup(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Single callback handler that routes by callback_data prefix."""
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
    if not data.startswith("s:"):
        return

    try:
        import trader_positions
        uid = _uid(update)
        parts = data.split(":")
        screen = parts[1] if len(parts) > 1 else ""

        if screen == "close":
            try:
                await q.message.delete()
            except Exception:
                pass
            return

        # ── Mutations first; they all reload settings + redirect to a screen ──
        if screen == "bs" and len(parts) >= 4:
            slot, amt = int(parts[2]), float(parts[3])
            s = trader_positions.get_user_settings(uid)
            presets = list(s["buy_presets_sol"])
            while len(presets) < 3:
                presets.append(0.05)
            presets[slot] = amt
            trader_positions.set_user_settings(uid, buy_presets_sol=presets[:3])
            return await _render(q, uid, "b")

        if screen == "trg" and len(parts) >= 4:
            idx, pct = int(parts[2]), int(parts[3])
            s = trader_positions.get_user_settings(uid)
            ladder = list(s["tp_ladder"])
            if idx < len(ladder):
                ladder[idx] = dict(ladder[idx]); ladder[idx]["pct"] = pct
                trader_positions.set_user_settings(uid, tp_ladder=ladder)
            return await _render(q, uid, f"tr:{idx}")

        if screen == "trs" and len(parts) >= 4:
            idx, pct = int(parts[2]), int(parts[3])
            s = trader_positions.get_user_settings(uid)
            ladder = list(s["tp_ladder"])
            if idx < len(ladder):
                ladder[idx] = dict(ladder[idx]); ladder[idx]["sell_pct"] = pct
                trader_positions.set_user_settings(uid, tp_ladder=ladder)
            return await _render(q, uid, f"tr:{idx}")

        if screen == "tx" and len(parts) >= 3:
            idx = int(parts[2])
            s = trader_positions.get_user_settings(uid)
            ladder = list(s["tp_ladder"])
            if 0 <= idx < len(ladder):
                ladder.pop(idx)
                trader_positions.set_user_settings(uid, tp_ladder=ladder)
            return await _render(q, uid, "t")

        if screen == "ta":
            s = trader_positions.get_user_settings(uid)
            ladder = list(s["tp_ladder"])
            if len(ladder) < MAX_TP_RUNGS:
                # Sensible default new rung: double the highest current pct
                # or +100% if empty.
                next_pct = (max((r["pct"] for r in ladder), default=50) * 2
                            if ladder else 100)
                ladder.append({"pct": next_pct, "sell_pct": 50})
                trader_positions.set_user_settings(uid, tp_ladder=ladder)
            return await _render(q, uid, "t")

        if screen == "l" and len(parts) >= 3:
            pct = int(parts[2])
            trader_positions.set_user_settings(uid, sl_pct=-abs(pct))
            return await _render(q, uid, "l")

        if screen == "p" and len(parts) >= 3:
            pct = int(parts[2])
            trader_positions.set_user_settings(uid, tsl_pct=abs(pct))
            return await _render(q, uid, "p")

        if screen == "e" and len(parts) >= 3:
            pct = int(parts[2])
            trader_positions.set_user_settings(uid, breakeven_pct=abs(pct))
            return await _render(q, uid, "e")

        # ── Pure navigation (no mutation) ──
        return await _render(q, uid, ":".join(parts[1:]) or "m")

    except Exception as e:
        print(f"[trader_setup] callback failed: {e}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)


async def _render(q, uid: str, screen: str):
    """Re-render the menu at the given screen using the current settings."""
    import trader_positions
    s = trader_positions.get_user_settings(uid)
    parts = screen.split(":")
    code = parts[0]

    if code == "m":
        text = "*⚙️ TRADER SETUP*\n\n" + _fmt_settings(s)
        kb = _kb_main()
    elif code == "b":
        text, kb = _fmt_buy_presets(s), _kb_buy_presets(s)
    elif code == "t":
        text, kb = _fmt_tp_ladder(s), _kb_tp_ladder(s)
    elif code == "tr" and len(parts) >= 2:
        idx = int(parts[1])
        text, kb = _fmt_tp_rung_editor(s, idx), _kb_tp_rung_editor(s, idx)
    elif code == "l":
        text = _fmt_sl(s)
        kb = _kb_simple_picker("s:l", SL_PRESETS, s["sl_pct"], signed_negative=True)
    elif code == "p":
        text = _fmt_tsl(s)
        kb = _kb_simple_picker("s:p", TSL_PRESETS, s["tsl_pct"])
    elif code == "e":
        text = _fmt_be(s)
        kb = _kb_simple_picker("s:e", BE_PRESETS, s["breakeven_pct"])
    else:
        text = "*⚙️ TRADER SETUP*\n\n" + _fmt_settings(s)
        kb = _kb_main()

    try:
        await q.edit_message_text(
            text, parse_mode=constants.ParseMode.MARKDOWN, reply_markup=kb,
        )
    except Exception as e:
        # Editing fails if the message body didn't change (BadRequest:
        # Message is not modified). Safe to ignore.
        if "not modified" not in str(e).lower():
            raise


# ── Registration ────────────────────────────────────────────────────────

def register(app: Application, admin_ids: set[int]):
    global _admin_ids
    _admin_ids = set(admin_ids)
    app.add_handler(CommandHandler("setup", cmd_setup))
    app.add_handler(CallbackQueryHandler(cb_setup, pattern=r"^s:"))
    print(f"[trader_setup] registered (admin_ids={len(_admin_ids)})", flush=True)
