# Composite-predictions silent-failure postmortem — `market_cap` dict vs scalar

**Incident.** Composite-receipts logging table had 0 rows after 24h+ of production operation, despite the daemon running cleanly with 7615 accumulated rolling-sample composite scores. User flagged via direct observation: three high-composite mints they personally noticed today (2hi6Cn graduator, 9KjPt prediction, 635owmt graduator) — none captured in `composite_predictions`.

**Verdict.** Cross-detection logic dereferenced `m.get("market_cap")` as if it were a scalar (`float(mc) < MC_FLOOR_USD`), but the live snapshot returns it as a DICT (`{"sol": 28.3, "usd": 2695, "sol_usd": 95.18}`). `float({...})` raises `TypeError`. Broad-except in the score-precompute hook silently swallowed the exception every tick a high-composite mint appeared.

**Mitigation deployed:** 2026-05-13T01:56:33Z. Cross-detection extracts the `usd` value from the dict, with isinstance guards + per-mint try/except so future schema drift can't take out the whole batch.

**Verification at +4 min post-deploy:** 7 composite_predictions rows captured, including mints with composite=8.55/MC=$6,965, composite=9.25/MC=$13,186, composite=6.94/MC=$16,716. Cross-detection working end-to-end. Daemon rolling-sample at 141 samples (post-restart accumulation).

---

## The bug

`web/composite_predictions.py:282-291` (pre-fix):

```python
mc = m.get("market_cap")
if mc is None or float(mc) < MC_FLOOR_USD:
    continue
```

When `mc` is a dict (the actual production shape), `float({...})` raises `TypeError: float() argument must be a string or a real number, not 'dict'`.

The exception propagated UP to `_score_mints` in `web/main.py` where:

```python
try:
    composite_predictions.maybe_log_crossings(out)
except Exception as e:
    print(f"[composite] cross-log error: {e}", flush=True)
```

The broad-except in the CALLER swallowed the TypeError. Each tick a high-composite mint hit the MC check, the exception fired, the whole tick's cross-batch was lost, and the error was logged to fly logs (which retain ~2 minutes — invisible to anyone not actively tailing).

### Why the rolling sample was populated despite the bug

The sample-update happens BEFORE the cross-detection loop:

```python
for m in enriched_mints:
    cs = _compute_composite(m)
    if cs is None:
        continue
    _add_sample(now, cs)               # ← sample updated here, before MC check
    composites.append((m.get("mint"), cs, m))
threshold = _current_threshold(now)
...
for mint, cs, m in composites:
    ...
    mc = m.get("market_cap")
    if mc is None or float(mc) < MC_FLOOR_USD:  # ← bug here
        continue
```

Samples were being added correctly. The threshold was being computed correctly. Only the cross-INSERT path was raising. So daemon snapshot showed "warmup active, 7615 samples" — apparently healthy — while no crosses ever landed.

### Why this didn't surface during smoke testing

The pre-deploy smoke test (commit `e880c5a` deploy receipt) used SYNTHETIC test mints with `market_cap` as a scalar `int`:

```python
test_mints = [
    {'mint': 'AAA', 'smart_money_in': 5, 'max_mult': 2.0, 'age_s': 60, 'market_cap': 10000},
    ...
]
```

The synthetic data shape didn't match production's dict shape. **The schema-mismatch was invisible to the smoke test because the smoke test built its own input.** A production-shape smoke test (parsing `/api/live` response and feeding through `maybe_log_crossings`) would have caught this.

---

## Third instance of silent-failure-via-broad-except this week

This is the **third instance** of the same pattern surfacing as a production-data bug:

| # | Date | Module | Failure |
|---|---|---|---|
| 1 | 2026-05-10 | `case_study_harness/sources/grad_oracle.py` | `SELECT feature_unique_buyers` against a `mint_checkpoints` schema that doesn't have that column → `OperationalError` → broad-except in `collection_loop` → silent 0 observations for 25h |
| 2 | 2026-05-11 | Audit 12-B Phase 1b methodology | Pre-reg assumed `age_bucket ∈ {15, 30, 60, 75}`; actual production data had ages up to 1500s (out-of-lane); investigator-side filtering would have silently dismissed real signal |
| 3 | **2026-05-13** | **`web/composite_predictions.py`** | **`float(market_cap)` against a dict-shaped field → `TypeError` → broad-except in `_score_mints` → silent 0 crosses for 24h+** |

All three follow the same recursion: **production data shape differs from the implementation's assumption; the divergence raises an exception; a broad-except in the calling code catches it; the failure is invisible because it doesn't surface to /api/status, doesn't alert TG, doesn't persist to a log file durably**.

The discipline rule that should catch this: **production-shape smoke testing**. Before any deploy that ingests live-snapshot fields, the smoke test should parse `/api/live` (or the production data source) and feed those exact dicts through the new code path. Synthetic test data is necessary but NOT sufficient.

Filing as a refinement to the existing memory rule on silent-failure-via-broad-except (which the postmortem on `case_study_01_harness_bug_postmortem.md` and this writeup both anchor).

### Discipline-pattern lesson (sixth this week)

The first five lessons were:
1. `feedback_no_bandaids.md` (pre-existing) — don't react to one mint with a parameter change
2. `case_study_01_harness_bug_postmortem.md` (2026-05-10) — silent-failure-via-broad-except: surface from real production
3. `feedback_dont_dismiss_unexpected_data.md` (2026-05-11) — investigate unexpected-distribution data before filtering
4. `feedback_pre_reg_memory_budget.md` (2026-05-12 v1) — memory budget calculation for Python rolling-window state
5. `feedback_pre_reg_memory_budget.md` (2026-05-12 v2) — extension for C-extension allocator churn

The sixth (this postmortem): **production-shape smoke testing**. Pre-deploy verification that exercises the new code path against ACTUAL live-snapshot dicts, not synthetic constructions.

Filing this as a new memory entry: `feedback_production_shape_smoke_testing.md` (drafted alongside this commit).

---

## The fix

`web/composite_predictions.py:282-301` (post-fix):

```python
crosses_to_insert: list[tuple] = []
for mint, cs, m in composites:
    try:
        if not mint or cs < threshold:
            continue
        # market_cap is a DICT in the live snapshot:
        #   {"sol": <float>, "usd": <float>, "sol_usd": <float>}
        # NOT a scalar. Extract USD value defensively, handling both
        # the dict shape and any legacy scalar shape.
        mc_raw = m.get("market_cap")
        if isinstance(mc_raw, dict):
            mc_usd = mc_raw.get("usd")
        elif isinstance(mc_raw, (int, float)):
            mc_usd = mc_raw
        else:
            mc_usd = None
        if mc_usd is None:
            continue
        try:
            mc_usd_f = float(mc_usd)
        except (TypeError, ValueError):
            continue
        if mc_usd_f < MC_FLOOR_USD:
            continue
        crosses_to_insert.append((
            mint, now, float(cs), float(threshold),
            int(m.get("smart_money_in") or 0),
            float(m.get("max_mult") or 1.0),
            int(m.get("age_s") or 0),
            mc_usd_f,
        ))
    except Exception as e:
        print(f"[composite_predictions] cross-build skipped for "
              f"mint={(mint or '?')[:14]}..: {e}", flush=True)
        continue
```

Three changes:
1. **MC dict extraction** — primary fix: handle both `{"usd": ...}` dict shape and legacy scalar shape, with `mc_raw is None` and parse-failure guards.
2. **Per-mint try/except** — one bad row can't take out the whole batch. Same shape as case_study_harness's per-pred enrichment try/except.
3. **Defensive `float()` guard** — even if `mc_usd` is somehow non-numeric, the inner try/except catches it instead of propagating up to the caller's broad-except.

The outer try/except around the sqlite write (lines 297+) is unchanged.

---

## Verification post-deploy

Deploy: 2026-05-13T01:56:33Z. Web restart cleared the bad state. At +4 min:

```
composite_predictions rows: 7
  HGTXVANx7t1W39yDK..  composite=6.94  threshold=6.19  mc=$16,716
  CKKEWhZGMrs8soP26..  composite=7.43  threshold=3.09  mc=$5,965
  BEbEbDqcQQxk6KRLu..  composite=3.30  threshold=3.09  mc=$22,403
  KiP7ttLcGVopfie8G..  composite=8.55  threshold=5.38  mc=$6,965
  hxR7V2aJUbPs7Q94F..  composite=5.95  threshold=5.38  mc=$10,714
  8ddeqehTZo689FFX4..  composite=5.68  threshold=5.38  mc=$13,035
  Ca1MCQkTPDxf23e4z..  composite=9.25  threshold=5.38  mc=$13,186

composite_predictions.n_samples: 141 (warmup, accumulating)
web VmRSS: 2.65 GB (post-restart settling; jemalloc still in effect)
```

All 7 captures have MC > $5,000 floor — confirms the dict-extraction is producing correct USD values. Threshold values (3.09, 5.38, 6.19) are LOW because the daemon is in warmup with sparse samples; as the deque fills toward MAX (50,000 samples), threshold will stabilize at a more meaningful P95 value.

**Cross detection is now product-functional.** The 24-hour invisible-failure period is closed.

---

## What we lost during the silent-failure window

The daemon ran for ~24h before this fix. Composite crosses that SHOULD have been logged during that window are unrecoverable (the rolling sample was in-memory only; the threshold-evaluated cross events weren't persisted). User-observed catches today (2hi6Cn, 9KjPt) are PERMANENTLY missing from the public receipts log for the composite product surface.

This is documented as part of the receipts trail's honest history. The lesson: silent-failure windows produce gaps in audit-grade data that no amount of forensics can backfill. **Production-shape smoke testing is the upstream prevention.**

---

## Latency observation (independent diagnosis)

User also flagged latency p95 17.6s pre-deploy. This deploy restarted the web service, so the latency stat resets to cold-start values (samples_n=1, p95=56s — dominated by index loading + first-tick GBM warm-up). Steady-state latency will take ~5-10 min of samples to settle.

The latency issue is **independent of the composite bug**. Tomorrow's diagnosis: check whether p95 17.6s at 5h uptime is a new growth path or just normal post-jemalloc steady-state. VmRSS is stable (2.4-2.6 GB) so the memory side is fine — the latency degradation must be from elsewhere (sqlite contention, snapshot file size growth, observer-daemon producing larger snapshots, etc.).

Filed as a separate investigation for follow-up.

---

## Receipts trail

| Commit | Action |
|---|---|
| `e880c5a` Composite-receipts logging + pre-reg | Original deploy; this is when the bug was introduced |
| `c8b037d` Composite-receipts deploy receipt | First-pass verification; smoke-tested with synthetic dicts that didn't match production shape |
| `f5dc072` First memory mitigation | Subsample/maxlen on rolling deque |
| `1b6492c` Second-leak postmortem + watchdog | Memory watchdog as workaround |
| `c754236` jemalloc + Tier 2 archival | Memory pressure root-cause fix |
| **(this commit) Composite-predictions MC-dict postmortem + fix** | Cross detection now product-functional after 24h of silent failure |
| (later, separate) Latency p95 17.6s investigation | Independent of composite; needs fresh diagnostic |

---

## Cross-references

- [`composite_receipts_logging_prereg.md`](composite_receipts_logging_prereg.md) — original pre-reg
- [`composite_receipts_memory_postmortem_2026_05_12.md`](composite_receipts_memory_postmortem_2026_05_12.md) — first incident on the same daemon
- [`case_study_01_harness_bug_postmortem.md`](case_study_01_harness_bug_postmortem.md) — first instance of the same silent-failure pattern
- Memory: `feedback_pre_registration_branches.md` — discipline; the third silent-failure instance triggers the memory rule extension
- Memory: `feedback_no_bandaids.md` — fix is engineering-correction (dict extraction + per-mint try/except), not a parameter band-aid
- Memory: `project_hot_launch_composite_signal.md` — strategic context; composite-receipts is the load-bearing infrastructure for the dual-track product
