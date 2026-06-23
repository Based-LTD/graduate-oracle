"""
GRADUATE Telegram bot.

Commands:
  /start              — welcome + tier status
  /probe <CA>         — graduation probability + comparable historicals
  /watch <CA>         — pin a contract to your watchlist
  /unwatch <CA>       — remove from watchlist
  /portfolio          — current watchlist with live stats
  /alert <rule>       — subscribe to alerts (see /alert with no args for help)
  /alerts             — list your active alert rules
  /cancel <id>        — remove an alert rule
  /wallet <address>   — pump.fun history for a wallet
  /leaderboard        — top smart-money wallets right now
  /upgrade            — Pick a tier (Builder 0.4 SOL/mo · Pro 1 SOL/mo)
  /status             — your tier + alert quota
  /help               — this list

Reads the same SQLite DB as the web service. Reads the live observer snapshot
and historical curves directly from disk — no API key needed for the bot's own
internal queries.

Run:  python bot/main.py   (set TELEGRAM_BOT_TOKEN in .env)
"""
import asyncio
import json
import os
import contextlib
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

import httpx

# Make web/ importable so we can reuse db / sol_pay (small, DB-only modules)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "web"))

# Load .env from project root
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, constants
from telegram.ext import (
    Application, CommandHandler, ContextTypes, MessageHandler, filters,
)

import db
import sol_pay
import tg_fires
import webhooks as webhooks_mod

SNAPSHOT_PATH = ROOT / "observer-active.json"

# We talk to the local web service for grad_prob + wallet_intel data.
# Same VM, no auth needed (bound to localhost from the bot's perspective).
WEB_BASE = os.environ.get("WEB_BASE_URL", "http://127.0.0.1:8765").rstrip("/")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


# ── Launch-month TG promo ──────────────────────────────────────────────
# Set TG_FREE_UNTIL (unix timestamp) to make composite_score free for
# ALL telegram users through that timestamp. Read on every check so the
# window can be extended / cut short by flipping the secret — no deploy
# required. After the timestamp, paywall snaps back automatically.
def _tg_free_trial_active() -> bool:
    raw = os.environ.get("TG_FREE_UNTIL", "0").strip()
    try:
        return int(raw) > int(time.time())
    except ValueError:
        return False


def _tg_free_trial_end_label() -> str:
    """Human-readable end date for the launch-week TG promo. Used in copy
    so users see exactly when the free window closes."""
    raw = os.environ.get("TG_FREE_UNTIL", "0").strip()
    try:
        ts = int(raw)
        if ts <= 0: return ""
        import datetime as _dt
        d = _dt.datetime.utcfromtimestamp(ts)
        return d.strftime("%b %-d, %Y %H:%M UTC")
    except Exception:
        return ""


def _tg_free_banner() -> str:
    """Returns a celebratory promo banner when the TG trial is active,
    else an empty string. Prepended to /start, /plans, /upgrade copy so
    every visitor sees the offer immediately."""
    if not _tg_free_trial_active():
        return ""
    end = _tg_free_trial_end_label()
    return (
        "🎉 *LAUNCH PROMO — FREE THIS MONTH*\n"
        f"Composite signal ACT/WATCH/SCOUT alerts open to everyone through *{end}*.\n"
        "No SOL, no token, no signup. Subscribe with `/alert composite_score` and "
        "alerts start firing immediately. After the window closes, the paywall "
        "snaps back automatically.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )

# Admin allow-list for the /grant command. Set on Fly via:
#   fly secrets set ADMIN_TG_IDS=123456789,987654321
# Comma-separated telegram_ids. Anyone in this list can /grant any tier
# (Builder/Pro/Free) to themselves or another telegram_id without payment.
_ADMIN_TG_IDS: set[int] = set()
for _raw in os.environ.get("ADMIN_TG_IDS", "").split(","):
    _raw = _raw.strip()
    if _raw.isdigit():
        _ADMIN_TG_IDS.add(int(_raw))

sol_pay.init()


# ── async HTTP helpers (httpx with connection pool) ───────────────────────
_HTTP: Optional[httpx.AsyncClient] = None

def _http() -> httpx.AsyncClient:
    global _HTTP
    if _HTTP is None:
        _HTTP = httpx.AsyncClient(timeout=10.0, follow_redirects=False)
    return _HTTP


async def _api_get(path: str, params: Optional[dict] = None) -> Optional[dict]:
    try:
        r = await _http().get(WEB_BASE + path, params=params)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


# ── DB helpers for TG-specific tables ─────────────────────────────────────

def _ensure_tg_tables():
    with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=10)) as c, c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS tg_users (
            telegram_id   INTEGER PRIMARY KEY,
            username      TEXT,
            chat_id       INTEGER,
            joined_at     INTEGER NOT NULL,
            last_seen_at  INTEGER
        );
        CREATE TABLE IF NOT EXISTS tg_watchlist (
            telegram_id   INTEGER NOT NULL,
            mint          TEXT NOT NULL,
            added_at      INTEGER NOT NULL,
            PRIMARY KEY (telegram_id, mint)
        );
        CREATE TABLE IF NOT EXISTS tg_probes_daily (
            telegram_id INTEGER NOT NULL,
            day         INTEGER NOT NULL,
            n           INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (telegram_id, day)
        );
        """)
_ensure_tg_tables()


def _today_int() -> int:
    return int(time.strftime("%Y%m%d", time.gmtime()))


def _check_and_inc_probe(tg_id: int, cap: int) -> tuple[bool, int, int]:
    """Atomically: check today's /probe count vs cap, increment if under.
    Returns (allowed, used, cap)."""
    today = _today_int()
    with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=10)) as c, c:
        c.row_factory = sqlite3.Row
        row = c.execute("SELECT n FROM tg_probes_daily WHERE telegram_id=? AND day=?",
                        (tg_id, today)).fetchone()
        used = row["n"] if row else 0
        if cap >= 0 and used >= cap:
            return False, used, cap
        c.execute("""INSERT INTO tg_probes_daily (telegram_id, day, n) VALUES (?, ?, 1)
                     ON CONFLICT(telegram_id, day) DO UPDATE SET n = n + 1""",
                  (tg_id, today))
        return True, used + 1, cap


def _maybe_auto_subscribe_composite(telegram_id: int) -> Optional[bool]:
    """During the TG_FREE_UNTIL launch promo, silently ensure a user has a
    composite_score rule. Returns:
      • None  — promo not active (no-op)
      • False — user already had an active rule
      • True  — newly inserted

    Called from `_upsert_user` so EVERY interaction with the bot retro-
    actively subscribes legacy users (those who joined before the
    /start auto-subscribe was added 2026-06-17). After the promo expires,
    this is a no-op and the PAID_ALERT_KINDS gate at the dispatcher
    transparently stops free users — no rule cleanup needed.

    Wrapped in try/except so a DB hiccup never breaks the command path
    this helper is called from."""
    if not _tg_free_trial_active():
        return None
    try:
        now_ts = int(time.time())
        with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=10)) as c, c:
            row = c.execute(
                "SELECT id FROM tg_alert_rules "
                "WHERE telegram_id = ? AND kind = 'composite_score' AND active = 1",
                (telegram_id,),
            ).fetchone()
            if row:
                return False
            c.execute(
                "INSERT INTO tg_alert_rules "
                "(telegram_id, kind, threshold, params, active, created_at, activated_at) "
                "VALUES (?, 'composite_score', 0, '{}', 1, ?, ?)",
                (telegram_id, now_ts, now_ts),
            )
            return True
    except Exception as e:
        print(f"[auto_subscribe] failed for {telegram_id}: {e}", flush=True)
        return False


def _upsert_user(update: Update):
    u = update.effective_user
    chat = update.effective_chat
    if not u:
        return
    now = int(time.time())
    with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=10)) as c, c:
        c.execute("""INSERT INTO tg_users (telegram_id, username, chat_id, joined_at, last_seen_at)
                     VALUES (?, ?, ?, ?, ?)
                     ON CONFLICT(telegram_id) DO UPDATE SET
                       username = excluded.username,
                       chat_id = excluded.chat_id,
                       last_seen_at = excluded.last_seen_at""",
                  (u.id, u.username or "", chat.id if chat else None, now, now))
    # During the launch promo, any user who touches the bot gets retroactively
    # subscribed to composite_score. Legacy users (joined pre-2026-06-17) who
    # never re-tapped /start would otherwise stay invisible to the dispatcher
    # because they have no rule. This closes the gap silently.
    _maybe_auto_subscribe_composite(u.id)


def _user_tier(telegram_id: int) -> tuple[str, dict]:
    """Return (tier_name, tier_limits)."""
    with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=10)) as c, c:
        c.row_factory = sqlite3.Row
        row = c.execute(
            "SELECT tier FROM api_keys WHERE telegram_id = ? AND revoked = 0 ORDER BY id DESC LIMIT 1",
            (telegram_id,),
        ).fetchone()
    tier = row["tier"] if row else "free"
    return tier, db.TIERS[tier]


# ── snapshot reader ───────────────────────────────────────────────────────

def _read_snapshot() -> Optional[dict]:
    if not SNAPSHOT_PATH.exists():
        return None
    try:
        return json.loads(SNAPSHOT_PATH.read_text())
    except Exception:
        return None


def _find_in_snapshot(mint: str) -> Optional[dict]:
    snap = _read_snapshot()
    if not snap:
        return None
    return next((m for m in snap.get("mints", []) if m.get("mint") == mint), None)


# ── command handlers ──────────────────────────────────────────────────────

def _live_headline_line() -> str:
    """Pull live timing + accuracy. Median runway between our ≥0.70 call and
    the bonding curve completing — this is the bot/fast-trader edge."""
    try:
        with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=5)) as c, c:
            now = int(time.time())
            lags_rows = c.execute("""
                SELECT (o.graduated_at - p.predicted_at) AS lag_s
                  FROM predictions p
                  JOIN post_grad_outcomes o ON o.mint = p.mint
                 WHERE p.was_calibrated = 1
                   AND p.predicted_prob >= 0.70
                   AND p.age_bucket IN (30, 60)
                   AND p.actual_graduated = 1
                   AND o.graduated_at IS NOT NULL
                   AND p.predicted_at >= ?
            """, (now - 90 * 86400,)).fetchall()
            lags = sorted([r[0] for r in lags_rows if r[0] is not None and r[0] >= 0])
            row = c.execute(
                "SELECT COUNT(*) AS total, "
                "SUM(CASE WHEN actual_graduated=1 THEN 1 ELSE 0 END) AS hit "
                "FROM predictions WHERE predicted_prob >= 0.70 "
                "AND age_bucket IN (30, 60) AND actual_graduated IS NOT NULL "
                "AND predicted_at >= ?",
                (now - 30 * 86400,),
            ).fetchone()
        total = int(row[0] or 0); hit = int(row[1] or 0)
        if total >= 30 and len(lags) >= 30:
            p50 = lags[len(lags) // 2]
            runway = f"{p50}s" if p50 < 60 else f"{p50 // 60}m"
            pct = 100 * hit / total
            return (f"⚡ *Median runway:* `{runway}` between our ≥0.70 call and "
                    f"the bonding curve completing _(n={len(lags):,} grads)_.\n"
                    f"🎯 *Hit rate:* `{pct:.1f}%` of those calls graduate within 24h.")
    except Exception:
        pass
    return ("🛡 *950,000+ pump.fun mints indexed.* Every prediction publicly "
            "hashed _before_ the outcome was known.")


def welcome_text() -> str:
    """Hero pulled live each /start hit. The verdict line stays as the
    receipts-grade discipline anchor below the live number."""
    return _tg_free_banner() + (
        "🎯 *graduate-oracle*\n"
        "_pump.fun decoded · launching on @prooflaunch\\__\n\n"
        + _live_headline_line() + "\n\n"
        "🛡 _Every prediction publicly hashed before outcome._ "
        "950,000+ mints in our receipts chain.\n"
        "📊 _Three urgency tiers — ACT (~4min runway), WATCH (~7min), "
        "SCOUT (~10min). Pick what your speed allows._\n\n"
        "*This is a paid signal. Two ways in:*\n\n"
        "💎 *Subscribe in SOL* — `0.2 SOL/mo`\n"
        "    Founding rate locks forever. Type `/upgrade` for Phantom QR.\n\n"
        "🪙 *Hold 500,000 $GO* — auto-unlock\n"
        "    Sell → drops back. No double-charge. Token launches imminent.\n\n"
        "_Just looking? `/probe <CA>` scores any mint free. "
        "`/sample` shows the last 10 ACT calls + outcomes. "
        "`/verdict` shows the pre-registered receipt chain._\n\n"
        "_NFA · DYOR · prediction model output, not financial advice. "
        "Pump.fun is high-risk; positions can go to zero._"
    )




# Side-by-side tier comparison — the source of truth for "what do I get when
# I pay?" Surfaced via /plans, /upgrade with no args, and embedded in /start
# for first-time users. Gives every visitor a clear answer without having to
# leave Telegram for the website.
#
# 2026-06-16: wrapped in plans_explainer_text() so the launch-month TG promo
# banner can be prepended dynamically without a deploy. The constant remains
# for any external imports; the helper is what new call sites should use.
_PLANS_BODY = (
    "*GRADUATE — real-time pump.fun graduation alert*\n\n"
    "Median runway between our ≥0.70 confidence call and the bonding curve "
    "completing is seconds — enough for any sub-second bot or fast TG-sniper "
    "to enter on the curve before migration.\n\n"
    "*Two ways in:*\n\n"
    "💎 *Subscribe in SOL* — founding rate locks forever:\n"
    "  `/upgrade tg_paid` — *0.2 SOL/mo* — TG composite signal access\n"
    "  `/upgrade builder` — *0.4 SOL/mo* — Builder API (5,000 calls/day)\n"
    "  `/upgrade pro`     — *1 SOL/mo* — Pro API (50k calls/day · webhooks)\n\n"
    "🪙 *Hold $GO* — auto-unlock when held, reverts if you sell:\n"
    "  Hold *500,000 $GO* → TG composite signal access\n"
    "  Hold *2,500,000 $GO* → Builder API (firehose)\n\n"
    "*The API is the firehose* — every pump.fun mint scored in its first 60 "
    "seconds. The composite signal alert is the focused product on top.\n\n"
    "*Built for:*\n"
    "🤖 sniper bots · ⚡ fast TG-sniper traders\n"
    "🛣️ DEX aggregators · 🖥️ trading terminals\n\n"
    "_Every prediction publicly hashed before outcome was known. Pre-launch "
    "audit chain: graduateoracle.fun/verdict_\n\n"
    "→ `/upgrade` to start."
)


def plans_explainer_text() -> str:
    """Prepends the TG free-trial banner when the promo window is open."""
    return _tg_free_banner() + _PLANS_BODY


# Back-compat alias for any callers still reading the constant. The helper
# above is the right thing to call going forward — it adapts to the trial.
PLANS_EXPLAINER = _PLANS_BODY


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Dead-simple paywall-first welcome. We used to auto-subscribe new
    users to free grad_prob at threshold 0.70 — removed 2026-06-12 to
    align with the "paid or token-holder" pre-launch positioning. Users
    can still opt into the free signal explicitly with `/alert grad_prob`.

    2026-06-17: during the launch promo window (TG_FREE_UNTIL), /start
    auto-subscribes the user to composite_score so they have ZERO
    commands to learn. Just tap START → see celebration → wait for the
    first signal. Removes the "how do I actually start?" confusion users
    reported on day 1 of the promo.
    """
    # _upsert_user runs the auto-subscribe helper internally during the
    # promo, so by the time we get here the user already has a rule (if
    # eligible). We just need to know which case to render copy for.
    _upsert_user(update)
    tg_id = update.effective_user.id

    # Launch-promo fast path — celebratory copy.
    if _tg_free_trial_active():
        # Recheck rule existence to decide copy. _upsert_user just ensured
        # one exists; this tells us whether it was already there or new.
        # (We can't reuse the helper's return value because _upsert_user
        # doesn't propagate it — and that's fine, this is a single SELECT.)
        try:
            with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=10)) as c:
                row = c.execute(
                    "SELECT created_at FROM tg_alert_rules "
                    "WHERE telegram_id = ? AND kind = 'composite_score' AND active = 1",
                    (tg_id,),
                ).fetchone()
            # If the rule was created in the last 5 seconds, treat as "just
            # subscribed" (this exact /start call). Otherwise "already".
            already = bool(row and (int(time.time()) - int(row[0]) > 5))
        except Exception:
            already = False

        end = _tg_free_trial_end_label()
        head = "You're already subscribed." if already else "Just subscribed you to *composite_score*."
        msg = (
            "🎉 *YOU'RE IN — FREE THIS MONTH*\n\n"
            f"{head} ⚡ACT / 📊WATCH / 🛰SCOUT alerts will fire to this chat automatically "
            f"as they hit, all the way through *{end}*.\n\n"
            "*What this is:* the same composite signal we publish at "
            "graduateoracle.fun/accuracy — real-time, hashed before outcome was known.\n\n"
            "*That's it. Sit back and wait for the first ACT to hit.*\n\n"
            "_Useful commands:_\n"
            "  `/alerts` — see your active subscriptions\n"
            "  `/probe <CA>` — score any mint right now (free)\n"
            "  `/sample` — last 10 ACT calls + outcomes\n"
            "  `/verdict` — the pre-launch audit chain\n\n"
            "_NFA · DYOR · pump.fun is high-risk, positions can go to zero._"
        )
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("📊 Live demo",  url="https://graduateoracle.fun/"),
            InlineKeyboardButton("🔍 Receipts",   url="https://graduateoracle.fun/accuracy"),
        ]])
        await update.message.reply_text(msg, parse_mode=constants.ParseMode.MARKDOWN, reply_markup=kb)
        return

    # Default (post-promo) — paywall pitch with subscribe buttons.
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("💎 Subscribe in SOL", url="https://graduateoracle.fun/api"),
        InlineKeyboardButton("🪙 $GO info",         url="https://graduateoracle.fun/for-terminals"),
    ]])
    await update.message.reply_text(
        welcome_text(), parse_mode=constants.ParseMode.MARKDOWN, reply_markup=kb,
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _upsert_user(update)
    await update.message.reply_text(welcome_text(), parse_mode=constants.ParseMode.MARKDOWN)


async def cmd_plans(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Side-by-side tier comparison. Most-asked question; this is the answer."""
    _upsert_user(update)
    await update.message.reply_text(plans_explainer_text(), parse_mode=constants.ParseMode.MARKDOWN)


async def cmd_grant(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Admin-only — issue a tier key without payment. Used to dogfood the
    bot and to comp partners / early users.

    Auth: requester's telegram_id must appear in ADMIN_TG_IDS (env var).

    Usage:
      /grant pro                 — grant Pro to yourself
      /grant builder 123456789   — grant Builder to telegram_id 123456789
      /grant pro 30              — grant Pro for 30 days
      /grant pro 123456789 90    — grant Pro to that user for 90 days

    Issues a fresh API key bound to the target telegram_id and replies
    with the plaintext (shown once — admin must DM it to the recipient
    out-of-band)."""
    _upsert_user(update)
    requester = update.effective_user.id
    if requester not in _ADMIN_TG_IDS:
        await update.message.reply_text("not authorized.")
        return

    if not ctx.args:
        await update.message.reply_text(
            "*usage:*\n"
            "`/grant pro` — Pro to yourself\n"
            "`/grant builder <telegram_id>` — Builder to someone else\n"
            "`/grant pro 365` — Pro to yourself for 365 days\n"
            "`/grant pro <telegram_id> <days>` — Pro to someone else for N days",
            parse_mode=constants.ParseMode.MARKDOWN,
        )
        return

    tier = ctx.args[0].strip().lower()
    if tier not in db.TIERS:
        await update.message.reply_text(
            f"unknown tier `{tier}` — must be one of: {', '.join(db.TIERS.keys())}",
            parse_mode=constants.ParseMode.MARKDOWN,
        )
        return

    # Parse optional [target_tg_id] [days]. Heuristic: if the next arg is
    # a small int (<10000), treat it as days; else as a telegram_id.
    target_tg = requester
    days: Optional[int] = None
    rest = [a for a in ctx.args[1:]]
    if rest:
        try:
            n = int(rest[0])
            if n < 100000:
                days = n
            else:
                target_tg = n
                if len(rest) >= 2:
                    days = int(rest[1])
        except ValueError:
            await update.message.reply_text("args must be integers (telegram_id and/or days).")
            return

    rec = db.create_key(
        tier=tier,
        telegram_id=target_tg,
        label=f"granted by {requester}",
        expires_in_days=days,
    )
    expiry = (
        f"expires in *{days}* days"
        if days else "_no expiry — comp grant_"
    )
    target_label = "yourself" if target_tg == requester else f"`{target_tg}`"

    # Reply directly to the admin with the new key. Plain text — the API
    # key contains underscores from secrets.token_urlsafe() that trip up
    # Telegram's MARKDOWN parser even inside backticks. Plain text is
    # bulletproof and the key is still copyable on long-press.
    target_plain = "yourself" if target_tg == requester else str(target_tg)
    expiry_plain = (f"expires in {days} days" if days
                    else "no expiry — comp grant")
    tail = (
        "DM this key to the recipient. TG bot tier auto-picks up via "
        "telegram_id binding."
        if target_tg != requester else
        f"you're now on this tier — try /status or /alert grad_prob 30."
    )
    await update.message.reply_text(
        f"✅ {tier.upper()} granted to {target_plain}.\n\n"
        f"key:    {rec['key']}\n"
        f"prefix: {rec['prefix']}\n"
        f"id:     {rec['id']}\n"
        f"{expiry_plain}\n\n"
        f"{tail}",
    )


async def cmd_accuracy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Live receipts — same number we put on the website. ONE signal,
    two methods of validation (LOO backtest + forward production), same
    answer. No legacy runner / heuristic noise."""
    _upsert_user(update)
    d = await _api_get("/api/accuracy")
    if not d:
        await update.message.reply_text(
            "couldn't pull /api/accuracy right now — try /accuracy again in a sec.",
            parse_mode=constants.ParseMode.MARKDOWN,
        )
        return

    def _pct(v): return "—" if v is None else f"{v*100:.0f}%"
    def _n(v): return "—" if v is None else f"{int(v):,}"

    # Graduates · on-chain verified — forward_calibrated pickBest, the
    # SAME number /accuracy + hero + ticker show (sitewide consistency).
    # Was bound to d.lifetime (LOO, chronically warming/None) — dead-track
    # binding, fixed 2026-05-16 with the rest of the sweep.
    def _pick_band(src):
        if not src:
            return None
        ok = [b for b in (src.get("thresholds") or {}).values()
              if b.get("actual_grad_rate") is not None and (b.get("n_resolved") or 0) >= 30]
        over = [b for b in ok if b["actual_grad_rate"] >= b["threshold_pct"] / 100]
        if over:
            return sorted(over, key=lambda b: -b["threshold_pct"])[0]
        if ok:
            return sorted(ok, key=lambda b: b["threshold_pct"]/100 - b["actual_grad_rate"])[0]
        return None

    _fc = d.get("forward_calibrated") or d.get("forward") or {}
    _band = _pick_band(_fc)
    if _band:
        backtest_pct = _band["actual_grad_rate"]
        backtest_n   = _band["n_resolved"]
        _band_pct    = _band["threshold_pct"]
    else:
        backtest_pct = backtest_n = _band_pct = None

    # Live composite signal — deliberately pre-verdict (the n=7 discipline).
    # Replaces the structurally-unfillable act_slice dead metric. Matches
    # the /accuracy "Live composite signal" card exactly.
    live_line_a = "🛰 *Live composite signal:* _forward-validating_"
    live_line_b = ("   _3-tier ACT/WATCH/SCOUT · verdict publishes at the "
                   "pre-registered sample floor — no rate before it's earned_")

    # Post-bond sustain rate — co-billed with graduation rates per the
    # honesty pass. Graduation alone isn't a profit thesis: many bonded
    # mints dump on PumpSwap. n is large (corpus accrues from observer's
    # post-bond price polling) so this is rarely warming in practice.
    pg = d.get("post_graduation") or {}
    pg_rate = pg.get("sustain_rate_30m")
    pg_n = pg.get("n_resolved_30m")
    if pg_rate is not None:
        sustain_line_a = (
            f"🔁 *Sustains 30m post-bond:* *{_pct(pg_rate)}* of graduates held "
            f"≥80% of grad price"
        )
        sustain_line_b = f"   _independent on-chain DEX measurement, n={_n(pg_n)}_"
    else:
        sustain_line_a = "🔁 *Sustains 30m post-bond:* _warming_"
        sustain_line_b = "   _accruing on-chain DEX outcomes_"

    dr = d.get("drift") or {}
    status = "DRIFT DETECTED ⚠️" if dr.get("drift_detected") else "CALIBRATED · STABLE ✓"

    # Timing edge — median runway between our ≥0.70 call and the bonding
    # curve completing. The headline number on the website too.
    timing = (d.get("headline") or {}).get("time_to_grad") or {}
    last30 = (d.get("headline") or {}).get("last_30d") or {}
    if timing.get("status") == "ok" and timing.get("p50_s", 0) > 0:
        p50 = timing["p50_s"]
        runway = f"{p50}s" if p50 < 60 else f"{p50 // 60}m"
        n_grads = timing.get("n_grads", 0)
        runway_line_a = f"⚡ *Median runway:* `{runway}` between our ≥0.70 call and the bonding curve completing"
        runway_line_b = f"   _n={_n(n_grads)} grads · bot-actionable in real time_"
    else:
        runway_line_a = "⚡ *Median runway:* _warming_"
        runway_line_b = "   _accruing resolved graduations_"

    if last30.get("status") == "ok" and last30.get("n_resolved", 0) > 0:
        hit_line_a = f"🎯 *Calibrated accuracy:* *{_pct(last30['hit_rate'])}* of ≥0.70 calls graduate within 24h"
        hit_line_b = f"   _last 30d · n={_n(last30['n_resolved'])} · hashed before outcome_"
    else:
        hit_line_a = "🎯 *Calibrated accuracy:* _warming_"
        hit_line_b = "   _accruing resolved outcomes_"

    lines = [
        "*graduate-oracle — the receipts*",
        "_real-time pump.fun graduation alert · built for fast traders + bots_",
        "",
        runway_line_a,
        runway_line_b,
        "",
        hit_line_a,
        hit_line_b,
        "",
        sustain_line_a,
        sustain_line_b,
        "",
        f"_System: {status}_",
        "",
        "→ Full receipts: graduateoracle.fun/accuracy",
        "→ Pre-launch audit: graduateoracle.fun/verdict",
        "",
        "_NFA · DYOR · graduation alone is not a profit thesis — see post-bond above_",
    ]

    await update.message.reply_text("\n".join(lines), parse_mode=constants.ParseMode.MARKDOWN,
                                    disable_web_page_preview=True)


async def cmd_probe(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _upsert_user(update)
    if not ctx.args:
        await update.message.reply_text("usage: `/probe <contract address>`",
                                        parse_mode=constants.ParseMode.MARKDOWN)
        return
    tg_id = update.effective_user.id
    tier, lim = _user_tier(tg_id)
    cap = lim.get("tg_probes_per_day", 25)
    allowed, used, _ = _check_and_inc_probe(tg_id, cap)
    if not allowed:
        await update.message.reply_text(
            f"daily /probe limit reached ({used}/{cap}).\n"
            "→ `/upgrade builder` (0.4 SOL/mo) for 200/day · `/upgrade pro` (1 SOL/mo) for unlimited",
            parse_mode=constants.ParseMode.MARKDOWN,
        )
        return
    mint = ctx.args[0].strip()

    # Ask the local web service for live data — it's already scored.
    live = await _api_get("/api/live", params={"limit": 200})
    snap_mint = None
    if live:
        snap_mint = next((m for m in live.get("mints", []) if m.get("mint") == mint), None)

    if not snap_mint:
        await update.message.reply_text(
            f"`{mint[:8]}…` not in the live observer set right now. "
            "score is only computed for currently-active mints.",
            parse_mode=constants.ParseMode.MARKDOWN,
        )
        return

    m = snap_mint
    prob = m.get("grad_prob")
    cp = m.get("combined_prob") or {}
    combined = cp.get("prob")
    combined_str = f"*{combined*100:.0f}%*" if combined is not None else "—"
    # Breakdown line — shows which signal is driving combined.
    if cp.get("early_warming"):
        breakdown = f"_curve {(prob or 0)*100:.0f}% · early signal warming · combined = curve_"
    elif cp.get("early_prob") is not None:
        breakdown = (
            f"_curve {(cp.get('curve_prob') or 0)*100:.0f}% · "
            f"early {(cp.get('early_prob') or 0)*100:.0f}% · "
            f"weighted {cp.get('weight_early', 0)*100:.0f}% early / "
            f"{cp.get('weight_curve', 0)*100:.0f}% curve_"
        )
    else:
        breakdown = ""
    is_mayhem = m.get("is_mayhem_mode")
    flag = "⚠️ MAYHEM" if is_mayhem else ("✅ non-mayhem" if is_mayhem is False else "❓ mayhem-status pending")

    def pct(v):
        return "—" if v is None else f"{v*100:.0f}%"

    # From-now upside — what the trader actually wants to know:
    # P(this mint reaches Nx its CURRENT price), not its launch price.
    p2 = pct(m.get("runner_prob_2x_from_now"))
    p5 = pct(m.get("runner_prob_5x_from_now"))
    p10 = pct(m.get("runner_prob_10x_from_now"))
    peak_now = m.get("expected_upside_from_now")
    peak_now_s = f"{peak_now:.2f}× from now" if peak_now is not None else "—"

    # Creator track record (if known)
    c = m.get("creator_history")
    creator_lines = ""
    if c:
        runner_tag = " · *▲ runner dev*" if c.get("runner_creator") else (" · ✓ verified dev" if c.get("good_creator") else "")
        creator_lines = (
            f"\n*creator:* `{c['creator'][:6]}…{c['creator'][-4:]}` — "
            f"{c['n_launches']} launches · "
            f"{c['grad_rate']*100:.0f}% grad · "
            f"{c['rate_5x']*100:.0f}% 5×{runner_tag}"
        )
    elif m.get("first_buyer"):
        creator_lines = "\n*creator:* new (no prior history)"

    # Live signals — smart money, bundles, fees, dex-paid, manufactured
    signal_bits = []
    n_smart = m.get("smart_money_in") or 0
    if n_smart:
        signal_bits.append(f"▲ *{n_smart}* smart money in")
    bun = m.get("bundle") or {}
    if bun.get("detected"):
        signal_bits.append(f"⚠ bundle *{bun.get('size')}* wallets · *{bun.get('pct'):.0f}%* of supply")
    fd = m.get("fee_delegation") or {}
    if (fd.get("total_bps") or 0) > 0:
        signal_bits.append(f"🤝 fees *{(fd.get('total_bps') or 0) / 100:.0f}%* delegated")
    dx = m.get("dex_paid") or {}
    if dx.get("is_paid"):
        signal_bits.append("🟢 DEX paid")
    if m.get("manufactured_pump"):
        signal_bits.append("🔥 hot launch")
    flags = m.get("bot_flags") or []
    if flags:
        signal_bits.append(f"flags: {', '.join(flags)}")
    signals_line = ("\n" + "  ·  ".join(signal_bits)) if signal_bits else ""

    # Post-grad survival predictor — only meaningful for near-grad mints.
    pgs = m.get("post_grad_survival_prob") or {}
    nearGrad = (m.get('current_vsol_sol') or 0) >= 80 or (prob or 0) >= 0.5
    pgs_line = ""
    if pgs and nearGrad:
        status = pgs.get("status")
        if status == "warming":
            pgs_line = f"\n*post-grad sustain:* warming · {pgs.get('n_total_resolved') or 0}/30 resolved samples"
        elif status == "warming_clean_corpus_accumulating":
            # Finding 7e (2026-05-07): clean corpus rebuilding post-fix.
            n = pgs.get('n_total_resolved') or 0
            pgs_line = f"\n*post-grad sustain:* warming · {n}/30 clean post-fix samples"
        elif status in ("warming_loose_match", "warming_too_few_matches",
                        "metric_recalibration_in_progress",
                        "sunset_pending_architecture_review",
                        "sunset_pending_validation_rerun",
                        "sunset_lane_60s_structural_limit"):
            # Skip rendering for any non-live diagnostic status, including
            # the Finding 7i permanent-sunset terminal state.
            pgs_line = ""
        elif pgs.get("prob") is not None:
            pgs_line = f"\n*post-grad sustain (30m):* *{pgs.get('prob')*100:.0f}%* (k-NN over {pgs.get('n_total_resolved')} resolved graduates)"

    # Early-grad predictor — uses ONLY at-launch features (creator, smart
    # money, cluster, fee delegation, etc.) to predict graduation BEFORE
    # the curve reveals itself. Most valuable when divergent from main
    # grad_prob (e.g. low curve-shape grad_prob but high early signal).
    egp = m.get("early_grad_prob") or {}
    egp_line = ""
    if egp.get("status") == "live" and egp.get("prob") is not None:
        egp_pct = egp["prob"] * 100
        # Show divergence vs main grad_prob — the alpha signal
        diff = (egp["prob"] - (prob or 0)) * 100
        diff_tag = (f" *(+{diff:.0f}pp vs curve)*" if diff > 10
                    else f" _(-{abs(diff):.0f}pp vs curve)_" if diff < -10
                    else "")
        egp_line = f"\n*early-grad signal:* *{egp_pct:.0f}%*{diff_tag} · k-NN at-launch features only"
    elif egp.get("status") == "warming":
        egp_line = f"\n*early-grad signal:* warming · {egp.get('n_total_resolved') or 0}/30 resolved samples"

    # Token name + symbol header — pulled from on-chain Token-2022 metadata.
    meta = m.get("metadata") or {}
    name_line = ""
    if meta.get("name") or meta.get("symbol"):
        name_part = meta.get("name") or ""
        sym_part = f" · `${meta.get('symbol')}`" if meta.get("symbol") else ""
        name_line = f"*{name_part}*{sym_part}\n"

    # Market cap line — shows USD when available, falls back to SOL.
    mc = m.get("market_cap") or {}
    if mc.get("usd"):
        mc_line = f"\n*MC:* ${mc['usd']:,}  ·  ({mc['sol']:.0f} SOL)"
    elif mc.get("sol"):
        mc_line = f"\n*MC:* {mc['sol']:.0f} SOL"
    else:
        mc_line = ""

    text = (
        f"{name_line}"
        f"`{mint}`  ·  {flag}\n\n"
        f"🎯 *combined odds:* {combined_str}\n"
        f"{breakdown}"
        f"{mc_line}\n\n"
        f"*runner odds (from current price):*\n"
        f"  ≥2× → *{p2}*  ·  ≥5× → *{p5}*  ·  ≥10× → *{p10}*\n"
        f"  expected peak: *{peak_now_s}*\n\n"
        f"vSOL *{m['current_vsol_sol']:.1f}*  ·  "
        f"buyers *{m['unique_buyers']}*  ·  "
        f"age *{m['age_s']:.0f}s*  ·  "
        f"mult *{m['current_mult']:.2f}×*"
        f"{creator_lines}"
        f"{signals_line}"
        f"{pgs_line}"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Pump", url=f"https://pump.fun/coin/{mint}"),
        InlineKeyboardButton("Axiom", url=f"https://axiom.trade/t/{mint}"),
        InlineKeyboardButton("Dex", url=f"https://dexscreener.com/solana/{mint}"),
    ]])
    await update.message.reply_text(text, parse_mode=constants.ParseMode.MARKDOWN, reply_markup=kb)


async def cmd_watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _upsert_user(update)
    if not ctx.args:
        await update.message.reply_text("usage: `/watch <contract address>`",
                                        parse_mode=constants.ParseMode.MARKDOWN)
        return
    mint = ctx.args[0].strip()
    tg_id = update.effective_user.id
    tier, lim = _user_tier(tg_id)
    with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=10)) as c, c:
        c.row_factory = sqlite3.Row
        n = c.execute("SELECT COUNT(*) AS n FROM tg_watchlist WHERE telegram_id = ?", (tg_id,)).fetchone()["n"]
        cap = lim["watchlist"]
        if cap >= 0 and n >= cap:
            await update.message.reply_text(
                f"watchlist full ({n}/{cap}). `/upgrade builder` or `/upgrade pro` for unlimited slots.")
            return
        c.execute("INSERT OR IGNORE INTO tg_watchlist (telegram_id, mint, added_at) VALUES (?, ?, ?)",
                  (tg_id, mint, int(time.time())))
    await update.message.reply_text(f"⭐ pinned `{mint[:6]}…{mint[-4:]}`", parse_mode=constants.ParseMode.MARKDOWN)


async def cmd_unwatch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _upsert_user(update)
    if not ctx.args:
        await update.message.reply_text("usage: `/unwatch <contract address>`",
                                        parse_mode=constants.ParseMode.MARKDOWN)
        return
    mint = ctx.args[0].strip()
    with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=10)) as c, c:
        c.execute("DELETE FROM tg_watchlist WHERE telegram_id = ? AND mint = ?",
                  (update.effective_user.id, mint))
    await update.message.reply_text("removed.")


async def cmd_portfolio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _upsert_user(update)
    tg_id = update.effective_user.id
    # Admins (operators using the trader): route /portfolio to the
    # TRADER portfolio (positions), not the legacy watchlist. The
    # trader portfolio is what the persistent home keyboard + menu
    # imply, so the watchlist meaning would surprise them.
    if tg_id in _ADMIN_TG_IDS:
        try:
            import trader_commands as _tc
            return await _tc.cmd_portfolio(update, ctx)
        except Exception as e:
            print(f"[bot] /portfolio trader path failed, falling back to watchlist: {e}",
                  flush=True)
            # Fall through to legacy watchlist if trader path crashes —
            # better to show something than nothing
    with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=10)) as c, c:
        c.row_factory = sqlite3.Row
        rows = c.execute("SELECT mint FROM tg_watchlist WHERE telegram_id = ? ORDER BY added_at DESC",
                         (tg_id,)).fetchall()
    if not rows:
        await update.message.reply_text("watchlist empty. add one: `/watch <CA>`",
                                        parse_mode=constants.ParseMode.MARKDOWN)
        return
    live = await _api_get("/api/live", params={"limit": 200}) or {}
    by_mint = {m["mint"]: m for m in live.get("mints", [])}
    lines = ["*your watchlist*"]
    for r in rows:
        m = by_mint.get(r["mint"])
        if m:
            p = m.get("grad_prob")
            ps = f"{p*100:.0f}%" if p is not None else "—"
            lines.append(f"`{r['mint'][:6]}…{r['mint'][-4:]}` · vSOL {m['current_vsol_sol']:.1f} · {m['unique_buyers']} buyers · *{ps}*")
        else:
            lines.append(f"`{r['mint'][:6]}…{r['mint'][-4:]}` · ○ idle")
    await update.message.reply_text("\n".join(lines), parse_mode=constants.ParseMode.MARKDOWN)


async def cmd_leaderboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _upsert_user(update)
    data = await _api_get("/api/wallets", params={"kind": "smart", "limit": 10, "min_total": 8})
    if not data or not data.get("wallets"):
        await update.message.reply_text("wallet index still warming up — try again in a minute.")
        return
    rows = data["wallets"]
    n_indexed = data.get("n_wallets_indexed", 0)
    lines = [f"*top smart-money wallets* (of {n_indexed:,} indexed)"]
    for i, w in enumerate(rows, 1):
        lines.append(
            f"{i}. `{w['wallet']}` · {w['total']} mints · {w['graduated']} grads · "
            f"smart {w['smart_score']:.2f}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode=constants.ParseMode.MARKDOWN)


async def cmd_wallet(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _upsert_user(update)
    if not ctx.args:
        # No address → show the user's TRADER wallet (custodial) instead of
        # bouncing with a usage error. The trader hub uses /trader → Wallet
        # but /wallet directly here is a faster path for the common case.
        # Falls through to the old smart-money lookup when an address IS
        # passed (preserves backward compatibility).
        tg_id = update.effective_user.id
        if tg_id in _ADMIN_TG_IDS:
            try:
                import trader_wallets
                wallet = trader_wallets.get_or_create_wallet(str(tg_id))
                pk = wallet["public_key"]
                try:
                    bal = trader_wallets.get_balance_sol(pk)
                    bal_str = f"*{bal:.6f}* SOL"
                except Exception:
                    bal_str = "_RPC unavailable_"
                await update.message.reply_text(
                    f"💰 *Your trader wallet*\n\n"
                    f"Balance: {bal_str}\n\n"
                    f"`{pk}`\n\n"
                    "_For deposit / withdraw, tap /trader → 💰 Wallet._",
                    parse_mode=constants.ParseMode.MARKDOWN,
                )
                return
            except Exception as e:
                await update.message.reply_text(f"❌ wallet view failed: {str(e)[:200]}")
                return
        # Non-admin: original usage hint
        await update.message.reply_text(
            "usage: `/wallet <address>` (smart-money lookup)",
            parse_mode=constants.ParseMode.MARKDOWN,
        )
        return
    addr = ctx.args[0].strip()
    short = addr[:12]
    data = await _api_get(f"/api/wallet/{short}")
    if not data or not data.get("found"):
        await update.message.reply_text(f"no observed pump.fun activity for `{short}…`",
                                        parse_mode=constants.ParseMode.MARKDOWN)
        return
    rec = data["stats"]
    text = (
        f"*wallet `{short}`*\n\n"
        f"mints touched: *{rec['total']}*\n"
        f"graduated: {rec['graduated']}  ·  runners 2x+: {rec['runner']}  ·  rugs: {rec['rug']}\n"
        f"grad rate: *{rec['grad_rate']*100:.1f}%*  ·  good rate: *{rec['good_rate']*100:.1f}%*\n"
        f"smart score: *{rec['smart_score']:.3f}*"
    )
    await update.message.reply_text(text, parse_mode=constants.ParseMode.MARKDOWN)


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _upsert_user(update)
    tg_id = update.effective_user.id
    tier, lim = _user_tier(tg_id)
    with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=10)) as c, c:
        c.row_factory = sqlite3.Row
        watch_n = c.execute("SELECT COUNT(*) AS n FROM tg_watchlist WHERE telegram_id = ?", (tg_id,)).fetchone()["n"]
        rules_n = c.execute("SELECT COUNT(*) AS n FROM tg_alert_rules WHERE telegram_id = ? AND active = 1",
                            (tg_id,)).fetchone()["n"]
    cap_w = "∞" if lim["watchlist"] < 0 else lim["watchlist"]
    cap_a = "∞" if lim["alerts"] < 0 else lim["alerts"]
    text = (
        f"tier: *{lim['label']}*\n"
        f"watchlist: {watch_n} / {cap_w}\n"
        f"alerts:    {rules_n} / {cap_a}\n"
        f"realtime:  {'✅' if lim['realtime'] else '❌'}\n"
    )
    if tier == "free":
        text += "\n\n→ `/upgrade builder` (0.4 SOL/mo) — premium alerts + real-time data\n→ `/upgrade pro` (1 SOL/mo) — unlimited everything"
    await update.message.reply_text(text, parse_mode=constants.ParseMode.MARKDOWN)


# Alert kinds split by tier. The free kinds are publicly observable from any
# pump.fun websocket; the paid kinds require our wallet leaderboard, creator
# history index, or from-now math — they are the differentiated alpha and gate
# the upgrade pitch.
# SIMPLIFICATION (2026-05-04): the product is one signal — grad_prob.
# All other alert kinds were creating noise / turds. Backtest data showed
# only grad_prob has a calibrated edge worth interrupting users for. Other
# signals (smart money, whales, creator history, etc.) still appear INSIDE
# the alert as supporting context, but they don't fire separate alerts.
FREE_ALERT_KINDS = {"grad_prob"}
PAID_ALERT_KINDS: set[str] = {"composite_score"}  # gated at fire-dispatch time


async def cmd_alert(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _upsert_user(update)
    if len(ctx.args) < 1:
        await update.message.reply_text(
            "🎯 *graduate-oracle*\n\n"
            "*One signal:* grad_prob ≥ 70% with clean entry.\n"
            "Forward, on-chain resolved: 58% of ≥70%-confidence calls graduated (n=2,400+).\n\n"
            "*Commands:*\n"
            "  `/alert composite_score`   — ⚡the live product: 3-tier ACT/WATCH/SCOUT signal\n"
            "  `/alert composite_score act` — ACT tier only (highest conviction)\n"
            "  `/alert grad_prob`         — graduation-probability track (legacy)\n"
            "  `/alert grad_prob 80`      — tighter grad_prob\n"
            "  `/alerts`                  — see your active rules\n"
            "  `/alert remove composite_score`  — unsubscribe\n\n"
            "_Threshold floor is 50% — values below that are model noise (15-17% historical)._\n\n"
            "Every alert includes the full context: smart-money, whales, "
            "creator history, bundle/dex/fee flags, and the calibration receipt.\n\n"
            "_NFA · DYOR · prediction model output, not financial advice._",
            parse_mode=constants.ParseMode.MARKDOWN,
        )
        return
    tg_id = update.effective_user.id
    tier, lim = _user_tier(tg_id)
    cap = lim["alerts"]
    # During TG_FREE_UNTIL window, the bot is fully free — and that has to
    # mean *no* slot caps either, otherwise free users get pushed into a
    # swap dance and the promo is hostile. After the window the paid cap
    # (1 for free tier) snaps back.
    if tier == "free" and _tg_free_trial_active():
        cap = -1
    kind = ctx.args[0]

    # Handle remove (e.g. /alert remove grad_prob)
    if kind == "remove" and len(ctx.args) >= 2:
        target_kind = ctx.args[1]
        with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=10)) as c, c:
            cur = c.execute(
                "UPDATE tg_alert_rules SET active = 0 WHERE telegram_id = ? AND kind = ? AND active = 1",
                (tg_id, target_kind),
            )
            n = cur.rowcount
        msg = f"✓ removed {n} active `{target_kind}` rule(s)" if n else f"no active `{target_kind}` rule to remove"
        await update.message.reply_text(msg, parse_mode=constants.ParseMode.MARKDOWN)
        return

    # composite_score — the 3-tier composite product (ACT/WATCH/SCOUT).
    # Self-serve subscribe. Optional min-tier arg. Self-contained branch:
    # returns before the grad_prob-only validation below, leaving that path
    # untouched (purely additive). Pricing/tier-gating is applied in the
    # paywall layer (web/alert_push) — pilot phase is open while the
    # forward-validation verdict is pending; this is stated honestly to
    # the user rather than fabricating a price.
    if kind == "composite_score":
        # 2026-06-11: composite_score is now gated. Verdict landed Jun 2;
        # pilot is over. Check effective_tier (paid OR token-held) against
        # db.COMPOSITE_SIGNAL_REQUIRED_TIER. Token holders auto-qualify the
        # moment ORACLE_MINT is set + their wallet is linked.
        # During TG_FREE_UNTIL window, skip the gate entirely — launch promo.
        req = getattr(db, "COMPOSITE_SIGNAL_REQUIRED_TIER", None)
        if req is not None and not _tg_free_trial_active():
            # Look up paid tier + token-held tier; allow higher of the two.
            # expires_at IS NULL: token-holder / comp key (no time bound).
            # expires_at > now: still within paid window.
            now_ts = int(time.time())
            with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=10)) as c, c:
                c.row_factory = sqlite3.Row
                row = c.execute(
                    "SELECT tier, COALESCE(token_held_tier, 'free') AS token_tier "
                    "FROM api_keys WHERE telegram_id = ? AND revoked = 0 "
                    "AND (expires_at IS NULL OR expires_at > ?) "
                    "ORDER BY id DESC LIMIT 1",
                    (tg_id, now_ts),
                ).fetchone()
            paid_t = row["tier"] if row else "free"
            token_t = row["token_tier"] if row else "free"
            rank = ["free", "tg_paid", "builder", "pro", "unlimited"]
            def idx(t): return rank.index(t) if t in rank else 0
            eff_idx = max(idx(paid_t), idx(token_t))
            if eff_idx < idx(req):
                await update.message.reply_text(
                    "🔒 *Composite signal is now paid.*\n\n"
                    "The pilot ended when the pre-registered verdict cycle "
                    "completed Jun 2.\n\n"
                    "*Two ways in:*\n"
                    "  💎 `/upgrade tg_paid` — *0.2 SOL/mo* (founding rate, locked forever)\n"
                    "  🪙 Hold *500,000 $GO* in a linked wallet — auto-upgrade when held, "
                    "reverts if you sell\n\n"
                    "_Subscribe now and your price never goes up. Or hold $GO for "
                    "the perpetual-access path — pick whichever fits._",
                    parse_mode=constants.ParseMode.MARKDOWN,
                )
                return
        min_tier = None
        if len(ctx.args) > 1:
            arg = ctx.args[1].strip().upper()
            if arg in ("ACT", "WATCH", "SCOUT"):
                min_tier = arg
            else:
                await update.message.reply_text(
                    "usage: `/alert composite_score` (all tiers) · "
                    "`/alert composite_score act` (ACT only) · "
                    "`/alert composite_score watch` (ACT+WATCH)",
                    parse_mode=constants.ParseMode.MARKDOWN,
                )
                return
        with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=10)) as c, c:
            c.row_factory = sqlite3.Row
            existing_rows = c.execute(
                "SELECT id, kind, threshold FROM tg_alert_rules "
                "WHERE telegram_id = ? AND active = 1 ORDER BY id",
                (tg_id,),
            ).fetchall()
            n = len(existing_rows)
            # Free-tier convenience: a user with only ONE non-composite slot
            # in use almost always wants to swap it for composite — that IS
            # the live product. Auto-deactivate the old rule, log clearly so
            # they know what happened, and keep going. Anything more nuanced
            # (multiple rules, paid tier) falls through to the explicit
            # "swap or upgrade" prompt below.
            non_composite = [r for r in existing_rows if r["kind"] != "composite_score"]
            if cap >= 0 and n >= cap:
                if tier == "free" and len(non_composite) == 1 and len(existing_rows) == n:
                    old = non_composite[0]
                    c.execute(
                        "UPDATE tg_alert_rules SET active = 0 WHERE id = ?",
                        (old["id"],),
                    )
                    swap_note = (
                        f"_(swapped your existing `{old['kind']}` rule for composite "
                        f"— free tier allows 1 active alert. Re-add it any time with "
                        f"`/alert {old['kind']}`)_"
                    )
                else:
                    lines = [f"⚠️ *Alert quota full* ({n}/{cap}).", "", "*You currently have:*"]
                    for r in existing_rows:
                        thr = f" @ {int(r['threshold']*100)}%" if r["threshold"] else ""
                        lines.append(f"  `#{r['id']}` · {r['kind']}{thr}")
                    lines.append("")
                    lines.append("*To swap in composite_score:*")
                    if existing_rows:
                        first = existing_rows[0]
                        lines.append(f"  `/alert remove {first['kind']}` _(or any kind above)_")
                        lines.append(f"  then `/alert composite_score`")
                    if tier == "free":
                        lines.append("")
                        lines.append("*Or upgrade:* `/upgrade tg_paid` — *0.2 SOL/mo* — unlimited alerts.")
                    await update.message.reply_text(
                        "\n".join(lines), parse_mode=constants.ParseMode.MARKDOWN,
                    )
                    return
            else:
                swap_note = ""
            c.execute(
                "UPDATE tg_alert_rules SET active = 0 "
                "WHERE telegram_id = ? AND kind = 'composite_score' AND active = 1",
                (tg_id,),
            )
            now_ts = int(time.time())
            c.execute(
                "INSERT INTO tg_alert_rules (telegram_id, kind, threshold, params, active, created_at, activated_at) "
                "VALUES (?, 'composite_score', 0, ?, 1, ?, ?)",
                (tg_id, json.dumps({"min_tier": min_tier} if min_tier else {}), now_ts, now_ts),
            )
        tier_desc = {
            None:    "all tiers — ⚡ACT (high conviction) · 📊WATCH · 🛰SCOUT (recall)",
            "ACT":   "⚡ACT only — highest conviction",
            "WATCH": "⚡ACT + 📊WATCH",
            "SCOUT": "all tiers (ACT+WATCH+SCOUT)",
        }[min_tier]
        body = (
            f"✓ subscribed to *composite_score* — {tier_desc}\n\n"
            f"Composite-receipts: smart-money × momentum × freshness, gated by "
            f"the model's confidence gradient. Each fire shows tier, grad_prob, "
            f"and a mint CA + Pump/Axiom/Photon/Dex buttons.\n\n"
            f"_Pilot phase — performance is forward-validating in public "
            f"(graduateoracle.fun). No price during the pilot; that's deliberate, "
            f"not an oversight._\n\n"
            f"`/alert remove composite_score` to unsubscribe."
        )
        if swap_note:
            body = swap_note + "\n\n" + body
        await update.message.reply_text(body, parse_mode=constants.ParseMode.MARKDOWN)
        return

    # SIMPLIFICATION 2026-05-04: only grad_prob is a real alert kind. All
    # the legacy kinds (smart_in, runner_5x, dex_paid, etc.) are still
    # included as supporting context INSIDE every grad_prob alert via
    # _collect_signals(m), but they don't trigger separate alerts anymore.
    if kind != "grad_prob":
        await update.message.reply_text(
            f"📌 `{kind}` is no longer a separate alert kind. graduate-oracle "
            f"simplified to *one signal*: `grad_prob ≥ 70%` with clean entry "
            f"(58% forward on-chain at ≥70%, n=2,400+). The info you'd get from `{kind}` is "
            f"now included automatically inside every grad_prob alert.\n\n"
            f"Subscribe: `/alert grad_prob`",
            parse_mode=constants.ParseMode.MARKDOWN,
        )
        return
    if kind in PAID_ALERT_KINDS and tier == "free":
        with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=10)) as c, c:
            c.row_factory = sqlite3.Row
            existing = c.execute(
                "SELECT id, kind FROM tg_alert_rules "
                "WHERE telegram_id = ? AND kind IN ({}) AND active = 1".format(
                    ",".join("?" * len(PAID_ALERT_KINDS))
                ),
                (tg_id, *PAID_ALERT_KINDS),
            ).fetchone()
        if existing:
            await update.message.reply_text(
                f"🔒 You already have a premium-alert sample subscribed "
                f"(`{existing['kind']}`). Free tier is limited to one premium "
                f"alert with a 24-hour cooldown between fires.\n\n"
                f"→ `/cancel {existing['id']}` to swap it for a different kind\n"
                f"→ `/upgrade builder` (0.4 SOL/mo) for unlimited premium alerts + 10 rules",
                parse_mode=constants.ParseMode.MARKDOWN,
            )
            return
        # Allow the subscription. Will be rate-limited to 1 fire/24h in alert_tick.
        # We still mark it `active=1` so the eval loop sees it, then enforce
        # the per-fire cooldown there.
        # Fall through to the normal kind-validation path below.
        pass
    if kind not in FREE_ALERT_KINDS and kind not in PAID_ALERT_KINDS:
        await update.message.reply_text(f"unknown kind: `{kind}`. try /alert with no args.",
                                        parse_mode=constants.ParseMode.MARKDOWN)
        return

    with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=10)) as c, c:
        c.row_factory = sqlite3.Row
        existing_rows = c.execute(
            "SELECT id, kind, threshold FROM tg_alert_rules "
            "WHERE telegram_id = ? AND active = 1 ORDER BY id",
            (tg_id,),
        ).fetchall()
        n = len(existing_rows)
        # If the user already has an active grad_prob rule, this call is a
        # threshold change — we fall through and dedupe-replace below, no
        # quota block. The quota only blocks NEW kinds.
        has_same_kind = any(r["kind"] == kind for r in existing_rows)
        if cap >= 0 and n >= cap and not has_same_kind:
            lines = [f"⚠️ *Alert quota full* ({n}/{cap}).", "", "*You currently have:*"]
            for r in existing_rows:
                thr = f" @ {int(r['threshold']*100)}%" if r["threshold"] else ""
                lines.append(f"  `#{r['id']}` · {r['kind']}{thr}")
            lines.append("")
            lines.append(f"*To add `{kind}`:*")
            first = existing_rows[0]
            lines.append(f"  `/alert remove {first['kind']}` _(or any kind above)_")
            lines.append(f"  then re-run your /alert command")
            if tier == "free":
                lines.append("")
                lines.append("*Or upgrade:* `/upgrade tg_paid` — *0.2 SOL/mo* — unlimited alerts.")
            await update.message.reply_text(
                "\n".join(lines), parse_mode=constants.ParseMode.MARKDOWN,
            )
            return
        # Only grad_prob is allowed (validated above). Threshold is
        # user-tunable but FLOORED at 0.50 (below that is model noise per
        # backtest). Default is 0.70 — the sweet spot.
        if len(ctx.args) > 1:
            try:
                raw = float(ctx.args[1])
                threshold = raw / 100 if raw > 1 else raw
            except Exception:
                await update.message.reply_text(
                    "usage: `/alert grad_prob` (default 70%) or `/alert grad_prob 80`",
                    parse_mode=constants.ParseMode.MARKDOWN,
                )
                return
        else:
            threshold = 0.70
        floor_warning = ""
        if threshold < 0.50:
            floor_warning = (
                f"\n\n⚠ {threshold*100:.0f}% is below the 50% floor (model "
                f"noise zone — 30-50% band actually graduates ~15%). "
                f"Saved at 50%."
            )
            threshold = 0.50
        params = json.dumps({})
        # Same-kind dedup. Wallet alerts can have many active simultaneously
        # (one per tracked wallet address, distinguished by `params.address`).
        # Every other kind is one-per-user — adding a new threshold replaces
        # the old one instead of stacking duplicate alerts on the same mint.
        #
        # `activated_at` is set on every INSERT and must also be updated by
        # any future code path that flips active 0→1 on an existing row.
        # web/main.py's act_slice and audit cutoff queries anchor on
        # MAX(activated_at) over active grad_prob rules — re-activation
        # without updating activated_at silently includes pre-toggle fires.
        if kind != "wallet":
            c.execute(
                "UPDATE tg_alert_rules SET active = 0 "
                "WHERE telegram_id = ? AND kind = ? AND active = 1",
                (tg_id, kind),
            )
        now_ts = int(time.time())
        c.execute("""INSERT INTO tg_alert_rules (telegram_id, kind, threshold, params, active, created_at, activated_at)
                     VALUES (?, ?, ?, ?, 1, ?, ?)""",
                  (tg_id, kind, threshold, params, now_ts, now_ts))
    await update.message.reply_text(
        f"✓ subscribed: *grad_prob ≥ {threshold*100:.0f}%*\n"
        f"_You'll get an ACT alert when a mint scores above your threshold "
        f"with a clean entry, and a WATCH alert when it scores high but the "
        f"entry is too late. ≥70% forward, on-chain resolved: 58% graduation (n=2,400+)._"
        f"{floor_warning}",
        parse_mode=constants.ParseMode.MARKDOWN,
    )


async def cmd_alerts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _upsert_user(update)
    tg_id = update.effective_user.id
    tier, lim = _user_tier(tg_id)
    cap = lim["alerts"]
    if tier == "free" and _tg_free_trial_active():
        cap = -1
    with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=10)) as c, c:
        c.row_factory = sqlite3.Row
        rows = c.execute("""SELECT id, kind, threshold, params FROM tg_alert_rules
                             WHERE telegram_id = ? AND active = 1 ORDER BY id""",
                         (tg_id,)).fetchall()
    cap_str = "unlimited" if cap < 0 else f"{len(rows)}/{cap}"
    if not rows:
        await update.message.reply_text(
            f"📭 *No active alerts* (slots used: {cap_str}).\n\n"
            f"*Try one:*\n"
            f"  `/alert composite_score` — the live product (ACT/WATCH/SCOUT)\n"
            f"  `/alert grad_prob 70`    — graduation-probability track",
            parse_mode=constants.ParseMode.MARKDOWN,
        )
        return
    lines = [f"🔔 *Your active alerts* (slots used: {cap_str})", ""]
    for r in rows:
        thr_pct = int((r['threshold'] or 0) * 100)
        if r['kind'] == 'composite_score':
            # Reflect the actual tier filter (params.min_tier) instead of
            # always showing all three tiers. Otherwise users who ran
            # `/alert composite_score act` see a misleading label.
            min_tier = None
            try:
                import json as _json
                p = _json.loads(r['params']) if r['params'] else {}
                if isinstance(p, dict):
                    min_tier = p.get('min_tier')
            except Exception:
                pass
            tier_label = {
                "ACT":   "ACT only",
                "WATCH": "ACT + WATCH",
                "SCOUT": "ACT + WATCH + SCOUT",
            }.get(min_tier, "ACT/WATCH/SCOUT (all tiers)")
            desc = f"live composite signal · *{tier_label}*"
            thr_part = ""
        elif r['kind'] == 'grad_prob':
            desc = f"graduation probability ≥ *{thr_pct}%*"
            thr_part = ""
        else:
            desc = r['kind']
            thr_part = f" @ {thr_pct}%" if r['threshold'] else ""
        lines.append(f"  `#{r['id']}` — {desc}{thr_part}")
    lines.append("")
    lines.append("*Manage:*")
    for r in rows[:3]:
        lines.append(f"  `/alert remove {r['kind']}`")
    await update.message.reply_text("\n".join(lines), parse_mode=constants.ParseMode.MARKDOWN)


async def cmd_me(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Personal delivered-call receipt. Mirrors the public receipts moat at
    the individual level: of the composite calls WE sent YOU, what did they
    actually do — peak within 24h of the call (from composite_predictions,
    the resolver that works for composite), misses included.
    This is a mirror, not a pitch. Fenced: makes no 'it works' claim; the
    composite signal is in forward-validation (graduateoracle.fun/accuracy)."""
    _upsert_user(update)
    tg_id = update.effective_user.id
    now = int(time.time())
    cutoff_30d = now - 30 * 86400

    def _agg(rows):
        # Outcome comes from composite_predictions (the resolver that
        # actually works for composite fires) via the join below — NOT
        # tg_fires.peak_mult_from_entry, which is the grad_prob-era column
        # and never populates for composite (verified: it returned a false
        # 0% for every user). resolved = outcome_resolved_at is set;
        # unresolved fires are excluded from rates so pending never flatters.
        n = len(rows)
        resolved = [r for r in rows if r["outcome_resolved_at"] is not None]
        m = len(resolved)
        def pk(r): return r["peak_mult_24h"] or 0.0
        hit2 = sum(1 for r in resolved if pk(r) >= 2.0)
        hit5 = sum(1 for r in resolved if pk(r) >= 5.0)
        grad = sum(1 for r in resolved if r["did_graduate"] == 1)
        best = max(resolved, key=pk) if resolved else None
        return n, m, hit2, hit5, grad, best

    with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=10)) as c, c:
        c.row_factory = sqlite3.Row
        all_rows = c.execute(
            "SELECT f.mint AS mint, f.fired_at AS fired_at, "
            "f.features_json AS features_json, cp.peak_mult_24h AS peak_mult_24h, "
            "cp.did_graduate AS did_graduate, cp.outcome_resolved_at AS outcome_resolved_at "
            "FROM tg_fires f "
            "LEFT JOIN composite_predictions cp ON cp.mint = f.mint "
            "WHERE f.telegram_id = ? AND f.kind = 'composite_score'",
            (tg_id,),
        ).fetchall()

    if not all_rows:
        await update.message.reply_text(
            "no composite calls delivered to you yet. subscribe: "
            "`/alert composite_score`", parse_mode=constants.ParseMode.MARKDOWN)
        return

    rows_30 = [r for r in all_rows if (r["fired_at"] or 0) >= cutoff_30d]

    def _pct(a, b): return f"{a/b*100:.0f}%" if b else "—"

    def _block(title, rows):
        n, m, hit2, hit5, grad, best = _agg(rows)
        ls = [f"*{title}*",
              f"  delivered: *{n}*  ·  resolved: *{m}*  ·  cooking: {n - m}"]
        if m:
            ls.append(f"  ≥2× within 24h: *{hit2}/{m}* ({_pct(hit2, m)})  ·  "
                      f"≥5×: *{hit5}* ({_pct(hit5, m)})")
            ls.append(f"  graduated: *{grad}/{m}* ({_pct(grad, m)})")
            # tier split (resolved only)
            by = {}
            for r in rows:
                if r["outcome_resolved_at"] is None:
                    continue
                t = "?"
                try:
                    t = (json.loads(r["features_json"] or "{}").get("tier") or "?")
                except Exception:
                    pass
                d = by.setdefault(t, [0, 0])
                d[0] += 1
                if (r["peak_mult_24h"] or 0) >= 2.0:
                    d[1] += 1
            order = ["ACT", "WATCH", "SCOUT"]
            emo = {"ACT": "⚡", "WATCH": "📊", "SCOUT": "🛰"}
            for t in order:
                if t in by:
                    cnt, h2 = by[t]
                    ls.append(f"   {emo.get(t,'•')} {t}: {cnt} resolved · {_pct(h2,cnt)} ≥2×")
            if best and (best["peak_mult_24h"] or 0) > 1:
                bm = best["mint"]
                ls.append(f"  best: `{bm[:10]}…` *{best['peak_mult_24h']:.1f}×* peak within 24h")
        return "\n".join(ls)

    msg = (
        "📊 *Your delivered-call receipt*\n"
        "_of the composite calls we sent you — peak within 24h of the "
        "call, misses included. A mirror, not a pitch._\n\n"
        + _block("Last 30 days", rows_30) + "\n\n"
        + _block("All-time", all_rows) + "\n\n"
        "_Signal is in public forward-validation — the full, hashed-"
        "before-outcome trail is at graduateoracle.fun/accuracy. This "
        "shows what reached you; it makes no claim the signal is proven._"
    )
    await update.message.reply_text(msg, parse_mode=constants.ParseMode.MARKDOWN)


async def cmd_verdict(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """The pre-launch audit refined the product — the verdict page tells
    the audit story + the real signal we found."""
    _upsert_user(update)
    msg = (
        "🛡 *Pre-launch audit · 2026-06-13*\n\n"
        "Going into launch we audited every claim against the data. The "
        "original Jun 2 verdict reported _\"ACT calls peaked ≥5× within 24h "
        "at 74.7%\"_ — but that metric measured peak from *mint origin*, not "
        "from *the call*. Honest correction shipped.\n\n"
        "*What the signal actually does:*\n"
        "  ⚡ Median runway: *~14s* between our ≥0.70 call and the curve completing\n"
        "  🎯 Calibration:   when we say ≥0.70, ~83% graduate within 24h\n"
        "  🎯 When we say ≥0.90, reality lands close to 99%\n"
        "  📊 950k+ pump.fun mints indexed in the receipts chain\n\n"
        "*Built for fast traders + bots.* The runway is enough for any "
        "decent execution stack to enter on the bonding curve before "
        "graduation completes.\n\n"
        "_Full audit + verification: graduateoracle.fun/verdict_"
    )
    await update.message.reply_text(msg, parse_mode=constants.ParseMode.MARKDOWN)


async def cmd_tiers(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """The composite signal stack — three urgency tiers with measurably
    different runway windows."""
    _upsert_user(update)
    msg = (
        "📊 *The composite signal stack — pick your urgency*\n\n"
        "All three tiers fire the same alert kind. The tier represents "
        "*how soon* the bonding curve completes — the runway you have to "
        "enter before migration.\n\n"
        "⚡ *ACT* — graduating soonest\n"
        "  Median runway: *~4 min* · 29% fire within 60s · for fast TG-sniper "
        "users with one-tap entry.\n\n"
        "📈 *WATCH* — graduating in minutes\n"
        "  Median runway: *~7 min* · comfortable entry window for manual "
        "traders running on a hot wallet.\n\n"
        "🛰 *SCOUT* — most runway\n"
        "  Median runway: *~10 min* · the relaxed tier — phone traders, "
        "slower execution stacks.\n\n"
        "Same model, same calibration, three urgency windows.\n\n"
        "_Subscribe via_ `/alert composite_score` _— gets all three tiers._\n"
        "_Pre-launch audit: graduateoracle.fun/verdict_"
    )
    await update.message.reply_text(msg, parse_mode=constants.ParseMode.MARKDOWN)


async def cmd_sample(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Honest rolling receipts for ACT calls — headline graduation rate
    over 30 days, plus a balanced mix of recent resolved wins, resolved
    misses, and still-cooking calls.

    2026-06-19 rewrite: the previous version showed only the 10 most
    recent ACT calls, which were almost always still 'cooking' (the 24h
    outcome resolver hadn't run yet) — combined with a marketing tagline
    that implied 'sub-second execution catches most before migration',
    users reasonably concluded every ACT graduates. Actual graduation
    rate is ~37%. This rewrite shows that number prominently and gives
    the user real wins AND real misses in the same view."""
    _upsert_user(update)
    now = int(time.time())
    cutoff_30d = now - 30 * 86400

    with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=10)) as c, c:
        c.row_factory = sqlite3.Row

        # Headline: 30d graduation rate over RESOLVED ACT calls. This is the
        # one number users react to — and the one we were burying before.
        agg = c.execute("""
            SELECT COUNT(*) AS n,
                   SUM(CASE WHEN did_graduate=1 THEN 1 ELSE 0 END) AS n_grad
              FROM composite_predictions
             WHERE tg_tier='ACT' AND tier_logic_version='v2'
               AND outcome_resolved_at IS NOT NULL
               AND predicted_at > ?
        """, (cutoff_30d,)).fetchone()
        n_resolved = int(agg["n"] or 0)
        n_grad     = int(agg["n_grad"] or 0)
        grad_rate  = (n_grad / n_resolved * 100) if n_resolved else None

        n_cooking = c.execute("""
            SELECT COUNT(*) FROM composite_predictions
             WHERE tg_tier='ACT' AND tier_logic_version='v2'
               AND outcome_resolved_at IS NULL
               AND predicted_at > ?
        """, (cutoff_30d,)).fetchone()[0]

        # 5 most recent RESOLVED ACT calls (mix of grad + no_grad — the
        # honest sample). LEFT JOIN to get the actual graduated_at runway.
        resolved_rows = c.execute("""
            SELECT cp.mint, cp.predicted_at, cp.did_graduate,
                   cp.outcome_resolved_at, o.graduated_at
              FROM composite_predictions cp
         LEFT JOIN post_grad_outcomes o ON o.mint = cp.mint
             WHERE cp.tg_tier = 'ACT' AND cp.tier_logic_version = 'v2'
               AND cp.outcome_resolved_at IS NOT NULL
             ORDER BY cp.predicted_at DESC LIMIT 5
        """).fetchall()

        # 5 most recent still-cooking — surfaced separately so users see
        # that resolution is still pending, not silently treated as wins.
        cooking_rows = c.execute("""
            SELECT mint, predicted_at FROM composite_predictions
             WHERE tg_tier = 'ACT' AND tier_logic_version = 'v2'
               AND outcome_resolved_at IS NULL
             ORDER BY predicted_at DESC LIMIT 5
        """).fetchall()

    if not n_resolved and not cooking_rows:
        await update.message.reply_text("no recent ACT calls.")
        return

    lines = ["⚡ *ACT calls — honest 30-day receipts*\n"]
    if grad_rate is not None:
        lines.append(
            f"📊 *Graduation rate:* {grad_rate:.1f}% "
            f"({n_grad} of {n_resolved} resolved)"
        )
    else:
        lines.append("📊 *Graduation rate:* _no resolved samples yet_")
    lines.append(f"⏳ *Still resolving:* {n_cooking} calls awaiting 24h outcome")
    lines.append("")

    if resolved_rows:
        lines.append("*Recent 5 resolved* (wins AND misses):")
        for r in resolved_rows:
            if r["did_graduate"] == 1 and r["graduated_at"]:
                runway = r["graduated_at"] - r["predicted_at"]
                if runway < 60: runway_str = f"{runway}s"
                elif runway < 3600: runway_str = f"{runway // 60}m"
                else: runway_str = f"{runway // 3600}h"
                out = f"🚀 *grad in {runway_str}*"
            elif r["did_graduate"] == 1:
                out = "🚀 *grad*"
            else:
                out = "❌ _did not graduate_"
            lines.append(f"  `{r['mint'][:10]}…` — {out}")
        lines.append("")

    if cooking_rows:
        lines.append("*Recent 5 still cooking* (outcome pending):")
        for r in cooking_rows:
            age_h = (now - r["predicted_at"]) / 3600
            lines.append(f"  `{r['mint'][:10]}…` — ⏳ _{age_h:.1f}h old_")
        lines.append("")

    lines.append(
        "_Honest receipts — every ACT call is hash-committed before its "
        "outcome resolves. Full forward-validation: graduateoracle.fun/accuracy_"
    )
    await update.message.reply_text("\n".join(lines), parse_mode=constants.ParseMode.MARKDOWN)


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _upsert_user(update)
    if not ctx.args:
        await update.message.reply_text("usage: `/cancel <alert id>`",
                                        parse_mode=constants.ParseMode.MARKDOWN)
        return
    try:
        rid = int(ctx.args[0].lstrip("#"))
    except Exception:
        await update.message.reply_text("invalid id")
        return
    with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=10)) as c, c:
        c.execute("UPDATE tg_alert_rules SET active = 0 WHERE id = ? AND telegram_id = ?",
                  (rid, update.effective_user.id))
    await update.message.reply_text(f"alert #{rid} canceled.")


async def cmd_upgrade(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _upsert_user(update)
    # Purchasing kill-switch (2026-05-17). When closed, every /upgrade path
    # short-circuits to the honest "opening soon" — no payment intent, no
    # orphan key, no misleading pricing explainer. Single source of truth:
    # sol_pay.PURCHASING_OPEN.
    if not getattr(sol_pay, "PURCHASING_OPEN", False):
        await update.message.reply_text(
            sol_pay.opening_soon_msg(), parse_mode=constants.ParseMode.MARKDOWN,
        )
        return
    # /upgrade [tg_paid|builder|pro] [monthly|yearly]   default = tg_paid monthly
    # Aliases: `paid` and `tg` map to tg_paid (the cheap TG-only tier).
    tier = "tg_paid"
    plan = "monthly"
    for a in (ctx.args or []):
        al = a.lower()
        if al in ("tg_paid", "tg", "paid"):
            tier = "tg_paid"
        elif al in ("builder", "pro"):
            tier = al
        elif al in ("monthly", "yearly", "year", "month"):
            plan = "yearly" if "year" in al else "monthly"

    if not ctx.args:
        # No args — show the full free-vs-paid explainer + how to commit.
        await update.message.reply_text(
            plans_explainer_text() + "\n\n_Add `yearly` for 17% off, e.g._ `/upgrade pro yearly`",
            parse_mode=constants.ParseMode.MARKDOWN,
        )
        return

    try:
        intent = sol_pay.create_intent(tier=tier, plan=plan,
                                        telegram_id=update.effective_user.id)
    except ValueError as e:
        await update.message.reply_text(f"error: {e}")
        return

    # Build the Solana Pay URI — this is the standard supported by Phantom,
    # Solflare, Backpack, etc. when scanned as a QR code.
    pay_uri = intent["deeplink_solana_pay"]

    # Generate QR code as PNG bytes
    import io, qrcode
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=2)
    qr.add_data(pay_uri)
    qr.make(fit=True)
    img = qr.make_image(fill_color="white", back_color="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    caption = (
        f"*GRADUATE {tier.title()} · {plan}*\n\n"
        f"📲 *Scan this with Phantom* (Phantom → top-right QR icon)\n\n"
        f"Or send *{intent['amount_sol']} SOL* manually to:\n"
        f"`{intent['treasury_wallet']}`\n\n"
        f"with memo: `{intent['memo']}`\n\n"
        f"⏱ intent expires in {intent['ttl_minutes']} min · tier activates ~30s after confirmation"
    )
    await update.message.reply_photo(photo=buf, caption=caption,
                                     parse_mode=constants.ParseMode.MARKDOWN)



# ── alert evaluator ───────────────────────────────────────────────────────

def _read_active_rules() -> list[dict]:
    with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=10)) as c, c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute(
            "SELECT * FROM tg_alert_rules WHERE active = 1"
        ).fetchall()]


def _user_chat_id(telegram_id: int) -> Optional[int]:
    with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=10)) as c, c:
        c.row_factory = sqlite3.Row
        r = c.execute("SELECT chat_id FROM tg_users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        return r["chat_id"] if r else None


def _user_api_key_id(telegram_id: int) -> Optional[int]:
    """Latest active api_key id for this TG user, if any. Used to fan webhook
    deliveries to a Pro user's registered URLs alongside the TG message."""
    with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=10)) as c, c:
        c.row_factory = sqlite3.Row
        r = c.execute(
            "SELECT id FROM api_keys WHERE telegram_id = ? AND revoked = 0 "
            "ORDER BY id DESC LIMIT 1",
            (telegram_id,),
        ).fetchone()
        return r["id"] if r else None


# Track which (rule, mint) pairs we've already fired on so we don't spam.
_fired = set()  # (rule_id, mint, bucket_minute)

# Free-tier sample throttle: tracks how many ACT fires were SKIPPED for
# each free user since midnight UTC. Used in the alert message to show
# the user how many they missed (FOMO upgrade prompt). Resets daily via
# _reset_free_skipped_if_new_day() called at the top of each drain tick.
_free_skipped: dict[int, int] = {}
_free_skipped_day_utc: str = ""


def _reset_free_skipped_if_new_day():
    global _free_skipped, _free_skipped_day_utc
    import datetime
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    if today != _free_skipped_day_utc:
        _free_skipped = {}
        _free_skipped_day_utc = today


# ─── alert formatting · two-tier model ──────────────────────────────────
# Triggers (when to fire) are user-configured per kind: /alert grad_prob 30,
# /alert smart_in 3, etc. Format (what the alert looks like) follows the
# user's plan tier:
#   • Free  → minimal one-liner — just trigger reason + mint
#   • Paid  → rich card — token name, symbol, every active signal stacked,
#              grad odds, key stats, buttons
# Anyone with a non-free api_key tier (builder, pro, comp grants) gets the
# rich format. The bot doesn't care which paid tier; that's an API/billing
# distinction, not an alert-format one.

# Universal suppression — never fires, regardless of trigger or tier.
# IMPORTANT: we used to suppress on manufactured_pump too, but our own
# forward-validation data shows flagged mints actually rug LESS than
# unflagged ones (lift 0.61 — the flag is anti-correlated with rugs).
# Suppressing on it was blocking the best alerts. We now surface the
# flag as a signal in the rich format ("⚠ early concentration pattern")
# but never use it as a gate. Only `bundle ≥30% supply held` remains as
# a hard suppress — that's a real active-dump risk (bundlers still
# loaded), distinct from a launch-time concentration pattern.
def _alert_is_suppressed(m: dict) -> Optional[str]:
    bun = m.get("bundle") or {}
    if bun.get("detected") and (bun.get("pct") or 0) >= 30:
        return f"active bundle holding {bun.get('pct'):.0f}%"
    # Multi-flag rug heuristic — sniper-coordination patterns the bundle≥30%
    # rule alone misses. Validated against HENRIETTA (2026-05-02): bundle was
    # 27% (under threshold) but holder_top10=32% + zero smart money + fresh
    # wallet first-3 → severity=high. Each flag is named so an audit trail
    # exists when a user asks "why didn't I get this alert?"
    rh = m.get("rug_heuristic") or {}
    if rh.get("severity") == "high":
        flags = ", ".join(rh.get("triggered_flags") or [])
        return f"rug_heuristic high · flags: {flags}"
    # Post-peak / pump-and-dump suppression. If max_mult / current_mult ≥ 2,
    # the mint already pumped to ≥2× current price and retraced ≥half. The
    # predicted upside is the upside that already happened — alerting now
    # puts the user into the dump, not the pump. Validated against
    # xf7SfsRq... (2026-05-03): peaked at 4.45× then dropped to 0.92× by
    # age 57s; alert fired with model saying "5× from now is 19%" because
    # neighbors with similar features pumped, but this one was already DOA.
    cur_mult = m.get("current_mult") or 0
    max_mult = m.get("max_mult") or 0
    if cur_mult > 0 and max_mult / cur_mult >= 2.0:
        return f"post-peak retrace · peaked at {max_mult:.2f}×, now {cur_mult:.2f}×"
    # Relisted-old-mint suppression. Observer's age_s is "first time we
    # saw this mint in our stream" — if the mint went dormant then woke
    # up (or observer restarted), age_s says brand new but the mint is
    # actually old. Validated against 2hU1qtA4gq... (2026-05-03): observer
    # age_s=32 but mint was 21 days old; we'd already predicted on it.
    # The predictions table is the truth — if we scored this mint > 1h
    # ago, it's not new to us regardless of what the observer says.
    mint = m.get("mint")
    if mint:
        try:
            import contextlib, sqlite3
            with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=2)) as c, c:
                row = c.execute(
                    "SELECT MIN(predicted_at) FROM predictions WHERE mint = ?",
                    (mint,),
                ).fetchone()
                if row and row[0] and (int(time.time()) - int(row[0])) >= 3600:
                    return f"relisted old mint · first scored {(int(time.time()) - int(row[0]))//3600}h ago"
        except Exception:
            pass
    return None


def _escape_md(s: str) -> str:
    """Escape Telegram MarkdownV1 metacharacters in user-supplied strings.
    The MarkdownV1 parser chokes when it sees an unmatched `*` or `_` in
    a token name like "Don*t Tread On Me" or "ETH_USD" — the entire
    message fails to send with "can't find end of the entity". We learned
    this 2026-05-03 from silent alert drops.

    V1 only treats *, _, [, ], `, and \\ specially. Everything else is
    safe. We replace each with the same char prefixed by backslash."""
    if not s:
        return ""
    out = []
    for ch in str(s):
        if ch in "*_[]`\\":
            out.append("\\")
        out.append(ch)
    return "".join(out)


async def _maybe_auto_trade(application, tg_id: int, snap: dict, mint: str,
                            *, queued_at: int = 0):
    # Day 4.53 reverted 2026-06-23: had an age guard here (refuse alerts
    # > 60s old) — killed valid late-but-good catches. Holding the
    # backlog/staleness problem for a later, smarter fix. For now:
    # auto-trade fires on every alert that passes the user's own gates.
    _ = queued_at  # accepted but unused; preserves signature compat
    """If the user has auto-trade enabled AND this alert's tier meets
    their threshold AND they're under their max-concurrent open cap,
    fire a buy via orchestrator.

    All existing safety guards apply automatically because we route
    through orchestrator.buy():
      • TOS acceptance check
      • Rate limiter (3s spacing + 10/min burst)
      • Balance floor (refuses if wallet too low)
      • max_trade_sol cap
      • Slippage / tip from user settings

    Buy receipt is sent labeled with the 🤖 AUTO-BUY prefix so the user
    sees clearly that this wasn't a manual tap."""
    user_id = str(tg_id)
    try:
        import trader_positions
        cfg = trader_positions.get_user_settings(user_id)
    except Exception as e:
        print(f"[auto_trade] get_user_settings failed: {e}", flush=True)
        return

    if not cfg.get("auto_trade_enabled"):
        return

    # Inactivity gate — if the user hasn't interacted with the bot in
    # N hours, refuse to auto-trade. Defends against "set and forget +
    # walk away forever" wallet drain. Reset by any /trader interaction
    # (the bot's _upsert_user updates tg_users.last_seen_at on every
    # message + callback the user fires). Set max_inactive_hours=0 to
    # disable this gate entirely.
    max_inactive_h = int(cfg.get("auto_trade_max_inactive_hours") or 0)
    if max_inactive_h > 0:
        try:
            with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=5)) as c:
                c.row_factory = sqlite3.Row
                row = c.execute(
                    "SELECT last_seen_at FROM tg_users WHERE telegram_id = ?",
                    (tg_id,),
                ).fetchone()
            last_seen = int(row["last_seen_at"]) if row and row["last_seen_at"] else 0
        except Exception:
            last_seen = 0
        if last_seen > 0:
            idle_s = int(time.time()) - last_seen
            if idle_s > max_inactive_h * 3600:
                await application.bot.send_message(
                    tg_id,
                    f"🤖 Auto-trade SKIPPED on `{mint[:6]}…{mint[-4:]}` "
                    f"— you've been inactive for {idle_s//3600}h "
                    f"({max_inactive_h}h cap).\n"
                    f"Send `/trader` to resume auto-buys.",
                    parse_mode=constants.ParseMode.MARKDOWN,
                    disable_web_page_preview=True,
                )
                return

    # Tier gate. ACT > WATCH > SCOUT.
    alert_tier = (snap or {}).get("tier") or ""
    min_tier   = cfg.get("auto_trade_min_tier") or "ACT"
    rank = {"SCOUT": 1, "WATCH": 2, "ACT": 3}
    if rank.get(alert_tier, 0) < rank.get(min_tier, 3):
        return

    # Max concurrent cap
    try:
        n_open = trader_positions.count_open_positions(user_id)
    except Exception as e:
        print(f"[auto_trade] count_open_positions failed: {e}", flush=True)
        return
    cap = int(cfg.get("auto_trade_max_concurrent") or 3)
    if n_open >= cap:
        await application.bot.send_message(
            tg_id,
            f"🤖 Auto-trade SKIPPED on `{mint[:6]}…{mint[-4:]}` "
            f"— you have {n_open} open positions (cap = {cap}).\n"
            f"Increase cap in /trader → Settings → Auto-Trade, or close "
            f"some positions first.",
            parse_mode=constants.ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
        return

    size_lamports = int(cfg.get("auto_trade_size_lamports") or 5_000_000)
    sol_amount = size_lamports / 1e9

    # Run the buy in a try; catch all OrchestratorError shapes so a
    # buy failure becomes a notification instead of a silent miss.
    try:
        import trader_orchestrator
        result = trader_orchestrator.buy(
            user_id=user_id,
            mint=mint,
            sol=sol_amount,
            live=True,
        )
    except Exception as e:
        # User-facing message extraction. OrchestratorError has a clean
        # .user_facing_msg attribute; fall back to repr for unexpected.
        msg = getattr(e, "user_facing_msg", None) or str(e)[:200]
        await application.bot.send_message(
            tg_id,
            f"🤖 Auto-trade FAILED on `{mint[:6]}…{mint[-4:]}`: {msg}",
            parse_mode=constants.ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
        return

    # Success — render the receipt via the trader_commands formatter so
    # we match the manual-buy UX, just with an AUTO-BUY prefix.
    try:
        import trader_commands
        body = trader_commands._format_buy_receipt(result)
        text = f"🤖 *AUTO-BUY*\n\n{body}"
        kb = trader_commands._kb_position_actions(result.get("position_id"))
        await application.bot.send_message(
            tg_id, text,
            parse_mode=constants.ParseMode.MARKDOWN,
            reply_markup=kb,
            disable_web_page_preview=True,
        )
    except Exception as e:
        print(f"[auto_trade] receipt render failed: {e}", flush=True)
        # Still tell the user it succeeded
        await application.bot.send_message(
            tg_id,
            f"🤖 *AUTO-BUY* on `{mint[:6]}…` succeeded. "
            f"See /trader → Portfolio.",
            parse_mode=constants.ParseMode.MARKDOWN,
        )


async def _send_alert(application, chat_id: int, text: str, **kw):
    """Send an alert with markdown, falling back to plain text on parse
    failure. Belt-and-suspenders against the next time a token name has
    a metachar we forgot to escape — better to send a slightly uglier
    plain-text alert than silently drop it."""
    try:
        await application.bot.send_message(
            chat_id=chat_id, text=text,
            parse_mode=constants.ParseMode.MARKDOWN,
            disable_web_page_preview=True, **kw,
        )
    except Exception as e:
        if "can't find end of the entity" in str(e) or "Can't parse entities" in str(e):
            # Strip markdown chars and resend as plain text. The user still
            # gets the alert; only formatting is lost.
            plain = (text.replace("*", "").replace("_", "")
                          .replace("`", "").replace("```", ""))
            try:
                await application.bot.send_message(
                    chat_id=chat_id, text=plain,
                    disable_web_page_preview=True,
                    **{k: v for k, v in kw.items() if k != "parse_mode"},
                )
                print(f"[bot] markdown parse failed, sent plain-text fallback for chat {chat_id}", flush=True)
            except Exception as e2:
                print(f"[bot] plain-text fallback ALSO failed for chat {chat_id}: {e2}", flush=True)
        else:
            raise


def _collect_signals(m: dict) -> list[str]:
    """All active signals on a mint, formatted as category-prefixed bullets.
    Used to populate the rich (paid) alert format. Each line tags WHY the
    signal matters so a trader can scan in <2s without reading every word.
    Order: smart-money / creator first (highest-info), then momentum,
    quality/alignment, then outlook + risk warnings."""
    bullets: list[str] = []
    smart = m.get("smart_money_in") or 0
    if smart >= 1:
        bullets.append(f"▲ smart-money — *{smart}* wallet(s) in top buyers")

    cl = m.get("cluster") or {}
    if (cl.get("n_clustered_pairs") or 0) >= 1:
        bullets.append(
            f"◇ cluster — *{cl.get('n_clustered_pairs')}* coordinated pair(s) "
            f"piling in"
        )

    ch = m.get("creator_history") or {}
    if ch.get("runner_creator"):
        bullets.append(
            f"▲ creator — runner dev · *{ch.get('n_launches', 0)}* launches · "
            f"*{(ch.get('rate_5x') or 0)*100:.0f}%* 5× rate"
        )
    elif ch.get("good_creator"):
        bullets.append(
            f"✓ creator — verified · *{ch.get('n_launches', 0)}* launches · "
            f"*{(ch.get('grad_rate') or 0)*100:.0f}%* grad rate"
        )

    accel = m.get("vsol_acceleration") or 0
    if accel >= 5:
        bullets.append(f"⚡ momentum — vSOL accelerating *+{accel:.1f}* SOL/30s")

    wb = m.get("wallet_balance") or {}
    if (wb.get("n_whale_wallets") or 0) >= 3:
        bullets.append(
            f"$ whales — *{wb.get('n_whale_wallets')}* in top buyers · "
            f"avg *{wb.get('avg_buyer_sol', 0):.1f}* SOL"
        )

    fd = m.get("fee_delegation") or {}
    if (fd.get("total_bps") or 0) > 0:
        pct = (fd.get("total_bps") or 0) / 100
        bullets.append(f"🤝 alignment — fees *{pct:.0f}%* delegated")

    dx = m.get("dex_paid") or {}
    if dx.get("is_paid"):
        bullets.append("🟢 quality — DexScreener paid info enabled")

    # Post-grad sustains is now surfaced in the headline pairing of
    # _format_alert_rich (right under entry_line), not as a context bullet,
    # so users see "graduates: X% / sustains: Y%" together. Keeping it here
    # too would render the same number twice. See project_watch_grad_vs_runner.md.

    # Early-grad signal — most valuable when it DIVERGES upward from the
    # curve-shape grad_prob (i.e. early signals predict graduation before
    # the curve has moved). Surface only when it's high AND divergent.
    egp = m.get("early_grad_prob") or {}
    main_grad = m.get("grad_prob") or 0
    if egp.get("status") == "live" and (egp.get("prob") or 0) >= 0.40:
        egp_pct = egp["prob"] * 100
        diff_pp = (egp["prob"] - main_grad) * 100
        if diff_pp >= 15:
            bullets.append(
                f"🚀 alpha — early-grad signal *{egp_pct:.0f}%* "
                f"(+{diff_pp:.0f}pp vs curve · this is the edge)"
            )
        else:
            bullets.append(
                f"🎯 early signal — at-launch features predict *{egp_pct:.0f}%* graduation"
            )

    bun = m.get("bundle") or {}
    if bun.get("detected") and (bun.get("pct") or 0) > 0:
        bullets.append(
            f"⚠ risk — bundlers hold *{bun.get('pct'):.0f}%* of supply"
        )

    # 🔥 hot launch — fast concentrated early entry. Forward-validation
    # shows these rug LESS than baseline (49.6% vs 80.8%, lift 0.61), so
    # we surface this as a positive signal, not a warning.
    if m.get("manufactured_pump"):
        bullets.append(
            "🔥 hot launch — heavy concentrated early entry "
            "(historically rugs less than baseline)"
        )

    return bullets


def _format_composite_alert(snap: dict, msg_extra: str) -> str:
    """Composite-receipts cross alert, two-tier ACT / WATCH.

    Tier dispatch:
      ACT   → high-conviction, 75% historical grad rate (gp_60 ≥ 0.25)
      WATCH → lower-conviction, 28% historical grad rate (gp_60 in [0.10, 0.25))

    The tier is decided upstream in composite_predictions.evaluate_tg_pushes
    based on grad_prob_60. Renderer differentiates header + supporting copy
    so the reader instantly knows the conviction level."""
    meta = snap.get("metadata") or {}
    mint = snap.get("mint") or ""
    name = meta.get("name") or (mint[:6] + "…" + mint[-4:] if mint else "?")
    sym = meta.get("symbol")
    title = f"*{name}*" + (f"  ·  `${sym}`" if sym else "")
    tier = (snap.get("tier") or "ACT").upper()
    score = snap.get("composite_score") or 0
    thr = snap.get("threshold_at_cross") or 0
    ratio = snap.get("score_ratio") or (score / thr if thr else 0)
    smart = snap.get("smart_money_in") or 0
    mm = snap.get("max_mult_at_cross") or 0
    age_s = snap.get("age_s_at_cross") or 0
    mc = snap.get("mc_at_cross_usd") or 0
    gp_60 = snap.get("grad_prob_60")
    gp_str = f"{gp_60*100:.1f}%" if gp_60 is not None else "—"

    # Tier semantics retuned 2026-05-15 (3-tier, peak≥5× = primary metric).
    # Rates are back-test projections under forward-validation — labelled
    # honestly as such, not stated as proven live numbers (n=7 retraction
    # discipline). SCOUT is the recall tier: surfaced so it's not missed,
    # user's eye is the precision filter.
    if tier == "ACT":
        header = f"⚡ *ACT* — {title}"
        conviction_line = f"*{gp_str}* chance to graduate  ·  _~71% of these hit 5×_"
    elif tier == "SCOUT":
        header = f"🛰 *SCOUT* — {title}"
        conviction_line = f"*{gp_str}* chance to graduate  ·  _weaker signal — judge it yourself_"
    else:  # WATCH
        header = f"📊 *WATCH* — {title}"
        conviction_line = f"*{gp_str}* chance to graduate  ·  _~56% of these hit 5×_"

    lines = [
        header,
        conviction_line,
        # Dropped "X× launch" — backward-looking, inflates the receipt for
        # late entries. Every number on the alert must be honest FROM the
        # signal moment forward (we sell measurement, not window-dressing).
        f"smart-money *{smart}* in  ·  age *{age_s}s*  ·  *${mc:,.0f}* MC",
        f"composite *{score:.1f}* ({ratio:.2f}× threshold)",
        f"`{mint}`",
    ]
    return "\n".join(lines)


def _format_alert_basic(m: dict, msg_extra: str) -> str:
    """Free-tier format. Same headline, same value — minimal context.
    Three lines of signal + the mint. Encourages the upgrade by SHOWING
    the product works without giving the full picture (smart money,
    whales, creator, signals). The free user gets the call AND the
    confidence; they pay for the full reasoning."""
    meta = m.get("metadata") or {}
    name = meta.get("name") or m["mint"][:6] + "…" + m["mint"][-4:]
    sym = meta.get("symbol")
    title = f"*{name}*" + (f"  ·  `${sym}`" if sym else "")

    grad = m.get("grad_prob") or 0
    g_cal = m.get("grad_prob_calibration") or {}
    cm = m.get("current_mult") or 0
    age_s = m.get("age_s") or 0

    is_watch = msg_extra.startswith("📊 WATCH")
    flavor_label = "📊 *WATCH*" if is_watch else "🎯 *ACT*"

    if g_cal.get("historical_n") and g_cal["historical_n"] >= 10:
        hist_rate = (g_cal.get("historical_actual_rate") or 0) * 100
        hist_n = g_cal["historical_n"]
        backtest_str = f"_{hist_rate:.0f}% historical (n={hist_n:,})_"
    else:
        backtest_str = "_see /accuracy for live hit rate_"

    if is_watch:
        entry_str = f"_catching late — {cm:.2f}× launch_"
    else:
        entry_str = f"_entry {cm:.2f}× · age {age_s:.0f}s_"

    # Sustains rendering: live → render co-billed; warming-anything → render
    # warming line; sunset/recalibration/validation_rerun → drop sustains
    # half entirely (Path E + Finding 7e auto-lift gate, 2026-05-07).
    pgs = m.get("post_grad_survival_prob") or {}
    pgs_status = pgs.get("status")
    if pgs_status == "live" and pgs.get("prob") is not None:
        sustains_str = f"*{pgs['prob']*100:.0f}%* sustain"
    elif pgs_status in ("sunset_pending_architecture_review",
                        "sunset_pending_validation_rerun",
                        "metric_recalibration_in_progress",
                        "sunset_lane_60s_structural_limit"):
        sustains_str = None
    else:
        # warming, warming_clean_corpus_accumulating, warming_too_few_matches
        n_so_far = pgs.get("n_total_resolved") or 0
        sustains_str = f"sustain _warming ({n_so_far}/30)_"

    # Curve position — observed mechanical state, distinct from grad_prob
    # the model output. Lets free-tier alerts carry one piece of context
    # without expanding the format. See _graduation_progress in web/main.py.
    gpp = m.get("graduation_progress_pct") or {}
    if gpp.get("status") == "graduated":
        curve_str = "_curve: 100% — graduated_"
    elif gpp.get("pct") is not None:
        curve_str = f"_curve: {gpp['pct']:.0f}% to bond_"
    else:
        curve_str = ""

    # Same disclosure as rich format — flag bundled+manufactured launches
    # so users know the calibration population is narrow. See _format_alert_rich.
    bun = m.get("bundle") or {}
    pop_str = ""
    if bun.get("detected") and m.get("manufactured_pump"):
        pop_str = "_population: bundled launch_"

    headline = (f"*{grad*100:.0f}%* graduate · {sustains_str} post-bond"
                if sustains_str else f"*{grad*100:.0f}%* graduate")
    lines = [
        f"{flavor_label} — {title}",
        headline,
        f"{backtest_str}",
        f"{entry_str}",
    ]
    if curve_str: lines.append(curve_str)
    if pop_str:   lines.append(pop_str)
    lines.append(f"`{m['mint']}`")
    lines.append("")
    lines.append("_NFA · DYOR_")
    return "\n".join(lines)


def _format_alert_rich(m: dict, msg_extra: str) -> str:
    """Paid-tier alert format. ONE signal at the top (grad_prob + backtest
    hit rate context) — that's THE product. Everything else is supporting
    context: entry quality, market, side signals, rug check. The reader
    sees the headline value in the first 2 lines, the rest is decision-
    making context."""
    meta = m.get("metadata") or {}
    name = meta.get("name") or m["mint"][:6] + "…" + m["mint"][-4:]
    sym = meta.get("symbol")
    title = f"*{name}*" + (f"  ·  `${sym}`" if sym else "")

    grad = m.get("grad_prob") or 0
    g_cal = m.get("grad_prob_calibration") or {}
    rp = m.get("rug_prob") or {}

    # ── HEADER: flavor + name + headline value ─────────────────────────
    is_watch = msg_extra.startswith("📊 WATCH")
    flavor_label = "📊 *WATCH*" if is_watch else "🎯 *ACT*"
    header = f"{flavor_label} — {title}"

    # ── CO-BILLED PREDICTION LINE: graduation + sustains as equal numbers ─
    # Graduation alone isn't tradeable — many bonded mints dump on PumpSwap.
    # Sustains and graduation are CO-EQUAL predictions, surfaced in the same
    # line so neither dominates the visual hierarchy. See project_watch_grad
    # _vs_runner.md for the data driving this framing.
    # Sustains co-bill: live → render; sunset/recalibration/validation_rerun
    # → omit the sustains half so we don't render "warming" against a feature
    # that's disabled. The graduate half stands alone in those states.
    pgs = m.get("post_grad_survival_prob") or {}
    pgs_status = pgs.get("status")
    pgs_prob = pgs.get("prob")
    if pgs_status == "live" and pgs_prob is not None:
        co_billed_line = f"*{grad*100:.0f}%* graduate  ·  *{pgs_prob*100:.0f}%* sustain post-bond"
    elif pgs_status in ("sunset_pending_architecture_review",
                        "sunset_pending_validation_rerun",
                        "metric_recalibration_in_progress",
                        "sunset_lane_60s_structural_limit"):
        co_billed_line = f"*{grad*100:.0f}%* graduate"
    else:
        n_so_far = pgs.get("n_total_resolved") or 0
        co_billed_line = f"*{grad*100:.0f}%* graduate  ·  sustain post-bond _warming ({n_so_far}/30)_"

    # Subordinate backtest reference — italic, single line, NOT the headline.
    if g_cal.get("historical_n") and g_cal["historical_n"] >= 10:
        hist_rate = (g_cal.get("historical_actual_rate") or 0) * 100
        hist_n = g_cal["historical_n"]
        backtest_line = (
            f"_historical graduate rate at this band: {hist_rate:.0f}% (n={hist_n:,}) · "
            f"post-bond outcome data on /accuracy_"
        )
    else:
        backtest_line = (
            f"_see graduate-oracle.fly.dev/accuracy for backtest + post-bond outcomes_"
        )

    # ── ENTRY: the trader-relevant context (clean vs late). Neutral framing
    # — no "model still bullish on graduation" wording; graduation alone is
    # not a profit thesis given current post-bond data. Just facts.
    cm = m.get("current_mult") or 0
    age_s = m.get("age_s") or 0
    vsol_growth = m.get("vsol_growth_sol") or 0
    if is_watch:
        entry_line = (
            f"_Catching late — *{cm:.2f}×* launch · age {age_s:.0f}s_"
        )
    else:
        entry_line = (
            f"_Entry *{cm:.2f}×* launch · vsol +{vsol_growth:.1f} · "
            f"age {age_s:.0f}s_"
        )

    # ── MARKET (compact monospace) ──────────────────────────────────────
    # MC line annotates with current SOL/USD price so the USD figure can't
    # be read as fixed — SOL price floats, MC USD floats with it. The
    # primary trader-relevant metric on this block is now `Curve` — how
    # close to graduation the bonding curve is (observed mechanical state,
    # distinct from grad_prob the model output). See _graduation_progress
    # in web/main.py for the formula.
    mc = m.get("market_cap") or {}
    sol_usd = mc.get("sol_usd")
    if mc.get("usd"):
        u = mc["usd"]
        usd_str = f"${u/1000:.1f}k" if u >= 1000 else f"${u:,.0f}"
        mc_str = (f"{usd_str} (SOL ${sol_usd:.0f})" if sol_usd
                  else usd_str)
    elif mc.get("sol"):
        mc_str = f"{mc['sol']:.0f} SOL"
    else:
        mc_str = "—"

    gpp = m.get("graduation_progress_pct") or {}
    if gpp.get("status") == "graduated":
        curve_str = "100% — graduated"
    elif gpp.get("pct") is not None:
        curve_str = f"{gpp['pct']:.0f}% to bond"
    else:
        curve_str = "—"

    market_block = (
        "```\n"
        f"Curve   {curve_str}\n"
        f"MC      {mc_str}\n"
        f"vSOL    {m.get('current_vsol_sol', 0):.1f}\n"
        f"buyers  {m.get('unique_buyers', 0)}\n"
        "```"
    )

    # ── SIGNALS (supporting context — smart money, whales, creator, etc) ─
    signals = _collect_signals(m)
    sig_block = ""
    if signals:
        sig_block = "*Context*\n" + "\n".join(signals) + "\n\n"

    # ── RUG safety check (quiet, at the bottom — not a headliner) ──────
    rug_line = ""
    if rp.get("prob") is not None:
        rl = rp.get("lift_x") or 0
        rug_emoji = "🔴" if rl >= 3 else ("🟡" if rl >= 1.5 else "🟢")
        rug_line = f"{rug_emoji} _Rug check: {rp['prob']*100:.0f}% (model)_\n\n"
    elif rp.get("status") == "warming":
        rug_line = "🟡 _Rug check: warming (corpus building)_\n\n"

    # Disclosure: when a fire is BOTH manufactured_pump AND bundle_detected,
    # be explicit that the population this prediction was calibrated on is
    # exclusively bundled launches. Source: gate_validation 2026-05-04 found
    # 7/7 in-lane fires under rule 8 were bundled+manufactured (n_low=0 in
    # both axes). The model isn't currently a generalized graduation predictor
    # — it's a bundled-pump predictor. Set trader expectations accordingly.
    bun = m.get("bundle") or {}
    is_bundled = bool(bun.get("detected"))
    is_manufactured = bool(m.get("manufactured_pump"))
    pop_line = ""
    if is_bundled and is_manufactured:
        pop_line = (
            "_population: bundled launch · "
            "sustains rate calibrated on similar pumps_\n\n"
        )

    return (
        f"{header}\n"
        f"{co_billed_line}\n"
        f"{entry_line}\n"
        f"{backtest_line}\n\n"
        f"{market_block}\n\n"
        f"{sig_block}"
        f"{rug_line}"
        f"{pop_line}"
        f"_NFA · DYOR · prediction model output, not financial advice_\n\n"
        f"`{m['mint']}`"
    )


async def alert_push_drain_tick(context: ContextTypes.DEFAULT_TYPE):
    """Drains the event-pushed pending_alerts queue. Web service enqueues
    the instant a prediction crosses a user's threshold; this tick delivers
    them via TG. Replaces polling for prediction-tied kinds (grad_prob,
    runner_5x, runner_10x) where the lane window is too narrow for the 15s
    polling tick to reliably catch.

    Tier cooldown: paid kinds for free users are still rate-limited to 1
    fire per 24h here too, so the push path doesn't bypass the upgrade-pitch
    cooldown."""
    try:
        _reset_free_skipped_if_new_day()
        import alert_push
        rows = alert_push.drain_pending(limit=50)
        if not rows:
            return
        now_ts = int(time.time())
        application = context.application
        for row in rows:
            tg_id = row["telegram_id"]
            kind = row["kind"]
            mint = row["mint"]
            msg_extra = row.get("msg_extra") or ""
            snap = row.get("snapshot") or {}
            if not snap.get("mint"):
                snap["mint"] = mint

            # Paid-alert hard block. Pre-2026-06-12 this was a soft 24h
            # cooldown for free users — but PAID_ALERT_KINDS was empty so
            # composite_score grandfathered through. Now: any fire for a
            # PAID_ALERT_KIND requires the user's effective_tier (max of
            # paid api_key tier + $ORACLE token-held tier) to meet
            # db.COMPOSITE_SIGNAL_REQUIRED_TIER. If not, skip — row stays
            # marked delivered so we don't keep retrying.
            if kind in PAID_ALERT_KINDS:
                req = getattr(db, "COMPOSITE_SIGNAL_REQUIRED_TIER", None)
                if req is not None:
                    import contextlib
                    paid_t, token_t = "free", "free"
                    try:
                        # expires_at IS NULL: token-holders or comp keys
                        # (no time bound). expires_at > now: still within
                        # paid window. Anything else => row is expired,
                        # treat as free tier.
                        with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=5)) as c, c:
                            c.row_factory = sqlite3.Row
                            r = c.execute(
                                "SELECT tier, COALESCE(token_held_tier, 'free') AS token_tier "
                                "FROM api_keys WHERE telegram_id = ? AND revoked = 0 "
                                "AND (expires_at IS NULL OR expires_at > ?) "
                                "ORDER BY id DESC LIMIT 1",
                                (tg_id, now_ts),
                            ).fetchone()
                            if r:
                                paid_t = r["tier"] or "free"
                                token_t = r["token_tier"] or "free"
                    except Exception:
                        pass
                    rank = ["free", "tg_paid", "builder", "pro", "unlimited"]
                    def _idx(t): return rank.index(t) if t in rank else 0
                    eff_idx = max(_idx(paid_t), _idx(token_t))
                    # During TG_FREE_UNTIL window, deliver to everyone subscribed
                    # regardless of tier. After window expires, normal gate applies.
                    if eff_idx < _idx(req) and not _tg_free_trial_active():
                        # Below required tier — skip the fire. We don't DM
                        # an upgrade prompt here (would spam every fire);
                        # cmd_alert handles that at rule-creation time.
                        continue

            # Render via the same paid-tier format the polling path uses.
            tier, _ = _user_tier(tg_id)
            # FREE-TIER THROTTLE: free users normally get a 15% sample of the
            # live firehose (proves the product works without giving away the
            # full value). Paid users get the full feed.
            #
            # During the TG_FREE_UNTIL launch promo window we DISABLE the
            # throttle entirely — every subscribed user gets every fire,
            # tier-agnostic. This matches the paid-gate bypass at line ~2242
            # so the "EVERYONE IS FREE RIGHT NOW" promise actually holds.
            # After the promo expires, the 15% sample auto-restores.
            FREE_SAMPLE_RATE = 0.15
            import random as _random
            if (tier == "free"
                    and not _tg_free_trial_active()
                    and _random.random() >= FREE_SAMPLE_RATE):
                # Mark this fire as "skipped for free tier" — track the
                # missed count for upgrade prompt context.
                _free_skipped[tg_id] = _free_skipped.get(tg_id, 0) + 1
                continue  # don't render or send; row already marked delivered
            try:
                if kind == "composite_score":
                    # Composite cross — separate formatter regardless of tier.
                    # Composite is a hot-launch event signal, not a graduation
                    # prediction, so the grad_prob render shape doesn't fit.
                    msg = _format_composite_alert(snap, msg_extra)
                elif tier == "free":
                    skipped_today = _free_skipped.get(tg_id, 0)
                    msg = _format_alert_basic(snap, msg_extra)
                    if skipped_today > 0:
                        msg += (f"\n\n_📊 Free tier — you're seeing 1 of "
                                f"every ~7 ACT fires. {skipped_today} since "
                                f"midnight UTC went to paid users only._\n"
                                f"_Upgrade: /upgrade for the full feed._")
                else:
                    msg = _format_alert_rich(snap, msg_extra)
                kb_rows = [[
                    InlineKeyboardButton("Pump", url=f"https://pump.fun/coin/{mint}"),
                    InlineKeyboardButton("Axiom", url=f"https://axiom.trade/t/{mint}"),
                    InlineKeyboardButton("Photon", url=f"https://photon-sol.tinyastro.io/en/lp/{mint}"),
                    InlineKeyboardButton("Dex", url=f"https://dexscreener.com/solana/{mint}"),
                ]]
                # Trader buy buttons — operator-only. Reads the recipient's
                # saved buy presets (3 amounts) from trader_user_settings.
                # Non-admin recipients (current prod users) never see this row.
                if tg_id in _ADMIN_TG_IDS:
                    try:
                        import trader_commands
                        if trader_commands.is_enabled():
                            kb_rows.append(
                                trader_commands.build_buy_buttons(
                                    mint, user_id=tg_id,
                                ).inline_keyboard[0]
                            )
                    except Exception as e:
                        # Never let trader integration break alert delivery
                        print(f"[bot] trader buy-buttons skipped: {e}", flush=True)
                kb = InlineKeyboardMarkup(kb_rows)
                await _send_alert(application, tg_id, msg, reply_markup=kb)

                # ── Auto-trade evaluator ────────────────────────────
                # If user has opted in AND alert tier meets their threshold
                # AND they're under their concurrent-open cap → fire a buy.
                # Operator-only during beta (same gate as buy buttons).
                # Goes through orchestrator.buy() so all existing safety
                # (rate limit, balance floor, TOS, max_trade_sol cap) applies.
                if (kind == "composite_score"
                        and tg_id in _ADMIN_TG_IDS):
                    try:
                        queued_at = int(row.get("queued_at") or 0)
                        await _maybe_auto_trade(application, tg_id, snap, mint,
                                                queued_at=queued_at)
                    except Exception as e:
                        print(f"[bot] auto-trade evaluator failed: {e}",
                              flush=True)
                # Log to tg_fires so the morning audit + /api/alerts/audit
                # endpoint sees push-fired alerts. Without this, the audit
                # is blind to anything the push path delivers (which is
                # most alerts now).
                try:
                    tg_fires.record(
                        m=snap, rule_id=row["rule_id"], telegram_id=tg_id,
                        kind=kind, threshold=0,
                    )
                except Exception as e:
                    print(f"[bot] push-path tg_fires.record failed: {e}", flush=True)
                # Update last_fired_at so the polling tick's cooldown
                # logic doesn't double-fire the same rule.
                try:
                    import contextlib
                    with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=5)) as c, c:
                        c.execute(
                            "UPDATE tg_alert_rules SET last_fired_at = ? WHERE id = ?",
                            (now_ts, row["rule_id"]),
                        )
                except Exception:
                    pass
                # Mirror the in-memory _fired set used by the polling tick
                # so it doesn't re-fire the same (rule, mint, 30-min-bucket).
                _fired.add((row["rule_id"], mint, now_ts // 60 // 30))
            except Exception as e:
                print(f"[alert_push_drain] send failed for tg={tg_id} mint={mint[:8]}: {e}",
                      flush=True)
    except Exception as e:
        print(f"[alert_push_drain] tick failed: {e}", flush=True)


# Admin TG IDs that receive proactive health alerts (disk / memory /
# resolver lag). Born from the 2026-06-02 verdict-day outage where a
# full-disk + watchdog-threshold-drift combo crashed the site for ~1h
# without anyone knowing. See web/health_watchdog.py.
HEALTH_ALERT_ADMINS = (1518749020,)   # primary admin (dsproul)


async def health_alert_tick(context: ContextTypes.DEFAULT_TYPE):
    """JobQueue callback — polls system_health_alerts every 60s, DMs admins
    on new raises AND new resolutions. Pure passive consumer; never writes
    alerts (web/health_watchdog.py owns that)."""
    application = context.application
    try:
        with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=5)) as c:
            c.row_factory = sqlite3.Row
            # Pending raises: never DM'd, not resolved
            pending = c.execute(
                "SELECT alert_id, raised_at, severity, category, message, metric_value "
                "FROM system_health_alerts "
                "WHERE dmd_at IS NULL AND resolved_at IS NULL "
                "ORDER BY alert_id ASC LIMIT 20"
            ).fetchall()
            # Newly-resolved alerts: DM'd but resolved within last 5 min, never confirmed
            recent_resolutions = c.execute(
                "SELECT alert_id, severity, category, message, resolved_at "
                "FROM system_health_alerts "
                "WHERE dmd_at IS NOT NULL AND resolved_at IS NOT NULL "
                "AND resolved_at > ? "
                "AND COALESCE(dmd_at, 0) < resolved_at "
                "ORDER BY alert_id ASC LIMIT 20",
                (int(time.time()) - 300,),
            ).fetchall()
    except Exception as e:
        print(f"[health_alert_tick] db read failed: {e}", flush=True)
        return

    for row in pending:
        emoji = "🚨" if row["severity"] == "critical" else "⚠️"
        msg = (f"{emoji} *{row['severity'].upper()} · {row['category']}*\n"
               f"{row['message']}\n"
               f"_alert #{row['alert_id']} · graduate-oracle health watchdog_")
        for tg_id in HEALTH_ALERT_ADMINS:
            try:
                await application.bot.send_message(
                    tg_id, msg, parse_mode=constants.ParseMode.MARKDOWN)
            except Exception as e:
                print(f"[health_alert_tick] DM to {tg_id} failed: {e}", flush=True)
        try:
            with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=5)) as c, c:
                c.execute(
                    "UPDATE system_health_alerts SET dmd_at=? WHERE alert_id=?",
                    (int(time.time()), row["alert_id"]),
                )
        except Exception as e:
            print(f"[health_alert_tick] mark dmd failed: {e}", flush=True)

    for row in recent_resolutions:
        msg = (f"✅ *RESOLVED · {row['category']}*\n"
               f"{row['message']}\n"
               f"_alert #{row['alert_id']} cleared_")
        for tg_id in HEALTH_ALERT_ADMINS:
            try:
                await application.bot.send_message(
                    tg_id, msg, parse_mode=constants.ParseMode.MARKDOWN)
            except Exception as e:
                print(f"[health_alert_tick] resolution DM to {tg_id} failed: {e}", flush=True)
        # Bump dmd_at to a value > resolved_at so we don't re-send.
        try:
            with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=5)) as c, c:
                c.execute(
                    "UPDATE system_health_alerts SET dmd_at=? WHERE alert_id=?",
                    (int(time.time()), row["alert_id"]),
                )
        except Exception as e:
            print(f"[health_alert_tick] mark resolved-dmd failed: {e}", flush=True)


async def alert_tick(context: ContextTypes.DEFAULT_TYPE):
    """JobQueue callback — fires every 15s, evaluates all active rules."""
    application = context.application
    try:
        rules = _read_active_rules()
        if not rules:
            return
        live = await _api_get("/api/live", params={"limit": 200})
        if not live:
            return
        # /api/live already filters mayhem + dead mints + scores them
        scored = []
        for m in live.get("mints", []):
            # Alerts trigger on the COMBINED headline (curve-shape blended
            # with at-launch features) — that's the number users see on the
            # dashboard, /probe output, and rich alerts. During the
            # early_grad cold-start, combined === grad_prob anyway.
            cp = (m.get("combined_prob") or {})
            m["_prob"] = cp.get("prob") if cp.get("prob") is not None else m.get("grad_prob")
            scored.append(m)

        now_min = int(time.time() / 60)
        now_ts  = int(time.time())
        for rule in rules:
            tg_id = rule["telegram_id"]
            kind = rule["kind"]
            threshold = rule["threshold"] or 0

            # Free-tier premium-alert cooldown — give them a sample taste
            # but rate-limit to once per 24h so they FOMO into upgrading.
            if kind in PAID_ALERT_KINDS:
                user_tier, _ = _user_tier(tg_id)
                if user_tier == "free":
                    last_fired = rule.get("last_fired_at") or 0
                    if last_fired and (now_ts - last_fired) < 86400:
                        continue   # still in cooldown — skip this rule entirely
            for m in scored:
                key = (rule["id"], m["mint"], now_min // 30)
                if key in _fired:
                    continue

                # Universal suppression — never alert on manufactured
                # pumps or actively dangerous bundles, no matter the kind.
                if _alert_is_suppressed(m):
                    continue

                fire = False
                msg_extra = ""
                if kind == "grad_prob":
                    # Fixed 70% threshold. Two flavors per alert_push._matches:
                    #   🎯 ACT  — clean entry (cur≤2, vsol≥0)
                    #   📊 WATCH — entry too late, calibration receipt only
                    GRAD_THRESHOLD = 0.70
                    age_s = m.get("age_s") or 0
                    grad = m.get("grad_prob")
                    cur_mult = m.get("current_mult") or 0
                    vsol_growth = m.get("vsol_growth_sol") or 0
                    if (age_s <= 90 and grad is not None and grad >= GRAD_THRESHOLD):
                        fire = True
                        if cur_mult <= 2.0 and vsol_growth >= 0:
                            msg_extra = (
                                f"🎯 ACT — grad_prob *{grad*100:.0f}%* at age "
                                f"{age_s:.0f}s · entry {cur_mult:.2f}× · vsol +{vsol_growth:.1f}"
                            )
                        else:
                            why = []
                            if cur_mult > 2.0:
                                why.append(f"entry already *{cur_mult:.1f}×* launch")
                            if vsol_growth < 0:
                                why.append(f"vsol *{vsol_growth:.1f}*")
                            msg_extra = (
                                f"📊 WATCH — grad_prob *{grad*100:.0f}%* at age "
                                f"{age_s:.0f}s · {' · '.join(why)}"
                            )
                elif kind == "runner_5x":
                    p = m.get("runner_prob_5x_from_now")
                    upside = m.get("expected_upside_from_now") or 0
                    cm = m.get("current_mult", 1) or 1
                    vsol_growth = m.get("vsol_growth_sol") or 0
                    # Same entry-quality gates as grad_prob: don't fire if
                    # already pumped past 2× or if liquidity is bleeding.
                    if (p is not None and p >= threshold and upside >= 1.5
                            and cm <= 2.0 and vsol_growth >= 0):
                        fire = True
                        msg_extra = (f"runner odds *{p*100:.0f}%* for ≥5× from current price "
                                     f"(now at {cm:.2f}× launch, upside {upside:.1f}×, vsol +{vsol_growth:.1f})")
                elif kind == "runner_10x":
                    p = m.get("runner_prob_10x_from_now")
                    upside = m.get("expected_upside_from_now") or 0
                    cm = m.get("current_mult", 1) or 1
                    vsol_growth = m.get("vsol_growth_sol") or 0
                    if (p is not None and p >= threshold and upside >= 1.5
                            and cm <= 2.0 and vsol_growth >= 0):
                        fire = True
                        msg_extra = (f"runner odds *{p*100:.0f}%* for ≥10× from current price "
                                     f"(now at {cm:.2f}× launch, upside {upside:.1f}×, vsol +{vsol_growth:.1f})")
                elif kind == "smart_in":
                    n_in = m.get("smart_money_in") or 0
                    if n_in >= threshold:
                        fire = True
                        msg_extra = f"*{n_in}* smart-money wallets currently in top buyers"
                elif kind == "runner_dev":
                    ch = m.get("creator_history") or {}
                    if ch.get("runner_creator"):
                        fire = True
                        msg_extra = (f"runner-dev launch — creator has *{ch.get('n_launches', 0)}* "
                                     f"prior mints, *{(ch.get('rate_5x') or 0)*100:.0f}%* hit ≥5×")
                elif kind == "acceleration":
                    accel = m.get("vsol_acceleration") or 0
                    if accel >= threshold:
                        fire = True
                        msg_extra = (f"⚡ vSOL acceleration *+{accel:.1f}* SOL · "
                                     f"last 30s: +{m.get('vsol_velocity_30s', 0):.1f} SOL")
                elif kind == "whale_pile_in":
                    wb = m.get("wallet_balance") or {}
                    n_whales = wb.get("n_whale_wallets") or 0
                    if n_whales >= threshold:
                        fire = True
                        msg_extra = (f"$ *{n_whales}* whale wallets in top buyers · "
                                     f"avg {wb.get('avg_buyer_sol', 0):.1f} SOL · "
                                     f"max {wb.get('max_buyer_sol', 0):.1f} SOL")
                elif kind == "cluster_pile_in":
                    cl = m.get("cluster") or {}
                    if (cl.get("n_clustered_pairs") or 0) >= 1:
                        fire = True
                        msg_extra = (f"◇ smart-money cluster pile-in — "
                                     f"*{cl.get('n_clustered_pairs')}* clustered pair(s), "
                                     f"max {cl.get('max_pair_count')} prior co-buys")
                elif kind == "dex_paid":
                    dx = m.get("dex_paid") or {}
                    if dx.get("is_paid"):
                        fire = True
                        msg_extra = (f"🟢 DexScreener paid info enabled · "
                                     f"{dx.get('n_websites', 0)} site(s) · "
                                     f"{dx.get('n_socials', 0)} social(s)")
                elif kind == "fee_delegation_set":
                    fd = m.get("fee_delegation") or {}
                    if (fd.get("total_bps") or 0) > 0:
                        fire = True
                        pct = (fd.get("total_bps") or 0) / 100
                        n = fd.get("n_delegates") or 0
                        msg_extra = (f"🤝 creator-fee delegation set — "
                                     f"*{pct:.0f}%* across {n} delegate(s)")
                elif kind == "fee_delegated_full":
                    fd = m.get("fee_delegation") or {}
                    if (fd.get("total_bps") or 0) >= 10000:
                        fire = True
                        prim = fd.get("primary_delegate") or ""
                        prim_short = prim[:6] + '…' + prim[-4:] if len(prim) > 14 else prim
                        msg_extra = (f"🤝 *100%* of creator fees delegated → "
                                     f"`{prim_short}`")
                elif kind == "bundle_detected":
                    bun = m.get("bundle") or {}
                    if bun.get("detected") and (bun.get("pct") or 0) >= threshold:
                        fire = True
                        msg_extra = (f"⚠ *bundle detected* — *{bun.get('size')}* wallets "
                                     f"bundled at t={bun.get('at_t_s', 0):.2f}s · "
                                     f"hold *{bun.get('pct'):.0f}%* of supply")
                elif kind == "vsol_burst":
                    if m["vsol_growth_sol"] >= threshold:
                        fire = True
                        msg_extra = f"vSOL +{m['vsol_growth_sol']:.1f} SOL"
                elif kind == "x_factor":
                    # Lift threshold + entry-quality gates same as runner.
                    xf = m.get("x_factor") or {}
                    lift = xf.get("lift_x")
                    upside = m.get("expected_upside_from_now") or 0
                    cm = m.get("current_mult", 1) or 1
                    vsol_growth = m.get("vsol_growth_sol") or 0
                    if (lift is not None and lift >= threshold
                            and upside >= 1.5 and cm <= 2.0 and vsol_growth >= 0):
                        tier = xf.get("best_tier")
                        prob = xf.get("prob") or 0
                        msg_extra = (
                            f"x_factor *{lift:.1f}× base* on the ≥{tier:g}× "
                            f"tier (prob *{prob*100:.0f}%*, entry {cm:.2f}×, "
                            f"upside {upside:.1f}×, lift threshold {threshold:.1f}×)"
                        )
                        fire = True
                # Tier-kind alerts handled by the dedicated branch above
                # (around line 1299) — they `continue` past this dispatch.
                if not fire:
                    continue
                _fired.add(key)

                # Persist the fire + full alert-time feature snapshot for
                # later outcome resolution. Wrapped — a logging failure
                # must never break an alert send.
                try:
                    tg_fires.record(
                        m=m, rule_id=rule.get("id"), telegram_id=tg_id,
                        kind=kind, threshold=threshold,
                    )
                except Exception as e:
                    print(f"[bot] tg_fires.record failed: {e}")

                # Webhook fan-out for Pro-tier users with registered URLs.
                # Runs alongside the TG message — receivers get a structured
                # JSON push with HMAC sig instead of having to scrape TG.
                api_key_id = _user_api_key_id(tg_id)
                if api_key_id:
                    try:
                        webhooks_mod.enqueue(
                            event_kind=kind,
                            payload={
                                "mint":         m["mint"],
                                "kind":         kind,
                                "threshold":    threshold,
                                "grad_prob":    m.get("grad_prob"),
                                "current_mult": m.get("current_mult"),
                                "vsol_sol":     m.get("current_vsol_sol"),
                                "buyers":       m.get("unique_buyers"),
                                "age_s":        m.get("age_s"),
                                "smart_money_in": m.get("smart_money_in"),
                                "creator_history": m.get("creator_history"),
                                "wallet_balance":  m.get("wallet_balance"),
                                "cluster":         m.get("cluster"),
                                "runner_prob_5x_from_now":  m.get("runner_prob_5x_from_now"),
                                "runner_prob_10x_from_now": m.get("runner_prob_10x_from_now"),
                            },
                            api_key_filter=api_key_id,
                        )
                    except Exception as e:
                        print(f"[bot] webhook enqueue failed: {e}")

                chat_id = _user_chat_id(tg_id)
                if not chat_id:
                    continue
                try:
                    # If this is a free-tier premium-alert sample, append the
                    # FOMO upgrade nudge right in the alert body.
                    upgrade_tail = ""
                    if kind in PAID_ALERT_KINDS:
                        user_tier_now, _ = _user_tier(tg_id)
                        if user_tier_now == "free":
                            upgrade_tail = (
                                "\n\n_🔒 your daily free sample · "
                                "next fire in 24h · `/upgrade builder` for unlimited_"
                            )

                    # Format follows the user's plan tier:
                    #   • free  → minimal one-liner
                    #   • paid  → rich card with all signals
                    user_plan_tier, _ = _user_tier(tg_id)
                    if user_plan_tier == "free":
                        text = _format_alert_basic(m, msg_extra) + upgrade_tail
                    else:
                        text = _format_alert_rich(m, msg_extra)
                    kb = InlineKeyboardMarkup([[
                        InlineKeyboardButton("Pump", url=f"https://pump.fun/coin/{m['mint']}"),
                        InlineKeyboardButton("Axiom", url=f"https://axiom.trade/t/{m['mint']}"),
                        InlineKeyboardButton("Dex", url=f"https://dexscreener.com/solana/{m['mint']}"),
                    ]])
                    await _send_alert(application, chat_id, text, reply_markup=kb)
                    # Stamp the fire so the 24h cooldown can be enforced on
                    # next tick. Always update — Builder/Pro users use this
                    # for telemetry too, even though they're not rate-limited.
                    try:
                        with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=5)) as c, c:
                            c.execute(
                                "UPDATE tg_alert_rules SET last_fired_at = ? WHERE id = ?",
                                (now_ts, rule["id"]),
                            )
                    except Exception:
                        pass
                except Exception as e:
                    print(f"[bot] alert send failed: {e}")
    except Exception as e:
        print(f"[bot] evaluator error: {e}")


# ── main ──────────────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not set in env.")
        print("get one from @BotFather on Telegram, then add to .env")
        sys.exit(1)

    async def _post_init(application):
        """Register the bot's command menu + chat menu button so users
        see a 'Menu' icon next to the text input that opens the command
        list (with /trader as the top item). This is the 'home button'
        — one tap from anywhere instead of typing /trader."""
        from telegram import BotCommand, MenuButtonCommands
        try:
            await application.bot.set_my_commands([
                BotCommand("trader",      "🏠 Trader hub — buy/sell/portfolio/settings"),
                BotCommand("portfolio",   "📊 Your open + closed positions"),
                BotCommand("wallet",      "💰 Wallet balance + deposit/withdraw"),
                BotCommand("me",          "👤 Account info + tier"),
                BotCommand("help",        "📖 What this bot does"),
            ])
            await application.bot.set_chat_menu_button(
                menu_button=MenuButtonCommands(),
            )
            print("[bot] command menu + chat menu button set", flush=True)
        except Exception as e:
            print(f"[bot] menu setup failed (non-fatal): {e}", flush=True)

    app = (Application.builder()
              .token(BOT_TOKEN)
              .post_init(_post_init)
              .build())

    # TOS acceptance — registered early so the inline button callback is
    # available to all subsequent flows that might surface the prompt.
    try:
        import tos_gate
        tos_gate.register(app)
    except Exception as e:
        print(f"[bot] tos_gate registration failed (non-fatal): {e}", flush=True)

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("probe", cmd_probe))
    app.add_handler(CommandHandler("watch", cmd_watch))
    app.add_handler(CommandHandler("unwatch", cmd_unwatch))
    app.add_handler(CommandHandler("portfolio", cmd_portfolio))
    app.add_handler(CommandHandler("leaderboard", cmd_leaderboard))
    app.add_handler(CommandHandler("wallet", cmd_wallet))
    app.add_handler(CommandHandler("alert", cmd_alert))
    app.add_handler(CommandHandler("alerts", cmd_alerts))
    app.add_handler(CommandHandler("me", cmd_me))
    app.add_handler(CommandHandler("verdict", cmd_verdict))
    app.add_handler(CommandHandler("tiers", cmd_tiers))
    app.add_handler(CommandHandler("sample", cmd_sample))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("upgrade", cmd_upgrade))
    app.add_handler(CommandHandler("plans", cmd_plans))
    app.add_handler(CommandHandler("accuracy", cmd_accuracy))
    app.add_handler(CommandHandler("grant", cmd_grant))

    # Trader commands — operator-gated, off by default. Set TRADER_ENABLED=1
    # + ADMIN_TG_IDS=<id> on the bot's env to turn on. Isolated in a
    # separate module so trader bugs can't crash this main bot loop.
    try:
        import trader_commands
        if trader_commands.register(app, _ADMIN_TG_IDS):
            # Setup-menu module — depends on trader_commands being enabled.
            try:
                import trader_setup
                trader_setup.register(app, _ADMIN_TG_IDS)
            except Exception as e:
                print(f"[bot] trader_setup registration failed: {e}", flush=True)
            # Withdraw wizard — isolated so wizard bugs can't crash buys/sells.
            try:
                import trader_withdraw
                trader_withdraw.register(app, _ADMIN_TG_IDS)
            except Exception as e:
                print(f"[bot] trader_withdraw registration failed: {e}", flush=True)
    except Exception as e:
        # Importing the trader module pulls in web/* — if anything goes
        # wrong we log + skip. The bot keeps running with the existing
        # public commands.
        print(f"[bot] trader_commands registration failed (skipping): {e}", flush=True)

    # Schedule alert evaluator via JobQueue (proper PTB v21 pattern)
    app.job_queue.run_repeating(alert_tick, interval=15, first=10, name="alert_evaluator")
    # Fast drain of event-pushed alerts. Web service writes pending_alerts
    # the instant a prediction crosses a user threshold (no 15s polling lag).
    # Tick is 1.5s — small SELECT on an indexed table, sub-ms in practice.
    app.job_queue.run_repeating(alert_push_drain_tick, interval=1.5, first=3,
                                name="alert_push_drain")
    # Proactive health alerts — polls system_health_alerts (written by
    # web/health_watchdog.py) and DMs admins on raises + resolutions.
    app.job_queue.run_repeating(health_alert_tick, interval=60, first=30,
                                name="health_alert_drain")

    print("[bot] GRADUATE oracle online")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
