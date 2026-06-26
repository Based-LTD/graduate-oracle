"""
Event-driven TG alert push — kills the 30s lane-race in the polling tick.

Old architecture: bot polls /api/live every 15s, evaluates each mint against
each rule. The lane window for grad_prob predictions is age 60-90s = 30s,
so the bot has at most TWO chances to catch a winning prediction. With
snapshot caching, sqlite contention, or any small latency, that window can
slip past entirely. Concrete real miss: mint BAtrhVFg... at age 60s had
grad_prob=54% with all suppression gates open and a matching active rule
at threshold 30%. Bot never fired. Predictions ages out, mint banks.

New architecture: web service writes pending_alerts the moment a prediction
crosses a threshold inside the score precompute path. Bot drains the queue
every 1.5s (small, fast SELECT). Lane race is gone — every prediction is
evaluated against every active rule the moment it exists, regardless of
when the bot's tick happens to fire.

Schema:

    CREATE TABLE pending_alerts (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        rule_id         INTEGER NOT NULL,
        telegram_id     INTEGER NOT NULL,
        kind            TEXT NOT NULL,
        mint            TEXT NOT NULL,
        age_bucket      INTEGER NOT NULL,
        snapshot_json   TEXT NOT NULL,   -- m_out subset for rendering
        msg_extra       TEXT,
        queued_at       INTEGER NOT NULL,
        delivered_at    INTEGER,
        UNIQUE(rule_id, mint, age_bucket)
    );

The UNIQUE(rule_id, mint, age_bucket) constraint is the dedup primitive:
a (rule, mint, age 30 or 60) tuple can only ever queue once. INSERT OR
IGNORE in the hot path makes this safe to call repeatedly.

Only handles prediction-tied alert kinds (grad_prob, runner_5x, runner_10x).
Non-prediction kinds (vsol_burst, smart_in, etc.) stay on the existing
poll path because they're not bound to the narrow lane window.
"""
import contextlib
import json
import sqlite3
import threading
import time

import db


# Both close AND commit. Without close(), fds leak (10K+ outage on
# 2026-05-03). Without commit, INSERTs silently roll back when close()
# fires (10h of lost writes on 2026-05-03 round 2). The combo is what
# every sqlite use site needs in this codebase.
@contextlib.contextmanager
def _connect(timeout: float = 5):
    conn = sqlite3.connect(db.DB_PATH, timeout=timeout)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


PUSH_KINDS = ("grad_prob", "bucket", "composite_score")

_init_lock = threading.Lock()
_initialized = False
_rules_cache = {"refreshed_at": 0.0, "rules": []}
_rules_cache_lock = threading.Lock()
RULES_CACHE_TTL_S = 30


def init_schema():
    global _initialized
    with _init_lock:
        if _initialized:
            return
        with _connect(timeout=10) as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS pending_alerts (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    rule_id         INTEGER NOT NULL,
                    telegram_id     INTEGER NOT NULL,
                    kind            TEXT NOT NULL,
                    mint            TEXT NOT NULL,
                    age_bucket      INTEGER NOT NULL,
                    snapshot_json   TEXT NOT NULL,
                    msg_extra       TEXT,
                    queued_at       INTEGER NOT NULL,
                    delivered_at    INTEGER,
                    UNIQUE(rule_id, mint, age_bucket)
                )
            """)
            c.execute("""
                CREATE INDEX IF NOT EXISTS idx_pending_undelivered
                    ON pending_alerts(queued_at)
                 WHERE delivered_at IS NULL
            """)
        _initialized = True


def _refresh_rules():
    """Pull active prediction-tied rules. Cached for RULES_CACHE_TTL_S — the
    push hook fires for every mint on every score pass, so refreshing per call
    would be a hot-loop killer. Rules change rarely (user adds/removes alert
    via /alert command) so 30s TTL is plenty."""
    now = time.time()
    with _rules_cache_lock:
        if (now - _rules_cache["refreshed_at"]) < RULES_CACHE_TTL_S and _rules_cache["rules"]:
            return _rules_cache["rules"]
    try:
        placeholders = ",".join("?" for _ in PUSH_KINDS)
        with _connect(timeout=5) as c:
            c.row_factory = sqlite3.Row
            rows = c.execute(f"""
                SELECT id, telegram_id, kind, threshold, params
                  FROM tg_alert_rules
                 WHERE active = 1 AND kind IN ({placeholders})
            """, PUSH_KINDS).fetchall()
        rules = [dict(r) for r in rows]
    except Exception:
        rules = []
    with _rules_cache_lock:
        _rules_cache["refreshed_at"] = now
        _rules_cache["rules"] = rules
    return rules


def _matches(rule, m_out):
    """Returns (matched, msg_extra). msg_extra is the trigger reason
    rendered into the TG alert.

    Anti-late-call gate: every runner-tier prediction must have
    expected_upside_from_now ≥ MIN_UPSIDE_FROM_NOW. Without this, we fire
    on mints whose neighbors did 5× FROM LAUNCH but where the actual
    from-now upside is ~1× — meaning "this already pumped, you'd be the
    exit liquidity". Catches the "click → coin dead" pattern."""
    MIN_UPSIDE_FROM_NOW = 1.5  # require the model expects ≥1.5× from now
    kind = rule["kind"]
    threshold = rule["threshold"] or 0
    age_s = m_out.get("age_s") or 0
    if age_s > 90:
        return False, ""
    if kind == "bucket":
        # Post-cutover (2026-05-06): explicit bucket-tier rules. Threshold
        # field is unused; the tier ('HIGH' or 'MED') is stored in params
        # JSON to keep the schema TEXT/REAL split intact. Avoids the legacy
        # threshold→bucket-gate mapping ambiguity from the kind='grad_prob'
        # branch. See docs/research/alert_rule_post_cutover_gap.md.
        bucket = m_out.get("grad_prob_bucket")
        bucket_rank = {"HIGH": 2, "MED": 1, "LOW": 0}
        # Parse tier from params JSON; default to MED for safety (firing on
        # MED+ if params is missing/malformed beats not firing at all).
        target = "MED"
        try:
            params = rule.get("params") or "{}"
            if isinstance(params, str):
                import json as _json
                params = _json.loads(params)
            if isinstance(params, dict) and params.get("tier") in ("HIGH", "MED"):
                target = params["tier"]
        except Exception:
            pass
        if bucket is None or bucket_rank.get(bucket, 0) < bucket_rank[target]:
            return False, ""
        gp_cal = m_out.get("grad_prob_gbm_calibrated")
        cur_mult = m_out.get("current_mult") or 0
        vsol_growth = m_out.get("vsol_growth_sol") or 0
        bucket_emoji = "🟢" if bucket == "HIGH" else "🟡"
        cal_str = (f"{gp_cal*100:.1f}%" if gp_cal is not None else "—")
        # Edge = grad_prob / age-bucket base rate, computed live in main.py:875.
        # Drops gracefully when sparse data leaves lift_x null — we never invent.
        lift_x = m_out.get("grad_prob_lift_x")
        edge_str = f" · {round(lift_x)}× better than average" if lift_x else ""
        if cur_mult <= 2.0 and vsol_growth >= 0:
            return True, (
                f"🎯 ACT {bucket_emoji} *{bucket}* — {cal_str} chance to graduate{edge_str} "
                f"· age {age_s:.0f}s · entry {cur_mult:.2f}× launch · vsol +{vsol_growth:.1f}"
            )
        why = []
        if cur_mult > 2.0:
            why.append(f"entry already *{cur_mult:.1f}×* launch")
        if vsol_growth < 0:
            why.append(f"vsol *{vsol_growth:.1f}*")
        return True, (
            f"📊 WATCH {bucket_emoji} *{bucket}* — {cal_str} chance to graduate{edge_str} "
            f"· age {age_s:.0f}s · {' · '.join(why) if why else 'entry context normal'}"
        )
    if kind == "grad_prob":
        # Post-cutover (2026-05-06): bucket headline + calibrated probability
        # + base rate as supporting context. Bare "X% to graduate" framing
        # retired at Gate 5 (deployed k-NN's score scale was a model-scale
        # artifact, not a probability claim). Bimodal-aware buckets per
        # docs/research/bucket_cutoffs_bimodal_finding.md.
        bucket = m_out.get("grad_prob_bucket")  # "HIGH"/"MED"/"LOW"/None
        gp_cal = m_out.get("grad_prob_gbm_calibrated")
        # Threshold ≥0.5 = require HIGH; threshold ≥0.0 = require MED+.
        # Floor at MED+ so users don't get LOW-bucket spam.
        bucket_gate = "HIGH" if (threshold or 0.70) >= 0.5 else "MED"
        bucket_rank = {"HIGH": 2, "MED": 1, "LOW": 0}
        if bucket is None or bucket_rank.get(bucket, 0) < bucket_rank[bucket_gate]:
            return False, ""
        cur_mult = m_out.get("current_mult") or 0
        vsol_growth = m_out.get("vsol_growth_sol") or 0
        bucket_emoji = "🟢" if bucket == "HIGH" else "🟡"
        cal_str = (f"{gp_cal*100:.1f}%" if gp_cal is not None else "—")
        # Edge = grad_prob / age-bucket base rate, computed live in main.py:875.
        # Drops gracefully when sparse data leaves lift_x null — we never invent.
        lift_x = m_out.get("grad_prob_lift_x")
        edge_str = f" · {round(lift_x)}× better than average" if lift_x else ""
        if cur_mult <= 2.0 and vsol_growth >= 0:
            return True, (
                f"🎯 ACT {bucket_emoji} *{bucket}* — {cal_str} chance to graduate{edge_str} "
                f"· age {age_s:.0f}s · entry {cur_mult:.2f}× launch · vsol +{vsol_growth:.1f}"
            )
        why = []
        if cur_mult > 2.0:
            why.append(f"entry already *{cur_mult:.1f}×* launch")
        if vsol_growth < 0:
            why.append(f"vsol *{vsol_growth:.1f}*")
        return True, (
            f"📊 WATCH {bucket_emoji} *{bucket}* — {cal_str} chance to graduate{edge_str} "
            f"· age {age_s:.0f}s · {' · '.join(why) if why else 'entry context normal'}"
        )
    # All other kinds (runner_5x, runner_10x, x_factor, etc.) were dropped
    # 2026-05-04 in the simplification. The product is grad_prob. Other
    # signals appear inside the alert as supporting context but don't
    # trigger separate alerts — they were creating turds.
    return False, ""


# Fields the bot needs to render _format_alert_rich + _collect_signals.
# Kept lean to keep the JSON write small and fast.
_SNAPSHOT_FIELDS = (
    "mint", "age_s", "current_mult", "max_mult",
    "current_vsol_sol", "unique_buyers", "market_cap",
    "metadata", "first_buyer", "top_buyers",
    "grad_prob", "grad_prob_lift_x", "grad_prob_calibration",
    # Post-2026-05-07 sixth-finding Fix 1: bucket + calibrated GBM fields
    # in snapshot so future content inspection can see what fired. Pre-fix
    # the bucket assignment was set on m_out at fire time but absent from
    # the persisted snapshot — auditors couldn't verify what the rule saw.
    "grad_prob_bucket", "grad_prob_gbm_calibrated", "grad_prob_gbm_shadow",
    "runner_prob_2x_from_now", "runner_prob_3x_from_now",
    "runner_prob_5x_from_now", "runner_prob_10x_from_now",
    "runner_prob_2x_from_now_calibration",
    "runner_prob_5x_from_now_calibration",
    "runner_prob_10x_from_now_calibration",
    "x_factor", "rug_prob",
    "expected_upside_from_now", "median_upside_from_now",
    "expected_peak_mult", "median_peak_mult",
    "smart_money_in", "cluster", "creator_history",
    "vsol_acceleration", "wallet_balance", "fee_delegation",
    "dex_paid", "post_grad_survival_prob", "early_grad_prob",
    "bundle", "manufactured_pump",
)


def _is_relisted_old_mint(c, mint: str, now: int) -> bool:
    """Detect mints whose observer-reported age_s is misleading because the
    observer only saw them recently — but they actually existed and were
    scored hours/days ago. The observer assigns age_s from "first time we
    saw this mint in our stream." If the mint went dormant then woke up,
    or the observer restarted, the observer thinks the mint is brand new.

    Real example 2026-05-03: 2hU1qtA4gq... had observer age_s=32s but was
    21 days old. The bonding curve had been around forever; we just hadn't
    seen activity in our stream until that moment. Alerting on it as if
    it were a fresh launch is wrong — predictions are calibrated against
    actually-fresh mints.

    Cheap check: did we predict on this mint > 1h ago? If yes, it's not
    new to us either."""
    try:
        row = c.execute(
            "SELECT predicted_at FROM predictions "
            "WHERE mint = ? ORDER BY predicted_at ASC LIMIT 1",
            (mint,),
        ).fetchone()
        if row and row[0] and (now - int(row[0])) >= 3600:
            return True
    except Exception:
        pass
    return False


def maybe_push(m_out):
    """Hot-path hook called from _enrich_mint after m_out is fully built.
    Evaluates prediction-tied rules against the freshly scored mint and
    inserts matching rows into pending_alerts. Failures swallowed — the
    score path must never break for an alert error.

    Also enforces the same suppression rules the polling tick uses, so the
    push path doesn't silently leak alerts on flagged rugs:
      - bundle_pct >= 30 (active dangerous bundle)
      - rug_heuristic.severity == "high" (multi-flag rug pattern)
      - relisted old mint (we predicted on it >1h ago, observer just
        re-saw it — not actually a fresh launch)"""
    try:
        init_schema()
        # Only push when we're inside the prediction lane and have an
        # age_bucket-aligned prediction (30 or 60). Outside this, there's
        # nothing new to push for prediction kinds.
        age_s = m_out.get("age_s") or 0
        if age_s > 90:
            return
        age_bucket = m_out.get("age_bucket")
        if age_bucket not in (30, 60):
            return
        # Suppression — mirror the polling tick's `_alert_is_suppressed`.
        # m_out["rug_heuristic"] and m_out["bundle"] are populated upstream
        # by _enrich_mint, so this is a pure dict read — no extra cost.
        bun = m_out.get("bundle") or {}
        if bun.get("detected") and (bun.get("pct") or 0) >= 30:
            return
        rh = m_out.get("rug_heuristic") or {}
        if rh.get("severity") == "high":
            return
        # Post-peak / pump-and-dump suppression. If the mint has already
        # peaked at ≥2× its current price, the predicted upside is the
        # upside that already happened — alerting now puts the user into
        # a retrace, not a pump. Real example: xf7SfsRq... peaked at
        # 4.45× then dumped to 0.92× by age 57s; model still said "5×
        # from now is 19%" because neighbors with similar features did,
        # but this specific mint was post-peak. Catches the DOA pattern.
        cur_mult = m_out.get("current_mult") or 0
        max_mult = m_out.get("max_mult") or 0
        if cur_mult > 0 and max_mult / cur_mult >= 2.0:
            return
        rules = _refresh_rules()
        if not rules:
            return
        matches = []
        for rule in rules:
            ok, msg_extra = _matches(rule, m_out)
            if ok:
                matches.append((rule, msg_extra))
        if not matches:
            return
        snapshot = {k: m_out.get(k) for k in _SNAPSHOT_FIELDS}
        snap_json = json.dumps(snapshot, default=str)
        now = int(time.time())
        with _connect(timeout=5) as c:
            # Relisted-old-mint suppression. Cheap one-row query on the
            # predictions table indexed on mint. Only runs on alert
            # candidates (rare), so it doesn't slow the hot path for the
            # 90%+ of mints that don't trip a threshold.
            if _is_relisted_old_mint(c, m_out["mint"], now):
                return
            for rule, msg_extra in matches:
                # Prefer-earlier dedup: skip a 60s fire if we already fired
                # at 30s on the same (rule, mint) within the last 30 min.
                # The 30s call captures the freshest entry; 60s would just
                # duplicate the alert at a worse entry price.
                if age_bucket == 60:
                    prior = c.execute("""
                        SELECT 1 FROM pending_alerts
                         WHERE rule_id = ? AND mint = ? AND age_bucket = 30
                           AND queued_at >= ?
                         LIMIT 1
                    """, (rule["id"], m_out["mint"], now - 1800)).fetchone()
                    if prior:
                        continue
                try:
                    c.execute("""
                        INSERT OR IGNORE INTO pending_alerts
                            (rule_id, telegram_id, kind, mint, age_bucket,
                             snapshot_json, msg_extra, queued_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (rule["id"], rule["telegram_id"], rule["kind"],
                          m_out["mint"], age_bucket, snap_json, msg_extra, now))
                except sqlite3.OperationalError:
                    # Transient lock — bot's polling tick still covers as
                    # a fallback for slower kinds, and this rule will get
                    # a chance again on the next score pass for this mint
                    # within the lane.
                    pass
    except Exception:
        pass


def push_composite_cross(mint: str, composite_score: float, threshold_at_cross: float,
                         smart_money_in: int, max_mult_at_cross: float,
                         age_s_at_cross: int, mc_at_cross_usd: float,
                         metadata: dict | None = None,
                         tier: str = "ACT",
                         grad_prob_60: float | None = None,
                         is_starred: bool = False) -> None:
    """Push helper for composite-receipts crosses. Called from
    composite_predictions.evaluate_tg_pushes after tier classification.

    The tier (ACT or WATCH) is computed UPSTREAM in composite_predictions
    based on grad_prob_60. This helper just routes the snapshot to TG via
    pending_alerts. Rule threshold semantics:
      - rule.threshold = minimum tier accepted ('ACT' = 1.0, 'WATCH' = 0.5,
        or use the params.min_tier field). Currently we fire any composite
        rule on either tier — the rule presence is the consent signal.

    Tier metadata is embedded in the snapshot so the bot renderer can
    branch on ACT vs WATCH. Uses age_bucket=0 sentinel — distinct from
    grad_prob's 30/60 lanes."""
    try:
        init_schema()
        if tier not in ("ACT", "WATCH", "SCOUT"):
            return
        ratio = (composite_score / threshold_at_cross) if threshold_at_cross > 0 else 0
        with _connect(timeout=5) as c:
            c.row_factory = sqlite3.Row
            rules = c.execute("""
                SELECT id, telegram_id, kind, threshold, params
                  FROM tg_alert_rules
                 WHERE active = 1 AND kind = 'composite_score'
            """).fetchall()
        if not rules:
            return
        snapshot = {
            "mint": mint,
            "composite_score": composite_score,
            "threshold_at_cross": threshold_at_cross,
            "score_ratio": ratio,
            "smart_money_in": smart_money_in,
            "max_mult_at_cross": max_mult_at_cross,
            "age_s_at_cross": age_s_at_cross,
            "mc_at_cross_usd": mc_at_cross_usd,
            "tier": tier,
            "is_starred": bool(is_starred),
            "grad_prob_60": grad_prob_60,
            "metadata": metadata or {},
        }
        snap_json = json.dumps(snapshot, default=str)
        gp_str = f"{grad_prob_60*100:.1f}%" if grad_prob_60 is not None else "—"
        tier_emoji = {"ACT": "⚡", "WATCH": "📊", "SCOUT": "🛰"}.get(tier, "📊")
        # Day 4.69: numbered ★ alerts — give starred alerts a sequence
        # number so users see the rolling cadence ("★ #14 today").
        # Builds anticipation and rewards engagement.
        star_prefix = ""
        if is_starred:
            try:
                with _connect(timeout=3) as cc:
                    today_n = cc.execute("""
                        SELECT COUNT(*) AS n FROM composite_predictions
                         WHERE tg_pushed_at >= strftime('%s', 'now', 'start of day')
                           AND tg_tier IN ('WATCH','SCOUT')
                           AND threshold_at_cross > 0
                           AND (composite_score/threshold_at_cross) >= 3.0
                           AND smart_money_in BETWEEN 3 AND 9
                           AND mc_at_cross_usd >= 10000
                           AND mc_at_cross_usd < 15000
                    """).fetchone()["n"]
                star_prefix = f"★ ALPHA #{today_n + 1} · "
            except Exception:
                star_prefix = "★ ALPHA · "
        msg_extra = (f"{tier_emoji} {star_prefix}{tier} — grad_prob {gp_str} · "
                     f"score {composite_score:.1f} ({ratio:.2f}× threshold) · "
                     f"smart_money {smart_money_in} · {max_mult_at_cross:.2f}× · "
                     f"age {age_s_at_cross}s · ${mc_at_cross_usd:,.0f} MC")
        now = int(time.time())
        with _connect(timeout=5) as c:
            for rule in rules:
                rule = dict(rule)
                # Optional params.min_tier filter. Rank: ACT(3) > WATCH(2) >
                # SCOUT(1). No min_tier (default, e.g. rule 14) → fire all
                # three. min_tier='WATCH' → fire ACT+WATCH, skip SCOUT. etc.
                try:
                    params = rule.get("params") or "{}"
                    if isinstance(params, str):
                        params = json.loads(params)
                    if isinstance(params, dict):
                        min_tier = params.get("min_tier")
                        if min_tier:
                            rank = {"SCOUT": 1, "WATCH": 2, "ACT": 3}
                            if rank.get(tier, 0) < rank.get(min_tier, 0):
                                continue
                except Exception:
                    pass
                try:
                    c.execute("""
                        INSERT OR IGNORE INTO pending_alerts
                            (rule_id, telegram_id, kind, mint, age_bucket,
                             snapshot_json, msg_extra, queued_at)
                        VALUES (?, ?, 'composite_score', ?, 0, ?, ?, ?)
                    """, (rule["id"], rule["telegram_id"], mint,
                          snap_json, msg_extra, now))
                except sqlite3.OperationalError:
                    pass

        # B2B webhook fan-out — same event, machine-consumable. Fires once
        # per cross (NOT per TG rule). Subscribers registered via
        # POST /api/v1/webhooks get an HMAC-signed POST. Independent of
        # whether any TG rule matched — webhook customers are a separate
        # consumer class from the TG bot. Best-effort; never blocks the
        # cross path. Mirrors the /api/v1/signals payload shape.
        try:
            import webhooks as _webhooks
            _webhooks.enqueue("composite.cross", {
                "mint":               mint,
                "tier":               tier,
                "composite_score":    composite_score,
                "threshold_at_cross": threshold_at_cross,
                "score_ratio":        ratio,
                "smart_money_in":     smart_money_in,
                "max_mult_at_cross":  max_mult_at_cross,
                "age_s_at_cross":     age_s_at_cross,
                "mc_at_cross_usd":    mc_at_cross_usd,
                "grad_prob_60":       grad_prob_60,
            })
        except Exception:
            pass
    except Exception:
        pass


def drain_pending(limit: int = 50):
    """Bot calls this on its fast tick (every 1.5s). Returns undelivered
    rows and atomically marks them delivered. Each row is fully self-
    contained (snapshot + msg_extra) so the bot can render and send
    without any further server roundtrip."""
    init_schema()
    out = []
    try:
        with _connect(timeout=5) as c:
            c.row_factory = sqlite3.Row
            rows = c.execute("""
                SELECT id, rule_id, telegram_id, kind, mint, age_bucket,
                       snapshot_json, msg_extra, queued_at
                  FROM pending_alerts
                 WHERE delivered_at IS NULL
                 ORDER BY queued_at ASC
                 LIMIT ?
            """, (limit,)).fetchall()
            if not rows:
                return out
            ids = [r["id"] for r in rows]
            c.execute(
                f"UPDATE pending_alerts SET delivered_at = ? WHERE id IN "
                f"({','.join('?' for _ in ids)})",
                (int(time.time()), *ids)
            )
    except Exception:
        return out
    for r in rows:
        d = dict(r)
        try:
            d["snapshot"] = json.loads(d.pop("snapshot_json"))
        except Exception:
            d["snapshot"] = {}
        out.append(d)
    return out
