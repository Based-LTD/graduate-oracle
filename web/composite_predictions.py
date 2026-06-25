"""Composite-receipts logging — tamper-evident predictions for the dashboard's
hot-launch composite signal (smart_money_in × max_mult × freshness + MC floor).

Per pre-registration `docs/research/composite_receipts_logging_prereg.md`
(graduate-oracle public mirror) + user direction 2026-05-11. The composite
signal is the user-facing product surface (per `project_dual_track_signal_strategy.md`)
and ongoing observation shows it producing high-hit-rate catches. Without
this module, those catches live only in conversation logs; with it, they
accumulate as audit-grade rows with the same tamper-evident receipts
discipline that grad_prob predictions enjoy (per `web/ledger.py`).

This module is the COMPOSITE TRACK's analog of `web/predictions.py` +
`web/ledger.py` for the grad_prob track. The two ledgers are independent
— grad_prob remains at V3 unchanged; composite is its own row type with
its own leaf format starting at V1.

Methodology (frozen pre-data, per pre-reg):
  - Composite formula: smart_money_in * max_mult * (1 / (1 + age_s/600))
    (matches `web/static/app.js:683` and `case_study_01_amendment_02_*`)
  - Threshold: top 10% of composite_score over rolling 24h sample
    (P90; cold-start P95 until 24h of samples accumulate)
  - MC floor: $5,000 USD (configurable; pre-registered for Audit 12)
  - Dedupe: one row per mint, FIRST cross only — subsequent crosses
    ignored even if the mint dropped below threshold and re-crossed
  - Outcome resolution: at 24h post-cross, JOIN to existing tables
    (predictions.actual_graduated, predictions.actual_max_mult,
    post_grad_outcomes.sustained_30m) — no new outcome daemon needed

Wallet-redaction compatibility: composite rows carry only aggregate
counts + per-mint scalars + outcome booleans. No wallet addresses
anywhere. Compatible with Option A + Option 5 redactions.
"""
import contextlib
import hashlib
import json
import sqlite3
import threading
import time
from collections import deque
from typing import Optional

import db


# ─── Configuration (frozen per pre-reg) ─────────────────────────────────────

# Cross-detection threshold. P90 of rolling 24h composite_score sample.
# Cold-start: use P95 of available samples until 24h elapses (more
# conservative — fewer false-positive crosses while data accumulates).
COMPOSITE_PERCENTILE_TARGET = 90.0     # P90 after warmup
COMPOSITE_PERCENTILE_COLD   = 95.0     # P95 during warmup
ROLLING_WINDOW_S            = 24 * 3600
WARMUP_DURATION_S           = 24 * 3600

# EMERGENCY MITIGATION (2026-05-12T05:00Z, postmortem 005): the original
# unbounded deque + 24h rolling window blew up to ~22M entries (~3 GB
# Python heap) on production at 258 samples/sec ingest rate, starving
# memory and slowing EVERY scoring stage proportionally. Hard cap on
# deque size + 1-in-N subsample fixes the memory issue with no
# methodology change to the P90/P95 percentile semantics — 50k samples
# is still 100x oversampled for a 90th-percentile estimate. See
# docs/research/composite_receipts_memory_postmortem_2026_05_12.md.
SAMPLE_MAXLEN               = 50_000   # hard cap; deque maxlen evicts oldest on overflow
SAMPLE_SUBSAMPLE_EVERY      = 10       # only every Nth call adds to the rolling sample

# Market-cap floor (USD). Mints below this floor are not eligible to
# cross regardless of composite_score. Pre-registered per Audit 12.
MC_FLOOR_USD                = 5000.0

# Outcome resolution window. At T+24h post-cross, the outcome resolver
# joins to predictions + post_grad_outcomes to populate did_graduate /
# peak_mult_24h / did_sustain_30m. Outcomes for crosses where 24h hasn't
# yet elapsed stay NULL.
OUTCOME_GRACE_S             = 24 * 3600

# Sustain threshold (binary). 1 if 30min-post-grad vSOL >= 80% of grad-
# time vSOL, else 0. Reused from `post_grad_tracker.SURVIVAL_THRESHOLD`
# semantics (post_grad_outcomes.sustained_30m is already binary; we just
# read it directly).
# No constant needed locally; resolver reads sustained_30m directly.


# ─── Schema migration ───────────────────────────────────────────────────────

_SCHEMA_LOCK = threading.Lock()
_SCHEMA_INITIALIZED = False


def _ensure_schema(c: sqlite3.Connection) -> None:
    """Initialize composite_predictions + composite_prediction_commits tables.
    Idempotent. Same forward-motion convention as web/predictions.py — ALTER
    swallows duplicate-column errors."""
    global _SCHEMA_INITIALIZED
    if _SCHEMA_INITIALIZED:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_INITIALIZED:
            return
        c.execute("""
            CREATE TABLE IF NOT EXISTS composite_predictions (
                mint                  TEXT NOT NULL,
                predicted_at          INTEGER NOT NULL,
                composite_score       REAL NOT NULL,
                threshold_at_cross    REAL NOT NULL,
                smart_money_in        INTEGER NOT NULL,
                max_mult_at_cross     REAL NOT NULL,
                age_s_at_cross        INTEGER NOT NULL,
                mc_at_cross_usd       REAL NOT NULL,
                -- Outcome columns (populated by resolver at T+24h)
                outcome_resolved_at   INTEGER,
                did_graduate          INTEGER,
                peak_mult_24h         REAL,
                did_sustain_30m       INTEGER,
                PRIMARY KEY (mint)
            )
        """)
        # TG push dedup + audit columns. NULL tg_pushed_at = not yet
        # evaluated for push. tg_tier ∈ {'ACT','WATCH','below_threshold',
        # 'expired'} once evaluated. The composite_predictions row is the
        # audit trail; tg_pushed_at marks when the push decision was made
        # (regardless of whether it was sent), tg_tier records the verdict.
        for stmt in (
            "ALTER TABLE composite_predictions ADD COLUMN tg_pushed_at INTEGER",
            "ALTER TABLE composite_predictions ADD COLUMN tg_tier TEXT",
            "ALTER TABLE composite_predictions ADD COLUMN tier_logic_version TEXT",
        ):
            try:
                c.execute(stmt)
            except sqlite3.OperationalError:
                pass  # already exists
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_composite_tg_unpushed
                ON composite_predictions(predicted_at)
             WHERE tg_pushed_at IS NULL
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_composite_pred_at
                ON composite_predictions(predicted_at)
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_composite_outcome_unresolved
                ON composite_predictions(outcome_resolved_at)
                WHERE outcome_resolved_at IS NULL
        """)
        # Composite-ledger commits — parallel structure to prediction_commits.
        c.execute("""
            CREATE TABLE IF NOT EXISTS composite_prediction_commits (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                period_start      INTEGER NOT NULL,
                period_end        INTEGER NOT NULL,
                merkle_root_hex   TEXT NOT NULL,
                n_crosses         INTEGER NOT NULL,
                first_pred_ts     INTEGER,
                last_pred_ts      INTEGER,
                computed_at       INTEGER NOT NULL,
                leaf_version      INTEGER NOT NULL DEFAULT 1,
                UNIQUE (period_start, period_end)
            )
        """)
        _SCHEMA_INITIALIZED = True


# ─── Rolling composite sample (P90/P95 threshold computation) ───────────────

_sample_lock = threading.Lock()
# Hard cap via maxlen — deque auto-evicts oldest on overflow. This is
# the memory-safety floor: even if ingest rate spikes, the deque never
# exceeds SAMPLE_MAXLEN entries. The time-based trim below is
# belt-and-suspenders for normal operation.
_sample: deque = deque(maxlen=SAMPLE_MAXLEN)  # entries: (ts, composite_score)
_daemon_boot_ts: Optional[int] = None  # set on first add
_add_call_counter = 0  # for SAMPLE_SUBSAMPLE_EVERY


def _add_sample(ts: int, composite_score: float) -> None:
    """Add a composite_score observation to the rolling-24h sample.
    Subsamples 1-in-SAMPLE_SUBSAMPLE_EVERY calls (memory mitigation per
    postmortem 005). Hard cap via deque maxlen. Time-based trim is a
    secondary defense — if subsample rate is high enough that the deque
    fills before maxlen evicts, the cap kicks in first."""
    global _daemon_boot_ts, _add_call_counter
    with _sample_lock:
        if _daemon_boot_ts is None:
            _daemon_boot_ts = ts
        _add_call_counter += 1
        if _add_call_counter % SAMPLE_SUBSAMPLE_EVERY != 0:
            return  # subsample skip
        _sample.append((ts, composite_score))
        cutoff = ts - ROLLING_WINDOW_S
        # maxlen handles the unbounded-growth case; this trim handles
        # the case where the deque has older-than-window entries from
        # a low-ingest period. Bounded by min(maxlen, time-window count).
        while _sample and _sample[0][0] < cutoff:
            _sample.popleft()


def _current_threshold(now: int) -> Optional[float]:
    """Compute the current composite_score threshold. P90 of 24h rolling
    sample, OR P95 during warmup (first 24h after daemon boot)."""
    with _sample_lock:
        if not _sample:
            return None
        if _daemon_boot_ts is None:
            return None
        warmup = (now - _daemon_boot_ts) < WARMUP_DURATION_S
        target = COMPOSITE_PERCENTILE_COLD if warmup else COMPOSITE_PERCENTILE_TARGET
        scores = sorted(s for _, s in _sample)
        # MIN_SAMPLES gate — don't fire crosses on a 1-sample distribution.
        # Per pre-reg: require >= 30 samples accumulated before any cross
        # fires. Below that the percentile estimate is too noisy.
        if len(scores) < 30:
            return None
        k = int(target / 100.0 * (len(scores) - 1))
        return float(scores[k])


def snapshot() -> dict:
    """Surface current daemon state for /api/status and operator visibility."""
    with _sample_lock:
        n = len(_sample)
        boot = _daemon_boot_ts
    now = int(time.time())
    warmup = boot is None or (now - boot) < WARMUP_DURATION_S
    return {
        "n_samples":                n,
        "warmup":                   warmup,
        "warmup_target":            COMPOSITE_PERCENTILE_COLD,
        "live_target":              COMPOSITE_PERCENTILE_TARGET,
        "current_threshold":        _current_threshold(now),
        "mc_floor_usd":             MC_FLOOR_USD,
        "outcome_grace_s":          OUTCOME_GRACE_S,
        "rolling_window_s":         ROLLING_WINDOW_S,
        "daemon_boot_ts":           boot,
    }


# ─── Cross detection (called per snapshot tick) ─────────────────────────────

def _compute_composite(m: dict) -> Optional[float]:
    """Compute composite_score for a single mint. Returns None if any
    input is missing. Matches `web/static/app.js:683` formula exactly:
        smart_money_in * max_mult * (1 / (1 + age_s/600))
    """
    sm = m.get("smart_money_in")
    mult = m.get("max_mult")
    age = m.get("age_s")
    if sm is None or mult is None or age is None:
        return None
    try:
        sm_f = float(sm)
        mult_f = float(mult) if mult is not None else 1.0
        if mult_f <= 0: mult_f = 1.0
        age_f = max(1.0, float(age))
        freshness = 1.0 / (1.0 + age_f / 600.0)
        return sm_f * mult_f * freshness
    except (TypeError, ValueError):
        return None


def maybe_log_crossings(enriched_mints: list[dict]) -> None:
    """Inspect a batch of enriched mint dicts (one snapshot tick's worth).
    For each mint, compute composite_score; update the rolling sample; if
    composite_score >= current threshold AND mc_usd >= floor AND mint is
    not already in composite_predictions, INSERT a row.

    Postmortem 005 (2026-05-12) restructure: sqlite is only touched when
    we have actual crosses to insert. The hot path (in-memory rolling-
    sample update + threshold compute) does NOT open a sqlite connection,
    avoiding ~1289 connection-acquire cycles per tick + their associated
    sqlite write-lock contention with other writers (predictions drain
    thread, post_grad_tracker, etc.).

    Idempotent: PRIMARY KEY (mint) ensures dedupe — INSERT OR IGNORE skips
    re-crossing mints. The cross is anchored to the FIRST observation
    above threshold per pre-reg dedup rule.

    Never raises. Wallet-redaction safe — only aggregate counts + scalars
    written; no wallet addresses.
    """
    if not enriched_mints:
        return
    now = int(time.time())
    # Hot path — in-memory only. Update rolling sample + compute
    # threshold. No sqlite. Return early if no threshold available
    # (warmup) or no mints meet criteria.
    composites: list[tuple] = []  # (mint, cs, m) for cross-eligible mints
    for m in enriched_mints:
        cs = _compute_composite(m)
        if cs is None:
            continue
        _add_sample(now, cs)
        composites.append((m.get("mint"), cs, m))
    threshold = _current_threshold(now)
    if threshold is None:
        return  # warmup / insufficient samples
    # Identify crossers (above threshold + above MC floor + has mint id).
    # Per-mint try/except so one bad row can't take out the whole batch.
    # (Lesson reinforced from case_study_harness silent-enrichment-bug and
    # Audit 12-B's feature_unique_buyers postmortem: silent-failure-via-
    # broad-except patterns surface from real production data shapes that
    # the original implementation didn't anticipate.)
    crosses_to_insert: list[tuple] = []
    for mint, cs, m in composites:
        try:
            if not mint or cs < threshold:
                continue
            # market_cap is a DICT in the live snapshot:
            #   {"sol": <float>, "usd": <float>, "sol_usd": <float>}
            # NOT a scalar. Extract USD value defensively, handling both
            # the dict shape and any legacy scalar shape.
            mc_raw = m.get("market_cap")
            if isinstance(mc_raw, dict):
                mc_usd = mc_raw.get("usd")
            elif isinstance(mc_raw, (int, float)):
                mc_usd = mc_raw
            else:
                mc_usd = None
            if mc_usd is None:
                continue
            try:
                mc_usd_f = float(mc_usd)
            except (TypeError, ValueError):
                continue
            if mc_usd_f < MC_FLOOR_USD:
                continue
            crosses_to_insert.append((
                mint, now, float(cs), float(threshold),
                int(m.get("smart_money_in") or 0),
                float(m.get("max_mult") or 1.0),
                int(m.get("age_s") or 0),
                mc_usd_f,
            ))
        except Exception as e:
            print(f"[composite_predictions] cross-build skipped for "
                  f"mint={(mint or '?')[:14]}..: {e}", flush=True)
            continue
    if not crosses_to_insert:
        return
    # Only touch sqlite when we have crosses to write. Connection open +
    # write happens at most a few times per day at projected ~5-15
    # crosses/day rate, NOT every tick.
    inserted: list[tuple] = []  # rows that ACTUALLY inserted (not IGNOREd)
    try:
        with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=10)) as c, c:
            _ensure_schema(c)
            for row in crosses_to_insert:
                cur = c.execute("""
                    INSERT OR IGNORE INTO composite_predictions
                        (mint, predicted_at, composite_score, threshold_at_cross,
                         smart_money_in, max_mult_at_cross, age_s_at_cross,
                         mc_at_cross_usd)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, row)
                if cur.rowcount > 0:
                    inserted.append(row)
    except Exception as e:
        print(f"[composite_predictions] cross-log failed: {e}", flush=True)
        return
    # TG push is now deferred — see evaluate_tg_pushes() called every tick.
    # The cross is committed to composite_predictions immediately (audit
    # trail preserved); the TG decision waits for grad_prob_60 to land so
    # the two-tier filter (ACT / WATCH) has its quality input.


# ─── TG push evaluator (two-tier ACT/WATCH on grad_prob_60) ────────────────
#
# Called every tick from main.py. Sweeps recent composite_predictions rows
# that haven't been TG-evaluated yet (tg_pushed_at IS NULL), looks up
# grad_prob_60 from predictions table, classifies tier, pushes ACT/WATCH
# fires, expires stale ones. Two-tier filter is derived from the back-test
# on 428 resolved crosses (2026-05-14):
#
#   ACT   tier: grad_prob_60 >= 0.25 → 75% grad rate, ~10 fires/day
#   WATCH tier: grad_prob_60 in [0.10, 0.25) → 28% grad rate, ~4 fires/day
#
# Both anchored in composite cross (gives the population), grad_prob_60 is
# the quality gate. Composite-only is ~13% grad rate (1.3× lift); pairing
# with grad_prob_60 ≥ 0.25 boosts to 75% (2.4× the all-mints rate at same
# gp_60 tier). The two signals are independently predictive.

# 3-tier ML-gradient structure — frozen in docs/research/two_tier_retune_prereg.md
# AMENDMENT 01. bestgp = max(gp60, gp30). Bands stratify the ML score
# gradient; the composite_strong OR-arm is the only non-ML rule (the sole
# fix for gp-less late-crossers — the retired 'expired' class).
TG_TIER_ACT_MIN_GP    = 0.15
TG_TIER_WATCH_MIN_GP  = 0.05
TG_TIER_SCOUT_MIN_GP  = 0.02
TG_PUSH_MAX_AGE_S     = 300  # 5 min — past this with no gp AND not composite-strong → discard
TIER_LOGIC_VERSION    = "v2"


def _composite_strong(smart_money_in, max_mult_at_cross) -> bool:
    """The non-ML OR-arm. Load-bearing: only mechanism that surfaces the
    gp-less late-cross strong movers (old 'expired' class). Overfit-risk
    surface per pre-reg §8 — Branch-C refuses to keep tuning this pair."""
    return (smart_money_in or 0) >= 7 and (max_mult_at_cross or 0) >= 4.0


def evaluate_tg_pushes(live_mints_by_mint: dict | None = None) -> dict:
    """Sweep recently-crossed mints, classify into ACT/WATCH/below/expired,
    push qualifying ones to TG. Safe to call every tick; idempotent via
    tg_pushed_at column. Returns a stats dict for observability.

    `live_mints_by_mint` (added 2026-06-19): an optional {mint → enriched mint
    dict} from the same scoring pass that owns the freshest grad_prob.
    If provided, we read grad_prob from the live cache FIRST and only fall
    back to the SQLite predictions table for mints that have aged out of
    the live window.

    Why this matters: predictions.log_prediction() enqueues to an async
    drain that batches every ~250ms but with up to a 5s wake delay; under
    real load that drain can lag the model output by 60-120s. For TG push
    latency that drain delay was the #1 bottleneck. Reading grad_prob from
    the in-memory live cache skips the drain entirely and brings end-to-end
    TG latency from observer→user down from ~3min to ~5s.

    Logic per pending cross:
      - try grad_prob from live cache (current snapshot) — fresh, no drain lag
      - fall back to SQLite predictions table for mints not in live cache
      - if not yet available AND cross is younger than TG_PUSH_MAX_AGE_S: skip
      - if available: classify ACT/WATCH/SCOUT/below_threshold and push

    Never raises. Wallet-redaction safe (no wallet addresses in TG snapshot)."""
    stats = {"considered": 0, "act": 0, "watch": 0, "scout": 0,
             "below": 0, "waiting": 0, "errors": 0,
             "gp_from_live": 0, "gp_from_sqlite": 0}
    live_mints_by_mint = live_mints_by_mint or {}
    now = int(time.time())
    try:
        with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=5)) as c, c:
            c.row_factory = sqlite3.Row
            # Pull unpushed crosses from the last 30 minutes (avoid scanning
            # the whole table; older-than-30min unpushed are stale anyway).
            rows = c.execute("""
                SELECT mint, predicted_at, composite_score, threshold_at_cross,
                       smart_money_in, max_mult_at_cross, age_s_at_cross,
                       mc_at_cross_usd
                  FROM composite_predictions
                 WHERE tg_pushed_at IS NULL
                   AND predicted_at > ?
            """, (now - 1800,)).fetchall()

            for r in rows:
                stats["considered"] += 1
                mint = r["mint"]
                age_since_cross = now - r["predicted_at"]
                try:
                    # FAST PATH: read grad_prob from the live cache for this
                    # tick. This is the same data the WebSocket/REST surfaces
                    # see and skips the predictions-table drain queue entirely.
                    bestgp = None
                    live_mint = live_mints_by_mint.get(mint)
                    if live_mint:
                        gp_live = live_mint.get("grad_prob")
                        if gp_live is not None:
                            bestgp = float(gp_live)
                            stats["gp_from_live"] += 1
                    # SLOW PATH: fall back to SQLite for crosses on mints that
                    # have aged out of the live observer window (older than
                    # ~60s typically — the live cache only carries fresh ones).
                    if bestgp is None:
                        gp_rows = c.execute("""
                            SELECT predicted_prob
                              FROM predictions
                             WHERE mint = ? AND age_bucket IN (30, 60)
                               AND predicted_prob IS NOT NULL
                        """, (mint,)).fetchall()
                        gps = [x["predicted_prob"] for x in gp_rows]
                        bestgp = max(gps) if gps else None
                        if bestgp is not None:
                            stats["gp_from_sqlite"] += 1
                    comp_strong = _composite_strong(
                        r["smart_money_in"], r["max_mult_at_cross"])

                    # Frozen 3-tier + discard (pre-reg AMENDMENT 01).
                    if bestgp is not None and bestgp >= TG_TIER_ACT_MIN_GP:
                        tier = "ACT"
                        # Day 4.65 ACT-QUALITY FLOOR (data-validated on
                        # 15,109-resolved-mint observer dataset, 2026-06-24).
                        # Even if grad_prob qualifies for ACT, demote to
                        # WATCH if EITHER:
                        #   • score_ratio < 2.0× (marginal cross — score
                        #     barely above threshold = thin signal)
                        #   • smart_money_in outside [3, 9] (validated
                        #     sweet spot; both <3 and >9 underperform base
                        #     graduation rate by ~2× on the observer set)
                        #
                        # Observer-data baseline: grad rate 12.0%, peak 4.5×.
                        # sr ≥ 3 × SM 3-9 = 22.4% grad (1.87× base).
                        # sr 2-3 × SM 6-9 = 11.3% (BELOW base — the gate
                        # rightly demotes this band's weakest cells).
                        try:
                            thr  = float(r["threshold_at_cross"] or 0)
                            comp = float(r["composite_score"] or 0)
                            sm   = r["smart_money_in"]
                            sr   = (comp / thr) if thr > 0 else 0
                            sm_in_band = (sm is not None and 3 <= sm <= 9)
                            if sr < 2.0 or not sm_in_band:
                                tier = "WATCH"
                        except Exception:
                            pass
                    elif bestgp is not None and bestgp >= TG_TIER_WATCH_MIN_GP:
                        tier = "WATCH"
                    elif (bestgp is not None and bestgp >= TG_TIER_SCOUT_MIN_GP) or comp_strong:
                        # SCOUT recovery tier. comp_strong reaches here even
                        # with bestgp=None (the retired 'expired' class).
                        tier = "SCOUT"
                    elif bestgp is None and age_since_cross < TG_PUSH_MAX_AGE_S:
                        # No gp yet AND not composite-strong — wait, gp may land.
                        stats["waiting"] += 1
                        continue
                    else:
                        # DISCARD: bestgp < 0.02 (or never came) AND not
                        # composite-strong = the real death-zone. 'expired'
                        # retired — these are just below_threshold now.
                        c.execute(
                            "UPDATE composite_predictions SET tg_pushed_at=?, "
                            "tg_tier='below_threshold', tier_logic_version=? WHERE mint=?",
                            (now, TIER_LOGIC_VERSION, mint),
                        )
                        stats["below"] += 1
                        continue

                    import alert_push
                    alert_push.push_composite_cross(
                        mint=mint,
                        composite_score=r["composite_score"],
                        threshold_at_cross=r["threshold_at_cross"],
                        smart_money_in=r["smart_money_in"],
                        max_mult_at_cross=r["max_mult_at_cross"],
                        age_s_at_cross=r["age_s_at_cross"],
                        mc_at_cross_usd=r["mc_at_cross_usd"],
                        metadata=None,  # name/symbol fetched lazily by bot if needed
                        tier=tier,
                        grad_prob_60=bestgp,
                    )
                    c.execute(
                        "UPDATE composite_predictions SET tg_pushed_at=?, "
                        "tg_tier=?, tier_logic_version=? WHERE mint=?",
                        (now, tier, TIER_LOGIC_VERSION, mint),
                    )
                    stats[tier.lower()] = stats.get(tier.lower(), 0) + 1
                except Exception as e:
                    stats["errors"] += 1
                    print(f"[composite_predictions] tg-push eval failed for {mint[:14]}: {e}", flush=True)
    except Exception as e:
        print(f"[composite_predictions] tg-push sweep failed: {e}", flush=True)
    return stats


# ─── Outcome resolver ───────────────────────────────────────────────────────

def resolve_pending_outcomes() -> int:
    """Sweep composite_predictions for crosses where predicted_at + 24h has
    passed and outcome columns are still NULL. Populate from existing
    tables. Returns count of rows resolved this pass.

    Outcome sources (existing tables; no new daemon needed):
      - did_graduate, peak_mult_24h ← predictions.actual_graduated /
        actual_max_mult (resolved by the existing predictions outcome
        resolver in web/predictions.py)
      - did_sustain_30m ← post_grad_outcomes.sustained_30m (resolved
        by web/post_grad_tracker.py)

    Crosses with no predictions row (rare — composite caught a mint that
    grad_prob didn't predict) get peak_mult / did_graduate from
    mint_checkpoints.actual_max_mult / actual_graduated as a fallback.
    """
    now = int(time.time())
    cutoff = now - OUTCOME_GRACE_S
    n_resolved = 0
    try:
        with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=10)) as c, c:
            _ensure_schema(c)
            pending = c.execute("""
                SELECT mint, predicted_at FROM composite_predictions
                 WHERE outcome_resolved_at IS NULL
                   AND predicted_at <= ?
            """, (cutoff,)).fetchall()
            for mint, pred_ts in pending:
                # Primary: join to predictions table (grad_prob track has
                # already resolved outcomes by the time 24h passes).
                pred_row = c.execute("""
                    SELECT actual_graduated, actual_max_mult
                      FROM predictions
                     WHERE mint = ?
                       AND actual_max_mult IS NOT NULL
                     ORDER BY predicted_at ASC
                     LIMIT 1
                """, (mint,)).fetchone()
                did_grad: Optional[int] = None
                peak_mult: Optional[float] = None
                if pred_row is not None:
                    did_grad = pred_row[0] if pred_row[0] is not None else None
                    peak_mult = pred_row[1] if pred_row[1] is not None else None
                else:
                    # Fallback: mint_checkpoints
                    mc_row = c.execute("""
                        SELECT actual_graduated, actual_max_mult
                          FROM mint_checkpoints
                         WHERE mint = ?
                           AND actual_max_mult IS NOT NULL
                         ORDER BY checkpoint_age_s DESC
                         LIMIT 1
                    """, (mint,)).fetchone()
                    if mc_row is not None:
                        did_grad = mc_row[0] if mc_row[0] is not None else None
                        peak_mult = mc_row[1] if mc_row[1] is not None else None
                # Sustain — only meaningful when did_graduate=1. Read from
                # post_grad_outcomes.
                did_sustain: Optional[int] = None
                if did_grad == 1:
                    pgo_row = c.execute("""
                        SELECT sustained_30m FROM post_grad_outcomes
                         WHERE mint = ?
                           AND sustained_30m IS NOT NULL
                         LIMIT 1
                    """, (mint,)).fetchone()
                    if pgo_row is not None:
                        did_sustain = pgo_row[0]
                # Only mark outcome_resolved_at when we have at least one
                # non-null outcome field — otherwise leave NULL and retry
                # at next sweep (the join sources may still be populating).
                if did_grad is not None or peak_mult is not None:
                    c.execute("""
                        UPDATE composite_predictions
                           SET outcome_resolved_at = ?,
                               did_graduate        = ?,
                               peak_mult_24h       = ?,
                               did_sustain_30m     = ?
                         WHERE mint = ?
                    """, (now, did_grad, peak_mult, did_sustain, mint))
                    n_resolved += 1
    except Exception as e:
        print(f"[composite_predictions] resolve failed: {e}", flush=True)
        return 0
    return n_resolved


# ─── Composite ledger (parallel to web/ledger.py for grad_prob) ─────────────

# Composite leaf format V1. Independent versioning namespace from the
# grad_prob ledger (which is at V3 in web/ledger.py). Future evolution
# bumps composite leaf version, not grad_prob ledger version.
_COMPOSITE_LEAF_FIELDS_V1 = (
    "mint", "predicted_at",
    "composite_score", "threshold_at_cross",
    "smart_money_in", "max_mult_at_cross", "age_s_at_cross",
    "mc_at_cross_usd",
)
_COMPOSITE_LEAF_FIELDS_BY_VERSION = {1: _COMPOSITE_LEAF_FIELDS_V1}
COMPOSITE_LEAF_VERSION = 1


def composite_leaf_hash(row: dict, leaf_version: int = COMPOSITE_LEAF_VERSION) -> bytes:
    """SHA-256 over the canonical serialization of a composite-prediction
    row. Matches the byte-for-byte discipline of web/ledger.py.leaf_hash:
    json.dumps with sort_keys + separators + allow_nan=False."""
    fields = _COMPOSITE_LEAF_FIELDS_BY_VERSION.get(leaf_version)
    if fields is None:
        raise ValueError(f"unknown composite leaf_version: {leaf_version}")
    payload = {k: row.get(k) for k in fields}
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                            allow_nan=False).encode("utf-8")
    return hashlib.sha256(serialized).digest()


def _merkle_root(leaves: list[bytes]) -> bytes:
    """Same Bitcoin-style merkle as web/ledger.py.merkle_root. Inlined to
    keep this module independent."""
    if not leaves:
        return hashlib.sha256(b"").digest()
    layer = list(leaves)
    while len(layer) > 1:
        if len(layer) % 2 == 1:
            layer.append(layer[-1])
        layer = [hashlib.sha256(layer[i] + layer[i + 1]).digest()
                 for i in range(0, len(layer), 2)]
    return layer[0]


def commit_period(t0: int, t1: int) -> Optional[dict]:
    """Commit a tamper-evident merkle root over composite_predictions rows
    in [t0, t1). Returns the commit dict or None if no rows in the period.
    """
    try:
        with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=10)) as c, c:
            _ensure_schema(c)
            cols = list(_COMPOSITE_LEAF_FIELDS_V1)
            rows = c.execute(f"""
                SELECT {', '.join(cols)} FROM composite_predictions
                 WHERE predicted_at >= ? AND predicted_at < ?
                 ORDER BY predicted_at ASC, mint ASC
            """, (t0, t1)).fetchall()
            if not rows:
                return None
            row_dicts = [dict(zip(cols, r)) for r in rows]
            version = COMPOSITE_LEAF_VERSION
            leaves = [composite_leaf_hash(r, leaf_version=version) for r in row_dicts]
            root = _merkle_root(leaves)
            now = int(time.time())
            c.execute("""
                INSERT OR IGNORE INTO composite_prediction_commits
                    (period_start, period_end, merkle_root_hex, n_crosses,
                     first_pred_ts, last_pred_ts, computed_at, leaf_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (t0, t1, root.hex(), len(rows),
                  row_dicts[0]["predicted_at"], row_dicts[-1]["predicted_at"],
                  now, version))
            return {
                "period_start":    t0,
                "period_end":      t1,
                "merkle_root_hex": root.hex(),
                "n_crosses":       len(rows),
                "computed_at":     now,
                "leaf_version":    version,
            }
    except Exception as e:
        print(f"[composite_predictions] commit failed: {e}", flush=True)
        return None


def hourly_commit_tick() -> None:
    """Commit the previous full hour's composite crosses, if any. Called
    by a background daemon analogous to the grad_prob ledger's commit
    sweep. Idempotent: INSERT OR IGNORE on (period_start, period_end)."""
    now = int(time.time())
    period_end = (now // 3600) * 3600           # start of current hour
    period_start = period_end - 3600            # start of previous hour
    commit_period(period_start, period_end)


# ─── Daemon (background outcome resolver + hourly commit) ───────────────────

_daemon_started = False
_daemon_lock = threading.Lock()


def _daemon_loop():
    while True:
        try:
            resolve_pending_outcomes()
        except Exception:
            pass
        try:
            hourly_commit_tick()
        except Exception:
            pass
        time.sleep(300)  # 5 min sweep cadence — outcome resolution is not
                        # latency-critical (24h grace anyway)


def start() -> None:
    """Spawn the background daemon. Idempotent."""
    global _daemon_started
    with _daemon_lock:
        if _daemon_started:
            return
        _daemon_started = True
        t = threading.Thread(target=_daemon_loop, name="composite_predictions",
                             daemon=True)
        t.start()
