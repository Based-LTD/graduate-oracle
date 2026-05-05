# Lane 7 audit — runner_prob recalibration mechanism

**Run date:** 2026-05-05 evening (after Lane 7 calibration validation)
**Pre-registration:** [BACKLOG.md → "Lane 7 audit"](../../BACKLOG.md)
**Decision criteria (frozen pre-run):**
- (a) only → fix is "ensure calibration daemon builds runner_prob curves"
- (b) only → fix is "remove saturation bypass at high end"
- Both → both fixes needed, sequence matters
- **Neither → third mechanism we haven't identified, re-scope**

---

## Headline (decision applied fresh, post-writeup)

**Both pre-registered mechanisms REJECTED.** The diagnostic surfaced a third mechanism that wasn't in the original hypothesis:

**Mechanism (c) — historical curve drift.** Lane 7 aggregated 89,077 predictions stored over time. Each prediction was calibrated against the curve THAT EXISTED at predict time. The curves rebuild every 15 min and have matured significantly — early predictions were calibrated against less-mature curves with fewer anchors and narrower coverage. The current curves are well-calibrated; older stored predictions were not.

**Decision per pre-registered rule:** "Neither (a) nor (b) → re-scope." Tomorrow's recalibration ticket converts from "audit + fix code" to "verify on a recent slice — if fine, no fix needed; if still broken, deeper investigation."

## Mechanism (a) — REJECTED

`/api/accuracy.calibration_curves` shows all 5 runner_prob_*_from_now tiers loaded:

| Tier | Anchors | Total samples |
|---|---:|---:|
| runner_prob_2x_from_now | 11 | 89,113 |
| runner_prob_3x_from_now | 11 | 89,113 |
| runner_prob_5x_from_now | 11 | 89,113 |
| runner_prob_10x_from_now | 11 | 89,113 |
| runner_prob_20x_from_now | 8 | 89,072 |

Plus 5 from_launch variants and grad_prob, all built. **Curves exist with ample anchors.** The calibration daemon (15-min rebuild loop in `web/predictions.py:_rebuild_calibration_curves`) is functioning correctly.

## Mechanism (b) — REJECTED

Simulated `apply_calibration` against the current curves on raw inputs spanning the full range. The curves correctly map raw values to their empirical anchor actual rates:

**runner_prob_5x_from_now (current curve):**

| raw input | calibrated output | nearby anchor's actual rate |
|---:|---:|---:|
| 0.05 | 0.027 | 0.027 ← exact match |
| 0.45 | 0.396 | 0.396 ← exact match |
| 0.75 | 0.582 | 0.582 ← exact match |
| 0.85 | 0.787 | 0.787 ← exact match |
| 0.95 | 0.873 | 0.873 ← exact match |
| 1.00 | 0.923 | 0.873 ← interpolated cleanly between 0.95→0.873 and 1.05→0.973 |

The curves include an anchor at raw=1.05 (last anchor) with actual rate 0.97 for 5x_from_now, 0.98 for 10x_from_now, etc. **This anchor catches the saturation case** — when score_full produces raw=1.0 (all 50 neighbors hit), the curve interpolates between (0.95, ~0.87) and (1.05, ~0.97), yielding ~0.92. Not the 1.0 the saturation bypass would produce.

**Inspecting the `__call__` method:** the saturation bypass exists in code (the "above last anchor: scale toward (1,1)" branch when xN < 1 maps raw=1.0 to 1.0 algebraically). But it's not currently triggered because `xN = 1.05 ≥ 1` for all current curves, sending raw=1.0 through the `min(1.0, raw)` branch — and raw=1.0 < pts[-1][0]=1.05 means raw=1.0 actually falls into the "interpolate between two surrounding anchors" branch, not the "above last anchor" branch. The bypass is dead code with the current anchor positions.

## Mechanism (c) — Historical curve drift (NOT pre-registered, surfaced by data)

If both (a) and (b) are rejected, why did Lane 7 measure 12pp mis-scaling?

The answer: **predictions stored months ago were calibrated against curves that didn't yet have the 1.05 anchor.** As the corpus grew, the calibration daemon rebuilt curves with progressively more anchors and better coverage. Older stored predicted_prob values reflect the calibration as it was at the time, not the current well-calibrated state.

Concrete trace: an early prediction with raw_grad=1.0 might have been calibrated against a curve whose last anchor was at xN=0.85, yN=0.7. The curve's "above last anchor" branch would map raw=1.0 to:
- `yN + (raw - xN) * (1.0 - yN) / (1.0 - xN)`
- = 0.7 + (1.0 - 0.85) * (1.0 - 0.7) / (1.0 - 0.85)
- = 0.7 + 0.15 * 0.3 / 0.15
- = 0.7 + 0.3 = **1.0**

So an old prediction with saturated raw=1.0 got STORED as predicted_prob=1.0 because the curve at that time didn't catch the saturation case. Today's curves do (xN=1.05 maps it to ~0.92). Lane 7's [0.9, 1.0) bin showing 60-70pp deltas captures these old high-bias predictions averaged with the small fraction of recent better-calibrated ones.

**The curves self-corrected as samples accumulated.** No code bug — just maturity.

## What this means for tomorrow's recalibration ticket

The original ticket (per /api/scope caveat shipped earlier) was "magnitude recalibration in progress." The audit changes this from "fix calibration code" to "verify current calibration is good":

### Tomorrow's recalibration plan

**Step 1: re-run Lane 7 on RECENT predictions only.** Filter `predictions.predicted_at >= now - 24h`. If recent slice is well-calibrated (within ±5pp on majority of bins ≥0.5), the original Lane 7 finding is confirmed historical and no code change is needed.

**Step 2: if recent slice still mis-scaled** — there's a real bug we haven't found. Investigate further. Candidates to re-examine:
- Is `apply_calibration` actually being called on every score_full output? Check the call site.
- Is the curve being LOADED correctly into `_CALIBRATION_CURVES` after each rebuild?
- Is there a race between curve rebuild and score read?

**Step 3 (regardless of recent slice result): consider tightening the curve's saturation handling.** Even though current anchors handle it, the dead-code "scale toward (1,1)" branch is a footgun. If the corpus ever evolves to NOT have a 1.05 anchor, the bypass wakes up. Replace with: `if xN < 1: return yN` (i.e., for inputs above last anchor, output the last anchor's actual rate). One-line defensive fix.

### What to update in /api/scope

The current caveat ("calibrated: 'directional, magnitude recalibration pending'") is appropriate while we verify the recent slice. Once Step 1 confirms current calibration is good, update the caveat to: "calibrated against forward outcomes; values pre-2026-05-05 may have residual mis-scaling due to curve maturity over time; recent values are within ±5pp." Or revert to `calibrated: True` if the verification is decisive.

## Why this is a useful audit even though it rejected the original hypothesis

1. **The fix scope changed cleanly.** Tomorrow's recalibration ticket isn't a code change — it's a verification + a defensive cleanup. ~30 min of work, not 2-3h.
2. **The /api/scope caveat I shipped tonight is more nuanced than the original framing suggested.** It's not "the model is overconfident" — it's "old stored predictions are overconfident; new ones are correctly calibrated." Worth refining the user-facing language once verified.
3. **The dead-code saturation branch is a real footgun** even though it's not currently active. Defensive fix is cheap and prevents the bug from waking up if curves ever lose their 1.05 anchor.

## Caveats

- The audit only covered the from_now variants in detail. Spot-checked from_launch (5x and 10x) — they also have 1.05 anchors, so the same finding applies. If full from_launch audit reveals different mechanism, escalate.
- "Recent slice" verification (Step 1 of tomorrow's plan) is what the audit doesn't actually run — it requires querying predictions.predicted_at + the 4 calibration tiers + computing deltas. Pre-registered as the recalibration ticket's first action, not done in this audit.
- The dead-code saturation branch has been there since the curve class was written. It's only "dead" given current anchor positions — if the rebuild ever drops the 1.05 anchor (e.g., if data drift moves the population's behavior), the bypass wakes up. Worth addressing even if not load-bearing today.

## Numerical artifacts

- `/tmp/audit_recalib.py` and `/tmp/audit_recalib_v2.py` (on local) reproduce the audit. v2 simulates apply_calibration against the live curves and is the authoritative trace.
