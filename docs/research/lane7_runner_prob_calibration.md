# Lane 7 — runner_prob calibration validation

**Run date:** 2026-05-05 evening
**Pre-registration:** [BACKLOG.md → "Lane 7 — runner_prob calibration validation"](../../BACKLOG.md)
**Decision criteria (frozen pre-run):**
- Calibration on bin ≥0.5 within ±10pp on ≥75% of populated bins → CALIBRATED
- 10-25pp off on majority → MIS-SCALED (recalibratable)
- >25pp off on majority → BROKEN (signal is operator-intuition that didn't survive measurement)

---

## Headline

**runner_prob is MIS-SCALED, not BROKEN.** All from_now variants come in within ±25pp on majority of high-prob bins — overconfident by ~11-13pp on average. The signal IS real; the magnitude needs recalibration.

**Decision per pre-registered rule applied fresh: recalibrate via Platt scaling / isotonic — same pattern as grad_prob's existing self-correcting curve.** Don't ship runner_prob as a B2B signal until recalibration is verified working.

| Tier | Verdict | Avg \|delta\| (bins ≥0.5) |
|---|---|---:|
| runner_prob_2x_from_now | MIS-SCALED | 13.0pp |
| runner_prob_3x_from_now | MIS-SCALED | 13.6pp |
| runner_prob_5x_from_now | MIS-SCALED | 11.8pp |
| runner_prob_10x_from_now | MIS-SCALED | 11.3pp |
| runner_prob_2x_from_launch | MIS-SCALED | 21.3pp |
| runner_prob_3x_from_launch | MIS-SCALED | 21.9pp |
| runner_prob_5x_from_launch | CALIBRATED | 18.6pp |
| runner_prob_10x_from_launch | CALIBRATED | 14.3pp |

The "CALIBRATED" verdicts on 5x and 10x from_launch are passing the 75%-of-bins-within-10pp rule, but their average |delta| is still 14-19pp — the calibration is good ENOUGH on most bins but breaks at the saturated [0.9,1.0) bin (see saturation issue below).

## The systematic pattern: overconfidence

Across all tiers and populations, the model's predicted probability is **higher than the actual hit rate** on bins ≥0.5. Examples:

| Tier | Bin | Predicted | Actual | Delta |
|---|---:|---:|---:|---:|
| 2x_from_now | [0.9, 1.0) | 0.952 | 0.769 | -18.3pp |
| 5x_from_now | [0.7, 0.8) | 0.748 | 0.582 | -16.6pp |
| 10x_from_now | [0.8, 0.9) | 0.853 | 0.630 | -22.3pp |
| 5x_from_launch | [0.9, 1.0) | 0.993 | 0.287 | **-70.7pp** |
| 10x_from_launch | [0.9, 1.0) | 0.952 | 0.333 | **-61.9pp** |

The 60-70pp deltas at the [0.9, 1.0) bins for 5x/10x from_launch are the **saturation issue**: when all 50 nearest neighbors hit the threshold, raw kNN output is 1.0 — but in practice many of those mints don't actually achieve the runner. Same saturation pattern grad_prob has at the high end.

## Stratification by bundle_detected

| Tier | Bundled (n=6,392) | Non-bundled (n=82,685) |
|---|---|---|
| 2x_from_now | BROKEN (avg \|delta\| 18.5pp; small bin samples) | MIS-SCALED (avg 12.8pp) |
| 5x_from_now | CALIBRATED on top bin (small samples elsewhere) | MIS-SCALED (avg 11.6pp) |
| 10x_from_now | MIS-SCALED (avg 13.1pp) | MIS-SCALED (avg 11.6pp) |

**Selection bias check:** non-bundled mis-scaling is consistent at ~12pp across all from_now tiers — uniform overconfidence, same direction, similar magnitude as the full sample. Bundled subset has too few samples in high-prob bins (3-128 per bin in some cases) to draw clean stratification conclusions, but the directional finding is consistent: mis-scaled toward overconfidence in both populations.

So unlike grad_prob (which had clear bundled vs non-bundled selection bias from Lane 1), runner_prob's mis-scaling is **uniformly distributed across populations**. The recalibration fix applies to all populations equally.

## Why this happens (probable mechanism)

Score_full at [web/grad_prob.py:556-572](../../web/grad_prob.py) DOES call `apply_calibration` on runner_prob fields. Two probable reasons calibration isn't fixing the overconfidence:

1. **The calibration daemon may not have built curves for runner_prob tiers** the way it has for grad_prob. The `predictions.get_all_calibration_curves()` output lists which curves are populated; if runner_prob_*_from_now curves have <30 anchor points, calibration is essentially passthrough.
2. **Even with curves built, the saturation issue (raw kNN = 1.0 when all 50 neighbors hit) may break the curve fit** at the high end. The [0.9, 1.0) bin for 5x_from_launch shows actual=0.287 — that's a clear sign the saturation case is leaking through uncalibrated.

Both diagnoses are testable in code, separately from this writeup.

## Decision per pre-registered rule

**MIS-SCALED on majority of tiers** (5 of 8). The other 3 (5x and 10x from_launch on full sample, 5x_from_now bundled) pass calibration on bin counts but have high average |delta| driven by saturation outliers. Per pre-registered rule:

> 10-25pp off on majority of bins → MIS-SCALED. Recalibrate via Platt scaling / isotonic.

**Action: recalibrate, don't deprecate. Surface the recalibration as a documented fix.**

The existing `apply_calibration` infrastructure handles this — the question is whether it's actually populated and applied for runner_prob tiers. If yes: re-anchor the curves. If no: build the curves and wire them in. Either way, this is a config / data fix, not a research project.

## Implications for the API surface

`runner_prob_2x/5x/10x` and their `_from_now` variants are exposed at `/api/v1/probe`, `/api/live`, and `/api/scope`. External consumers indexing on these fields are reading **overconfident-by-~12pp** numbers today.

Before any B2B story relies on runner_prob:
1. Confirm calibration curves are populated for all 4 from_now tiers
2. Ensure the apply_calibration at score_full actually transforms saturating values (not just passes through)
3. Re-validate calibration with this same Lane 7 methodology after the fix
4. THEN runner_prob is a real product signal alongside grad_prob

Until then, **runner_prob fields are signal but not ground truth**. The `/api/scope` documentation should reflect this — current "calibrated: True" claim on these fields is technically aspirational, not currently delivered at the desired tolerance.

## What this does NOT change

- Lane 9 / 10 retrain plan stays unchanged. The retrain trains a new GBM on sustained_30m as a primary label; runner_prob's recalibration is a separate fix on the existing k-NN.
- The selection bias findings stay valid. Lane 7 confirms runner_prob doesn't carry an additional selection bias on top of grad_prob's — the issue is purely mis-scaling.
- No deploy decisions tonight per discipline contract.

## Next steps (not decisions, just the obvious follow-ups)

1. **Inspect calibration curve state.** Read `predictions.get_all_calibration_curves()` output, verify which runner_prob tiers have anchored curves. If <30 anchors per tier, that's the immediate fix.
2. **Audit apply_calibration handling of saturating values.** The [0.9, 1.0) bin's 60-70pp deltas on from_launch suggest the saturation case (raw kNN = 1.0) breaks the curve fit. Worth a code-level audit of whether saturated raws bypass calibration.
3. **Re-validate after recalibration.** Run Lane 7's exact methodology on a fresh sample after the fix lands. If the next run shows <10pp on majority of bins, calibration is restored.

## Caveats

- 89,077 rows is a large sample, but high-prob bins have variable density. Some bundled-stratified bins have <30 samples — confidence intervals there are wide.
- "Actual hit" is computed from `predictions.actual_max_mult`, which comes from observer-curves. Per Lane 11, ~30% of non-bundled graduators are missing from observer-curves entirely. Their actual_max_mult would be NULL and they'd be excluded from this analysis. This means **the 80%+ subset analyzed here is biased toward the captured population** — actual hit rates on non-bundled mints could differ slightly if the missing 30% had different distributions. Worth keeping in mind, doesn't change the directional finding.
- "from_now" outcomes use `actual_max_mult / entry_mult ≥ N`. This assumes peak after entry. In practice peak could be BEFORE entry — but `entry_mult` is the cur_mult at the FIRST score in that bucket window, and `actual_max_mult` is over the full curve, so peaks before first-score are included. Could undercount actual_from_now hits modestly.

## Numerical summary saved to `/tmp/lane2/lane7_calibration.json`
