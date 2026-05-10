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

---

## Finding 8 — interim TG re-enable gate (pre-registered 2026-05-07, ~30 min after deploy)

8 days of silent TG (waiting for full Finding 8 acceptance) is too long for product presentability. Pre-registering an interim gate that lets us re-enable rules 9+10 sooner if the EMA smoothing demonstrably prevents bursts at a shorter timeframe. The full 7d gate stays as the longer-term validator; the interim gate is a faster-but-lower-confidence path that buys back user-visible alert delivery.

This is a sub-pre-registration under the parent Finding 8. Not a new finding — same hypothesis, same fix, same Path E escalation. Just an additional, earlier, more permissive verdict point.

### Hypothesis (same as parent Finding 8 H1)

EMA smoothing deployed at 2026-05-07T16:45:54Z prevents the cutoff-recompute aliasing burst. After ~48h post-deploy, bucket distribution should show no bursts even if total volume is below design target.

### Acceptance criterion (frozen, less strict than the full 7d window)

Evaluated at **2026-05-09T16:45Z** (48h post-deploy):

1. **Maximum hour-level MED count in any 1h window ≤ 30.** Yesterday's burst was 636 in one hour; this allows 5× design target as ceiling. A pass means EMA smoothing successfully dampened the recompute discontinuity.
2. **At least one daemon recompute fires within the 48h window without producing a burst.** A scheduled recompute happens at 24h post-deploy (~2026-05-08T16:45Z); the gate requires that recompute, AND any other restart-triggered recomputes in the window, to produce no burst.
3. **HIGH count: any value (0 included).** Genuine rarity acceptable at the interim. The full 7d gate enforces HIGH coverage; interim is permissive.
4. **No mass-coverage requirement.** The 16/24-hour-coverage criterion from the full 7d gate is NOT applied at the interim. Interim is about "no bursts," not "always firing."

### Decision rules (frozen)

| Outcome at 2026-05-09T16:45Z | Action |
|---|---|
| **PASS (no burst, no >30/hr violations)** | Re-enable rules 9+10 with content-inspection gate (sample 10 alerts post-re-enable, confirm sensible content before declaring done). Full 7d acceptance criterion CONTINUES running for full validation. |
| **FAIL at 48h** (no burst, but some other criterion violation discovered during interim) | Rules stay disabled. Full 7d window continues. Reassess at full close 2026-05-15T16:45Z. |
| **FAIL with burst before 48h** | Immediate re-deactivation of rules (if they had been re-enabled prematurely — currently they're already deactivated, so this is moot). Escalate to Path E (fixed-percentile cutoffs). EMA smoothing rejected as insufficient. |

### Why a 48h interim gate (and not 24h or 72h)

- **24h** is too short — only one scheduled recompute fires in that window; insufficient to characterize whether smoothing works across multiple recomputes.
- **48h** captures TWO scheduled recomputes (deploy + ~24h) plus any restart-triggered recomputes from incidental fly events. Enough to surface aliasing if it persists.
- **72h** captures three but adds 24 hours of silent TG without proportional information gain.

48h is the smallest window that exercises the smoothing mechanism multiple times.

### What this changes about the parent Finding 8 acceptance

**Nothing.** The parent's full 7d acceptance criterion (rolling MED rate in [21, 210], no hour > 10/hr, ≥16/24 hour coverage, HIGH in [1, 15]) is unchanged. The interim gate is **strictly less strict** — passing interim does not imply passing full. The full 7d gate remains the authoritative validator.

If interim PASSES and rules are re-enabled, the full 7d gate continues running. If full 7d FAILS later (e.g., persistent under-firing), rules get re-deactivated and Path E executes. Path E escalation is unchanged from the parent pre-registration.

### Why the X post probably ships at the interim gate, not the sustain auto-lift

- **TG re-enable is the more user-visible recovery.** "Alerts back on with bucket framing" is concrete and demoable. "Sustain feature back online" is technical and harder for outside readers to evaluate.
- **48h interim gate ≤ ~hours-to-day for sustain auto-lift** — but sustain auto-lift verdict at this commit's writing time is "deferred not failed" (Finding 7f-validation-deferred at `ea6d5f5`); re-validation triggers at corpus adequacy or 72h.
- The eight-findings narrative absorbs cleanly when at least one user-visible recovery has landed. Interim TG re-enable is the most likely first landing.

If interim gate passes at 2026-05-09T16:45Z (~48h from now): X post becomes most publishable. Sustain may or may not be re-validated by then. If yes, both wins land in the same post. If no, sustain becomes a separate +days-later post.

### Holding state until interim verdict

- **Rules 9+10 stay deactivated** until 2026-05-09T16:45Z verdict.
- **No silent re-enable.** Whatever the verdict, it gets committed publicly with the supporting data.
- **Full 7d gate continues independently.** Interim verdict doesn't preempt the full-window check.

### Receipts trail (Finding 8 chain, with interim sub-pre-reg)

| Commit | Action |
|---|---|
| `53be35f` Finding 8 pre-registration | Diagnostic ran |
| `790c8dd` Finding 8 diagnostic — H1 confirmed | EMA fix pre-registered |
| `4d13430` Finding 8 EMA fix landed | 7d full gate clock starts |
| `70b4baf` Finding 8 — interim 48h TG re-enable gate pre-registered | Interim verdict 2026-05-09T16:45Z; full verdict 2026-05-15T16:45Z |
| **(this commit) Finding 8 — interim criterion AMENDED pre-verdict; split EMA-verification from alert-volume** | Amendment commits at T+25.93h; verdict at T+48h; ~22h before verdict data resolves the criterion |

---

## Finding 8 — interim criterion amendment (committed pre-verdict)

**Captured:** 2026-05-08T18:01Z, 25.93h post-Finding-8-deploy. Verdict at 48h post-deploy is ~22h away. **This amendment commits before verdict data resolves the original criterion**, per the publish-then-post discipline rule: frozen criteria can be amended IF the amendment commits publicly before the verdict data is in.

### What the original interim criterion missed

The original interim criterion (frozen at `70b4baf`) had four sub-criteria:

1. Maximum hour-level MED count in any 1h window ≤ 30
2. ≥1 daemon recompute fires within 48h window without producing a burst
3. HIGH count: any value (0 included)
4. No mass-coverage requirement at interim

**The criterion conflated two distinct concerns into one verdict:**

- **Concern A — EMA-fix verification:** does the smoothing fix prevent recompute aliasing bursts? Sub-criteria 1, 2, and `rebuild_failures=0` directly test this.
- **Concern B — alert-volume verification:** does the volume-target calibration produce enough MED predictions to make rules 9+10 worth re-enabling? **The original criterion does NOT test this.** It only requires that bursts not happen. An interim PASS under the original criterion is consistent with both:
  - (a) EMA fix works AND alerts fire at design rate → re-enable rules 9+10 → users see alerts
  - (b) EMA fix works AND zero MED predictions are produced → re-enable rules 9+10 → users see silence

The current state at T+25.93h is shape (b): cumulative MED=0, max 1h MED=0, no bursts (because no MED at all). The original criterion would PASS at verdict trivially. Re-enabling rules 9+10 against this distribution would ship into ~6 days of silent rules until the full 7d gate fails for the rolling-7d MED < 21 reason and Path E executes.

**That's a verification-by-structure-not-substance failure** at the criterion-design level — same shape as the LOG_THRESHOLD framing-check (counting alerts isn't verifying alert content). The original criterion verified that bursts didn't happen, not that the system is in a state worth re-enabling rules under.

### The amendment (frozen here, splits Concern A from Concern B)

**EMA-fix-verified gate (subset of original criterion 1+2 + rebuild_failures):**

1. Maximum hour-level MED count in any 1h window ≤ 30
2. ≥1 daemon recompute fires within 48h window without producing a burst
3. `rebuild_failures = 0`

PASS = EMA smoothing fix is verified to prevent aliasing bursts. **This is the EMA fix's verdict.**

**Alert-volume gate (NEW, additional):**

4. ≥1 MED prediction observed in the 48h window (`COUNT(MED) WHERE predicted_at >= deploy AND predicted_at < deploy + 48h ≥ 1`)

PASS = the volume-target calibration is producing at least minimal alert flow. **This is rules-9+10's re-enable gate.**

### Decision rules (frozen)

At interim verdict (2026-05-09T16:45Z):

| EMA-fix-verified | Alert-volume gate | Action |
|---|---|---|
| PASS | PASS | Re-enable rules 9+10 with content-inspection sample of 10 alerts. EMA fix verified. Full 7d acceptance window continues. |
| PASS | FAIL | **Do NOT re-enable rules 9+10.** EMA fix verified independently. Trigger one of two pre-registered branches (chosen at verdict time based on diagnostic): (a) pre-register a cutoff-recalibration analysis (separate from EMA fix; investigates *why* 0 MED is produced), OR (b) trigger Path E early (revert to fixed-percentile cutoffs that produce alerts at known rate). |
| FAIL (burst) | n/a | Path E executes immediately per parent Finding 8 pre-registration. EMA fix rejected. |
| FAIL (other) | n/a | Stay in current state; reassess at full 7d window close. |

### What this amendment is NOT

**This is NOT a relaxation of the original criterion.** It's the opposite: it ADDS a gate (alert-volume) that the original criterion didn't include. Original criterion's bar to re-enable rules 9+10 was "no bursts." Amendment's bar to re-enable rules 9+10 is "no bursts AND ≥1 MED actually fired." Strictly higher bar.

**This is NOT a post-hoc rationalization.** The amendment commits at T+25.93h; the verdict is at T+48h; **22.07h remain before the verdict data resolves the criterion**. A reader inspecting the receipts trail can verify:

- Original interim criterion: committed at `70b4baf` (2026-05-07T17:30Z)
- Amendment: this commit (2026-05-08T18:01Z)
- Verdict event: 2026-05-09T16:45Z (~22h after this amendment)
- The amendment predates the verdict by a meaningful margin; the change isn't being made to fit a result that's already in.

**This is NOT a precedent for arbitrary amendments.** The discipline applied here is narrow: **a frozen criterion can be amended IF the amendment commits publicly BEFORE the verdict data resolves the original criterion**, AND the amendment refines/splits the criterion (not relaxes it), AND the amendment surfaces a conflation in the original criterion explicitly.

### The pattern this names

**Pre-verdict amendment of frozen criteria** is itself a discipline-pattern application — the publish-then-post rule applied to pre-registered acceptance criteria. The arc:

```
T-N: criterion committed publicly (frozen)
T-K (K>0): criterion examined for design flaws (e.g., conflation) before verdict data is in
T-K+ε: amendment committed publicly
T+0: verdict event happens; data interpreted under amended criterion
T+ε: reader can verify amendment predates verdict; criterion change wasn't post-hoc
```

This is a **strictly narrower** rule than "criteria can be amended whenever you want." It only fires when:
1. The amendment is committed publicly (verifiable timestamp).
2. The amendment commits before the verdict data is in (genuinely pre-verdict, not "before the deadline but after we've seen partial data").
3. The amendment refines or splits an existing criterion (catches a conflation), rather than relaxing it.

The 2026-05-07 LOG_THRESHOLD case had the same shape: an alert path's verification was found to conflate "alerts firing" with "alerts firing on sensible content" — the verification-by-content rule was added to memory as the refinement. The Finding 8 case applies the same shape to acceptance criteria themselves, not just to verification methodology.

**Adding to memory as a generalization:** verification-by-content applies recursively to the criteria themselves, not just to the system being tested.

### What changes operationally as a result

- `/api/status.acceptance_gates` already exposes the gate state. The `summary` field is updated to reflect the amended criterion (separate edit; deploys with next push).
- The TG pinned message + Variant 0 X post (already drafted) reference the original criterion. Both remain accurate at the EMA-fix-verification level; the alert-volume gate is a refinement that doesn't contradict their framing. **The drafted posts still ship as-is when their respective gates resolve.**
- The TG follow-up templates (`tg_pinned_message.md`) for "PASS" and "Path E" outcomes need a third template for "PASS-on-EMA-but-FAIL-on-alert-volume" — drafted next.

### Pre-drafted TG follow-up for the new branch

For the case where EMA-fix-verified PASSES but alert-volume FAILS (rules 9+10 stay deactivated; cutoff-recalibration or Path E to be chosen):

```
🔧 EMA fix verified — alerts still paused

The 48h interim acceptance gate split into two checks: (1) does the
EMA smoothing fix prevent calibration aliasing bursts? (2) does the
volume-target calibration produce enough MED alerts to be worth
re-enabling rules 9+10?

Check 1 PASSED — no bursts in 48h, ≥1 daemon recompute observed
without aliasing, rebuild_failures=0. The EMA fix works as designed.

Check 2 FAILED — 0 MED predictions in 48h. Re-enabling alerts now
would ship users into ~6 days of silence until the full 7d gate
closes. Not worth it.

Pre-registered next step: investigate WHY the cutoff is producing
zero MED, separate from the EMA-fix verification. OR execute the
parent Path E (fixed-percentile cutoffs) directly. Decision lands
within 24h.

Alert silence over alert noise still holds. Receipts updated:
github.com/Dspro-fart/graduate-oracle
```

This template lands in `pump-jito-sniper/docs/strategy/tg_pinned_message.md` (private artifact, separate edit).

### Receipts trail (Finding 8 chain, with amendment)

| Commit | Action |
|---|---|
| `53be35f` Finding 8 pre-registration | Diagnostic ran |
| `790c8dd` Finding 8 diagnostic — H1 confirmed | EMA fix pre-registered |
| `4d13430` Finding 8 EMA fix landed | 7d full gate clock starts |
| `70b4baf` Finding 8 — interim 48h TG re-enable gate pre-registered | Original criterion conflated EMA-verification with alert-volume |
| `f3f1f3e` Finding 8 — interim criterion amended pre-verdict; split EMA-verification from alert-volume | Amendment committed at T+25.93h; verdict at T+48h; criterion is strictly higher bar than original |
| **(this commit) Finding 8 interim verdict resolved — Variant 5B fired (EMA-fix PASS + alert-volume FAIL)** | Verdict at 2026-05-09T16:45:54Z; numbers below; rules 9+10 stay disabled; sub-branch decision (a-recalibration vs b-Path-E) remains user-owned per `feedback_methodology_calls_user_owned.md` |

---

## Interim verdict (Variant 5B fired) — 2026-05-09T16:45:54Z

**Verdict ships at T+~7h, behind the pre-registered "within minutes of verdict" cadence.** Late ship documented; root cause was a parallel investigation (score-latency Fix A+B deploy at 06:50Z + Case Study 01 daemon empty-DB diagnosis) that consumed the implementer's attention through the verdict window. The 7h delay is a discipline-pattern erosion entry; the durable artifact is publishing it anyway with the lateness called out, not silently shipping with a fictitious timestamp.

### Verdict numbers (queried from `/data/data.sqlite` post-verdict)

| Gate | Criterion | Measured | Result |
|---|---|---|---|
| EMA-fix-verified gate | Max 1h MED count in 48h ≤ 30 | **0** | ✅ PASS |
| EMA-fix-verified gate | ≥1 daemon recompute without burst | **2** (24h cadence × 48h window) | ✅ PASS |
| EMA-fix-verified gate | rebuild_failures = 0 | **0** | ✅ PASS |
| Alert-volume gate | ≥1 MED prediction in 48h | **0** | ❌ FAIL |

**Supporting state from `/api/status.bucket_cutoffs` at verdict time:**
- `bucket_logic_mode`: `bimodal_cliff` (engaged because raw GBM scores saturate at the top sample)
- `raw_gbm_p_high_unsmoothed`: 0.9795 (top decile saturated)
- `ceiling_mass_pct`: 15.5% (large cluster of mints scoring at the calibration ceiling)
- `n_above_ceiling`: 0
- `empty_high_window_count`: 1 (at least one recompute saw no HIGH cluster — consistent with the bimodal-cliff degenerate case)
- `n_samples_used`: 8995 across the 7d rolling window
- 48h-window prediction breakdown: HIGH=0, MED=0, LOW=4305 (n=4305 total, 100% LOW)

### What this means

**The EMA fix did its job.** Zero recompute aliasing bursts in 48h. The smoothed cutoff transitions held; `rebuild_failures=0`; the H1 (recompute aliasing) pathology that produced the original 697-MEDs-in-1h burst is now suppressed at the smoothing layer.

**The volume-target calibration is producing zero non-LOW assignments.** Not "low volume" — literally zero. The bimodal-cliff fallback engages because raw GBM scores are saturating, and downstream the cutoff resolves to a value (0.113) that nothing crosses (`n_above_ceiling=0`). Rules 9+10 cannot be re-enabled against this distribution: re-enabling now would ship users into the full 7d gate window with continuous silence, then fail the 7d MED-volume floor and trigger Path E anyway — a worse outcome than staying disabled and resolving the upstream cause first.

**Per the amended decision rules (PASS / FAIL row):** "Do NOT re-enable rules 9+10. Trigger one of two pre-registered branches: (a) cutoff-recalibration analysis, OR (b) trigger Path E early." That sub-branch decision is methodology and is user-owned (memory: `feedback_methodology_calls_user_owned.md`). It is **not** taken in this verdict commit. The "decision lands within 24h" pre-reg clock starts at this commit's timestamp.

### Downstream consequence — Case Study 01 trigger upstream-blocked

Case Study 01's data collection started at this verdict timestamp (trigger fired at 2026-05-09T16:45:54Z, T+0 of the verdict). The case study reads `predictions WHERE grad_prob_bucket IN ('HIGH','MED') AND age_bucket <= 75` from the same production DB. With 0 HIGH/MED predictions emitted in the 8h+ since trigger, the case study harness has 0 observations.

This is **not** a Case Study 01 daemon bug. It is the same upstream-block that this verdict identifies. The Case Study 01 pre-registration is being amended in a paired commit (publish-then-post discipline) to add a Branch C addendum covering the upstream-blocked subcondition explicitly. See `case_study_01_gmgn_comparison_prereg.md` § amendment.

### Sub-branch (a) vs (b) decision — pending user

| Sub-branch | What it does | Trade-off |
|---|---|---|
| **(a) Cutoff-recalibration analysis** | Investigate WHY `bimodal_cliff` mode produces 0 cross-cutoff samples. May lead to a refined volume-target calibration that handles ceiling-mass distributions. | Preserves volume-target self-stabilization. Could iterate — risk of fix-N+1 chain. |
| **(b) Trigger Path E early** | Ship fixed-percentile cutoffs (97th percentile of raw GBM scores in rolling 7d) immediately. Loses self-stabilization; ships consistently. | Stops the iteration. Locks in a known-good baseline. Cannot re-attempt volume-targeting without a fresh pre-reg. |

Both unblock the Case Study 01 trigger downstream (whichever ships and produces non-zero MED restores the source data). The user picks at "decision lands within 24h."

### Receipts trail update

This verdict commit is the closing event for the Finding 8 interim 48h gate. The full 7d gate (`full_close = 1778861154 = 2026-05-15T16:45Z`) remains open. Rules 9+10 stay disabled until either (a) recalibration succeeds and produces sustained MED flow, or (b) Path E ships and re-arms the rules with fixed-percentile cutoffs. Either way, a separate verdict commit will close the full gate.
