# Observer-curves archival — pre-registration

**Pre-registration commit.** Methodology, frozen growth-rate measurement, runway calculation, archival strategy, and verification gate committed BEFORE any production archival action. Per the receipts pattern: this commit must predate the destructive dry-run and the production archival. If a frozen criterion needs revision after this commit, the publish-then-post amendment discipline applies (refine/split, not relax; commit before verdict data; surface design flaw explicitly).

**Series:** companion to the volume-budget rule (`feedback_pre_reg_memory_budget.md`, extended 2026-05-12). This is the first disk-volume budget audit on graduate-oracle's `/data` mount.

---

## Strategic context

Today's incident exposed disk-volume growth in `/data/observer-curves/`. Each pump.fun mint observed by the Rust observer-daemon produces a JSON curve file written to this directory; wallet_intel + creator_history modules consume them to build in-memory indices. Files accumulate indefinitely — no current archival policy.

The user surfaced this as a discipline gap parallel to yesterday's memory-budget surfacing: workarounds (volume size increases) without timeline-bound root-cause fixes (archival) accumulate.

---

## Growth-rate measurement (frozen)

Production sampling at 2026-05-12T16:15Z, ~21 days of observer-curves history (2026-04-22 → 2026-05-12):

```
Per-day file counts (last 12 days):
  2026-05-01:  14,326 files /  286.5 MB
  2026-05-02:  25,041 files /  499.8 MB
  2026-05-03:  18,807 files /  376.1 MB
  2026-05-04:  20,580 files /  376.4 MB
  2026-05-05:  27,510 files /  604.4 MB
  2026-05-06:  28,425 files /  650.2 MB   ← peak observed
  2026-05-07:  24,359 files /  477.3 MB
  2026-05-08:  30,729 files /  635.6 MB
  2026-05-09:  26,419 files /  552.0 MB
  2026-05-10:  24,740 files /  472.9 MB
  2026-05-11:  29,649 files /  624.6 MB
  2026-05-12:  14,481 files /  113.1 MB (partial — mid-day)

  Average:  ~500 MB/day
  Peak:      650 MB/day (2026-05-06)
  Average file size:  ~21 KB
```

**Frozen baseline rate: 500 MB/day average; 650 MB/day worst case.**

The user-surfaced estimate of "1.2 GB/day" was conservative against observed reality. Actual rate is ~40% of that estimate. This is the publishable-receipts-trail measurement, not the original concern; the audit verifies the empirical rate before the archival design depends on it.

---

## Runway calculation (frozen)

```
/data volume:               25 GB total
Currently used:             9.4 GB (41%)
  observer-curves:          8.9 GB
  data.sqlite:             ~150 MB
  pkl indices:             ~300 MB (wallet_intel + grad_prob)
  case_studies + misc:     ~50 MB
Available:                  14 GB

Runway at peak growth (650 MB/day):  14 GB / 0.65 GB/day = 21.5 days
Runway at avg growth (500 MB/day):   14 GB / 0.50 GB/day = 28.0 days
Conservative target:                  21 days minimum
```

**Frozen runway: 21–28 days at current growth rate.** Archival pipeline must be operational before day 18 (3-day safety margin) — call it 2026-05-30.

---

## Archival strategy (frozen, tiered)

Three tiers based on file age. Tier boundaries are pre-registered; changing them post-deploy requires a publish-then-post amendment.

| Tier | Age range | Storage |
|---|---|---|
| **Tier 1 — Hot** | < 7 days | `/data/observer-curves/` as-is (current behavior) |
| **Tier 2 — Warm** | 7–30 days | gzip in place: `/data/observer-curves/<name>.json.gz` |
| **Tier 3 — Cold** | > 30 days | Move to `/data/observer-curves-archive/<YYYY-MM>/<name>.json.gz` (subdir partition by month for cleanup-by-month operations) |

**Why gzip in place for Tier 2 (not move):** wallet_intel + creator_history both maintain `_processed` sets keyed by basename. Gzipping keeps basenames AVAILABLE if a path ever needs cold-walk; just adds `.gz` suffix. Both modules currently `glob("*.json")` — they'll skip `.gz` files cleanly without breaking _processed-set semantics (the basename is still recognizable).

**Why move (not delete) for Tier 3:** the archived data IS part of the receipts trail — observed mint history is the load-bearing input to the wallet reputation index. Deletion is irrecoverable. Move-to-archive preserves the data with low storage cost (gzip compression ~80% typical).

**Optional Tier 4 — Glacier (future):** for files > 90 days, move to S3 Glacier. Out of scope for this pre-reg; can be added via publish-then-post amendment when storage volume warrants it. Current 25 GB volume with Tier 3 in-place is sufficient for ~12 months at current growth rate (~1 GB/month compressed).

### Estimated archival impact

```
Pre-archival /data/observer-curves/ size:           8.9 GB
Tier 2 candidates (7-30 days old, ~14 days):       ~7.0 GB raw → ~1.4 GB gzipped (80% compression)
Tier 3 candidates (>30 days old):                  ~0 GB (nothing > 30 days yet given 20-day history)

Estimated post-archival size:
  /data/observer-curves/      (Tier 1 + Tier 2 gzipped)  ~3.0 GB
  /data/observer-curves-archive/                          (empty for now)
  Total reclaimed:                                       ~5.9 GB
```

Reclaimed ~5.9 GB extends runway from 28 days → ~40 days post-first-archival-run.

---

## Verification gate (frozen pre-implementation)

The user's stated verification: "Test on small sample first (delete oldest 1% of curves in a dry run, verify wallet_intel.py still produces same leaderboard top-100). If verified: proceed with archival. If not: rethink the approach."

### Why this verification is correct

`wallet_intel.py` uses an incremental refresh pattern:
- `_load_cache()` reads the full state from `/data/wallet_intel_index.pkl` (132 MB on disk)
- `refresh()` only processes NEW files (not in `_processed` set)
- `_processed` set persists in the pkl

Therefore: archiving / deleting OLD curves should leave the leaderboard UNCHANGED because:
1. The pkl already contains all historical wallet stats
2. Old curves are already in `_processed` — they wouldn't be re-processed even if present
3. Cold-start path would lose data, but with pkl present, cold-walk doesn't happen

Same pattern in `creator_history.py` (verified by grep: `_processed` set + `_load_cache`).

**Risk surface:** if pkl is corrupted/deleted, the system loses ALL historical data. The pkl is the canonical state holder. Archival exposes this dependency more sharply but doesn't create it.

### Frozen verification protocol

1. **Snapshot pkl backups BEFORE any archival action:**
   - `cp /data/wallet_intel_index.pkl /data/wallet_intel_index.pkl.preverify.YYYYMMDD`
   - Same for `grad_prob_index.pkl` and `creator_history.pkl`
   - Documents the rollback state.

2. **Dry-run on 1% of oldest curves (~4,200 files):**
   - **MOVE not delete:** `mv` files from `/data/observer-curves/<file>.json` to `/data/observer-curves-dryrun-archive/<file>.json`
   - Move-not-delete makes the verification fully reversible. If the test FAILS, simply move files back.

3. **Trigger wallet_intel refresh + capture leaderboard top-100:**
   - Force a refresh tick via `supervisorctl signal HUP web` or similar
   - Sample `/api/v1/leaderboard?limit=100` (or equivalent endpoint)
   - Save as `dryrun_baseline_top100.json`

4. **Compare against pre-dry-run baseline:**
   - Capture the leaderboard BEFORE the move, save as `pre_dryrun_top100.json`
   - Diff with `dryrun_baseline_top100.json`
   - Expected: byte-identical (same wallet addresses in same order with same scores)

5. **Verification verdict:**
   - **PASS** (byte-identical): proceed with full production archival
   - **FAIL** (any difference): STOP. Move dry-run files back. Investigate. Do NOT proceed with production archival until root cause understood.

6. **Iteration-limit (frozen):** ONE dry-run attempt. If it fails, no fix-N+1 attempt; pause for architectural review. The discipline pattern (`feedback_pre_registration_branches.md`) applies: if the first verification gate fails, the next step is fresh pre-registration, not parameter tweaking.

### Why "leaderboard top-100" is the right verification axis

The leaderboard is the canonical user-facing output of wallet_intel.INDEX. If top-100 wallets + scores are unchanged, the index state is functionally unchanged. We're NOT checking byte-identity of the pkl (which would be too strict — pickle output isn't always deterministic across runs).

Smaller leaderboard subsets (top-10, top-50) are subsets of top-100 by construction; checking top-100 covers them. Larger sets (top-1000+) include long-tail wallets with low scores where minor recomputation noise might appear; top-100 is the cleanest signal.

---

## Implementation scope

`web/observer_curves_archival.py` (new module) — exposes:

```python
def assess() -> dict:
    """Sample-only: count files per tier, estimate sizes, no I/O modifications.
    Returns {tier_1_count, tier_1_size_mb, tier_2_count, tier_2_size_mb,
             tier_3_count, tier_3_size_mb, total_size_mb}."""

def dryrun_move(n_oldest: int, dst_dir: str) -> dict:
    """Move the N oldest curve files to dst_dir (does NOT delete).
    Returns {moved_count, moved_size_mb, moved_basenames_sample}.
    Idempotent: skips files already in dst_dir."""

def restore_dryrun(dst_dir: str) -> dict:
    """Inverse of dryrun_move: moves files back. For verification rollback."""

def gzip_in_place_tier_2(min_age_days: int = 7, max_age_days: int = 30, dry: bool = True) -> dict:
    """Gzip files in the tier-2 age range. dry=True returns intent only."""

def move_tier_3(min_age_days: int = 30, archive_dir: str = "/data/observer-curves-archive",
                dry: bool = True) -> dict:
    """Move files > min_age_days to archive_dir, partitioned by YYYY-MM."""
```

The module includes no destructive defaults — all destructive actions require explicit non-`dry` invocation. Auth-gated CLI invocation only; not exposed over HTTP.

---

## Schedule (frozen)

| Phase | Time | Action |
|---|---|---|
| Pre-reg commit | This commit | Methodology + frozen criteria + verification gate |
| Implementation commit | Within ~1h of pre-reg | `web/observer_curves_archival.py` module + dry-run CLI |
| Pkl backups | Before dry-run | `cp` all 3 pkl files with `.preverify.YYYYMMDD` suffix |
| Dry-run (1% / ~4,200 files) | After pkl backups | `dryrun_move` to `/data/observer-curves-dryrun-archive/` |
| Leaderboard capture | Before + after dry-run | `pre_dryrun_top100.json` + `dryrun_baseline_top100.json` |
| Verification verdict | Same session | PASS or FAIL per criterion above |
| **Production archival** | Only if dry-run PASS | Tier 2 gzip (immediately) + Tier 3 move (when applicable) |
| Documentation receipt | After production action | Deploy receipt committed publicly |

---

## Acceptance criteria (frozen)

For the **dry-run verification** (gate before production archival):

- **PASS:** byte-identical leaderboard top-100 (same wallets, same order, same scores). Proceed with production archival.
- **FAIL:** any difference detected. STOP. Move dry-run files back. Investigate.

For the **production archival** (after verification PASS):

- **PASS:** post-archival /data usage drops to expected ~3.5 GB (8.9 GB → ~3 GB observer-curves + 0.5 GB other), AND wallet_intel + creator_history leaderboards unchanged byte-for-byte, AND no daemon errors in fly logs for 1h post-archival.
- **FAIL:** size drop short of expected (compression ratio off), OR leaderboards changed, OR daemon errors. Surface for user direction.

---

## What this pre-reg does NOT do

Per the no-overengineering discipline:

- **No automatic archival daemon yet** — production archival is operator-triggered. A future amendment can add scheduled archival once the manual run validates the pipeline.
- **No S3 / external storage integration yet** — Tier 3 archives stay on `/data/observer-curves-archive/`. S3 export is future scope.
- **No retention policy beyond Tier 3** — files moved to Tier 3 stay there indefinitely. Glacier / 90-day-expiry policy is future scope.
- **No change to observer-daemon write behavior** — observer continues writing new curves to `/data/observer-curves/` as-is.

---

## Pre-registered next decisions (post-verification)

If dry-run PASSES and production archival runs successfully:

1. **Schedule:** add to ops cadence — re-run archival weekly to maintain `/data` < 50% utilization. Manual trigger initially; automatable later.
2. **S3 cold storage:** evaluate if /data usage exceeds 60% after Tier 2 maturity; add Tier 4 to S3 if needed.
3. **Audit cadence:** every 30 days, sample empirical growth rate vs frozen baseline. If growth rate exceeds 1 GB/day sustained, surface for architectural review (might indicate observer-daemon volume change).

---

## Cross-references

- `feedback_pre_reg_memory_budget.md` (2026-05-12 extension) — volume-budget rule analog; this pre-reg is the first application to disk volume
- `web_service_memory_pressure_postmortem_2026_05_12.md` — sister postmortem; same discipline pattern surfacing systemic gaps
- Memory: `project_wallet_index_is_the_moat.md` — explains why archival risk is non-trivial; the wallet index IS the moat data
- Memory: `feedback_no_bandaids.md` — volume increases without root-cause fix would be the band-aid alternative; archival is the engineering fix
- Memory: `feedback_pre_registration_branches.md` — discipline this pre-reg follows
