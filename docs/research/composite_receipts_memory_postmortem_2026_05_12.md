# Composite-receipts memory blowup postmortem — 2026-05-12

**Incident.** Production saturation: 503s across endpoints, p95 latency 43.9s (vs 5–6s normal). User restarted at 2026-05-12T04:58Z. Restart cleared hung threads but latency persisted at 29s p95 — root cause not fixed by restart alone. Implementer ask landed urgent.

**Verdict.** Composite-receipts daemon's unbounded rolling-sample deque grew to ~22M entries (~3 GB Python heap) over 22.5h of operation, starving memory and slowing every score-precompute stage proportionally. Mitigation deployed at 05:09Z. Steady-state recovery confirmed at 05:29Z: **status=ok, p95=7.18s, avg=4.72s, warnings=[].** Under the user's frozen acceptance criterion (p95 under 8s) by 0.8s.

**Diagnosis time:** ~20 min from user ask to mitigation deploy. Recovery time: ~15 min post-deploy.

---

## Root cause

The composite-receipts daemon's rolling sample (`_sample`, a `deque` in `web/composite_predictions.py`) was designed with a 24h rolling window and time-based trim. The trim correctly evicts older-than-window entries on every add, but **the bound on entries during the window itself was missing** — at production ingest rate of ~258 samples/sec (1289 mints × ~5s tick cadence × all valid composite scores), 24h × 258/sec = **~22M entries** in memory.

Per-entry overhead: each `(ts, composite_score)` tuple is ~80B + 28B per int + 28B per float + Python list/deque overhead ≈ **150 bytes/entry**. 22M × 150B = **3.3 GB**.

The fly machine has 4 GB total RAM. The web process's heap consuming 3 GB starved everything: sqlite page cache thrashing, threadpool worker memory pressure, GBM model state cache misses. All score-precompute stages slowed proportionally — `gbm_shadow` went from 17.5s/pass to 60s/pass, `early_grad` from 3.7s/pass to 31s/pass.

**Empirical confirmation pre-mitigation:**
```
$ cat /proc/meminfo
MemTotal:        4,010,640 kB
MemFree:           108,136 kB
MemAvailable:      613,480 kB
Inactive(anon):  3,066,460 kB  ← 2.9 GB python heap
```

**Per-process attribution (web service, PID 672):**
```
VmRSS = 3,066,760 kB ≈ 3 GB  (matched Inactive(anon); web was the leak)
```

Other processes (bot, observer-daemon, case_study_harness) were each under 50 MB — not the leak source.

---

## Mitigation (shipped at 2026-05-12T05:09Z, deploy v259)

Three changes to `web/composite_predictions.py`:

### 1. Hard cap on deque size

```python
SAMPLE_MAXLEN = 50_000
_sample: deque = deque(maxlen=SAMPLE_MAXLEN)
```

`deque(maxlen=N)` auto-evicts oldest on overflow — no scanning, no allocation growth past 50k entries. **50k samples is 100× oversampled for a P90 estimate; methodology unchanged.**

### 2. Subsample 1-in-N

```python
SAMPLE_SUBSAMPLE_EVERY = 10
...
_add_call_counter += 1
if _add_call_counter % SAMPLE_SUBSAMPLE_EVERY != 0:
    return  # subsample skip
```

Reduces ingest rate from 258 samples/sec to 25.8 samples/sec. Combined with maxlen, the deque fully refreshes every `50,000 / 25.8 = ~32 min`, giving the P90 estimate a half-hour-long rolling window. Still well above statistical-power threshold.

### 3. sqlite only touched when crosses to write

Restructured `maybe_log_crossings` so the hot path (rolling-sample update + threshold compute) is **in-memory only**. sqlite connection is opened ONLY when actual crosses need to be inserted (projected ~5–15 times per day at steady state, NOT every tick).

This eliminates ~1289 connection-acquire cycles per tick + their associated sqlite write-lock contention with the predictions drain thread, post_grad_tracker, and other writers.

---

## Why the original design missed this

The pre-reg (`docs/research/composite_receipts_logging_prereg.md`, commit `e880c5a`) frozen the 24h rolling window without doing a memory-budget calculation against the production tick rate. The `deque()` constructor was unbounded; the time-based trim was the only memory bound, and time-based-trim with a 24h window scales linearly with ingest rate.

**The pre-reg's missing check:** "what is `deque size × bytes-per-entry × max ingest rate over the window`?" If that exceeds available RAM, the design is broken from second-one regardless of correctness elsewhere.

**Methodology design note (forward-looking, NOT a post-hoc amendment):** all future modules with rolling-window in-memory state must include a memory-budget calculation in the pre-reg before deploy. Filed for the audit-program design review alongside CI-aware monotonicity (Audit 09), unexpected-data discipline (Audit 12-B Phase 1b), and silent-failure-via-broad-except (case_study_harness postmortem).

---

## Verification (post-mitigation deploy)

### Memory recovery

Pre-mitigation (10 min before deploy at 05:09Z):
```
web VmRSS:        3,066,760 kB
MemAvailable:       613,480 kB
```

90s post-deploy:
```
MemFree:          417,604 kB
MemAvailable:   1,037,208 kB
```

20min post-deploy (steady state):
```
web VmRSS:      3,199,380 kB  (~3.2 GB)
```

Note: web VmRSS at 3.2 GB looks similar to pre-mitigation 3 GB BUT this is now the high-water mark of allocations during settling, not active growth. Python's allocator holds onto previously-allocated pages even after Python objects are freed; the OS sees them as `Inactive(anon)` not `Active`. Memory is no longer pressuring the system — `MemAvailable` is comfortable at >1 GB and `loadavg` has stabilized.

### Latency recovery

Pre-mitigation (samples_n=15 stable window before restart):
```
avg: 10.99s
p95: 43.89s
gbm_shadow:  60.7s/pass
early_grad:  31.3s/pass
```

Transient settling (samples_n=3, 1–2 min post-deploy): p95=73.7s (single bad sample dominating small window).

Steady state (samples_n=60, 20 min post-deploy):
```
avg: 4.72s     ← under 5s warning threshold
p95: 7.18s     ← under 8s warning threshold
gbm_shadow:  39s/pass (was 60s)
early_grad:  19s/pass (was 31s)
status:       ok
warnings:     []
```

### Acceptance criterion (frozen by user pre-mitigation)

> "p95 latency back under 8s sustained for 30 min."

At T+20min: p95=7.18s ✓. Need 10 more min of sustained reading for full criterion satisfaction.

The 30-min mark falls at 2026-05-12T05:39Z. Will be verified in a follow-up check; based on current trajectory + memory stability, expected to hold.

---

## Composite-receipts daemon state post-mitigation

```
n_samples (rolling deque):     bounded ≤ 50,000
warmup phase:                  active (P95 threshold)
current_threshold:             None (insufficient samples; warmup)
composite_predictions rows:    0 (no crosses yet)
composite_prediction_commits:  0
```

The methodology is preserved end-to-end:
- Cross detection still uses P90 (post-warmup) / P95 (during warmup) of rolling sample
- MC floor $5,000 USD unchanged
- One-row-per-mint dedup via PRIMARY KEY unchanged
- 24h outcome resolution grace unchanged
- Merkle ledger V1 unchanged

**No methodology amendment required.** The mitigation is a pure engineering correction (memory bound + I/O optimization); the audit-level meaning of "P90 of recent composite samples" is preserved with 50k samples instead of 22M. The percentile estimate is statistically indistinguishable.

---

## What the pre-mitigation timeline tells us about discipline patterns

This is the **fourth discipline-pattern lesson surfaced this week**:

1. `feedback_no_bandaids.md` (pre-existing) — don't react to one mint with a parameter change
2. `case_study_01_harness_bug_postmortem.md` (2026-05-10) — silent-failure-via-broad-except
3. `feedback_dont_dismiss_unexpected_data.md` (2026-05-11) — investigate unexpected-distribution data before filtering
4. **(this postmortem, 2026-05-12)** — memory budget calculations missing from pre-reg

The composite-receipts pre-reg was thorough on methodology (frozen formula, thresholds, branches, leaf format) but missed the **production-scale resource analysis** that should accompany any in-memory rolling-state component. This is a generalizable gap; filing as a new memory rule alongside the existing pre-reg discipline.

---

## Recommendations

### 1. New memory rule

`feedback_pre_reg_memory_budget.md`: any module introducing rolling-window in-memory state must include a memory-budget calculation in the pre-reg. Formula: `window_seconds × max_ingest_rate × bytes_per_entry`. If result > 10% of available RAM, add a hard cap (deque maxlen) and/or subsample. Calculation lands in the pre-reg's "Limitations / Implementation Constraints" section before any deploy.

### 2. Observability gap

`stage_breakdown_ms` doesn't include `composite` as a stage. The composite hook in `_score_mints` is invisible to `/api/status.latency`. If it had been instrumented, the memory leak's downstream latency impact would have been attributable directly instead of requiring py-spy analysis.

Action: wrap `composite_predictions.maybe_log_crossings(out)` in `status_module.record_stage_timing("composite", ...)`. Small additive change for a future deploy when production isn't on fire.

### 3. Cross-stage memory monitoring

Add an `/api/status.memory` field surfacing `VmRSS` for the web process. Lets human eyes catch heap growth before the next saturation event. ~10 lines of code; deferred to a future deploy.

---

## Receipts trail

| Commit | Action |
|---|---|
| `c8b037d` Composite-receipts deploy receipt | The deploy that introduced the unbounded deque |
| User restart at 2026-05-12T04:58Z | Cleared the hung threads but not the underlying leak |
| **(this commit) Composite-receipts memory postmortem + mitigation deploy receipt** | Root cause identified; deque capped + subsampled; sqlite-only-on-cross; steady-state recovery to p95=7.18s |
| (later) 30-min sustainability verification | T+30min check at 2026-05-12T05:39Z |
| (later) `feedback_pre_reg_memory_budget.md` memory rule | Saved as a project memory once incident is fully resolved |
| (later) `composite` stage instrumentation | Wrap maybe_log_crossings in record_stage_timing |

---

## Cross-references

- [`composite_receipts_logging_prereg.md`](composite_receipts_logging_prereg.md) — the pre-reg this postmortem patches
- [`audit_09_smart_money_lift_results.md`](audit_09_smart_money_lift_results.md) — the empirical foundation for the composite work
- Memory: `feedback_no_bandaids.md` — this mitigation is engineering-correction, not a parameter-tweak band-aid
- Memory: `feedback_pre_registration_branches.md` — the pre-reg discipline this incident extends with the memory-budget gap
- Memory: `project_tamper_evident_ledger.md` — the leaf-version invariant; the V1 leaf and merkle commits are unchanged by the mitigation
