# Case Study 01 harness bug postmortem — silent enrichment failure

**Diagnosed:** 2026-05-10, ~17:54Z UTC. **Public commit:** this commit. **Pre-Tuesday-verdict surfacing per the original Case Study 01 pre-reg's commitment to "make implementation gaps part of the receipts trail."**

## TL;DR

User asked a binary question: is the 0-observations-after-Path-E result (a) a joiner bug or (b) a structural-overlap problem between graduate-oracle's lane-60s and GMGN's `--filter-preset strict --type new_creation` populations?

The empirical answer is **(c) — a third option neither hypothesis covered**: the harness has been silently failing inside `GradOracleSource.pull()` since Phase 2 launch (commit `08fb96c`, 2026-05-08) due to an enrichment-query schema mismatch. The exception was caught by an over-broad `except Exception` in `collection_loop` and converted into "no predictions to process," every iteration, indefinitely. **The pre-registered methodology was sound. The implementation never executed it.**

The C-iv subcondition (upstream-infrastructure-blocked) and the original (a)/(b) hypotheses are still answerable, but only AFTER the bug fix lands and produces actual observations. Tuesday's verdict will be based on post-fix data. The pre-reg amendment below documents this and frozen-it-publicly before any post-fix data is in.

## Diagnosis trail

### Step 1 — Validate joiner contract

Read `case_study_harness/joiner.py`. Joiner contract (line 8): "If no GMGN snapshot is within tolerance, the row still lands but `gmgn_snapshot_at` is NULL and `gmgn_in_strict_preset` is NULL — the absence is itself data." Therefore **0 observations means predictions never reached the joiner**, not that the join failed. The (a)/(b) hypothesis dichotomy assumed predictions reached the joiner. Both were false-premise.

### Step 2 — Validate predictions visibility

Direct sqlite query on `/data/data.sqlite`:
```sql
SELECT id, mint, predicted_at, predicted_prob, grad_prob_bucket, age_bucket
  FROM predictions
 WHERE predicted_at > 1778396128   -- Path E deploy_ts
   AND age_bucket <= 75
   AND grad_prob_bucket IN ('HIGH','MED')
 ORDER BY predicted_at;
```

Returned **6 MED predictions** (5 originally identified by user + 1 emitted during diagnosis). All have `mint_checkpoints` rows at ages 15/30/60/120. Visibility is fine.

### Step 3 — Validate insert path

Wrote a minimal diagnostic script that imports `insert_observation` from the harness module and inserts a synthetic test row directly. Result: row inserted successfully; `case_study_01_observations` count went 0 → 1; cleanup removed the test row. **Insert path works.**

### Step 4 — Validate `GradOracleSource.pull()` end-to-end

Wrote a second diagnostic that imports `GradOracleSource` directly and calls `pull(since_ts=deploy_ts)`. Result: **`sqlite3.OperationalError: no such column: feature_unique_buyers`** raised inside the per-pred enrichment loop at `grad_oracle.py:60`.

### Step 5 — Verify mint_checkpoints schema

```
PRAGMA table_info(mint_checkpoints):
  feature_creator_runner, feature_creator_5x_rate,
  feature_creator_n_launches, feature_smart_money,
  feature_n_whales, feature_cluster, feature_bundle_detected,
  feature_bundle_pct, feature_fee_delegated, feature_dex_paid,
  feature_hot_launch, feature_vsol_velocity,
  feature_funded_by_runner_creator, feature_funded_by_smart_money,
  feature_funded_by_whale, feature_creator_wallet_age_s,
  feature_first3_smart_count, feature_first3_clustered_count,
  feature_first3_whale_count, feature_first3_fresh_count,
  feature_concurrent_density_60s, feature_recent_grad_30m,
  feature_recent_grad_60m, feature_holder_top10_pct,
  feature_dev_buy_sol, ...
```

`feature_unique_buyers` does **not** exist. The harness's `GradOracleSource.pull()` enrichment query referenced a column that was never in the production schema — probably a confused field name during pre-reg drafting (the `unique_buyers` concept exists elsewhere in the codebase, e.g., `m_out["unique_buyers"]` for input-quality gating, but it's not in `mint_checkpoints`).

### Step 6 — Trace the silent-failure

`run_study.py:174` (collection_loop):
```python
try:
    preds = grad.pull()
except Exception as e:
    log(f"grad_oracle pull error: {e} (continuing)")
    preds = []
```

The exception fires inside the per-pred enrichment loop in `grad.pull()`. The broad `except Exception` catches the `OperationalError`, logs it, and sets `preds = []`. Every iteration. Forever. Daemon stays in `collection_loop`, polling cleanly, producing nothing.

The log line `f"grad_oracle pull error: {e} (continuing)"` should have surfaced this — except the log lines route to `/dev/stdout` → supervisord → fly logs, and fly logs don't retain history past ~2 minutes. By the time a human noticed "0 observations after 8h," the log line had rolled off thousands of times.

## Why two prior diagnoses missed this

**Earlier diagnosis (2026-05-09, around the original 0-observations escalation):** I correctly identified that the `predictions` table had 0 HIGH/MED rows at the time and concluded "upstream-block — same root cause as Finding 8." That conclusion was **half right** — there were genuinely 0 HIGH/MED rows BEFORE Path E. But I did not check what would happen AFTER Path E started producing them, because the harness daemon was in a state where `collection_loop` line 266's post-tick sleep was the visible py-spy frame and the upstream pipe was empty. I attributed the 0-observations to the upstream-empty state alone and missed the latent enrichment bug behind it.

**Today's diagnosis (Path E producing MED but harness still 0):** Path E started emitting MED predictions, the harness should have caught them, didn't. Two-part bug: (1) the enrichment SELECT references a non-existent column → exception fires inside `pull()`, (2) the broad `except` in `collection_loop` swallows it. The bug was always present; it was just MASKED until Path E created post-trigger MEDs that exposed it.

**Lesson for the audit program (filed in BACKLOG.md):** silent-failure-via-broad-except is a known anti-pattern. The harness's collection_loop exception handler should be replaced with a narrow, structured one that distinguishes "transient sqlite error" from "schema mismatch" from "upstream gone" — and the "schema mismatch" case should fail loudly OR alert through a path that bypasses fly-logs-only-keeps-2-minutes.

## The fix (shipped in same commit as this writeup)

### grad_oracle.py changes

1. Remove `feature_unique_buyers` from the SELECT clause. Verified column list matches `/data/data.sqlite mint_checkpoints` schema as of 2026-05-10. The corresponding observation field `go_feat_unique_buyers` stays in the schema and resolves to NULL (already nullable; no schema migration needed).
2. Wrap the per-pred enrichment in `try / except sqlite3.Error`. Future schema drift cannot silently take out the entire pipeline; per-pred failures log once and the pred lands without enrichment. Same shape as the joiner's "absence is itself data" contract.

### run_study.py changes

3. Replace the `grad.pull()` initial-seed call (which sets cursor to `int(time.time())`) with `grad.pull(since_ts=trigger_ts)` so a daemon restart mid-collection backfills predictions emitted between the original trigger and the restart. Inserts those rows immediately with empty `snapshot_buffer` (so GMGN side is NULL). The grad-side count is what the C-iv subcondition verifies; the GMGN absence on backfill rows is documented data, not missing data.

### What the fix produces (verified locally before deploy)

Live test against the post-fix module on the running container:

```
pull(since_ts=deploy_ts) returned 6 preds:
  1778413174 GSeGxskJ2z5B1j.. bucket=MED feat_count=9
  1778413527 8p2zCBFdY7U9sL.. bucket=MED feat_count=9
  1778427277 DiL7YLzM6JdC4e.. bucket=MED feat_count=9
  1778432022 E5tYsDqeWcXGg4.. bucket=MED feat_count=9
  1778433240 BL8pCSboJczivj.. bucket=MED feat_count=9
  1778435956 3KZsEFzaTpfa5s.. bucket=MED feat_count=9
```

Six post-Path-E MED preds, each enriched with 9 mint_checkpoints feature columns. Pre-fix this raised; post-fix it returns. Daemon needs a restart to pick up the module change (Python doesn't hot-reload imported modules); the restart happens with the next `fly deploy` that follows this commit.

## Pre-reg amendment 02 — Bug postmortem and verdict-window adjustment

**Per the publish-then-post discipline (memory: `feedback_pre_registration_branches.md`):** this amendment commits BEFORE the verdict data is in. The verdict was scheduled at trigger_ts + 48h + 24h grace = **2026-05-12T17:45Z**. Today is 2026-05-10T17:54Z. The verdict is ~48h away.

**What this amendment changes:**

1. **Eligible-data window narrows.** The pre-reg said "48h collection window starting at trigger_ts (2026-05-09T16:45Z)." Empirically, the harness was silent for the first ~25h of that window (16:45Z May 9 → 17:54Z May 10) due to the enrichment bug. **The eligible collection window is now (post-fix-deploy_ts) → trigger_ts + 48h.** With deploy at ~18:00Z May 10 and trigger window ending at 16:45Z May 11, that gives ~22h of post-fix collection vs the original 48h.

2. **Backfill rows count toward grad-side, not GMGN-side, observations.** Per the fix, the daemon will insert ~6 backfill rows (one per post-trigger pre-fix MED prediction) with `gmgn_snapshot_at=NULL`. These rows ARE valid observations for the C-iv subcondition's verification query (`SELECT COUNT(*) FROM predictions WHERE predicted_at IN window AND grad_prob_bucket IN ('HIGH','MED')`). They are NOT valid for the precision-comparison branches (A/B), since precision-comparison requires both grad-side bucket and GMGN-side strict-preset membership. The pre-reg's primary precision comparison can only use forward-collected rows where both sides are populated.

3. **Branch C is more likely to fire.** With ~22h of post-fix collection at Path E's projected 11 MED/day rate → ~10 MED forward-observations expected by trigger window close. The pre-reg's n≥30 threshold for Branch A/B verdict is unlikely to be met. Branch C ("inconclusive — n<30 or resolution rate <70%") fires at the verdict, and Subcondition C-iv fires inside Branch C ("upstream-infrastructure-blocked subcondition" — though the cause is now multi-factor: upstream was zero-emitting until Path E AND harness had silent enrichment bug AND post-fix window is shorter than the original 48h).

4. **The C-iv re-arm condition stays as-is.** The case study re-arms when Path E sub-branch produces ≥10 MED in first 24h post-deploy AND the harness is verified to be inserting rows. Path E's first 24h closes at 2026-05-11T06:55:28Z. Post-fix harness will have inserted ~6 backfill rows + whatever forward-collects. The re-arm fires if total post-fix MED rate ≥10/24h, which the projected Path E rate satisfies.

5. **Discipline note.** This amendment is documenting an implementation gap that prevented the original methodology from executing. It is NOT relaxing acceptance criteria, NOT changing the precision thresholds, NOT extending the original collection window. The eligible-data window narrowing and Branch-C-likelihood are MECHANICAL CONSEQUENCES of the bug, not relaxations.

## Receipts trail update (Case Study 01 chain)

| Commit | Action |
|---|---|
| `5bc8f33` Case Study 01 pre-registration | Methodology, criteria, branches, harness scope frozen before any data |
| `08fb96c` Case Study 01 Phase 2 — reusable harness scaffold | Harness shipped; **silent enrichment bug introduced here** (feature_unique_buyers column reference vs actual mint_checkpoints schema) |
| Trigger fired 2026-05-09T16:45:54Z | Daemon entered collection_loop with bug present; 0 observations through bug-blocked window |
| `87edcb7` Finding 8 interim verdict | Variant 5B fired; identified upstream pipe at 0 emission |
| `4d56f53` Case Study 01 — Branch C amended pre-verdict; Subcondition C-iv added | Pre-registered upstream-infrastructure-blocked path; assumed harness was working |
| `147777d` Path E deploy receipt | Path E shipped; upstream pipe started emitting MEDs at projected rate |
| `51a409f` Case Study 01 harness bug postmortem + fix + Pre-reg Amendment 02 | Empirical diagnosis identifies third root cause (silent enrichment bug); fix shipped same commit; amendment narrows eligible-data window and acknowledges Branch C likelihood |
| **(this commit) Deploy receipt — observations populated** | Fix deployed at 2026-05-10T19:25:37Z; 6 backfill rows landed; Path E anchor preserved across redeploy |

---

## Deploy receipt — 2026-05-10T19:25:37Z

**Deploy timestamp:** 1778441137 (UTC). `flyctl deploy --app graduate-oracle --remote-only`. Duration ~78s. Rolling-update; single machine; smoke + health checks passed; lease cleared.

### Verification (Step 1 of post-deploy)

`case_study_01_observations` row count: **0 → 6**. All 6 post-trigger MED predictions backfilled into the harness DB. Spot-check of row contents confirms the data is healthy and matches the live `predictions` table:

| predicted_at | mint | bucket | age | smart_money | whales | bundle_pct | gmgn_snapshot_at | gmgn_in_strict_preset |
|---|---|---|---|---|---|---|---|---|
| 1778413174 | GSeGxskJ2z5B1j.. | MED | 60 | 9 | 7 | 0.0 | NULL | NULL |
| 1778413527 | 8p2zCBFdY7U9sL.. | MED | 60 | 7 | 8 | 5.9 | NULL | NULL |
| 1778427277 | DiL7YLzM6JdC4e.. | MED | 60 | 2 | 7 | 46.2 | NULL | NULL |
| 1778432022 | E5tYsDqeWcXGg4.. | MED | 60 | 4 | 7 | 45.1 | NULL | NULL |
| 1778433240 | BL8pCSboJczivj.. | MED | 60 | 7 | 4 | 0.0 | NULL | NULL |
| 1778435956 | 3KZsEFzaTpfa5s.. | MED | 30 | 9 | 7 | 33.6 | NULL | NULL |

All grad-side feature columns populated from `mint_checkpoints` (the previously-failing enrichment now succeeds without `feature_unique_buyers`). GMGN side NULL on all 6, as designed for backfill rows (the in-memory snapshot_buffer was empty at backfill time; absence is data per joiner contract).

### Path E verification anchor preserved (Step 2 of post-deploy)

The redeploy reset `bucket_cutoffs._state["computed_at"]` to the new deploy time (1778441215 = 2026-05-10T19:26:55Z). Without anchor preservation, this would have shifted the Path E T+24h interim check from 06:55Z May 11 to 19:26Z May 11 — a ~12.5h methodology shift.

Anchor preservation: `web/main.py` `_acceptance_gates()` now uses a hardcoded `PATH_E_INITIAL_DEPLOY_TS = 1778396128` constant (the original Path E deploy timestamp from receipt commit `147777d`). Verified post-deploy:

```
GET /api/status → acceptance_gates[0]:
  path_e_deploy_ts:    1778396128 (2026-05-10T06:55:28Z)  ← original, not 19:25 redeploy
  path_e_interim_close: 1778482528 (2026-05-11T06:55:28Z) ← original T+24h
  path_e_full_close:    1779000928 (2026-05-17T06:55:28Z) ← original T+7d
```

Methodology integrity preserved. Future redeploys (Fix C latency deploy, etc.) will not shift these anchors.

### Forward-collection now operational

Daemon restart at 19:25Z installed the fixed module. The collection_loop polls every 60s:
- `gmgn.snapshot()` — populates rolling snapshot_buffer
- `grad.pull()` — returns new HIGH/MED preds (no longer raises)
- `joiner.match()` per pred — joins against snapshot_buffer with ±120s tolerance
- `insert_observation()` — lands the row

For new MED predictions emitted between now and the trigger window close at 2026-05-11T16:45:54Z (~21h), observations will land WITH GMGN side populated (since the snapshot_buffer is now alive). Those rows are eligible for the Branch A/B precision comparison, in contrast to the 6 backfill rows which only count toward the C-iv subcondition's grad-side count.

### Updated verification queries (Tuesday verdict, 2026-05-12T17:45:54Z)

```sql
-- Total observations at verdict cutoff
SELECT COUNT(*) FROM case_study_01_observations
 WHERE captured_at < 1778600754;
-- Includes both backfill rows (gmgn_snapshot_at IS NULL) and
-- forward-collected rows (gmgn_snapshot_at IS NOT NULL).

-- Forward-collected only (eligible for A/B precision comparison)
SELECT COUNT(*) FROM case_study_01_observations
 WHERE captured_at < 1778600754
   AND gmgn_snapshot_at IS NOT NULL;
-- This is the n that determines whether Branch A/B can fire.
-- Pre-reg threshold: n>=30. Likely n<30 given ~21h post-fix window
-- at projected ~11 MED/day → ~10 forward MED → minus mints not in
-- GMGN strict-preset → likely 0-5 fully-joined rows.

-- C-iv subcondition verification (grad-side count over original 48h)
SELECT COUNT(*) FROM predictions
 WHERE predicted_at >= 1778342754   -- trigger_ts
   AND predicted_at <  1778515554   -- trigger_ts + 48h
   AND grad_prob_bucket IN ('HIGH','MED')
   AND age_bucket <= 75;
-- Likely <30, fires C-iv subcondition. Multi-factor cause:
-- (1) upstream pipe at 0 emit until Path E (deploy at +14h into window)
-- (2) harness silent-bug-blocked from inserting until +27h into window
-- (3) post-fix collection window only ~21h vs original 48h
```

### Observations on the bug's age and the audit-program implication

The enrichment bug existed at Phase 2 launch (commit `08fb96c`, 2026-05-08). It was masked for ~25h before exposure because the upstream pipe was producing 0 HIGH/MED predictions in that window (Finding 8's bucket emission breakage). Path E started producing MEDs at 11:39Z May 10; the bug exposed itself when the first MED hit the daemon and `pull()` raised. Even then, the silent-failure path (`except Exception` + `log + preds=[]`) kept the failure invisible until the user noticed "0 observations after Path E."

This is the second silent-failure-via-broad-except pattern surfaced in this codebase. The first was the post_grad_survival_prob "3/5 training columns at zero since launch" finding (2026-05-07, commit reference in `post_grad_metric_broken_since_launch.md`). Both were diagnosed AFTER weeks of operational invisibility. The audit program's BACKLOG entry on this anti-pattern should be promoted to a structural improvement: any module whose failure produces silent zero-output should be wrapped with health-checks that surface via `/api/status.warnings`, not just `print(...)` to fly logs that retain ~2 minutes.

Filed for the audit-program post-Tuesday-verdict review.

## What this means for the Tuesday verdict

**Most likely verdict: Branch C with Subcondition C-iv firing**, with the multi-factor cause documented honestly:
- Upstream pipe was zero-emitting until Path E (Finding 8 chain)
- Harness was silently bug-blocked from inserting rows (this postmortem)
- Combined effect: 0 forward-observations until ~deploy of this fix; ~22h of remaining forward window after fix; n likely < 30 at verdict cutoff

The Branch C verdict will publish AS A FINDING, not as a failure. The receipts moat compounds when implementation gaps surface as part of the trail. This postmortem is the load-bearing artifact: future readers can verify that we caught the bug pre-verdict, surfaced it publicly, fixed it before the cutoff, and produced honest numbers about what the data ACTUALLY says.

The case study **re-arms automatically** under Subcondition C-iv if Path E produces ≥10 MED in its first 24h post-deploy (likely, per current rate). New `start_at_ts` is computed at re-arm time as `path_e_first_24h_close + 24h = 2026-05-12T06:55:28Z`. Re-armed case study runs against a fully-functional harness with no bug-block.

## Cross-references

- [`case_study_01_gmgn_comparison_prereg.md`](case_study_01_gmgn_comparison_prereg.md) § Amendment 01 — Subcondition C-iv (pre-existing)
- [`finding_8_path_e_pre_registration.md`](finding_8_path_e_pre_registration.md) — Path E that revealed this bug by producing post-trigger MEDs
- [`bucket_calibration_aliasing.md`](bucket_calibration_aliasing.md) § Interim verdict — Variant 5B that scoped Path E
- Memory: `feedback_pre_registration_branches.md` — discipline rules for pre-verdict amendments
- Memory: `feedback_methodology_calls_user_owned.md` — methodology decisions go through user; this fix is a code-level bug fix to make the existing methodology actually run, not a methodology change
