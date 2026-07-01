"""
Pump.fun Observer — local web service.

Reads:
  - observer-active.json (live in-memory snapshot from observer-daemon)
  - observer-curves/ (historical, used to score graduation probability)

Serves:
  - /         single-page HTML dashboard (auto-refresh)
  - /api/live JSON: scored live mints, sorted by graduation probability

Run:
  uvicorn main:app --reload --port 8765
  (or: python main.py)
"""
import json
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Optional

# Sentinel: this file runs as __main__ but paper_trade.py + api_v1.py do
# late `from main import X` inside hot-path functions. Without this alias,
# Python would load main.py a SECOND time (as the `main` module), re-
# executing every top-level start() and spawning duplicate daemon threads.
# See docs/research/late_import_double_load_postmortem_2026_05_13.md.
sys.modules["main"] = sys.modules[__name__]

# Load .env from project root before any other imports that read env vars
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from fastapi import FastAPI, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

import grad_prob
import wallet_intel
import creator_history
import cluster_intel
import wallet_balance
import fee_delegation
import dex_paid
import metadata
import narrative_enrichment
import post_grad_tracker
import early_grad_tracker
import mint_checkpoints
import rug_predictor
import rug_heuristic
import observer_health
import funding_ancestry
import status_module
import db
import api_v1
import sol_pay
import paper_trade
import calibration
import predictions
import gbm_shadow
import bucket_cutoffs
import ledger
import alert_push
import public_export
import composite_predictions
import tg_fires
import gate_validation
import wsfanout
from security import rate_limit_middleware, body_size_middleware

# Resolve paths relative to project root (parent of web/)
ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_PATH = ROOT / "observer-active.json"
CURVES_DIR = ROOT / "observer-curves"
WEB_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="GRADUATE",
    description="Live graduation probability + smart-money intelligence for pump.fun. Public API requires a free or paid key.",
    version="1.0.0",
    contact={"name": "GRADUATE", "url": "https://graduateoracle.fun"},
    # We own /docs ourselves — Swagger UI moves to /swagger so power users
    # can still hit it for try-it-out functionality, but the developer-
    # facing docs site is hand-curated content.
    docs_url="/swagger",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)
# Strict CORS — only our own domains can call the API from a browser.
# Server-to-server callers (bots, scripts) ignore CORS, so this only restricts
# JS-from-other-origins, never legitimate API integrations.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://graduateoracle.fun",
        "https://www.graduateoracle.fun",
        "https://graduate-oracle.fly.dev",
        "http://localhost:8765",
        "http://127.0.0.1:8765",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "X-API-Key", "Content-Type"],
    max_age=600,
)

# Per-IP rate limit + body size guard — registered as middleware so they run
# before any handler. Order: body size first (cheap reject), then rate limit.
app.middleware("http")(body_size_middleware)
app.middleware("http")(rate_limit_middleware)

app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
# brand assets at /brand/<file>
app.mount("/brand", StaticFiles(directory=ROOT / "brand", html=True), name="brand")
app.include_router(api_v1.router)


# Stop browsers from caching the dashboard during active development —
# updates to JS/CSS/HTML take effect on a normal refresh, no Cmd+Shift+R needed.
@app.middleware("http")
async def no_cache_assets(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith("/static") or path.startswith("/api"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response

# Auto-reap any forked pickle-saver child processes — without this, finished
# children become zombies (defunct PIDs in the process table) until the parent
# explicitly waits on them. SIG_IGN on SIGCHLD tells the kernel to clean up
# automatically. Safe because we don't care about child exit codes.
import signal
try:
    signal.signal(signal.SIGCHLD, signal.SIG_IGN)
except (AttributeError, ValueError):
    pass  # Windows or non-main thread

# Initialize indices on startup. Refresh intervals are deliberately long —
# the heavy refresh work (json.load on hundreds of new curves + dict merges)
# is CPU-bound and holds the GIL, blocking request handlers. Stretching the
# interval to 15-30 min reduces total time-in-refresh and keeps the dashboard
# responsive. Data freshness loss is minimal: live mint scoring is unaffected
# (it uses the in-memory index regardless of refresh cadence).
INDEX = grad_prob.init(curves_dir=str(CURVES_DIR), refresh_interval_s=900)   # 15 min
# Sticky-grad-prob cache. Once we score a mint at age_bucket 30 or 60, we
# lock that prediction value so the dashboard / TG doesn't keep changing it
# as the curve evolves. Maps mint -> (locked_prob, age_bucket). 60s value
# wins over 30s (refinement). Evicted lazily when the dict grows past 5000.
_LOCKED_GRAD: dict[str, tuple[float, int]] = {}
_LOCKED_GRAD_LOCK = threading.Lock()

WINTEL = wallet_intel.init(curves_dir=str(CURVES_DIR), refresh_interval_s=1800)  # 30 min
# Creator-history index: per-wallet aggregate of prior launch outcomes. Used
# in _enrich_mint to attach creator track-record stats to live mints. Forward-
# tested 4.6× lift in 5×+ outcomes when the creator has ≥3 prior launches.
CREATORS = creator_history.init(curves_dir=str(CURVES_DIR), refresh_interval_s=1800)
# Cluster-intel: pairwise co-buy history among smart-money wallets so we
# can flag "clustered pile-in" — multiple known leaderboard wallets in the
# same mint who have a history of moving together. V1 is smart-only (cheap);
# V2 will broaden to whole-population sybil detection.
cluster_intel.start(curves_dir=str(CURVES_DIR), get_wintel=lambda: WINTEL)
# Wallet enrichment — batches getMultipleAccounts via the same RPC sol_pay
# uses. Adds avg/max/whale-count of buyer SOL balances to /api/live.
wallet_balance.start(snapshot_path=str(SNAPSHOT_PATH))
# Fee delegation enrichment — pump.fun late-2026 added on-chain creator
# fee splitter. We decode the per-mint delegation table to surface
# "fees 100% delegated" / "fees split N ways" badges + a feature for the
# post-grad survival predictor (delegated mints likely sustain better
# because the creator has skin in the game via a partner contract).
fee_delegation.start(snapshot_path=str(SNAPSHOT_PATH))
# DexScreener paid-info enrichment — soft signal that creator has
# nonzero marketing budget (paid the $99-$300 to enable Enhanced Token
# Info). Useful filter against one-shot rugs at the fringes.
dex_paid.start(snapshot_path=str(SNAPSHOT_PATH))
# Token metadata enrichment — pulls name / symbol / image_url for every
# live mint via batched Helius getMultipleAccounts (Token-2022 metadata
# extension is parsed inline, no Borsh decoding needed).
metadata.start(snapshot_path=str(SNAPSHOT_PATH))
# Narrative watcher (Path A pilot) — refreshes a trending-terms set from
# Reddit + Google Trends every 15min. enrich() per mint is a cheap set
# intersection on name/symbol. INTERNAL ONLY: lands in mint_checkpoints
# for outcome stratification, NOT in /api/v1 and NOT in tier logic.
# See docs/research/path_a_narrative_pilot_proposal.md.
narrative_enrichment.start()
# Post-graduation survival tracker — watches mints that cross vsol≥115,
# polls Jupiter at 5/15/30-min checkpoints, persists outcomes. Builds
# the dataset for a future "did this graduate sustain?" predictor.
post_grad_tracker.start(snapshot_path=str(SNAPSHOT_PATH))
# Early-stage graduation predictor — k-NN over at-launch features only.
# Spots high-conviction mints BEFORE the curve reveals itself, which is
# the actual edge over watching pump.fun's UI directly.
early_grad_tracker.start(snapshot_path=str(SNAPSHOT_PATH))
# Multi-checkpoint feature corpus + capture daemon. Polls /api/live every
# 5s and writes one row per (mint, checkpoint_age) when the mint passes 15s
# / 30s / 60s / 120s. Idempotent via PK. Outcome resolvers and predictors
# (rug_prob, early_grad_v2) read from this table in later phases.
mint_checkpoints.init_schema()
funding_ancestry.init_schema()
mint_checkpoints.start()
# Outcome resolver — walks observer-curves every 5 min and stamps
# actual_graduated + actual_rugged onto every captured mint that has
# flushed. Both labels needed for the rug_prob and early_grad_v2 k-NN.
mint_checkpoints.start_resolver(str(CURVES_DIR))
# Observer trade-capture verifier · samples 50 fresh mints every 15 min,
# compares observer's n_trades to on-chain sig count, alerts loudly if
# median capture rate drops below 95%. This is the lie detector — it
# exists because we shipped a broken observer for weeks without noticing.
# Now any future "fix" gets continuously verified against ground truth.
observer_health.start(str(SNAPSHOT_PATH))

# SOL payment watcher — starts only if TREASURY_WALLET + RPC_HTTP are set
sol_pay.start_watcher_thread()

# Paper trading daemon — disabled 2026-05-13. Existing rows in paper_positions
# remain queryable at /api/paper for historical reference; no new entries open.
# paper_trade.start()

# Calibration daemon — runs leave-one-out k-NN on the historical curve index
# every 6h to compute "of mints we'd predict ≥X%, Y% actually graduated."
# Exposed at /api/accuracy and surfaced as a ticker on the dashboard.
calibration.start(lambda: INDEX)

# Forward-prediction logger + resolver. Every live score ≥50% gets logged
# (INSERT OR IGNORE on (mint, age_bucket)); a 5-min daemon scans flushed
# curves and updates rows with actual_graduated. After ~30 days this gives
# us a bulletproof forward calibration number.
predictions.start(curves_dir=str(CURVES_DIR))

# Tamper-evident ledger: hourly merkle-roots over every prediction made in
# the period. Exposed at /api/ledger/commits and /api/ledger/proof/{id}.
# Foundation for external commitment (twitter / on-chain memo) — for now,
# the surface alone is snapshot-able by anyone who wants to audit history.
ledger.start_commit_daemon()

# Composite-receipts ledger (added 2026-05-11). Parallel structure to the
# grad_prob ledger above — same tamper-evident merkle discipline applied
# to the composite_predictions table. Hourly commits + outcome resolver.
# See web/composite_predictions.py + docs/research/composite_receipts_logging_prereg.md
composite_predictions.start()

# ── Memory pressure watchdog (Path B workaround, 2026-05-12) ─────────────
# Per diagnostic at 06:38Z: web process memory grows ~175 MB/min in
# UNTRACKED C-extension memory (numpy/sklearn buffers + pymalloc
# fragmentation), driving the process to OOM-kill every ~1h. Not a
# discrete code leak — structural behavior under sustained sklearn/numpy
# workload at our throughput. Root-cause fix requires architectural
# change (offload scoring to subprocess, OR reduce per-tick scoring
# volume) — filed as audit-program review item.
#
# Workaround: self-exit on memory threshold; supervisord auto-restarts.
# Threshold set well below the 4 GB fly machine ceiling so the restart
# is clean (not an OOM kill). Documented as workaround in
# docs/research/web_service_memory_pressure_postmortem_2026_05_12.md.
def _memory_watchdog_loop():
    import os
    # 2026-06-10: threshold raised from 7.0 GB to 14.0 GB. fly.toml [[vm]]
    # memory_mb bumped 8GB → 16GB to triple the time between crash cycles
    # while Path 2 (subprocess-isolate scoring) lands as permanent fix.
    # Sits 2 GB below the 16 GB ceiling for clean-restart-not-OOM-kill margin.
    THRESHOLD_KB = 14_000_000   # 14.0 GB — well below 16 GB ceiling
    CHECK_INTERVAL_S = 30
    pid = os.getpid()
    print(f"[memory_watchdog] daemon started (pid={pid}, threshold={THRESHOLD_KB/1024/1024:.1f} GB)", flush=True)
    while True:
        try:
            with open(f"/proc/{pid}/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        rss_kb = int(line.split()[1])
                        if rss_kb >= THRESHOLD_KB:
                            print(f"[memory_watchdog] RSS={rss_kb} kB >= threshold; "
                                  f"requesting supervisord restart", flush=True)
                            # SIGTERM rather than os._exit so any in-flight
                            # write commits cleanly through atexit handlers.
                            import signal as _signal
                            os.kill(pid, _signal.SIGTERM)
                            return
                        break
        except Exception as e:
            print(f"[memory_watchdog] check error: {e}", flush=True)
        time.sleep(CHECK_INTERVAL_S)

_memory_watchdog_started = False
_memory_watchdog_lock = threading.Lock()


def _start_memory_watchdog():
    """Idempotent spawn — protects against late-import re-execution of
    main.py top-level (the sentinel above is the primary defense; this is
    the backstop, matching the pattern in observer_health/ledger/etc.)."""
    global _memory_watchdog_started
    if _memory_watchdog_started:
        return
    with _memory_watchdog_lock:
        if _memory_watchdog_started:
            return
        _memory_watchdog_started = True
        threading.Thread(target=_memory_watchdog_loop, daemon=True,
                         name="memory-watchdog").start()


_start_memory_watchdog()

# Event-driven TG alert push — eagerly enqueues alerts the moment a
# prediction crosses a user's threshold, instead of waiting for the bot's
# 15s poll. Kills the lane-race that caused real misses on grad_prob 54%
# mints in the 60-90s window.
alert_push.init_schema()

# ORACLE token utility scaffolding — DORMANT until ORACLE_MINT env is set.
# Adds api_keys columns (idempotent) and starts the 10-min refresh loop
# (which idles harmlessly with no RPC traffic while ORACLE_MINT is the
# sentinel placeholder). See web/token_utility.py for the full design.
import token_utility
token_utility.migrate_schema()
token_utility.start_refresh_loop()

# CLI wallet-link flow — browser handoff signature pattern for goracle's
# `signup --wallet` command. Migrates the pending_wallet_links table.
# Endpoints below at /api/signup/wallet/*; browser handoff at /cli-link/{id}.
import cli_wallet_link
cli_wallet_link.migrate_schema()

# Proactive health watchdog — 60s ticks measuring disk / memory / resolver
# lag. Raises rows in system_health_alerts on threshold cross; the TG bot
# polls and DMs admin TG IDs. Born from the 2026-06-02 verdict-day outage
# (full disk + watchdog-threshold mismatch went unnoticed for ~1h). See
# web/health_watchdog.py.
import health_watchdog
health_watchdog.start()

# Daily subscription-expiry sweep — downgrades expired paid keys to free
# and deactivates lapsed subscriptions ledger rows. Most code paths check
# expires_at directly; this loop is the belt-and-suspenders so stale
# tier='tg_paid' rows don't accumulate. See web/subscription_expiry.py.
import subscription_expiry
subscription_expiry.start()

# ORACLE burn queue + revenue buy-and-burn accumulator. Two burn sources
# feed one queue: pay-with-token receipts (PAY_BURN_PCT) and weekly revenue
# share (REVENUE_BURN_PCT). DORMANT until ORACLE_MINT set. See web/token_burn.py.
import token_burn
token_burn.start_accumulator()

# TG-fire outcome tracker — joins each fire to predictions.actual_max_mult
# once the curve has resolved, to give us "did this alert pay from the
# price the user actually saw?" aggregated across kinds + feature subsets.
# Pure observer; nothing feeds back into the live model. See web/tg_fires.py.
tg_fires.start()
# Gate-validation daemon — hourly tick that runs the BACKLOG.md
# pre-registered criterion mechanically. The whole point of the
# pre-registration is that we don't manually re-run the stats when
# n=30 hits and accidentally massage the definition. Daemon writes
# one row per tick to gate_validation_runs. /api/gate_validation
# surfaces the most recent decision.
gate_validation.start()

# Public-export daemon — once a day, materializes calibration / paper-trade /
# leaderboard snapshots, commits + pushes to github.com/Based-LTD/graduate-oracle
# via SSH deploy key (Fly secret GIT_DEPLOY_KEY). One commit/day on the public
# repo = visible "active project" on GitHub's contribution graph + accuracy
# track record any token holder or scanner can audit.
public_export.start(
    wintel_getter=lambda: WINTEL,
    get_calibration=calibration.get_snapshot,
    get_forward=predictions.get_live_calibration,
    get_paper=paper_trade.stats,
    get_runner=predictions.get_runner_calibration,
)


@app.on_event("startup")
async def _ws_register_loop():
    """Hand the running event loop to wsfanout so the sync precompute thread
    can schedule put_nowait calls onto per-client async queues."""
    import asyncio as _asyncio
    wsfanout.register_loop(_asyncio.get_running_loop())


@app.on_event("startup")
async def _gbm_shadow_warmup():
    """Eager-load the GBM shadow model at startup so the first prediction
    doesn't race with /data volume mount (the bug that cost us 19h of silent
    dual-write failure on 2026-05-05). Failure here is non-fatal — the retry
    loop in gbm_shadow._ensure_loaded() picks it up within 60s."""
    gbm_shadow.warmup()


@app.on_event("startup")
async def _perps_observatory_start():
    """Background poller for the Perps Observatory (Hyperliquid + stubs for
    Drift / Jupiter Perps). Tells the "we're expanding to perps" story with
    real, growing numbers — counters survive deploy via /data persistence."""
    import perps_observatory
    perps_observatory.start()


@app.on_event("startup")
async def _perps_intel_start():
    """Background poller for the wallet-intel layer — Hyperliquid leaderboard
    ingest + per-wallet position snapshots. Same playbook as our pump.fun
    smart_money_leaderboard. Building in public — data accumulates from
    boot, receipts chain follows once we ship signals."""
    import perps_intel
    perps_intel.start()


@app.on_event("startup")
async def _bucket_cutoffs_start():
    """Start the bucket-cutoffs rebuild daemon. 24h cadence (Lane 13
    anti-overshoot lesson). Initial rebuild may produce insufficient_samples
    on a fresh deploy; the daemon retries and bucket_for() returns "LOW"
    defensively until cutoffs are populated."""
    bucket_cutoffs.start()


@app.on_event("startup")
async def _trader_wallets_start():
    """Initialize the custody DB schema for the TG trading bot. Idempotent.
    The schema is in its OWN sqlite file (/data/trader.sqlite) so custody
    data stays physically isolated from the giant observer DB. The actual
    wallet generation happens lazily on first user interaction in the bot."""
    if not os.environ.get("TRADER_MASTER_KEY", "").strip():
        # Dormant: log once, don't init schema. Generating wallets without
        # the master key would be silently broken — fail loud at use time
        # by leaving the schema uninitialized.
        print("[trader_wallets] DORMANT — TRADER_MASTER_KEY not set; "
              "trading bot custody disabled until set", flush=True)
        return
    try:
        import trader_wallets
        trader_wallets.init_schema()
        print("[trader_wallets] schema initialized — custody layer live", flush=True)
    except Exception as e:
        print(f"[trader_wallets] init failed: {e}", flush=True)


@app.on_event("startup")
async def _trader_deposits_start():
    """Start the deposit-watcher daemon + trading-state schema. The
    daemon polls every active user wallet for incoming SOL on the
    POLL_INTERVAL_S tick. Writes deposit events + maintains the
    last-seen balance ledger. Dormant if TRADER_MASTER_KEY is unset."""
    try:
        import trader_deposits
        trader_deposits.start()
    except Exception as e:
        print(f"[trader_deposits] start failed: {e}", flush=True)


_snapshot_cache: dict = {"mtime": None, "data": None}
_snapshot_cache_lock = threading.Lock()


def _read_snapshot() -> Optional[dict]:
    """mtime-cached snapshot reader. 420 KB JSON; reparsing every poll was
    a real CPU hog. Cache by file mtime: skip the parse if unchanged.

    2026-06-10: Reinstated after proving (via full rollback test) that this
    cache does NOT contribute to the structural memory leak — the leak is
    in _score_mints sklearn allocations, not here. Net 10× perf win."""
    if not SNAPSHOT_PATH.exists():
        return None
    try:
        mtime = SNAPSHOT_PATH.stat().st_mtime_ns
    except OSError:
        return None
    with _snapshot_cache_lock:
        if _snapshot_cache["mtime"] == mtime and _snapshot_cache["data"] is not None:
            return _snapshot_cache["data"]
    try:
        data = json.loads(SNAPSHOT_PATH.read_text())
    except Exception:
        return None
    with _snapshot_cache_lock:
        _snapshot_cache["mtime"] = mtime
        _snapshot_cache["data"] = data
    return data


HARD_HIT_FLAGS = {
    "sybil_buyers", "diffuse_sybil",
    "fresh_wallet_farm", "sniper_dominated", "bad_history_buyers",
    "no_sell_pressure", "wallet_cycling",
}

# Graduation-state thresholds on virtual_sol_reserves (the snapshot field).
# Pump.fun graduation is at vsol ≈ 115 (= 30 initial + 85 real SOL deposited).
NEAR_GRAD_VSOL = 100.0     # within striking distance of graduation
GRAD_VSOL      = 115.0     # threshold itself

def _graduation_progress(m: dict) -> dict:
    """Curve-position metric — fraction of the bonding curve filled, NOT a
    prediction. Distinct from grad_prob (model output): this is observed
    mechanical state, derived directly from current_vsol_sol.

    Formula (locked once, here): pct = (vsol - 30) / 85 × 100, clamped to
    [0, 100]. Pump.fun curves start at vsol = 30 SOL (initial seed) and
    graduate at vsol ≈ 115 SOL (30 + 85 real deposits). The naive
    `vsol / 115 × 100` gives 26% at launch, which is the wrong intuition;
    we want fresh launches to read as 0%.

    status: "live"      — actively climbing the curve
            "near"      — within striking distance (vsol >= 100)
            "graduated" — bonded (vsol >= 115); pct returns 100.0
            "unknown"   — vsol missing (pct = None)

    Defining the contract here so every display surface (alert template,
    dashboard, API consumers) renders consistently. Do not redefine on
    the display side."""
    vsol = m.get("current_vsol_sol")
    if vsol is None:
        return {"pct": None, "status": "unknown"}
    pct = max(0.0, min(100.0, (vsol - 30.0) / 85.0 * 100.0))
    if vsol >= GRAD_VSOL:
        status = "graduated"
    elif vsol >= NEAR_GRAD_VSOL:
        status = "near"
    else:
        status = "live"
    return {"pct": round(pct, 1), "status": status}


def _graduation_state(m: dict) -> Optional[str]:
    """Returns 'near' (active climbing toward graduation) or None.

    The dashboard is for finding *entries*; mints that already graduated
    can't be traded on pump.fun anymore (their bonding curve closed and
    trading moved to Raydium), so we hide them — receipts live in the
    daily commit + forward calibration numbers, not on the action board.

    We DO want to keep visible the active mints climbing through the final
    approach to graduation — they're the climax. Pump.fun trades come in
    bursts during this phase, and our default `last_trade_age > 25s` stale
    hide would cause them to flicker. Returning 'near' lets _enrich_mint
    skip the stale rule for these mints, with a 60-second tolerance."""
    cur = m.get("current_vsol_sol") or 0
    last_age = m.get("last_trade_age_s") or 0
    if cur >= GRAD_VSOL:
        return None  # graduated — let normal stale rules hide it
    if cur >= NEAR_GRAD_VSOL and last_age <= 60:
        return "near"
    return None


def _combine_probs(grad_prob, early_grad: Optional[dict], age_s) -> dict:
    """Age-weighted blend of curve-shape and at-launch graduation predictors.
    Each predictor is most accurate when its features are most relevant —
    early features matter most at launch, curve-shape matters most as the
    curve develops. We weight them with α = 1 / (1 + age/300):
      age 0s   → 100% early
      age 60s  →  83% early
      age 5min →  50/50
      age 30min → 14% early
    Falls back to grad_prob alone when the early predictor is warming."""
    g = float(grad_prob or 0.0)
    egp = early_grad or {}
    if egp.get("status") != "live" or egp.get("prob") is None:
        return {
            "prob":            round(g, 4),
            "weight_early":    0.0,
            "weight_curve":    1.0,
            "early_warming":   True,
            "early_prob":      None,
            "curve_prob":      round(g, 4),
        }
    e = float(egp["prob"])
    a = max(0.0, float(age_s or 0.0))
    alpha = 1.0 / (1.0 + a / 300.0)
    combined = alpha * e + (1.0 - alpha) * g
    return {
        "prob":            round(combined, 4),
        "weight_early":    round(alpha, 3),
        "weight_curve":    round(1.0 - alpha, 3),
        "early_warming":   False,
        "early_prob":      round(e, 4),
        "curve_prob":      round(g, 4),
    }


def _enrich_mint(m: dict, rug_features_prefetched: Optional[dict] = None) -> tuple[dict, Optional[str]]:
    """Compute every derived field (bot signals, buyer quality, activity health,
    grad probability) for one snapshot mint. Returns (enriched, hide_reason).
    `hide_reason` is None when the mint should be shown on /api/live; otherwise
    a short reason code. Callers that bypass the dashboard filter (e.g. probe)
    can use the enriched dict regardless."""
    last_trade_age = m.get("last_trade_age_s", 0)
    hide_reason: Optional[str] = None

    # Mayhem gate. is_mayhem_mode == None means the observer's RPC lookup hasn't
    # resolved yet — exclude those from the dashboard until classification lands.
    if m.get("is_mayhem_mode") is not False:
        hide_reason = "mayhem_or_unresolved"

    # Manufactured-chart filter — drops obvious wash/bot pumps. Each individual
    # signal can be benign in isolation, so we only suppress when the mint trips
    # multiple thresholds simultaneously. The same fields stay on the response
    # (under "bot_*") so power users can audit why something was hidden.
    top1   = m.get("top_buyer_pct", 0.0) or 0.0
    top3   = m.get("top3_buyer_pct", 0.0) or 0.0
    repeat = m.get("repeat_buyer_rate", 0.0) or 0.0
    dust   = m.get("dust_buy_rate", 0.0) or 0.0

    # Sybil signal: cross-reference top buyers against the 150k-wallet history
    # index. Organic mints have ~50-70% known buyers (active pumpfun traders).
    # Sybil farms cluster around 0% known — every buyer is a wallet we've never
    # seen do a single trade on any of our 50k+ historical curves.
    top_buyers = m.get("top_buyers", []) or []
    unknown_pct = 0.0
    low_history_pct = 0.0
    sniper_pct = 0.0
    avg_smart = 0.0
    if top_buyers and WINTEL is not None:
        wallets = WINTEL._wallets
        unknown = sum(1 for w in top_buyers if w not in wallets)
        unknown_pct = unknown / len(top_buyers)

        known_recs = [wallets[w] for w in top_buyers if w in wallets]
        if known_recs:
            low_hist = sum(1 for r in known_recs if r.get("total", 0) <= 2)
            snipers  = sum(1 for r in known_recs if r.get("fast_rate", 0) >= 0.70)
            low_history_pct = low_hist / len(known_recs)
            sniper_pct      = snipers  / len(known_recs)
            avg_smart = sum(r.get("smart_score", 0) for r in known_recs) / len(known_recs)

    # Live smart-money signal: count how many of the current top buyers
    # appear in our smart-money leaderboard with a meaningful track record.
    # Uses the same threshold as the public smart leaderboard (min_total=8,
    # smart_score≥0.30) so what shows up on the badge is what shows up on
    # the leaderboard. This is real-time alpha: when our scoring wallets
    # are *currently in* a mint, that's a signal competitors can't replicate
    # without our own multi-week wallet index.
    n_smart_in = 0
    n_elite_in = 0   # Day 4.82 — wallets with smart_score >= 0.70.
                     # Backtest (n=1444 resolved): n_elite >= 3 cohort
                     # graduates at 29.6% vs n_elite == 0 at 1.5%. This
                     # is the new ★ ALPHA gate (replaces smart_money_in 3-9).
    n_fresh_buyers = 0   # Day 4.87 — top buyers with <5 total trades
                         # or completely unknown to wallet_intel. High
                         # count = coordinated sybil dump risk.
    smart_in_examples: list[str] = []
    if top_buyers and WINTEL is not None:
        for w in top_buyers:
            r = WINTEL._wallets.get(w)
            if not r:
                n_fresh_buyers += 1   # unknown = fresh
                continue
            total = r.get("total", 0) or 0
            score = r.get("smart_score", 0) or 0
            if total < 5:
                n_fresh_buyers += 1
            if total >= 8 and score >= 0.30:
                n_smart_in += 1
                if len(smart_in_examples) < 3:
                    smart_in_examples.append(w)
            if total >= 8 and score >= 0.70:
                n_elite_in += 1

    bot_flags = []
    if last_trade_age >= 15:
        bot_flags.append("stale_activity")

    # Activity-pattern signals — these catch coordinated wallet networks that
    # evade the buyer-quality filters by using wallets with prior history.
    # Empirical: organic pumpfun mints land at 30-40% sell ratio (people take
    # profit) and ~1.0-1.5 buys per buyer. Manufactured charts cluster at
    # <5% sells and 2.5+ buys per wallet.
    n_buys_total = m["n_buys"]
    n_sells_total = m["n_trades"] - m["n_buys"]
    sell_ratio = n_sells_total / max(m["n_trades"], 1)
    buys_per_buyer = n_buys_total / max(m["unique_buyers"], 1)
    if m["age_s"] > 60 and n_buys_total >= 50 and sell_ratio < 0.05:
        bot_flags.append("no_sell_pressure")
    if m["age_s"] > 60 and n_buys_total >= 50 and buys_per_buyer >= 2.5:
        bot_flags.append("wallet_cycling")

    if top1   >= 0.40: bot_flags.append("top_buyer_concentration")
    if top3   >= 0.65: bot_flags.append("top3_concentration")
    if repeat >= 0.30: bot_flags.append("consecutive_self_buys")
    if dust   >= 0.20 and unknown_pct >= 0.20:
        bot_flags.append("dust_trades")
    if len(top_buyers) >= 5 and unknown_pct >= 0.60:
        bot_flags.append("sybil_buyers")
    if len(top_buyers) >= 5 and top1 < 0.10 and unknown_pct >= 0.40:
        bot_flags.append("diffuse_sybil")
    if len(top_buyers) >= 5 and (len(top_buyers) - int(unknown_pct * len(top_buyers))) >= 5:
        if low_history_pct >= 0.40:
            bot_flags.append("fresh_wallet_farm")
        if sniper_pct >= 0.40:
            bot_flags.append("sniper_dominated")
        if avg_smart <= -0.25:
            bot_flags.append("bad_history_buyers")

    # Sybil/quality signals alone are enough to hide — all variants are
    # unambiguous patterns. For other signals, ≥2 flags hides; 1 flag tags
    # as suspect but stays visible.
    is_suspect = len(bot_flags) >= 1
    hard_hit = any(f in HARD_HIT_FLAGS for f in bot_flags)

    # Compute graduation_state BEFORE applying hide rules — graduated and
    # stalled mints stay visible regardless of trade activity, because their
    # post-graduation silence is the *expected* state and hiding them would
    # erase the receipts of a successful prediction.
    grad_state = _graduation_state(m)

    # Establish hide_reason precedence: mayhem > stale > bot signal.
    # Stale-hide is skipped when grad_state is set (graduated/near-grad
    # mints have legitimate reasons to be quiet) AND when the mint is
    # still in our prediction window (age ≤90s) — fresh launches often
    # have a brief flurry then a quiet stretch; we want to show our
    # prediction even if they paused.
    mint_age_s = m.get("age_s") or 0
    in_prediction_window = mint_age_s <= 90
    if (hide_reason is None and last_trade_age > 25
        and grad_state is None
        and not in_prediction_window):
        hide_reason = "stale_activity_hard"
    # Hard age cap. Past this the mint is out of every product window
    # (lane is ≤60s, grace 90s; post-grad surfaces gate via grad_state).
    # Frees the score path from running rug_predictor / k-NN / RPC
    # enrichment on dead mints. Graduated mints exempt — receipts stay
    # visible while they bank.
    DASHBOARD_HARD_AGE_CAP_S = 600  # 10 min
    if (hide_reason is None
            and mint_age_s > DASHBOARD_HARD_AGE_CAP_S
            and grad_state is None):
        hide_reason = "past_age_cap"
    if hide_reason is None and hard_hit:
        hide_reason = "hard_bot_signal"
    if hide_reason is None and len(bot_flags) >= 2:
        hide_reason = "multi_flag_bot"

    # ── HARD EARLY-EXIT for hidden mints ────────────────────────────────
    # Once a mint is marked for hiding, _score_mints filters it out from
    # the API response. Doing rug_predictor / early_grad / creator-history /
    # smart-money / RPC lookups for it from this point forward is pure waste.
    # Bail with a minimal m_out — this is THE hot-path optimization that
    # keeps the score precompute fast as the mint pool grows.
    if hide_reason is not None:
        return dict(m), hide_reason

    # ── PERF GATE ─────────────────────────────────────────────────────────
    # Skip score_full when:
    #  (a) age > 90s — out of lane, predictions get stripped anyway
    #  (b) age < 15s — too fresh, model has no signal (k-NN bucket starts
    #      at 30s, mints at age=5s would just return raw=0 / saturated low)
    # Both cases: skip k-NN + log_prediction + receipt lookups. Same output,
    # less wasted work. Pure win — no prediction sacrificed.
    age_s_for_gate = m.get("age_s") or 0
    in_prediction_window_for_score = 15 <= age_s_for_gate <= 90
    if not in_prediction_window_for_score:
        # Skip the heavy path entirely.
        score = {}
    else:
        feats = {
            "current_vsol": m["current_vsol_sol"],
            "vsol_growth": m["vsol_growth_sol"],
            "n_trades": m["n_trades"],
            "unique_buyers": m["unique_buyers"],
            "top_buyer_pct": top1 if top1 > 0 else 0.15,  # 0 means no buys yet
            "current_mult": m["current_mult"],
        }
        _t = time.time()
        score = INDEX.score_full(feats, m["age_s"])
        status_module.record_stage_timing("score_full", time.time() - _t)
    # ── Manufactured-pump flag ────────────────────────────────────────────
    # From the 2026-04-29 audit: 2× runners (manufactured pump-and-dumps)
    # had heavier early concentration than true 10× organic runners.
    # Heuristic: ≥4 SOL spent in the first 2 seconds AND mult is already
    # showing dump pressure (current_mult < 0.7× or top1 ≥ 0.40 = single-
    # wallet domination). This is a soft flag — surfaced on output but
    # doesn't auto-hide; lets traders decide.
    sol_2s = m.get("sol_spent_first_2s") or 0.0
    sol_5s = m.get("sol_spent_first_5s") or 0.0
    manufactured_pump = (
        sol_2s >= 4.0 and (top1 >= 0.40 or m.get("current_mult", 1.0) > 2.5)
    )
    if manufactured_pump:
        bot_flags.append("manufactured_pump")
        # Re-evaluate suspect / hard_hit since we just appended a flag.
        is_suspect = True
        # manufactured_pump is informational, not a hard hit — don't auto-hide
        # the mint; the user can decide whether to trust the signal.

    # Organic-followon signal (added 2026-05-12 per user direction).
    # Quantifies whether the first-5s buying continued past the first 2s
    # (organic interest) OR was concentrated in the opening 2s with no
    # follow-on (coordinated launch + dump risk).
    #
    #   organic_followon_sol   = SOL spent in seconds 2-5 of the mint's life
    #   organic_followon_ratio = sol_2s / sol_5s
    #     ratio close to 1.0 → 100% of first-5s buying happened in first 2s
    #                          (no organic follow-on; classic bundle pattern)
    #     ratio close to 0    → buying spread across 0-5s (organic interest)
    #
    # Flag fires at ratio > 0.95 (i.e., <5% of first-5s buying happened
    # after the opening burst). Threshold pre-registered for Audit 14
    # retroactive validation (see follow-up commit).
    if sol_5s > 0:
        organic_followon_sol = max(0.0, sol_5s - sol_2s)
        organic_followon_ratio = min(1.0, sol_2s / sol_5s)
    else:
        # No first-5s buying observed → ratio undefined. Use 0 (no signal
        # rather than false-positive flag) and surface as 0/0 in API.
        organic_followon_sol = 0.0
        organic_followon_ratio = 0.0
    if sol_5s > 0 and organic_followon_ratio > 0.95:
        bot_flags.append("no_organic_follow_on")
        # Soft flag — informational, like manufactured_pump. Does not auto-
        # hide the mint or auto-mark suspect (suspect derives from hard-hit
        # flags only). The flag layered over composite signal lets users
        # filter out bundle-pattern mints in the dashboard.

    # Forward-prediction logging — INSERT OR IGNORE on (mint, age_bucket)
    # so every distinct prediction is recorded once. Persists every tier
    # probability + the heuristic flags (manufactured_pump, dex_paid,
    # fee_delegated) alongside, so once the curve resolves we can score
    # each heuristic against actual outcomes ("of mints flagged X at
    # age N, Y% actually rugged / graduated / ran 5×"). Cheap.
    score["manufactured_pump"] = manufactured_pump
    # Pull the cached enrichment results directly from the modules —
    # they're already populated by background daemons by the time we
    # hit this point. None on cold start (treated as 0 by log_prediction).
    _dx = dex_paid.enrich(m["mint"]) or {}
    _fd = fee_delegation.enrich(m["mint"]) or {}
    score["dex_paid"]        = bool(_dx.get("is_paid"))
    score["fee_delegated"]   = bool((_fd.get("total_bps") or 0) > 0)
    # Bundle detection comes from the Rust observer, surfaced on the raw
    # snapshot mint dict. detected = ≥4 wallets in the tightest 500ms.
    score["bundle_detected"] = (m.get("bundle_size_max_500ms") or 0) >= 4
    # Forward-prediction logging is DEFERRED until after post_grad_survival_prob
    # is computed (~line 736), so the prediction record + the merkle leaf
    # include the full set of forward claims we make about this mint, not
    # just the curve-shape probabilities. See "log_prediction call site" below.

    # ── Creator-history attachment ────────────────────────────────────────
    # Lookup the chronologically-first buyer's track record. ~10–15% of live
    # mints will have a hit in the index; the rest were one-off launchers.
    # When we DO have history, the lift on 5×+ outcomes is 4.6× over baseline.
    creator_stats = CREATORS.lookup(m.get("first_buyer")) if CREATORS else None

    m_out = dict(m)
    # `grad_prob` is the calibrated number (empirically corrected via the
    # forward-prediction feedback loop). `grad_prob_raw` is the unfiltered
    # k-NN output, exposed for transparency / debugging.
    #
    # STICKY GRAD_PROB: once we score a mint at age_bucket = 30 or 60, we
    # LOCK that prediction value for display. Subsequent snapshots can
    # re-score (the model continuously updates), but the dashboard / TG
    # alerts show the AT-PREDICTION value so users aren't confused by a
    # number that drifts after the alert fires. If the next bucket (60)
    # produces a higher value, that one wins (refinement). After lane
    # closes the locked value sticks until the mint exits the snapshot.
    live_grad = score.get("grad_prob")
    age_bucket_now = score.get("age_bucket")
    mint_id = m.get("mint")
    if (mint_id and live_grad is not None
            and age_bucket_now in (30, 60)):
        with _LOCKED_GRAD_LOCK:
            existing = _LOCKED_GRAD.get(mint_id)
            if existing is None or age_bucket_now > existing[1]:
                _LOCKED_GRAD[mint_id] = (live_grad, age_bucket_now)
            # Bound cache size — drop oldest if over 5000 mints
            if len(_LOCKED_GRAD) > 5000:
                # Cheap eviction: just clear half. We don't need
                # perfect LRU since stale entries are harmless (just
                # never read again).
                for k in list(_LOCKED_GRAD.keys())[:2500]:
                    _LOCKED_GRAD.pop(k, None)
    locked = _LOCKED_GRAD.get(mint_id) if mint_id else None
    if locked is not None:
        # Display the locked value, not the live recomputation. Tag the
        # bucket so consumers can see which prediction we're showing.
        m_out["grad_prob"] = locked[0]
        m_out["grad_prob_locked_age_bucket"] = locked[1]
    else:
        m_out["grad_prob"] = live_grad
    m_out["grad_prob_raw"] = score.get("grad_prob_raw")
    # `*_saturation` flags surface when a probability is at the model's ceiling
    # (raw=1.0 → "high") or floor (raw=0.0 → "low"). Many different mints can
    # share the same calibrated value when their raw saturates — bots should
    # treat unsaturated high-prob signals as the higher-confidence pick.
    m_out["grad_prob_saturation"] = score.get("grad_prob_saturation")
    # base_rate = fraction of all indexed curves at this age that graduated.
    # lift_x   = grad_prob / base_rate — how much above random this signal is.
    # A 30% call at a 5% base rate is 6× lift; same 30% at 25% base rate is
    # 1.2× lift (barely above noise). Lift is what makes the headline number
    # legible to a trader.
    _gp_br = score.get("grad_prob_base_rate")
    m_out["grad_prob_base_rate"] = _gp_br
    if (m_out["grad_prob"] is not None and _gp_br is not None and _gp_br > 0):
        m_out["grad_prob_lift_x"] = round(m_out["grad_prob"] / _gp_br, 2)
    else:
        m_out["grad_prob_lift_x"] = None
    # Inline calibration receipt — the historical accuracy at this exact
    # (age_bucket × threshold_band) cell. Lets the trader see "of 44 mints
    # we said ≥90% to graduate at age 60s, 91% actually did" without leaving
    # the prediction. None if we don't have enough resolved samples yet for
    # this combination.
    # Lazy receipt: only compute if the prediction is meaningful (≥30%).
    # Below that the receipt would say "low confidence band, low historical
    # rate" which adds no value to the trader. Skip the cache lookup entirely
    # to keep the hot path fast under high mint counts.
    _gp = m_out.get("grad_prob")
    if _gp is not None and _gp >= 0.30:
        m_out["grad_prob_calibration"] = predictions.get_calibration_receipt(
            _gp, m_out.get("age_bucket"),
        )
    else:
        m_out["grad_prob_calibration"] = None
    m_out["grad_neighbors"] = score.get("n_neighbors")
    m_out["grad_n_graduated"] = score.get("n_graduated")
    m_out["age_bucket"] = score.get("age_bucket")
    m_out["calibrated"] = score.get("calibrated", False)
    # Multi-tier runner probabilities. Two semantics per tier:
    #   *_from_launch (the bare `runner_prob_Nx` keys) — P(neighbor peaked at
    #     ≥N× ITS launch price). Useful for ranking "is this a runner overall?"
    #   *_from_now — P(neighbor peaked at ≥N× ITS price AT THIS AGE). This
    #     is the trader-relevant number — "if I buy at the current price, does
    #     it N× from here?" — and what the dashboard treats as headline.
    for tier_key in ("runner_prob_2x", "runner_prob_3x", "runner_prob_5x",
                     "runner_prob_10x", "runner_prob_20x"):
        m_out[tier_key]              = score.get(tier_key)             # calibrated
        m_out[tier_key + "_raw"]     = score.get(tier_key + "_raw")    # uncalibrated reference
        m_out[tier_key + "_from_now"] = score.get(tier_key + "_from_now")
        m_out[tier_key + "_saturation"]          = score.get(tier_key + "_saturation")
        m_out[tier_key + "_from_now_saturation"] = score.get(tier_key + "_from_now_saturation")
        # Per-tier base_rate + lift — same framing as grad_prob_lift_x. The
        # base_rate is the fraction of all corpus mints in this age bucket
        # that hit Nx (from_launch or from_now). A 30% prob at a 3% base
        # rate is 10× lift = strong signal; 30% at 25% base rate is barely
        # above random.
        m_out[tier_key + "_base_rate"]            = score.get(tier_key + "_base_rate")
        m_out[tier_key + "_from_now_base_rate"]   = score.get(tier_key + "_from_now_base_rate")
        m_out[tier_key + "_lift_x"]               = score.get(tier_key + "_lift_x")
        m_out[tier_key + "_from_now_lift_x"]      = score.get(tier_key + "_from_now_lift_x")
    # x_factor: the headline upside signal — peer to grad_prob. Highest
    # from-now lift across the surfaced trader tiers (2× / 5× / 10×). A coin
    # may have low grad_prob but still be a real trade if its x_factor lift
    # is high — that's a "neighbors with these features tended to pump from
    # right here" signal, even if they didn't graduate. Closes the gap where
    # we silently penalized rug-pump mints traders make money on.
    m_out["x_factor"] = score.get("x_factor")
    # Inline calibration receipts for the surfaced runner tiers (2x/5x/10x).
    # Lazy: only computed when prob is meaningful (≥30%). Same caveat about
    # observer-derived labels (Phase C / Tier 3 deferred).
    for tier_label in ("2x", "5x", "10x"):
        prob = m_out.get(f"runner_prob_{tier_label}_from_now")
        if prob is not None and prob >= 0.30:
            m_out[f"runner_prob_{tier_label}_from_now_calibration"] = (
                predictions.get_runner_calibration_receipt(
                    prob, m_out.get("age_bucket"), tier_label,
                )
            )
        else:
            m_out[f"runner_prob_{tier_label}_from_now_calibration"] = None
    m_out["expected_peak_mult"]      = score.get("expected_peak_mult")        # peak / launch
    m_out["median_peak_mult"]        = score.get("median_peak_mult")
    m_out["expected_upside_from_now"]= score.get("expected_upside_from_now")  # peak / current
    m_out["median_upside_from_now"]  = score.get("median_upside_from_now")
    m_out["runner_n_valid_neighbors"] = score.get("runner_n_valid_neighbors")
    # Creator history block (None if first buyer is unknown / unseen).
    m_out["creator_history"] = creator_stats
    # Manufactured-pump flag — informational, surfaced as its own field too.
    m_out["manufactured_pump"] = manufactured_pump
    # Graduation lifecycle state: graduated | stalled | near | null.
    # Drives the dashboard badge so users can see the receipts of a payoff.
    m_out["graduation_state"] = grad_state
    # Curve position — observed mechanical state (NOT a prediction).
    # Pairs with grad_prob (model output) for the "where is it / where
    # might it go" framing. Schema: {pct: 0-100 | null, status: live |
    # near | graduated | unknown}. See _graduation_progress() for formula.
    m_out["graduation_progress_pct"] = _graduation_progress(m)
    # Live smart-money cross-reference. n_smart_in is the count of
    # leaderboard wallets currently among the mint's top buyers.
    # smart_money_examples is REDACTED per project_wallet_index_is_the_moat.md
    # (2026-05-10) and Audit 09 verdict (commit 34ce847, 7.37x graduation
    # rate lift at sm>=7 stratum, CIs strongly non-overlapping). The wallet
    # reputation index is the load-bearing moat asset; specific wallet
    # addresses are proprietary index data. Field name preserved for
    # backward compatibility; values redacted to []. Aggregate count
    # (smart_money_in) remains public — that's the signal-layer the
    # receipts trail depends on; the addresses behind it are the moat.
    m_out["smart_money_in"]       = n_smart_in
    m_out["n_elite_in"]           = n_elite_in
    m_out["n_fresh_buyers"]       = n_fresh_buyers
    m_out["n_top_buyers"]         = len(top_buyers)
    m_out["smart_money_examples"] = []
    m_out["is_suspect"] = is_suspect
    m_out["bot_flags"] = bot_flags
    # Organic-followon signal (added 2026-05-12). Two computed fields
    # surface alongside bot_flags. See _enrich_mint where these are
    # derived from sol_spent_first_2s / sol_spent_first_5s.
    m_out["organic_followon_sol"]   = organic_followon_sol
    m_out["organic_followon_ratio"] = organic_followon_ratio
    m_out["unknown_buyer_pct"] = unknown_pct
    m_out["low_history_pct"] = low_history_pct
    m_out["sniper_pct"] = sniper_pct
    m_out["avg_top_buyer_smart"] = avg_smart
    m_out["sell_ratio"] = sell_ratio
    m_out["buys_per_buyer"] = buys_per_buyer
    # Velocity / acceleration — pulled directly from snapshot (computed in
    # the Rust observer over 30s/60s windows). High velocity + positive
    # acceleration = mint speeding up toward graduation. Negative
    # acceleration = cooling off.
    m_out["vsol_velocity_30s"] = m.get("vsol_velocity_30s")
    m_out["vsol_velocity_60s"] = m.get("vsol_velocity_60s")
    m_out["vsol_acceleration"] = m.get("vsol_acceleration")
    # Cluster signal — are 2+ smart-money wallets in this mint who have
    # historically moved together? n_clustered_pairs > 0 = confirmed pile-in
    # by a known group. cluster_density = strength of the signal (0-1).
    # clustered_wallets (specific wallet addresses) REDACTED per
    # project_wallet_index_is_the_moat.md + Audit 09 (commit 34ce847).
    # Same backward-compat shape as smart_money_examples redaction: field
    # name + aggregate metrics (n_clustered_pairs, cluster_density,
    # max_pair_count) preserved; the wallet-address list cleared to [].
    _cluster = cluster_intel.cluster_signal(top_buyers or [])
    if isinstance(_cluster, dict):
        _cluster["clustered_wallets"] = []
    m_out["cluster"] = _cluster
    # Wallet-balance enrichment: are top buyers actual whales or fresh
    # spam wallets? n_whale_wallets, avg_buyer_sol, max_buyer_sol.
    m_out["wallet_balance"] = wallet_balance.enrich_top_buyers(top_buyers or [])
    # Fee-delegation block — pump.fun's on-chain creator-fee splitter.
    # Returns None on cold start until the daemon caches the lookup;
    # otherwise contains delegate count, total BPS, primary delegate.
    m_out["fee_delegation"] = fee_delegation.enrich(m["mint"])
    # DexScreener paid-info status — soft signal of creator seriousness.
    # None until the daemon caches the lookup; otherwise contains is_paid
    # bool plus website / socials counts.
    m_out["dex_paid"] = dex_paid.enrich(m["mint"])
    # Token name + symbol + image — None until metadata daemon caches it.
    m_out["metadata"] = metadata.enrich(m["mint"])
    # Narrative watcher (Path A pilot, internal-only). Parallel signal —
    # does NOT feed composite/ACT/WATCH. Stratified against outcomes via
    # mint_checkpoints after 48-72h per the pre-registered decision rule.
    # MUST run AFTER m_out["metadata"] is populated above — it reads
    # name/symbol from it. (Bugfix 2026-05-16: was called one line early,
    # before metadata existed → enrich() saw empty input → riding_trend=0
    # for every mint since launch. Trending set / daemon were always fine.)
    m_out["narrative"] = narrative_enrichment.enrich(m_out.get("metadata"))
    # Bundle-launch detection — Axiom-style "Bundlers: X%" stat. Computed
    # by the Rust observer; we just relabel for the API surface. The
    # `detected` flag fires when the tightest 500ms window had ≥4 distinct
    # buyers (a Jito bundle by definition; humans can't coordinate that
    # tight). `pct` is the % of circulating supply those bundle wallets
    # still hold — moves over time as bundlers buy more, sell, or hold.
    bundle_size = m.get("bundle_size_max_500ms") or 0
    m_out["bundle"] = {
        "detected":   bundle_size >= 4,
        "size":       bundle_size,
        "pct":        round(m.get("bundle_pct") or 0.0, 1),
        "at_t_s":     m.get("bundle_t_s"),
    }
    # Post-graduation survival predictor — k-NN over historical resolved
    # outcomes. Returns None until ~20 resolved samples with features have
    # accumulated; before then, the dashboard hides the field. The feature
    # vector is pulled from the *enriched* mint (so smart_money_in,
    # wallet_balance.n_whale_wallets etc. are populated for prediction).
    # Wire field name is post_grad_survival_prob (matches /api/scope schema doc
    # at line ~1279 and the ledger leaf field). External consumers index on
    # this name. Internal display layers may pretty-print as "sustains".
    #
    # INVARIANT: post_grad_survival_prob and grad_prob MUST be computed from
    # the same _enrich_mint snapshot. Gate validation (web/gate_validation.py,
    # criterion in BACKLOG.md) assumes synchronous features — both numbers
    # describe the SAME mint state at the SAME instant, otherwise the
    # stratification compares apples to oranges. predict_survival is currently
    # synchronous (k-NN over an in-memory index), preserving this invariant.
    # Don't async-ify it, swap in an HTTP/RPC call, or move it outside this
    # function without re-validating the gate logic.
    m_out["post_grad_survival_prob"] = post_grad_tracker.predict_survival(m_out)

    # ── log_prediction call site ──────────────────────────────────────────
    # Deferred from earlier in the function so post_grad_survival_prob is
    # included in the persisted record (and in the merkle leaf hash). The
    # leaf locks in EVERY forward claim we surfaced — graduation prob,
    # runner-from-now probs, AND post-bond sustain prob — so a future
    # change to any tracker can't quietly re-score historic predictions.
    if in_prediction_window_for_score:
        # Stash the post-grad sustain prob into the score dict that
        # log_prediction reads. None when warming or absent — handled by
        # log_prediction.
        _pgs = m_out.get("post_grad_survival_prob") or {}
        score["post_grad_survival_prob"] = _pgs.get("prob") if _pgs.get("status") == "live" else None
        # ── Retrain v1 dual-write shadow scoring ─────────────────────────
        # Compute the GBM probability in parallel with the deployed k-NN.
        # k-NN remains source of truth for `predicted_prob`; this just logs
        # the shadow value to grad_prob_gbm_shadow for 24-48h validation.
        # Disabled by default via GBM_SHADOW_ENABLED env var. Never raises.
        # Pre-registered dual-write criteria: BACKLOG.md "Retrain v1 dual-write window".
        _t = time.time()
        _gbm_out = gbm_shadow.score_one(score, m_out)
        if _gbm_out is not None:
            score["grad_prob_gbm_shadow"]               = _gbm_out["grad_prob_gbm"]
            score["gbm_shadow_features_complete"]       = 1 if _gbm_out["features_complete"] else 0
            # Calibrated value may be None when isotonic is unavailable —
            # raw still gets logged. Defensive containment per Gate 5 cascade.
            _cal_prob = _gbm_out.get("grad_prob_gbm_calibrated")
            score["grad_prob_gbm_calibrated_shadow"]    = _cal_prob
            # Bucket label (HIGH/MED/LOW) — defensive: returns "LOW" if cutoffs
            # unavailable. Bimodal-aware logic per the 2026-05-06 spec
            # revision: bucket_for() takes both calibrated AND raw GBM so
            # MED can be gated on raw_GBM percentile inside the at-ceiling
            # cluster. See docs/research/bucket_cutoffs_bimodal_finding.md.
            # Internal write only during the calibrated-shadow window;
            # m_out propagation + V3 leaf semantic flip happen at the
            # Track B cutover deploy.
            # Pass m_out so the input-quality gate (Fix 2, sixth-finding
            # pre-registered 2026-05-07) can short-circuit to LOW on
            # degenerate inputs before the GBM/isotonic-derived bucket
            # assignment runs. Pre-fix, 1-buyer fresh mints were reaching
            # MED bucket because their raw GBM scored in the top-3% of the
            # ceiling cluster — but the underlying inputs gave the model
            # no signal to act on. The gate is a precondition check.
            score["grad_prob_bucket"]                   = bucket_cutoffs.bucket_for(
                _cal_prob, _gbm_out["grad_prob_gbm"], m_out=m_out
            )
            # Cutover (2026-05-06): expose calibrated value + bucket via
            # /api/live so alert templates and dashboard read them. Replaces
            # the legacy bare-percentage framing for grad_prob.
            m_out["grad_prob_bucket"]         = score["grad_prob_bucket"]
            m_out["grad_prob_gbm_calibrated"] = _cal_prob
            status_module.record_stage_timing("gbm_shadow", time.time() - _t)
        predictions.log_prediction(
            m["mint"], score.get("age_bucket"),
            score.get("grad_prob"), score=score,
        )
    # Early-stage graduation predictor — k-NN over at-launch features
    # ONLY. Useful as alpha when the main grad_prob (curve-shape-driven)
    # hasn't moved yet. Returns "warming" until ≥30 resolved outcomes.
    #
    # 2026-06-19 LATENCY FIX: see rug_predictor note above. early_grad
    # was eating ~681s cumulative per tick (~43s wall-clock across 16
    # workers). Like rug_prob, it's informational only — not used by
    # any TG tier decision or composite cross. Disabled in fast-path
    # mode; re-enable by setting EARLY_GRAD_FAST_PATH=0 in env.
    if os.environ.get("EARLY_GRAD_FAST_PATH", "1") != "1":
        _t = time.time()
        m_out["early_grad_prob"] = early_grad_tracker.predict(m_out)
        status_module.record_stage_timing("early_grad", time.time() - _t)
    else:
        m_out["early_grad_prob"] = None

    # ── lane6_features namespace ─────────────────────────────────────────
    # Convenience dict packaging the 17 features Lane 9 validated (closes
    # +14pp non-bundled AUC over the current k-NN's 6-feature vector).
    # Each value is sourced from existing m_out fields — no new computation.
    # Tomorrow's retrain pipeline reads `m_out["lane6_features"]` instead of
    # cherry-picking from 118 m_out keys. Additive only: this dict is a
    # VIEW of values that already exist on m_out under their primary names.
    # Don't read this dict from existing live consumers (alert templates,
    # API surfaces) — those should keep using the primary field names.
    # Source map: see docs/research/lane6_unused_features.md.
    m_out["lane6_features"] = {
        # Curve-shape features (top of Lane 9's importance ranking)
        "max_mult":             m_out.get("max_mult"),
        "vsol_velocity_30s":    m_out.get("vsol_velocity_30s"),
        "vsol_velocity_60s":    m_out.get("vsol_velocity_60s"),
        "vsol_acceleration":    m_out.get("vsol_acceleration"),
        # Concentration / bot-pattern features
        "top3_buyer_pct":       m_out.get("top3_buyer_pct"),
        "repeat_buyer_rate":    m_out.get("repeat_buyer_rate"),
        "dust_buy_rate":        m_out.get("dust_buy_rate"),
        "buys_per_buyer":       m_out.get("buys_per_buyer"),
        # Early-load features
        "sol_spent_first_2s":   m_out.get("sol_spent_first_2s"),
        "sol_spent_first_5s":   m_out.get("sol_spent_first_5s"),
        # Bundle features
        "bundle_pct":           m_out.get("bundle_pct"),
        "bundle_size_max_500ms": m_out.get("bundle_size_max_500ms"),
        # Trader-quality / activity features
        "sell_ratio":           m_out.get("sell_ratio"),
        "smart_money_in":       m_out.get("smart_money_in"),
        "unknown_buyer_pct":    m_out.get("unknown_buyer_pct"),
        "low_history_pct":      m_out.get("low_history_pct"),
    }
    # Devil-candle predictor — k-NN over the mint_checkpoints corpus
    # (60s-checkpoint feature vectors labeled by actual_rugged). The
    # probability that a single trade dropped this mint's price ≥40%
    # within its first 5 minutes. Returns:
    #   {prob, n_neighbors, n_total_resolved, status}
    # status ∈ {"warming", "too_young", "live"}. Bots should gate on
    # status=="live" before treating prob as actionable.
    # rug_predictor opens a sqlite connection + runs Python-loop k-NN over
    # ~1700 training rows — non-trivial cost. The result is lane-stripped
    # past 90s anyway, so skip the work outside the lane window. Same
    # behaviour, less CPU.
    # 2026-06-19 LATENCY FIX: rug_predictor was eating ~70s of every
    # 127s precompute tick (cumulative ~1093s across 16 workers, ~18s
    # CPU time per call — anomalously slow for a numpy k-NN). That delay
    # was cascading into model-scoring lag (mints scored at age 145s
    # instead of 60s) which then drove TG push latency to ~3 min.
    #
    # rug_prob is informational, not used in any TG tier decision or
    # composite cross detection — so skipping it here drops tick wall-
    # clock from 127s → ~5s without affecting any user-facing signal.
    # The field surfaces on API responses as None during the live
    # observer window; a slower background enrichment fills it in
    # once mints age out, before they're consumed by accuracy/audit.
    # Re-enable by setting RUG_PREDICTOR_FAST_PATH=0 in env.
    if (in_prediction_window_for_score
            and os.environ.get("RUG_PREDICTOR_FAST_PATH", "1") != "1"):
        _t = time.time()
        m_out["rug_prob"] = rug_predictor.predict_for_mint(
            m_out.get("mint"), prefetched_features=rug_features_prefetched
        )
        status_module.record_stage_timing("rug_predictor", time.time() - _t)
    else:
        m_out["rug_prob"] = None
    # Hand-coded red-flag detector — transparent, non-learned counterpart
    # to rug_prob. Fires immediately (no warmup), every triggered flag is
    # named so consumers can audit. The TG alerter uses severity="high"
    # as a hard suppression rule; API consumers can set their own threshold.
    _t = time.time()
    m_out["rug_heuristic"] = rug_heuristic.compute(m_out)
    status_module.record_stage_timing("rug_heuristic", time.time() - _t)
    # Combined headline — age-weighted blend of curve-shape and
    # at-launch predictors. The user-facing "ultimate" probability
    # users react to. Falls back to grad_prob alone when the early
    # predictor is still warming.
    m_out["combined_prob"] = _combine_probs(
        m_out.get("grad_prob"),
        m_out.get("early_grad_prob"),
        m_out.get("age_s"),
    )
    # Market cap — computed from the pump.fun constant-product bonding
    # curve (k ≈ 30 SOL × 1.073B tokens). USD value uses the cached
    # Jupiter SOL/USD price; if it's cold we ship `usd: null`.
    vsol = m_out.get("current_vsol_sol") or 0
    if vsol > 0:
        mc_sol = (vsol * vsol) / 32.19
        try:
            import jupiter_price
            sol_usd = jupiter_price.get_sol_usd()
        except Exception:
            sol_usd = None
        m_out["market_cap"] = {
            "sol": round(mc_sol, 1),
            "usd": round(mc_sol * sol_usd) if sol_usd else None,
            # SOL/USD at the moment we computed this MC. Lets renderers
            # annotate the USD figure with "(SOL $X)" so users know the
            # USD floats with SOL price.
            "sol_usd": round(sol_usd, 2) if sol_usd else None,
        }
    else:
        m_out["market_cap"] = None

    # ── LANE GATE ──────────────────────────────────────────────────────────
    # The product predicts at age 30s and 60s only. Past 90s (60s + grace),
    # we strip every prediction-shaped field from the response so the lane
    # is enforced AT THE SOURCE. All downstream surfaces (dashboard, TG bot,
    # API consumers) automatically see null and treat the mint as "out of
    # window" — no JS-side or bot-side gating required.
    #
    # Mint metadata, holder info, smart money — all kept. We're hiding the
    # PREDICTION, not the mint itself. A trader can still see the mint exists
    # in /api/live; we just don't claim a probability for it.
    LANE_GRACE_S = 90
    age_s = m_out.get("age_s") or 0
    if age_s > LANE_GRACE_S:
        for k in (
            "grad_prob", "grad_prob_raw", "grad_prob_saturation",
            "grad_prob_base_rate", "grad_prob_lift_x",
            "grad_prob_calibration", "grad_neighbors", "grad_n_graduated",
            "expected_peak_mult", "median_peak_mult",
            "expected_upside_from_now", "median_upside_from_now",
            "runner_prob_2x", "runner_prob_3x", "runner_prob_5x",
            "runner_prob_10x", "runner_prob_20x",
            "runner_prob_2x_raw", "runner_prob_3x_raw", "runner_prob_5x_raw",
            "runner_prob_10x_raw", "runner_prob_20x_raw",
            "runner_prob_2x_from_now", "runner_prob_3x_from_now",
            "runner_prob_5x_from_now", "runner_prob_10x_from_now",
            "runner_prob_20x_from_now",
            "runner_prob_2x_saturation", "runner_prob_3x_saturation",
            "runner_prob_5x_saturation", "runner_prob_10x_saturation",
            "runner_prob_20x_saturation",
            "runner_prob_2x_from_now_saturation",
            "runner_prob_3x_from_now_saturation",
            "runner_prob_5x_from_now_saturation",
            "runner_prob_10x_from_now_saturation",
            "runner_prob_20x_from_now_saturation",
            "runner_prob_2x_base_rate", "runner_prob_3x_base_rate",
            "runner_prob_5x_base_rate", "runner_prob_10x_base_rate",
            "runner_prob_20x_base_rate",
            "runner_prob_2x_from_now_base_rate",
            "runner_prob_3x_from_now_base_rate",
            "runner_prob_5x_from_now_base_rate",
            "runner_prob_10x_from_now_base_rate",
            "runner_prob_20x_from_now_base_rate",
            "runner_prob_2x_lift_x", "runner_prob_3x_lift_x",
            "runner_prob_5x_lift_x", "runner_prob_10x_lift_x",
            "runner_prob_20x_lift_x",
            "runner_prob_2x_from_now_lift_x",
            "runner_prob_3x_from_now_lift_x",
            "runner_prob_5x_from_now_lift_x",
            "runner_prob_10x_from_now_lift_x",
            "runner_prob_20x_from_now_lift_x",
            "x_factor",
            "runner_n_valid_neighbors",
            "combined_prob", "early_grad_prob", "rug_prob",
            "runner_prob_2x_from_now_calibration",
            "runner_prob_5x_from_now_calibration",
            "runner_prob_10x_from_now_calibration",
        ):
            if k in m_out:
                m_out[k] = None
        m_out["prediction_window"] = "expired"
    else:
        m_out["prediction_window"] = "open"

    # Event-driven TG alert push. Hot-path-safe: rules cached for 30s,
    # --- Wallet redaction (Option 5 — broader scope) ---
    # Five remaining wallet-shaped surfaces redacted at the API + snapshot
    # boundary per project_wallet_index_is_the_moat.md + Audit 09 verdict
    # (commit 34ce847: 7.37x grad-rate lift at smart_money_in>=7, CIs
    # strongly non-overlapping). The narrow Option A redaction
    # (smart_money_examples + clustered_wallets) shipped at deploy
    # 06480be; this block closes the remaining five vectors.
    #
    # Placement: runs BEFORE alert_push.maybe_push so the persisted
    # snapshot file (which the TG bot consumes) also has redacted
    # wallet fields. Same shape as the Option A redactions earlier
    # in this function (smart_money_examples line ~746, clustered_wallets
    # line ~765) — those also redact pre-snapshot.
    #
    # Internal computations above (cluster signal, wallet_balance
    # enrichment via local `top_buyers` var, creator_history lookup
    # via `first_buyer` read from `m` at line ~608, fee_delegation
    # enrichment) already completed using the live values from `m`
    # and local variables — those are untouched. Only the m_out
    # API/snapshot surface fields are zeroed.
    #
    # Backward-compatible: field names preserved, list types stay
    # lists (empty), nullable identifier fields go to None.
    # See docs/research/wallet_redaction_2026_05_11.md for Option A
    # receipt; broader-scope receipt commits alongside this deploy.
    m_out["top_buyers"]  = []
    m_out["first_buyer"] = None
    if isinstance(m_out.get("creator_history"), dict):
        m_out["creator_history"]["creator"] = None
    if isinstance(m_out.get("fee_delegation"), dict):
        m_out["fee_delegation"]["primary_delegate"] = None
        _delegates = m_out["fee_delegation"].get("delegates")
        if isinstance(_delegates, list):
            for _d in _delegates:
                if isinstance(_d, dict):
                    _d["wallet"] = None

    # only fires when age_bucket ∈ {30,60} (i.e. a prediction was just
    # logged), INSERT OR IGNORE on (rule_id, mint, age_bucket) for dedup.
    # Bot drains the pending_alerts queue on a 1.5s tick and renders.
    if hide_reason is None:
        _t = time.time()
        alert_push.maybe_push(m_out)
        status_module.record_stage_timing("alert_push", time.time() - _t)

    return m_out, hide_reason


# Shared threadpool for parallel mint scoring. 16 workers — _enrich_mint is
# mostly cached lookups + numpy k-NN (numpy releases the GIL during the
# heavy linear algebra), so threads scale well. Most tasks are now light
# (out-of-lane mints skip score_full entirely), so we can run more in
# parallel without saturating CPU.
import concurrent.futures as _futures
_score_pool = _futures.ThreadPoolExecutor(
    max_workers=16, thread_name_prefix="score-pool",
)


SCORING_AGE_CUTOFF_S = 120   # only score mints in or near the prediction window


def _score_mints(snapshot: dict) -> list[dict]:
    """For each confirmed non-mayhem, non-suspect mint in the active scoring
    window (age < SCORING_AGE_CUTOFF_S), compute grad probability.

    All mints in window go through the threadpool — heavy in-window mints
    benefit from parallel score_full; light out-of-window mints have no
    real work but submit-overhead is small (<1ms) so it doesn't matter.

    2026-06-10: filter to active candidates (age < 120s) at entry. The
    public-scope contract says predictions ≤ 60s only; re-scoring mature
    mints every snapshot epoch was the 271 MB/min sklearn-allocation leak
    that crash-looped the web process every ~16 min. Mature mints drop
    from `out` here; their composite fires (always at 30-60s) already
    happened, and outcome resolution runs on a separate worker. Net: no
    signal lost. See feedback_machine_spec_derivative_configs.

    Thread-safe: all caches hold their own locks; predictions log queue is
    thread-safe; numpy k-NN releases the GIL."""
    INDEX.maybe_refresh()
    all_mints = snapshot.get("mints", [])
    if not all_mints:
        return []
    mints = [m for m in all_mints if (m.get("age_s") or 0) < SCORING_AGE_CUTOFF_S]
    if not mints:
        return []
    # Fix B (2026-05-09): batch-prefetch all rug_predictor 60s feature
    # vectors in ONE sqlite query. Replaces 1289 per-mint sqlite3.connect()
    # calls inside the parallel scoring loop with a single connection.
    # The dict is read-only after construction; safe to share across worker
    # threads without locking.
    try:
        rug_features_prefetched = rug_predictor.batch_fetch_features(
            [m["mint"] for m in mints if m.get("mint")]
        )
    except Exception as e:
        print(f"[score] rug_predictor batch prefetch failed: {e}; "
              f"falling back to per-mint sqlite path", flush=True)
        rug_features_prefetched = None
    out: list[dict] = []
    futures = [_score_pool.submit(_enrich_mint, m, rug_features_prefetched) for m in mints]
    for fut in futures:
        try:
            enriched, hide = fut.result()
        except Exception:
            continue
        if hide is None:
            out.append(enriched)
    # Composite-receipts cross-detection (added 2026-05-11 per
    # composite_receipts_logging_prereg.md). Inspects this tick's enriched
    # mints, updates the rolling-24h composite_score sample, identifies
    # mints whose composite_score >= P90 + mc_usd >= $5k AND that are not
    # already in composite_predictions, and inserts rows. Idempotent
    # (PRIMARY KEY on mint); never raises; runs ~1ms per tick. Wallet-
    # redaction safe (only aggregate counts + scalars persisted).
    try:
        composite_predictions.maybe_log_crossings(out)
    except Exception as e:
        print(f"[composite] cross-log error: {e}", flush=True)
    # TG push evaluator — separate from cross detection. Sweeps recent
    # crosses awaiting grad_prob_60, classifies into ACT/WATCH/below/expired,
    # pushes qualifying ones to TG. Idempotent via tg_pushed_at column.
    #
    # 2026-06-19 LATENCY FIX: pass the just-scored mints into evaluator so it
    # can read grad_prob from this tick's live cache instead of waiting for
    # the predictions table's async drain to commit (which was adding 60-120s
    # of pure dispatcher lag, observed in the wild as ~3 min total push delay).
    try:
        live_mints_by_mint = {m["mint"]: m for m in out if m.get("mint")}
        composite_predictions.evaluate_tg_pushes(live_mints_by_mint)
    except Exception as e:
        print(f"[composite] tg-push eval error: {e}", flush=True)
    # Composite ranker: top-of-list is the higher of grad_prob lift and
    # x_factor lift. A coin with mid grad but a strong x_factor (= "neighbors
    # pumped from this exact age + price ratio") surfaces alongside graduation
    # winners, instead of being buried because it's not destined to graduate.
    def _rank_key(r):
        gl = r.get("grad_prob_lift_x")
        xf = r.get("x_factor") or {}
        xl = xf.get("lift_x")
        best_lift = max(
            gl if gl is not None else -1,
            xl if xl is not None else -1,
        )
        gp = r.get("grad_prob")
        return (-best_lift, -(gp or -1), -r["current_vsol_sol"])
    out.sort(key=_rank_key)
    return out


# ─── shared scoring cache · precompute model ────────────────────────────
# /api/live, /api/v1/live, /api/v1/runners, /api/v1/smart_money_active all
# call _score_mints(snap) for the same snapshot. The observer writes the
# snapshot every ~2s; running the full scoring pipeline (k-NN, predictions
# logging, creator + smart-money cross-references) on every request burns
# ~1s of CPU per call when the snapshot hasn't changed.
#
# Naive request-time caching has a thundering-herd problem: under burst,
# every concurrent request misses the cache and computes in its own thread,
# GIL-contending. The fix is to compute proactively in a dedicated daemon
# thread whenever the snapshot epoch_ms changes, and have request handlers
# return the cached result with an O(1) read.
_score_cache: dict = {
    "epoch_ms":     None,
    "computed_at":  0.0,
    "result":       None,
    "n_tracked":    0,    # surfaced on /api/live; cached so we skip re-reading the 430KB snapshot file
}
_score_cache_lock = threading.Lock()
_precompute_thread_started = False
_precompute_lock = threading.Lock()


def _start_precompute_thread():
    """Start the daemon that recomputes scores whenever the snapshot version
    advances. Idempotent — safe to call repeatedly."""
    global _precompute_thread_started
    if _precompute_thread_started:
        return
    with _precompute_lock:
        if _precompute_thread_started:
            return
        _precompute_thread_started = True

        def _loop():
            print("[score-precompute] daemon started", flush=True)
            last_epoch = None
            while True:
                try:
                    snap = _read_snapshot()
                    if snap is not None:
                        epoch_ms = snap.get("snapshot_epoch_ms")
                        if epoch_ms != last_epoch:
                            status_module.reset_stage_timings()
                            t0 = time.time()
                            result = _score_mints(snap)
                            elapsed = time.time() - t0
                            # Pre-serialize JSON once per snapshot so /api/live
                            # returns cached bytes — no dict→JSON cost per
                            # request. ~400 KB serialization happens here
                            # (once / snapshot) instead of on every hit.
                            # Reinstated 2026-06-10 after proving via full
                            # rollback that this is NOT a leak source.
                            try:
                                snap_age_s = max(0, int((time.time() * 1000 - (epoch_ms or 0)) / 1000))
                                payload = {
                                    "snapshot_age_s":  snap_age_s,
                                    "n_tracked_total": snap.get("n_tracked", 0),
                                    "n_indexed_curves": INDEX.n_curves_indexed,
                                    "mints":           result,
                                }
                                serialized = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                            except Exception:
                                serialized = None
                            with _score_cache_lock:
                                _score_cache["epoch_ms"]    = epoch_ms
                                _score_cache["computed_at"] = time.time()
                                _score_cache["result"]      = result
                                _score_cache["n_tracked"]   = snap.get("n_tracked", 0)
                                _score_cache["json_bytes"]  = serialized
                            last_epoch = epoch_ms
                            # Status telemetry: record the per-snapshot scoring
                            # latency so the status page shows real numbers.
                            status_module.record_latency_sample(elapsed)
                            status_module.record_daemon_success("score_precompute")
                            # Fan out to active websocket clients (Pro tier).
                            # Slim payload — only the top 60 by grad_prob —
                            # to keep frame size sane over the wire. Stamp
                            # n_indexed_curves + n_tracked so demo / clients
                            # can render lifetime + active totals without a
                            # second REST hit.
                            try:
                                top = result[:60] if isinstance(result, list) else []
                                wsfanout.broadcast(wsfanout.make_frame(
                                    kind="live_update",
                                    mints=top,
                                    snapshot_epoch_ms=epoch_ms,
                                    extra={
                                        "n_indexed_curves": INDEX.n_curves_indexed,
                                        "n_tracked":        snap.get("n_tracked", 0),
                                    },
                                ))
                            except Exception as e:
                                print(f"[ws] broadcast skipped: {e}", flush=True)
                except Exception as e:
                    print(f"[score-precompute] failed: {e}", flush=True)
                # 2026-06-09: bumped from 0.5s → 2s. Observer writes the
                # snapshot every ~5s; polling 4× faster than the producer
                # just spins CPU. Combined with mtime-cached _read_snapshot()
                # this dramatically cuts the precompute thread's footprint.
                time.sleep(2.0)

        threading.Thread(target=_loop, daemon=True, name="score-precompute").start()


def _score_mints_cached(snapshot: dict) -> list[dict]:
    """O(1) cache read. Recomputation happens in the precompute daemon — by
    the time a request arrives the answer is already waiting.

    On the very first request after boot the cache may be empty; in that
    case we compute synchronously (one-shot bootstrap) and let the daemon
    take over for subsequent ticks."""
    _start_precompute_thread()
    with _score_cache_lock:
        if _score_cache["result"] is not None:
            return _score_cache["result"]
    # Bootstrap path: cache empty, compute inline so the first request
    # doesn't 503. Subsequent requests will hit the precomputed cache.
    result = _score_mints(snapshot)
    with _score_cache_lock:
        if _score_cache["result"] is None:
            _score_cache["epoch_ms"]    = snapshot.get("snapshot_epoch_ms")
            _score_cache["computed_at"] = time.time()
            _score_cache["result"]      = result
    return result


@app.get("/health", include_in_schema=False)
def health():
    """Lightweight liveness probe for Fly health checks.
    Avoids /api/live's full snapshot read + scoring loop, which can take
    seconds during cold-walk and trip the 10s health check timeout."""
    return {"ok": True}


@app.get("/api/live")
def api_live():
    """Hot path. Returns pre-serialized JSON bytes from the precompute
    cache when available — no dict→JSON cost per request. Cache-Control
    lets Fly's edge collapse multi-client polling."""
    _start_precompute_thread()
    with _score_cache_lock:
        cached_bytes  = _score_cache.get("json_bytes")
        cached_result = _score_cache["result"]
        cached_epoch_ms  = _score_cache["epoch_ms"]
        cached_n_tracked = _score_cache["n_tracked"]

    if cached_bytes is not None:
        return Response(
            content=cached_bytes,
            media_type="application/json",
            headers={"Cache-Control": "public, max-age=2"},
        )

    if cached_result is None:
        snap = _read_snapshot()
        if not snap:
            return JSONResponse({"error": "no snapshot yet — is observer-daemon running?"}, status_code=503)
        cached_result = _score_mints_cached(snap)
        cached_epoch_ms = snap.get("snapshot_epoch_ms", 0)
        cached_n_tracked = snap.get("n_tracked", 0)

    snap_age_s = max(0, int((time.time() * 1000 - (cached_epoch_ms or 0)) / 1000))
    return JSONResponse(
        content={
            "snapshot_age_s":  snap_age_s,
            "n_tracked_total": cached_n_tracked,
            "n_indexed_curves": INDEX.n_curves_indexed,
            "mints":           cached_result,
        },
        headers={"Cache-Control": "public, max-age=2"},
    )


# 2026-06-11: REMOVED the public unauth /api/wallets leaderboard. Per the
# receipts+reputation layer strategy memory, the wallet index is the moat
# and NEVER goes public. Discovery-of-unknown-wallets is the paid B2B surface;
# verification-of-named-wallets is the public /verify wedge. The full
# enumerable leaderboard is /api/v1/wallets/leaderboard, gated at Pro tier.
# A free-tier "n_wallets_indexed" counter is still exposed via /api/live
# for marketing purposes — that's just the integer, not the index itself.


@app.get("/api/paper", tags=["paper"], summary="Paper-trading P&L")
def api_paper():
    """Live performance of the paper-trading harness running three thresholds
    (70/80/90% grad probability). No auth — public proof-of-signal."""
    return paper_trade.stats()


@app.get("/api/postmortems", tags=["paper"],
         summary="Closed trades with catastrophic loss + full entry context")
def api_postmortems(min_loss_pct: float = 0.30, limit: int = 50,
                    require_context: bool = True):
    """Returns paper trades that closed with a loss ≥ `min_loss_pct` (default
    −30%), most-recent first, including the full enriched-mint snapshot we
    had at entry time.

    Default surfaces only rows that have entry_context_json populated (i.e.
    trades opened after 2026-04-28 when the column was added). Pass
    `require_context=false` to include older context-less rows.

    Useful for:
    - Understanding which mints fooled all our filters at entry
    - Aggregating which bot_flags fired (or didn't) on losing trades
    - Driving future entry-filter improvements based on real failures

    No auth — full transparency."""
    return paper_trade.get_postmortems(
        min_loss_pct=min_loss_pct, limit=limit, require_context=require_context,
    )


# Headline-slice cache. The hero on /, /api, /for-terminals, and the bot's
# /start all read from /api/accuracy.headline. Under a Twitter spike on launch
# day, that's a 10× burst. Recomputing 3 SQL queries per request is fine on
# normal traffic; under a burst it just means 10× the queries for the same
# value. TTL-cached at module scope; one query per minute serves every
# visitor. Thread-safe via a single GIL-protected dict assignment (no lock
# needed since the worst race is two threads recomputing the same value
# back-to-back — harmless).
_HEADLINE_CACHE_TTL_S = 90
_HEADLINE_CACHE: dict = {"ts": 0, "data": {"status": "warming"}}


def _compute_headline() -> dict:
    import contextlib as _cl
    import time as _t
    _now = int(_t.time())
    try:
        with _cl.closing(sqlite3.connect(db.DB_PATH, timeout=5)) as c, c:
            # was_calibrated=1 = the post-cutover, self-correcting calibration era.
            # Pre-cutover, score 0.70 had a different meaning (raw uncalibrated
            # model output ~55% grad rate). Including those rows in trajectory or
            # hit-rate mixes regimes — same threshold, different model behind it.
            # Apples-to-apples requires this filter, mirroring the timing query.
            def _hr(time_clause, args):
                row = c.execute(
                    "SELECT COUNT(*) AS total, SUM(CASE WHEN actual_graduated=1 THEN 1 ELSE 0 END) AS hit "
                    "FROM predictions WHERE was_calibrated = 1 AND predicted_prob >= 0.70 AND age_bucket IN (30, 60) "
                    f"AND actual_graduated IS NOT NULL {time_clause}",
                    args,
                ).fetchone()
                total = int(row[0] or 0); hit = int(row[1] or 0)
                if not total: return {"status": "warming"}
                return {"status": "ok", "n_resolved": total, "n_graduated": hit,
                        "hit_rate": hit / total}
            last_30d  = _hr("AND predicted_at >= ?", (_now - 30 * 86400,))
            lifetime_strict = _hr("", ())
            # Weekly trajectory (not monthly) over a 90-day window. Weekly bucketing
            # surfaces within-month regime changes — alert criteria tightened in
            # mid-May 2026, dropping volume ~10× while hit rate stepped up from
            # ~55% to ~90%+. Monthly bucketing hid that as a single 58% row.
            # Weekly buckets render the curve honestly.
            trajectory = []
            for r in c.execute(
                "SELECT strftime('%Y-W%W', datetime(predicted_at, 'unixepoch')) AS wk, "
                "       MIN(predicted_at) AS wk_start, "
                "       COUNT(*) AS total, SUM(CASE WHEN actual_graduated=1 THEN 1 ELSE 0 END) AS hit "
                "FROM predictions WHERE was_calibrated = 1 AND predicted_prob >= 0.70 AND age_bucket IN (30, 60) "
                "AND actual_graduated IS NOT NULL AND predicted_at > ? "
                "GROUP BY wk ORDER BY wk",
                (_now - 90 * 86400,),
            ).fetchall():
                total = int(r[2] or 0); hit = int(r[3] or 0)
                if not total: continue
                trajectory.append({
                    "week":          r[0],
                    "week_start_unix": int(r[1]),
                    "n":             total,
                    "n_graduated":   hit,
                    "hit_rate":      hit / total,
                })
            # When did the calibrated model go live? Useful context for LLMs and
            # auditors trying to understand why earlier months don't appear.
            row = c.execute(
                "SELECT MIN(predicted_at) FROM predictions WHERE was_calibrated = 1"
            ).fetchone()
            model_deployed_at = int(row[0]) if row and row[0] else None

            # TIMING — the real edge. For ≥0.70 calls that DID graduate, how
            # many seconds between our call and the bonding-curve completing?
            # This is the bot/fast-trader actionable window. Median tells the
            # honest "you have ~N seconds to enter" story.
            lags = c.execute("""
                SELECT (o.graduated_at - p.predicted_at) AS lag_s
                  FROM predictions p
                  JOIN post_grad_outcomes o ON o.mint = p.mint
                 WHERE p.was_calibrated = 1
                   AND p.predicted_prob >= 0.70
                   AND p.age_bucket IN (30, 60)
                   AND p.actual_graduated = 1
                   AND o.graduated_at IS NOT NULL
                   AND p.predicted_at >= ?
            """, (_now - 90 * 86400,)).fetchall()
            lag_secs = sorted([r[0] for r in lags if r[0] is not None and r[0] >= 0])
            timing = {"status": "warming"}
            if len(lag_secs) >= 30:
                def _p(p): return lag_secs[min(int(len(lag_secs) * p), len(lag_secs) - 1)]
                under_30s = sum(1 for s in lag_secs if s < 30)
                under_60s = sum(1 for s in lag_secs if s < 60)
                timing = {
                    "status":       "ok",
                    "n_grads":      len(lag_secs),
                    "p10_s":        _p(0.10),
                    "p25_s":        _p(0.25),
                    "p50_s":        _p(0.50),
                    "p75_s":        _p(0.75),
                    "p90_s":        _p(0.90),
                    "under_30s_pct": under_30s / len(lag_secs),
                    "under_60s_pct": under_60s / len(lag_secs),
                }

            # TIER RUNWAYS — by TG composite tier (ACT/WATCH/SCOUT). Different
            # tier = different urgency window = different customer type. The
            # site copy hydrates these into the hero detail.
            tier_runways = {}
            for tier in ("ACT", "WATCH", "SCOUT"):
                tier_lags = c.execute("""
                    SELECT (o.graduated_at - cp.predicted_at) AS lag_s
                      FROM composite_predictions cp
                      JOIN post_grad_outcomes o ON o.mint = cp.mint
                     WHERE cp.tg_tier = ?
                       AND cp.did_graduate = 1
                       AND o.graduated_at IS NOT NULL
                       AND cp.predicted_at >= ?
                """, (tier, _now - 90 * 86400)).fetchall()
                ls = sorted([r[0] for r in tier_lags if r[0] is not None and r[0] >= 0])
                if len(ls) >= 20:
                    tier_runways[tier] = {
                        "status":   "ok",
                        "n":        len(ls),
                        "p50_s":    ls[len(ls) // 2],
                    }
                else:
                    tier_runways[tier] = {"status": "warming", "n": len(ls)}

            # RECALL — of mints that graduated AND that our scorer saw at age
            # 30/60 in the calibrated regime, what fraction did we call at
            # ≥0.70? Denominator restricted to "observed at 30/60s by the
            # calibrated model" so it's apples-to-apples with precision. The
            # naive denominator ("all graduations on Solana") undercounts our
            # coverage because most mints graduate instantly or are out of our
            # scoring window. This is the meaningful recall for a trader
            # choosing whether to wire our signal into their agent.
            since_30d = _now - 30 * 86400
            recall_row = c.execute("""
                SELECT COUNT(DISTINCT o.mint) AS observed_grads,
                       COUNT(DISTINCT CASE WHEN p.predicted_prob >= 0.70 THEN o.mint END) AS called
                  FROM post_grad_outcomes o
                  JOIN predictions p ON p.mint = o.mint
                 WHERE o.graduated_at >= ?
                   AND p.was_calibrated = 1
                   AND p.age_bucket IN (30, 60)
            """, (since_30d,)).fetchone()
            observed = int(recall_row[0] or 0); called = int(recall_row[1] or 0)
            recall_last_30d = ({
                "status": "ok",
                "n_graduations_observed": observed,
                "n_called_at_70pct": called,
                "recall_among_observed": called / observed,
            } if observed > 0 else {"status": "warming"})

            # POST-GRAD RUNNER RECEIPTS — the metric a post-graduation trader
            # actually cares about: "of mints we tagged runner_prob_Nx ≥ 0.5,
            # what % actually went Nx?" Pulled from the cached aggregation in
            # predictions.py. Hit = actual_max_mult / entry_mult ≥ N. Age 30/60
            # window, calibrated regime.
            from predictions import _RUNNER_RECEIPT_CACHE, _RUNNER_RECEIPT_LOCK, _refresh_runner_receipt_cache
            try:
                _refresh_runner_receipt_cache()
                with _RUNNER_RECEIPT_LOCK:
                    runner_cache = dict(_RUNNER_RECEIPT_CACHE.get("data") or {})
            except Exception:
                runner_cache = {}
            runner_rates = {}
            # BASE RATES — the "single biggest omission" critics flag. For each
            # tier (2x/5x/10x), compute the UNCONDITIONAL rate at which observed
            # graduations hit that peak multiplier. The model's hit rate is only
            # meaningful as LIFT over this base rate. Without it, "65% hit 2x"
            # could mean the model adds nothing if 65% of all graduates hit 2x
            # anyway. With it, lift = model_rate / base_rate quantifies edge.
            base_rates = {}
            for tier_label, tier_x in (("2x", 2.0), ("5x", 5.0), ("10x", 10.0)):
                row = c.execute("""
                    SELECT COUNT(*) AS n,
                           SUM(CASE WHEN (actual_max_mult / entry_mult) >= ? THEN 1 ELSE 0 END) AS n_hit
                      FROM predictions
                     WHERE was_calibrated = 1
                       AND age_bucket IN (30, 60)
                       AND actual_max_mult IS NOT NULL
                       AND entry_mult IS NOT NULL
                       AND entry_mult > 0
                """, (tier_x,)).fetchone()
                n = int(row[0] or 0); h = int(row[1] or 0)
                if n >= 100:
                    base_rates[tier_label] = {"n": n, "n_hit": h, "rate": h / n}
            for tier in ("2x", "5x", "10x"):
                td = runner_cache.get(tier) or {}
                n_total = 0; n_hit = 0
                for ab in (30, 60):
                    cell = (td.get(ab) or {}).get(0.5)
                    if cell:
                        n_total += int(cell.get("n") or 0)
                        n_hit   += int(cell.get("n_hit") or 0)
                if n_total >= 20:
                    model_rate = n_hit / n_total
                    base = base_rates.get(tier)
                    lift = (model_rate / base["rate"]) if (base and base["rate"] > 0) else None
                    runner_rates[tier] = {
                        "status": "ok",
                        "threshold_band": ">=50% model confidence",
                        "n": n_total,
                        "n_hit": n_hit,
                        "hit_rate": model_rate,
                        "base_rate": base["rate"] if base else None,
                        "base_rate_n": base["n"] if base else None,
                        "lift_over_base": lift,
                    }
                else:
                    runner_rates[tier] = {"status": "warming", "n": n_total}

        return {
            "criteria":      "predicted_prob >= 0.70 AND age_bucket IN (30, 60) AND was_calibrated = 1 AND actual_graduated IS NOT NULL",
            "criteria_note": "All hit-rate and trajectory measures are restricted to the calibrated-model regime (was_calibrated=1). Pre-cutover predictions used the same 0.70 threshold against an uncalibrated score and are excluded — including them would mix two regimes.",
            "trajectory_note": "Weekly buckets over the last 90 days under the calibrated regime. Hit rate stepped up mid-May 2026 when alert criteria were tightened (volume dropped ~10×, precision climbed from ~55% → ~90%+). The full audit of that change is at /verdict.",
            "live_endpoint_note": "live_endpoint is /api/accuracy. .headline.last_30d is the public rolling hit rate; .headline.trajectory is the week-by-week curve under the calibrated regime.",
            "model_deployed_at": model_deployed_at,
            "last_30d":      last_30d,
            "lifetime":      lifetime_strict,
            "trajectory":    trajectory,
            "time_to_grad":  timing,
            "tier_runways":  tier_runways,
            "recall_30d":    recall_last_30d,
            "recall_note":   "Of mints that graduated AND that our scorer saw at age 30 or 60 seconds, the fraction we called at ≥0.70 confidence. Denominator restricted to 'observed by the calibrated scorer' — most pump.fun mints graduate before age 30s or never reach scoring window, so they're out of scope. This is the meaningful recall for a trader deciding whether the high-confidence signal is too narrow.",
            "post_grad_runner_rates": runner_rates,
            "post_grad_runner_note":  "Hit = actual_max_mult / entry_mult ≥ Nx (peak, not realized — exit logic is up to you). Labels are observer-derived (caveat: scraper gaps). 'Threshold band' = mints scored ≥50% by the model on runner_prob_Nx_from_now at age 30 or 60. Each tier also reports BASE RATE (unconditional % of observed graduations that hit Nx) and LIFT (model_rate / base_rate). Lift > 1.0 means the model adds edge over picking randomly from graduates; lift = 1.0 means no edge; lift < 1.0 means the screen is anti-signal.",
            "computed_at":   _now,
            "ttl_s":         _HEADLINE_CACHE_TTL_S,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _get_headline_cached() -> dict:
    import time as _t
    _now = int(_t.time())
    if _now - _HEADLINE_CACHE["ts"] < _HEADLINE_CACHE_TTL_S:
        return _HEADLINE_CACHE["data"]
    data = _compute_headline()
    _HEADLINE_CACHE["ts"] = _now
    _HEADLINE_CACHE["data"] = data
    return data


# Full /api/accuracy response cache. The endpoint runs 14+ DB aggregations
# per hit — graduation calibration, drift, post-grad stats, flag calibration
# for every binary heuristic, label-source breakdown, tg_fires stats, act
# slice, headline timing. That's a lot of work for data that doesn't change
# meaningfully sub-minute. Cold path is ~25s; a background warmer thread
# keeps the cache always warm so no user ever waits.
_ACCURACY_FULL_CACHE_TTL_S = 120
_ACCURACY_FULL_CACHE_WARM_S = 60   # re-compute every 60s — half the TTL
_ACCURACY_FULL_CACHE: dict = {"ts": 0, "data": None}


def _compute_accuracy_response() -> dict:
    """The actual accuracy computation — extracted so the background warmer
    can call it without going through FastAPI route machinery."""
    lifetime = calibration.get_snapshot()
    forward  = predictions.get_live_calibration()
    forward_calibrated = predictions.get_live_calibration(calibrated_only=True)
    runner   = predictions.get_runner_calibration()
    # Self-calibration map (raw → calibrated) per tier. Updated every 15
    # min by the calibration daemon. Empty curves mean we don't have
    # enough data yet for that tier.
    curves   = predictions.get_all_calibration_curves()
    # Drift watcher — recent vs lifetime accuracy at the ≥80% bucket.
    drift    = predictions.get_drift_status()
    # Post-graduation survival — counts graduates we're tracking and the
    # 30-min sustain rate once outcomes resolve. The dataset accumulates
    # over time; until ~30 days of data, predictions stay stubbed null.
    post_grad = post_grad_tracker.stats()
    # Manufactured-pump heuristic forward validation — same self-correcting
    # pattern as grad_prob: we logged the flag, the curve resolver tells us
    # what actually happened, and we report rug-rate of flagged vs unflagged
    # mints. Lift > 1 means the flag is doing real work.
    manufactured = predictions.get_manufactured_pump_calibration()
    # Forward validation of the binary heuristics surfaced as badges /
    # alert kinds. Same shape as `manufactured_pump` block — once we have
    # ≥10 resolved samples on each side, lift ratios show the trader-
    # relevant signal: do flagged mints actually outperform baseline?
    dex_paid_cal      = predictions.get_flag_calibration("dex_paid")
    fee_delegated_cal = predictions.get_flag_calibration("fee_delegated")
    bundle_cal        = predictions.get_flag_calibration("bundle_detected")
    # Per-fire outcome accounting — the only place we measure alerts AS
    # alerts (not as mints). Buckets break down hit rate by feature subset
    # so we can answer "are fires where weight_early ≥0.80 systematically
    # worse than the population?" once we have a few hundred resolved.
    tg_fires_stats = tg_fires.stats()
    # Label-source breakdown — what fraction of our resolved labels come
    # from on-chain ground truth (Tier 1 backfill) vs observer-derived?
    # This number is the corpus-correctness receipt: 100% on-chain means
    # every accuracy claim below rests on Solana state, not our (lossy)
    # observer's reconstruction.
    label_source = predictions.get_label_source_breakdown()

    # ACT slice — the slice that matches what TG actually fires on:
    # ≥70% grad_prob AND entry_mult ≤ 2.0 (clean entry). This is the
    # number the /accuracy page surfaces as "Live forward".
    #
    # Anchored to the currently-active grad_prob rule's created_at: we ONLY
    # count predictions made under the current product configuration. Without
    # this floor, we'd average over predictions made under prior thresholds
    # / pre-backfill calibration regimes — different products masquerading
    # as the same number. n must reach 30 before we publish a rate; below
    # that the slice reports `warming` with raw counts so the page can show
    # an honest "n=X, need ≥30" notice instead of a misleading percentage.
    import contextlib as _cl
    act_slice = {"status": "warming"}
    try:
        with _cl.closing(sqlite3.connect(db.DB_PATH, timeout=5)) as c, c:
            # Cutoff anchored on activated_at, written by every 0→1 transition
            # (rule INSERT today; future re-activation paths must also write
            # this column — see schema migration in web/db.py).
            #
            # SINGLE-RULE-ONLY: MAX is correct ONLY because there's exactly
            # one active grad_prob rule. If a second active grad_prob rule
            # ever lands, MAX silently truncates good fires from the older
            # rule (anchors on the newer rule's activation, even when an
            # older fire was correctly produced under the older still-active
            # rule). The strictly-correct query is per-rule: an EXISTS
            # subquery joining fires.rule_id to tg_alert_rules.activated_at,
            # so each fire is judged against ITS rule's activation. Tracked
            # in BACKLOG.md ('Multi-rule cutoff') — switch BEFORE adding a
            # second active grad_prob rule.
            cutoff_row = c.execute("""
                SELECT MAX(activated_at) AS cutoff
                  FROM tg_alert_rules
                 WHERE active = 1 AND kind = 'grad_prob'
            """).fetchone()
            cutoff = int((cutoff_row[0] if cutoff_row else 0) or 0)
            row = c.execute("""
                SELECT COUNT(*),
                       SUM(CASE WHEN actual_graduated = 1 THEN 1 ELSE 0 END)
                  FROM predictions
                 WHERE predicted_at >= ?
                   AND age_bucket IN (30, 60)
                   AND predicted_prob >= 0.70
                   AND entry_mult <= 2.0
                   AND actual_graduated IS NOT NULL
            """, (cutoff,)).fetchone()
            n_resolved = int(row[0] or 0)
            n_grad = int(row[1] or 0)
        if n_resolved >= 30:
            act_slice = {
                "status":             "ok",
                "n_resolved":         n_resolved,
                "n_graduated":        n_grad,
                "actual_grad_rate":   n_grad / n_resolved,
                "criteria":           "grad_prob >= 0.70 AND entry_mult <= 2.0 AND age_bucket in (30, 60), since current rule activation",
                "since":              cutoff,
                "min_sample":         30,
            }
        else:
            act_slice = {
                "status":             "warming",
                "n_resolved":         n_resolved,
                "n_graduated":        n_grad,
                "min_sample":         30,
                "since":              cutoff,
                "note":               "post-simplification sample too small to publish; will surface a rate at n>=30",
            }
    except Exception:
        pass

    headline = _get_headline_cached()

    out = {
        "prediction_window_s":   60,                  # we predict at age <=60s; numbers below are bounded to that scope
        "headline":              headline,            # 30-day hit rate + trajectory — the public hero counter
        "label_source":          label_source,
        "lifetime":              lifetime if lifetime is not None else {"status": "warming"},
        "act_slice":             act_slice,           # what TG fires on — clean-entry ≥70%
        "forward":               forward,             # all-time forward calibration (mixed pre/post-calibration)
        "forward_calibrated":    forward_calibrated,  # post-self-correction only
        "runner":                runner,
        "calibration_curves":    curves,
        "drift":                 drift,
        "post_graduation":       post_grad,
        "manufactured_pump":     manufactured,
        "dex_paid":              dex_paid_cal,
        "fee_delegated":         fee_delegated_cal,
        "bundle_detected":       bundle_cal,
        "tg_fires":              tg_fires_stats,
        "self_correcting":       any(c.get("n_total_samples", 0) >= 30 for c in curves.values()),
    }
    return out


@app.get("/api/accuracy", tags=["calibration"], summary="Model calibration · proven accuracy")
def api_accuracy():
    """Live receipts. Cached for performance — the underlying compute is
    ~25s cold. Background warmer below keeps it always hot.

    On cache miss, returns last-known data immediately (if any) and
    triggers a background recompute. On cold boot (no cache yet), computes
    once synchronously."""
    import time as _t
    _cached = _ACCURACY_FULL_CACHE
    _now = int(_t.time())

    # Cache hit — return immediately
    if _cached["data"] is not None and (_now - _cached["ts"]) < _ACCURACY_FULL_CACHE_TTL_S:
        return _cached["data"]

    # Cache stale but we have data — serve stale, let the warmer refresh
    if _cached["data"] is not None:
        return _cached["data"]

    # Cold boot — first ever request, no cache yet. Compute synchronously.
    fresh = _compute_accuracy_response()
    _ACCURACY_FULL_CACHE["ts"]   = _now
    _ACCURACY_FULL_CACHE["data"] = fresh
    return fresh


# Background warmer — keeps /api/accuracy cache always populated. Computes
# fresh data into a local variable, THEN atomically swaps into the cache.
# Users hitting during a recompute always see the previous cached version
# (stale-while-revalidate). No user ever waits the ~25s cold path.
def _accuracy_cache_warmer_loop():
    import time as _t
    _t.sleep(15)   # let the app boot before first DB hit
    print(f"[accuracy_cache_warmer] started · interval={_ACCURACY_FULL_CACHE_WARM_S}s",
          flush=True)
    while True:
        try:
            t0 = _t.time()
            fresh = _compute_accuracy_response()
            elapsed = _t.time() - t0
            _ACCURACY_FULL_CACHE["ts"]   = int(_t.time())
            _ACCURACY_FULL_CACHE["data"] = fresh
            print(f"[accuracy_cache_warmer] refreshed in {elapsed:.1f}s", flush=True)
        except Exception as e:
            print(f"[accuracy_cache_warmer] failed: {e}", flush=True)
        _t.sleep(_ACCURACY_FULL_CACHE_WARM_S)


import threading as _threading_acc
_threading_acc.Thread(target=_accuracy_cache_warmer_loop, daemon=True,
                      name="accuracy-cache-warmer").start()


@app.get("/api/scope", tags=["calibration"], summary="Product scope · what we predict and at what age")
def api_scope():
    """Documents what predictions we make, the age window we make them at,
    the corpus they're calibrated against, and what we explicitly DO NOT
    claim. Honest scope = defensible product."""
    label_source = predictions.get_label_source_breakdown()
    return {
        "headline": {
            "description":            "We predict which pump.fun mints will graduate, in their first 60 seconds.",
            "high_confidence_band":   "≥70% grad_prob",
            "live_hit_rate_endpoint": "/api/accuracy (see .headline.last_30d for the rolling 30-day hit rate; see .headline.trajectory for month-over-month)",
            "alert_threshold_fixed":  0.70,
            "base_rate_baseline":     "~5% of any random pump.fun mint reaches graduation — the floor every hit rate is measured against",
            "verify_endpoint":        "/api/ledger/commits",
            "disclaimer":             "NFA. DYOR. graduate-oracle outputs probability scores from a model. Alerts describe model output, not financial advice. Pump.fun is high-risk; positions can go to zero. You are responsible for your trades.",
        },
        "predictions": {
            "graduation_prob": {
                "description":    (
                    "Calibrated probability that the mint reaches the bonding-curve "
                    "graduation threshold (vSOL ≥ 115). Live base rate ~5% on in-lane mints. "
                    "Display layer surfaces this as HIGH/MED/LOW ranking buckets "
                    "(grad_prob_bucket field); absolute probability available for "
                    "sizing decisions. The bare 'X% to graduate' framing was retired "
                    "at the Gate 5 calibrated-GBM cutover (2026-05-06): the deployed "
                    "k-NN's score scale was a model-scale artifact, not a probability "
                    "claim. Calibrated GBM is anchored to the live base rate via an "
                    "isotonic regression layer trained on dual-write resolved outcomes."
                ),
                "valid_window_s":     60,
                "calibrated":         True,
                "calibration_layer":  "isotonic_v1",
                "label_source":       "vsol_threshold_115",
                "live_base_rate":     "~5% on in-lane mints",
                "display_recommendation": (
                    "Use grad_prob_bucket (HIGH/MED/LOW) as the headline; show "
                    "calibrated probability + base rate as supporting context. "
                    "Avoid bare 'X%' framing — the number means '~X% likely to "
                    "graduate' only AFTER the isotonic layer; raw GBM scores "
                    "(grad_prob_gbm_shadow) are model-scale artifacts."
                ),
                "bucket_method": (
                    "Bimodal-aware: when the calibrated distribution has a hard "
                    "ceiling (single calibrated value with ≥1% mass — currently the "
                    "case due to limited isotonic training-data resolution at the "
                    "upper tail), HIGH = above-ceiling outliers, MED = at-ceiling "
                    "AND raw_GBM ≥ 97th percentile of raw scores, LOW = otherwise. "
                    "When training data accumulates enough to smooth the upper tail "
                    "(no value clears 1% mass), the daemon automatically falls back "
                    "to standard percentile cutoffs (HIGH = top-1%, MED = top-5%). "
                    "Live mode + values visible in /api/status under "
                    "bucket_cutoffs.bucket_logic_mode. See "
                    "docs/research/bucket_cutoffs_bimodal_finding.md."
                ),
            },
            "rug_prob": {
                "description":    "Probability of a single trade ≥40% drop within first 5 minutes (devil candle).",
                "valid_window_s": 60,
                "calibrated":     True,
                "label_source":   "trade_timeline_in_first_5min",
            },
            "runner_prob_2x_5x_10x_from_now": {
                "description":    "Probability the mint peaks at N× from current price.",
                "valid_window_s": 300,
                "calibrated":     "directional only — magnitude is non-stationary",
                "label_source":   "max_mult_observed",
                "caveat":         "Lane 13 audit (2026-05-05, rolling 24h windows): magnitude calibration shows non-stationary behavior across recent rolling windows. The 2x_from_now tier swung from -35pp over to +5pp under in 18 hours on the best-populated days, with ~5-35pp drift on most days and near-zero on others. The bias direction has historically flipped (Lane 7 full sample was OVERCONFIDENT; recent slices were UNDER-confident). Treat the field as a RANKING signal, not a literal probability. Recalibration tuning in progress (slowing rebuild cadence 15min → 90min per Lane 13's mechanism-2 fix); re-validation in 1 week. Consumers making sizing decisions on absolute magnitude should use ranking buckets (high / medium / low) rather than the raw value. See docs/research/lane13_calibration_stability.md.",
            },
            "post_grad_survival_prob": {
                "description":    "PERMANENTLY SUNSET 2026-05-08. Field returns {prob: null, status: 'sunset_lane_60s_structural_limit'}. The aggregate post_graduation.sustain_rate_30m on /api/accuracy continues — that's the independent Jupiter measurement, unaffected.",
                "valid_window_s": None,
                "calibrated":     "permanently sunset (2026-05-08); structural boundary documented after three model-class attempts",
                "label_source":   "jupiter_price_polling",
                "caveat":         "FINDING 7 chain complete (sunset 2026-05-08): three model-class attempts all failed pre-registered acceptance criteria. (Path C, max-scaling z-score on 5 dims) FAILED — 1e-6 floor on sparse dimensions exploded distances to 10^14. (Path D2, log-z-score on 2 continuous + binary post-filter) FAILED at small corpus then re-FAILED at n=901 by density collapse on dense (0,0,0)-signature corpus. (Path 7h, calibrated logistic regression with 15-feature interaction-term vector) FAILED CRIT 2 — model 1.22pp WORSE than per-signature baseline on the only minority signature with n>=30. Per pre-registered iteration-limit at the model-class level (frozen 2026-05-07), the feature is permanently retired. Structural finding: lane-60s sustain prediction is not viable from the available features given the signature distribution of resolved graduates. The aggregate post_graduation.sustain_rate_30m on /api/accuracy continues unchanged — that's the independent Jupiter measurement. Full receipts trail at github.com/Based-LTD/graduate-oracle docs/research/post_grad_metric_broken_since_launch.md (Finding 7a-7i complete).",
            },
            "graduation_progress_pct": {
                "description":    "Curve position — fraction of the bonding curve filled. Observed mechanical state, NOT a prediction. Pairs with grad_prob (which IS a prediction) for the 'where is it / where might it go' framing — do not sum or substitute.",
                "valid_window_s": None,
                "calibrated":     False,
                "label_source":   "derived_from_current_vsol_sol",
                "shape":          "{pct: 0-100 | null, status: live | near | graduated | unknown}",
                "formula":        "max(0, min(100, (current_vsol_sol - 30) / 85 * 100))",
                "caveat":         "Naive vsol/115 would give 26% at fresh launches. The (vsol-30)/85 form anchors 0% at launch, 100% at graduation.",
            },
            "creator_score": {
                "description":    "Creator's track record across all observed launches.",
                "valid_window_s": None,
                "calibrated":     True,
                "label_source":   "on_chain_backfill",
            },
        },
        "corpus": {
            "n_indexed_mints":      label_source.get("n_total"),
            "n_resolved_outcomes":  label_source.get("n_resolved"),
            "pct_on_chain_truth":   label_source.get("pct_onchain_truth"),
        },
        "out_of_scope": [
            "lifetime trade-by-trade tracking of mature mints",
            "slow-cook rug detection over hour-plus windows",
            "post-graduation DEX price action beyond 30 minutes",
            "real-time charting / OHLC aggregation",
        ],
        "trade_capture_sla": {
            "window":   "first_60s",
            "target":   "≥95%",
            "verifier": "/api/observer_health  (samples 50 fresh mints every 15 min)",
        },
    }


@app.get("/api/predictor/health", tags=["calibration"], summary="Rug-prob predictor health · sample counts, status, corpus cross-check")
def api_predictor_health():
    """Live state of the rug_prob k-NN predictor: sample counts, warming/live
    status, calibration freshness, and a corpus-wide cross-check of distinct
    rugged mints across all checkpoint ages (not just the 60s training set).

    Use this to decide whether the predictor has graduated from "warming" to
    "live" and whether MIN_SAMPLES / MIN_POSITIVE_SAMPLES thresholds should
    move."""
    s = rug_predictor.stats()
    distinct_rugged_all_ages = None
    rows_by_age: dict = {}
    import contextlib
    try:
        with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=5)) as c, c:
            row = c.execute(
                "SELECT COUNT(DISTINCT mint) FROM mint_checkpoints WHERE actual_rugged = 1"
            ).fetchone()
            distinct_rugged_all_ages = int(row[0]) if row and row[0] is not None else 0
            for age, n_total, n_resolved, n_rugged in c.execute("""
                SELECT checkpoint_age_s,
                       COUNT(*),
                       SUM(CASE WHEN actual_rugged IS NOT NULL THEN 1 ELSE 0 END),
                       SUM(CASE WHEN actual_rugged = 1 THEN 1 ELSE 0 END)
                  FROM mint_checkpoints
                 GROUP BY checkpoint_age_s
                 ORDER BY checkpoint_age_s
            """):
                rows_by_age[int(age)] = {
                    "n_captured":  int(n_total or 0),
                    "n_resolved":  int(n_resolved or 0),
                    "n_rugged":    int(n_rugged or 0),
                }
    except Exception as e:
        return JSONResponse({**s, "corpus_error": str(e)}, status_code=200)
    return {
        **s,
        "corpus": {
            "distinct_rugged_mints_all_ages": distinct_rugged_all_ages,
            "rows_by_checkpoint_age":         rows_by_age,
        },
    }


@app.get("/api/ledger/commits", tags=["calibration"], summary="Tamper-evident hourly merkle commits over predictions")
def api_ledger_commits(since: int | None = None, limit: int = 200):
    """Returns recent hourly merkle commitments. Each commit locks in every
    prediction made during one hour — its merkle_root_hex is a function of
    the (mint, age_bucket, predicted_prob, ...) of every covered prediction.
    Snapshot this endpoint regularly. If a past root ever changes, we
    rewrote history."""
    return {
        "commit_period_s":         ledger.COMMIT_PERIOD_S,
        "current_leaf_version":    ledger.CURRENT_LEAF_VERSION,
        # Versioned field map — verifiers use the leaf_version on each
        # commit row to look up which fields its merkle root was hashed
        # over. Adding a new field bumps the version and adds a new
        # entry here; old versions stay frozen so historical proofs
        # keep verifying.
        "leaf_fields_by_version":  {str(v): list(fs) for v, fs in
                                    ledger._LEAF_FIELDS_BY_VERSION.items()},
        "commits":                 ledger.list_commits(since=since, limit=limit),
    }


@app.get("/api/ledger/proof/{prediction_id}", tags=["calibration"], summary="Merkle proof that a prediction was committed in its hour-root")
def api_ledger_proof(prediction_id: int):
    """Given a prediction id, returns its leaf hash, the merkle path of
    sibling hashes from leaf to root, and the committed root for that hour.
    Verify locally: hash the leaf payload, walk the path applying
    sha256(left || right) at each step, confirm the result equals
    merkle_root_hex of the commit."""
    proof = ledger.get_proof(prediction_id)
    if not proof:
        raise HTTPException(404, detail={"error": "prediction_not_found_or_not_yet_committed"})
    return proof


@app.get("/api/alerts/audit", tags=["calibration"], summary="Recent TG alert fires joined with the actual outcome of each called mint")
def api_alerts_audit(hours: int = 24, limit: int = 200):
    """For each TG alert we fired in the last `hours`, return the call details
    (kind, threshold, predicted_prob at fire time) and the actual outcome
    (max_mult achieved, did_graduate, resolved_at). The receipt for "did
    your alerts actually catch winners?" — readable by anyone.

    Filtered to the currently-active grad_prob rule and fires logged at or
    after that rule's activation. Without this floor, fires from older rules
    (different thresholds, different criteria) leak in and contaminate the
    aggregate hit rates with traffic that no longer represents the product.
    """
    import sqlite3, contextlib
    time_cutoff = int(time.time()) - hours * 3600
    out = []
    rule_cutoff = 0
    rule_ids = []
    with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=10)) as c, c:
        c.row_factory = sqlite3.Row
        # activated_at is written by every 0→1 transition (see web/db.py
        # migration + bot/main.py rule INSERTs). COALESCE to created_at as
        # safety for any pre-migration rows the backfill missed.
        #
        # SINGLE-RULE-ONLY: when a second active grad_prob rule lands, the
        # `cutoff = max(...)` line below silently truncates good fires from
        # the older rule. Switch to a per-rule correlated subquery against
        # tg_fires (each fire judged against ITS rule's activated_at).
        # See BACKLOG.md 'Multi-rule cutoff'.
        active = c.execute("""
            SELECT id, COALESCE(activated_at, created_at, 0) AS activated_at
              FROM tg_alert_rules
             WHERE active = 1 AND kind = 'grad_prob'
        """).fetchall()
        rule_ids = [r["id"] for r in active]
        rule_cutoff = max((r["activated_at"] for r in active), default=0)
        # Effective cutoff is the LATER of the user's `hours` window and the
        # active rule's activation. Both must be true to count a fire.
        cutoff = max(time_cutoff, rule_cutoff)
        if rule_ids:
            placeholders = ",".join("?" * len(rule_ids))
            rows = c.execute(f"""
                SELECT f.id, f.kind, f.threshold, f.mint, f.fired_at,
                       f.grad_prob, f.runner_prob_5x_from_now,
                       f.runner_prob_10x_from_now,
                       f.peak_mult_from_entry, f.peak_mult_absolute,
                       f.did_graduate, f.resolved_at, f.resolution_reason
                  FROM tg_fires f
                 WHERE f.fired_at >= ?
                   AND f.rule_id IN ({placeholders})
                 ORDER BY f.fired_at DESC
                 LIMIT ?
            """, (cutoff, *rule_ids, limit)).fetchall()
            for r in rows:
                out.append(dict(r))
    n_resolved = sum(1 for r in out if r.get("resolved_at"))
    n_graduated = sum(1 for r in out if r.get("did_graduate"))
    n_2x_from_entry = sum(1 for r in out if (r.get("peak_mult_from_entry") or 0) >= 2.0)
    n_5x_from_entry = sum(1 for r in out if (r.get("peak_mult_from_entry") or 0) >= 5.0)
    return {
        "window_hours":      hours,
        "active_rule_ids":   rule_ids,
        "rule_activated_at": rule_cutoff,
        "n_alerts":          len(out),
        "n_resolved":        n_resolved,
        "n_graduated":       n_graduated,
        "n_2x_from_entry":   n_2x_from_entry,
        "n_5x_from_entry":   n_5x_from_entry,
        "fires":             out,
    }


@app.get("/api/gate_validation", tags=["calibration"], summary="Pre-registered sustains-gate criterion · mechanical execution")
def api_gate_validation(history: int = 1):
    """Returns the most recent gate-validation run (set `history` for more).
    The criterion is pre-registered in BACKLOG.md ('Sustains-gate validation
    criterion'); this endpoint executes it mechanically against the live
    data on an hourly tick. The decision field is one of:

    - "warming"   — n < 15 in either bucket; not enough data yet
    - "fails"     — ratio < 1.5×; sustains doesn't discriminate, gate stays display-only
    - "hold"      — 1.5× ≤ ratio < 2×; collect 30 more fires before deciding
    - "validates" — ratio ≥ 2× AND n ≥ 15 each; gate ships as a hard suppression

    Two side stratifications are computed alongside the primary criterion:
    by `manufactured_pump` and by `bundle_detected`. These were flagged
    2026-05-04 as label-leak hypotheses — if 100% of post-bond runners are
    bundled/manufactured, the model has effectively learned "bundled scam =
    runner" rather than a real signal. Same population, different bucketing.
    No decision rule applied to the side analyses; they're observational.
    """
    runs = gate_validation.latest(history=max(1, min(history, 100)))
    if not runs:
        # Daemon hasn't ticked yet — compute on demand so the endpoint
        # never returns empty for a live deploy.
        live = gate_validation.compute()
        return {"runs": [{"computed_at": live["computed_at"],
                          "decision":    live["decision"],
                          "full_result": live}],
                "criterion_source": "BACKLOG.md"}
    return {"runs": runs, "criterion_source": "BACKLOG.md"}


@app.get("/api/predictions/by_mint/{mint}", tags=["calibration"], summary="What we predicted on a specific mint, at the moment we predicted it")
def api_predictions_by_mint(mint: str):
    """Look up our forward-logged predictions for a single mint. Returns
    every (age_bucket × predicted_prob) row we logged + the resolved outcome
    if available. This is the receipt for "what did you say about THIS
    coin?" — every prediction is timestamped and locked in via the merkle
    ledger before the outcome was known."""
    import sqlite3, contextlib
    with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=5)) as c, c:
        c.row_factory = sqlite3.Row
        rows = c.execute("""
            SELECT id, mint, age_bucket, predicted_prob, predicted_at,
                   runner_prob_2x, runner_prob_3x, runner_prob_5x,
                   runner_prob_10x, runner_prob_20x,
                   runner_prob_2x_from_now, runner_prob_3x_from_now,
                   runner_prob_5x_from_now, runner_prob_10x_from_now,
                   runner_prob_20x_from_now,
                   entry_mult, was_calibrated,
                   actual_graduated, actual_max_mult, resolved_at,
                   resolution_reason
              FROM predictions
             WHERE mint = ?
             ORDER BY age_bucket ASC, predicted_at ASC
        """, (mint,)).fetchall()
    if not rows:
        raise HTTPException(404, detail={"error": "no_predictions_logged_for_this_mint"})
    return {
        "mint":        mint,
        "n_predictions": len(rows),
        "predictions": [dict(r) for r in rows],
    }


@app.get("/api/wallet/{short}")
def api_wallet_lookup(short: str):
    """
    Lookup by 12-char short prefix — used internally by the bot to avoid
    loading its own copy of the wallet index. Public, no auth needed.
    """
    if WINTEL is None:
        raise HTTPException(503, detail={"error": "wallet_index_warming_up"})
    rec = WINTEL._wallets.get(short[:12])
    if not rec:
        return {"wallet": short, "found": False}
    return {"wallet": short, "found": True, "stats": {**rec, "wallet": short[:12]}}


# Helper: serve an HTML template with cache-busted /static/* URLs and
# `Cache-Control: no-cache` so browsers always pull the latest HTML on
# refresh. Solves the "I deployed but the user still sees old content"
# issue we hit on /accuracy. Apply to every templated route.
def _serve_html(template_name: str):
    html = (WEB_DIR / "templates" / template_name).read_text()
    try:
        js_v = int((WEB_DIR / "static" / "app.js").stat().st_mtime)
        css_v = int((WEB_DIR / "static" / "style.css").stat().st_mtime)
        html = html.replace("/static/app.js", f"/static/app.js?v={js_v}")
        html = html.replace("/static/style.css", f"/static/style.css?v={css_v}")
    except Exception:
        pass
    return HTMLResponse(html, headers={"Cache-Control": "no-cache, must-revalidate"})


@app.get("/api", response_class=HTMLResponse, include_in_schema=False)
def api_landing():
    return _serve_html("api.html")


@app.get("/docs", response_class=HTMLResponse, include_in_schema=False)
def docs_page():
    """Hand-curated developer docs: quickstart + per-endpoint + cookbook.
    Swagger UI is available at /swagger; this route is the marketing+integration
    artifact developers actually read before signing up."""
    return _serve_html("docs.html")


@app.get("/cli", response_class=HTMLResponse, include_in_schema=False)
def cli_page():
    """goracle CLI install + usage. Pure marketing surface — the install
    command is the headline. Source: cli/ in the main repo."""
    return _serve_html("cli.html")


@app.get("/bot", response_class=HTMLResponse, include_in_schema=False)
def bot_page():
    """Telegram bot landing page — explainer, quickstart, tokenomics,
    disclaimers. The headline nav 'TG BOT' button now points here
    instead of directly opening Telegram, so users get context first."""
    return _serve_html("bot.html")


@app.get("/status", response_class=HTMLResponse, include_in_schema=False)
def status_page():
    """Public status / uptime page. JSON version at /api/status."""
    return _serve_html("status.html")


@app.get("/accuracy", response_class=HTMLResponse, include_in_schema=False)
def accuracy_page():
    """The receipts page — leads with backtest + live forward hit rates.
    Pulls live numbers from /api/accuracy via JS."""
    return _serve_html("accuracy.html")


@app.get("/calls", response_class=HTMLResponse, include_in_schema=False)
def calls_page():
    """Public signal-calls index page — paste a CA to look up, browse recent calls."""
    return _serve_html("calls.html")


@app.get("/alert/{mint}", response_class=HTMLResponse, include_in_schema=False)
def alert_proof_page(mint: str):
    """Public proof page for a single signal. Loads alert_proof.html and
    hydrates via /api/v1/alert/{mint}. Tweetable receipt URL."""
    html = (WEB_DIR / "templates" / "alert_proof.html").read_text()
    # Inject mint into template via JS hydration
    html = html.replace("{{MINT_PLACEHOLDER}}", mint)
    try:
        js_v = int((WEB_DIR / "static" / "app.js").stat().st_mtime)
        css_v = int((WEB_DIR / "static" / "style.css").stat().st_mtime)
        html = html.replace("/static/app.js", f"/static/app.js?v={js_v}")
        html = html.replace("/static/style.css", f"/static/style.css?v={css_v}")
    except Exception:
        pass
    return HTMLResponse(html, headers={"Cache-Control": "no-cache, must-revalidate"})


@app.get("/perps", response_class=HTMLResponse, include_in_schema=False)
def perps_page():
    """Perps Observatory — live counters of perp data being collected.
    v0 source: Hyperliquid (real). Drift + Jupiter Perps stubbed as
    "pending" to signal expansion. Sets the "coming Q3 in goracle-mcp"
    narrative with real, growing numbers."""
    return _serve_html("perps.html")


@app.get("/api/perps/observatory", tags=["perps"], summary="Perps Observatory snapshot")
def perps_observatory_api():
    """Public, no auth. Returns the current Perps Observatory snapshot —
    per-DEX status, top funding rates, top 24h volume, lifetime counters.
    Same numbers the /perps page renders."""
    import perps_observatory
    return perps_observatory.snapshot()


@app.get("/api/perps/wallet/{address}", tags=["perps"], summary="Per-wallet drill-down")
def perps_wallet_api(address: str):
    """Public, no auth. Returns per-wallet lifetime stats, current open
    positions, per-horizon win rate, recent entries, leaderboard history."""
    import perps_intel
    return perps_intel.wallet_detail(address)


@app.get("/api/perps/drift/wallet/{address}", tags=["perps"], summary="Drift wallet drill-down")
def perps_drift_wallet_api(address: str):
    """Public, no auth. Returns lifetime stats for a Drift trader, drawn
    from the public S3 trade history (Nov 2022 → Jan 2025, pre-exploit)."""
    import perps_intel
    return perps_intel.drift_wallet_detail(address)


@app.get("/api/perps/drift/markets", tags=["perps"], summary="Drift market index overview")
def perps_drift_markets_api():
    """Public, no auth. Returns per-market rollup across every Drift market
    we have files for: file count, trade-row count, indexed volume (post
    2026-06-17 schema bump), date range."""
    import perps_intel
    return {"markets": perps_intel.drift_top_markets()}


@app.get("/api/perps/drift/market/{market}", tags=["perps"], summary="Drift per-market drill-down")
def perps_drift_market_api(market: str):
    """Public, no auth. Returns aggregate stats for a single Drift market
    plus the most recent files we ingested for it. Data is pre-exploit
    historical (Drift's public S3 publishing ended 2025-01-08)."""
    import perps_intel
    return perps_intel.drift_market_detail(market)


@app.get("/api/perps/ledger/commits", tags=["perps"], summary="Perps receipts merkle commits")
def perps_ledger_api(limit: int = 50):
    """Public, no auth. Hourly merkle roots over the SHA256 leaves of every
    smart-money entry detected in that hour. Same discipline as the pump.fun
    /api/ledger/commits chain — proves entries are committed before their
    outcomes are observable."""
    import perps_intel
    rows = perps_intel.commits_list(limit=max(1, min(500, limit)))
    return {
        "leaf_format": (
            "SHA256 over canonical JSON: "
            "{v, id, ts, addr, mkt, side, size, entryPx, posUsd, lev, "
            "px@det, smart, whale, mRoi, mPnl}"
        ),
        "merkle_format": "Bitcoin-style pairwise SHA256, last leaf duplicated on odd levels",
        "n_commits":    len(rows),
        "commits":      rows,
    }


@app.get("/api/perps/market/{market}", tags=["perps"], summary="Per-market drill-down")
def perps_market_api(market: str):
    """Public, no auth. Returns smart-money currently positioned on this
    market, per-horizon win rate for entries on this market, recent entries,
    and a price-series buffer for sparkline rendering."""
    import perps_intel
    return perps_intel.market_detail(market)


@app.get("/perps/wallet/{address}", response_class=HTMLResponse, include_in_schema=False)
def perps_wallet_page(address: str):
    return _serve_html("perps_wallet.html")


@app.get("/perps/drift/wallet/{address}", response_class=HTMLResponse, include_in_schema=False)
def perps_drift_wallet_page(address: str):
    return _serve_html("perps_drift_wallet.html")


@app.get("/perps/drift/market/{market}", response_class=HTMLResponse, include_in_schema=False)
def perps_drift_market_page(market: str):
    return _serve_html("perps_drift_market.html")


# ── /conditions — public pump.fun market temperature ────────────────────
@app.get("/api/v1/conditions", tags=["public"], summary="Pump.fun conditions index")
def api_conditions():
    """Self-relative pump.fun market temperature. Every number is computed
    from our own observations (composite_predictions + post_grad_outcomes +
    predictions). No external assumptions baked in. Self-relative tiering
    against our 35-day historical distribution.

    Methodology + raw SQL: /conditions/methodology
    """
    import conditions
    return conditions.compute()


@app.get("/conditions", response_class=HTMLResponse, include_in_schema=False)
def conditions_page():
    return _serve_html("conditions.html")


@app.get("/conditions/methodology", response_class=HTMLResponse, include_in_schema=False)
def conditions_methodology_page():
    return _serve_html("conditions_methodology.html")


# ── GO Lens — the userscript overlay for Solana terminals ────────────────
@app.get("/lens", response_class=HTMLResponse, include_in_schema=False)
def lens_page():
    return _serve_html("lens.html")


@app.get("/lens/goracle-lens.user.js", include_in_schema=False)
def lens_userscript():
    """Serves the GO Lens userscript with the mime type Tampermonkey
    needs to recognize it as installable. Lives outside /web/ so it can
    be edited / iterated without touching the web app templates."""
    path = Path(__file__).parent.parent / "lens" / "goracle-lens.user.js"
    if not path.exists():
        raise HTTPException(404, detail={"error": "userscript_missing"})
    return FileResponse(
        path,
        media_type="application/javascript",
        headers={
            # Tampermonkey checks for updates via the @updateURL header; a
            # short cache lets us push fixes fast while still being polite
            # to the CDN.
            "Cache-Control": "public, max-age=300",
        },
    )


@app.get("/perps/market/{market}", response_class=HTMLResponse, include_in_schema=False)
def perps_market_page(market: str):
    return _serve_html("perps_market.html")


@app.get("/demo", response_class=HTMLResponse, include_in_schema=False)
def demo_page():
    """Live watch-the-bot-work page. Renders the same WebSocket feed our
    paying customers + LLM clients see, with smart-money glow + runner-prob
    flash + aggregate stats. Public, no auth — pulls the open /api/v1/live
    REST for initial state, then upgrades to the WebSocket firehose."""
    return _serve_html("demo.html")


@app.get("/receipts", response_class=HTMLResponse, include_in_schema=False)
def receipts_page():
    """Public verifiable receipts — Merkle commits + per-prediction proof lookup.
    Pulls live data from /api/ledger/commits and /api/ledger/proof/{id} via JS."""
    return _serve_html("receipts.html")


@app.get("/verdict", response_class=HTMLResponse, include_in_schema=False)
def verdict_page():
    """The 2026-06-02 Branch A verdict story — pre-reg → criteria → numbers
    → verification. Standalone landing page for the receipts moat narrative."""
    return _serve_html("verdict.html")


@app.get("/for-terminals", response_class=HTMLResponse, include_in_schema=False)
def for_terminals_page():
    """The terminal-integration landing page — why embed us, how to wire it
    up (REST/WS/webhook), code samples, token alignment, 30-day pilot CTA.
    Built for the embedded-by-terminals strategic target."""
    return _serve_html("for-terminals.html")


@app.get("/verify", response_class=HTMLResponse, include_in_schema=False)
def verify_search_page():
    """Public wallet-verification search page. Phase 1 wedge of the larger
    receipts + reputation layer strategy. Read-only, no claim flow yet."""
    return _serve_html("verify.html")


@app.get("/verify/{wallet}", response_class=HTMLResponse, include_in_schema=False)
def verify_wallet_page(wallet: str):
    """Public wallet-verification result page. The JS reads the wallet from
    the URL path and fetches /api/verify/{wallet} for the data."""
    return _serve_html("verify.html")


@app.get("/api/verify/{wallet}", tags=["calibration"], summary="Public wallet verification (redacted-for-public)")
def api_verify_wallet(wallet: str):
    """Public read-only verification of a single, named Solana wallet.

    Returns observed on-chain stats: graduation/runner/rug counts, fast-snipe
    rate, total invested. DELIBERATELY OMITS smart_score, rank, percentile,
    and any cross-wallet comparison — those would leak the discovery moat
    (see project_receipts_reputation_layer_strategy memory). Public verifies
    named wallets; private B2B discovers unknown wallets. Same engine,
    surgically separate exposure."""
    try:
        import wallet_intel
        if wallet_intel.INDEX is None:
            raise HTTPException(503, detail={"error": "wallet_index_warming_up"})
        wallet_intel.INDEX.maybe_refresh()
        short = (wallet or "").strip()[:12]
        if len(short) < 8:
            raise HTTPException(400, detail={"error": "invalid_wallet"})
        rec = wallet_intel.INDEX._wallets.get(short)
        if not rec:
            return {
                "wallet": wallet,
                "found": False,
                "hint": "no observed buys for this wallet in our dataset",
            }
        total = max(rec.get("total", 0), 0)
        def rate(field):
            return (rec.get(field, 0) / total) if total else 0.0
        total_sol = rec.get("total_buy_sol_lam", 0) / 1e9
        return {
            "wallet": wallet,
            "found": True,
            "stats": {
                "graduation_rate":   rate("graduated"),
                "runner_rate":       rate("runner"),
                "rug_rate":          rate("rug"),
                "fast_snipe_rate":   rate("fast_snipes"),
                "total_invested_sol": total_sol,
                "avg_buy_sol":       rec.get("avg_buy_sol", 0.0),
            },
            "raw_counts": {
                "graduated": rec.get("graduated", 0),
                "runner":    rec.get("runner", 0),
                "mid":       rec.get("mid", 0),
                "rug":       rec.get("rug", 0),
                "total":     total,
                "fast_snipes": rec.get("fast_snipes", 0),
            },
            "_doc": ("Every number computed from observed on-chain activity. "
                     "Rank/percentile/smart-score omitted by design — public "
                     "verify settles named-wallet claims; discovery stays "
                     "private (see /for-terminals)."),
            "_methodology_url": "https://graduate-oracle.fly.dev/verdict",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(503, detail={"error": "verify_lookup_failed", "detail": str(e)})


@app.get("/api/health", tags=["status"], summary="Public system health (latest watchdog tick + active alerts)")
def api_health():
    """Machine-readable system-health snapshot for terminals embedding us.

    Returns the latest watchdog tick (disk / memory / resolver-stuck count)
    plus any currently-active alerts. Designed to be polled by external
    uptime monitors (UptimeRobot, BetterStack, etc.) and by terminals'
    integration health checks. Public, no auth — being transparent about
    uptime IS the trust signal."""
    try:
        c = sqlite3.connect(db.DB_PATH, timeout=5)
        try:
            c.row_factory = sqlite3.Row
            latest = c.execute(
                "SELECT ts, disk_pct, disk_avail_gb, web_rss_mb, "
                "watchdog_headroom_mb, resolver_oldest_h "
                "FROM system_health ORDER BY ts DESC LIMIT 1"
            ).fetchone()
            active = c.execute(
                "SELECT alert_id, raised_at, severity, category, message "
                "FROM system_health_alerts WHERE resolved_at IS NULL "
                "ORDER BY alert_id DESC"
            ).fetchall()
        finally:
            c.close()
        # Healthy if no critical alerts; degraded if warning; down if critical.
        worst = "ok"
        for a in active:
            if a["severity"] == "critical":
                worst = "down"; break
            if a["severity"] == "warning":
                worst = "degraded"
        return {
            "status": worst,
            "checked_at": int(time.time()),
            "latest_tick": {
                "ts":                   latest["ts"] if latest else None,
                "disk_pct":             round(latest["disk_pct"], 1) if latest and latest["disk_pct"] is not None else None,
                "disk_avail_gb":        round(latest["disk_avail_gb"], 1) if latest and latest["disk_avail_gb"] is not None else None,
                "web_rss_mb":           round(latest["web_rss_mb"], 0) if latest and latest["web_rss_mb"] is not None else None,
                "watchdog_headroom_mb": round(latest["watchdog_headroom_mb"], 0) if latest and latest["watchdog_headroom_mb"] is not None else None,
                "resolver_oldest_h":    round(latest["resolver_oldest_h"], 1) if latest and latest["resolver_oldest_h"] is not None else None,
            } if latest else None,
            "active_alerts": [
                {"alert_id": a["alert_id"], "raised_at": a["raised_at"],
                 "severity": a["severity"], "category": a["category"],
                 "message": a["message"]}
                for a in active
            ],
            "_doc": ("Poll this from your uptime monitor or terminal health "
                     "check. status='ok' = no active alerts; 'degraded' = "
                     "warning; 'down' = critical. /health is the lightweight "
                     "liveness probe; this is the rich health surface."),
        }
    except Exception as e:
        # Health endpoint must NEVER 5xx — that defeats its own purpose.
        return {
            "status": "unknown",
            "checked_at": int(time.time()),
            "error": str(e),
        }


@app.get("/api/burn/pending", tags=["token"], summary="Pending ORACLE burns (public transparency)")
def api_burn_pending():
    """Pending ORACLE burn queue — both pay-with-token receipts and weekly
    revenue commitments. Public-readable: every burn the protocol owes,
    visible before execution. The receipts brand applied to token mechanics."""
    try:
        import token_burn
        return token_burn.summary_pending()
    except Exception as e:
        raise HTTPException(503, detail={"error": "burn_pending_failed", "detail": str(e)})


@app.get("/api/burn/history", tags=["token"], summary="Executed ORACLE burns (public history)")
def api_burn_history(limit: int = 50):
    """History of executed ORACLE burns with on-chain tx signatures —
    pay-with-token receipts and weekly revenue buy+burns. Public-readable:
    every burn happened, with the tx anyone can verify."""
    try:
        import token_burn
        limit = max(1, min(int(limit or 50), 500))
        return {"count": None, "burns": token_burn.history(limit=limit)}
    except Exception as e:
        raise HTTPException(503, detail={"error": "burn_history_failed", "detail": str(e)})


@app.get("/api/verdict", tags=["calibration"], summary="Locked pre-registered verdict snapshot (public, machine-readable)")
def api_verdict():
    """Machine-readable snapshot of the 2026-06-02 Branch A verdict on the
    3-tier composite signal — for terminals embedding our 'verified' badge.

    Public (no auth) by design: this is the receipts-moat payload anyone
    integrating us would want to display next to the signal. Pulled from
    the immutable prereg_verdicts row on /data so the response is exactly
    what was ratified, not a re-computation."""
    try:
        c = sqlite3.connect(db.DB_PATH, timeout=5)
        try:
            c.row_factory = sqlite3.Row
            row = c.execute(
                "SELECT prereg_name, ratified_at, branch, ratified_by, "
                "evaluation_sha256, payload_json FROM prereg_verdicts "
                "WHERE prereg_name = ?",
                ("composite_tier_prereg_v2_2026-05-18",),
            ).fetchone()
        finally:
            c.close()
        if not row:
            raise HTTPException(404, detail={"error": "no_verdict_recorded"})
        payload = json.loads(row["payload_json"])
        return {
            "prereg_name":       row["prereg_name"],
            "branch":            row["branch"],
            "ratified_at":       row["ratified_at"],
            "ratified_by":       row["ratified_by"],
            "evaluation_sha256": row["evaluation_sha256"],
            "prereg_sha256":     payload.get("prereg_sha256"),
            "t0_unix":           payload.get("t0_unix"),
            "per_tier":          payload.get("per_tier"),
            "criteria":          payload.get("criteria"),
            "n_total_resolved":  payload.get("n_total_resolved"),
            "_doc": ("Verify by recomputing sha256 of the pre-reg doc "
                     "(delete final SHA-256 line, sha256sum the rest). "
                     "Must equal prereg_sha256. Full story: /verdict"),
            "_human_url": "https://graduate-oracle.fly.dev/verdict",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(503, detail={"error": "verdict_query_failed", "detail": str(e)})


@app.get("/api/status", tags=["status"], summary="System status JSON")
def api_status():
    """Live system health: uptime, recent /api/live latency, daemon
    heartbeats. Polled every 10s by /status. No auth — public artifact."""
    out = status_module.get_status()
    # GBM shadow scoring health (retrain v1 dual-write window). External
    # monitoring + this handler watch model_loaded + consecutive_load_failures
    # to verify dual-write is alive without querying predictions table.
    try:
        out["gbm_shadow"] = gbm_shadow.stats_snapshot()
    except Exception:
        pass
    # Bucket-cutoff state (HIGH/MED/LOW boundaries). Surfaces computed_age_s
    # so monitoring can detect a stalled rebuilder.
    try:
        out["bucket_cutoffs"] = bucket_cutoffs.snapshot()
    except Exception:
        pass
    # Active acceptance gates (Finding 7/8 chain, 2026-05-07).
    # The dashboard banner reads this list and renders if non-empty.
    # Each entry: {name, summary, since, expected_close, doc_url}.
    out["acceptance_gates"] = _acceptance_gates()
    return out


def _acceptance_gates() -> list[dict]:
    """Return the list of currently-active pre-registered acceptance gates.
    Drives the dashboard banner + the /status acceptance-gates panel.

    Each gate is a dict the frontend can render generically. When a gate's
    expected_close passes OR the gate's escalation triggers, the entry is
    removed from this list (handled inline below as the gates resolve).
    """
    gates: list[dict] = []
    # Finding 8 — bucket calibration interim TG re-enable gate (48h)
    finding_8_deploy = 1778169954  # 2026-05-07T16:45:54Z
    interim_close = finding_8_deploy + 48 * 3600
    full_close = finding_8_deploy + 24 * 3600 + 7 * 86400
    now = int(time.time())
    if now < full_close:
        # Path E (Finding 8 sub-branch b, pre-registered 2026-05-10 commit
        # 4d56f53): when bucket_cutoffs is in fixed_percentile_raw_gbm mode,
        # the gate's interim/full criteria switch to the Path E acceptance
        # criterion (rolling-7d MED in [21,210], no zero-MED 24h sub-window).
        #
        # Anchor (methodology integrity): the T+24h interim and T+7d full
        # acceptance windows are anchored to the FIRST Path E deploy
        # timestamp (commit 147777d deploy receipt: 2026-05-10T06:55:28Z =
        # 1778396128 epoch). Subsequent fly deploys (e.g., the case_study
        # harness bug fix at commit 51a409f) recompute Path E cutoffs but
        # MUST NOT shift the verification anchor. Hardcoded as a constant
        # rather than reading bucket_cutoffs._state["computed_at"] (which
        # resets on every restart). Documented in
        # docs/research/case_study_01_harness_bug_postmortem.md § Pre-reg
        # Amendment 02.
        PATH_E_INITIAL_DEPLOY_TS = 1778396128
        try:
            _bc = bucket_cutoffs.snapshot()
            _path_e_active = (_bc.get("bucket_logic_mode") == "fixed_percentile_raw_gbm")
            _path_e_deploy_ts = PATH_E_INITIAL_DEPLOY_TS if _path_e_active else None
        except Exception:
            _path_e_active = False
            _path_e_deploy_ts = None

        if _path_e_active and _path_e_deploy_ts:
            path_e_t24h = _path_e_deploy_ts + 24 * 3600
            path_e_t7d  = _path_e_deploy_ts + 7 * 86400
            gates.append({
                "id":                "finding_8_bucket_calibration",
                "name":              "Bucket calibration acceptance — Path E",
                "summary":           (
                    f"Path E shipped (fixed-percentile cutoffs on raw GBM, "
                    f"99.5p MED + 99.9p HIGH over 48h window). T+24h interim "
                    f"check (MED ≥10) at {path_e_t24h}; T+7d acceptance "
                    f"(rolling-7d MED in [21,210] AND no zero-MED 24h "
                    f"sub-window) at {path_e_t7d}."
                ),
                "deployed_at":       finding_8_deploy,
                "interim_verdict":   "5B_pass_fail",
                "path_e_deploy_ts":  _path_e_deploy_ts,
                "path_e_interim_close": path_e_t24h if now < path_e_t24h else None,
                "path_e_full_close": path_e_t7d if now < path_e_t7d else None,
                "doc_url":           "https://github.com/Based-LTD/graduate-oracle/blob/main/docs/research/finding_8_path_e_pre_registration.md",
            })
        else:
            gates.append({
                "id":              "finding_8_bucket_calibration",
                "name":            "Bucket calibration acceptance",
                "summary":         (
                    "Variant 5B fired at interim verdict (2026-05-09T16:45Z): "
                    "EMA-fix gate PASS (max 1h MED=0, rebuild_failures=0); "
                    "alert-volume gate FAIL (0 MED in 48h, 0 HIGH, 4305 LOW). "
                    "Rules 9+10 stay disabled. Path E pre-registered "
                    "(commit 4d56f53); awaiting deploy."
                ),
                "deployed_at":     finding_8_deploy,
                "interim_close":   interim_close if now < interim_close else None,
                "interim_verdict": "5B_pass_fail",
                "full_close":      full_close,
                "doc_url":         "https://github.com/Based-LTD/graduate-oracle/blob/main/docs/research/bucket_calibration_aliasing.md",
            })
    # Finding 7f sustain auto-lift gate removed: feature was permanently
    # sunset 2026-05-08 (Finding 7i, commit 7658639) after three model-class
    # attempts all failed pre-registered acceptance. No further verification.
    # Sustain-related context surfaces via /api/scope.predictions.post_grad_survival_prob
    # which already documents the permanent sunset; no acceptance gate is active.
    return gates


@app.get("/api/sol_price", tags=["status"], summary="Live SOL/USD price (5-min cached)")
def api_sol_price():
    """Cached Jupiter SOL→USD quote, used by the marketing page to render
    dollar-equivalent prices that don't go stale when SOL moves. Refreshes
    server-side every 5 minutes; clients can poll freely."""
    import jupiter_price, sol_pay
    p = jupiter_price.get_sol_usd()
    return {
        "sol_usd": p,
        "computed_at": int(time.time()),
        # Marketing page reads these to gate the paid CTAs. Single source of
        # truth is sol_pay.PURCHASING_OPEN — flip there to open/close store.
        "purchasing_open": bool(sol_pay.PURCHASING_OPEN),
        "intro_price": sol_pay.INTRO_PRICE,
    }


# ── WebSocket firehose · /api/v1/ws ─────────────────────────────────────────
# Pro-tier (or trial during launch week). Auth via ?api_key=... (browsers
# can't easily set headers on the initial WS handshake, so query-string is
# the practical default). Frame shape:
#   {"kind":"live_update","ts":...,"snapshot_epoch_ms":...,"count":N,"mints":[...]}
# pushed once per observer snapshot tick (~5-10s cadence). On connect, an
# immediate hello frame {"kind":"hello","tier":<actual>} confirms the link.
#
# Connection caps protect the server from anonymous-trial abuse:
#  - WS_MAX_GLOBAL_CLIENTS: total simultaneous connections (default 200)
#  - WS_MAX_PER_IP_CLIENTS: per-IP cap (default 5)
# Both are env-tunable so we can lift them once we've watched real load.
import os as _ws_os
import threading as _ws_threading
WS_MAX_GLOBAL_CLIENTS = int(_ws_os.environ.get("WS_MAX_GLOBAL_CLIENTS", "200"))
WS_MAX_PER_IP_CLIENTS = int(_ws_os.environ.get("WS_MAX_PER_IP_CLIENTS", "5"))
_ws_global_count: int = 0
_ws_per_ip_count: dict = {}
_ws_count_lock = _ws_threading.Lock()


def _ws_admit(ip: str) -> tuple[bool, str]:
    """Decide whether to admit a new WS connection. Returns (allowed, reason).
    Increments counters on admit; remember to call _ws_release on disconnect."""
    global _ws_global_count
    with _ws_count_lock:
        if _ws_global_count >= WS_MAX_GLOBAL_CLIENTS:
            return False, "global_cap"
        if _ws_per_ip_count.get(ip, 0) >= WS_MAX_PER_IP_CLIENTS:
            return False, "per_ip_cap"
        _ws_global_count += 1
        _ws_per_ip_count[ip] = _ws_per_ip_count.get(ip, 0) + 1
        return True, "ok"


def _ws_release(ip: str) -> None:
    global _ws_global_count
    with _ws_count_lock:
        _ws_global_count = max(0, _ws_global_count - 1)
        n = _ws_per_ip_count.get(ip, 0) - 1
        if n <= 0:
            _ws_per_ip_count.pop(ip, None)
        else:
            _ws_per_ip_count[ip] = n


@app.websocket("/api/v1/ws")
async def ws_firehose(websocket: WebSocket):
    import asyncio
    import json as _json
    from auth import _free_trial_active, _FREE_TRIAL_SYNTHETIC
    api_key = websocket.query_params.get("api_key") or ""
    record = db.lookup_key(api_key.strip()) if api_key else None
    # Launch-week free trial: open the WebSocket firehose to anonymous /
    # invalid-key connections during the FREE_TRIAL_UNTIL window. Pure
    # signal-side promotion — bot operators who can capitalize get the
    # fastest path we offer, no paywall, no friction. After 2026-06-22 the
    # trial expires automatically and pro-tier check snaps back.
    if not record:
        if _free_trial_active():
            record = _FREE_TRIAL_SYNTHETIC
        else:
            await websocket.close(code=4401)  # custom: unauthorized
            return
    if record["tier"] not in ("pro", "free_trial"):
        await websocket.close(code=4403)  # custom: payment required
        return

    # Caller IP (Fly forwards via X-Forwarded-For; fall back to direct host).
    ip = (
        websocket.headers.get("fly-client-ip")
        or websocket.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or (websocket.client.host if websocket.client else "unknown")
    )
    admitted, reason = _ws_admit(ip)
    if not admitted:
        # 1013 = "try again later" — semantically right for capacity issues.
        await websocket.close(code=1013, reason=reason)
        return

    await websocket.accept()
    q: asyncio.Queue = asyncio.Queue(maxsize=wsfanout.QUEUE_MAXSIZE)
    wsfanout.add_client(q)

    try:
        # Hello frame — honest tier so trial users know what they're on.
        hello = {
            "kind": "hello",
            "tier": record["tier"],
            "push_cadence_s": "~5-10",
            "note": "pushes arrive within 2s of each observer snapshot tick; observer cadence is 5-10s. Initial state lives at /api/v1/live (poll once on connect).",
        }
        await websocket.send_text(_json.dumps(hello, separators=(",", ":")))
        while True:
            payload = await q.get()
            await websocket.send_text(payload)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[ws] client error: {e}", flush=True)
    finally:
        wsfanout.remove_client(q)
        _ws_release(ip)


@app.post("/api/upgrade", tags=["payments"], summary="Create a SOL payment intent")
def create_upgrade_intent(payload: dict):
    """
    One-shot signup. If no existing API key is supplied, this endpoint will
    issue a fresh one and link the payment intent to it — no two-step "get
    free key, then upgrade" flow needed.

    Body: {
      "tier": "pro",                           // default "pro"
      "plan": "monthly" | "yearly",            // default "monthly"
      "key": "<existing api key>",             // optional · upgrades that key
      "email": "you@example.com",              // optional · for renewal reminders
      "telegram_id": <int>                     // optional · binds to a TG user
    }

    Returns memo + amount + Phantom deeplinks AND (if a new key was minted)
    the plaintext key shown ONCE. Send SOL with the memo to auto-activate.
    """
    # Launch-week free trial short-circuit. If FREE_TRIAL_UNTIL is open,
    # users don't need to sign up or pay — the API is fully free with no
    # key required. Return a "not_open" status (which the goracle CLI
    # already prints cleanly) with a message telling them exactly what
    # to do instead. This unblocks any user hitting `goracle signup` on
    # the OLD CLI without forcing a re-publish.
    import os as _os
    try:
        _ft_until = int(_os.environ.get("FREE_TRIAL_UNTIL", "0").strip() or 0)
    except ValueError:
        _ft_until = 0
    if _ft_until > int(time.time()):
        return {
            "status": "not_open",
            "message": (
                "🚀 Launch promo is live — no signup or payment needed.\n\n"
                "The API is fully free during the promo window. Just call any "
                "endpoint with no key:\n\n"
                "  curl https://graduateoracle.fun/api/v1/runners?tier=5x&min_prob=0.20\n\n"
                "  curl https://graduateoracle.fun/api/v1/live\n\n"
                "When the promo ends, run `npx goracle signup` again to lock "
                "the founding rate. Until then you don't need to do anything."
            ),
            "free_trial_active": True,
            "purchasable": False,
        }

    # Purchasing kill-switch (2026-05-17) — checked BEFORE any key is
    # minted, so a closed state can't orphan a free key. Honest 200
    # payload, not a 500/400. Single source of truth: sol_pay.PURCHASING_OPEN.
    if not getattr(sol_pay, "PURCHASING_OPEN", False):
        return {
            "status": "not_open",
            "message": sol_pay.opening_soon_msg(),
            "intro_price": sol_pay.INTRO_PRICE,
            "purchasable": False,
        }
    payload = payload or {}
    tier = (payload.get("tier") or "pro").strip()
    plan = (payload.get("plan") or "monthly").strip()
    raw_key = (payload.get("key") or "").strip()
    email = (payload.get("email") or "").strip().lower() or None
    tg_id = payload.get("telegram_id")

    new_key_record = None
    key_id: Optional[int] = None
    if raw_key:
        rec = db.lookup_key(raw_key)
        if rec:
            key_id = rec["id"]
    # No existing key + no Telegram link = mint a fresh key now and bind the
    # intent to it. Avoids the prior "orphan intent" bug where a payment
    # without an attached identity was silently dropped on fulfillment.
    if key_id is None and not tg_id:
        new_key_record = db.create_key(
            email=email, tier="free", expires_in_days=365,
            label="upgrade-flow",
        )
        key_id = new_key_record["id"]

    try:
        intent = sol_pay.create_intent(
            tier=tier, plan=plan, key_id=key_id,
            telegram_id=int(tg_id) if tg_id else None,
        )
    except sol_pay.PurchasingClosed:
        return {
            "status": "not_open",
            "message": sol_pay.opening_soon_msg(),
            "intro_price": sol_pay.INTRO_PRICE,
            "purchasable": False,
        }
    except ValueError as e:
        raise HTTPException(400, detail={"error": str(e)})

    if new_key_record:
        intent["new_key"] = new_key_record["key"]   # plaintext, shown once
        intent["new_key_prefix"] = new_key_record["prefix"]
        intent["new_key_warning"] = (
            "Save this key now — we don't keep a copy. "
            "It will auto-upgrade to Pro the moment your SOL payment confirms (~30s)."
        )
    return intent


@app.get("/api/upgrade/{memo}", tags=["payments"], summary="Check intent status")
def check_intent(memo: str):
    intent = sol_pay.lookup_intent(memo)
    if not intent:
        raise HTTPException(404, detail={"error": "intent not found"})
    return {
        "memo": intent["memo"],
        "tier": intent["tier"],
        "plan": intent["plan"],
        "amount_sol": intent["amount_sol"],
        "fulfilled": bool(intent["fulfilled"]),
        "tx_signature": intent.get("tx_signature"),
        "expires_at": intent["expires_at"],
        "fulfilled_at": intent.get("fulfilled_at"),
    }


# ── goracle CLI wallet-link flow (token-holder signup) ────────────────────
@app.post("/api/signup/wallet/init", tags=["payments"], summary="Start a CLI token-holder signup")
def signup_wallet_init(payload: dict):
    """CLI POSTs {wallet, tier} to start a wallet-link signup. Server stores
    a pending link, returns the browser_url + the exact message to sign.

    The wallet is NOT touched yet — no balance check, no key minted. That
    all happens when the browser submits the signature."""
    # Short-circuit during the launch free-trial window. The CLI's
    # signupWithWallet path doesn't handle a "status: not_open" payload
    # (only the SOL-pay path does), so we surface the free-trial reason as
    # a 400 with a clear message — the CLI prints `err.message` verbatim.
    import os as _os
    try:
        _ft_until = int(_os.environ.get("FREE_TRIAL_UNTIL", "0").strip() or 0)
    except ValueError:
        _ft_until = 0
    if _ft_until > int(time.time()):
        raise HTTPException(400, detail={"error": (
            "Launch promo is live — no signup needed. The API is fully free "
            "right now with no key. Try: curl https://graduateoracle.fun/api/v1/runners"
            "?tier=5x&min_prob=0.20  ·  Re-run goracle signup after the promo ends "
            "to lock the founding rate."
        )})
    payload = payload or {}
    wallet = (payload.get("wallet") or "").strip()
    tier   = (payload.get("tier") or "builder").strip()
    try:
        return cli_wallet_link.create_link(wallet=wallet, tier=tier)
    except ValueError as e:
        raise HTTPException(400, detail={"error": str(e)})


@app.post("/api/signup/wallet/sign", tags=["payments"], summary="Submit signature for a CLI wallet-link")
def signup_wallet_sign(payload: dict):
    """Browser POSTs {link_id, signature} after Phantom signMessage. Server
    verifies, checks $GO balance, mints a key if eligible. Returns the
    resolved status; the plaintext key is NOT returned here — the CLI
    fetches it via the polling endpoint."""
    payload = payload or {}
    link_id  = (payload.get("link_id") or "").strip()
    sig_b58  = (payload.get("signature") or "").strip()
    if not link_id or not sig_b58:
        raise HTTPException(400, detail={"error": "link_id and signature required"})
    return cli_wallet_link.submit_signature(link_id, sig_b58)


@app.get("/api/signup/wallet/{link_id}", tags=["payments"], summary="Poll CLI wallet-link status")
def signup_wallet_poll(link_id: str):
    """CLI polls every 2s. Returns the link status; on `fulfilled` includes
    the plaintext API key (visible for 5 min after fulfillment, then nulled
    from the response — the key remains valid forever, the response just
    stops returning the plaintext)."""
    out = cli_wallet_link.poll_link(link_id)
    if out.get("status") == "not_found":
        raise HTTPException(404, detail={"error": "link not found"})
    return out


@app.get("/cli-link/{link_id}", response_class=HTMLResponse, include_in_schema=False)
def cli_link_page(link_id: str):
    """Browser handoff page for goracle CLI's --wallet signup. Loads the
    pending link, asks Phantom to sign the message, posts the signature."""
    return _serve_html("cli_link.html")


@app.post("/api/keys/new", tags=["keys"], summary="Disabled — paid-only service (2026-06-11)")
def issue_free_key(payload: Optional[dict] = None):
    """2026-06-11: free signups disabled. graduate-oracle is a paid product;
    keys are issued on payment confirmation via /api/upgrade. Returns 402."""
    raise HTTPException(
        status_code=402,
        detail={
            "error": "paid_service",
            "message": "graduate-oracle is a paid product. There is no free tier. "
                       "To get a key, pay for a subscription at /api/upgrade.",
            "upgrade_url": "/api",
        },
    )


@app.get("/api/me", tags=["keys"], summary="Inspect your current API key")
def me(request: Request, _key=Depends(api_v1.require_api_key)):
    rec = request.state.api_key
    info = request.state.quota_info
    return {
        "key_prefix": rec["key_prefix"],
        "tier": rec["tier"],
        "email": rec["email"],
        "telegram_id": rec["telegram_id"],
        "wallet": rec["wallet"],
        "created_at": rec["created_at"],
        "expires_at": rec["expires_at"],
        "tier_limits": db.TIERS[rec["tier"]],
        "today": info,
    }


@app.get("/", response_class=HTMLResponse)
def index():
    """Landing page is the LIVE demo — watch the bot work in real time.
    Replaces the prior static dashboard, which now lives at /about."""
    return _serve_html("demo.html")


@app.get("/about", response_class=HTMLResponse, include_in_schema=False)
def about_page():
    """Prior landing — copy-led value prop, tier runways, narrative. Kept
    available at /about for users who want the deeper explainer."""
    return _serve_html("index.html")


if __name__ == "__main__":
    import uvicorn
    # Bind 0.0.0.0 so Fly.io / any container proxy can reach us. Locally this
    # is also fine (just makes the port reachable on the LAN — harmless).
    uvicorn.run(app, host="0.0.0.0", port=8765, log_level="info")
