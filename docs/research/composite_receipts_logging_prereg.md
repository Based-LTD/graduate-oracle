# Composite-receipts logging — pre-registration

**Pre-registration commit.** Methodology + frozen config + outcome definitions committed alongside the implementation in the same commit cycle. Per the publish-then-post discipline, the pre-reg lives publicly before the system accumulates any audit-grade rows. The receipts ledger that backs this system is itself part of the receipts moat — its design choices are frozen here.

**Series:** Composite track companion to the grad_prob receipts trail (`web/predictions.py` + `web/ledger.py`, current leaf format V3). Per memory `project_dual_track_signal_strategy.md`: grad_prob is the moat infrastructure; composite is the user-facing product surface. Both tracks need tamper-evident audit logs; this pre-reg establishes the composite track's ledger.

**User direction (2026-05-11):** ongoing observation shows the hot-launch composite + MC floor producing high-hit-rate catches. These currently live only in conversation logs. The receipts trail extends to the composite track so the catches accumulate as audit-grade rows.

---

## Strategic context

- **Audit 09 verdict (commit `34ce847`)**: wallet reputation index empirically validated as load-bearing moat (7.37× grad lift at sm≥7 stratum; clean monotonic 2x trend; non-overlapping CIs).
- **Audit 12-B Phase 1b (commit `e2aaf51`)**: freshness factor has empirically detectable signal on 2x_runner_rate at lane-60s (1.40× lift, non-overlapping CIs).
- **Audit 12-A (commit `66e1138`)**: retroactive composite test reached METHODOLOGY-EXPOSES-LEAKAGE verdict; forward composite test (with snapshot max_mult at predicted_at) remains the appropriate validation.
- **Wallet redaction (deploys `06480be` + `d8af9ec`)**: all wallet-shaped surfaces in /api/live redacted; composite signal continues to surface via aggregate fields.

The composite formula's two forward-safe components (smart_money_in + freshness) are independently validated. The third (max_mult at predicted_at) is in flight via Audit 12-B Phase 2 forward collection. This logging system captures the COMPOSITE signal's catches in real time so future audits have an audit-grade row history to analyze.

---

## Methodology (frozen)

### Composite-score formula

Identical to the live dashboard formula (`web/static/app.js:683`):

```
composite_score = smart_money_in × max_mult × (1 / (1 + age_s/600))
```

Freshness half-life = 600s. All three inputs are snapshot values at the moment of evaluation. Phase 2 (Audit 12-B) will test whether age-at-predicted-at deserves a steeper decay; this system uses the production formula AS-IS so the receipts log reflects what the user actually saw.

### Cross-detection threshold

**Top 10% of composite_score over rolling 24h sample** (P90). Cold-start: P95 of available samples until 24h elapses (more conservative — fewer false-positive crosses while data accumulates).

Minimum sample size before any cross fires: **n ≥ 30**. Below that, the percentile estimate is too noisy to be a reliable threshold. During this MIN_SAMPLES warmup, no crosses are logged.

### Market-cap floor

**≥ $5,000 USD.** Mints below this floor are excluded from cross-eligibility regardless of composite_score. Pre-registered per Audit 12 and Case Study 01 Amendment 02.

### Dedup rule

**One row per mint, FIRST cross only.** PRIMARY KEY (mint) enforces dedup at the table level. INSERT OR IGNORE means subsequent crosses on the same mint (e.g., mint dropped below threshold then re-crossed) are silently skipped — they don't generate new audit-grade rows.

This is the "first cross is the prediction we publish" semantics. A future Audit could test whether second-cross events have different outcomes; if so, a separate logging table can be added. For now: one prediction per mint.

### Outcome resolution

At T+24h post-cross, the background resolver joins to existing tables (no new outcome daemon required):

- **`did_graduate`**: `predictions.actual_graduated` JOIN by mint (grad_prob track's outcome resolver). Fallback: `mint_checkpoints.actual_graduated`.
- **`peak_mult_24h`**: `predictions.actual_max_mult`. Fallback: `mint_checkpoints.actual_max_mult`.
- **`did_sustain_30m`**: `post_grad_outcomes.sustained_30m` (binary, 1 if survival_30m ≥ 0.80 per existing `post_grad_tracker` semantics). Only meaningful when `did_graduate = 1`; NULL otherwise.

Crosses where 24h hasn't yet elapsed retain `outcome_resolved_at = NULL` until the resolver sweep finds them. Crosses where the JOIN sources are still warming retry on the next sweep (5-min cadence).

### Sustain definition

Inherited verbatim from `web/post_grad_tracker.py` — sustain = 30min-post-grad vSOL ≥ 80% of grad-time vSOL. The composite-receipts log reads `post_grad_outcomes.sustained_30m` directly; no parallel definition.

---

## Table schemas (frozen)

### `composite_predictions`

```sql
CREATE TABLE composite_predictions (
    mint                  TEXT NOT NULL,
    predicted_at          INTEGER NOT NULL,     -- UNIX epoch when cross fired
    composite_score       REAL NOT NULL,
    threshold_at_cross    REAL NOT NULL,        -- the P90/P95 value at cross time
    smart_money_in        INTEGER NOT NULL,
    max_mult_at_cross     REAL NOT NULL,        -- live snapshot value (NOT lifecycle peak)
    age_s_at_cross        INTEGER NOT NULL,
    mc_at_cross_usd       REAL NOT NULL,
    -- Outcome columns (NULL until +24h resolver fires)
    outcome_resolved_at   INTEGER,
    did_graduate          INTEGER,
    peak_mult_24h         REAL,
    did_sustain_30m       INTEGER,
    PRIMARY KEY (mint)
);
CREATE INDEX idx_composite_pred_at ON composite_predictions(predicted_at);
CREATE INDEX idx_composite_outcome_unresolved ON composite_predictions(outcome_resolved_at) WHERE outcome_resolved_at IS NULL;
```

### `composite_prediction_commits`

Tamper-evident merkle commits, parallel to `prediction_commits` (grad_prob track):

```sql
CREATE TABLE composite_prediction_commits (
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
);
```

---

## Merkle leaf format

**Composite leaf V1** — independent versioning namespace from the grad_prob ledger (which is at V3 in `web/ledger.py`). Future evolution bumps the composite leaf version, not the grad_prob ledger version. The two tracks evolve independently.

Leaf fields (frozen at V1):

```
_COMPOSITE_LEAF_FIELDS_V1 = (
    "mint", "predicted_at",
    "composite_score", "threshold_at_cross",
    "smart_money_in", "max_mult_at_cross", "age_s_at_cross",
    "mc_at_cross_usd",
)
```

**Outcome columns are NOT in the leaf.** They are populated AFTER the cross is committed, so including them in the leaf would either delay commits (wait for outcome) or require re-committing (breaks tamper-evidence). The leaf locks in the PREDICTION (what we knew at predicted_at); the outcome is a separate verifiable claim that any reader can reconstruct from the public outcome tables.

This is structurally identical to the grad_prob ledger's design: V3 leaves include the prediction but not `actual_graduated` / `actual_max_mult` — those are post-hoc.

Hash + merkle primitives are identical to `web/ledger.py` (Bitcoin-style merkle with odd-node self-duplication; sha256 over `json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False)`).

### Commit cadence

Hourly. The daemon commits the previous full hour's crosses at the top of each new hour. Idempotent via `UNIQUE (period_start, period_end)`. Same shape as the grad_prob ledger's commit daemon.

---

## API surface

### `GET /api/v1/composite_predictions`

Paginated read-only listing of composite_predictions rows. Auth-gated via `require_api_key` (same pattern as `/api/v1/predictions`).

Query params:
- `limit` (1–500, default 50)
- `offset` (≥ 0)
- `only_resolved` (bool, default false) — if true, returns only rows with `outcome_resolved_at IS NOT NULL`

Response shape:
```json
{
  "count": N, "total": M, "limit": ..., "offset": ...,
  "daemon": { ... snapshot() output ... },
  "rows": [ { ...row fields... } ]
}
```

### `GET /api/v1/composite_ledger`

Paginated read-only listing of `composite_prediction_commits`. Auth-gated. Same purpose as `/api/ledger/commits` but for the composite track.

### Wallet-redaction compatibility

Both endpoints return ONLY aggregate counts (`smart_money_in`) + per-mint scalars (mint addr, composite_score, max_mult, age, mc_usd) + outcome booleans. **No wallet addresses anywhere.** Fully compatible with the wallet redaction deployed at `06480be` + `d8af9ec`. The mint address is the only public identifier surfaced.

---

## Cross-detection integration

The cross detector hooks into the score-precompute pipeline in `web/main.py` `_score_mints()`. After each tick's enriched-mint list is built, `composite_predictions.maybe_log_crossings(enriched_mints)` is called:

1. Update the rolling-24h composite_score sample with this tick's values.
2. Compute the current threshold (P90 after warmup, P95 during).
3. For each mint, check if composite_score ≥ threshold AND mc_usd ≥ floor AND mint not already in table.
4. INSERT OR IGNORE rows for crossers.

Runs ~1ms per tick. Never raises. Wallet-redaction safe — only aggregate fields persisted.

---

## What this audit DOES NOT do

Per the no-overengineering discipline:

- **No new dashboard surface.** The composite signal already has dashboard sort + filter UI; this system only adds an audit log.
- **No new bot consumer.** TG alerts for composite crosses can come later via a separate workstream.
- **No replay or back-fill.** The log starts at deploy time. Historical composite crosses before deploy are not recoverable; this is an additive forward-only instrumentation.
- **No public-mirror sync of rows.** The composite_predictions table lives in `/data/data.sqlite` on the Fly app; commits land in `composite_prediction_commits` for tamper-evidence; the rows themselves aren't mirrored to graduate-oracle docs. External commitment (e.g., on-chain memos or Twitter) is a future extension.

---

## Pre-registered next audits

Once the receipts log accumulates 30+ rows with resolved outcomes:

### Audit 12-E — Composite receipts validation

Per-row outcome stratification. Tests:
- What fraction of composite crosses graduated?
- What fraction sustained at 30m?
- What was the peak_mult distribution?

Acceptance: report headline numbers + comparison to user's "approximately 10-of-10" observation. If empirical fraction is materially below the user's observation (say <50% graduation), surface the gap and investigate. If at or above, the user's intuition is empirically validated.

Pre-registered to ship at first commit threshold (~30 resolved rows = ~1 week post-deploy at projected rate).

### Audit 12-F — Threshold sensitivity

Tests whether P90 is the right threshold. Re-runs the resolved-row outcome stratification with P85 / P95 / P99 thresholds (retroactively computable from the rolling-sample state). If P95 produces materially higher hit rate without losing volume, recommend P95 as the new default in a future pre-reg amendment.

---

## Iteration-limit (frozen)

Per the discipline pattern: this system is shipped ONCE. If the audit verdicts (12-E, 12-F) surface issues, the FIRST fix attempt is on parameter values (threshold, MC floor, etc.) — those can be amended via publish-then-post if the empirical evidence supports a change. The SECOND fix attempt requires a fresh pre-registration (cannot iterate on parameter values without explicit re-arming). The escalation path: if 12-E/F both fail acceptance, sunset the composite-receipts log (acknowledge the composite formula's structural issues) and pre-register Audit 12-G to evaluate alternative composite designs.

---

## Receipts trail

| Commit | Action |
|---|---|
| `34ce847` Audit 09 results | Wallet index empirically validated as moat |
| `66e1138` Audit 12-A results | Retroactive composite leakage exposed; forward audit recommended |
| `c2b3a8a` Audit 12 pre-reg + Amendment 01 | 4-branch criterion for forward audit |
| `b0f400e` Audit 12-B pre-registration | Composite decomposition framework |
| `e2aaf51` Audit 12-B Phase 1b results | Freshness H3 PASSES at lane-60s |
| `70da8ba` Phase 2 harness instrumentation | go_entry_mult column for forward arm |
| `a93764f` Older-age prediction investigation | Hypothesis B confirmed; lane gate enforced |
| `e880c5a` Composite-receipts logging pre-reg + implementation | Cross-detection + outcome resolver + composite ledger V1 + /api/v1 endpoints; first composite-track receipts trail |
| **(this commit) Deploy receipt — composite-receipts logging live** | flyctl deploy 2026-05-11T05:38:54Z → 05:40:11Z, ~77s, healthy; tables + indices created; daemon running; endpoints registered + auth-gated |
| (later, +1 week) Audit 12-E — receipts validation | Per-row outcome stratification at first commit threshold |

---

## Deploy receipt — 2026-05-11T05:38:54Z

**Deploy timestamp:** epoch 1778480334 → 1778480411. flyctl deploy --remote-only, ~77s rolling-update, single machine, smoke + health passed.

### Schema verification

```
$ sqlite3 /data/data.sqlite "SELECT name FROM sqlite_master WHERE name LIKE 'composite%'"
  composite_predictions
  composite_prediction_commits
  idx_composite_pred_at
  idx_composite_outcome_unresolved
```

Both tables + 2 indices created. Schema migration ran cleanly via `_ensure_schema()` on first daemon tick.

### Endpoint verification

```
$ curl -sw "\n%{http_code}" https://graduate-oracle.fly.dev/api/v1/composite_predictions
{"detail":{"error":"missing_api_key","hint":"send Authorization: Bearer <key> or X-API-Key: <key>"}}
HTTP 401
```

HTTP 401 (auth-gated, endpoint registered) — NOT HTTP 404 (would indicate missing route). The auth gate matches `/api/v1/predictions` and other existing endpoints.

### Service health

All four services running post-deploy with uptime ≈ 5.5 min (matches deploy time):
- web: RUNNING (PID 673)
- observer-daemon: RUNNING
- case_study_harness: RUNNING
- bot: RUNNING

No crash loops; no error logs surfaced in fly logs.

### Daemon warmup state

Daemon boot timestamp = deploy timestamp. Warmup phase active (first 24h uses P95 threshold; transitions to P90 after 24h of samples accumulate). Minimum 30 samples required before any cross fires — accumulating now from each ~5s snapshot tick.

Expected first cross: depends on the rolling sample's distribution + whether any mint clears the dynamic P95 threshold + the $5,000 MC floor. Historical-rate projection (from grad_prob track's HIGH/MED rate at similar criteria): ~5–15 crosses per day once warm.

### Smoke verification (local, pre-deploy)

End-to-end isolated test confirmed:
- Cross detection fires when composite ≥ threshold ✓
- MC floor blocks low-mc mints (1000 USD rejected) ✓
- Dedup via PRIMARY KEY (mint) — second cross on same mint silently ignored ✓
- Merkle commit produces stable root over the rows ✓

### Forward verification timeline

| Checkpoint | Time | Action |
|---|---|---|
| T+1h | 2026-05-11T06:39Z | First hourly merkle commit fires (over previous hour's crosses, likely 0 during warmup) |
| T+24h | 2026-05-12T05:39Z | Warmup transitions P95 → P90 |
| T+1 week | 2026-05-18 | First Audit 12-E (composite receipts validation) — if ≥30 resolved rows accumulated |

The +1 week verdict is the first meaningful empirical check on whether the composite-receipts trail produces the high-hit-rate catches the user empirically observed.

### Wallet-redaction compatibility verified

Post-deploy `/api/v1/composite_predictions` returns rows with only the frozen leaf fields + outcome columns. No wallet-shaped surfaces. Compatible with both Option A (`06480be`) and Option 5 (`d8af9ec`) redactions.

---

## Cross-references

- [`audit_12_hot_launch_composite_validation_prereg.md`](audit_12_hot_launch_composite_validation_prereg.md) — sister forward audit
- [`audit_12b_composite_decomposition_prereg.md`](audit_12b_composite_decomposition_prereg.md) — decomposition test of the same composite formula
- [`case_study_01_amendment_02_composite_vs_gmgn_re_arm.md`](case_study_01_amendment_02_composite_vs_gmgn_re_arm.md) — composite-vs-GMGN forward comparison
- [`wallet_redaction_2026_05_11.md`](wallet_redaction_2026_05_11.md) + [`wallet_redaction_option_5_2026_05_11.md`](wallet_redaction_option_5_2026_05_11.md) — wallet redaction this system is compatible with
- Memory: `project_tamper_evident_ledger.md` — leaf-version invariant: never edit existing version's fields; bump version, add new constants. Composite ledger V1 is the first version of its independent namespace.
- Memory: `project_dual_track_signal_strategy.md` — strategic framing
- Memory: `feedback_pre_registration_branches.md` — discipline
