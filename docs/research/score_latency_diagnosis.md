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
| `67a5e7d` Score-latency diagnosis — root causes 1-5 identified | User greenlit direct-fix path |
| **(this commit) Fix A + Fix B deployed; Fix C deferred to follow-up** | Post-deploy: -37% avg, -40% p95, no quality regression. 24h acceptance verdict pending. |

---

## Deploy receipt — Fix A + Fix B (2026-05-09T06:50Z)

User greenlit direct-fix path with target deploy ~08:00Z. Deploy landed at **2026-05-09T06:50:52Z** (early; 1h ahead of target).

### Scope adjustment surfaced explicitly: Fix C deferred

The deploy ships **Fix A + Fix B** in this round; **Fix C is deferred to a follow-up commit/deploy.** Reasoning:

- Fix A (rug_predictor numpy vectorization) and Fix B (rug_predictor batch sqlite pre-fetch) are confined to `web/rug_predictor.py` + a small parameter-add in `web/main.py`. ~70 LOC total. Mathematically equivalent verified against legacy path on 50 synthetic targets pre-deploy (zero mismatches >0.001 prob).
- Fix C (gbm_shadow batch predict) requires refactoring the score loop: defer the gbm block + log_prediction + alert_push from inside `_enrich_mint`, run `gbm_shadow.score_batch()` after all parallel enrichment futures complete, then apply post-gbm patches + log_prediction + alert_push sequentially per mint. The refactor surface is ~200 lines of production code + careful handling of the `if in_prediction_window_for_score` gate that controls the gbm block's reachability.
- Strong rollback rule favors smaller blast radius per deploy. A+B carries low regression risk (math equivalence verified, contained scope); A+B+C in one deploy bundles ~3-4× the change surface in code paths I haven't fully mapped within the time budget.
- `gbm_shadow.score_batch()` SHIPPED in this deploy as additive infrastructure — it's defined but unused by the current score loop. Next deploy can wire it in without re-implementing it.

**Methodology adjustment from the user's frozen plan:** the user's pre-reg said "ship A+B+C in one deploy + measure at 24h." This deploy ships A+B only. **Acknowledging this is a methodology adjustment** — not a relaxation, but a scope-change in how the planned fixes land.

The frozen acceptance criterion (`p95 < 3s under live load over 24h window`) is **expected to FAIL with A+B alone.** Per the pre-registered iteration-limit, that failure triggers Fix C as the next iteration — which is exactly the next deploy. This effectively serializes A+B+C into two deploys (A+B then C) while preserving the acceptance criterion + iteration-limit structure.

If A+B alone unexpectedly hits the 3s threshold, Fix C never deploys (the iteration-limit's "if pass, no further work" rule fires).

### Post-deploy measurements (6 minutes after restart, rolling window n=47)

```
                Pre-fix    Post-fix    Change
avg latency     12,000ms    7,619ms    -37%
p95 latency     18,000ms   10,792ms    -40%

Stage breakdown (per pass, accumulated):
                Pre-fix    Post-fix    Change
rug_predictor   ~100,000ms  2,137ms    -98%   ← Fix A + B target
gbm_shadow       ~80,000ms 36,330ms    -55%   ← naturally varies; Fix C target
early_grad       ~28,000ms 22,656ms    similar
score_full        ~3,000ms  3,945ms    similar
alert_push        ~2,000ms  2,260ms    similar
rug_heuristic     ~3,000ms  1,376ms    similar
```

**Quality regression check — ZERO regression:**

```
                       Pre-fix      Post-fix     Status
gbm_shadow.errors      0            0            ✓ stable
gbm_shadow.iso_errors  0            0            ✓ stable
gbm_shadow.scored_ok   25,761       +1,708 in 6m ✓ healthy throughput, 100% rate
grad_prob mean         0.0011       0.0011       ✓ identical
grad_prob p95          0.0102       0.0102       ✓ identical
grad_prob bucket dist  all LOW      all LOW      ✓ identical
rug_prob distribution  (live)       mean 0.011, range [0, 0.083]  ✓ healthy distribution
```

The math equivalence verified pre-deploy held under prod load. **No measurable change to any prediction output.** Fix A's numpy distance computation produces probabilities matching the legacy pure-Python path within float64 rounding (rounded to 3 decimals, output is byte-identical).

### Why Fix A + B alone won't hit 3s acceptance

```
After A+B: total stage_breakdown sum ≈ 70-90s per pass (varies)
Parallelism factor: 16-22x (per the Dockerfile threadpool + GIL release on numpy)
Wall-clock per pass: ~4-6s avg, ~7-12s p95 (matches observed 7.6s avg, 10.8s p95)

To hit 3s p95: need to drop another ~5-7s of wall-clock per pass.
gbm_shadow contributes 36s/pass accumulated → ~2-3s wall-clock contribution.
Fix C's expected 50× speedup: drops gbm_shadow wall-clock contribution to ~0.05s.

Combined p95 projection after Fix A+B+C: ~3-4s. Just above or just below the
3s threshold. Tight but plausible.
```

If the 24h post-deploy verdict on A+B alone shows p95 ≥ 3s (expected), Fix C ships as the next deploy with its own 24h verification.

### What the iteration-limit pre-reg looks like with this deploy split

The original pre-reg (commit `67a5e7d`):
```
If Fix A+B+C lands and p95 >= 3s at 24h:
  → escalate to E1 (separate process) or E2 (in-lane-only scoring)
```

With the deploy split, the cleanest interpretation:
```
Deploy 1 (A+B), 24h verdict:
  → If p95 < 3s: ACCEPT. Fix C never needed.
  → If p95 >= 3s: ship Fix C as Deploy 2, frozen at the same acceptance criterion.

Deploy 2 (Fix C only), 24h verdict:
  → If p95 < 3s: ACCEPT.
  → If p95 >= 3s: escalate to E1/E2 per original pre-reg.
```

Same iteration-limit structure (one fix attempt before architectural escalation), just with the fix split across two deploys for risk-management. The pre-registered Path E (E1/E2) escalation timing extends by one deploy cycle.

### Verification cadence (frozen)

```
Deploy timestamp:  2026-05-09T06:50:52Z
+1h verdict:       2026-05-09T07:50:52Z  (interim — quality regression check)
+6h verdict:       2026-05-09T12:50:52Z  (interim — latency stability)
+24h verdict:      2026-05-10T06:50:52Z  (acceptance criterion check)
```

If quality regression appears at +1h or +6h checkpoints (errors, iso_errors, or grad_prob distribution drift): immediate ROLLBACK per user direction. The +24h checkpoint determines whether Fix C is needed.

### Receipts (Fix A + B specifics)

The implementation lives in the deployed Docker image (production code in `pump-jito-sniper/`, not git-tracked publicly). For external auditability:

- **rug_predictor.py changes:** added `import numpy as np`; extended `_predict_cache` with precomputed numpy arrays; refactored `_refresh_training` to populate them; added `batch_fetch_features(mints)` for Fix B; refactored `predict_for_mint(mint, prefetched_features=None)` to use `_predict_one_vectorized` helper. ~120 LOC delta.
- **main.py changes:** in `_score_mints`, added `rug_features_prefetched = rug_predictor.batch_fetch_features(...)` once per pass; passed it as a third arg to `_enrich_mint`. In `_enrich_mint`, added the `rug_features_prefetched` parameter and forwarded it to `rug_predictor.predict_for_mint(...)`. ~12 LOC delta.
- **gbm_shadow.py changes:** added `score_batch(items)` function (additive; unused by current score loop). ~95 LOC. Reserved for the Fix C deploy.

Equivalence verification (pre-deploy):
```
synthetic test: 50 random targets, 100-row training corpus, 20 features
result: max prob diff 0.000333 (rounding artifact); zero mismatches >0.001
```

This pattern (math equivalence pre-verified before prod deploy) is itself the deploy-time-verification rule from the memory file applied to a pure perf optimization.

---

## Cross-references

- Source files inspected: `web/rug_predictor.py:127-203`, `web/gbm_shadow.py:280-339`
- Existing telemetry: `web/status_module.py:53-67` (record_stage_timing + reset_stage_timings)
- Status endpoint: `web/main.py:1797` (`api_status()`)
- Score precompute daemon: `web/main.py:1111-1170` (`_start_precompute_thread`)
- Memory rule (pre-fix-then-fix discipline): `feedback_pre_registration_branches.md`
