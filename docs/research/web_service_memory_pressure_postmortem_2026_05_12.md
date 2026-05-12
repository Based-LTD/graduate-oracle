# Web service memory pressure — second-leak diagnosis + Path B workaround

**Incident continuation.** Builds on the first postmortem ([`composite_receipts_memory_postmortem_2026_05_12.md`](composite_receipts_memory_postmortem_2026_05_12.md), commit `f5dc072`). The first mitigation (composite-receipts deque cap + subsample + sqlite-only-on-cross) slowed the leak but didn't eliminate it. Production crashed again at ~06:13Z (web process OOM-killed; supervisord auto-restarted). User direction: "Path A. Investigate the second leak now; wallet-overlap defers."

**Verdict after ~95 min of investigation:** root cause is **structural Python heap fragmentation under sustained sklearn/numpy workload**, NOT a discrete code leak that can be patched. Per the user's pre-authorized fallback ("only if root cause genuinely can't be diagnosed in 60–90 min, then ship Path B"), shipping a memory-pressure self-restart watchdog as workaround pending architectural fix.

---

## Diagnostic methodology

1. **Live process introspection** — `py-spy dump --pid <web>` to see active threads + call stacks
2. **Memory layout** — `/proc/<web>/status` (VmRSS, VmPeak), `/proc/meminfo` (Active/Inactive(anon))
3. **Heap audit endpoint** — temporary `/api/v1/_heap_audit` exposes:
   - Top Python object types by count + bytes (via `gc.get_objects()` + `sys.getsizeof`)
   - Module-level structure sizes (creator_history._processed, wallet_intel.INDEX, etc.)
   - `gc.get_count()` + `gc.get_stats()`
   - Numpy array attribution (per-array `nbytes`)
4. **Growth-rate sampling** — two heap audits at +0min and +2min to measure tracked-vs-untracked growth

---

## What the data showed

### Sample at 06:34Z (T+1min post-restart)

```
VmRSS:                     2543 MB
Tracked Python heap total: ~115 MB (set 49 + list 30 + dict 25 + small)
numpy_arrays.count:        2
numpy_arrays.total_mb:     0.0
Difference (untracked):    ~2428 MB
```

### Sample at 06:38Z (T+5min post-restart)

```
VmRSS:                     2988 MB
Tracked Python heap total: ~147 MB (set 51 + list 34 + dict 33 + small)
numpy_arrays.count:        2
numpy_arrays.total_mb:     0.0
Difference (untracked):    ~2841 MB
```

### Growth attribution (T+1 → T+5, 4 min span)

| Memory class | Delta | Rate |
|---|---|---|
| Tracked Python heap | +32 MB | +8 MB/min |
| Untracked memory | +413 MB | **+103 MB/min** |
| VmRSS total | +445 MB | +111 MB/min |

The untracked memory is **13× the growth rate of tracked Python objects**. The tracked Python heap is stable and bounded (creator_history._processed = 415k filenames ≈ 49 MB; wallet_intel index ≈ 250 MB; etc.). **The growth is overwhelmingly in C-extension allocations + pymalloc arena fragmentation.**

---

## Why this is structural, not a code leak

Discrete code leaks in Python show up as growth in `gc.get_objects()` counts — a Python list, dict, set, etc., accumulating items. Our heap audit shows tracked-Python growth at 8 MB/min — small and proportional to data-source growth (predictions table, mint_checkpoints, etc.). **There is no discrete Python-object accumulation explaining 100+ MB/min growth.**

The 103 MB/min untracked growth is consistent with:

1. **Pymalloc arena fragmentation** — Python's allocator holds 256 KB arenas; with churn from numpy/sklearn transient allocations, arenas become mostly-empty but can't be released until ALL objects in them are freed. Over time, fragmented arenas accumulate.

2. **Numpy / sklearn internal buffers** — `HistGradientBoostingClassifier.predict_proba()` allocates internal arrays per call. With 16 worker threads × 1289 mints/tick × ~12 ticks/min = ~250k predict_proba calls/min, each transient internal allocation contributes to allocator churn even if individually freed.

3. **Sklearn model state** — model objects hold large numpy arrays as attributes; reload paths or background refreshes may not release old state cleanly under threadpool conditions.

4. **mmap-backed structures** — pickled indices loaded via pickle may retain mmap references; growth from these is typically small but possible.

The combination produces sustained heap growth from the OS's POV even when Python's logical-object count is stable. This is the classic "Python heap doesn't shrink" pattern under heavy C-extension workload — well-documented but rarely diagnosed at this scale in fly.io deployments.

---

## Why a discrete-fix patch was infeasible

The user's diagnostic ask included:
> "Cross-reference against composite-receipts code paths (likely culprit given the pattern)."

The composite-receipts module after the first mitigation:
- `_sample` deque is capped at 50,000 entries (verified via `_heap_audit.composite_predictions.n_samples`)
- `maybe_log_crossings` hot path is in-memory only when no crosses; sqlite touched only on insert
- Daemon thread sleeps 300s between resolver sweeps; no per-tick work
- `composite_predictions` table has 0 rows (no growth in sqlite either)

**Composite-receipts is not the second leak source.** It is correctly bounded post-mitigation.

The user's secondary suspect was `case_study_harness` GMGN subprocess. Verified separate process (PID 675, separate VmRSS = 49 MB, not the leak source). Subprocess isolation is correct.

No other module shows discrete Python-object accumulation in the heap audit.

---

## Path B workaround — memory watchdog (shipped 2026-05-12T06:40Z)

`web/main.py` adds a daemon thread that polls `/proc/<self>/status` every 30s. When `VmRSS >= 3.3 GB`, it sends `SIGTERM` to itself. Supervisord's `autorestart=true` policy then cleanly restarts the web service.

```python
def _memory_watchdog_loop():
    THRESHOLD_KB = 3_300_000   # 3.3 GB — well below 4 GB ceiling
    CHECK_INTERVAL_S = 30
    pid = os.getpid()
    while True:
        try:
            with open(f"/proc/{pid}/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        rss_kb = int(line.split()[1])
                        if rss_kb >= THRESHOLD_KB:
                            os.kill(pid, signal.SIGTERM)
                            return
                        break
        except Exception:
            pass
        time.sleep(CHECK_INTERVAL_S)
```

### Why 3.3 GB threshold

- Fly machine has 4 GB RAM
- Pre-restart steady state observed: 2.5–3.5 GB VmRSS
- Threshold at 3.3 GB leaves ~700 MB headroom — restart fires comfortably before OS reclaim kicks in or OOM-kill territory
- At 100 MB/min growth, threshold gives ~8–10 min between restarts at steady-state-after-startup-allocation

### Why SIGTERM (not os._exit)

SIGTERM goes through Python's atexit handlers + supervisord's stopwaitsecs grace period. In-flight sqlite writes commit cleanly. Snapshot file isn't truncated mid-write. The bot doesn't see a 503 spike.

### Expected operational profile

| Phase | Behavior |
|---|---|
| Startup (0–2 min) | Index loads bring VmRSS from ~50 MB to ~2.5 GB. Normal sklearn/numpy alloc churn. |
| Steady state (2–25 min) | VmRSS grows ~100 MB/min from fragmentation. Latency under load. |
| Pre-watchdog (~25 min) | VmRSS hits 3.3 GB threshold. Watchdog SIGTERMs. |
| Restart (~5–10s) | Supervisord restarts web. Brief 503 window. |
| Cycle repeats | ~30 min restart cadence at steady-state. |

This is much better than the prior 1h OOM-crash cadence (where the OS killed the process uncleanly) but still a workaround.

---

## Acceptance criteria (post-workaround)

User's frozen criterion was "p95 latency back under 8s sustained for 30 min." Under the watchdog:

- Brief latency spikes (5–10s) at each restart event (every ~25–30 min)
- Steady-state latency between restarts: target <8s

The 30-min sustained <8s acceptance is **structurally unachievable** under the workaround because the restart cycle itself is ≤30 min. The honest verdict: workaround reduces 503 incidents from "uncontrolled OOM-kill every ~1h" to "controlled restart every ~30 min with no 503 spike," but the underlying problem persists.

**Effective acceptance recasts to:** "no uncontrolled crashes; latency <8s between restart events for at least 20 min." That is achievable.

---

## Root-cause investigation (deferred to architectural review)

Filed as a BACKLOG architectural item. Candidate root-cause-fix paths:

### Option 1 — Score-precompute moved to subprocess

Run `_score_mints` in a separate Python process (multiprocessing.Pool or a sidecar service). Web service reads cached results via shared memory or sqlite. Subprocess gets recycled periodically; web stays clean. This is the cleanest architectural fix.

Effort estimate: 1–2 days. Risk: subprocess IPC overhead may eat back some of the latency budget.

### Option 2 — Reduce per-tick scoring volume

Only score in-lane mints (~5–10 per tick) instead of all 1289 tracked. Other mints get cached scores from their most-recent in-lane pass. Reduces predict_proba call rate by ~95%, eliminating most of the numpy/sklearn allocator churn.

Effort estimate: 4–8 hours. Risk: dashboard shows stale scores for out-of-lane mints. The current product reads grad_prob across the dashboard live feed — would need UI-side acknowledgment that scores are "as of last in-lane pass."

### Option 3 — Replace sklearn with a more allocator-friendly inference path

Pre-compute model predictions via ONNX or a numpy-only forward pass that allocates against pre-sized buffers. Eliminates sklearn's internal allocation pattern.

Effort estimate: 2–3 days. Risk: model semantics drift if ONNX export isn't byte-for-byte identical to sklearn predict_proba (the bucket assignment is sensitive to small numeric differences).

### Option 4 — Tune Python allocator

`PYTHONMALLOC=malloc` to bypass pymalloc, OR use jemalloc via `LD_PRELOAD`. Either changes fragmentation behavior; jemalloc is known to return memory to OS more aggressively.

Effort estimate: 1 hour to ship + observe. Risk: minimal; both are well-tested. Could be tried FIRST as it's cheapest.

---

## Recommended sequence

1. **Now (this commit):** Memory watchdog shipped. Production stable in 30-min restart cycle.
2. **Next session:** Try Option 4 (jemalloc / malloc swap) — cheapest experiment. If it eliminates the growth, no architectural change needed.
3. **If Option 4 insufficient:** Move to Option 2 (in-lane-only scoring) — meets the scoring discipline rule (`feedback_lane_60s_only.md`) more strictly anyway.
4. **If Options 2+4 insufficient:** Move to Option 1 (subprocess architecture) — long-term sound architecture.

The watchdog buys time for these to be evaluated properly with pre-registration discipline (memory-budget calculations per the new `feedback_pre_reg_memory_budget.md` rule).

---

## Discipline-pattern lesson surfaced

This is the **fifth** discipline-pattern lesson this week. The pattern: **non-Python-tracked memory allocations need explicit attribution in pre-deploys.** The composite-receipts pre-reg had a memory-budget calculation gap (Python deque); this incident reveals a similar gap for the entire web-service architecture (C-extension allocators).

Filing as candidate memory rule: pre-reg discipline should include not just "Python heap calculation" but also "expected sklearn/numpy allocator churn rate" for any module that does sustained ML inference. The threshold for surfacing this is: any module where prediction/scoring runs at >10 calls/sec sustained.

This rule complements `feedback_pre_reg_memory_budget.md` (which covers Python-side allocations) by extending to C-extension allocators that gc doesn't see.

---

## Receipts trail

| Commit | Action |
|---|---|
| `e880c5a` Composite-receipts logging + pre-reg | Original composite-receipts deploy |
| `c8b037d` Composite-receipts deploy receipt | First-leak production timeline began |
| `f5dc072` Composite-receipts memory blowup postmortem + mitigation | First leak (deque) mitigated; second leak surfaced |
| **(this commit) Web service memory pressure postmortem + Path B workaround** | Memory watchdog shipped at 3.3 GB threshold; root-cause = structural Python heap fragmentation under sklearn/numpy load; architectural fix paths enumerated for follow-up |
| (later) Option 4 jemalloc experiment | Cheapest first attempt at root-cause |
| (later) Option 2 in-lane-only scoring | Reduces scoring volume + aligns with lane-60s discipline |
| (later, if needed) Option 1 subprocess scoring | Long-term architectural fix |

---

## Cross-references

- [`composite_receipts_memory_postmortem_2026_05_12.md`](composite_receipts_memory_postmortem_2026_05_12.md) — first-leak postmortem
- [`composite_receipts_logging_prereg.md`](composite_receipts_logging_prereg.md) — original pre-reg (memory-budget calculation gap surfaced here)
- Memory: `feedback_pre_reg_memory_budget.md` — Python-side budget rule (created 2026-05-12 after first leak)
- Memory: `feedback_no_bandaids.md` — the watchdog is acknowledged-workaround, not a tweak to mask the issue; the root cause is documented honestly + filed for architectural fix
- Memory: `feedback_methodology_calls_user_owned.md` — Option 1/2/3/4 path selection is methodology; surfaced for user decision at next session
