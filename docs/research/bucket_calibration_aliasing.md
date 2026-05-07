# Finding 8 — bucket calibration aliasing (pre-registration)

**Captured:** 2026-05-07. Pre-registration ships BEFORE diagnostic work, per the iteration-limit discipline. Hypothesis, diagnostic method, acceptance criterion, and escalation path are all frozen here. The diagnostic that follows can confirm, refine, or reject the hypothesis — but cannot reframe the acceptance criterion or skip the escalation rule.

---

## Observed phenomenon

While running LOG_THRESHOLD verification + bucket distribution check post-cutover, surfaced a discontinuity in the MED-bucket distribution that doesn't match design-target-driven steady-state behavior:

```
hour_back  HIGH    MED    LOW   med/high_prob<0.5
  - 0h..-8h    0      0   ~75-130 each       0
  - 9h ago     0     61    64                61
  -10h ago     0    636    51               636    ← 2-hour spike
  -11h ago     0      2    54                 2
  -12h..-23h   0      0   ~60-140 each       0
```

- **HIGH bucket: 0 across entire 24h.** Design target: 5/week ≈ 0.7/day → seeing 0/day is within Poisson noise but 7/7 days at 0 wouldn't be.
- **MED bucket: 0 except for a 2-hour spike** (-9h to -10h ago) producing 697 of 699 total MED-bucket predictions in 24h.
- Design target: 10 MED/day ≈ 0.4/hour. The probability of 636-in-one-hour under Poisson(λ=0.4) is essentially zero.
- Last 1h: 0 HIGH, 0 MED, 130 LOW. Recent max predicted_prob = 0.6122 (assigned to LOW).

The 2-hour burst pattern is not consistent with random-Poisson variance around a steady mean. It's a calibration instability.

## Why this matters (user-facing impact)

The bucket framework's product value depends on **consistent alert delivery**. Bursty delivery fails users in two distinct ways:

1. **22 hours of zero alerts** → users disengage, the channel stops feeling alive
2. **2-hour burst of 300+ alerts** → users overwhelmed, signal:noise drops to zero, brand damage

Bursty delivery is a different product than steady delivery. **Re-enabling rules 9+10 against this distribution is worse than the current rules-deactivated state**, not better. Pre-registering the fix BEFORE re-enable.

## Hypothesis (frozen pre-registration)

**Primary hypothesis (H1):** the `raw_gbm_p_high` daemon's cutoff-recompute window produces an aliasing burst.

Mechanism:
1. The daemon recomputes the volume-target cutoff every `REBUILD_INTERVAL_S = 24 hours` over a `SAMPLE_WINDOW_S = 7d` rolling window.
2. During recompute, the new cutoff briefly sits at the volume-target percentile, which can be lower than the previous cutoff if the underlying score distribution drifted upward.
3. Mints in the at-ceiling cluster that were just below the old cutoff suddenly qualify under the new lower cutoff.
4. A wave of these mints flips to MED in the post-recompute window.
5. Within hours, as the rolling window absorbs the new high-prob mints, the cutoff stabilizes higher; nothing new qualifies.
6. Net effect: 2-hour MED burst around recompute, then ~22 hours of nothing until the next recompute.

**Alternate hypothesis (H2):** the spike is unrelated to the daemon recompute and is caused by an upstream model output discontinuity (GBM shadow recompute, isotonic re-fit, or feature pipeline event).

**Alternate hypothesis (H3):** the spike is a market burst — pump.fun produced an unusually high concentration of high-prob mints in that 2-hour window (e.g., a viral meta).

The diagnostic separates these.

## Diagnostic method (frozen pre-registration)

Before proposing any fix, run the following. Each step's output is committed publicly before the next decision.

1. **Pull bucket-cutoff state history.** Recover the timeline of `raw_gbm_p_high` (and `ceiling_value`, `n_samples_used`, `computed_at`) over the last 7 days. Sources, in order of preference:
   - `/api/status` snapshots, if archived
   - Daemon log lines (`[bucket_cutoffs] bimodal_cliff mode: ...`) from `fly logs --since 7d`
   - Reconstruct from predictions table (each prediction's bucket assignment was made against the then-current cutoff; can back out the cutoff per-window)

2. **Cross-reference spike timing with daemon rebuild events.**
   - The MED spike was at -9h to -10h ago. Compute the actual UTC timestamps.
   - Find the daemon rebuild events near that window.
   - **If a rebuild event fired within ±15 min of the spike's start** → H1 confirmed. Aliasing is the mechanism.
   - **If no rebuild event aligns** → H1 rejected. Open sub-investigation under H2 or H3.

3. **If H1 confirmed, characterize the aliasing magnitude.**
   - What was the cutoff value before the rebuild?
   - What was the cutoff value after the rebuild?
   - How many of the 636 spike-hour MED predictions had `raw_gbm_score` between the old and new cutoff?
   - This bounds the aliasing-attributable share. If it's ≥80% of the spike, H1 fully explains. If <80%, sub-investigation needed for the remaining mints.

4. **If H2 (upstream): identify the discontinuity source.** Likely candidates: GBM shadow rebuild, calibration re-fit, feature extractor change. Cross-reference timestamps.

5. **If H3 (market burst): rare but possible.** Check whether the 636 mints share creator/cluster/funding patterns suggesting coordinated activity.

## Acceptance criterion (frozen pre-registration, no revising downward)

Whatever fix ships, post-fix the bucket distribution must satisfy:

1. **Rolling-7d MED rate within 0.3–3× of `TARGET_MED_PER_DAY`** (currently 10/day). So the rolling-7d MED count must be within [21, 210].
2. **No individual hour exceeds 5× the per-hour design rate** (5 × 10/24 ≈ 2.1/hour, so cap at 10/hour). Even spikes mustn't exceed 10 MED in any single hour.
3. **Continuous coverage:** at least 16 of every 24 hours must have ≥1 MED OR be a confirmed low-volume window (rolling-1h prediction count <50). The "0 alerts for 22 hours" pattern fails this.
4. **HIGH bucket fires at within 0.3–3× of `TARGET_HIGH_PER_WEEK`** (currently 5/week). Rolling-7d HIGH count must be within [1, 15].

Acceptance check runs on a 7-day rolling window beginning 24 hours after deploy (allow one full rebuild cycle to settle).

## Pre-registered iteration-limit escalation (frozen)

If the first fix attempt fails the acceptance criterion at the 7-day check:

- **Refined retry path:** ONLY if the diagnostic of the failure surfaces a NEW mechanism not in H1/H2/H3 above. Refined-retry pre-registration must include the new mechanism as its hypothesis + new acceptance criterion.

- **Path E escalation:** if no new mechanism is identified, **revert to fixed-percentile cutoffs** without volume-targeting:
  - `raw_gbm_p_high` set to a fixed 97th percentile of raw GBM scores in the rolling-7d window, recomputed daily.
  - Loses the volume-target self-stabilization but ships consistently.
  - Accept the under-firing trade-off: design rate may not be hit, but distribution will be steady (no 2-hour spikes, no 22-hour silences).
  - User-facing /api/scope explicitly states the trade-off.

**This is iteration-limit discipline applied at the top of a new finding chain.** Don't try fix-N, fix-N+1, fix-N+2 on the calibration logic. Pre-register the stop-iterating point now, before any fix is implemented.

## What does NOT change without re-pre-registration

- Acceptance criterion thresholds (0.3–3× target, 5× hourly cap, 16/24 hour coverage)
- The Path E escalation (fixed-percentile fallback)
- The iteration cap (one refined retry, then Path E)

If the diagnostic reveals the situation is meaningfully different from the assumed shape (e.g., the system is actually working correctly and the spike was a one-time event), THAT becomes a separate finding requiring its own pre-registration. **Don't quietly relax the criterion to fit the data.**

## What this finding shares + doesn't share with Finding 7

**Shares:**
- Pre-fix-then-fix discipline pattern (publicly timestamped diagnosis-before-fix)
- Iteration-limit pre-registration (escalation path frozen before first fix ships)
- Acceptance criteria frozen pre-deploy

**Doesn't share:**
- Different system (bucket calibration vs post_grad k-NN)
- Different lifecycle position (alert volume management vs prediction model correctness)
- Independent of post_grad_survival_prob auto-lift gate (which runs in parallel today)

## Receipts trail

| Diagnosis | Action |
|---|---|
| **(this commit) Finding 8 pre-registration — bucket calibration aliasing hypothesis** | (diagnostic next, then fix or sub-investigation) |
| ... | ... |

The trail extends as the diagnostic, fix, and verification ship.

## Cross-references

- BACKLOG.md "Finding 8" — same pre-registration in the backlog index
- `web/bucket_cutoffs.py` — the daemon under investigation
- Memory: `feedback_pre_registration_branches.md` — discipline rules including iteration-limit and verification-by-content
- Memory: `MEMORY.md` — adding a one-line index entry for this finding

## Holding state until acceptance criterion passes

- **Rules 9+10 stay deactivated.** Re-enabling against current bursty distribution is worse than current state. Stays deactivated until acceptance criterion passes.
- **/api/scope** does NOT yet acknowledge bucket distribution issues. Not surfaced publicly until diagnostic runs. (Will be added if H1+ confirmed.)
- **X post stays held.** Cutover narrative absorbs Finding 8 resolution.
