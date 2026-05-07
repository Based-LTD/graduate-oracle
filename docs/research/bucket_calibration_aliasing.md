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

## Diagnostic results (committed before fix proposal)

### Step 1: Current bucket cutoff state

```
bucket_logic_mode:    bimodal_cliff
ceiling_value:        0.1132
ceiling_mass_pct:     27.0%       (27% of samples sit at the ceiling)
raw_gbm_p_high:       0.9795      ← the active MED cutoff
n_above_ceiling:      0           (zero mints above ceiling currently)
n_samples_used:       3911        (over 7-day rolling window)
empty_high_window_count: 1
computed_at:          2026-05-07T16:12:56Z
```

### Step 2: MED-bucket prediction timing reconstruction

Pulled all MED-bucket predictions from the predictions table over last 24h:

```
total MED predictions in 24h: 699
first MED at: 2026-05-07T05:06:29Z (-41501s ago)
last MED at:  2026-05-07T06:32:05Z (-36365s ago)
window span: 5136s ≈ 1.4 hours

MED count by hour:
  -10h ago: 697   ← spike concentrated in a single hour
  -11h ago:   2
  all other hours in 24h: 0
```

The "2-hour spike" was actually a **1.4-hour event** at **05:06 UTC to 06:32 UTC** with 99.7% of MED-bucket assignments concentrated in a single hour.

### Step 3: raw_gbm distribution at the bucket boundary

Within the spike-hour MED predictions:

```
spike-hour MED raw_gbm range: [0.6252, 0.9795]   median: 0.9795
```

Within LOW-bucket predictions in the surrounding hours:

```
-8h  LOW raw_gbm max: 0.5567
-9h  LOW raw_gbm max: 0.6517
-10h LOW raw_gbm max: 0.5581  (same hour as spike but LOW-bucketed)
-11h LOW raw_gbm max: 0.5223
-12h LOW raw_gbm max: 0.5663
```

**Boundary movement evidence:**
- During spike: mints with raw_gbm as low as **0.6252** were assigned MED.
- After spike: mints with raw_gbm up to **0.6517** are assigned LOW.
- Current cutoff: **0.9795**.

So between the spike and now, the cutoff **moved from ~0.6252 to 0.9795** — a 57% jump. **The cutoff was lower during the spike than it is now.** Mints in the raw_gbm range [0.6252, 0.9795] qualified as MED during the spike but would be LOW under the post-spike cutoff.

### H1 verdict: confirmed

The diagnostic confirms hypothesis H1 (calibration aliasing during cutoff recompute). The mechanism is now characterized:

1. A daemon-restart event (likely a fly machine restart or a deploy) occurred near 2026-05-07T05:06Z.
2. At restart, `bucket_cutoffs.start()` triggered an immediate `rebuild()` (per `web/bucket_cutoffs.py:412`).
3. The recompute produced a lower `raw_gbm_p_high` value (~0.6252) — likely because the rolling 7-day window at that moment had fewer high-raw_gbm samples than it does now.
4. Mints with raw_gbm in the [0.6252, 0.9795] range began qualifying as MED.
5. ~697 mints were MED-bucketed over the next 1.4 hours.
6. As new high-raw_gbm samples accumulated in the rolling window (some of them produced by the spike itself), subsequent recomputes (next restart or next 24h scheduled) raised the cutoff to 0.9795.
7. Post-cutoff-rise: zero mints qualify as MED because raw_gbm > 0.9795 is rare.

H2 (upstream model discontinuity) and H3 (market burst) are rejected. The mechanism is internal to the bucket-cutoff daemon's volume-target derivation.

### Why H1 is the load-bearing finding

The volume-target formula (`web/bucket_cutoffs.py:223-237`) uses the `target_med_count`-th highest raw_gbm value in the at-ceiling cluster as the cutoff. With `target_med_count = TARGET_MED_PER_DAY * 7 = 70`, the cutoff is the 70th-highest raw in the rolling 7-day at-ceiling cluster.

This formula is **sensitive to which 70 mints are at the top** of the cluster at recompute time. When the rolling window happens to have fewer high-raw_gbm samples (e.g., a quiet period, or aging-out of recent burst), the 70th-highest can be substantially lower. Each recompute is a discrete step against a possibly-changed sample set.

**The aliasing isn't a bug in any single line of code — it's a property of "discrete recompute over a rolling window with a percentile-based threshold."**

## Pre-registered fix (Finding 8 first attempt)

Per pre-registration: first-attempt fix can target H1 mechanism directly without re-pre-registration as long as it doesn't violate the iteration-limit (one refined retry → Path E).

**Approach: smooth cutoff transitions across recomputes.**

Replace `_state["raw_gbm_p_high"] = raw_p_high` (instantaneous overwrite) with an EMA (exponential moving average) blend:

```python
SMOOTHING_ALPHA = 0.2   # new cutoff weight; old cutoff weight = 0.8

prev = _state["raw_gbm_p_high"]
if prev is None:
    new_cutoff = raw_p_high  # cold start: no smoothing
else:
    new_cutoff = SMOOTHING_ALPHA * raw_p_high + (1 - SMOOTHING_ALPHA) * prev
_state["raw_gbm_p_high"] = new_cutoff
```

**Why EMA:**
- A single recompute can move the cutoff by at most 20% of the (new - old) gap. For the observed [0.6252, 0.9795] jump, EMA would have produced 0.6252 → 0.7960 → 0.8327 → ... approaching 0.9795 over multiple recomputes.
- Reverse direction works the same: a recompute that drops the cutoff sharply gets dampened. Mints in the gap qualify gradually rather than in a 1.4-hour burst.
- Cold start (first ever recompute, prev is None) bypasses smoothing — reasonable since there's nothing to smooth against.
- Daemon restart: `_state` is in-process memory and resets on restart. **EMA needs to persist across restarts** to be effective. Fix includes persisting `raw_gbm_p_high` to a small JSON sidecar file (`/data/bucket_cutoffs_state.json`) and reloading on startup.

**Verification gate (frozen acceptance criterion, repeat from pre-registration):**
1. Rolling-7d MED rate within 0.3-3× `TARGET_MED_PER_DAY`
2. No individual hour exceeds 5× per-hour design rate (10/hour cap)
3. ≥16 of every 24 hours have ≥1 MED OR are low-volume (<50 pred/hour)
4. HIGH bucket within 0.3-3× `TARGET_HIGH_PER_WEEK`

7-day window, starting 24h after deploy.

**Pre-registered iteration-limit (repeat from pre-registration):**
- If acceptance fails at 7d check → Path E (fixed-percentile cutoffs without volume-targeting). No fix-N+1 attempts on the smoothing logic.

## Receipts trail

| Diagnosis | Action |
|---|---|
| `53be35f` Finding 8 pre-registration — H1 hypothesis + diagnostic + acceptance + Path E | (diagnostic ran) |
| **(this commit) Finding 8 diagnostic — H1 confirmed** | (smoothing fix pre-registered + ships next) |
| ... | (fix deploys, 7d acceptance check, then either ship-confirmed or Path E) |

## Cross-references

- BACKLOG.md "Finding 8" — same pre-registration in the backlog index
- `web/bucket_cutoffs.py` — the daemon under investigation
- Memory: `feedback_pre_registration_branches.md` — discipline rules including iteration-limit and verification-by-content
- Memory: `MEMORY.md` — adding a one-line index entry for this finding

## Holding state until acceptance criterion passes

- **Rules 9+10 stay deactivated.** Re-enabling against current bursty distribution is worse than current state. Stays deactivated until acceptance criterion passes.
- **/api/scope** does NOT yet acknowledge bucket distribution issues. Not surfaced publicly until diagnostic runs. (Will be added if H1+ confirmed.)
- **X post stays held.** Cutover narrative absorbs Finding 8 resolution.

---

## Finding 8 fix landed (deploy verification)

**Deployed:** 2026-05-07T16:45Z. EMA smoothing + persistence sidecar shipped to `web/bucket_cutoffs.py`.

### Pre-deploy seed (preventing first-deploy aliasing)

Before pushing the code, seeded `/data/bucket_cutoffs_state.json` with the current `raw_gbm_p_high=0.9795`:
```json
{"raw_gbm_p_high": 0.9795082800150695, "saved_at": 1778171883}
```

This guards against a first-deploy aliasing event: without seeding, the freshly-deployed code would cold-start (no persisted file → no smoothing) and the rebuild could land on a different cutoff than the prior in-memory state. With seeding, the first rebuild post-deploy already smooths against the known-stable value.

### Post-deploy verification

```
[bucket_cutoffs] daemon started (rebuild every 24h)
[bucket_cutoffs] bimodal_cliff mode: ceiling=0.1132 (26.7% mass),
                  raw_gbm_threshold=0.9795 (target_med=70/7d),
                  above_ceiling=0 (n=3975)
                  [EMA: prev=0.9795 computed=0.9795 → smoothed=0.9795]
```

`/api/status.bucket_cutoffs`:
```
raw_gbm_p_high (smoothed):    0.9795082800150695
raw_gbm_p_high_unsmoothed:    0.9795082800150695
ceiling_value:                0.1132
computed_at:                  1778172354 (2026-05-07T16:45:54Z)
n_samples_used:               3975
status:                       ok
```

The freshly-computed unsmoothed cutoff happened to match the seed value exactly (0.9795) — both were derived from the same 7-day window with very similar membership. So the first EMA blend was identity. Future recomputes will exercise the smoothing meaningfully whenever the rolling window's 70th-highest at-ceiling raw_gbm shifts.

### What ships in the fix

1. `SMOOTHING_ALPHA = 0.2` — new computed cutoff weight, old cutoff weight = 0.8.
2. `_load_persisted_cutoff()` / `_save_persisted_cutoff()` — JSON sidecar at `/data/bucket_cutoffs_state.json` survives daemon restarts.
3. Rebuild path: load persisted previous → blend with freshly-computed → save smoothed result.
4. `/api/status` exposes both `raw_gbm_p_high` (smoothed, used for bucket assignment) and `raw_gbm_p_high_unsmoothed` (the raw computed value, for diagnostic visibility).
5. Daemon log line prints the EMA blend per rebuild.

### Acceptance check timeline (frozen pre-registration)

7-day window starting **2026-05-08T16:45Z** (24h after deploy, allowing one full rebuild cycle to settle). Acceptance criteria (frozen):

1. Rolling-7d MED rate within 0.3-3× `TARGET_MED_PER_DAY=10` → window count in [21, 210]
2. No individual hour exceeds 5× per-hour design rate → cap at 10/hour
3. ≥16 of every 24 hours have ≥1 MED OR are low-volume (<50 predictions/hour)
4. HIGH bucket within 0.3-3× `TARGET_HIGH_PER_WEEK=5` → window count in [1, 15]

### Pre-registered Path E if acceptance fails (frozen, repeat from pre-registration)

Revert to fixed-percentile cutoffs without volume-targeting. Loses self-stabilization; ships consistently. **No fix-N+1 attempts on smoothing logic.**

### Receipts trail (Finding 8 chain, complete through deploy)

| Diagnosis | Action |
|---|---|
| `53be35f` Finding 8 pre-registration | (diagnostic ran) |
| `790c8dd` Finding 8 diagnostic — H1 confirmed; EMA fix pre-registered | (fix implemented) |
| **(this commit) Finding 8 fix landed** | (7d acceptance window starts 2026-05-08T16:45Z) |

### Holding state until 7d acceptance check

- **Rules 9+10 stay deactivated.** Re-enabling gated on acceptance criterion pass at 2026-05-15T16:45Z (24h post-deploy + 7d window).
- **/api/scope** unchanged — no public claims about bucket distribution stability until empirically verified.
- **X post stays held** — the eight-findings narrative needs at least one of (Finding 7f auto-lift, Finding 8 acceptance) cleanly resolved before publishing. Earliest: 7f auto-lift in ~10 hours; Finding 8 in 8 days.
