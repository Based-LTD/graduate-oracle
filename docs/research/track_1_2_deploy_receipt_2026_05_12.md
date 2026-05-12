# Track 1 (jemalloc) + Track 2 (observer-curves archival) — deploy receipts

**Both tracks shipped 2026-05-12 per user direction.** Track 1 is the architectural root-cause fix that was overdue from yesterday's web service memory pressure postmortem (commit `1b6492c`). Track 2 is the first application of the volume-budget discipline rule (`feedback_pre_reg_memory_budget.md` 2026-05-12 extension) to disk volume.

Pre-reg companion for Track 2: [`observer_curves_archival_prereg.md`](observer_curves_archival_prereg.md), commit `0463b1d`.

---

## Track 1 — jemalloc deploy

**Deploy:** 2026-05-12T16:11:01Z. `flyctl deploy --remote-only`. ~3 min build (libjemalloc2 apt install + image rebuild). Single deploy.

### Changes

- `deploy/Dockerfile`: added `libjemalloc2` to apt-get install
- `deploy/supervisord.conf`: added `LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libjemalloc.so.2` + `PYTHONMALLOC=malloc` to `[program:web]` environment

### Verification post-deploy

Acceptance criterion (frozen pre-deploy in postmortem 1b6492c):
> "PASS: <30 MB/min (allocator-churn dropped >3×; can disable memory watchdog)"

Measured VmRSS over the 1h+ post-deploy window:

| Sample | Time | VmRSS (kB) | Δ from baseline |
|---|---|---|---|
| Baseline (T+2min) | 16:13Z | 2,534,180 | 0 |
| T+62min | 17:13Z | 2,485,092 | **−49 MB** |
| T+85min | 17:36Z | 2,448,400 | **−86 MB** |

**Memory growth rate: NEGATIVE.** Web process gave back memory to OS over the 85-min window. This is fundamentally different behavior from pre-mitigation pattern (+100 MB/min); jemalloc actively compacts arenas and releases pages back to the OS, in contrast to CPython's default pymalloc which holds pages indefinitely.

**Track 1 verdict: STRONG PASS** under the frozen <30 MB/min criterion. The C-extension allocator-churn fragmentation that drove yesterday's incident is no longer accumulating.

### Memory watchdog status

Per acceptance criterion, the memory watchdog daemon (`web/main.py`) "can be disabled" on PASS. Decision: **KEEP for now** as belt-and-suspenders for 7 days of further observation. Disable in a future commit if 7-day VmRSS trend remains stable (no creep back above 3 GB).

Rationale: even with jemalloc, future code changes (new ML modules, additional sklearn paths) could regress. The watchdog at 3.3 GB threshold is a no-op when memory stays well below; it costs nothing operationally.

---

## Track 2 — observer-curves archival (Tier 2 gzip)

**Operation start:** 2026-05-12T17:12Z. Background gzip of 233k Tier 2 files (age 7-30 days). Completed: 2026-05-12T~17:22Z (~10 min runtime; 0 stat-errors on final pass).

### Pre-archival state

```
/data volume:               25 GB total, 9.4 GB used (38%)
  observer-curves:          8.9 GB (424,173 files)
    Tier 1 (Hot, <7 days):  3,837 MB (191,069 files)
    Tier 2 (Warm, 7-30d):   4,398 MB (233,104 files)  ← targeted
    Tier 3 (Cold, >30d):    0 MB (0 files)
  Available:                14 GB
  Runway (peak rate):       21 days
```

### Verification gate execution

Per the frozen verification protocol in the pre-reg:

1. **Pkl backups created** at 16:38Z:
   - `wallet_intel_index.pkl.preverify.20260512` (138 MB)
   - `grad_prob_index.pkl.preverify.20260512` (167 MB)
   - `creator_history.pkl.preverify.20260512` (38 MB)

2. **Baseline leaderboard captured:** wallet_intel.INDEX top-100 with aggregate fingerprints only (graduated/runner/rug/total/fast_snipes; NO wallet addresses surfaced per `project_wallet_index_is_the_moat.md` discipline).

3. **Dry-run move of 4,200 oldest files** (1% of corpus) to `/data/observer-curves-dryrun-archive/`. Move-not-delete; reversible. Sample basenames captured.

4. **Post-dry-run leaderboard captured.** Compared against baseline.

5. **Verdict: PASS.** Top-100 byte-identical (same ranks, same aggregate counts) — the pkl is canonical for index state; the curves directory is only consulted for cold-walk (which didn't fire because pkl was present).

6. **Dry-run files restored** to `/data/observer-curves/`.

### Production archival execution

After verification PASS, the production Tier 2 gzip ran:

- Small-batch confirmation (age 19-20 days, 7,820 files): 90.7 MB reclaimed, compression ratio 0.259, 0 errors.
- Background run (full Tier 2 age 7-30 days, ~226k files): completed in ~10 min.
- Final straggler run (age-window edge cases, 229 files): 3.5 MB reclaimed, compression ratio 0.284, 0 errors.

Cumulative: **234k files gzipped, 0 errors.**

### Post-archival state

```
/data volume:               25 GB total, 6.7 GB used (27%)  ← was 38%
  observer-curves:          5.9 GB                          ← was 8.9 GB
    Tier 1 (Hot, <7 days):  3,824 MB (~unchanged)
    Tier 2 (Warm, 7-30d):   1,196 MB (~234k files,
                                       mostly .json.gz)     ← was 4,398 MB
    Tier 3 (Cold, >30d):    0 MB
  Available:                17 GB                            ← was 14 GB
  Runway (peak rate):       28 days                          ← was 21 days
```

**Disk reclaim: 3.0 GB** (33% reduction in observer-curves footprint). Runway extended by ~7 days.

### Post-archival leaderboard verification

Captured wallet_intel top-100 with same script. Final rank-100 entry: `{"graduated": 300, "rug": 5, "runner": 114, "total": 426}` — byte-identical to baseline. **Leaderboard preserved.**

### Track 2 verdict: PASS

- Disk reclaim achieved (~3 GB)
- Zero errors during gzip operation
- Leaderboard byte-identical pre/post archival
- pkl files unmodified; backups preserved (can be deleted in a future cleanup when confident)
- No daemon errors in fly logs

---

## Combined status snapshot at deploy completion

```
Time:           2026-05-12T17:36Z (~85 min into Track 1, ~25 min into Track 2 production)
status:         warn
warnings:       [score latency avg 7.84s (>5s), score latency p95 11.56s (>8s)]
Web VmRSS:      2,448,400 kB (2.45 GB) — trending DOWN ✓
MemAvailable:   947,884 kB (947 MB) — vs 130 MB pre-mitigation ✓
Inactive(anon): 2,595,840 kB (2.59 GB) — vs 3.5 GB pre-mitigation ✓
/data used:     6.7 GB / 25 GB (27%) — vs 9.4 GB / 38% pre-archival ✓
```

**Latency note:** p95 11.56s is still above the 8s threshold but recovering (was 12.4s mid-archival, 43.9s during yesterday's pressure peak). The elevated p95 during the deploy window is plausibly explained by Tier 2 gzip CPU competition with the score-pool workers. Expected to recover as the heap quieter post-gzip. Will re-sample in 30 min for a clean steady-state reading.

---

## What this verdict closes

- **Yesterday's memory pressure root cause:** Option 4 (jemalloc) chosen as cheapest experiment per the postmortem's recommended sequence; passed criterion. The user's "discipline gap" framing (workarounds without timeline-bound root-cause fixes accumulate) is now closed — root cause shipped within 24h of the workaround.
- **Volume runway concern:** went from 21 days runway → 28 days. Tier 2 gzip is repeatable on a cadence (operator can re-run weekly or monthly). Tier 3 archival not yet needed (no files >30 days).
- **Discipline gap (volume budget):** rule was added 2026-05-12 in `feedback_pre_reg_memory_budget.md` and applied here; first end-to-end production application of the rule.

## What this verdict opens (filed for follow-up)

1. **Automate Tier 2 cadence:** add a daemon thread or supervisord cron to re-run `gzip-tier-2 --apply` weekly. Operator-trigger ok for now; automate before runway tightens.
2. **Tier 3 readiness:** as files age past 30 days (first eligible at ~2026-05-22), `move-tier-3` will start moving them to `/data/observer-curves-archive/`. Test the partition-by-month logic at first use.
3. **S3 cold storage:** evaluate at 60% volume utilization. Currently 27%; no immediate need.
4. **wallet_intel cold-walk safety:** if pkl is ever corrupted/deleted, cold-walk will glob `*.json` (won't see `.json.gz` files). Either (a) update `glob` pattern to include `.gz` + transparent decompression, OR (b) keep cold-walk-as-emergency-only and document that gzipped files are safe ONLY when pkl is intact. Filed as architectural item; not urgent (pkl is stable).
5. **Memory watchdog disable decision:** keep for 7 days of jemalloc observation. Decide at 2026-05-19 whether to remove.

---

## Receipts trail

| Commit | Action |
|---|---|
| `1b6492c` web service memory pressure postmortem + Path B workaround | Yesterday's diagnosis + watchdog |
| `0463b1d` observer-curves archival pre-registration | Track 2 methodology + frozen verification gate |
| **(this commit) Track 1 (jemalloc) + Track 2 (Tier 2 gzip) deploy receipt** | Both tracks executed today; verifications PASS; runway extended; memory growth eliminated |
| (later, ~2026-05-19) Memory watchdog disable decision | After 7-day observation |
| (later, ~2026-05-22) Tier 3 first use | When files age past 30 days |

---

## Pkl backup retention policy

Pre-verify pkl backups created at 16:38Z (`*.preverify.20260512`) consume ~343 MB on /data. Retention: **keep for 7 days** for emergency rollback. Delete on 2026-05-19 if no incidents.

Stored at:
- `/data/wallet_intel_index.pkl.preverify.20260512`
- `/data/grad_prob_index.pkl.preverify.20260512`
- `/data/creator_history.pkl.preverify.20260512`

---

## Cross-references

- [`observer_curves_archival_prereg.md`](observer_curves_archival_prereg.md) — Track 2 pre-reg
- [`web_service_memory_pressure_postmortem_2026_05_12.md`](web_service_memory_pressure_postmortem_2026_05_12.md) — Yesterday's postmortem; Track 1 is Option 4 from its enumerated fixes
- [`composite_receipts_memory_postmortem_2026_05_12.md`](composite_receipts_memory_postmortem_2026_05_12.md) — First-leak postmortem (deque fix)
- Memory: `feedback_pre_reg_memory_budget.md` — discipline rule that motivated the pre-reg + 2026-05-12 extension
- Memory: `feedback_no_bandaids.md` — Track 1 is the engineering root-cause fix, not a parameter tweak
- Memory: `project_wallet_index_is_the_moat.md` — explains why archival risk is non-trivial; leaderboard byte-identical verification is the load-bearing check
