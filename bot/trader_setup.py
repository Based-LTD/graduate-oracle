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
    KeyboardButton,
    ReplyKeyboardMarkup,
    constants,
)


def _persistent_home_kb() -> ReplyKeyboardMarkup:
    """Tiny always-visible keyboard at the bottom of the chat. Tapping
    a button sends the corresponding slash command (which our command
    handlers pick up). resize_keyboard=True makes the buttons small
    instead of taking up keyboard-sized real estate; is_persistent=True
    keeps it shown when the regular keyboard is dismissed."""
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("/trader"), KeyboardButton("/portfolio"), KeyboardButton("/wallet")],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# Make web/ importable
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
sys.path.insert(0, str(_REPO / "web"))

_admin_ids: set[int] = set()

# Day 4.59: TRADER_PUBLIC=1 opens the gate to everyone.
import os as _os
_TRADER_PUBLIC: bool = (_os.environ.get("TRADER_PUBLIC", "") or "").strip() == "1"


def _is_admin(update: Update) -> bool:
    u = update.effective_user
    if not u:
        return False
    if _TRADER_PUBLIC:
        return True
    return u.id in _admin_ids


def _uid(update: Update) -> str:
    return str(update.effective_user.id)


# ── Preset value sets (the "button bank" for each setting) ──────────────

# Wider preset ranges (Day 4.20) — pump.fun winners can do 50× routinely
# so giving users high-end ladders matters.
BUY_AMOUNT_PRESETS = [0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0]
TP_GAIN_PRESETS    = [25, 50, 100, 200, 500, 1000, 2000, 5000]
TP_SELL_PRESETS    = [10, 25, 33, 50, 75, 100]
SL_PRESETS         = [10, 15, 20, 25, 30, 40, 50, 75, 90]
TSL_PRESETS        = [10, 15, 20, 25, 30, 40, 50, 70]
BE_PRESETS         = [5, 10, 15, 20, 30, 50]
STALE_TIMEOUT_PRESETS = [10, 20, 30, 60, 120]   # minutes
STALE_BAND_PRESETS    = [1, 2, 3, 5, 10]        # percent
SLIPPAGE_PRESETS_BPS = [100, 200, 500, 1000, 1500, 2000, 3000, 5000]   # 1% → 50%
MAX_TRADE_PRESETS    = [0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0]  # SOL; "off" = no cap

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
    cap = s.get("max_trade_sol")
    cap_line = f"*{cap:.4f}* SOL" if cap is not None else "_no cap_"
    return (
        "*🛒 Buy amounts (inline buttons):*\n"
        f"  {presets} SOL\n\n"
        f"*🎯 Take-profit ladder:*\n{ladder}\n\n"
        f"*🛑 Stop loss:* {(format(s['sl_pct'], '+.0f') + '%') if s.get('sl_pct') is not None else '*OFF*'}\n"
        f"*📈 Trailing stop:* {(format(s['tsl_pct'], '.0f') + '% off high') if s.get('tsl_pct') is not None else '*OFF*'}\n"
        f"*🔒 Breakeven:* {('at +' + format(s['breakeven_pct'], '.0f') + '% gain') if s.get('breakeven_pct') is not None else '*OFF*'}\n"
        f"*🚀 Moonshot Mode:* {'*ON* (BE+TSL off after 1st TP)' if s.get('moonshot_mode_enabled') else '*OFF*'}\n\n"
        f"*⚡ Slippage:* {s['slippage_bps']/100:.1f}%\n"
        f"*💨 Speed (Jito tip):* `{s['jito_tip_mode']}`\n"
        f"*🛡 Max per trade:* {cap_line}"
    )


def _kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎨 Strategy Preset", callback_data="s:strat")],
        [InlineKeyboardButton("🤖 Auto-Trade",    callback_data="s:at")],
        [InlineKeyboardButton("🛒 Buy Amounts", callback_data="s:b"),
         InlineKeyboardButton("🎯 TP Ladder",   callback_data="s:t")],
        [InlineKeyboardButton("🛑 Stop Loss",    callback_data="s:l"),
         InlineKeyboardButton("📈 Trailing",     callback_data="s:p")],
        [InlineKeyboardButton("🔒 Breakeven",    callback_data="s:e"),
         InlineKeyboardButton("⏱ Stale Exit",    callback_data="s:stale")],
        [InlineKeyboardButton("🚀 Moonshot Mode", callback_data="s:moon")],
        [InlineKeyboardButton("⚡ Slippage",     callback_data="s:slip"),
         InlineKeyboardButton("💨 Speed (Tip)",  callback_data="s:tip")],
        [InlineKeyboardButton("🛡 Max Trade",    callback_data="s:cap")],
        [InlineKeyboardButton("🏠 Home",        callback_data="h:m"),
         InlineKeyboardButton("✕ Close",        callback_data="s:close")],
    ])


def _kb_buy_presets(s: dict) -> InlineKeyboardMarkup:
    """Sub-menu: edit the 3 buy preset slots.

    Layout per slot:
      [━━━ Slot N — current: X SOL ━━━]   ← visual divider (noop)
      [0.001] [0.005] [0.01] [0.025]
      [0.05]  [0.1]   [0.25] [✏️ Custom]
    """
    rows = []
    for slot in range(3):
        current = s["buy_presets_sol"][slot]
        # Header divider — clickable but noop. Shows which slot's row
        # is which, fixes the previous confusion of unlabeled rows.
        rows.append([InlineKeyboardButton(
            f"━━━ Slot {slot+1} — current: {current} SOL ━━━",
            callback_data=f"s:bs:hdr:{slot}",
        )])
        slot_buttons = []
        for amt in BUY_AMOUNT_PRESETS:
            label = f"{amt}" + (" ✓" if amt == current else "")
            slot_buttons.append(InlineKeyboardButton(
                label, callback_data=f"s:bs:set:{slot}:{amt}",
            ))
        # Custom-input button — last position in slot's button group
        custom_btn = InlineKeyboardButton(
            "✏️ Custom", callback_data=f"s:bs:custom:{slot}",
        )
        # Pack as 4-per-row; custom replaces the 8th slot if 10 presets,
        # or appends if fewer. Here we have 10 presets — show 4+4 + custom row.
        rows.append(slot_buttons[:4])
        rows.append(slot_buttons[4:8])
        # Remaining presets + custom in a final small row
        last_row = list(slot_buttons[8:]) + [custom_btn]
        rows.append(last_row)
    rows.append([InlineKeyboardButton("← Back", callback_data="s:m")])
    return InlineKeyboardMarkup(rows)


def _fmt_buy_presets(s: dict) -> str:
    p = s["buy_presets_sol"]
    return (
        "*🛒 BUY AMOUNTS*\n\n"
        "These are the 3 inline buttons under every signal alert.\n"
        "Tap a preset to assign it, or *✏️ Custom* for any amount "
        "(e.g. 0.15 SOL).\n\n"
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
    actions = []
    if len(s["tp_ladder"]) < MAX_TP_RUNGS:
        actions.append(InlineKeyboardButton("➕ Add rung", callback_data="s:ta"))
    if len(s["tp_ladder"]) > 0:
        # One-tap "turn TP off entirely" — clears the whole ladder
        actions.append(InlineKeyboardButton("🛑 OFF (clear all)",
                                            callback_data="s:tclr"))
    if actions:
        rows.append(actions)
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


def _kb_simple_picker(prefix: str, presets: list, current,
                      signed_negative: bool = False,
                      allow_off: bool = False) -> InlineKeyboardMarkup:
    """Generic 1-line picker. Used for SL, TSL, breakeven.
    allow_off adds an "OFF" button that disables the feature (current=None)."""
    btns = []
    for v in presets:
        display = f"{-v}%" if signed_negative else f"{v}%"
        tag = " ✓" if current is not None and v == abs(int(current)) else ""
        btns.append(InlineKeyboardButton(
            f"{display}{tag}", callback_data=f"{prefix}:{v}",
        ))
    if allow_off:
        off_tag = " ✓" if current is None else ""
        btns.append(InlineKeyboardButton(
            f"OFF{off_tag}", callback_data=f"{prefix}:off",
        ))
    rows = [btns[:4], btns[4:]] if len(btns) > 4 else [btns]
    rows.append([InlineKeyboardButton("← Back", callback_data="s:m")])
    return InlineKeyboardMarkup(rows)


def _fmt_sl(s: dict) -> str:
    sl = s.get("sl_pct")
    if sl is None:
        cur = "Currently: *OFF* — positions never auto-exit on a drawdown.\n\n"
    else:
        cur = (f"Currently: exit at *{sl:+.0f}%* gain "
               f"(i.e. {-sl:.0f}% loss).\n\n")
    return (
        "*🛑 STOP LOSS*\n\n"
        + cur +
        "Tap a value to update. Lower = tighter risk. Tap *OFF* to disable."
    )


def _fmt_tsl(s: dict) -> str:
    tsl = s.get("tsl_pct")
    if tsl is None:
        cur = "Currently: *OFF* — no trailing exit. Position rides until SL/TP/manual.\n\n"
    else:
        cur = (f"Currently: exit if price drops *{tsl:.0f}%* below the high "
               "seen since buying.\n\n")
    return (
        "*📈 TRAILING STOP*\n\n"
        + cur +
        "Only triggers AFTER price has risen above entry. Higher = more room "
        "for volatility; lower = locks profits faster. Tap *OFF* to disable."
    )


def _fmt_be(s: dict) -> str:
    be = s.get("breakeven_pct")
    if be is None:
        current_line = (
            "Currently: *OFF* — stop-loss stays at your configured "
            "floor regardless of how high price climbs.\n\n"
        )
    else:
        current_line = (
            f"Currently: when gain reaches *+{be:.0f}%*, the "
            "stop-loss flips to entry price (0% loss).\n\n"
        )
    return (
        "*🔒 BREAKEVEN*\n\n"
        + current_line
        + "One-shot — only flips once. Lower threshold = more aggressive "
          "risk removal. Tap *OFF* to disable entirely (let winners breathe)."
    )


# ── Slippage / Speed / Max-Trade ────────────────────────────────────────

def _fmt_slippage(s: dict) -> str:
    return (
        "*⚡ SLIPPAGE TOLERANCE*\n\n"
        f"Currently: *{s['slippage_bps']/100:.1f}%*\n\n"
        "Max price drift accepted between quote and on-chain execution. "
        "Higher = trade lands more reliably but you pay worse fills. "
        "Lower = better fills but failed buys when volatile.\n\n"
        "Pump.fun pre-grad typically needs 5-15%; post-grad on Raydium "
        "can do 1-3%."
    )


def _kb_slippage(s: dict) -> InlineKeyboardMarkup:
    btns = []
    for bps in SLIPPAGE_PRESETS_BPS:
        tag = " ✓" if bps == s["slippage_bps"] else ""
        btns.append(InlineKeyboardButton(
            f"{bps/100:.1f}%{tag}", callback_data=f"s:slip:{bps}",
        ))
    return InlineKeyboardMarkup([
        btns[:4], btns[4:],
        [InlineKeyboardButton("← Back", callback_data="s:m")],
    ])


_TIP_MODE_DESCRIPTIONS = {
    "auto":  "Auto — match the live Jito p95 floor",
    "fast":  "Fast — 50k lamports (~$0.005)",
    "turbo": "Turbo — 200k lamports (~$0.02)",
    "ultra": "Ultra — 500k lamports (~$0.05)",
}


def _fmt_tip(s: dict) -> str:
    mode = s["jito_tip_mode"]
    desc = _TIP_MODE_DESCRIPTIONS.get(mode, mode)
    return (
        "*💨 EXECUTION SPEED*\n\n"
        f"Currently: *{desc}*\n\n"
        "How much you tip Jito validators to prioritize your tx. Higher "
        "tip = your bundle wins more auctions = your buy lands first.\n\n"
        "• *Auto* tracks the live floor — lands ~95% of the time, cheapest\n"
        "• *Fast/Turbo/Ultra* are fixed amounts that escalate aggression\n"
        "• Use Ultra when racing a hot launch; Auto otherwise"
    )


def _kb_tip(s: dict) -> InlineKeyboardMarkup:
    current = s["jito_tip_mode"]
    rows = []
    for mode in ("auto", "fast", "turbo", "ultra"):
        tag = " ✓" if mode == current else ""
        rows.append([InlineKeyboardButton(
            f"{mode.capitalize()}{tag}", callback_data=f"s:tip:{mode}",
        )])
    rows.append([InlineKeyboardButton("← Back", callback_data="s:m")])
    return InlineKeyboardMarkup(rows)


def _fmt_cap(s: dict) -> str:
    cap = s.get("max_trade_sol")
    cap_line = f"*{cap:.4f}* SOL" if cap is not None else "*No cap*"
    return (
        "*🛡 MAX PER TRADE*\n\n"
        f"Currently: {cap_line}\n\n"
        "Hard safety cap. Any single /buy or [Buy] button tap that "
        "exceeds this amount is REFUSED before reaching the chain. "
        "Protects against fat-fingered taps and stale buy-button data.\n\n"
        "Set generously above your largest expected trade."
    )


def _kb_cap(s: dict) -> InlineKeyboardMarkup:
    cap = s.get("max_trade_sol")
    btns = []
    for v in MAX_TRADE_PRESETS:
        tag = " ✓" if cap is not None and abs(cap - v) < 1e-9 else ""
        btns.append(InlineKeyboardButton(f"{v} SOL{tag}",
            callback_data=f"s:cap:{v}"))
    off_tag = " ✓" if cap is None else ""
    rows = [btns[:4], btns[4:],
            [InlineKeyboardButton(f"No cap{off_tag}", callback_data="s:cap:off")],
            [InlineKeyboardButton("← Back", callback_data="s:m")]]
    return InlineKeyboardMarkup(rows)


# ── Auto-trade settings ────────────────────────────────────────────────

# picker buttons. 0.001 dropped — too small for net positive after
# overhead. Added 0.25, 0.5, 1.0, 2.0 for higher-conviction users.
_AUTO_SIZE_PRESETS_SOL = [0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0]
_AUTO_CAP_PRESETS      = [1, 3, 5, 10]                      # max concurrent
_AUTO_IDLE_PRESETS_H   = [1, 4, 6, 12, 24]                  # inactivity pause hrs


def _star_telemetry(uid: str) -> str:
    """Day 4.69: render today/week ★ trade stats for the user. Compact,
    headline-style. Joins trader_positions (trade outcomes) with
    composite_predictions (signal features) to identify which trades
    were on ★ starred alerts."""
    import sqlite3, contextlib, time, os
    try:
        import trader_positions as _tp
        trader_db = _tp._db_path()
        # composite_predictions lives in data.sqlite — same dir
        data_db = os.path.join(os.path.dirname(trader_db), "data.sqlite")
        if not os.path.exists(data_db):
            return ""
        with contextlib.closing(sqlite3.connect(trader_db, timeout=3)) as c:
            c.row_factory = sqlite3.Row
            c.execute(f"ATTACH DATABASE '{data_db}' AS d")
            now = int(time.time())
            def stats(since):
                rows = c.execute("""
                    SELECT t.buy_sol_lamports, t.net_pnl_lamports,
                           cp.composite_score, cp.threshold_at_cross,
                           cp.smart_money_in, cp.tg_tier
                      FROM trader_positions t
                      LEFT JOIN d.composite_predictions cp ON cp.mint = t.mint
                     WHERE t.user_id = ? AND t.buy_timestamp >= ?
                       AND t.status='sold' AND t.net_pnl_lamports IS NOT NULL
                """, (str(uid), since)).fetchall()
                star_rows = []
                for r in rows:
                    thr = r["threshold_at_cross"] or 0
                    sr  = (r["composite_score"]/thr) if thr > 0 and r["composite_score"] else 0
                    sm  = r["smart_money_in"]
                    starred = (r["tg_tier"] in ("WATCH","SCOUT")
                               and sr >= 3
                               and sm is not None and 3 <= sm <= 9)
                    if starred:
                        star_rows.append(r)
                if not star_rows:
                    return None
                wins = sum(1 for r in star_rows if (r["net_pnl_lamports"] or 0) > 0)
                net  = sum(r["net_pnl_lamports"] for r in star_rows) / 1e9
                return (len(star_rows), wins, net)
            d = stats(now - 24*3600)
            w = stats(now - 7*24*3600)
    except Exception as e:
        print(f"[_star_telemetry] failed: {e}", flush=True)
        return ""
    lines = []
    if d:
        n, wins, net = d
        lines.append(f"  Today: *★ ALPHA {n}* trades · *{wins}* wins · `{net:+.4f}` SOL")
    if w:
        n, wins, net = w
        wr = (wins/n*100) if n else 0
        lines.append(f"  Week:  *★ ALPHA {n}* trades · *{wr:.0f}%* wr · `{net:+.4f}` SOL")
    return ("\n" + "\n".join(lines)) if lines else ""


def _fmt_auto_trade(s: dict, uid: str = "") -> str:
    enabled = s.get("auto_trade_enabled")
    size_sol = (s.get("auto_trade_size_lamports") or 0) / 1e9
    starred_only = bool(s.get("auto_trade_starred_only", 1))
    min_tier = s.get("auto_trade_min_tier") or "ACT"
    cap = s.get("auto_trade_max_concurrent") or 3
    idle_h = s.get("auto_trade_max_inactive_hours") or 0
    state = "🟢 *ON*" if enabled else "🔴 *OFF*"
    mode_line = (
        "*★ ALPHA only* (recommended — proven edge)"
        if starred_only else
        f"*Legacy tier mode* — `{min_tier}` and stricter"
    )
    idle_line = (f"Pause after: *{idle_h}h* idle" if idle_h > 0
                 else "Pause after: *OFF*")
    tele = _star_telemetry(uid) if uid else ""
    return (
        "*🤖 ★ AUTO-BUY*\n\n"
        f"State: {state}\n"
        f"Mode: {mode_line}\n"
        f"Size per buy: *{size_sol:.4f}* SOL\n"
        f"Max concurrent: *{cap}*\n"
        f"{idle_line}\n"
        + (f"\n*📊 Your ★ ALPHA activity:*{tele}\n" if tele else "")
        + "\n_★ ALPHA = the algorithm's picks. The intersection of "
        "three signal features (decisive composite cross, smart money "
        "sweet spot, market structure) that statistically outperform "
        "the average free-tier alert. Backtest: 1.5-2.5× lift over "
        "base graduation rate._\n\n"
        "_The Oracle predicts (free signals). The Algorithm picks (★ ALPHA)._\n\n"
        "⚠️ _Most pump.fun trades lose money. Auto-trading compounds losses. "
        "Start small and watch closely._"
    )


def _kb_auto_trade(s: dict) -> InlineKeyboardMarkup:
    enabled = bool(s.get("auto_trade_enabled"))
    cur_size = (s.get("auto_trade_size_lamports") or 0) / 1e9
    cur_starred_only = bool(s.get("auto_trade_starred_only", 1))
    cur_cap = s.get("auto_trade_max_concurrent") or 3
    cur_idle = s.get("auto_trade_max_inactive_hours") or 0
    rows = []
    # Master toggle
    if enabled:
        rows.append([InlineKeyboardButton("🛑 Turn OFF",
                                          callback_data="s:at:off")])
    else:
        rows.append([InlineKeyboardButton("✅ Turn ON",
                                          callback_data="s:at:on")])
    # ★ Only mode toggle — the headline. (Day 4.69)
    rows.append([
        InlineKeyboardButton(
            ("✓ ★ ALPHA only (recommended)" if cur_starred_only
             else "★ ALPHA only"),
            callback_data="s:at:smode:on",
        ),
    ])
    rows.append([
        InlineKeyboardButton(
            ("⚙️ Legacy tier mode" if cur_starred_only
             else "✓ ⚙️ Legacy tier mode (advanced)"),
            callback_data="s:at:smode:off",
        ),
    ])
    # Size picker — split into 2 rows since we have 8 options now.
    size_buttons = [
        InlineKeyboardButton(
            ("✓ " if abs(v - cur_size) < 1e-9 else "") + f"{v:g} SOL",
            callback_data=f"s:at:size:{int(v*1e9)}",
        ) for v in _AUTO_SIZE_PRESETS_SOL
    ]
    rows.append(size_buttons[:4])
    rows.append(size_buttons[4:])
    # Legacy tier picker — only show when in legacy mode
    if not cur_starred_only:
        cur_tier = s.get("auto_trade_min_tier") or "ACT"
        rows.append([
            InlineKeyboardButton(("✓ " if cur_tier == t else "") + label,
                                 callback_data=f"s:at:tier:{t}")
            for t, label in [("ACT", "ACT"), ("WATCH", "+WATCH"), ("SCOUT", "+SCOUT")]
        ])
        cur_inc_starred = bool(s.get("auto_trade_include_starred"))
        rows.append([
            InlineKeyboardButton(
                ("✓ ★ Include starred" if cur_inc_starred else "★ Include starred"),
                callback_data=("s:at:star:off" if cur_inc_starred else "s:at:star:on"),
            ),
        ])
    # Cap
    rows.append([
        InlineKeyboardButton(("✓ " if cur_cap == c else "") + f"{c}",
                             callback_data=f"s:at:cap:{c}")
        for c in _AUTO_CAP_PRESETS
    ])
    # Inactivity timeout — last row, includes OFF option
    idle_buttons = [
        InlineKeyboardButton(("✓ " if cur_idle == h else "") + f"{h}h",
                             callback_data=f"s:at:idle:{h}")
        for h in _AUTO_IDLE_PRESETS_H
    ]
    idle_buttons.append(InlineKeyboardButton(
        ("✓ " if cur_idle == 0 else "") + "OFF",
        callback_data="s:at:idle:0",
    ))
    rows.append(idle_buttons)
    rows.append([InlineKeyboardButton("← Back", callback_data="s:m")])
    return InlineKeyboardMarkup(rows)


# ── Stagnation timeout ─────────────────────────────────────────────────

def _fmt_stale(s: dict) -> str:
    timeout = s.get("stale_timeout_minutes") or 0
    band    = s.get("stale_band_pct") or 3.0
    if timeout == 0:
        line1 = "Currently: *OFF* — positions never auto-close on stagnation."
    else:
        line1 = (f"Currently: close any position that stays within "
                 f"*±{band:.1f}%* of its anchor for *{timeout} min*.")
    return (
        "*⏱ STAGNATION TIMEOUT*\n\n"
        + line1 + "\n\n"
        "Frees up your concurrent-cap slot when a coin goes dead "
        "(no movement in either direction). Different from SL — fires "
        "even if you're slightly green but flat. Different from TSL — "
        "doesn't require a peak.\n\n"
        "_Anchor resets every time price moves outside the band, so "
        "active coins are unaffected. Only fires on truly dead positions._"
    )


def _fmt_moonshot(s: dict) -> str:
    on = bool(s.get("moonshot_mode_enabled"))
    cur = ("Currently: *ON* — after the first TP rung fires, breakeven "
           "and trailing-stop are disabled for the remaining position."
           if on else
           "Currently: *OFF* — BE and TSL stay active across all TPs.")
    return (
        "*🚀 MOONSHOT MODE*\n\n"
        + cur + "\n\n"
        "*What it does:* After your first TP rung fires (de-risking part "
        "of the position), BE-arm and trailing-stop are suppressed for "
        "the remainder. SL stays active. Remaining TP rungs still fire.\n\n"
        "*Why use it:* Pump.fun coins often pump → correct → pump again. "
        "A tight TSL on the thinned position catches the correction and "
        "exits before the second leg. With Moonshot ON, you take partial "
        "profit early, then let the rest ride for the post-correction move.\n\n"
        "*Tradeoff:* If the coin just bleeds after TP1, you'll watch "
        "unrealized profit fade until SL fires. Best for users with high "
        "conviction in the corrections-recover pattern."
    )


def _kb_moonshot(s: dict) -> InlineKeyboardMarkup:
    on = bool(s.get("moonshot_mode_enabled"))
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(("✓ ON" if on else "ON"), callback_data="s:moon:on"),
         InlineKeyboardButton(("OFF" if on else "✓ OFF"), callback_data="s:moon:off")],
        [InlineKeyboardButton("← Back", callback_data="s:m")],
    ])


def _kb_stale(s: dict) -> InlineKeyboardMarkup:
    cur_t = s.get("stale_timeout_minutes") or 0
    cur_b = s.get("stale_band_pct") or 3.0
    rows = []
    # Timeout row
    t_btns = [
        InlineKeyboardButton(("✓ " if cur_t == v else "") + f"{v}m",
                             callback_data=f"s:stale:t:{v}")
        for v in STALE_TIMEOUT_PRESETS
    ]
    t_btns.append(InlineKeyboardButton(
        ("✓ " if cur_t == 0 else "") + "OFF",
        callback_data="s:stale:t:0",
    ))
    rows.append(t_btns)
    # Band row
    b_btns = [
        InlineKeyboardButton(("✓ " if abs(cur_b - v) < 0.01 else "") + f"±{v}%",
                             callback_data=f"s:stale:b:{v}")
        for v in STALE_BAND_PRESETS
    ]
    rows.append(b_btns)
    rows.append([InlineKeyboardButton("← Back", callback_data="s:m")])
    return InlineKeyboardMarkup(rows)


# ── Strategy presets ───────────────────────────────────────────────────

def _fmt_strategy() -> str:
    import trader_positions
    lines = ["*🎨 STRATEGY PRESETS*\n",
             "Apply a complete bundle of exit + execution rules with "
             "one tap. Buy amounts stay as you have them.\n"]
    for name in ("conservative", "balanced", "yolo"):
        p = trader_positions.STRATEGY_PRESETS[name]
        ladder = "  ".join(f"+{r['pct']:.0f}%/{r['sell_pct']:.0f}%"
                           for r in p["tp_ladder"])
        lines.append(
            f"\n{p['label']}\n"
            f"  _{p['blurb']}_\n"
            f"  TP: {ladder}  ·  SL {p['sl_pct']:+.0f}%  ·  TSL {p['tsl_pct']:.0f}%\n"
            f"  Slippage {p['slippage_bps']/100:.1f}%  ·  Speed `{p['jito_tip_mode']}`"
        )
    return "\n".join(lines)


def _kb_strategy() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎯 Conservative", callback_data="s:strat:conservative")],
        [InlineKeyboardButton("⚖️ Balanced",    callback_data="s:strat:balanced")],
        [InlineKeyboardButton("🚀 YOLO",        callback_data="s:strat:yolo")],
        [InlineKeyboardButton("← Back",         callback_data="s:m")],
    ])


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
        if screen == "bs" and len(parts) >= 3:
            action = parts[2]
            # ── Header (visual divider) — noop, just re-render ──
            if action == "hdr":
                return await _render(q, uid, "b")
            # ── Preset value set ──
            if action == "set" and len(parts) >= 5:
                slot, amt = int(parts[3]), float(parts[4])
                s = trader_positions.get_user_settings(uid)
                presets = list(s["buy_presets_sol"])
                while len(presets) < 3:
                    presets.append(0.05)
                presets[slot] = amt
                trader_positions.set_user_settings(uid, buy_presets_sol=presets[:3])
                return await _render(q, uid, "b")
            # ── Custom amount — open the text-input wizard ──
            if action == "custom" and len(parts) >= 4:
                slot = int(parts[3])
                ctx.user_data["bs_custom_slot"] = slot
                ctx.user_data["bs_state"] = "awaiting_custom"
                await q.edit_message_text(
                    f"✏️ *Set Slot {slot+1} — Custom amount*\n\n"
                    f"Reply with the SOL amount (e.g. `0.15`).\n"
                    f"Min: 0.0001 SOL · Max: 10 SOL\n\n"
                    f"_Reply `cancel` to abort._",
                    parse_mode=constants.ParseMode.MARKDOWN,
                )
                return
            # Legacy 4-part fallback (old callback_data shape): s:bs:<slot>:<amt>
            if len(parts) >= 4:
                try:
                    slot, amt = int(parts[2]), float(parts[3])
                    s = trader_positions.get_user_settings(uid)
                    presets = list(s["buy_presets_sol"])
                    while len(presets) < 3:
                        presets.append(0.05)
                    presets[slot] = amt
                    trader_positions.set_user_settings(uid, buy_presets_sol=presets[:3])
                    return await _render(q, uid, "b")
                except (ValueError, IndexError):
                    pass

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

        if screen == "tclr":
            # One-tap clear: turn TP entirely off.
            trader_positions.set_user_settings(uid, tp_ladder=[])
            return await _render(q, uid, "t")

        if screen == "l" and len(parts) >= 3:
            val = parts[2]
            if val == "off":
                trader_positions.set_user_settings(uid, clear_sl_pct=True)
            else:
                try:
                    trader_positions.set_user_settings(uid, sl_pct=-abs(int(val)))
                except ValueError:
                    pass
            return await _render(q, uid, "l")

        if screen == "p" and len(parts) >= 3:
            val = parts[2]
            if val == "off":
                trader_positions.set_user_settings(uid, clear_tsl_pct=True)
            else:
                try:
                    trader_positions.set_user_settings(uid, tsl_pct=abs(int(val)))
                except ValueError:
                    pass
            return await _render(q, uid, "p")

        if screen == "e" and len(parts) >= 3:
            val = parts[2]
            if val == "off":
                trader_positions.set_user_settings(uid, clear_breakeven_pct=True)
            else:
                try:
                    trader_positions.set_user_settings(uid, breakeven_pct=abs(int(val)))
                except ValueError:
                    pass
            return await _render(q, uid, "e")

        if screen == "slip" and len(parts) >= 3:
            bps = int(parts[2])
            trader_positions.set_user_settings(uid, slippage_bps=bps)
            return await _render(q, uid, "slip")

        if screen == "tip" and len(parts) >= 3:
            mode = parts[2]
            if mode in trader_positions.JITO_TIP_MODE_LAMPORTS:
                trader_positions.set_user_settings(uid, jito_tip_mode=mode)
            return await _render(q, uid, "tip")

        if screen == "cap" and len(parts) >= 3:
            val = parts[2]
            if val == "off":
                trader_positions.set_user_settings(uid, clear_max_trade_sol=True)
            else:
                try:
                    trader_positions.set_user_settings(uid, max_trade_sol=float(val))
                except ValueError:
                    pass
            return await _render(q, uid, "cap")

        if screen == "strat" and len(parts) >= 3:
            name = parts[2]
            try:
                trader_positions.apply_strategy_preset(uid, name)
            except ValueError:
                pass
            return await _render(q, uid, "m")  # back to main with new values

        # ── Auto-trade actions ──
        if screen == "at" and len(parts) >= 3:
            action = parts[2]
            try:
                if action == "on":
                    trader_positions.set_auto_trade_config(uid, enabled=True)
                elif action == "off":
                    trader_positions.set_auto_trade_config(uid, enabled=False)
                elif action == "size" and len(parts) >= 4:
                    trader_positions.set_auto_trade_config(
                        uid, size_lamports=int(parts[3]))
                elif action == "tier" and len(parts) >= 4:
                    trader_positions.set_auto_trade_config(
                        uid, min_tier=parts[3])
                elif action == "cap" and len(parts) >= 4:
                    trader_positions.set_auto_trade_config(
                        uid, max_concurrent=int(parts[3]))
                elif action == "idle" and len(parts) >= 4:
                    trader_positions.set_auto_trade_config(
                        uid, max_inactive_hours=int(parts[3]))
                elif action == "star" and len(parts) >= 4:
                    trader_positions.set_auto_trade_config(
                        uid, include_starred=(parts[3] == "on"))
                elif action == "smode" and len(parts) >= 4:
                    # Day 4.69: ★ Only mode toggle
                    trader_positions.set_auto_trade_config(
                        uid, starred_only=(parts[3] == "on"))
            except (ValueError, Exception) as e:
                print(f"[trader_setup] auto-trade action failed: {e}", flush=True)
            return await _render(q, uid, "at")

        # ── Stale-exit actions ──
        if screen == "moon" and len(parts) >= 3:
            sub = parts[2]
            try:
                if sub == "on":
                    trader_positions.set_user_settings(uid, moonshot_mode_enabled=True)
                elif sub == "off":
                    trader_positions.set_user_settings(uid, moonshot_mode_enabled=False)
            except Exception as e:
                print(f"[trader_setup] moonshot toggle failed: {e}", flush=True)
            return await _render(q, uid, "moon")

        if screen == "stale" and len(parts) >= 3:
            kind = parts[2]
            try:
                if kind == "t" and len(parts) >= 4:
                    trader_positions.set_auto_trade_config(
                        uid, stale_timeout_minutes=int(parts[3]))
                elif kind == "b" and len(parts) >= 4:
                    trader_positions.set_auto_trade_config(
                        uid, stale_band_pct=float(parts[3]))
            except (ValueError, Exception) as e:
                print(f"[trader_setup] stale-exit action failed: {e}", flush=True)
            return await _render(q, uid, "stale")

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
        kb = _kb_simple_picker("s:l", SL_PRESETS, s["sl_pct"], signed_negative=True, allow_off=True)
    elif code == "p":
        text = _fmt_tsl(s)
        kb = _kb_simple_picker("s:p", TSL_PRESETS, s["tsl_pct"], allow_off=True)
    elif code == "e":
        text = _fmt_be(s)
        kb = _kb_simple_picker("s:e", BE_PRESETS, s["breakeven_pct"], allow_off=True)
    elif code == "slip":
        text, kb = _fmt_slippage(s), _kb_slippage(s)
    elif code == "tip":
        text, kb = _fmt_tip(s), _kb_tip(s)
    elif code == "cap":
        text, kb = _fmt_cap(s), _kb_cap(s)
    elif code == "strat":
        text, kb = _fmt_strategy(), _kb_strategy()
    elif code == "at":
        text, kb = _fmt_auto_trade(s, uid), _kb_auto_trade(s)
    elif code == "stale":
        text, kb = _fmt_stale(s), _kb_stale(s)
    elif code == "moon":
        text, kb = _fmt_moonshot(s), _kb_moonshot(s)
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


# ═══════════════════════════════════════════════════════════════════════
# HUB — the top-level button menu (/trader). Everything reachable from
# inline buttons; no commands required.
# ═══════════════════════════════════════════════════════════════════════
#
# Callback prefix:  h:<screen>[:<arg>...]
#   h:m         → main dashboard
#   h:p         → portfolio list
#   h:p:<id>    → portfolio position detail
#   h:w         → wallet
#   h:c         → close-all confirmation
#   h:cy        → close-all confirmed (executes)
#   h:refresh   → re-render current screen (no-op if same)
#   h:close     → dismiss
#
# Settings link → existing s: callback chain.


def _kb_hub_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Portfolio", callback_data="h:p"),
         InlineKeyboardButton("💰 Wallet",    callback_data="h:w")],
        [InlineKeyboardButton("📜 History",   callback_data="h:hist"),
         InlineKeyboardButton("⚙️ Settings",  callback_data="s:m")],
        [InlineKeyboardButton("🚪 Close All", callback_data="h:c"),
         InlineKeyboardButton("🔄 Refresh",   callback_data="h:m")],
        [InlineKeyboardButton("✕ Close",     callback_data="h:close")],
    ])


def _fmt_hub_main(uid: str) -> str:
    """The dashboard: balance + open positions + realized PnL."""
    import trader_wallets, trader_portfolio
    # Balance
    try:
        wallet = trader_wallets.wallet_for(uid)
        if wallet:
            bal_sol = trader_wallets.get_balance_sol(wallet["public_key"])
            bal_line = f"💰 *{bal_sol:.4f}* SOL  ·  `{wallet['public_key'][:6]}…{wallet['public_key'][-4:]}`"
        else:
            bal_line = "💰 _No wallet yet — tap Wallet to provision_"
    except Exception as e:
        bal_line = f"💰 _balance unavailable: {str(e)[:40]}_"

    # Open positions (unrealized)
    try:
        s = trader_portfolio.portfolio_summary(uid)
        n = s["n_open"]
        if n == 0:
            port_line = "📊 _No open positions._\n_Tap [Buy 0.0X] under any ACT/WATCH alert._"
        else:
            pnl = s["total_unrealized_pnl_lamports"] / 1e9
            pct = s["total_unrealized_pnl_pct"] * 100
            cost = s["total_cost_basis_lamports"] / 1e9
            val  = s["total_current_value_lamports"] / 1e9
            arrow = "📈" if pnl >= 0 else "📉"
            port_line = (
                f"📊 *{n}* open  ·  cost *{cost:.4f}*  ·  now *{val:.4f}*\n"
                f"   {arrow} unrealized: *{pnl:+.4f}* SOL  ({pct:+.1f}%)"
            )
    except Exception as e:
        port_line = f"📊 _portfolio unavailable: {str(e)[:40]}_"

    # Realized — closed-trade aggregate. Honest cumulative number.
    try:
        rs = trader_portfolio.realized_summary(uid)
        if rs["n_trades"] == 0:
            real_line = "💵 _No closed trades yet._"
        else:
            net = rs["total_net_pnl_lamports"] / 1e9
            wr  = rs["win_rate"] * 100
            sign = "🟢" if net >= 0 else "🔴"
            real_line = (
                f"💵 *Realized*: {sign} *{net:+.4f}* SOL  "
                f"({rs['n_wins']}W / {rs['n_losses']}L · {wr:.0f}% wr)"
            )
    except Exception as e:
        real_line = f"💵 _realized unavailable: {str(e)[:40]}_"

    return f"*🤖 GRADUATE TRADER*\n\n{bal_line}\n\n{port_line}\n\n{real_line}"


# ── Portfolio list ──────────────────────────────────────────────────────

def _fmt_portfolio_list(s: dict) -> str:
    n = s["n_open"]
    if n == 0:
        return ("*📊 PORTFOLIO*\n\n_No open positions._\n\n"
                "Tap a Buy button under any ACT/WATCH/SCOUT alert "
                "to open one.")
    lines = [f"*📊 PORTFOLIO* ({n} open)\n",
             "Tap a position to manage:"]
    return "\n".join(lines)


def _kb_portfolio_list(s: dict) -> InlineKeyboardMarkup:
    rows = []
    for p in s["positions"][:15]:
        pid = p["id"]
        mint_short = p["mint"][:5] + "…" + p["mint"][-4:]
        if p["current_sol_value_lamports"] is None:
            label = f"#{pid} {mint_short}  ❓ no quote"
        else:
            pct = p["unrealized_pnl_pct"] * 100
            arrow = "📈" if pct >= 0 else "📉"
            label = f"#{pid} {mint_short}  {arrow} {pct:+.0f}%"
        rows.append([InlineKeyboardButton(label, callback_data=f"h:p:{pid}")])
    if s["n_open"] > 15:
        rows.append([InlineKeyboardButton(
            f"…+{s['n_open']-15} more", callback_data="h:p"  # no-op
        )])
    rows.append([InlineKeyboardButton("← Back", callback_data="h:m")])
    return InlineKeyboardMarkup(rows)


# ── Position detail ─────────────────────────────────────────────────────

def _fmt_position_detail(pos: dict, settings: dict) -> str:
    import json
    mint = pos["mint"]
    pid = pos["id"]
    cost = pos["buy_sol_lamports"] / 1e9
    status = pos.get("status", "open")

    # Two distinct views: open positions show LIVE PnL (Jupiter quote vs
    # cost); closed positions show REALIZED net PnL with fee breakdown.
    if status == "sold":
        sell_total = (pos.get("sell_sol_lamports") or 0) / 1e9
        buy_fee = (pos.get("buy_fee_lamports") or 0) / 1e9
        sell_fee = (pos.get("sell_fee_lamports") or 0) / 1e9
        net = (pos.get("net_pnl_lamports") or 0) / 1e9
        net_pct = (net / cost * 100) if cost else 0
        sign = "🟢" if net >= 0 else "🔴"
        verdict = "PROFIT" if net >= 0 else "LOSS"

        # MC snapshot block — only when both stored
        entry_mc = pos.get("entry_mcap_lamports")
        exit_mc = pos.get("exit_mcap_lamports")
        mc_block = ""
        if entry_mc and exit_mc:
            try:
                import jupiter_price
                sol_usd = jupiter_price.get_sol_usd()
            except Exception:
                sol_usd = None
            # Reuse the same MC formatter from trader_commands. bot/ isn't
            # a package, so we import by module name (it's on sys.path).
            import trader_commands as _tc
            _fmt_mcap = _tc._fmt_mcap
            mc_change = ((exit_mc - entry_mc) / entry_mc * 100)
            arrow = "📈" if mc_change >= 0 else "📉"
            mc_block = (
                f"\n📊 MC: *{_fmt_mcap(entry_mc, sol_usd)}* "
                f"→ *{_fmt_mcap(exit_mc, sol_usd)}* "
                f"{arrow} {mc_change:+.0f}%"
            )

        cur_line = (
            f"*Status:* sold ({pos.get('exit_reason') or 'manual'})\n\n"
            f"Cost basis: *{cost:.4f}* SOL\n"
            f"Got back:   *{sell_total:.4f}* SOL\n"
            + (f"Fees:      −*{buy_fee + sell_fee:.5f}* SOL\n"
               if (buy_fee + sell_fee) > 0 else "")
            + f"───────────────────\n"
            f"{sign} *Net: {net:+.4f} SOL ({net_pct:+.2f}%)* — {verdict}"
            + mc_block
        )
    else:
        cur = pos.get("current_sol_value_lamports")
        if cur is None:
            cur_line = (f"*Status:* open\n\n"
                        f"Cost: *{cost:.4f}* SOL\n"
                        f"_no quote available (mint may have rugged)_")
        else:
            cur_sol = cur / 1e9
            pnl = (cur - pos["buy_sol_lamports"]) / 1e9
            pct = pnl / cost * 100 if cost else 0
            arrow = "📈" if pnl >= 0 else "📉"
            cur_line = (
                f"*Status:* open\n\n"
                f"Cost: *{cost:.4f}* SOL\n"
                f"Now:  *{cur_sol:.4f}* SOL  {arrow} *{pct:+.1f}%* ({pnl:+.4f})"
            )

    # Exit rules — only meaningful for open positions
    rules_block = ""
    if status == "open":
        ladder_str = ""
        try:
            if pos.get("tp_ladder_json"):
                ladder = json.loads(pos["tp_ladder_json"])
                for i, r in enumerate(ladder):
                    done = "✓ " if i < (pos.get("next_tp_index") or 0) else ""
                    ladder_str += f"\n  {done}TP{i+1}: +{r['pct']:.0f}% sell {r['sell_pct']:.0f}%"
        except Exception:
            pass
        sl_pct = pos.get("sl_pct")
        tsl_pct = pos.get("tsl_pct")
        be_pct = pos.get("breakeven_pct")
        armed = bool(pos.get("sl_armed_at_breakeven"))
        rules = []
        if ladder_str:
            rules.append(f"🎯 Ladder:{ladder_str}")
        if sl_pct is not None:
            sl_display = "0% (at entry — breakeven armed)" if armed else f"{sl_pct:+.0f}%"
            rules.append(f"🛑 SL: {sl_display}")
        if tsl_pct is not None:
            rules.append(f"📈 TSL: {tsl_pct:.0f}% off high")
        if be_pct is not None and not armed:
            rules.append(f"🔒 BE: at +{be_pct:.0f}% flips SL to entry")
        if rules:
            rules_block = "\n\n" + "\n".join(rules)

    return (
        f"*📍 POSITION #{pid}*\n"
        f"`{mint}`\n\n"
        f"{cur_line}"
        f"{rules_block}"
    )


def _kb_position_detail(pid: int, is_open: bool = True, mint: str = "") -> InlineKeyboardMarkup:
    rows = []
    if is_open:
        rows.append([
            InlineKeyboardButton("Sell 25%", callback_data=f"ts:{pid}:25"),
            InlineKeyboardButton("Sell 50%", callback_data=f"ts:{pid}:50"),
            InlineKeyboardButton("Sell ALL", callback_data=f"ts:{pid}:100"),
        ])
    if mint:
        # Dexscreener URL button — opens the live chart in browser.
        rows.append([
            InlineKeyboardButton("📊 Dexscreener",
                                 url=f"https://dexscreener.com/solana/{mint}"),
        ])
    rows.append([
        InlineKeyboardButton("← Portfolio", callback_data="h:p"),
        InlineKeyboardButton("🏠 Home",     callback_data="h:m"),
    ])
    return InlineKeyboardMarkup(rows)


# ── Wallet ──────────────────────────────────────────────────────────────

def _fmt_wallet(uid: str) -> str:
    import trader_wallets
    try:
        wallet = trader_wallets.get_or_create_wallet(uid)
        pk = wallet["public_key"]
        try:
            bal = trader_wallets.get_balance_sol(pk)
            bal_str = f"*{bal:.6f}* SOL"
        except Exception:
            bal_str = "_RPC unavailable_"
        return (
            f"*💰 WALLET*\n\n"
            f"Balance: {bal_str}\n\n"
            f"Pubkey (long-press to copy):\n"
            f"`{pk}`\n\n"
            "_Send SOL to this address to fund trades._\n"
            "_Custody is server-side — encrypted with your master key, "
            "private key never leaves the server._"
        )
    except Exception as e:
        return f"*💰 WALLET*\n\n_error: {str(e)[:200]}_"


def _kb_wallet() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💸 Withdraw", callback_data="wd:start")],
        [InlineKeyboardButton("🔄 Refresh", callback_data="h:w"),
         InlineKeyboardButton("🏠 Home",   callback_data="h:m")],
    ])


# ── Close all ──────────────────────────────────────────────────────────

def _fmt_closeall_confirm(uid: str) -> str:
    import trader_portfolio
    s = trader_portfolio.portfolio_summary(uid)
    n = s["n_open"]
    if n == 0:
        return "*🚪 CLOSE ALL*\n\n_No open positions to close._"
    pnl = s["total_unrealized_pnl_lamports"] / 1e9
    return (
        f"*🚪 CLOSE ALL?*\n\n"
        f"You have *{n}* open position{'s' if n != 1 else ''}.\n"
        f"Current unrealized PnL: *{pnl:+.4f}* SOL.\n\n"
        "_This will sell 100% of every position at market._\n"
        "_Confirm below._"
    )


def _kb_closeall_confirm(has_positions: bool) -> InlineKeyboardMarkup:
    if not has_positions:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🏠 Home", callback_data="h:m")],
        ])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Yes, close ALL", callback_data="h:cy")],
        [InlineKeyboardButton("✕ Cancel",         callback_data="h:m")],
    ])


# ── /trader command ────────────────────────────────────────────────────

def _fmt_history(uid: str) -> str:
    """Render closed-trade history. Used by /history command + h:hist button."""
    import trader_portfolio, sqlite3, contextlib
    import trader_positions as _tp
    try:
        rs = trader_portfolio.realized_summary(uid)
    except Exception as e:
        return f"❌ history failed: {str(e)[:200]}"
    if rs["n_trades"] == 0:
        return ("📜 *No closed trades yet.*\n\nClosed trades will appear here "
                "with PnL once they fire.")

    net = rs["total_net_pnl_lamports"] / 1e9
    fees = rs["total_fees_lamports"] / 1e9
    sign = "🟢" if net >= 0 else "🔴"
    lines = [
        f"📜 *Closed trades — {rs['n_trades']} total*\n",
        f"  {sign} Realized: *{net:+.4f}* SOL",
        f"  Wins/Losses: *{rs['n_wins']}* / *{rs['n_losses']}*  "
        f"({rs['win_rate']*100:.0f}% wr)",
        f"  Fees paid: *{fees:.5f}* SOL",
        f"  Best: *{rs['best_trade']/1e9:+.4f}*  ·  "
        f"Worst: *{rs['worst_trade']/1e9:+.4f}*",
        "\n─── *Recent (last 10)* ───",
    ]

    db_path = _tp._db_path()
    with contextlib.closing(sqlite3.connect(db_path, timeout=10)) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT id, mint, buy_sol_lamports, net_pnl_lamports, "
            "       exit_reason, sell_timestamp "
            "  FROM trader_positions "
            " WHERE user_id = ? AND status = 'sold' "
            " ORDER BY sell_timestamp DESC LIMIT 10",
            (str(uid),),
        ).fetchall()
    for r in rows:
        d = dict(r)
        net_l = (d.get("net_pnl_lamports") or 0) / 1e9
        cost = (d.get("buy_sol_lamports") or 0) / 1e9
        pct = (net_l / cost * 100) if cost else 0
        s = "🟢" if net_l >= 0 else "🔴"
        reason = d.get("exit_reason") or "manual"
        short_mint = d["mint"][:6] + "…"
        lines.append(
            f"`#{d['id']:>3}` {s} *{net_l:+.4f}* SOL ({pct:+.1f}%) "
            f"— `{short_mint}` · _{reason}_"
        )
    return "\n".join(lines)


async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Recent closed trades + cumulative realized PnL."""
    if not _is_admin(update):
        return
    uid = _uid(update)
    await update.message.reply_text(
        _fmt_history(uid),
        parse_mode=constants.ParseMode.MARKDOWN,
        disable_web_page_preview=True,
    )


async def cmd_trader(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Top-level entry point — opens the hub."""
    if not _is_admin(update):
        return
    # TOS gate — required before any trading flow. Once accepted, this
    # is a fast DB lookup; we don't re-prompt on every hub callback.
    try:
        import tos_gate
        if not tos_gate.is_accepted(update.effective_user.id):
            await update.message.reply_text(
                tos_gate.TOS_TEXT,
                parse_mode=constants.ParseMode.MARKDOWN,
                reply_markup=tos_gate.tos_keyboard(),
                disable_web_page_preview=True,
            )
            return
    except Exception as e:
        print(f"[trader_setup] TOS gate failed: {e}", flush=True)
        return
    try:
        # Re-attach the persistent home keyboard on EVERY /trader invocation.
        # Telegram's reply keyboard is supposed to persist client-side once
        # sent, but in practice it can disappear after deploys, long
        # inactivity windows, or TG client quirks. Re-sending it every
        # /trader is cheap (1 extra message) and means the keyboard never
        # vanishes for the user.
        try:
            await update.message.reply_text(
                "🏠",
                reply_markup=_persistent_home_kb(),
            )
        except Exception as ke:
            print(f"[trader_setup] home_kb install failed: {ke}", flush=True)

        text = _fmt_hub_main(_uid(update))
        await update.message.reply_text(
            text, parse_mode=constants.ParseMode.MARKDOWN,
            reply_markup=_kb_hub_main(), disable_web_page_preview=True,
        )
    except Exception as e:
        await update.message.reply_text(f"❌ /trader failed: {str(e)[:200]}")
        traceback.print_exc(file=sys.stderr)


# ── Hub callback router ────────────────────────────────────────────────

async def cb_hub(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
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
    if not data.startswith("h:"):
        return

    try:
        uid = _uid(update)
        parts = data.split(":")
        screen = parts[1] if len(parts) > 1 else "m"

        if screen == "close":
            try:
                await q.message.delete()
            except Exception:
                pass
            return

        if screen == "m":
            text, kb = _fmt_hub_main(uid), _kb_hub_main()
        elif screen == "p" and len(parts) >= 3:
            # Position detail
            import trader_positions
            import trader_portfolio
            try:
                pid = int(parts[2])
            except ValueError:
                # Probably a "no-op" callback (e.g. the "…+X more" button)
                return
            row = trader_positions.get_position(pid)
            if row is None or str(row["user_id"]) != uid:
                text = "_Position not found._"
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("← Portfolio", callback_data="h:p")],
                ])
            else:
                is_open = row.get("status") == "open"
                # Only quote Jupiter for OPEN positions; closed rows have
                # static realized numbers stored.
                enriched = trader_portfolio.value_position(row) if is_open else row
                text = _fmt_position_detail(enriched, {})
                kb = _kb_position_detail(pid, is_open=is_open, mint=row.get("mint") or "")
        elif screen == "p":
            import trader_portfolio
            s = trader_portfolio.portfolio_summary(uid)
            text, kb = _fmt_portfolio_list(s), _kb_portfolio_list(s)
        elif screen == "w":
            text, kb = _fmt_wallet(uid), _kb_wallet()
        elif screen == "hist":
            text = _fmt_history(uid)
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Refresh", callback_data="h:hist"),
                 InlineKeyboardButton("🏠 Home",    callback_data="h:m")],
            ])
        elif screen == "c":
            import trader_portfolio
            s = trader_portfolio.portfolio_summary(uid)
            text = _fmt_closeall_confirm(uid)
            kb = _kb_closeall_confirm(s["n_open"] > 0)
        elif screen == "cy":
            # Confirmed close-all — execute
            import trader_positions, trader_orchestrator
            opens = trader_positions.list_open_positions(uid)
            text_lines = [f"*🚪 Closing {len(opens)} positions…*\n"]
            for pos in opens:
                try:
                    r = trader_orchestrator.sell(uid, pos["id"], sell_pct=1.0, live=True)
                    sig = r.get("sell_signature", "")[:16]
                    text_lines.append(f"✅ `#{pos['id']}` {sig}…")
                except Exception as e:
                    text_lines.append(f"❌ `#{pos['id']}` {str(e)[:80]}")
            text = "\n".join(text_lines)
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Home", callback_data="h:m")],
            ])
        else:
            text, kb = _fmt_hub_main(uid), _kb_hub_main()

        try:
            await q.edit_message_text(
                text, parse_mode=constants.ParseMode.MARKDOWN,
                reply_markup=kb, disable_web_page_preview=True,
            )
        except Exception as e:
            if "not modified" not in str(e).lower():
                raise

    except Exception as e:
        print(f"[trader_setup] hub callback failed: {e}", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)


# ── Registration ────────────────────────────────────────────────────────

async def handle_custom_buy_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Text-input handler for the ✏️ Custom buy-amount picker.
    Only fires when user_data['bs_state'] == 'awaiting_custom'. Otherwise
    returns silently so other text handlers (withdraw wizard, etc.) can
    process the message."""
    if not update.message or not update.message.text:
        return
    if (ctx.user_data or {}).get("bs_state") != "awaiting_custom":
        return
    text = update.message.text.strip()
    if text.lower() == "cancel":
        ctx.user_data.pop("bs_state", None)
        ctx.user_data.pop("bs_custom_slot", None)
        await update.message.reply_text("✖️ Cancelled.")
        raise ApplicationHandlerStop

    slot = ctx.user_data.get("bs_custom_slot")
    if slot is None:
        ctx.user_data.pop("bs_state", None)
        raise ApplicationHandlerStop

    try:
        amt = float(text)
    except ValueError:
        await update.message.reply_text(
            "Not a number. Try again (e.g. `0.15`) or `cancel`.",
        )
        raise ApplicationHandlerStop
    if not (0.0001 <= amt <= 10):
        await update.message.reply_text(
            "Out of range. Must be 0.0001 to 10 SOL. Try again or `cancel`.",
        )
        raise ApplicationHandlerStop

    uid = str(update.effective_user.id)
    import trader_positions as _tp
    s = _tp.get_user_settings(uid)
    presets = list(s["buy_presets_sol"])
    while len(presets) < 3:
        presets.append(0.05)
    presets[int(slot)] = amt
    _tp.set_user_settings(uid, buy_presets_sol=presets[:3])
    ctx.user_data.pop("bs_state", None)
    ctx.user_data.pop("bs_custom_slot", None)
    await update.message.reply_text(
        f"✅ Slot {int(slot)+1} set to *{amt}* SOL.\n\n"
        "Open `/trader → ⚙️ Settings → 🛒 Buy Amounts` to verify.",
        parse_mode=constants.ParseMode.MARKDOWN,
    )
    raise ApplicationHandlerStop


def register(app: Application, admin_ids: set[int]):
    global _admin_ids
    _admin_ids = set(admin_ids)
    # Hub (primary entry)
    app.add_handler(CommandHandler("trader", cmd_trader))
    app.add_handler(CommandHandler("start_trader", cmd_trader))  # alias
    app.add_handler(CommandHandler("history", cmd_history))
    # Settings (legacy power-user)
    app.add_handler(CommandHandler("setup", cmd_setup))
    # Callback routers
    app.add_handler(CallbackQueryHandler(cb_hub,   pattern=r"^h:"))
    app.add_handler(CallbackQueryHandler(cb_setup, pattern=r"^s:"))
    # Text-input handler for ✏️ Custom buy-amount. group=-10 to run
    # before default handlers, but it's a no-op unless bs_state is set.
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_buy_amount),
        group=-10,
    )
    print(f"[trader_setup] hub + settings registered (admin_ids={len(_admin_ids)})", flush=True)
