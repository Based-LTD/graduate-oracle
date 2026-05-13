# Late-import double-load postmortem — duplicate daemon threads → latency drift

**Incident.** Post-jemalloc latency p95 climbed back from ~4.7-7.2s baseline to 13.4-17.6s over 5h uptime. Initially suspected as memory regression or sqlite contention; both ruled out (VmRSS stable + dropping, sqlite p95 89μs/lookup).

**Verdict.** Five daemon threads were running TWICE in production due to a Python late-import gotcha: `paper_trade.py` and `api_v1.py` do `from main import ...` inside hot-path functions. The running script is `python -u /app/web/main.py` (loaded as `__main__`), so `from main import X` triggers a SEPARATE module load — re-executing every top-level `start()` call. Daemons WITH idempotency guards (observer_health, ledger, composite_predictions, tg_fires) survived this; daemons WITHOUT guards (paper-trade, calibration, sol-pay-watcher, predictions-resolve, memory-watchdog) spawned a SECOND worker thread each.

The smoking gun: both paper-trade threads were captured running `sklearn.predict_proba` on the GBM hot path in a 30s py-spy sample. Literal 2× GBM scoring load, GIL-contended. Magnitude scaled with mint count growth — explaining why the duplication only manifested as visible latency drift recently (the duplication itself predates jemalloc).

**Mitigation deployed:** 2026-05-13T~04:55Z. Single commit:
1. `sys.modules` sentinel at top of `main.py` — primary fix
2. `_started` + `_start_lock` idempotency guards added to 5 daemon spawn sites — backstop

**Verification post-deploy:**
- py-spy thread dump on new web PID 673: every named daemon at EXACTLY 1 thread (pre-fix: 5 daemons at 2 each)
- GBM `scored_ok=635` at 3min uptime, `errors=0` — no regression
- Latency stat warming up; will stabilize over next 10-15 min as cache populates

---

## The bug

`web/paper_trade.py:498` (inside paper-trade daemon's warmup loop):

```python
def _loop():
    print(f"[paper_trade] daemon started · ...", flush=True)
    while True:
        try:
            from main import INDEX                              # ← here
            if INDEX is not None and INDEX.n_curves_indexed > 0:
                break
        except Exception:
            pass
        time.sleep(2)
```

Sequence of events on startup:
1. Process boots: `python -u /app/web/main.py` runs the script
2. `__main__` module is loaded; top-level code executes once
3. Line 207: `paper_trade.start()` — spawns paper-trade daemon thread
4. Main thread continues past line 207, finishing main.py load
5. Paper-trade daemon thread starts its `_loop()`, immediately calls `from main import INDEX`
6. Python checks `sys.modules` — sees `__main__` but NOT `main`
7. Python loads `main.py` AGAIN as a separate module named `main`
8. The SECOND load re-runs all top-level code from scratch
9. Daemons WITHOUT `_started` guards spawn a second thread each

Same pattern for `api_v1.py:52,86,121,155` — though those are triggered on the first inbound API request rather than at startup.

### Affected daemons (pre-fix)

py-spy dump on web PID 672 (5h uptime):

| Daemon | Count | Has guard? |
|---|---|---|
| paper-trade | **2** | No |
| calibration | **2** | No |
| sol-pay-watcher | **2** | No |
| predictions-resolve | **2** | No |
| memory-watchdog | **2** | No |
| observer-health | 1 | yes (`_started`) |
| ledger-commit | 1 | yes (`_daemon_started`) |
| composite_predictions | 1 | yes (`_daemon_started`) |
| tg-fires | 1 | yes (`_started`) |
| all pre-line-199 daemons (mint-checkpoints, post-grad, early-grad, dex-paid, fee-delegation, wallet-balance, metadata, cluster-intel) | 1 | yes (internal) |

The selective duplication conclusively proves the late-import re-execution: every daemon with an internal idempotency guard survived; every daemon without one duplicated. Not a sampling artifact, not a race — a structural bug.

### Why latency drifted post-jemalloc

The duplication has been in place as long as the late-import pattern (cannot date precisely — `pump-jito-sniper` source repo has no commits to git-blame against). What changed post-jemalloc deploy: live mint count grew, GBM model size grew. Doubled `predict_proba` calls per tick scaled linearly with mint count; the underlying duplication only became visible-as-latency once per-mint scoring volume crossed a threshold.

The post-jemalloc memory pressure root cause (C-extension fragmentation) and this latency root cause are **TWO DISTINCT BUGS WITH OVERLAPPING SYMPTOMS**. jemalloc fixed memory; this commit fixes latency.

---

## The fix

### Primary: sys.modules sentinel

`web/main.py` near the top of the file (after stdlib imports, before any module imports that could trigger late-import callbacks):

```python
import sys
# ...
# Sentinel: this file runs as __main__ but paper_trade.py + api_v1.py do
# late `from main import X` inside hot-path functions. Without this alias,
# Python would load main.py a SECOND time (as the `main` module), re-
# executing every top-level start() and spawning duplicate daemon threads.
sys.modules["main"] = sys.modules[__name__]
```

This makes `from main import X` resolve to the SAME module object as `__main__`. The Python import machinery finds `main` already in `sys.modules`, skips the load entirely, and returns the existing module — no re-execution.

### Backstop: idempotency guards

Added `_started` + `_start_lock` to the 5 spawn sites missing them, mirroring the pattern already in observer_health/ledger/composite_predictions/tg_fires. Pattern:

```python
_started = False
_start_lock = threading.Lock()

def start(...):
    """... Idempotent."""
    global _started
    if _started:
        return
    with _start_lock:
        if _started:
            return
        _started = True
    # spawn thread
```

Defense-in-depth: even if a future refactor breaks the sentinel (e.g., switching from `python main.py` to `python -m web.main`, which changes `__name__`), no daemon will double-spawn.

Sites updated:
- `web/paper_trade.py:start()`
- `web/calibration.py:start()`
- `web/sol_pay.py:start_watcher_thread()`
- `web/predictions.py:start()` (predictions-resolve only — predictions-drain already had `_drain_thread_started`, calibration-curves already idempotent)
- `web/main.py` — wrapped memory-watchdog spawn in `_start_memory_watchdog()` with guard

---

## Verification

### Thread count (immediate, conclusive)

`py-spy dump --pid 673` post-deploy. Every named daemon at exactly 1:

```
1 "paper-trade"           (was 2)
1 "calibration"           (was 2)
1 "sol-pay-watcher"       (was 2)
1 "predictions-resolve"   (was 2)
1 "memory-watchdog"       (was 2)
1 each for all 30+ other daemons (unchanged)
```

### GBM scored_ok (no regression)

3-min uptime: `scored_ok=635, errors=0` — model loaded and scoring cleanly.

### Latency (steady-state pending)

Cold start sample at 3min: p95=40s (index load + first batch). Will stabilize over 10-15 min. Acceptance criterion: p95 trends toward post-jemalloc baseline of ~6-8s. If it stabilizes above 10s, a separate growth path is in play.

---

## Two distinct issues, overlapping symptoms

| Issue | Root cause | Fix | Status |
|---|---|---|---|
| Memory pressure | C-extension allocator fragmentation (numpy/sklearn) | jemalloc + PYTHONMALLOC=malloc | Deployed 2026-05-12, verified PASS (VmRSS dropping) |
| Latency drift | Duplicate GBM scoring from late-import double-load | sys.modules sentinel + 5 daemon guards | Deployed 2026-05-13 (this commit) |

Both surfaced as production warning-state symptoms over the past 48h. Both now identified, both shipped with independent root-cause fixes.

---

## Discipline-pattern lesson (sixth this week)

Filing a new memory rule alongside this postmortem:

> **Python late-imports of `__main__`-loaded scripts re-execute the module unless `sys.modules` is sentineled first. Required pattern for any `web/main.py`-style entry point that has secondary imports.**

Sister to the silent-failure-via-broad-except family from earlier in the week. Same shape: a structural Python behavior that creates invisible duplication/swallowing in long-lived production services, only visible via direct diagnostic inspection (py-spy, broad-except review). Discoverable by py-spy / module-state introspection, NOT by /api/status or log tailing.

The lessons-this-week count:
1. `feedback_no_bandaids.md` (pre-existing) — don't react to one mint with a parameter change
2. `case_study_01_harness_bug_postmortem.md` (2026-05-10) — silent-failure-via-broad-except: surface from real production
3. `feedback_dont_dismiss_unexpected_data.md` (2026-05-11) — investigate unexpected-distribution data before filtering
4. `feedback_pre_reg_memory_budget.md` v1+v2 (2026-05-12) — memory budget + C-extension allocator churn
5. `feedback_production_shape_smoke_testing.md` (2026-05-13) — pre-deploy tests on actual production data shapes
6. **`feedback_main_module_sentinel.md` (2026-05-13, this postmortem)** — `__main__` vs `main` late-import double-load

All six share a common root: **production behavior diverges from what the code-as-written appears to do, and the divergence is invisible without targeted diagnostic instrumentation**. Six lessons in eight days. Filing the pattern as a meta-rule candidate for the audit-program review.

---

## Composite-receipts table — separate issue, queued

Independently: `composite_predictions` table reported 0 rows again post-MC-dict-fix despite `_daemon_started` guard preventing duplicate-daemon root cause. Different failure mode. Queued for diagnosis after this fix verifies clean.

---

## Receipts trail

| Commit | Action |
|---|---|
| `1d6009e` Composite-predictions MC-dict postmortem + fix | 24h composite silent-failure resolved |
| **(this commit) Late-import double-load fix** | 5 daemons de-duplicated; latency drift root-caused |
| (next) Composite-predictions 0-rows-again investigation | Separate failure mode, queued |
| (next) Latency p95 steady-state verification | Acceptance: ≤8s |

---

## Cross-references

- Memory: `feedback_pre_reg_memory_budget.md` — sister rule (production-divergence class)
- Memory: `feedback_broad_except_silent_failure.md` — sister rule (silent failure class)
- Memory: `feedback_production_shape_smoke_testing.md` — sister rule (production-divergence class)
- Memory: `feedback_main_module_sentinel.md` — new rule from this postmortem
- `web_service_memory_pressure_postmortem_2026_05_12.md` — independent latency contributor (memory side)
- `track_1_2_deploy_receipt_2026_05_12.md` — jemalloc verification (memory side)
- `composite_predictions_mc_dict_postmortem_2026_05_13.md` — separate same-day fix
