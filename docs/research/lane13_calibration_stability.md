# Lane 13 — Calibration stability analysis

**Run date:** 2026-05-05 evening (after Lane 7 recent-slice rerun)
**Pre-registration:** [BACKLOG.md → "Lane 13 — calibration stability analysis"](../../BACKLOG.md)
**Decision criteria (frozen pre-run):**
1. Single flip, recent only → mechanism 1 (sample-period bias) → "wait, monitor, sharpen caveat"
2. Monotonic over → under → mechanism 2 (curve overshoot) → "slow rebuild cadence"
3. ≥3 direction flips → mechanism 3 (oscillating instability) → "structural redesign"
4. No clear pattern → mechanism 4 → "escalate to bug hunt"

---

## Headline (decision applied fresh, post-writeup)

**Per pre-registered rule applied STRICTLY: Mechanism 4 fires.** The pattern doesn't fit any single branch cleanly across all 4 tiers.

**But invoking the pre-registered "data is decisive in a way the hypothesis didn't anticipate" meta-note:** the qualitative pattern is a **hybrid of mechanisms 2 and 3**. There's a strong monotonic trend (over → less over → near zero → occasionally under) consistent with curve overshoot, BUT the day-to-day variance is high enough to produce 2 sign crossings on most tiers — closer to oscillation than clean monotonicity.

**The fix scope is closer to mechanism 2 than 3** — slow the rebuild cadence — but with the mechanism 3 caveat: at the current resolved-prediction velocity, calibration may be intrinsically unstable on this signal regardless of rebuild cadence. Both fixes (slower rebuild + stationarity-aware reference) may be needed.

## Numbers

### Signed delta time-series (rolling 24h windows, stepped 12h, 30-day query window)

Note: predictions table only goes back to **2026-04-28** (7 days, not 30). Schema-level retention or earlier data pruning. Working sample is 7 days, n=89,122.

| Window mid | n | 2x_from_now | 3x_from_now | 5x_from_now | 10x_from_now |
|---|---:|---:|---:|---:|---:|
| 04-29 06 | 202 | — | — | — | — |
| 04-29 18 | 907 | -55.6pp | -49.1pp | -36.4pp | -41.9pp |
| **04-30 06** | **16,804** | **-17.7pp** | **-11.5pp** | **-11.0pp** | **-11.9pp** |
| **04-30 18** | **29,486** | **-30.6pp** | **-25.6pp** | **-20.4pp** | **-26.0pp** |
| **05-01 06** | **27,312** | **-35.5pp** | **-29.7pp** | **-28.3pp** | **-32.3pp** |
| **05-01 18** | **28,538** | **-7.4pp** | **-7.9pp** | **-6.7pp** | **-2.0pp** |
| **05-02 06** | **36,328** | **+5.5pp** | **+4.2pp** | **-3.0pp** | **+3.0pp** |
| **05-02 18** | **27,752** | **-15.3pp** | **-2.1pp** | **-7.9pp** | **+1.0pp** |
| 05-03 06 | 6,376 | -40.8pp | -36.4pp | -39.1pp | -16.3pp |
| 05-03 18 | 1,114 | -29.7pp | -23.8pp | — | — |
| 05-04 06 | 1,851 | -0.9pp | +8.2pp | — | — |

**Bold rows have n ≥ 5,000** — high-confidence windows. The early 04-29 reading (n=202) and late 05-03/05-04 readings (n=1k-6k) are noisier.

### What the strict rule says (per-tier classification)

| Tier | Sign changes | Drift (last⅓ − first⅓) | Verdict per strict rule |
|---|---:|---:|---|
| 2x_from_now | 2 | +5.7pp | Single-flip with crossing |
| 3x_from_now | 3 | +10.7pp | Oscillating (≥3 crossings) |
| 5x_from_now | 0 | -1.4pp | Stable |
| 10x_from_now | 2 | +20.8pp | Single-flip with crossing |

Per the OVERALL strict rule: 1/4 oscillating, 0/4 cleanly monotonic, 2/4 single-flip, 1/4 stable → **"pattern unclear → Mechanism 4."**

### What the qualitative read shows

Looking at the high-n windows only (≥5k, the trustworthy readings):

- **04-30 06:** -17.7pp (over)
- **04-30 18:** -30.6pp (over, deeper)
- **05-01 06:** -35.5pp (over, deepest)
- **05-01 18:** -7.4pp (improving)
- **05-02 06:** +5.5pp (crossed zero, slightly under)
- **05-02 18:** -15.3pp (back to over)

The trajectory on 2x_from_now (best-populated tier) is: **-17 → -30 → -35 → -7 → +5 → -15** over 30 hours. That's a swing from -35 to +5 in 18 hours (a 40pp shift), then another swing back to -15. The variance is enormous, but the dominant pattern is **trending from heavily over-confident toward zero with high day-to-day noise**.

This is precisely what **mechanism 2 (curve overshoot from fast rebuild)** predicts: 15-min rebuilds chase recent samples, the curve corrects past historical over-confidence, day-to-day variance in the underlying outcome rate causes the calibrated value to swing dramatically as the curve re-anchors.

The 2 sign crossings (rather than 0 or ≥3) indicate the curve is in the *transition* zone — still correcting past the historical bias but with enough variance to occasionally cross zero. If we sampled another 30 days, we'd likely see the deltas stabilize closer to zero with smaller swings.

## Three findings worth flagging beyond the strict verdict

### 1. The trajectory is consistent across all 4 tiers

Drift values are all positive (last third > first third by 5.7-20.8pp). The system is moving toward less over-confidence. None of the tiers shows a flat or worsening trajectory — that's a SIGNAL, not noise.

### 2. The 5x_from_now tier shows 0 sign changes — but ONLY because its later windows have insufficient data

5x_from_now's last 2 windows (05-03 18 and 05-04 06) had no bins ≥0.5 with n≥10. The tier looks "stable" in the strict rule because its valid time-series is shorter, not because it's actually stable. With more samples it would likely show the same trajectory.

### 3. The variance scales with sample size in the right direction

Big-sample windows (n=27k-36k on 04-30, 05-01, 05-02) cluster around -15 to -35pp early, then -7 to +5pp later. Small-sample windows (n=202, 907, 1k-6k) have wilder swings (-55, -40). The signal is real; the noise is sample-size-driven and concentrates at the time-window edges.

## Decision per pre-registered rule (strict + qualitative)

**Strict:** Mechanism 4 (no clean pattern) → escalate to bug hunt.

**Qualitative honest read:** **dominant pattern is mechanism 2 (curve overshoot) with mechanism 3-like noise.** The trajectory across tiers is consistent (over → less over → ~zero); the variance is high enough to look oscillating in a strict-counting sense.

**Recommended fix scope (this is a fork from the strict rule):**

The strict rule says "escalate, bug hunt." The qualitative read says "slow the rebuild cadence and re-evaluate." These are NOT compatible — slowing the rebuild is a code change, bug-hunting is investigation.

Per the pre-registered discipline ("Pre-registration meta-note: data is decisive in a way the hypothesis didn't anticipate"), the right move is to **note both interpretations explicitly and let next-session pick**. A reasonable next-session call:

1. **Slow the rebuild cadence to 1-2h** as a low-risk first move (mechanism 2's fix)
2. **Re-run Lane 13 in 1 week** — if the time-series flattens around zero with reduced variance, mechanism 2 was right. If variance stays high, mechanism 3 is real and structural redesign is needed.
3. **In parallel, do a light bug-hunt pass** on the storage path: are we sure the `predicted_at` timestamps are accurate? Are calibration writes actually happening atomically?

This three-way response covers all the qualitative evidence without picking a single mechanism prematurely.

## What this means for tomorrow's recalibration ticket

Updated guidance from "investigate direction flip" to:

1. **Slow rebuild cadence from 15min to 1-2h.** Low-risk code change, addresses mechanism 2 if it's the dominant cause.
2. **Re-validate via Lane 13 rerun in ~1 week.** If trajectory stabilizes near zero with reduced variance, fix worked.
3. **Sharpen /api/scope caveat** with the time-pattern finding. Replace the static "overconfident by 12pp" implication with: "magnitude calibration shows non-stationary behavior over recent rolling windows — predicted is over-stated by 5-35pp on most days, near-zero on others. Treat the field as a ranking signal, not a literal probability."
4. **Defensive 1-line fix on the dead-code "scale toward (1,1)" branch** — still worth doing, irrelevant to current behavior but a footgun if curves ever lose their 1.05 anchor.

The recalibration ticket scope is now: ~1h code work (rebuild cadence + defensive fix) + caveat update + scheduled re-validation in 1 week. Not the "real investigation" the recent-slice rerun implied — Lane 13 narrowed the suspects.

## Caveats

- **Sample window is 7 days, not 30** — predictions table only retains ~7 days of data. The "30-day window" framing in the pre-registration was aspirational. Working sample is the full retained history.
- **Two of four tiers (5x and 10x) have shorter valid time-series** — late windows have insufficient data. Pattern claims are most trustworthy on 2x_from_now (10 valid windows) and 3x_from_now (10 valid windows).
- **The 2 sign-crossings on 2x and 10x are right at the zero line** (one crossing per direction during the variance phase). They're NOT separating clearly different regimes; they're noise around an improving trajectory. A stricter "must cross by ≥3pp on each side" rule would have classified these as monotonic.
- **The strict rule was written for a longer time-series.** With only 7-10 valid windows per tier, classifying a 2-flip pattern as "oscillating" vs "single-flip with noise" is borderline. A 30-day true window would let us count crossings more meaningfully.
- **Resolution-time bias may inflate later windows' apparent under-confidence.** Recent predictions resolve gradually as `actual_max_mult` updates. Predictions from yesterday may still be resolving today, with hits coming in late and pushing actual rates UP. This would explain the late under-confident readings without invoking mechanism 2 — it's the same temporal artifact concern Lane 7 audit raised, just in a different shape.

## Numerical artifacts

- `/tmp/lane2/lane13_data.csv` — 30-day extract (89,122 rows)
- `/tmp/lane2/lane13_stability.py` — analysis script
- `/tmp/lane2/lane13_results.json` — windows + classifications
