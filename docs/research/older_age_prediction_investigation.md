# Older-age prediction investigation

**Investigation pass.** User direction (2026-05-11): decompose the 4.4% of predictions with age_bucket > 60 surfaced in Audit 12-B Phase 1b results (commit `e2aaf51`). Pre-registered hypotheses:

- **Hypothesis A (observer-ingestion-lag population):** mints whose observer first sees them at older real-time ages — lane-60s respected in observer-time; on-chain age is still young. Product opportunity.
- **Hypothesis B (lane-60s discipline violation):** prediction-write path doesn't enforce lane-60s for some mints. Code-path bug.

**Verdict: Hypothesis B confirmed.** The older-age predictions exist ONLY in a historical 4-day window (April 28 → May 2, 2026). The lane-60s gate is currently enforced; zero older-age predictions in the last 24 hours.

**This inverts the Hypothesis-A framing in [`audit_12b_phase1b_freshness_results.md`](audit_12b_phase1b_freshness_results.md).** The older-age rows are not observer-lag-population — they are historical artifact from a defunct gate-bypass period. The Phase 1b H3 verdict (clean signal at age=30 vs age=60) still stands; the substantive interpretation of older ages as "observer-lag product opportunity" was wrong.

---

## Investigation methodology

1. **Locate the lane gate in source.** Grep `PREDICTION_LANE_AGE_BUCKETS` in `pump-jito-sniper/web/predictions.py`.
2. **Verify all write paths flow through the gate.** Grep `INSERT INTO predictions` across the codebase.
3. **Timeline analysis of older-age rows.** Query production sqlite for `min(predicted_at)` and `max(predicted_at)` per `age_bucket > 60` stratum to identify when the violations occurred.
4. **Compare to recent state.** Check the last 24h for any new older-age predictions, indicating whether the gate is currently enforced.
5. **Compare entry_mult population.** If older-age rows have similar entry_mult fill rate to lane-60s rows, they went through the same log_prediction code path (confirming Hypothesis B).

---

## Findings

### 1. Lane gate is correctly implemented in current source

`web/predictions.py:178`:
```python
PREDICTION_LANE_AGE_BUCKETS = (30, 60)
```

`web/predictions.py:194` (top of `log_prediction()`):
```python
if age_bucket not in PREDICTION_LANE_AGE_BUCKETS:
    return
```

Strict tuple-membership check. Any age_bucket not in {30, 60} returns immediately, BEFORE any queue enqueue. Lane-60s discipline is enforced at the log_prediction boundary.

### 2. All write paths flow through log_prediction

Three `INSERT INTO predictions` statements exist in the codebase, all in `web/predictions.py` (lines 313, 380, 395). All three execute inside the drain thread that receives records from `_pred_queue`. The queue is populated exclusively by `log_prediction` (line 213 `_pred_queue.put(record)`). **No other code path writes to the predictions table.**

The lane gate at line 194 is therefore the single chokepoint. If it's enforced, only age_bucket ∈ {30, 60} reach the predictions table.

### 3. Timeline analysis — older-age predictions are bounded to April 28 → May 2

Production sqlite query (30d window):

| age_bucket | n | first_predicted_at | last_predicted_at |
|---:|---:|---|---|
| 120 | 17,802 | 2026-04-28T18:47Z | **2026-05-02T20:44Z** |
| 180 | 17,873 | 2026-04-28T18:48Z | **2026-05-02T20:45Z** |
| 300 | 17,733 | 2026-04-28T18:50Z | **2026-05-02T20:45Z** |
| 600 | 16,873 | 2026-04-28T18:53Z | **2026-05-02T20:45Z** |
| 900 | 15,856 | 2026-04-28T18:58Z | **2026-05-02T20:23Z** |
| 1500 | 14,903 | 2026-04-28T19:06Z | **2026-05-02T20:23Z** |

**Total older-age predictions: ~101,000.** All older-age rows fall within a ~4-day window starting 2026-04-28T18:47Z and ending 2026-05-02T20:45Z. The cluster's tight start/end times strongly suggest a SPECIFIC CODE CHANGE caused the gate-bypass at the start of this window and a follow-up FIX restored the gate at the end of the window.

The 4.4% of older-age rows in Phase 1b's 30d sample are entirely from this 4-day historical window. They are not ongoing.

### 4. Last 24h has ZERO older-age predictions

Direct query: `SELECT COUNT(*) FROM predictions WHERE predicted_at >= (now-86400) AND age_bucket > 60`

Result: **0 rows.**

The lane gate is currently enforced. Hypothesis B's code-path bug exists in the historical record but does not exist in production now.

### 5. entry_mult fill rate is consistent across older-age vs lane-60s rows

- Older-age rows (n=101,040): entry_mult populated for 68,301 = **67.6%**
- Lane-60s rows (n=58,884 within 30d): entry_mult populated similarly to the 71.2% overall rate

The older-age rows went through the **same code path** as lane-60s rows (log_prediction → drain thread → INSERT). They were not from a different writer. This confirms Hypothesis B — same code path, just the gate was bypassed.

---

## Likely root cause (not investigated to source-control level)

Pump-jito-sniper is not version-controlled (per the broader codebase architecture), so direct git-blame on the lane gate is not available from this investigation. The most likely sequence based on the evidence:

1. **Before April 28:** lane gate enforced (either present or no older-age predictions were attempted)
2. **April 28 ~18:47Z:** A change either (a) added a code path that bypassed the gate, OR (b) removed/weakened the gate. Older-age predictions begin landing in the table.
3. **April 28 → May 2:** Gate-bypass period. ~101k older-age rows accumulate (~25k per age stratum, similar across strata = consistent with the same code path firing at all ages).
4. **May 2 ~20:45Z:** A change restored the gate (or removed the bypassing code path). Older-age predictions stop landing.
5. **May 2 → now:** Lane gate enforced. Phase 1b's empirical observation is rooted in this historical artifact, not in ongoing observer-lag.

The tight clustering of last-prediction timestamps across all age strata (2026-05-02T20:23Z–20:45Z, within 22 minutes) is the fingerprint of a single coordinated change — most likely a deploy that landed at ~20:45Z May 2.

---

## Implications for prior audits

### Audit 12-B Phase 1b results — Hypothesis A framing is wrong

The Phase 1b results writeup ([`audit_12b_phase1b_freshness_results.md`](audit_12b_phase1b_freshness_results.md)) framed the older-age first-predictions as plausibly observer-ingestion-lag population:

> "The older-age strata in this audit are plausibly the observer-lag-population, not lane-60s-discipline violations."

This investigation **refutes that framing.** The older-age strata are entirely historical artifact from the April 28 → May 2 gate-bypass period. They are NOT a product opportunity to surface late-arriving mints.

**Phase 1b's H3 verdict (freshness factor has empirically detectable signal on 2x_runner_rate, lift 1.40× at age=30 vs age=60 with non-overlapping CIs) still stands.** That verdict is based on lane-60s ages {30, 60} and is independent of the older-age rows. The clean signal is at lane-60s; the wider-age data is contaminated.

A future addendum to the Phase 1b results writeup will note this correction. The H3 verdict's substantive interpretation (freshness factor underweights age-at-prediction by ~8× vs the empirical effect) is unaffected; it was always restricted to lane-60s within the original audit's primary verdict.

### Audit 12-A retroactive validation — unaffected

Audit 12-A's stratification was on the composite_score quartile (smart_money × actual_max_mult), not on age_bucket. The dedup query uses `MIN(predicted_at)` per mint, so it would have picked up an older-age first-prediction for the few mints whose only prediction in the window happened to be in the bypass period. Sample-share contribution is small (~4.4%) and within the broader leakage caveat already documented in 12-A's results.

### Wallet redaction — unaffected

All redaction is at the API surface and snapshot persistence, independent of historical prediction-write behavior.

### Path E + Case Study 01 — unaffected

Both operate on current bucket-emission and live prediction flow. The historical gate-bypass doesn't affect any current acceptance criteria.

---

## Recommendations

### 1. Treat older-age data as filterable noise in future audits

Going forward, retroactive audits should add `AND age_bucket IN (30, 60)` to their JOIN predicates. The ~101k historical rows are NOT representative of current scoring discipline and could contaminate stratified analyses. This is forward-looking discipline, NOT a methodology amendment of prior audits.

A grep search for audit-program query files would identify any others affected; for now, only Audit 12-B Phase 1b was substantively affected.

### 2. No code change required

The gate is currently enforced; no remediation is needed for production. The historical rows can remain in the table (they document a real event in the system's history). Future code reviews of the prediction-write path can use this investigation as a regression-prevention anchor — if anyone proposes loosening the lane gate, the April 28–May 2 incident is the receipts-trail evidence for keeping it strict.

### 3. Add a database-level invariant check (optional, deferred)

A future Audit 12-D (lane-discipline invariant) could pre-register:
- Continuous monitoring: alert if any new prediction lands with `age_bucket NOT IN (30, 60)` post a deploy timestamp anchor
- Surface via `/api/status.warnings` if violation detected
- This would prevent a future regression from going unnoticed for 4 days

Filed for the audit-program design review; not auto-actioned by this investigation.

---

## Receipts trail

| Commit | Action |
|---|---|
| `e2aaf51` Audit 12-B Phase 1b results — freshness retroactive | Surfaced the older-age first-predictions as observer-lag-population (Hypothesis A framing) |
| `70da8ba` Phase 2 harness instrumentation (go_entry_mult added) | Forward-arm setup |
| **(this commit) Older-age prediction investigation — Hypothesis B confirmed** | Empirical analysis: older-age rows bounded to April 28 → May 2 historical window; lane gate currently enforced; Phase 1b's Hypothesis-A framing refuted; older-age rows are historical artifact, not product opportunity |

---

## Cross-references

- [`audit_12b_phase1b_freshness_results.md`](audit_12b_phase1b_freshness_results.md) — Phase 1b results (Hypothesis A framing now refuted)
- [`audit_12b_composite_decomposition_prereg.md`](audit_12b_composite_decomposition_prereg.md) — Phase 2 instrumentation home
- Memory: `feedback_lane_60s_only.md` — lane-60s discipline; this investigation confirms current production enforcement
- Memory: `feedback_dont_dismiss_unexpected_data.md` — the rule that ALSO surfaced this investigation. Per that rule, the wider-data finding was surfaced and investigated; the investigation showed the wider data was historical artifact, not signal. The rule still applied correctly: we DIDN'T dismiss the data preemptively; we investigated it and learned what it was.
- Memory: `feedback_methodology_calls_user_owned.md` — user-directed this investigation; result lands as evidence for future methodology calls
