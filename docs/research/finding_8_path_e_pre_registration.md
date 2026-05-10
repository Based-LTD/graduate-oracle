# Finding 8 Path E pre-registration — fixed-percentile cutoffs on raw GBM

**Pre-registration commit.** Methodology, percentile choice, window choice, acceptance criterion, iteration-limit, and the backlog-ticket-for-deferred-calibration-bug all frozen here BEFORE the Path E code change deploys. Per the receipts pattern: this commit must predate the deploy. If anything in this writeup needs revision after deploy, the publish-then-post / pre-verdict-amendment discipline applies (a strictly narrower or refining amendment, committed publicly before the verdict data resolves).

**Branches off:** Finding 8 interim verdict (commit `87edcb7`, Variant 5B fired 2026-05-09T16:45:54Z). The verdict's amended decision-rule table for "PASS / FAIL" specified two pre-registered sub-branches: **(a) cutoff-recalibration analysis** OR **(b) trigger Path E early**. The user picked **(b)** at 2026-05-10 with three framing corrections that are encoded throughout this writeup.

---

## Why Path E (not sub-branch (a))

**Three reasons, ordered by methodology weight:**

### 1. Iteration-limit pre-registered for this case (load-bearing)

The parent Finding 8 pre-reg (`53be35f`) said: "If acceptance fails at 7d check → Path E. No fix-N+1 attempts on the smoothing logic." The interim amendment (`f3f1f3e`) split EMA-verification from alert-volume but inherited the parent iteration-limit. Sub-branch (a) cutoff-recalibration would be a new investigation chain ("why is isotonic calibration squashing upper-tail signal?") that risks fix-N+1, fix-N+2, fix-N+3 on the calibration design. Path E is the pre-registered escalation that stops the iteration loop.

### 2. Empirical snapshot shows signal exists; calibration is the bottleneck (load-bearing)

The pushback against (b) earlier in the chain was: "the calibrated output is degenerate — no signal exists upstream." The snapshot data refuted that:

| Field | min | median | p90 | p95 | p97 | p99 | max |
|---|---|---|---|---|---|---|---|
| `grad_prob_gbm_shadow` (raw GBM, post-deploy) | 0.0155 | 0.2988 | 0.4586 | 0.5176 | 0.5542 | 0.6197 | **0.8855** |
| `grad_prob_gbm_calibrated_shadow` (post-isotonic) | 0.0000 | 0.0405 | 0.0806 | 0.1132 | 0.1132 | 0.1132 | **0.1132** |

(n=5263 over the 48h window 2026-05-07T16:45 → 2026-05-09T16:45.)

The raw GBM has clean upper-tail distribution (max 0.886). The isotonic-calibrated output is hard-capped at 0.1132 with 7.2% of mints sitting at exactly that ceiling value. The calibration is the bottleneck. Path E sidesteps it by cutoff-on-raw-GBM-directly.

### 3. Tiebreaker (NOT load-bearing) — Case Study 01 unblock

Case Study 01's source data is HIGH+MED bucket emissions. Subcondition C-iv (in `case_study_01_gmgn_comparison_prereg.md` § Amendment 01) re-arms the case study trigger when either Finding 8 sub-branch produces ≥10 MED in the first 24h post-deploy. Path E ships and produces non-zero MED → C-iv re-arm fires.

**This is a downstream consequence, not a justification.** Reasons #1 and #2 carry the methodology weight; Path E is the right call even without the case study unblock. Framing #3 as a tiebreaker rather than load-bearing matters because the case study C-iv amendment shipped earlier today, and methodology choices should not bend to recent operational pressure.

---

## What Path E sidesteps (calibration bug, deferred not disproved)

**The isotonic calibration is squashing real upper-tail signal.** That is a fixable bug, not a structural-boundary case (it is **not** analogous to Finding 7, which had 3/5 training columns with zero values since launch — a data-plumbing dead-end with no signal to extract).

Path E does not fix the calibration. It bypasses it. The calibration bug is **deferred, not disproved.** A backlog ticket is opened explicitly so the bug doesn't get buried under the Path E ship: see [`BACKLOG.md` entry "Isotonic calibration squashes upper-tail signal to ceiling — investigate post-Path-E"](../../BACKLOG.md). The backlog ticket carries no iteration-limit (investigative scope only); any actual fix attempt would require a fresh pre-registration with its own iteration-limit, separate from this Path E deploy.

---

## Implementation (frozen)

### Window choice — 48h rolling

The bucket_cutoffs.py module currently uses a **7d rolling sample window** (constant `SAMPLE_WINDOW_S`). This window includes pre-deploy saturation events and breaks the percentile-based approach:

| Window | n samples | p99 | p99.5 | max | Status |
|---|---|---|---|---|---|
| 7d | 11829 | 0.9795 | 0.9795 | 0.9795 | **Saturated** (May 7 burst events still in window) |
| 3d | 6897 | 0.9795 | 0.9795 | 0.9795 | **Saturated** (same reason) |
| **48h** | **4173** | **0.6165** | **0.6703** | **0.8696** | **Clean — chosen** |
| 24h | 1906 | 0.5947 | 0.6690 | 0.8696 | Clean but n borderline |

The May 7 burst (Finding 8 trigger event) included 458 saturation events at raw=0.9795. They've now rolled out of the 48h window (Finding 8 deploy was at 16:45 UTC May 7; this writeup commits ~57h post-deploy). Choosing 48h gets clean signal NOW; the 7d window stays poisoned for another ~5 days until the saturation events roll out.

**Trade-off acknowledged:** 48h is more variance-prone day-to-day than 7d. The acceptance criterion (below) is calibrated to that variance.

### Percentile choice — 99.5th of raw GBM (user direction)

Per user call (greenlit 2026-05-10): **MED cutoff = 99.5th percentile of raw GBM scores in the 48h rolling window.**

Projected emission rate at deploy time (using the snapshot above):
- 99.5th percentile of 48h raw GBM = **0.6703**
- Mints in 48h above this cutoff: **~22 mints** = ~11 MED/day
- Closest to original `target_med_per_day=10` design intent
- Inside the parent Finding 8 acceptance band of [3, 30] MED/day

**HIGH cutoff** is left at a strictly higher percentile. Per user's framing "Path E ships consistently, not aggressively," HIGH is set to 99.9th percentile of the same window:
- 99.9th of 48h raw GBM = **0.7322**
- Mints in 48h above: ~4 mints = ~2 HIGH/day

### Bucket assignment in Path E mode

```
IF raw_gbm_score >= high_min_raw  → HIGH
ELIF raw_gbm_score >= med_min_raw → MED
ELSE                               → LOW
```

The calibrated value (`grad_prob_gbm_calibrated_shadow`) is **not used** for bucket assignment in Path E mode. It continues to be computed and stored in the predictions table (no change to the data write path) for the eventual calibration-bug investigation. Path E is purely a change to the bucket-emission decision function.

### Daemon recompute cadence

Inherits the existing `rebuild_interval_s = 86400` (24h). Path E recomputes the 99.5th and 99.9th raw GBM percentiles at each 24h tick over the trailing 48h window. EMA smoothing from the previous regime is dropped — fixed-percentile is structurally less prone to recompute aliasing because the cutoff moves smoothly with the rolling distribution rather than discontinuously around volume-target adjustment events.

---

## Acceptance criterion (frozen)

### Primary acceptance — 7d window after Path E deploys

**Both must hold at T+7d post-deploy:**

1. **Rolling-7d MED count in [21, 210].** Inherited from parent Finding 8 acceptance band — ~3 to ~30 MED/day, calibrated to Path E's projected ~11/day. The lower bound (21) catches under-firing; the upper bound (210) catches over-firing if raw GBM distribution shifts.
2. **No 24h sub-window with zero MED.** Continuous-flow check; protects against aliasing-style silences re-emerging via a different mechanism.

### Interim verification — 24h post-deploy

**Required at T+24h:**

3. **MED count ≥ 10 in the first 24h.** Triggers Subcondition C-iv re-arm of the Case Study 01 trigger if satisfied (this is the only place where the case-study coupling is acceptance-load-bearing — the C-iv re-arm condition was pre-registered in the case study amendment, not invented post-hoc to justify Path E).
4. **rebuild_failures = 0.** Verifies the new code path doesn't crash the recompute daemon.

### Failure → escalation

If primary acceptance fails at T+7d:

- **NOT permitted:** trying a different percentile (99th, 99.7th, 99.9th, etc.) under the same Path E framing. That is fix-N+1 on Path E and is pre-emptively forbidden by the iteration-limit below.
- **Permitted:** a fresh pre-registration with its own iteration-limit. Possibilities include (i) calibration-bug investigation finally fires (the deferred backlog ticket gets pulled), (ii) a different cutoff structure entirely (e.g., per-feature gating instead of percentile), (iii) sunset the bucket emission for the lane and ship a different alert primitive. **The choice between these is methodology and is user-owned at that future verdict point.**

### Iteration-limit (explicit, frozen)

**One Path E deploy. One 7d acceptance check. If it fails, no fix-N+1 attempt on Path E parameters.** Same iteration-limit shape that the parent Finding 8 used to escalate from EMA-fix-with-tweaks to Path E. Path E is itself the escalation; it cannot have its own iteration sub-loop without a fresh pre-registration.

---

## Pre-drafted post-deploy verification snapshots

Per the publish-then-post pattern, the verification queries are frozen here so post-deploy reads are mechanical:

```sql
-- T+24h interim check (run at 2026-05-11T~deploy_time+24h)
SELECT COUNT(*) FROM predictions
 WHERE predicted_at >= [DEPLOY_TS]
   AND predicted_at <  [DEPLOY_TS] + 86400
   AND grad_prob_bucket = 'MED';
-- Expected: ≥10 (acceptance criterion 3); triggers Case Study 01 C-iv re-arm if satisfied.

-- T+7d primary acceptance (run at 2026-05-17T~deploy_time)
SELECT COUNT(*) FROM predictions
 WHERE predicted_at >= [DEPLOY_TS]
   AND predicted_at <  [DEPLOY_TS] + 7 * 86400
   AND grad_prob_bucket = 'MED';
-- Expected: 21 ≤ count ≤ 210 (acceptance criterion 1).

-- T+7d zero-window check (acceptance criterion 2)
WITH per_day AS (
  SELECT (predicted_at / 86400) AS day_bucket, COUNT(*) AS med_count
    FROM predictions
   WHERE predicted_at >= [DEPLOY_TS]
     AND predicted_at <  [DEPLOY_TS] + 7 * 86400
     AND grad_prob_bucket = 'MED'
   GROUP BY day_bucket
)
SELECT MIN(med_count) FROM per_day;
-- Expected: > 0 (no 24h sub-window at zero).
```

`[DEPLOY_TS]` is filled in at deploy time and committed in a separate "deploy receipt" addendum to this writeup.

---

## Operational changes at deploy

- `web/bucket_cutoffs.py`: implement the Path E rebuild path. New `bucket_logic_mode = "fixed_percentile_raw_gbm"`. Existing `bimodal_cliff` and `standard_percentile` modes are retained in code (dead-code fallback) for forensic clarity but no longer engage given the new mode-selection logic.
- `web/main.py` `_acceptance_gates()`: update `summary` and add `path_e_status: "deployed"` field reflecting Path E shipped.
- `_acceptance_gates()` adds an interim-verification field surfacing T+24h MED count and T+7d MED count vs the frozen acceptance bands, so the dashboard banner can render the gate state without operator intervention.
- No changes to the predictions table schema; no changes to upstream feature pipeline; no changes to gbm_shadow scoring or isotonic calibration steps. Path E is bucket-emission-only.

---

## Receipts trail (Finding 8 chain, with Path E)

| Commit | Action |
|---|---|
| `53be35f` Finding 8 pre-registration | Diagnostic ran |
| `790c8dd` Finding 8 diagnostic — H1 confirmed | EMA fix pre-registered |
| `4d13430` Finding 8 EMA fix landed | 7d full gate clock starts |
| `70b4baf` Finding 8 — interim 48h TG re-enable gate pre-registered | Original criterion conflated EMA-verification with alert-volume |
| `f3f1f3e` Finding 8 — interim criterion amended pre-verdict | EMA-verification split from alert-volume; strictly higher bar |
| `87edcb7` Finding 8 interim verdict resolved (Variant 5B fired) | EMA-fix PASS + alert-volume FAIL; sub-branch (a vs b) decision opens |
| **(this commit) Finding 8 Path E pre-registration — fixed-percentile cutoffs on raw GBM, sub-branch (b) chosen** | Pre-registers Path E methodology, percentile, window, acceptance criterion, iteration-limit, deferred-calibration-bug ticket; commits BEFORE deploy |
| (next commit) Path E deploy receipt | Deploy timestamp, post-deploy snapshot of bucket_cutoffs state, updated verification SQL with `[DEPLOY_TS]` filled in |

---

## Cross-references

- [`bucket_calibration_aliasing.md`](bucket_calibration_aliasing.md) — Finding 8 chain (parent pre-reg, EMA fix, interim verdict)
- [`case_study_01_gmgn_comparison_prereg.md`](case_study_01_gmgn_comparison_prereg.md) § Amendment 01 — Subcondition C-iv re-arm condition that this Path E deploy can trigger
- [`../../BACKLOG.md`](../../BACKLOG.md) — calibration backlog ticket entry (opened in same commit as this pre-reg)
- Memory: `feedback_pre_registration_branches.md` — discipline rules including iteration-limit, publish-then-post, pre-verdict amendment, recursion-applies-to-discipline-itself
- Memory: `feedback_methodology_calls_user_owned.md` — the (a)-vs-(b) decision was user-owned per this rule; implementer's role was to surface trade-offs and execute, not to choose

---

## Discipline note

This pre-reg was drafted after the user explicitly invited adversarial pushback on their own (b) recommendation. The pushback identified one empirically wrong claim in the user's framing (the "no signal exists" framing for raw GBM was refuted by the distribution snapshot). The user accepted all three framing corrections without resistance. The pre-reg reflects the corrected framing throughout — calibration is "deferred not disproved," reason #3 is "tiebreaker not load-bearing," and the empirical snapshot is the load-bearing receipts artifact for the percentile choice.

The pattern: methodology calls are user-owned, but the implementer's job is to surface trade-offs honestly even when the user has already committed to a direction. The user's openness to "where do you push back?" is the discipline pattern operating recursively at the user-implementer interface.
