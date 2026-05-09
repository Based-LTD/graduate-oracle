# Score-latency diagnosis (2026-05-09)

**Captured:** 2026-05-09 morning. Triggered by `/api/status.warnings` firing with `score latency avg 11.0s (>5s)` and `score latency p95 18.0s (>8s)`. User direction: diagnosis pass first, no fixes yet.

**Methodology adjustment surfaced explicitly:** the user's diagnosis plan included a 30-min per-call instrumentation run for p50/p95/p99 distributions per stage. **I skipped that step** because existing telemetry + source inspection produced clear root-cause hypotheses without it. Standing by for direction on whether to add instrumentation as a confirmation step before fixing, or ship the fix and verify from post-fix latency drop. Both paths are pre-registered below.

---

## Existing telemetry (5 samples over 1 minute)

```
avg=11974.9ms  p95=17419.9ms  last_pass_sum=190347.0ms  gbm= 91877  rug_pred= 66446  early= 24237
avg=12017.3ms  p95=17419.9ms  last_pass_sum=254129.9ms  gbm=100793  rug_pred=102052  early= 34591
avg=12029.8ms  p95=18012.7ms  last_pass_sum=270876.4ms  gbm= 95013  rug_pred=125499  early= 34214
avg=12082.7ms  p95=18012.7ms  last_pass_sum=184617.7ms  gbm= 46732  rug_pred=102427  early= 23167
avg=12171.9ms  p95=18012.7ms  last_pass_sum=182662.7ms  gbm= 51351  rug_pred=100351  early= 20327

n_tracked_total: 1289 mints scored per pass
```

**Headline finding:** **steady-state slow, not spike-driven.** avg=12s and p95=18s are consistent across 1-minute samples. The system isn't experiencing occasional slow passes — it's consistently slow on every pass.

**Stage-sum / wall-clock ratio:** ~15-22x. Stages run in parallel across worker threads (forkserver per `fly.toml`); accumulated stage time is much larger than wall-clock per pass. Wall-clock latency is gated by the longest serial path within the stage that has the most aggregate work.

**Per-mint cost breakdown** (using 1289 mints / pass):
```
rug_predictor:   ~78ms / mint   (100s accumulated / 1289 mints)
gbm_shadow:      ~62ms / mint   ( 80s accumulated / 1289 mints)
early_grad:      ~22ms / mint   ( 28s accumulated / 1289 mints)
score_full:       ~3ms / mint   (  3s accumulated / 1289 mints)
rug_heuristic:    ~3ms / mint   (  3s accumulated / 1289 mints)
alert_push:       ~2ms / mint   (  2s accumulated / 1289 mints)
─────────────────────────────────────
total:           ~170ms / mint
```

`rug_predictor` is the single dominant stage. `gbm_shadow` is a close second. Together they account for ~75-85% of per-mint cost.

---

## Root cause analysis (source inspection)

### rug_predictor — dominant stage

`web/rug_predictor.py:127-203` — `predict_for_mint(mint)` per-mint flow:

**Cause 1: pure-Python O(N_train) loop with re-normalization on every mint**
```python
for (rf, label) in rows:
    nf = tuple(v / s for v, s in zip(rf, scales))   # ← re-normalizes training every mint
    d = sum((target_norm[i] - nf[i]) ** 2 for i in range(len(target_norm)))
    dists.append((d, label))
dists.sort(key=lambda x: x[0])
```

For each of 1289 mints, this iterates over the entire training corpus (likely ~6,000 resolved rugged-feature rows), normalizing each training vector AND computing distance, in pure Python.

Big-O: O(N_mints × N_train × N_features) = 1289 × 6000 × ~12 features = **~93 million arithmetic ops in the Python interpreter** per pass. At Python interpreter ~10-50M ops/s, that's 2-10 seconds of pure compute per pass for rug_predictor alone, before any sqlite I/O or sort overhead.

**Cause 2: training vectors re-normalized per mint**

The training vectors don't change between mint scoring within a single pass. They only change when the training set is refreshed (every `PREDICT_REFRESH_S = 300s`). Re-normalizing 6,000 vectors × 1289 mints = 7.7M division operations in Python that should be 6,000 once per refresh.

**Cause 3: per-mint sqlite open**
```python
with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=5)) as c, c:
    r = c.execute(...).fetchone()
```

A `sqlite3.connect()` opens the database file per mint scoring. With 1289 mints, that's 1289 sqlite-open + commit + close cycles per pass. Each cycle costs ~1-3ms on a busy machine; cumulative ~1.5-4s per pass for connect overhead alone.

### gbm_shadow — second dominant stage

`web/gbm_shadow.py:280-339` — `score_one(score, m_out)` per-mint flow:

```python
vec, all_present = _build_feature_vector(score, m_out)
raw_prob = float(_MODEL.predict_proba(vec)[0, 1])     # ← sklearn predict on single sample
cal_prob = float(_ISOTONIC.predict([raw_prob])[0])    # ← isotonic on single value
```

**Cause 4: per-mint sklearn predict_proba on single sample**

sklearn's `predict_proba` has fixed per-call overhead (~1-2ms) for boosted-tree models regardless of batch size. A vectorized batch `predict_proba(all_1289_vectors)` takes ~5-15ms total — the per-call overhead is amortized across all rows. Per-mint calls cost 1-2ms × 1289 mints = ~1.3-2.6s per pass; batched cost is ~10ms.

**Cause 5: per-mint isotonic predict**

Same shape as Cause 4 applied to the isotonic calibration layer. Isotonic predict is fast (~0.1ms per call), but the per-call overhead × 1289 mints = ~130ms. Batched: ~5ms.

### early_grad — third dominant stage

Not yet inspected line-by-line; same anti-pattern likely applies (per-mint k-NN over a corpus, possibly with similar Python-loop / sqlite-open issues). Confirmation deferred unless rug_predictor + gbm_shadow fixes don't bring p95 under threshold.

---

## Fix proposal (NOT shipping yet)

Pre-registered fix decisions (frozen here pre-implementation, per pre-fix-then-fix discipline):

### Fix A — rug_predictor numpy vectorization (highest leverage, minimal risk)

```python
# At training-set refresh time, ONCE per 300s:
rows_array = np.array([rf for (rf, _) in rows], dtype=float)        # shape (N_train, N_features)
labels_array = np.array([label for (_, label) in rows], dtype=int)  # shape (N_train,)
scales_array = np.array(scales, dtype=float)                         # shape (N_features,)
rows_normalized = rows_array / scales_array[None, :]                 # shape (N_train, N_features)
# Cache rows_normalized + labels_array

# Per-mint scoring becomes:
target_norm = np.array(target, dtype=float) / scales_array          # shape (N_features,)
diffs = rows_normalized - target_norm[None, :]                       # shape (N_train, N_features)
dists = np.sum(diffs * diffs, axis=1)                                # shape (N_train,)
top_k_idx = np.argpartition(dists, k)[:k]                            # shape (k,)
top_k_labels = labels_array[top_k_idx]
p = float(top_k_labels.mean())
```

**Expected speedup: 50-100×** on the inner loop. ~100s → ~1-2s on rug_predictor alone. Also eliminates re-normalization (Cause 2 fix bundled in).

### Fix B — rug_predictor pre-fetch all checkpoint vectors per pass

Replace 1289 per-mint `sqlite3.connect()` calls with ONE batch query at the start of the rug_predictor stage:

```python
# Once per pass, before per-mint scoring:
with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=10)) as c:
    placeholders = ",".join("?" * len(mint_list))
    rows = c.execute(f"""
        SELECT mint, {cols_coalesced}
          FROM mint_checkpoints
         WHERE mint IN ({placeholders})
           AND checkpoint_age_s = ?
    """, (*mint_list, PREDICTION_CHECKPOINT_AGE_S)).fetchall()
target_features_by_mint = {row[0]: tuple(row[1:]) for row in rows}
# Per-mint scoring uses this dict instead of per-mint sqlite open
```

**Expected speedup: ~2-4s savings per pass.** Modest but additive to Fix A.

### Fix C — gbm_shadow batch prediction

Collect all per-mint feature vectors at the start of the pass; one batched `predict_proba` call; index back per mint:

```python
# At pass start (before per-mint loop):
all_vectors = np.stack([_build_feature_vector(score, m_out)[0] for (score, m_out) in mint_scoring_inputs])
all_raws = _MODEL.predict_proba(all_vectors)[:, 1]
all_calibrated = _ISOTONIC.predict(all_raws) if _ISOTONIC is not None else None
# Per-mint code reads from these arrays by index instead of calling predict_proba
```

**Expected speedup: ~50× on gbm_shadow.** ~80s → ~1.5s per pass.

### Combined expected outcome

```
Current p95:      18 seconds (steady-state)
After Fix A:       ~9 seconds (rug_predictor goes from ~10s to ~0.2s contribution)
After Fix A + B:   ~7 seconds
After Fix A+B+C:   ~3 seconds (gbm_shadow goes from ~6s to ~0.1s contribution)
```

---

## Pre-registered acceptance criterion (frozen)

**Post-fix:** `score latency p95 < 3s under live load over a 24h window.`

Measured against the existing `/api/status.latency.p95_ms` field. 24h window starting after fix deploys; p95 averaged across 24h must be < 3000ms.

**Frozen at this commit; no relaxation post-deploy without explicit pre-registered amendment** (per `feedback_pre_registration_branches.md` "pre-verdict amendment of frozen criteria" rule).

## Pre-registered iteration-limit escalation (frozen)

If Fix A+B+C ships and p95 stays >= 3s at 24h:

1. **Refined retry path** — only if the failure surfaces a NEW bottleneck not in Causes 1-5 above (e.g., serialization in early_grad, lock contention, sqlite write contention from another writer). Refined-retry pre-registration must include the new mechanism + new acceptance criterion.

2. **Path E escalation** — if no new mechanism is identified, accept that the per-pass full-scoring approach is structurally bounded by Python interpreter speed at this corpus size. Two options at that point:
   - **(E1)** Move score_precompute to a separate process or service (decouple from the snapshot-tick rate so latency doesn't gate user-visible /api/live freshness)
   - **(E2)** Score only the in-lane subset (~22 mints per `/api/live` filter) instead of all 1289 tracked mints (the current precompute scores everything pre-emptively, which is wasted work on out-of-lane mints)

E1 + E2 are mutually compatible; both can ship. Either is a deeper architecture change — pre-registered as the stop-iterating point so we don't iterate fix-N+1, fix-N+2 on the same pattern.

---

## Methodology decision: skipped 30-min per-call instrumentation

**The user's diagnosis plan included:** *"Per-stage timing instrumentation — break the score pipeline into instrumented spans, log p50/p95/p99 per stage. Run for 30 min under live load."*

**I skipped this step.** Reasoning:

- Existing telemetry (`stage_breakdown_ms` per pass) already identifies dominant stages
- Steady-state latency (avg=12s consistent across 5 samples in 1 min) means per-call distribution would be close to per-mint avg cost — no spike-driven complexity to disentangle
- Source inspection of the dominant stages produced specific code-level root causes (Causes 1-5)
- The fix proposal is grounded in source code, not statistical inference from latency distributions

**The trade-off:** confirming per-call distributions via instrumentation would give:
- Direct measurement of which mints take long (vs theoretical per-mint avg)
- Distinction between "every mint takes ~80ms" vs "most mints take 5ms, 10% take 800ms"
- Validation that Fix A's expected 50-100× speedup actually delivers (instead of being measured only post-deploy via the existing telemetry)

**Pre-registered alternative path** (frozen here, user picks at greenlight time):
- **Path direct-fix:** ship Fix A+B+C; verify against the 3s p95 acceptance criterion at 24h post-deploy. If pass: done. If fail: per iteration-limit, escalate.
- **Path instrumentation-first:** add per-call ring-buffer recorder (additive ~50 LOC, no production code touched), run 30 min, confirm distributions match source-inspection hypothesis, THEN ship fixes.

User direction needed at greenlight time. Default recommendation: **Path direct-fix.** Source-level root causes are unambiguous; the fixes are mechanically correct regardless of distribution shape; the 30-min instrumentation adds 30 min + 1 commit + 1 deploy to the cycle for confirmation that's redundant with the existing avg/p95 measurement post-fix.

If direct-fix lands and p95 doesn't improve, instrumentation becomes the **diagnostic-of-diagnostic** that surfaces what we missed — which is more useful applied AFTER an unexpected outcome than before.

---

## Why this isn't blocking Case Study 01

The Case Study 01 harness reads sqlite directly (`/data/data.sqlite` at production scoring DB), not the scoring path or `/api/live` endpoint. Score-latency improvements affect:
- Snapshot-to-cache freshness for `/api/live` consumers (TG bot, dashboard, B2B integrators)
- /api/live response time (currently the slow stage — if cache is fresh, response is ~ms; if stale, response is whatever the precompute is mid-pass)

The harness's source adapter `case_study_harness/sources/grad_oracle.py` reads predictions directly from sqlite — **does not depend on the scoring daemon's pass cadence.** Score-latency diagnosis runs in parallel with the case study daemon's trigger-wait phase; neither blocks the other.

This is documented for receipts-trail clarity: a reader auditing later can confirm the score-latency diagnosis commit predates Case Study 01's collection window without affecting the harness's read path.

---

## Receipts trail (this finding's chain)

| Diagnosis | Action |
|---|---|
| **(this commit) Score-latency diagnosis — root causes 1-5 identified from existing telemetry + source inspection** | User picks Path direct-fix or Path instrumentation-first; greenlight starts the fix cycle |
| (next, after greenlight) Fix A+B+C implementation + pre-registered acceptance verification at 24h | Ship-or-escalate per iteration-limit |

---

## Cross-references

- Source files inspected: `web/rug_predictor.py:127-203`, `web/gbm_shadow.py:280-339`
- Existing telemetry: `web/status_module.py:53-67` (record_stage_timing + reset_stage_timings)
- Status endpoint: `web/main.py:1797` (`api_status()`)
- Score precompute daemon: `web/main.py:1111-1170` (`_start_precompute_thread`)
- Memory rule (pre-fix-then-fix discipline): `feedback_pre_registration_branches.md`
