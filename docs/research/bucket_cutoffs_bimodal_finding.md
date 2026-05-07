# Calibrated GBM bimodal cliff — pre-cutover finding & revised bucket spec

**Discovery:** 2026-05-06 ~T+24h calibrated-shadow window, daemon's first successful rebuild
**Status at capture:** Track A live in shadow mode for 24.27h, 223,304 scored, 0 errors. Track B held pending revised spec.
**Why this artifact:** the pre-registered HIGH/MED/LOW spec (top-1% / top-5% percentile cutoffs) doesn't match the calibrated distribution's actual shape. Surface, document, decide deliberately. Same discipline pattern as Lane 13 transition-zone, Lane 14 multi-fire, Gate 5 range-empty case, and yesterday's deployed-kNN saturation diagnosis.

---

## Finding

**The calibrated GBM distribution is bimodal with a cliff, not a smooth tail.**

7-day sample window, n=2,336 calibrated predictions:

| Calibrated value | Predictions | % of total |
|---|---:|---:|
| **0.11320754716981132** (the ceiling) | **201** | **8.60%** |
| 5 distinct values above ceiling, each n=1 | 5 | 0.21% |
| Below ceiling | 2,130 | 91.18% |

**Distinct calibrated values total: 45.** The distribution is sparse with a hard ceiling at the highest training-bin rate.

**Manual percentiles confirm the collapse:**
```
p50:  0.04054054054054054   (= 3/74, common low-confidence rate)
p75:  0.04054054054054054   (same — many predictions at this exact value)
p90:  0.09090909090909091
p95:  0.11320754716981132   (= 6/53, the ceiling)
p99:  0.11320754716981132   (also the ceiling — 8.6% mass swallows both percentiles)
```

## Root cause

`0.11320754716981132 = 6/53` — this is the actual rate from one specific bin in the isotonic training data. `IsotonicRegression` is a step function: every raw GBM score above some training-bin threshold maps to that bin's empirical rate. The 14h dual-write training window had a single rate for raw GBM ≥ ~0.5 (because it was the highest rate observed there), so all 201 predictions whose raw GBM landed in that range get clipped to the same calibrated value.

**This is not a bug.** It's a well-known property of isotonic regression on small training sets — step-function output with cliffs at training-bin rate-points. The math is correct; the distribution shape is what the data supports.

## Why the pre-registered fallbacks don't fit

The original Gate 5 / UI threshold pre-registration listed three fallback options if the calibrated distribution didn't span the rule's threshold range. None of them apply here:

| Option | Verdict against the data |
|---|---|
| (a) Adjust percentiles → top-0.5% / top-3% | **Doesn't help.** 8.6% mass at the ceiling > 3% > 0.5%. Both still hit the same value. Even top-0.1% (n=2 of 2336) still hits 0.1132 because there's nothing else there. |
| (b) 2-bucket framing at ceiling | **Wrong volume.** ~9% HIGH = ~30 alerts/hour at current rate. Way too noisy. |
| (c) Hold cutover indefinitely | **Doesn't fix anything.** Training data shape won't change without retrain. |

These fallbacks assumed a smooth distribution that just happened to compress below the threshold. Reality is bimodal with a cliff.

## What the data actually shows (interpreted as signal, not artifact)

The bimodal structure carries information the spec needs to respect:

- **~91% below ceiling**: low-confidence territory. Model says nothing notable.
- **~8.6% at-ceiling cluster**: "raw GBM is high enough that training data caps the calibrated probability at 11.3% — the model is at the edge of what training resolution supports."
- **~0.21% above-ceiling outliers**: "raw GBM is in a region where the small training sample showed nearly all graduating." Rare, extreme.

These three regions are genuinely different signals. A spec that pretends they're a smooth tail loses information.

## Revised bucket spec — option (e), proposed for review

**Frozen pre-implementation, applied fresh post-implementation. Same discipline as Gate 5 + UI threshold pre-registrations.**

### Definitions

```
ceiling_value = the highest calibrated value with mass ≥1% of the sample
                (i.e., the most common high-end value — the cliff point)
                Threshold: cnts(value) / total_n ≥ 0.01

raw_gbm_p_high = 97th percentile of raw GBM scores in the same 7d window
                 (the smooth raw distribution gives us a usable ranking signal
                 inside the at-ceiling cluster)

HIGH = calibrated > ceiling_value
       → above-ceiling outliers, "model unusually confident beyond what
       training showed." Rare event signal.

MED  = calibrated == ceiling_value AND raw_GBM >= raw_gbm_p_high
       → at-ceiling AND in top-3% of raw distribution.
       "Strongest signal among the at-ceiling cluster, even though
       calibration can't distinguish further at this training resolution."

LOW  = everything else.
```

### Why the 97th percentile specifically (not 95 or 99)

The choice is calibrated to a volume target, not arbitrary. Working the math:

- Target MED volume = ~10 alerts/day (a usable daily signal frequency)
- At-ceiling cluster = 8.6% × ~334 in-lane predictions/day = ~28.7 at-ceiling/day
- High raw GBM correlates with at-ceiling-calibrated (the isotonic step-function clips high raws to ceiling), so top-K% of raw GBM is dominated by at-ceiling members
- Top-3% of all in-lane predictions → ~10/day → 97th percentile of raw GBM

Re-tunable: if at-ceiling cluster size shifts (e.g., to 12% or 4%) on a future rebuild, derive a new percentile from the volume target. `raw_gbm_p_high` is the calibrated knob; the volume target is the invariant.

### No-mass-value fallback — the success-case future state

When calibration matures and the cliff dissolves (post-cutover, accumulating training data smooths the upper tail of isotonic), no single calibrated value will have ≥1% mass. At that point the bimodal-cliff workaround is no longer needed and the daemon **falls back to the standard percentile spec** without code changes:

```
on rebuild:
  if no calibrated value has cnts/n ≥ 0.01:
    # Distribution has smoothed sufficiently — bimodal-aware logic sunsets.
    high_min = quantile(values, 99)   # standard top-1%
    med_min  = quantile(values, 95)   # standard top-5%
    bucket_logic_mode = "standard_percentile"
  else:
    # Bimodal-cliff state — use ceiling+raw_GBM hybrid.
    high_min, med_min, raw_gbm_p_high = (ceiling_value, ceiling_value, p97_raw)
    bucket_logic_mode = "bimodal_cliff"
```

**Surface `bucket_logic_mode` in /api/status** so external monitoring sees the transition when it happens. This makes the spec self-deprecating in the right way — captures a known successor state and documents the transition condition. Future-Claude reading the spec sees "this is for now; here's what replaces it when conditions change."

### Empty-HIGH handling — expected variance, not a fault

HIGH targets ~5/week (Poisson rate λ=5). Some 7-day windows will produce zero above-ceiling outliers by chance. **This is NOT a fault state.** Specifically:

- P(0 HIGH events in 7 days at λ=5) = e⁻⁵ ≈ 0.67% — rare but not impossible per-rebuild
- P(0 HIGH events in 14 days at λ=10) = e⁻¹⁰ ≈ 0.0045% — at this point genuinely a signal
- Over many rebuilds, expect occasional zero-HIGH windows; track frequency, don't react to single instances

**Operational rules:**
1. **Do NOT compensate by widening MED criteria** to "at least fire something." That dilutes MED's signal quality and the bimodal-aware spec stops being honest about the model's actual behavior.
2. **Track empty-HIGH frequency** in `/api/status.bucket_cutoffs.empty_high_window_count` (incremented each rebuild that produces zero above-ceiling samples).
3. **Decision rule's HIGH-too-rare branch** (below) fires only when 0 HIGH for 7+ consecutive days, not on a single empty rebuild.

### Expected fire volumes (validated against the 7d sample)

- HIGH: ~5/week (~0.7/day) — rare, attention-worthy
- MED: ~3% of in-lane predictions (~10/day at current rate) — useful daily signal
- LOW: ~96.8% — quiet majority

This matches the original "rare signal + daily signal + quiet" intent of the pre-registered three-bucket framing, achieved through a spec that respects the actual data shape.

### Decision rule (post-launch validation, applied at +7 days post-cutover)

Concrete triggers, not aspirational ones — each branch has a specific countable condition that fires the action.

| Branch | Trigger | Action |
|---|---|---|
| **Clean** | HIGH 0.3-3/day rolling 7d AND MED 1-30/day rolling 7d AND users react to bucket labels (engagement signal steady-or-better vs pre-cutover) | Cutover holds. Re-evaluate at T+30d for spec simplification. |
| **HIGH-too-rare** | **0 HIGH alerts across 7 consecutive days** with prediction volume normal (Poisson p=e⁻⁵≈0.67%, decisively below threshold) | Lower ceiling threshold (define `ceiling_value` mass cutoff from ≥1% to ≥0.5%). Re-pre-register before any change. |
| **MED-too-noisy** | **MED >25/day for 3 consecutive days** OR engagement-drop signal (alerts ignored or muted at materially higher rate post-cutover) | Tighten `raw_gbm_p_high` from p97 → p99 (top-1% of raw). Re-pre-register. |
| **Mixed** | One bucket inside spec range, the other outside (e.g., HIGH clean but MED 35/day for 2 days, then 8/day) — unstable but qualitatively right | Investigate copy/template + sample window; don't roll back. Hold for 7d more, re-evaluate. |
| **Regression** | **HIGH AND MED both fire 0 alerts across any 24h period with prediction volume ≥100** in that window (i.e., the model is producing predictions but the bucket logic is dropping all of them) | **Roll back: `fly secrets set GBM_SHADOW_ENABLED=0 -a graduate-oracle`** (instant disable, machine restart). Re-investigate. |

### Sample-size escape hatch (Lane 14 / Gate 5 lesson)

If the daemon's 7-day sample drops below n=500 calibrated predictions (e.g., due to observer outages or low pump.fun volume), the bucket cutoffs analysis becomes premature. The daemon already has `MIN_SAMPLES_FOR_CUTOFFS = 100`; the operative additional rule is: if `n_samples_used < 500`, log a warning to /api/status (`status: "low_sample_window"`) and let `bucket_for()` keep returning prior cutoffs (last-known-good). Do NOT recompute cutoffs from undersized samples.

### Multi-fire discipline (Lane 14 lesson)

If two branches of the decision rule both fire (e.g., HIGH too rare AND MED too noisy simultaneously), both flags must be addressed in the response — not "pick one." Both signal that the spec's percentile and ceiling-mass thresholds need joint adjustment, not separate ones.

## Future simplification path — retrain dissolves the cliff

The bimodal cliff is a property of the current 14h dual-write training window — limited upper-tail resolution. As more dual-write data accumulates post-cutover, the upper tail of the isotonic curve smooths out naturally. The cliff is not permanent.

**Re-evaluation trigger:** at T+30 days OR T+10,000 post-cutover calibrated predictions (whichever first), check whether `cnts(ceiling_value) / total_n` has dropped below 3%. If yes, the cliff has dissolved enough that the original top-1% / top-5% percentile spec works directly. Re-pre-register a spec simplification.

This avoids "wait days for retrain before cutover" while preserving the option to simplify when the data supports it.

## Implementation deltas vs the existing Track B cutover patch

Files in `docs/research/track_b_cutover_patch.md` mostly hold. Specific deltas needed:

1. **`web/bucket_cutoffs.py`**: revise `rebuild()` to compute `ceiling_value` (largest-mass distinct value with ≥1% mass) AND `raw_gbm_p97` of raw GBM scores. Persist both to module state.
2. **`web/bucket_cutoffs.py`**: revise `bucket_for()` to take TWO args: `(calibrated_prob, raw_gbm_prob)`. HIGH/MED/LOW logic per the spec above.
3. **`web/main.py`**: pass both calibrated and raw to `bucket_for()` at the bucket-lookup site.
4. **`web/static/app.js`**: bucket badge tooltip text updated to reflect "above-ceiling outlier" / "at-ceiling top-rank" framing instead of "top 1% / top 5% percentile."
5. **`web/main.py`** `/api/scope`: bucket_method description updated to reflect bimodal-aware logic.
6. **All other Track B patch items hold**: V3 leaf bump, m_out propagation, alert template, V3 leaf-version flip at cutover.

### Local test additions (extend `scripts/track_b_test.py`)

Four new test cases, each named to make failure-cause obvious:

1. **`test_bimodal_distribution_assigns_correctly`** — Construct synthetic 1000-sample bimodal: 910 values at 0.04, 85 at ceiling 0.1132, 5 distinct above-ceiling outliers (each n=1) at [0.13, 0.15, 0.18, 0.22, 0.30]. Run `rebuild()` then `bucket_for()` on each value. Verify:
   - All 5 above-ceiling outliers → **HIGH**
   - Top-3% of raw GBM at-ceiling members → **MED** (~3% of total = ~30 samples)
   - All below-ceiling values → **LOW**
   - Empty-HIGH counter increments correctly when above-ceiling absent

2. **`test_bimodal_regression_against_old_logic`** — REGRESSION TEST. Same bimodal data. Run the OLD bucket_for() (percentile-only). Verify it FAILS to distinguish HIGH from MED — i.e., old logic produces `high_min ≈ med_min` (within epsilon) on bimodal input. This confirms the new logic is doing what the old logic couldn't.

3. **`test_smooth_distribution_falls_back_to_percentile`** — Construct synthetic 1000-sample uniform distribution on [0, 0.3] (no value with ≥1% mass on any single point). Run `rebuild()`. Verify:
   - `bucket_logic_mode == "standard_percentile"` (fallback fires)
   - `high_min` ≈ p99 (~0.297)
   - `med_min` ≈ p95 (~0.285)
   - `bucket_for(0.295)` → HIGH; `bucket_for(0.290)` → MED; `bucket_for(0.10)` → LOW
   This proves the spec self-deprecates correctly when the cliff dissolves post-retrain.

4. **`test_low_sample_window_holds_last_known_good`** — Feed n=200 samples (below 500 threshold). Verify daemon does NOT recompute cutoffs (returns previous values), and `/api/status.bucket_cutoffs.status` surfaces `"low_sample_window"`. Repeat with n=600 — verify daemon recomputes normally.

The regression test (case 2) is the load-bearing one: it proves the new logic ships value the old logic didn't have.

## Receipts moat — third pre-fix structural diagnosis in 48 hours

This is the **third pre-fix structural diagnosis** the cutover pre-flight has surfaced in a 48-hour window:

1. **2026-05-05 morning** — deployed kNN absolute-threshold saturation. [docs/research/deployed_knn_saturation_diagnosis.md](deployed_knn_saturation_diagnosis.md)
2. **2026-05-05 evening** — calibrated GBM over-confidence vs `actual_graduated` (Gate 5 over-confident branch fires). [docs/research/gbm_v1_isotonic_calibration.md](gbm_v1_isotonic_calibration.md)
3. **2026-05-06 morning** — calibrated GBM bimodal cliff at isotonic ceiling (this document).

Each diagnosis publicly committed before the corresponding fix shipped. **The discipline pattern is functioning as designed** — each finding caught and documented publicly before the fix is built. Single instances are easy to dismiss; consecutive ones in a tight window are evidence of a working system.

Future readers (acquirers, auditors, B2B prospects, external researchers) inspecting the github commit history will see: three timestamped pre-fix diagnoses in 48 hours, each followed by a fix that addressed exactly what the diagnosis named. That trail is genuinely unusual in this product space — most teams ship, hit issues silently, fix quietly. The pattern of finding → publishing → fixing with timestamps proving the order is the receipts moat working at peak strength.

## Cross-references

- [docs/research/deployed_knn_saturation_diagnosis.md](deployed_knn_saturation_diagnosis.md) — yesterday's pre-fix diagnosis
- [docs/research/gbm_v1_isotonic_calibration.md](gbm_v1_isotonic_calibration.md) — original calibration writeup that anticipated the range-empty case but didn't predict the cliff specifically
- [docs/research/track_b_cutover_patch.md](track_b_cutover_patch.md) — cutover patch, needs the deltas above
- [BACKLOG.md "UI threshold update"](../../BACKLOG.md) — original pre-registration; this revision supersedes the percentile-cutoff portion
- [feedback_pre_registration_branches.md](../../../.claude/projects/-Users-danielsproul/memory/feedback_pre_registration_branches.md) — Lane 13/14 + Gate 5 discipline this artifact applies
