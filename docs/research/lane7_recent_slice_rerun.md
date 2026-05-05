# Lane 7 recent-slice rerun — verify current calibration vs the shipped caveat

**Run date:** 2026-05-05 evening (after Lane 7 audit)
**Pre-registration:** [BACKLOG.md → "Lane 7 recent-slice rerun"](../../BACKLOG.md)
**Decision criteria (frozen pre-run):**
- Within ±5pp on majority of bins ≥0.5 → mechanism (c) confirmed, /api/scope caveat too pessimistic, update tomorrow
- Still mis-scaled by ≥8pp on majority → mechanism (c) wrong, fourth mechanism we haven't identified
- Mixed → tier-specific fix

---

## Headline (decision applied fresh, post-writeup)

**Mechanism (c) REJECTED per pre-registered rule.** 2 of 4 tiers still mis-scaled by ≥8pp on recent (last 24h, n=1,500) data. The other 2 had insufficient bin coverage to evaluate (only one bin ≥0.5 with n≥10).

**But the direction has FLIPPED.** Lane 7's full sample showed OVERCONFIDENCE (predicted > actual). Recent slice shows UNDERCONFIDENCE (predicted < actual) on the well-populated bins.

**The /api/scope caveat shipped earlier today STAYS LIVE.** Current data does not support the "current calibration is fine, mis-scaling is historical" framing. The audit's mechanism (c) was wrong.

## Numbers

### runner_prob_2x_from_now (recent, n=1,500)

| Bin | n | Predicted | Actual | Delta |
|---|---:|---:|---:|---:|
| [0.5, 0.6) | 36 | 0.543 | 0.611 | **+6.8pp** |
| [0.6, 0.7) | 24 | 0.635 | 0.750 | **+11.5pp** |
| [0.7, 0.8) | **109** | 0.775 | 0.872 | **+9.7pp** |
| [0.8, 0.9) | **95** | 0.836 | 0.958 | **+12.2pp** |
| [0.9, 1.0) | 13 | 0.937 | 0.923 | -1.4pp |

Avg |delta| on bins≥0.5: **8.3pp** (just over the ≥8pp "still mis-scaled" threshold).

The two largest bins ([0.7, 0.8) n=109 and [0.8, 0.9) n=95) are consistently **+10pp UNDER**. That's a clean signal, not noise — both bins large enough to have tight confidence intervals, both pointing the same direction.

### runner_prob_3x_from_now (recent, smaller bins)

| Bin | n | Predicted | Actual | Delta |
|---|---:|---:|---:|---:|
| [0.5, 0.6) | 22 | 0.555 | 0.864 | **+30.9pp** |
| [0.6, 0.7) | 6 | 0.643 | 0.833 | +19.0pp |
| [0.7, 0.8) | 3 | 0.729 | 0.667 | -6.2pp |
| [0.8, 0.9) | 4 | 0.834 | 0.500 | -33.4pp |

Most bins ≥0.5 here have n<15 — confidence intervals are wide. The +30.9pp at [0.5, 0.6) (n=22) is the only well-populated reading and is loudly UNDER.

### runner_prob_5x_from_now and runner_prob_10x_from_now

Insufficient data on bins ≥0.5 in the last 24h — only 1-2 high-confidence predictions resolved, can't compute calibration cleanly. The model is producing mostly low-prob outputs at these tiers in recent traffic.

## The direction flip

Lane 7's 89k full sample showed:
- 2x_from_now [0.7, 0.8): predicted 0.748, actual 0.582, **-16.6pp** (OVERCONFIDENT)
- 2x_from_now [0.8, 0.9): predicted 0.868, actual 0.787, **-8.0pp** (OVERCONFIDENT)

Recent 24h slice shows:
- 2x_from_now [0.7, 0.8): predicted 0.775, actual 0.872, **+9.7pp** (UNDERCONFIDENT)
- 2x_from_now [0.8, 0.9): predicted 0.836, actual 0.958, **+12.2pp** (UNDERCONFIDENT)

Same bins, same tier, opposite direction. Magnitude is similar (~10pp). This is a **calibration overshoot** pattern — the curve has corrected past the empirical rate and is now mis-scaling in the other direction.

## Three plausible mechanisms (none yet pre-registered)

1. **Sample-period bias.** Last 24h had unusually-pumping mints. Pump.fun activity varies day-to-day; if today's market saw more graduation runners than the corpus average, recent predictions appear underconfident in retrospect. Self-corrects as more samples accumulate.
2. **Curve overshoot.** As recent high-actual-rate samples enter the calibration anchor set, the curve adjusts to map RAW values DOWN. New predictions then get calibrated DOWN, but the underlying outcome distribution still shows high hit rates → underconfident calibration. This is a real pathology of fast-evolving calibration when the outcome distribution is non-stationary.
3. **Fourth mechanism we haven't identified.** Either a bug in how stored predicted_prob is being computed, or a resolution-path difference (recent predictions resolve via slightly different code), or a real shift in mint behavior over time.

## What this means for the /api/scope caveat

**Keep it live.** "directional, magnitude recalibration pending" is now MORE supported, not less:
- Lane 7 found ~12pp OVERCONFIDENT on the historical aggregate
- This rerun finds ~10pp UNDERCONFIDENT on recent data
- BOTH are mis-scaled, just in different directions
- Calling current calibration "fine" would be wrong

**But the framing should sharpen.** The caveat as shipped implies a static "the model is overconfident by 12pp." Reality is more nuanced: **the calibration is unstable**. It overshoots historically, undershoots recently. The right user-facing framing is: "magnitude calibration is non-stationary; treat the field as a ranking signal, not as a literal probability." That's a small revision tomorrow, not a removal.

## What this means for tomorrow's recalibration workstream

The audit's 30-min "verify + 1-line defensive cleanup" plan is wrong. Real investigation needed:

1. **Investigate the direction flip.** Run calibration analysis on rolling 7-day windows over the last 30 days. Plot avg delta by tier over time. If the direction oscillates, the curve is overshooting. If it's monotonically improving, recent under-confidence is the corrected state and historical over-confidence will fade.
2. **Check if the curve is being rebuilt too frequently for the data velocity.** 15-min rebuild may be too aggressive when daily resolved-prediction count is small. Slower rebuild (1-2h) lets each anchor stabilize before being incorporated.
3. **Consider the saturation hypothesis from the other side.** The dead-code "scale toward (1,1)" branch never wakes up given current xN=1.05 anchor. But maybe the OPPOSITE is happening — the curve's "below first anchor: scale toward (0,0)" branch is mishandling near-zero raws. Worth tracing.
4. **Don't ship a fix tonight.** The system is mis-scaled, but in a different direction than the caveat implies. Updating the caveat to match what we now know is honest. Shipping a code fix without understanding the direction-flip mechanism risks making things worse.

## Caveats

- n=1,500 in last 24h is solid but smaller than Lane 7's 89k. Some bins have n<15 — wider CIs.
- The 24h window may capture a single market cycle (US trading day, EU evening, Asia open). A 7-day rolling rerun would average across cycles.
- The recent direction flip could partially reflect resolution-time bias: predictions made in last 24h may not all have FULLY resolved (max_mult could still update). The CSV was filtered to actual_max_mult IS NOT NULL, but late-resolving mints could shift the actual rate up (more time → higher peak observed).
- Lane 7's full-sample analysis is dominated by older predictions (long tail). Recent slice is dominated by today's predictions. The "direction flip" could partly be a sampling artifact of the full-sample's heavy historical weighting vs the recent slice's narrower window.

## Discipline note

The user asked: run the recent-slice rerun tonight to avoid letting an overstated current-problem caveat live overnight. The verification was supposed to give us either "caveat is too pessimistic, relax it" or "caveat is right, leave it." It gave us "caveat is right, but the underlying mechanism is different than we thought."

That's the third valid outcome of any verification: the framing is right but the mechanism inside it isn't. Pre-registration didn't enumerate this case explicitly, but the implication is clear — keep the caveat, but tomorrow's investigation has to be deeper than "1-line defensive cleanup." The audit was good and the verification was good; together they point at a fourth mechanism that needs its own investigation before any fix ships.

## Numerical artifacts

- `/tmp/lane2/lane7_recent.py` — analysis script
- `/tmp/lane2/lane7_recent.json` — full results dump
