# graduate-oracle backlog

Tracked items that are known-correct-but-deferred. Not for hypothetical future work — for things we've already decided to do but haven't shipped.

## Pre-registered decisions

### Score-latency diagnosis (pre-registered 2026-05-09)

`/api/status.warnings` firing: `score latency avg 11.0s (>5s)` and `score latency p95 18.0s (>8s)`. User direction: diagnosis pass first, no fixes yet; pre-register acceptance criterion before fix ships.

**Headline finding:** steady-state slow, not spike-driven. avg=12s and p95=18s consistent across samples. `rug_predictor` + `gbm_shadow` are dominant (75-85% of per-mint cost). 1289 tracked mints scored per pass; per-mint cost ~170ms.

**Root causes (5 named in writeup):** pure-Python O(N_train) k-NN loop with re-normalization per mint, per-mint sqlite open, per-mint sklearn predict_proba on single sample, per-mint isotonic predict.

**Fix proposal (NOT shipped yet):** Fix A (rug_predictor numpy vectorization), Fix B (batch sqlite pre-fetch), Fix C (gbm_shadow batch predict). Combined expected p95: 18s → ~3s.

**Frozen acceptance criterion:** `score latency p95 < 3s under live load over 24h window` post-deploy. Frozen at this commit; no relaxation without explicit pre-registered amendment per the publish-then-post discipline rule.

**Iteration-limit pre-registered (frozen):** if Fix A+B+C ships and p95 stays >= 3s at 24h:
- Refined retry only if a NEW bottleneck not in Causes 1-5 surfaces (with new pre-reg + new criterion)
- Otherwise Path E: move score_precompute to separate process (decouple latency from snapshot tick) OR score only in-lane subset (~22 mints) instead of all 1289 tracked mints. Both compatible; either is a deeper architecture change. Stop-iterating point.

**Methodology adjustment surfaced explicitly:** I skipped the 30-min per-call instrumentation step because existing telemetry + source inspection produced unambiguous root causes. Standing by for user direction at greenlight time on whether to add instrumentation as confirmation OR ship the fix and verify from post-fix latency. Default recommendation: Path direct-fix (source-level root causes are unambiguous; instrumentation is more useful AFTER unexpected outcome than before).

**Not blocking Case Study 01.** The harness reads sqlite directly, not the scoring path. Parallel diagnosis track.

Full writeup: [`docs/research/score_latency_diagnosis.md`](docs/research/score_latency_diagnosis.md).

---

### Case Study 01 — Calibrated bucket vs component composition (pre-registered 2026-05-08)

First of N planned commercial-comparison studies. Tests empirically whether graduate-oracle's calibrated lane-60s HIGH+MED bucket outperforms GMGN's `--filter-preset strict --type new_creation` server-side composition on the same overlapping mint set.

**Strategic context:** the result determines whether the calibrated-bucket-alone product spec is the empirical differentiator, OR whether the product needs reshaping around what the data shows actually adds value (potentially the receipts-trail discipline itself, not the bucket). Per the user direction: discipline holds in either direction. Same shape as Finding 7i sunset precedent.

**Methodology (frozen, full detail in [`docs/research/case_study_01_gmgn_comparison_prereg.md`](docs/research/case_study_01_gmgn_comparison_prereg.md)):**
- 48h window starting after Finding 8 interim verdict (~2026-05-09T17:00Z)
- 60s polls of both APIs; direct sqlite read on graduate-oracle side (no rate limit); `gmgn-cli market trenches --chain sol --type new_creation --filter-preset strict --raw` on GMGN side
- Per-mint capture: graduate-oracle bucket assignment + feature snapshot; GMGN strict-preset membership + component fields; joint outcome resolution within 24h grace
- Stratified analysis: full overlap, both-products-positive subset

**Hypothesis (frozen):** graduate-oracle MED+HIGH precision ≥ GMGN strict-preset precision + 10pp on n≥30 overlap.

**Three pre-registered branches with templates pre-drafted:**

| Branch | Trigger | Action |
|---|---|---|
| **A — thesis supported** | n≥30 AND lift ≥10pp AND lift consistent on both-positive subset | Ship public writeup + X thread + TG; case study becomes B2B integration prospect artifact; harness configures for Study 02 |
| **B — thesis undermined** | n≥30 AND lift ≤0pp OR \|diff\| <5pp | Ship negative finding writeup + product-reshape discussion (data-anchored); pre-registered iteration-limit triggers product-spec reopen, not "let's run another comparison" loop |
| **C — insufficient sample / ambiguous** | n<30 OR resolution rate <70% | Decision tree: extend collection 48h (one extension) OR extend grace to 72h OR publish inconclusive (addressable-market-shape finding) |

**Pre-drafted templates committed alongside this pre-reg:**
- [`docs/research/case_study_01_gmgn_results_branch_a_template.md`](docs/research/case_study_01_gmgn_results_branch_a_template.md)
- [`docs/research/case_study_01_gmgn_results_branch_b_template.md`](docs/research/case_study_01_gmgn_results_branch_b_template.md)
- [`docs/research/case_study_01_gmgn_results_branch_c_template.md`](docs/research/case_study_01_gmgn_results_branch_c_template.md)

Each template fills in terminal numbers at outcome time; structure, framing, branch-execution scoped pre-data.

**Schedule (frozen):**
- Pre-reg commit: this commit
- Phase 2 (reusable harness scaffold): after pre-reg lands
- Wait window: until 2026-05-09T16:45Z (Finding 8 interim verdict)
- Collection: 48h, ~2026-05-09T17:00Z to ~2026-05-11T17:00Z
- Grace: +24h
- Branch verdict + writeup: ~2026-05-11T18:00Z
- X / TG variant ships: +30min after writeup

---

### Reusable comparison harness (pre-registered scope, 2026-05-08)

Case Study 01's instrumentation is built as the **reusable shape**, not a one-off. Future studies are one config file + one pre-reg writeup + one scheduled run on the same harness.

**Harness design (frozen scope, implementation in Phase 2):**

```
case_study_harness/
  sources/
    grad_oracle.py     # direct sqlite read on production daemon
    gmgn.py            # gmgn-cli wrapper with config-driven filter preset
    <future>.py        # pluggable for Study 02+
  joiner.py            # match mints across sources within ±120s tolerance
  resolver.py          # track outcome over post-prediction window
  run_study.py         # config-driven runner
  configs/
    study_01_gmgn.toml # Case Study 01 frozen config
    study_02_*.toml    # future
```

**Future studies the harness supports** (pre-registered scope; no specific commitments yet):

- **Study 02** — graduate-oracle vs Pump.fun's own analytics (defensive positioning before they decide build-vs-buy)
- **Study 03** — graduate-oracle vs Phantom's wallet-side intelligence (different B2B integration target)
- **Study 04** — longitudinal HIGH/MED bucket precision over 90+ days, published quarterly
- **Study 05** — DEFERRED: post-graduation behavior comparison (sustain field permanently sunset per Finding 7i; this study is currently out of scope, may reopen if a different sustain framing becomes viable)
- **Study 06** — k-NN-historical vs GBM-calibrated head-to-head on the same lane (internal model evolution receipts)

**Lesson banking from Case Study 01 (applies to harness for Studies 02+):** if Branch C fires due to overlap-density or resolution-rate limits, the harness should add an **overlap-density pre-check** at the start of comparison studies — verify viable overlap-per-hour during the first ~6h before committing to a full window. Pre-register feasibility checks, not just acceptance criteria.

### X thread sequence (drafted 2026-05-09; threads ship via @GraduateOracle)

Lane writeup → X thread conversion per outreach_plan.md Channel #5 ("public credibility-building over time; lane writeups are thread-ready"). Each thread's draft commits publicly BEFORE the post goes live (publish-then-post discipline applied to social-media content). All threads close with a "verify yourself" post citing source commit hashes — receipts moat operating at the social-media layer.

**Frozen sequence (all 5 threads drafted as of 2026-05-09):**

| # | Source | Status | When to ship |
|---|---|---|---|
| 1 | Finding 7 chain sunset (subsumes Variant D) — [`docs/research/x_threads/thread_01_finding_7_sunset.md`](docs/research/x_threads/thread_01_finding_7_sunset.md) | Drafted (Post 2 tweak applied) | Anytime; closing-the-loop is fresh now (sunset shipped 2026-05-08 at `7658639`) |
| 2 | Case Study 01 pre-reg (pre-commit-to-negative-findings) — [`docs/research/x_threads/thread_02_case_study_01_prereg.md`](docs/research/x_threads/thread_02_case_study_01_prereg.md) | Drafted | After Thread 1 lands AND trigger fires (~2026-05-09T16:05Z); sequencing matters because Thread 2 references Thread 1's precedent |
| 3 | Lane 1 bundled corpus selection-bias inversion — [`docs/research/x_threads/thread_03_lane_1_bundled_inversion.md`](docs/research/x_threads/thread_03_lane_1_bundled_inversion.md) | Drafted | Day 3-4 of cadence; one thread per week |
| 4 | Lane 13 transition-zone framing (methodology depth) — [`docs/research/x_threads/thread_04_lane_13_transition_zone.md`](docs/research/x_threads/thread_04_lane_13_transition_zone.md) | Drafted | Following week |
| 5 | Finding 8 EMA fix verdict — [`docs/research/x_threads/thread_05_finding_8_verdict_variants.md`](docs/research/x_threads/thread_05_finding_8_verdict_variants.md) | Drafted (3 branch variants A/B/C) | Within 1-2h after Finding 8 verdict commit lands publicly. Variant 5B is the most likely outcome per current data trajectory. |

**Variant D retired:** the single-post sustain-sunset announcement at `docs/research/x_post_draft.md` is superseded by Thread 1. Variant D's body is preserved in the drafts file for receipts-trail integrity (anyone auditing can confirm the option-(a) consolidation decision).

**Cadence rule:** ~one thread per week. Don't blast. Each thread is independent enough to stand alone, but Threads 1 → 2 → 5 form a closing-the-loop arc (sunset → ongoing study → study verdict) that compounds when read in sequence.

**Phase 2 status (2026-05-08): SCAFFOLD COMPLETE; daemon idle awaiting trigger.**

Source tree shipped at [`case_study_harness/`](case_study_harness/) (public mirror of deployed code). Config frozen at [`case_study_harness/configs/study_01_gmgn.toml`](case_study_harness/configs/study_01_gmgn.toml). Trigger logic: daemon polls every 60s until `start_at_ts = 1778342754` (2026-05-09T16:45:54Z = Finding 8 deploy + 48h) passes, then begins 48h collection. After collection: 24h grace window for outcome resolution. Then exits cleanly for analysis.

Output: `/data/case_studies.sqlite` (separate file from production scoring DB) → `case_study_01_observations` table.

**Per pre-reg constraints (frozen):** no live rules toggle; no production scoring code touched; pure read-only instrumentation against production DB + GMGN API. Daemon's effective behavior between scaffold-deploy and collection-trigger: log a heartbeat every ~10 minutes; consume negligible CPU/memory; touch zero files outside its own output DB.

---

### `/status` page consolidation (pre-registered 2026-05-07, defer impl this week)

Today's audit revealed that a B2B prospect or technical reader has to assemble "what's running right now" from `/api/scope` + `/api/accuracy` + the dashboard + research/. **The interim Fix 6 (acceptance-gates panel + dashboard banner) is the first iteration; the full consolidated page is the goal.**

Pre-registered scope for the full `/status` consolidation (impl deferred to this week, after the current acceptance gates close):

1. **Section 1: Deployed model architecture.** Calibrated GBM v1 + isotonic cascade + HIGH/MED/LOW bucket framework, with link to `docs/methodology.md`. Auto-derive from `/api/scope.predictions` + `/api/status.bucket_cutoffs` so the page can't go stale relative to deployed config.
2. **Section 2: Current state of each prediction field.** Pulls from `/api/scope.predictions` calibrated/caveat fields, but renders status pills (✅ live / ⚠️ directional-only / 🔄 acceptance-gate / 🛑 sunset). One row per field. Click-through to the relevant Finding writeup.
3. **Section 3: Active acceptance gates.** Already shipped today (Fix 6). Future iterations: graph the gate's progression — for sustain auto-lift, a corpus-size meter; for Finding 8 interim, a max-hour-MED meter against the 30/hr ceiling.
4. **Section 4: Recent receipts trail.** Last 10 commits to `github.com/Based-LTD/graduate-oracle` rendered with date, message, and link. Auto-pulled via GitHub API at page load. Ships the receipts trail prominently rather than burying it in `docs/research/`.
5. **Section 5: System health (existing).** Uptime, latency, daemon heartbeats — what `/status` already shows.

**Effort estimate:** ~2-3 hours when the current acceptance gates settle.

**Why not now:** the current acceptance gates (Finding 7f sustain auto-lift, Finding 8 interim/full TG re-enable) are the first audience for the panel. Building the full page before they resolve risks rendering a "live state" picture that gets immediately invalidated. Better to land Section 3 (acceptance gates) now (today's Fix 6), demonstrate it works through one full gate cycle, then extend to Sections 1/2/4.

---

### Finding 8 — bucket calibration aliasing (pre-registered 2026-05-07)

While running LOG_THRESHOLD verification + bucket distribution check post-cutover, surfaced that the MED bucket fires in 2-hour spikes (697 in -10h..-9h spike) followed by ~22 hours of zero. HIGH bucket fires zero across full 24h. This is not Poisson variance around `TARGET_MED_PER_DAY=10` — it's a calibration instability. Re-enabling rules 9+10 against this bursty distribution is **worse than the current rules-deactivated state**.

**Hypothesis (H1, primary):** the `raw_gbm_p_high` daemon's 24h cutoff-recompute window produces an aliasing burst — when the cutoff drops at recompute, at-ceiling-cluster mints just-below the old cutoff briefly qualify; new cutoff stabilizes higher within hours; nothing qualifies until the next recompute.

**Diagnostic (committed before any fix):** pull cutoff history (api/status snapshots, daemon logs, or reconstruct from predictions table). Cross-reference 2-hour MED spike timing with daemon rebuild events. ±15 min alignment confirms H1; no alignment opens H2/H3 sub-investigation.

**Acceptance criterion (frozen):**
1. Rolling-7d MED rate within 0.3-3× `TARGET_MED_PER_DAY`
2. No individual hour exceeds 5× per-hour design rate (10 MED/hour cap)
3. Continuous coverage: ≥16 of every 24 hours have ≥1 MED OR are confirmed low-volume (<50 predictions/hour)
4. HIGH bucket within 0.3-3× `TARGET_HIGH_PER_WEEK`

Check runs on rolling 7d window starting 24h after deploy.

**Pre-registered iteration-limit escalation (Path E):** if first fix fails acceptance, EITHER refined-retry-with-new-mechanism (only if diagnostic surfaces something not in H1/H2/H3), OR revert to fixed-percentile cutoffs without volume-targeting. Loses self-stabilization, accepts under-firing, ships consistently. **No fix-N, fix-N+1, fix-N+2 thrashing on calibration logic.**

**Holding state:** rules 9+10 stay deactivated until acceptance criterion passes. /api/scope unchanged until diagnostic confirms a finding worth surfacing publicly.

**Interim 48h TG re-enable gate (sub-pre-registration, 2026-05-07):** an earlier, less strict verdict point at 2026-05-09T16:45Z (48h post-Finding-8-deploy). Acceptance: max hour-level MED ≤30, ≥1 daemon recompute without burst, HIGH any value. Pass → re-enable rules 9+10 with content gate; full 7d gate continues. Fail with burst → immediate Path E. The full 7d criterion is unchanged. See `docs/research/bucket_calibration_aliasing.md` "interim TG re-enable gate" section.

**Interim criterion AMENDMENT (pre-verdict, 2026-05-08):** the original interim criterion conflated EMA-fix-verification with alert-volume verification. Amendment splits them — EMA-fix-verified gate (max 1h MED ≤30, ≥1 recompute without burst, rebuild_failures=0) is one verdict; alert-volume gate (≥1 MED in the 48h window) is a separate gate. Re-enable rules 9+10 only when BOTH pass. If EMA-fix passes but alert-volume fails, do NOT re-enable; pre-register cutoff-recalibration analysis OR trigger Path E early (decision at verdict time based on diagnostic). Amendment commits T+25.93h, verdict at T+48h — ~22h before verdict data resolves the criterion. **Strictly higher bar than original**; not a relaxation. See `docs/research/bucket_calibration_aliasing.md` "interim criterion amendment" section.

**Independent of Finding 7f auto-lift gate.** Different systems; runs in parallel without competing for cycles.

Full pre-registration: [`docs/research/bucket_calibration_aliasing.md`](docs/research/bucket_calibration_aliasing.md).

| Diagnosis | Action |
|---|---|
| **(this entry) Finding 8 pre-registration** | (diagnostic ships next, before any fix) |

---

### Finding 7e — post_grad data-source fix (pre-registered 2026-05-07)

After Path C and Path D2 both failed pre-registered acceptance criteria and Path E (sunset) executed, a 30-min code investigation found the root cause. `post_grad_tracker._loop` reads `observer-active.json` (raw observer output) directly; the enrichment fields `smart_money_in`, `wallet_balance.n_whale_wallets`, `fee_delegation.total_bps` are computed by `_enrich_mint` in the Python web layer at /api/live request time and are NOT in the snapshot file. Sister modules (`early_grad_tracker.py:307`, `mint_checkpoints.py:62`) use the correct pattern — HTTP self-call to `http://127.0.0.1:8765/api/live` — and have **clean** feature data in their respective tables. **Blast radius is confined to `post_grad_outcomes`.**

**Fix decisions (frozen):**

1. **Data source change:** `post_grad_tracker._loop` switches from `with open(snapshot_path) as f` → `urllib.request.urlopen("http://127.0.0.1:8765/api/live?limit=300")`. Mirrors `early_grad_tracker._loop`. No new abstractions. Sister modules' clean data is the proof point.

2. **Corrupted-row handling: Option 2 (filter, not wipe).** `_refresh_training_set` adds `WHERE graduated_at >= FIX_DEPLOY_TS` to exclude the 6,357 zero-feature historical rows from k-NN training. `FIX_DEPLOY_TS` is a module-level constant set to the fix-deploy epoch. Reversible — corrupted rows stay in the table for forensic value (they document the bug) but never enter training. If we later decide to wipe, we can; if we wipe now and find a recovery method, we can't.

3. **`MIN_SAMPLES_FOR_PREDICTION = 30`** per the existing original spec (was at 20 from prior tuning). Clean corpus at ~50/day grad rate → predictor warming exits within hours-to-day, not days.

4. **Auto-lift trigger:** when ≥30 clean post-fix rows accumulate AND Path D2 distance-distribution validation passes (median NN distance ∈ [0.5, 3.0] on 50 live in-lane mints), the sunset auto-lifts. Implementation: predict_survival surfaces `status='warming_clean_corpus_accumulating'` while corpus < 30; after corpus crosses threshold, status flips to `sunset_pending_validation_rerun` until a 1-line follow-up commit flips `LIFT_ENABLED=True` (gated on operator-run validation script success). If validation still fails on clean data, sunset stays — that becomes a different finding (about model architecture, not data plumbing) and the architecture review re-opens with a sharper question.

**What this means for the architecture review:**

The "is the data plumbing broken" question has a one-block answer (the HTTP self-call swap). The remaining architecture question — **"once data is clean, does k-NN actually work?"** — is contingent on Path D2's re-validation against clean data. If the validation passes, the architecture review is fully cancelled. If it fails, the review re-opens with the question reduced to "model choice given clean inputs," which is a 1-hour scoping conversation, not a multi-day study.

**Discipline-pattern observation (worth naming explicitly):** the iteration-limit pre-registration rule (added to `feedback_pre_registration_branches.md` as part of the Path D2/E commit) didn't just stop endless metric-tweaking. It also forced a "investigate root cause for 30 min before committing to multi-day review" check that prevented over-scoping the response. Iteration-limit pre-registration works at the **scope level**, not just the iteration count level. Two halves of "knowing when to stop iterating": stop the failed-fix retry loop, and stop committing to a bigger investigation than the situation actually requires.

**Receipts trail (timestamped sequence):**

| Diagnosis | Action |
|---|---|
| `5296351` Finding 7 layers 7a/7b | (Path C pre-registered) |
| `2d95a5a` Path C pre-registration | (Path C deployed; validation FAILED) |
| `c553d7f` Finding 7c — Path C failed; pre-register Path D2 + Path E | (Path D2 deployed; validation FAILED) |
| `707c169` Finding 7d + Path E execution receipt | (sunset shipped to prod) |
| `45fb3b9` Finding 7e — HTTP self-call fix pre-reg | (deployed; verification surfaced fix mechanically wrong — see 7f below) |
| `c3a83ef` Finding 7f — corrected fix + retraction | (deployed) |
| `ea6d5f5` Finding 7f — validation deferred (CRIT 1 PASS small-corpus) | Re-validation at n≥60 + 3 sigs OR 72h cap |
| `f3f1f3e` Finding 7g — re-validation at n=901 FAILS CRIT 1; clean-data hypothesis rejected | Architecture review reopens; iteration-limit at model-class level |
| **(see post_grad_metric_broken_since_launch.md) Finding 7h — calibrated LR + interactions, one-shot architecture attempt; frozen criteria** | Pre-registration ships first; experiment runs after commit lands; ship-or-sunset per branches |

**Finding 7h pre-registration summary** (full detail in writeup):

- **Model:** calibrated logistic regression with explicit binary-binary and binary-continuous interaction terms (15-feature vector). L2 penalty default; isotonic calibration on out-of-fold predictions.
- **Protocol:** stratified 5-fold CV on n=901 frozen corpus snapshot. Random seed 42.
- **Frozen criteria:**
  - CRIT 1: (0,0,0) base rate convergence ±5pp (`r_000 = 0.4935`)
  - CRIT 2: Minority-signature (n≥30; only (1,1,0) qualifies) Brier improvement ≥10pp over per-signature baseline (`r_(1,1,0) = 0.3925`)
  - CRIT 3: Coverage ≥95% on live in-lane sample (≥50 mints)
- **PASS branch:** ship as conditional sustain — (0,0,0) returns baseline, non-(0,0,0) returns calibrated LR. `LIFT_ENABLED` flips True. Public framing: "we predict sustain when signature signal exists; we don't pretend when it doesn't."
- **FAIL branch:** permanent sunset. Status `'sunset_lane_60s_structural_limit'`. Three-attempt structural-boundary verdict documented. Dashboard sustain card removed.
- **Strategic context (frozen):** sustain is upside, not required. Bias toward strict criteria. No softening at the margin.
- **Iteration-limit:** ONE attempt only. FAIL = permanent sunset; no further model-class iterations.

**Finding 7h experiment result (2026-05-08)** — see `docs/research/post_grad_metric_broken_since_launch.md` for full detail:

- **CRIT 1 PASS** — `p_000 = 0.4942` vs `r_000 = 0.4935`; |diff| = 0.0007 ≪ 0.05 tolerance.
- **CRIT 2 FAIL DECISIVELY** — Brier improvement = **-0.0122** (model 1.22pp WORSE than per-signature baseline) vs threshold ≥+0.10. Failed by ~11.22pp from threshold.
- **CRIT 3 PASS** — 14/14 in-lane mints got numeric predictions (sample below n≥50 target due to thin live traffic at run-time, but verdict unambiguous at 100%).
- **OVERALL: FAIL → Finding 7i permanent sunset executes.**

**Finding 7i — permanent sunset (executed 2026-05-08):**

`predict_survival` permanently returns `{prob: null, status: 'sunset_lane_60s_structural_limit'}`. Aggregate `post_graduation.sustain_rate_30m` on `/api/accuracy` continues unaffected. Three model-class attempts (Path C, Path D2, Path 7h) all failed pre-registered acceptance with consistent mechanism: lane-60s sustain prediction is not viable from the available 5 features given the signature distribution of resolved graduates. Iteration-limit at model-class level fired correctly. **Finding 7 chain complete (7a → 7i).**

---

### Finding 7f — Finding 7e fix retracted; mint_checkpoints JOIN approach pre-registered (2026-05-07)

The Finding 7e fix (`45fb3b9`) deployed cleanly but post-deploy verification revealed it was mechanically wrong. **The HTTP self-call to /api/live returns 0 graduating mints** because `/api/live`'s response window is the prediction lane (≤60s), not the post-graduation moment (vsol≥115 typically happens at age >> 60s).

Sister modules (`early_grad_tracker`, `mint_checkpoints`) work because they capture features at **age-checkpoints (15s, 30s, 60s)** when mints ARE in /api/live. `post_grad_tracker` was attempting to capture at **graduation moment**, which is a different lifecycle window. The sister-module pattern doesn't transfer mechanically.

**Verification-by-content gap (caught the fix being insufficient):** the Finding 7e investigation correctly identified the snapshot-source bug, but skipped manually verifying that graduating mints actually appear in /api/live. A 30-second curl test (`grep vsol >= 115`) would have shown zero immediately. **Verification-by-content applies at deploy time too, not just at fix-claim time.** This is the third instance of the same meta-pattern (confirming structure isn't confirming substance) — being added to `feedback_pre_registration_branches.md` as a generalization of the rule.

**Pre-registered fix decisions (Finding 7f, frozen):**

1. **Revert `_loop` data source:** back to `observer-active.json`. The snapshot file DOES include graduating mints; that's where graduation detection has to live.

2. **Replace `_record_graduation` feature extraction:** JOIN `mint_checkpoints` for the mint's latest checkpoint row. mint_checkpoints captures features cleanly at age-checkpoints with the correct enriched data path; verified clean on prod (smart_money 0-4+, n_whales 0-4+, fee_delegated 0/1).

3. **`_features_from_checkpoints(mint)` helper** queries mint_checkpoints; returns latest checkpoint's features, falls back to at-graduation snapshot extraction if mint has no checkpoint (rare edge case for mints that graduate before age 15s).

4. **FIX_DEPLOY_TS bumped to Finding 7f deploy moment.** Existing pre-7f rows (including the 12 zero-feature rows from the 7e attempt this morning) filter out of training. Training corpus rebuilds from clean 7f rows onward.

5. **Auto-lift gate retained:** `LIFT_ENABLED=False` until operator runs validation script against clean 7f corpus and acceptance criteria pass.

**Edge case (noted, not blocking):** mints that graduate before age 15s have no mint_checkpoints entry. They'll fall back to the at-graduation snapshot, which has 3 zero-fields. Surfacing them lets us measure their fraction; future architecture work can decide whether to drop or model separately.

**What this finding demonstrates about the receipts moat:** a discipline trail with publicly-retracted fixes is more trustworthy than one without. Each correction explicitly owns "the previous attempt was wrong, here's specifically why" — that's the discipline working at peak strength, not failing. **Nine iterations of pre-fix-then-fix in 72 hours; two of nine were corrections of prior fixes.**

---

### Finding 7 — Path C metric replacement (pre-registered 2026-05-07)

After diagnosis at `5296351`, the post_grad k-NN distance metric is being replaced. Single deploy after this commit; verification per pre-registered acceptance criterion before re-enabling sustain rendering.

**Hypothesis:** replacing max-scaling with std-dev (z-score) scaling in the post_grad k-NN distance metric produces a metric where dimensions contribute proportionally to their information content, eliminating the smart_money-dominated pathology.

**Method:**

```python
# Old (max-scaling, broken):
scales = (max(smarts), max(whales), max(buyers), max(velocity), 1)
# Produces (1, 1, 1632, 1949, 1) — dimension imbalance

# New (z-score scaling):
scales = (stdev(smarts), stdev(whales), stdev(buyers), stdev(velocity), stdev(fee_delegated))
# All dimensions contribute proportionally to spread/information content
```

Each dimension's contribution to distance becomes `(raw_difference / std_dev)²`. High-spread dimensions contribute more; low-spread dimensions contribute less but aren't drowned out.

**Pre-registered acceptance criterion (frozen):** post-fix, sample 50 live in-lane mints, compute mean nearest-neighbor distance for each. Verify:

- **Distribution spans 0.5-3.0 typical range:** tight matches sit near 0.5-1.0, genuinely loose matches sit at 2.0-3.0+. Median across 50 samples should fall in this range.
- **No collapse to zero or explosion to >10:** if samples cluster below 0.1 or above 5, the metric still has issues.

**Decision rule (post-fix):**
- Distribution in 0.5-3.0 range → metric is sane; loose-match threshold (75th percentile) becomes meaningful; re-enable sustain rendering with proper warming gate; update /api/scope to reflect post-fix state.
- Distribution outside range → metric still broken; re-investigate (possibly different normalization scheme — robust statistics like IQR, log-transform on heavy-tailed dimensions). Re-pre-register before any further change.

**Interim state (in tree, deploys with this commit pair):**
1. `post_grad_survival_prob` returns `{prob: null, status: 'metric_recalibration_in_progress'}` for ALL live mints during the fix window
2. Bot alert template + dashboard skip rendering sustain when status indicates recalibration
3. `/api/scope` description updated: `calibrated: "directional only — distance metric being recalibrated as of 2026-05-07"` + caveat naming Finding 7
4. Memory file `project_watch_grad_vs_runner` corrective single-line update

**Time bound:** ~30 min implementation + ~30 min verification. Sustain rendering stays suppressed until acceptance criterion passes.

**Frozen at this commit. No revising acceptance ranges downward without re-pre-registration.**

### Sixth-finding fixes — pre-registered before implementation (2026-05-07 morning)

After diagnosis at `597b5ab` (sharpened from `ce7a38b`), the four fixes ship together as a single combined deploy. Each pre-registered separately so the discipline pattern's "decide criteria first, apply fresh" property holds. Rules 9 + 10 stay deactivated until verification gate passes.

#### Fix 1 — snapshot field expansion (no pre-registration needed; pure additive)

`web/alert_push.py:_SNAPSHOT_FIELDS` extended with: `grad_prob_bucket`, `grad_prob_gbm_calibrated`, `grad_prob_gbm_shadow`. Lets future content inspection see what fired. Tiny code change. No behavior change for rule firing — purely improves auditability of `pending_alerts.snapshot_json`.

#### Fix 2 — input-quality gate in `bucket_for()` (pre-registered 2026-05-07 morning)

**Hypothesis:** the bucket-MED flood includes a meaningful fraction of degenerate inputs (1-buyer fresh mints, near-zero vsol growth) that should be filtered before bucket assignment, not after. Filtering at the bucket-assignment layer prevents downstream alert noise from inputs the model has no signal on.

**Method:** `bucket_for(calibrated_prob, raw_gbm_prob, m_out)` extended to accept the m_out dict. Returns "LOW" when ANY of:
- `unique_buyers < 3` (insufficient buyer diversity to support prediction)
- `n_trades < 5` (insufficient trade history for feature stability)
- `vsol_growth_sol < 1.0` (mint hasn't moved meaningfully past launch)

**Rationale:** these thresholds define "degenerate input" — the model has no signal to act on. Bucket assignment on degenerate inputs produces noise alerts, not predictions. The gate is a precondition check, not a model tuning. Frozen at this commit.

**Decision rule (re-evaluation at +30 days):**
- MED fire rate stays >2× design target (>20/day) post-gate → tighten further (e.g., `unique_buyers < 5`). Re-pre-register.
- MED fire rate drops below 5/day post-gate → loosen (e.g., `unique_buyers < 2`). Re-pre-register.
- 5-20/day → frozen criteria pass; close the iteration.

**Time bound:** ~30 min implementation + tests. Pairs with Fix 4 (volume calibration) — neither alone is sufficient.

#### Fix 3 — post_grad_survival_prob warming-on-loose-match (pre-registered 2026-05-07 morning)

**Hypothesis:** post-grad k-NN on degenerate-input feature vectors finds 8 neighbors that all happen to sustain because the matches are loose (any random 8 mints). The output `prob: 1.0, status: live` is meaningless on such inputs. A distance-quality gate makes the predictor return `status: warming` when the neighborhood is incoherent.

**Method:** `predict_survival()` returns `status: warming, prob: null` when the mean of the 8 nearest-neighbor distances exceeds a frozen quality threshold. Threshold derived from the empirical distribution of nearest-neighbor distances on the existing training data:
- **Frozen threshold:** distance threshold matches the 75th percentile of nearest-neighbor distances on the training set
- **Rationale:** "predictions above this threshold lack a coherent neighborhood" — the model can't distinguish meaningful matches from background noise

**Output when warming:** `{prob: null, n_neighbors: 8, status: 'warming', mean_distance: X}`. Alert template skips rendering sustain when `status='warming'`.

**Decision rule (re-evaluation at +30 days):**
- Warming-rate >50% of in-lane mints → threshold too tight; relax to 90th percentile. Re-pre-register.
- Warming-rate <5% AND degenerate inputs still showing `prob: 1.0` → tighten to 50th percentile. Re-pre-register.
- 5-50% warming-rate AND no pathological 100%-on-degenerate cases → frozen criteria pass.

**Time bound:** ~45 min implementation including the empirical threshold derivation + tests.

#### Fix 4 — volume-target-driven cutoffs (pre-registered 2026-05-07 morning)

**Hypothesis:** fixed percentile cutoffs (raw_gbm_p97) applied to whatever production volume comes in produces unstable alert rates. Inverting the relationship — derive percentile from a target alert rate — produces self-stabilizing cutoffs that adjust as production volume changes.

**Method:** `bucket_cutoffs.rebuild()` switches from "compute fixed percentiles" to "compute percentiles that hit fixed alert-rate targets":
- **HIGH target:** ~5/week → percentile that produces 5/week given trailing 7d volume
- **MED target:** ~10/day → percentile that produces 10/day given trailing 7d volume
- Volume = trailing 7d in-lane scoring rate (NOT just persisted predictions; account for LOG_THRESHOLD-fix-expanded persistence)

**Rationale:** alert rate is the quantity that matters for product behavior. Volume-adjusted cutoffs self-stabilize as production volume drifts. The targets become the frozen values; the percentiles are derived.

**Decision rule (post-deploy validation, applied at +7 days):**
- HIGH 0.3-3/day rolling 7d AND MED 1-30/day → clean. Hold targets.
- HIGH or MED systematically off design target by >2× sustained over 7d → adjust targets (not percentiles). Re-pre-register if change exceeds ±50%.
- Target volumes interact with Fix 2 (input-quality gate): if Fix 2 filters too aggressively, volumes drop and percentiles loosen automatically. If Fix 2 filters too loosely, volumes rise and percentiles tighten automatically. The two fixes interact correctly by construction.

**Time bound:** ~30 min implementation + tests.

### Combined-deploy verification gate (frozen criteria, applied fresh post-deploy)

Before re-activating rules 9 + 10, the combined fix must pass:

1. **Re-enable rules 9 and 10** in production
2. **Wait 4-8 hours** for production volume to surface
3. **Pull `tg_fires` audit + sample 10 actual fires** with full snapshot inspection
4. **Verify all five conditions:**
   - HIGH fire rate within 0.5×-2× of 5/week target (i.e., 0-2 in 4-8h is acceptable; >2 means HIGH gate is too loose)
   - MED fire rate within 0.5×-2× of 10/day target (i.e., 1-7 in 4-8h is acceptable; >7 means MED gate is too loose)
   - Each sampled alert's snapshot shows bucket assignment matching the rule (post-Fix-1, snapshot fields are populated)
   - Each sampled alert's content reads sensibly (no "0% graduate · 100% sustain" pathology — Fix 3 should produce status:warming on those, suppressing the sustain render)
   - Each sampled alert's mint has features justifying the bucket assignment (post-Fix-2, no 1-buyer fresh-mint pathology — those should be LOW)

5. **User confirms** content reads as useful, not noise. (Subjective gate — alert UX is what users actually act on.)

**If all five pass:** X post can go out with the genuinely-true claim "alerts switched to HIGH/MED/LOW buckets calibrated to live rates." Sixth fix marked done.

**If any fail:** another iteration. Another pre-fix-then-fix cycle. X post stays held. The receipts trail extends; that's the discipline pattern, not a failure.

### Sustains-gate validation criterion (pre-registered 2026-05-04)

When `post_grad_survival_prob` has been logged on ≥30 fires under the current rule, evaluate whether to add it as a hard suppression gate on WATCH alerts. The criterion was set BEFORE measurement to prevent post-hoc fitting.

**Gate validates and ships** if all of:
- The `sustains ≥ 0.5` group has post-bond runner rate (`peak_mult_from_entry ≥ 2.0` within 30 min of graduation) **at least 2× higher** than the `sustains < 0.5` group
- Each bucket has **n ≥ 15** resolved fires
- Holds at the same significance threshold on a clean re-run after the post-backfill corpus matures

**Gate fails and stays display-only** if either bucket has n<15 (insufficient data for either direction), or the rate ratio is <2×.

Intermediate result (1.5×–2× ratio with n≥15 in each bucket): hold for another 30 fires before deciding.

Source: `project_watch_grad_vs_runner.md` memory + 2026-05-04 chat. The 2× ratio comes from the survivorship-bias rule: a marginal gate that would suppress 49% of fires for a 1.1× edge is worse than no gate.

**Survivorship-bias rule applies:** this criterion is the criterion regardless of who's at the keyboard when n=30 is hit. No revising downward after seeing the result.

## Engineering debt

### Isotonic calibration squashes upper-tail signal to ceiling — investigate post-Path-E (opened 2026-05-10)

**Status:** investigative scope only; **NO iteration-limit attached**. Any actual fix attempt requires a fresh pre-registration with its own iteration-limit, separate from this ticket.

**Background.** Finding 8 Path E (pre-registered 2026-05-10, see [`docs/research/finding_8_path_e_pre_registration.md`](docs/research/finding_8_path_e_pre_registration.md)) sidestepped a known-bad isotonic calibration step by computing bucket cutoffs on raw GBM scores instead of post-isotonic calibrated scores. Path E is the working baseline; this ticket exists so the underlying calibration bug doesn't get buried under that ship.

**The bug, with empirical receipts.** Snapshot from 2026-05-10 (n=5263 over 48h post-Finding-8-deploy window):

| Field | min | median | p90 | p95 | p97 | p99 | max |
|---|---|---|---|---|---|---|---|
| `grad_prob_gbm_shadow` (raw GBM) | 0.0155 | 0.2988 | 0.4586 | 0.5176 | 0.5542 | 0.6197 | **0.8855** |
| `grad_prob_gbm_calibrated_shadow` (post-isotonic) | 0.0000 | 0.0405 | 0.0806 | 0.1132 | 0.1132 | 0.1132 | **0.1132** |

The raw GBM has clean upper-tail distribution (max 0.886). The isotonic-calibrated output is hard-capped at 0.1132 with **7.2% of mints** (379 of 5263) sitting at exactly that ceiling value. The calibration is squashing real upper-tail signal that exists in the raw GBM output.

This is not analogous to Finding 7's structural-boundary case (Finding 7 had 3/5 training columns at zero values since launch — a data-plumbing dead-end). Finding 8's calibration produces a degenerate output OVER non-degenerate input. That's a calibration-design or calibration-training bug, not a no-signal bug.

**What an investigation would look at.** Non-exhaustive starter list:
- Isotonic regression training set size and shape — is it overfitting a small calibration corpus that doesn't include the upper raw-GBM tail?
- Whether the isotonic step is being applied to a feature space the calibration model wasn't fit on (e.g., raw GBM scoring distribution shifted post-retrain but the isotonic calibrator is from before)
- Whether monotone-constraint enforcement in isotonic is collapsing the upper tail because of insufficient calibration anchor points at high raw scores

**Discipline notes:**
- This ticket has no scheduled work date. It pulls when (a) a future Finding 8 sub-iteration needs to revisit the calibration, OR (b) operational pressure permits investigative work that isn't on the active ship list.
- Any fix attempt MUST start with a fresh pre-registration: hypothesis, methodology, frozen acceptance criterion, iteration-limit. Touching the isotonic step without that scaffolding risks a fix-N+1 chain on calibration math, which is exactly the iteration trap Path E was the pre-registered escalation against.
- If the investigation surfaces "the calibration is fundamentally underdetermined for this raw distribution," that becomes the closing-the-loop verdict and the calibration step is documented-as-permanently-degenerate (the upstream signal is what we surface; calibration becomes a post-processing convenience for downstream consumers, not a load-bearing layer in bucket emission).

### Add `activated_at` column to `tg_alert_rules` ✅ done 2026-05-04

Schema migrated in [web/db.py](web/db.py) (ALTER TABLE + backfill from created_at). Bot rule INSERTs now write activated_at = now(). Cutoff queries in [web/main.py](web/main.py) (act_slice, audit) and [web/gate_validation.py](web/gate_validation.py) switched from `MIN(created_at)` to `MAX(activated_at)`. WARNING comments removed at all three sites.

If a future code path ever toggles `active=0→1` on an existing row, that path MUST update `activated_at = strftime('%s','now')` in the same statement. The bot's current pattern (INSERT-new + UPDATE-old-to-active=0) doesn't re-activate, so the invariant is currently easy to maintain — just don't introduce a re-activation path without writing activated_at.

### Multi-rule cutoff: replace MAX(activated_at) with a per-rule correlated subquery

`MAX(activated_at)` is correct only while there is exactly one active grad_prob rule. The day a second active grad_prob rule lands, MAX silently truncates good fires from the older rule (anchors on the newer rule's activation timestamp, but a fire from the older rule that happened before the newer rule's activation was correctly produced and should still count).

Switch to per-rule judging: each fire compared against ITS rule's activated_at, e.g. `EXISTS (SELECT 1 FROM tg_alert_rules r WHERE r.id = tg_fires.rule_id AND r.active = 1 AND r.activated_at <= tg_fires.fired_at)`. For act_slice (which queries the predictions table, not tg_fires directly), join through tg_fires by mint or run a parallel subquery. Three sites need the change:
- [web/main.py act_slice](web/main.py)
- [web/main.py audit](web/main.py)
- [web/gate_validation.py](web/gate_validation.py)

Inline comments at all three sites flag the SINGLE-RULE-ONLY assumption so a future engineer adding rule 9 sees the warning before the silent truncation lands.

## R&D — pre-registered hypotheses (2026-05-04)

These are exploratory analyses run while the model cooks. **None of these can become the gate-validation criterion** (that's frozen against the current sustains feature in the section above). Negative results get written to `docs/research/` so the trust moat compounds.

### Lane 1 — bundled-pump corpus check ✅ done 2026-05-04 evening

- **Hypothesis:** ≥90% of resolved post-grad outcomes are `manufactured_pump=1 AND bundle_detected=1`. If true, the WATCH model isn't a generalized graduation predictor — it's specifically a bundled-pump-graduation predictor, and the corpus reflects pump.fun's actual graduation pool, not a model bias.
- **Method:** Join `post_grad_outcomes` to `predictions` on mint. Stratify by `(manufactured_pump=1 AND bundle_detected=1)` vs `NOT`. Report four-cell table: count per cell + sustained_30m rate per cell.
- **Decision thresholds:**
  - **≥90% bundled** → pump.fun graduation pool IS bundled. Product framing is "we predict bundled-pump graduations." Unlocks Lane 2 (feature importance within bundled) + Lane 5 (feature engineering: holder-concentration trajectory, inter-bundle intervals, time-to-grad, creator skin-in-game).
  - **<80% bundled** → real selection bias somewhere. Unlocks Lane 3 (control-group base rates by time-of-day) + investigation: why is the observer/model underrepresenting non-bundled graduations?
  - **80-90% bundled** → ambiguous, hold; rerun in 1 week with more data.
- **Outcome handling:** writeup to `docs/research/lane1_bundled_corpus.md` regardless. Tier 2 lanes gate on the result.
- **RESULT:** 13.4% bundled (n=398 of 2,975 classified). Hypothesis REJECTED. Pump.fun's graduation pool is 87% non-bundled. Non-bundled sustain at 53.1% vs bundled 31.7%. Model has selection bias — finding only the worse-sustaining 13% slice. Decision per pre-registered <80% rule: unlocks Lane 3 + selection-bias investigation. Writeup: [docs/research/lane1_bundled_corpus.md](docs/research/lane1_bundled_corpus.md).

### Lane 2 — GBM on full joinable set, stratified by bundle_detected (formally pre-registered 2026-05-05 afternoon, sharpened mid-afternoon)

**Status:** ACTIVATED. Original gating rule (Lane 1 ≥90% bundled) is moot — Lane 1's <80% finding made the question broader, not narrower. Train on the full joinable set, stratify feature importance by `bundle_detected`, sanity-check with L1 logistic regression.

**Sharpened spec (this is the binding version):**
- **Train target:** `sustained_30m` (the trader-relevant outcome). NOT `actual_graduated` — graduation alone isn't the question that matters anymore.
- **Sample:** post_grad_outcomes joined to predictions on mint, with `manufactured_pump` and `bundle_detected` non-NULL. **n ≈ 2,975** (Lane 1's joinable subset).
- **Models to train:**
  - **GBM** (LightGBM) — primary
  - **L1-regularized logistic regression** — sanity check. If GBM and L1-LR produce materially different feature rankings, something interesting is happening (interactions or non-monotone effects). Both should agree on the high-level signal direction.
- **Output (three rankings):**
  - Per-feature importance for the FULL sample
  - Per-feature importance for the BUNDLED subset (`manufactured_pump=1 AND bundle_detected=1`, n≈398)
  - Per-feature importance for the NON-BUNDLED subset (n≈2,577)
- **Comparison to k-NN:** same labels (`sustained_30m`), same train/test split (80/20 held-out, stratified). Compare AUC + log-loss on held-out 20%.
- **Decision threshold for "GBM beats k-NN":** **≥3% AUC improvement on held-out.** Below that, k-NN's simplicity and online-update properties win.
- **Decision rule for stratified rankings:**
  - **Top-5 overlap ≤ 2** between bundled and non-bundled rankings → "different signal mechanisms" → retrain architecture should support per-population specialization (two sub-models, or interaction terms)
  - **Top-5 overlap ≥ 4** → "same signal, different magnitude" → retrain just needs richer features in a single model
  - **Top-5 overlap = 3** → ambiguous, hold

- **Hypothesis (primary):** a gradient-boosted-tree model trained on the full 4,256 resolved post-grad outcomes (sustained_30m as label) using existing 6 features + Lane 6's 17 unused features will identify which features drive the runner-vs-rug split, and the feature-importance ranking will differ between bundled and non-bundled populations.
- **Hypothesis (secondary, GBM/k-NN architecture comparison):** the GBM materially outperforms the current k-NN on the same labeled set, on the non-bundled subset specifically. If true, GBM becomes a candidate for replacing k-NN at retrain time.
- **Method:**
  - Pull all 4,256 resolved outcomes from `post_grad_outcomes` (`sustained_30m IS NOT NULL`)
  - Join to `predictions` for the existing model's features at age 30/60s + the bundle/manufactured flags
  - Augment with Lane 6's unused features where available (most are observer-derived; some require feature back-extraction from curve files for rows without a predictions row)
  - Train ONE GBM on the full set with `sustained_30m` as label
  - Train SECOND GBM with `actual_graduated` as label (the current model's task) for direct architecture comparison vs k-NN
  - Stratify feature importance by `bundle_detected` IN/NOT-IN, producing two rankings:
    - "What features predict sustain in the BUNDLED population" (improves the current narrow product)
    - "What features predict sustain in the NON-BUNDLED population" (what the model needs to find the missing 87%)
  - Evaluate via stratified k-fold, report per-subpopulation calibration
- **Decision thresholds:**
  - **Rankings differ materially** (top-5 features in bundled vs non-bundled overlap by ≤2 features): **bundled and non-bundled have different signal mechanisms.** A single combined model will underfit at least one population. Implication for retrain: either two specialized sub-models (route by bundle_detected at score time) or a single model with explicit bundle×feature interaction terms. Pre-register architecture choice before retrain.
  - **Rankings are similar** (top-5 overlap ≥4 features): same signal mechanism, the retrain just needs richer features — same architecture, better inputs. Lower complexity retrain.
  - **GBM beats k-NN by ≥10pp** on non-bundled-subset calibration at threshold ≥0.5: GBM is a candidate to replace k-NN. Pre-register the deploy criterion at retrain scoping time.
  - **GBM doesn't beat k-NN materially:** k-NN with cleaner inputs is sufficient; architecture isn't the lever.
- **Outcome handling:** writeup to `docs/research/lane2_gbm_stratified.md`. Both rankings published. Negative architecture comparison published.
- **No-ship rule applies:** the GBM doesn't ship from this experiment. It's a proof-of-concept that informs retrain scoping. Any deploy decision goes through fresh pre-registration with frozen criteria.

### Lane 4 — smart-money post-grad correlation ❌ DROPPED 2026-05-04 evening

**Status:** dropped, not deferred.

**Why:** the smart-money correlation question was load-bearing under the noon framing ("trader product is unproven, need orthogonal confirmation signal"). Under the post-Lane-1 framing ("we're missing 87% of the graduation pool — fix that first"), the leverage shifted. Smart-money confirmation as a runtime second-stage gate is still a valid future hypothesis, but it's lower priority than the retrain that addresses the actual selection bias.

**What survives:**
- Pre-registration spec stays in this file as historical record
- [docs/research/lane4_smart_money_post_grad.md](docs/research/lane4_smart_money_post_grad.md) — agent's "what blocked" writeup (sandbox lacked the credentials for live DB pull and the Solana RPC POST volume needed). The methodology and re-execution requirements are documented for any future pickup.
- Whoever revives this picks up where the agent stopped, runs against fresh data, and pre-registers any updated criterion at that time.

### Lane 4 (original, dropped) — smart-money post-grad correlation (background, ~6h)

- **Hypothesis:** post-bond runners (graduated, peak ≥ 2× from grad price within 30m on PumpSwap) have **at least 2×** the rate of smart-money wallet entries in the 0-30m post-grad window vs post-bond rugs.
- **Method:** For each graduated mint in the last 7 days, classify as runner/rug via `post_grad_outcomes.price_30m_usd / grad_price_usd ≥ 2.0` (upper-envelope across price_5m/15m/30m). Pull post-grad on-chain transactions per mint via a Solana RPC provider. Cross-reference buyer wallets against the curated 84-wallet smart-money index. Compute: mean count of distinct smart-money entries per runner, mean per rug, ratio.
- **Decision thresholds:**
  - **Ratio ≥ 2× AND n ≥ 15 in each bucket** → confirmation signal exists, orthogonal to grad_prob and sustains. Pre-register a future two-stage gate (WATCH + smart-money post-grad confirmation) as a separate validation hypothesis. **Does NOT ship in this 2-4 week window** — R&D output, not a gate amendment.
  - **Ratio 1.5×-2×** → marginal, hold for 30 more samples.
  - **Ratio < 1.5×** → no signal, drop the angle.
- **Outcome handling:** writeup to `docs/research/lane4_smart_money_post_grad.md` regardless. Includes the negative result if ratio < 1.5×.

### Lane 6 — computed-but-unused feature audit ✅ done 2026-05-04 evening

**RESULT:** 17 features available-but-unused (>5 strong-signal threshold). Top 3 candidates: `max_mult`, `vsol_acceleration`, `top3_buyer_pct + repeat_buyer_rate`. Writeup with full table + priors: [docs/research/lane6_unused_features.md](docs/research/lane6_unused_features.md).

Several of these features are exactly what's needed to address Lane 1's selection bias (`unknown_buyer_pct`, `low_history_pct`, `n_smart_in`, `sell_ratio` plausibly separate non-bundled graduators from non-bundled rugs). Retrain feature list now has a strong-prior input.

### Lane 6 (original spec) — computed-but-unused feature audit (background, ~30 min)

- **Hypothesis:** ≥3 features are computed by the observer or enrichment pipeline and stored on `m_out`/predictions/post_grad_outcomes but NOT in the 6-element k-NN feature vector that `score_full()` consumes.
- **Method:** Walk web/main.py:_enrich_mint, web/predictions.py schema, src/observer.rs ActiveMintSummary, post_grad_tracker feature columns. Enumerate every field. Cross-reference against `_normalize()` in grad_prob.py (the 6 features). Output: list of "available but unused" with type + source.
- **Decision thresholds:**
  - **>5 features available** → strong signal that retrain has obvious quick wins. Pre-register each as a separate validation hypothesis at the time we revisit retrain.
  - **2-5 features** → moderate; worth sequencing into the retrain plan.
  - **<2 features** → model already uses what's available; the leverage is in new features, not enabling existing ones.
- **Outcome handling:** writeup to `docs/research/lane6_unused_features.md`. **Strict no-ship rule applies** — listing features doesn't mean enabling them; each candidate gets pre-registered separately at retrain time.

## R&D — Tier 2 (unlocked by Lane 1, NOT YET RUN — pre-registered for tomorrow)

These two pre-registrations were created tonight, ahead of execution, while the Lane 1 reframe was fresh. They are NOT to be run in the same session that produced Lane 1's finding — fresh session, fresh review, follow the discipline contract.

### Lane 3 — control-group base rates by time-of-day

- **Hypothesis:** the model's selection bias against non-bundled graduators is NOT uniform across time of day. Specifically: non-bundled graduations cluster in some hour-of-day windows that the model's calibration may underrepresent.
- **Method:** Stratify the 2,577 non-bundled graduations + 398 bundled graduations by `hour_of_day(graduated_at, UTC)`. Compute, per hour:
  - n graduations (both buckets)
  - non-bundled share of graduations
  - sustain rate per bucket
  - rate at which the live model produced a high-confidence (≥0.7 grad_prob) call for mints graduating in that hour
- **Decision thresholds:**
  - High-conf-call rate is **uniform across hours** (within ±20% of overall mean): selection bias is NOT time-of-day-driven; rule out the time hypothesis. Move to feature-vector-bias investigation.
  - High-conf-call rate **varies materially by hour** (some hours <50% of mean): time-of-day is a real factor; investigation forks into "why some hours produce mints the model misses" — could be observer load, pump.fun activity bursting bot launches into specific windows, etc.
- **Outcome handling:** writeup to `docs/research/lane3_time_of_day.md`. Even null result (uniform) is a meaningful finding — rules out one hypothesis cleanly.

### Lane 9 — curve-replay feature engineering retrain ✅ done 2026-05-05 evening

**RESULT:** Non-bundled AUC = **0.7413** (vs Run B baseline 0.5997 on same sample). **+14.16pp gap closure on non-bundled, far above the ≥5pp threshold.** Decision per pre-registered rule: **retrain is justified**, implementation scoping is the next phase.

Top-importance features (matching Lane 6's priors):
- `max_mult_at_age` (peak so far) — non-bundled rank #1
- `vsol_velocity_60s` (60s momentum) — non-bundled rank #2
- `top3_buyer_pct`, `sol_spent_first_2s`, `dust_buy_rate` — secondary

Notes for retrain implementation:
- Bundled subset regressed -5.9pp (single-model retrain may modestly degrade bundled performance — flag for two-model investigation, but n_te=65 so could be noise).
- Top-5 ranking overlap = 3 (ambiguous between same-signal vs different-signal). Re-evaluate per-population specialization at retrain implementation time.
- 35% no-curve gap from extraction — observer collection leak (Lane 1 H_collection finding) caps achievable training set.
- Lane 9 model AUC 0.7413 EXCEEDS Run A's 0.723 ceiling — curve-replay features at age 30/60 carry MORE information than the post-grad snapshot features Run A leaned on. Unexpected; means the trader-relevant features are entirely accessible at predict time.

Writeup: [docs/research/lane9_curve_replay_retrain.md](../docs/research/lane9_curve_replay_retrain.md).

### Lane 9 (original spec) — curve-replay feature engineering retrain (formally pre-registered 2026-05-05 evening, ~3h budget)

Lane 2's two runs bracketed the answer: 0.74 AUC ceiling with full features (Run A, includes post-grad snapshot columns), 0.64 AUC floor with schema-only (Run B). Top-5 ranking overlap = 4 in both → "same signal, richer features needed." Lane 9 closes the gap: extract Lane 6's score-time-available features from curve files at age 30/60, retrain, see how much of the 10pp AUC gap closes.

- **Hypothesis:** Lane 6 features extracted at age 30/60 from curve replay close **≥5pp of the 10pp non-bundled AUC gap** between Run B (0.624) and Run A (0.723) — i.e. retrain non-bundled AUC ≥ 0.674.
- **Sample:** rows from Lane 2's 4,849-row dataset where the mint has a curve file in `/data/observer-curves`. Lane 1 noted ~67% join coverage with predictions; coverage of curves-given-predictions should be similar or higher.
- **Method:**
  - Replay each mint's curve at age 30 (or 60, matching the row's age_bucket). Extract Lane 6 features computable from raw trades:
    - `max_mult_at_age` — peak `mult_from_first` for trades with `t_s ≤ age`
    - `top3_buyer_pct` — sum of top-3 buyer SOL / total buy SOL
    - `repeat_buyer_rate` — fraction of consecutive trades by same `user_short`
    - `dust_buy_rate` — fraction of buys with `sol_amount < 0.01 SOL`
    - `sol_spent_first_2s`, `sol_spent_first_5s` — total buy SOL in first N seconds
    - `vsol_velocity_30s` — `vsol(age) - vsol(age-30)` (or `vsol(age) - vsol(0)` if age<30)
    - `vsol_velocity_60s` — same with 60s window
    - `vsol_acceleration` — `velocity_30s - velocity_60s` (proxy for d/dt)
    - `sell_ratio` — fraction of trades with `is_buy=False`
    - `buys_per_buyer` — n_buys / unique_buyers
    - `bundle_pct` — % supply held by ≥4-wallet 500ms-window bundle (if computable from raw trades; skip if requires observer state we don't have)
    - `n_smart_in` — count of smart-money wallets (from the curated index) in distinct trade users
  - Augment Run B's pre-graduation feature set with these. Train HistGradientBoostingClassifier on `sustained_30m`. Use the same 80/20 stratified split (random_state=42) as Run B for apples-to-apples comparison.
  - Report: full sample AUC, non-bundled AUC, bundled AUC, vs Run B baseline. Stratified feature importance.
- **Decision thresholds (frozen, applied AFTER writeup):**
  - **Non-bundled AUC ≥ 0.674 (≥5pp closure of the gap):** retraining is justified; implementation scoping is the next phase.
  - **Non-bundled AUC 0.644-0.673 (2-5pp closure):** partial result; Lane 8 (suppression matrix bias) jumps priority since features alone aren't enough.
  - **Non-bundled AUC < 0.644 (<2pp closure):** structural finding; feature engineering wasn't the lever, re-examine assumptions and re-scope.
- **Discipline:**
  - Hard stop at 3h. Past that = unanticipated complication, surface and pause.
  - Mechanical execution. Apply the decision rule AFTER the writeup, not during.
  - No deploy decisions tonight regardless of result. Rollout scoping is its own pre-registered work.
- **Outcome handling:** writeup to `docs/research/lane9_curve_replay_retrain.md`. Negative results published per the discipline contract.

### Lane 14 — bundled regression sub-investigation ✅ done 2026-05-05 late evening (Branches 1+2 both fire → hybrid action)

**RESULT (per pre-registered rule, applied fresh):** Two branches fire simultaneously.
- **Branch 1 (sample noise):** bootstrap 95% CI is **[-23.60pp, +10.87pp]** — overlaps zero. TRIPPED.
- **Branch 2 (single-feature dominance):** ablating `sol_spent_first_2s` recovers **+3.91pp** on bundled (above 3pp threshold). TRIPPED.

**Per Lane 13 divergence-handling discipline:** flagged publicly, action covers both, rule updated.

**Hybrid action (covers both):**
1. **Ship single-track retrain** — both branches agree on this baseline. Don't fork architecture.
2. **Don't implement asymmetric feature handling speculatively** at n=65. The Branch 2 evidence is borderline (3.91pp barely over threshold) and removing `sol_spent_first_2s` costs non-bundled -1.20pp.
3. **Add bundled-population monitoring post-ship** — weekly bundled AUC, per-prediction SHAP for `sol_spent_first_2s` on bundled vs non-bundled.
4. **Frozen re-investigation trigger:** rerun Lane 14 at n≥150 bundled in production. Add to BACKLOG as scheduled future work.

**Rule update for next sub-population analysis:**
- Branches 1 and 2 are evidence-types, not mutually exclusive verdicts. Define explicit precedence when multiple branches trip (1+2 = hybrid; 2+3 = distributed-with-leader; etc.)
- Compute bootstrap CI on each ablation recovery, not just on the regression
- Add minimum bundled n threshold (n≥150) for decisive verdicts; below that, default to hybrid + monitor

**Tomorrow's retrain implementation:** ship single-track GBM with full Lane 6 + Lane 9 features. Add stratified-AUC-by-bundle_detected logging to retrain validation. Ship-replace gate now includes "must not regress bundled AUC by ≥3pp" alongside non-bundled improvement.

Writeup: [docs/research/lane14_bundled_regression.md](../docs/research/lane14_bundled_regression.md).

### Lane 14 (original spec) — bundled regression sub-investigation (formally pre-registered 2026-05-05 evening)

Lane 9's retrain showed -5.9pp on bundled subset (held-out n=65) while showing +14.16pp on non-bundled. The bundled regression is a flag in the retrain scoping — small sample, could be noise — but worth closing the analytical loop before retrain ships single-track.

This pre-registration applies the new rules from `feedback_pre_registration_branches.md` (noise-thresholded events, transition zone, divergence handling).

- **Hypothesis enumeration (5 branches, with explicit transition zone):**
  1. **Sample noise:** bootstrap 95% CI on -5.9pp overlaps zero (CI includes ≥-1pp). Decision: ship single-track confidently. The flag was a small-sample artifact.
  2. **Single-feature dominance:** ablating ONE Lane 6 feature recovers ≥3pp on bundled (i.e., bundled AUC delta improves to better than -2.9pp). Decision: exclude that feature for bundled population in feature engineering, ship single-track with the asymmetric feature handling.
  3. **Distributed regression:** no single-feature ablation recovers ≥2pp on bundled; multiple small contributors. Decision: real signal that single-model architecture costs bundled performance. **Pre-register the two-model decision as future work — don't implement two-model speculatively.**
  4. **Transition zone:** bootstrap CI overlaps zero AND ablations show 0-2pp distributed effect. The signal is borderline. Decision: ship single-track, monitor bundled performance post-retrain at n≥150 in production, re-investigate if regression persists.
  5. **Other / unclear:** results don't fit any of the above. Re-scope.

- **Method:**
  - **Bootstrap:** resample held-out test set (with replacement) 1,000 times, recompute bundled AUC each time, build 95% CI. Use the same Lane 9 GBM model trained once.
  - **Ablation:** train Lane 9 GBM 13 times, each time omitting one Lane 6 feature. Compute bundled AUC change vs the full-feature baseline. Identify which feature ablations cause the largest IMPROVEMENT on bundled (i.e., features that, when removed, help bundled prediction — those are the features hurting bundled in the full model).
  - **Decision rule application:** apply the 5-branch rule to the combined evidence (bootstrap CI + ablation magnitudes).

- **Magnitude threshold (per pre-registration discipline rule):**
  - "Recovers ≥3pp" means bundled AUC delta improves by ≥3pp when feature is removed
  - "Distributed contributors" means each feature ablation moves bundled AUC by ≤2pp
  - Anything between 2-3pp is in the transition zone

- **Outcome handling:** writeup to `docs/research/lane14_bundled_regression.md`. Each branch produces a different first action for retrain implementation. Negative results (sample noise verdict) published — that's the "ship confidently, regression was a flag without signal" outcome.

### Lane 13 — calibration stability analysis ✅ done 2026-05-05 evening (Mechanism 4 by strict rule; qualitatively M2 with M3 noise)

**RESULT (per pre-registered rule, strictly applied):** Mechanism 4 — no single branch fits cleanly. 1/4 tiers oscillating, 0/4 cleanly monotonic, 2/4 single-flip, 1/4 stable. Strict verdict: escalate to bug hunt.

**Qualitative read (per pre-registered "data decisive in unanticipated way" meta-note):** dominant pattern is **Mechanism 2 (curve overshoot from fast rebuild) with Mechanism 3-like variance**. All 4 tiers' drift values are positive (last⅓ minus first⅓: +5.7pp / +10.7pp / -1.4pp / +20.8pp). The trajectory across high-n windows on 2x_from_now goes -17 → -30 → -35 → -7 → +5 → -15 over 30 hours — heavily over-confident moving toward zero with high day-to-day variance.

**The strict and qualitative reads diverge** because the strict rule assumed cleaner separation (≥8pp drift AND ≤1 cross). Real data shows 5-20pp drift WITH 2 crosses. The right interpretation is "transition zone" — curve still correcting historical over-confidence, with enough variance to occasionally cross zero.

**Sample-window caveat:** predictions table only retains ~7 days. The "30-day window" was aspirational; working sample is the full retained history. Two of four tiers (5x and 10x) have shorter valid time-series.

**Recommended fix scope (three-way response, none picks a single mechanism prematurely):**
1. **Slow rebuild cadence from 15min to 1-2h** — low-risk code change, addresses M2 if dominant
2. **Re-run Lane 13 in ~1 week** — if trajectory stabilizes near zero with reduced variance, M2 was right; if variance stays high, M3 is real and structural redesign needed
3. **Light bug-hunt on storage path** in parallel — verify predicted_at timestamps and atomic calibration writes
4. **Defensive 1-line fix** on dead-code "scale toward (1,1)" branch — still worth doing
5. **Sharpen /api/scope caveat** to: "magnitude calibration shows non-stationary behavior over recent rolling windows — predicted is over-stated by 5-35pp on most days, near-zero on others. Treat the field as a ranking signal, not a literal probability."

**Tomorrow's recalibration ticket scope:** ~1h code work + caveat update + scheduled re-validation in 1 week. Lane 13 narrowed the suspects from "real investigation" to "scoped fix + monitor."

Writeup: [docs/research/lane13_calibration_stability.md](../docs/research/lane13_calibration_stability.md).

### Lane 13 (original spec) — calibration stability analysis (formally pre-registered 2026-05-05 evening)

The recent-slice rerun showed direction flipping between Lane 7's full-sample (-12pp over) and recent 24h (+10pp under). Three plausible mechanisms surfaced (sample-period bias / curve overshoot / oscillating instability) plus a fourth catch-all. Lane 13 distinguishes them via rolling 24h windows over the last 30 days — each window's signed delta becomes a time-series the pattern reveals.

- **Hypothesis enumeration (the FOUR competing shapes a 30-day signed-delta plot can produce):**
  1. **Sample-period bias.** Single direction flip occurring only in the last few windows. No oscillation in the historical bulk. Recent slice is anomalous.
  2. **Curve overshoot from fast rebuild cadence.** Monotonic drift from over → under over time as recent high-actual-rate samples accumulate in the anchor set. Smooth trajectory.
  3. **Oscillating instability.** ≥3 direction flips in 30 days. Calibration fundamentally unstable on this signal at this rebuild cadence.
  4. **No clear pattern / chaotic.** Doesn't match any of the above shapes — bug or genuinely chaotic. Escalate to bug hunt.

- **Method:** rolling 24h windows over predictions in last 30 days. Per window, compute signed avg delta on bins ≥0.5 across all 4 runner_prob_*_from_now tiers. Output: time-series of signed delta per tier. Visual pattern + simple counts (zero crossings, monotonicity check) determine which branch fires.

- **Decision rule (4-branch, frozen pre-run):**
  - **Single flip, recent only → mechanism 1.** Fix: wait, monitor; sharpen the /api/scope caveat to "recent calibration appears underconfident on a small sample; will publish stable rate at n≥30 per recent window."
  - **Monotonic over → under over time → mechanism 2.** Fix: slow curve rebuild cadence from 15 min to 1-2h, allowing more samples to stabilize each anchor before the curve updates.
  - **≥3 direction flips in 30 days → mechanism 3.** Calibration fundamentally unstable. Fix: structural — either much slower rebuild + larger anchor sets, OR move calibration to use a stationarity-aware method (e.g., fixed historical reference rather than rolling).
  - **No clear pattern → mechanism 4.** Bug or genuinely chaotic. Escalate: trace storage path, resolution path, predicted_at-vs-resolved_at inconsistencies, look for a missing field that's making aggregate calibration look unstable.

- **Pre-registration meta-note (lesson from Lane 7 recent-slice):** when a verification is meant to confirm-or-reject, also enumerate "data is decisive in a way the hypothesis didn't anticipate." Lane 13's hypothesis 4 covers this branch explicitly. If the time-series doesn't match any of the four shapes, that's still a clean output — escalate to bug hunt rather than forcing a verdict that isn't there.

- **Outcome handling:** writeup to `docs/research/lane13_calibration_stability.md`. Each branch produces a different first action for tomorrow's recalibration workstream.

### Lane 7 recent-slice rerun ✅ done 2026-05-05 evening (mechanism (c) REJECTED; direction has FLIPPED)

**RESULT (decisive but unexpected):** 2 of 4 tiers still mis-scaled by ≥8pp on recent (last 24h, n=1,500) data. **Mechanism (c) REJECTED per pre-registered rule.**

**But the direction has FLIPPED.** Lane 7's full sample showed OVERCONFIDENT (predicted > actual). Recent slice shows UNDERCONFIDENT (predicted < actual). Same bins, opposite direction, similar magnitude (~10pp).

| Tier | Lane 7 full-sample | Recent slice |
|---|---:|---:|
| 2x_from_now [0.7, 0.8) | -16.6pp (over) | **+9.7pp (under)** |
| 2x_from_now [0.8, 0.9) | -8.0pp (over) | **+12.2pp (under)** |

Both directions on bins with n=95-109 — not noise. The calibration is unstable: overshoots historically, undershoots recently.

**Decisions:**

- **/api/scope caveat STAYS LIVE.** Current data does not support relaxing the framing. "directional, magnitude recalibration pending" is MORE supported, not less — both Lane 7 and the rerun show mis-scaling, just in different directions.
- **Caveat framing should sharpen tomorrow.** Replace "model is overconfident by 12pp" implication with: "magnitude calibration is non-stationary; treat the field as a ranking signal, not as a literal probability."
- **Tomorrow's recalibration scope grows back.** The audit's "30-min verify + 1-line cleanup" plan is wrong. Real investigation needed into why direction has flipped:
  1. Calibration analysis on rolling 7-day windows over last 30 days — plot avg delta by tier over time, see if direction oscillates
  2. Consider slowing curve rebuild from 15min → 1-2h (15min may be too aggressive for the resolved-prediction velocity)
  3. Trace "below first anchor: scale toward (0,0)" branch — opposite-direction analogue of the dead-code saturation branch
  4. **Don't ship a code fix until direction-flip mechanism is understood.** Risks making things worse.

**Three plausible mechanisms for the flip (none pre-registered yet, surface for tomorrow):**
1. Sample-period bias — recent 24h had unusually-pumping mints
2. Curve overshoot — calibration corrects past empirical rate as new high-rate samples enter anchors
3. Fourth bug we haven't identified

Writeup: [docs/research/lane7_recent_slice_rerun.md](../docs/research/lane7_recent_slice_rerun.md).

### Lane 7 recent-slice rerun (original spec) — verify current calibration vs the shipped caveat (formally pre-registered 2026-05-05 evening, ~30 min)

The audit's mechanism (c) hypothesis says current curves produce well-calibrated outputs and the mis-scaling Lane 7 measured was historical curve drift. If true, the `/api/scope` caveat we shipped earlier today ("directional, magnitude recalibration pending") **overstates a CURRENT problem that's actually historical**. Same shape as the morning's contamination fix — an active user-facing claim that doesn't match the data — except we created this one ourselves today, in good faith. The discipline is to verify against current data before letting the framing live overnight.

- **Hypothesis:** predictions with `predicted_at >= now - 24h` have **≤5pp average |delta| on bins ≥0.5** across all runner_prob_*_from_now tiers. Confirms audit's mechanism (c): the mis-scaling lives in pre-2026-05-05 historical data, not in current outputs.
- **Sample:** Lane 7's machinery, filtered to recent predictions only.
- **Method:** identical to Lane 7's calibration analysis (bins by predicted prob, actual hit rate per bin), just with `predicted_at >= now - 86400` filter. Stratified output for runner_prob_2x/3x/5x/10x_from_now.
- **Decision rule (frozen pre-run):**
  - **Within ±5pp on majority of bins** → audit's mechanism (c) confirmed. Tomorrow first action: update `/api/scope` caveat to "calibrated against forward outcomes; values pre-2026-05-05 may have residual mis-scaling due to curve maturity over time." Recalibration ticket reduced to **defensive 1-line dead-code cleanup** in `predictions.CalibrationCurve.__call__`.
  - **Still mis-scaled by ≥8pp on majority** → audit's hypothesis (c) is wrong. The mis-scaling is current, not historical. There's a fourth mechanism we haven't identified. Keep current caveat live, escalate diagnostic.
  - **Mixed** (some tiers clean, others not) → tier-specific fix needed; pre-register the asymmetry as its own investigation.
- **Outcome handling:** writeup to `docs/research/lane7_recent_slice_rerun.md`. The decision determines whether tomorrow's first 5-min ship is a /api/scope caveat update.
- **No deploy decisions tonight.** Verification only. The /api/scope update (if warranted) ships tomorrow; tonight surfaces the data needed to decide.

### Lane 7 audit — runner_prob recalibration mechanism ✅ done 2026-05-05 evening (BOTH pre-registered hypotheses REJECTED, third mechanism identified)

**RESULT:** Both pre-registered mechanisms rejected. (a) curves are built with 11 anchors and 89k+ samples per tier — calibration daemon working correctly. (b) saturation handling is correct in current state — curves include a 1.05 anchor that catches saturation, mapping raw=1.0 to ~0.92 (not 1.0).

**Third mechanism identified (Mechanism c — historical curve drift):** Lane 7 aggregated 89k predictions stored over time. Each was calibrated against the curve that existed at predict time. The curves matured — older predictions were calibrated against less-mature curves with fewer anchors and narrower coverage. Old saturated raws (raw=1.0) hit the curve's "scale toward (1,1)" branch when xN<1, mapping to literal 1.0. Current curves (xN=1.05) don't trigger this. Lane 7's measurement aggregates the historical and current together — appears mis-scaled in aggregate, but current calibration is fine.

**Tomorrow's recalibration ticket changes scope:** "audit + fix" → **"verify on a recent slice"**:
1. Re-run Lane 7's methodology on `predictions.predicted_at >= now - 24h` only
2. If recent slice is within ±5pp on majority of bins ≥0.5 → no code fix needed, update /api/scope caveat to reflect that recent calibration is good
3. If recent slice still mis-scaled → real bug we haven't found, deeper investigation
4. Either way, defensive cleanup: replace the dead-code "scale toward (1,1)" branch in `predictions.CalibrationCurve.__call__` with `if xN < 1: return yN`. One-line fix prevents the saturation bypass from waking up if curves ever lose the 1.05 anchor.

Estimated tomorrow work after this audit: ~30 min (verification query + defensive 1-liner), not 2-3h.

Writeup: [docs/research/lane7_audit_recalibration_mechanism.md](../docs/research/lane7_audit_recalibration_mechanism.md).

### Lane 7 audit (original spec) — runner_prob recalibration mechanism (formally pre-registered 2026-05-05 evening, diagnostic-only, ~30-60 min)

Lane 7 confirmed runner_prob_*_from_now is mis-scaled by ~12pp on bins ≥0.5, with a 60-70pp delta at the saturated [0.9, 1.0) bin. The recalibration FIX is a deploy decision (changes user-visible numbers) and shouldn't ship tonight. The AUDIT — diagnosing which mechanism is broken so tomorrow's fix scope is precise — is pure diagnostic and is appropriate tonight.

- **Hypothesis:** one of two mechanisms explains 100% of the mis-scaling:
  - **(a) Calibration daemon never built curves for runner_prob_*_from_now tiers.** `apply_calibration` becomes passthrough when <30 anchors per tier. Raw kNN output → user-visible probability with no correction.
  - **(b) Curves exist but saturating raw values bypass the curve fit at the high end.** The [0.9, 1.0) bin's 60-70pp delta is the smoking gun: when raw kNN = 1.0 (all 50 neighbors hit), `apply_calibration` may short-circuit or fail to map the saturated value through the fitted curve.
- **Method:**
  - Inspect the calibration storage (DB table or in-memory cache) for runner_prob_*_from_now tier entries
  - Count anchor points per tier; flag tiers with <30 anchors as "passthrough"
  - Read `apply_calibration` code path: does it have any short-circuit for raw≥0.99 or raw==1.0 that would bypass the curve fit?
  - Sample-check: pick 5 saturated raws (raw=1.0) from recent predictions, walk apply_calibration manually, see what comes out
- **Decision rule (frozen pre-run):**
  - **(a) only** (curves missing) → tomorrow's fix is "ensure calibration daemon builds runner_prob curves." Targeted fix, may already work once daemon completes a backfill.
  - **(b) only** (curves exist but saturation bypass) → tomorrow's fix is "remove saturation bypass at high end." Code change in `apply_calibration`.
  - **Both** → both fixes needed, sequence matters: build curves first, then verify saturation handling against the populated curves.
  - **Neither** → third mechanism we haven't identified. Re-scope before any fix.
- **Outcome handling:** writeup to `docs/research/lane7_audit_recalibration_mechanism.md`. Tomorrow's recalibration ticket is converted from "audit + fix" to "fix only" with a known scope.
- **No deploy decisions.** Audit only. Fix sequencing is tomorrow's call.

### Lane 7 — runner_prob calibration validation ✅ done 2026-05-05 evening (MIS-SCALED, recalibratable)

**RESULT:** runner_prob is MIS-SCALED, not BROKEN. Systematically overconfident by ~11-13pp on bins ≥0.5 across all from_now tiers (2x/3x/5x/10x). Mis-scaling is uniform across bundled vs non-bundled — no additional selection bias on top of grad_prob's.

**Decision per pre-registered rule:** recalibrate via Platt/isotonic — same self-correcting curve infrastructure grad_prob uses. Don't ship runner_prob as B2B signal until recalibration verified.

**Two probable mechanisms (testable in code):**
1. Calibration daemon may not have built curves for runner_prob_*_from_now tiers — passthrough if <30 anchors per tier
2. Saturating raw values (kNN = 1.0 when all 50 neighbors hit) may bypass the curve fit at high end. The [0.9, 1.0) bin for 5x_from_launch shows actual=0.287 vs predicted=0.993 — 70pp delta — clearly a saturation leak.

**Sample:** n=89,077 resolved predictions with runner_prob_* + actual_max_mult populated.

**Next steps (not deploy decisions, just follow-ups):**
1. Inspect `predictions.get_all_calibration_curves()` to check which runner_prob tiers have anchored curves
2. Audit `apply_calibration` for saturation handling
3. Re-run Lane 7 after the fix lands; if <10pp on majority of bins, calibration is restored

**API surface implication:** `/api/scope` claims runner_prob fields are calibrated. They're aspirational, not currently within tolerance. Either fix the calibration or update the docs before any B2B story relies on runner_prob.

Writeup: [docs/research/lane7_runner_prob_calibration.md](../docs/research/lane7_runner_prob_calibration.md).

### Lane 7 (original spec) — runner_prob calibration validation (formally pre-registered 2026-05-05 evening, ~2-3h budget)

The user surfaced earlier today that `runner_prob_2x/5x/10x` "are pretty fire" — operator intuition. Lane 7 validates this with the same rigor applied to grad_prob: pull historical runner_prob predictions, look up actual outcomes, compute calibration. Critical because the API exposes runner_prob fields and any user / B2B consumer indexing on them needs to know whether they're a real signal or a glorified guess.

- **Hypothesis (primary):** the `runner_prob_Nx_from_now` calibration curves at threshold ≥0.5 hit within ±10pp of stated probability on resolved post-grad outcomes (e.g. when the model says runner_prob_5x_from_now=0.6, actual ≥5× rate is in [0.50, 0.70]).
- **Hypothesis (secondary, selection-bias check):** runner_prob has the same bundled-vs-non-bundled selection bias grad_prob does. Stratify calibration by `bundle_detected`. If bundled has materially better calibration than non-bundled, runner_prob inherits the same Layer 2 issue and Lane 9-style retrain applies. If both stratify similarly, runner_prob is a cleaner signal.
- **Sample:** all resolved predictions where `runner_prob_Nx_from_now` is non-null and `actual_max_mult` is set. Tested across N ∈ {2, 3, 5, 10} for from_now variants. From-launch variants (`runner_prob_Nx`) tested separately — different label, different calibration question.
- **Method:**
  - Pull predictions joined to outcomes
  - For each tier (2x, 3x, 5x, 10x):
    - Compute "actual" outcome:
      - from_now variant: `actual_max_mult / entry_mult ≥ N`
      - from_launch variant: `actual_max_mult ≥ N`
    - Bin predictions by predicted prob (0.0-0.1, 0.1-0.2, ..., 0.9-1.0)
    - For each bin: actual hit rate, n, calibration delta
    - Stratify by bundle_detected
  - Report calibration table per tier per variant per bundle stratum
- **Decision thresholds (frozen pre-run):**
  - **Calibration on bin ≥0.5 within ±10pp on majority of populated bins (≥75%):** runner_prob is a calibrated signal. Surface as a real product signal alongside grad_prob.
  - **Calibration off by 10-25pp on majority of bins:** signal exists but is mis-scaled. Recalibrate via Platt scaling / isotonic — same pattern as grad_prob's existing self-correcting curve.
  - **Calibration off by >25pp on majority of bins:** signal is broken; runner_prob is operator-intuition that didn't survive measurement. Stop relying on it; investigate root cause before any fix.
- **Discipline:**
  - Mechanical execution. Apply decision rule fresh post-writeup.
  - No deploy decisions tonight.
  - Negative results published — particularly important here since it touches an API surface.
- **Outcome handling:** writeup to `docs/research/lane7_runner_prob_calibration.md`. If runner_prob fails calibration, surface immediately so we don't ship a B2B story that includes it.

### Lane 12 prep phase ✅ done 2026-05-05 late evening (PR-ready, not deployed)

Per the "two-deploy day" discipline note (each deploy adds 24-48h of incident-debug surface area; observer touches Rust + critical infrastructure → deserves fresh focus), Lane 12 prep ships PR-ready code without deploying it. The deploy is a 5-min `fly deploy` whenever the next focused window picks it up.

- **Hypothesis (prep-phase only):** the instrumentation can be written and tested locally without breaking observer behavior.
- **Method:** code, `cargo build`, mock log-format verification, write+self-test analysis script.
- **Decision rule (prep-phase):** if `cargo build` clean AND log format matches expected schema AND analysis script self-tests pass on synthetic logs → PR-ready, deploy waits.
- **Time bound:** 2h. Past that means instrumentation is more entangled than expected — surface and pause.

**RESULT (decision-rule applied fresh):** all three prep-phase gates PASS.
- **cargo build clean:** ✓ (only pre-existing warnings, zero new from this change)
- **Log format matches expected schema:** ✓ — emits 5 tagged event types `[OBSERVER:WS|HB|MEM|SIG]` + reconnect counter; parser regex matches them all
- **Analysis script self-tests:** ✓ — 4/4 hypothesis scenarios (A websocket / B fly restart / C process degradation / D rate-limit) classified correctly with "strong" confidence on synthetic logs

**Deliverables (PR-ready, not yet deployed):**
- `src/observer.rs` — instrumentation: 5 atomic counters + heartbeat task (60s) + memory snapshot task (300s) + SIGTERM/SIGINT handler + per-attempt reconnect logging on the WS loop
- `scripts/lane12_observer_analysis.py` — log parser, per-UTC-hour bucketing, 4-hypothesis decision-rule classifier
- `scripts/lane12_observer_analysis_test.py` — 4-scenario synthetic test fixture (run: `python3 scripts/lane12_observer_analysis_test.py`)

**Skipped (intentionally) in prep:**
- 10-min mock run on local: macOS lacks `/proc/self/status` (so MEM events would all show `source=unavailable`), and the WS subscribe needs a working Helius key + reachable endpoint. The unit-level evidence (cargo build + analyzer self-tests) covers what a mock run would have shown without the live-environment dependencies.

**Cutover sequence (whenever next focused session picks it up):**
1. `fly deploy --remote-only` (no other code changes — Rust-only diff in this image rebuild)
2. Verify log volume on Fly: `fly logs -a graduate-oracle | grep -c "OBSERVER:HB"` should show ~1/min
3. Wait 24-48h (full daily cycle including catastrophic windows)
4. `fly logs -a graduate-oracle --no-tail | python3 scripts/lane12_observer_analysis.py -`
5. Apply the 4-hypothesis decision rule from "Lane 12 — observer instrumentation" pre-registration above
6. Pre-register the FIX separately before any code change ships



### Lane 12 — observer instrumentation + 12-hour-cycle mechanism diagnosis (formally pre-registered 2026-05-05 evening)

Path A confirmed observer flush activity drops to 0.35× during 03-06 UTC + 16-18 UTC catastrophic windows, daily 12-hour cycle. The exact mechanism (subscription max-age vs fly machine restarts vs process degradation) isn't visible without observer-side instrumentation. Lane 12 is the targeted diagnostic: deploy instrumentation, watch the next 24-48h of bad-window logs, identify the mechanism, scope the fix.

- **Primary hypothesis:** the 12-hour cycle is caused by **Solana RPC websocket subscription max-age** — providers commonly cap long-lived subscriptions at 12h, forcing reconnects at fixed intervals. The observer's reconnection logic isn't bridging the gap cleanly, leaving observer effectively dark for some duration after each forced reconnect. Predictions from this hypothesis:
  - Reconnection events will cluster at ~12h intervals near the start of catastrophic windows
  - Each reconnect will be followed by minutes to tens of minutes of low/no trade-receipt activity
  - The pattern will be deterministic by UTC clock (not jitter)

- **Alternative hypotheses:**
  - **(B) Fly machine restart cadence.** Fly performs internal scheduled restarts. If our machine restarts at ~04 and ~16 UTC, observer takes time to re-establish state after each. Distinguishable from (A) by: machine state transitions in fly events would coincide with the windows; total downtime per event ~1-3 min, NOT enough to explain hours of low flush.
  - **(C) Process degradation cycles.** Memory pressure or task starvation builds up over the day; observer slows but doesn't crash. Distinguishable from (A) by: trade rate degrades gradually, not abruptly; no clean reconnect events; possibly correlated with memory metrics.
  - **(D) Helius rate-limit window.** Despite the public status page being clean (Lane 11 confirmed), per-key rate limits could trip during high-volume windows. Distinguishable: rate-limit errors visible in logs; activity drops at high-traffic UTC times specifically (US daytime/EU evening).

- **Instrumentation to deploy in `src/observer.rs`:**
  - **Websocket lifecycle:** every `connect`, `disconnect`, `reconnect_attempt` event with timestamp + reason (close code, error message). INFO level.
  - **Reconnection detail:** per-attempt: success/failure, duration, retries needed. INFO level.
  - **Rolling trade-rate heartbeat:** trades-received-per-minute over the last 60s, logged every 60s. INFO level. Cheap to compute (counter + tick).
  - **Memory snapshot:** RSS bytes logged every 5 min. INFO level.
  - **Fly platform events:** SIGTERM/SIGINT/health-check failures captured as ERROR-level lines so they're trivially greppable.

- **Decision rule (frozen pre-run, applied AFTER 24-48h of post-instrumentation logs):**
  - **Reconnections clustered at ~12h intervals at start of bad windows, with low trade-rate following each:** Hypothesis (A) confirmed. **Fix scope:** proactive reconnect on shorter cadence (e.g. every 4-6h, before provider's max-age forces it), or RPC redundancy across providers with different cycle phases.
  - **Trade-rate drops to zero during bad windows with no observable reconnects, no clean events:** Hypothesis (C) supported. **Fix scope:** memory/GC profiling, observer process refactor; bigger work item.
  - **Fly machine state transitions visible in events during bad windows:** Hypothesis (B) confirmed. **Fix scope:** contact fly support for scheduled-event timing, or set up multi-machine redundancy.
  - **Rate-limit errors clustered in bad windows:** Hypothesis (D) confirmed. **Fix scope:** higher-tier endpoint, multi-key rotation, or backoff with jitter.
  - **No clean signal after 48h:** escalate to side-by-side observer redundancy. Run two independent observer instances (different RPC keys / regions). If both miss the same windows, it's an external dependency. If they miss different windows, it's our process.

- **Time bound:** 1-2h to write + ship instrumentation. 24-48h passive log accumulation. Then 1-2h to analyze + decide. Total wall-clock: 2-3 days; total active work: 3-5h.

- **Outcome handling:** writeup to `docs/research/lane12_observer_instrumentation.md` after the analysis phase. The writeup determines tomorrow-after-tomorrow's fix workstream. Pre-register the fix separately before any code change ships.

- **Discipline:** instrumentation alone changes nothing observable to users — no deploy decision involved in adding logs. The fix THAT FOLLOWS the instrumentation analysis is a deploy decision and goes through fresh pre-registration.

### Lane 11 Path A — observer activity diagnostic ✅ done 2026-05-05 evening (smoking gun found)

**RESULT:** observer flush activity drops to **0.35× during catastrophic hours** (03-06 UTC + 16-18 UTC) compared to stable hours (07-14 UTC). 65% drop. Pattern is daily recurring, multiple zero-flush hours per day clustered at exactly 04-06 UTC and 16-18 UTC. **12-hour cycle confirmed.** Decision per pre-registered rule: localized fix scoped (single recurring event explains >50% of misses).

**The exact mechanism is not yet identified at the code level** — instrumentation needed. Strong candidates:
1. Subscription disconnect + slow reconnect (Solana RPC providers commonly cap websocket max-age at 12h)
2. Fly machine restart cadence
3. Process degradation cycles (Rust observer; less likely than #1)

**Method note:** fly logs CLI only retains buffer since the most recent restart, so live log analysis was impractical for 7-day historical. Used curve-flush filenames (`YYYYMMDDTHHMMSS_{mint}.json`) as observer-activity proxy — direct, persistent, no instrumentation required. Observer flush counts per hour reveal when observer was producing output. The pattern is decisively NOT random.

**Tomorrow's first action:** add INFO-level instrumentation in `src/observer.rs` for websocket connect/disconnect events, reconnection attempts, and rolling trade-rate heartbeat. After 24-48h of logs in the catastrophic windows, the exact mechanism will be visible. Fix follows from what's observed.

**Today's containment:** none. The fix needs the instrumentation layer first; otherwise we're guessing. Retrain plan unaffected — Layer 1 caps coverage at ~70% of non-bundled until fixed, but retrain still improves performance on the captured population. Layer 1 fix runs in parallel.

Writeup: [docs/research/lane11_path_a_logs.md](../docs/research/lane11_path_a_logs.md).

### Lane 11 Path A (original spec) — fly logs investigation for catastrophic UTC windows (formally pre-registered 2026-05-05 evening, ~2h budget)

Lane 11 found the leak clusters at 03-06 UTC and 16-18 UTC. The Helius+Solana status check decisively localized the cause to our infrastructure (no upstream maintenance pattern matches). Path A is the targeted diagnostic: examine fly logs during recent catastrophic windows, find the pattern.

- **Hypothesis:** a single recurring infrastructure event explains the 12-hour clustering. Candidates (in rough order of likelihood):
  - Observer's reconnection logic in `src/observer.rs` failing or stalling during specific operational stress
  - Fly machine restart cadence (fly machines occasionally restart on weekly/twice-weekly schedules; if our restart timing aligns with the catastrophic windows, every restart is followed by some unbridged time)
  - Load-balancer connection rotation (some L4 load balancers rotate websocket connections at 12-hour intervals)
  - Memory pressure / GC pause cycles in the observer process accumulating over the day
  - Helius rate limit windows (despite Helius's status page being clean — rate limits aren't outages, they're throttling)
- **Method:**
  - `fly logs --app graduate-oracle` for the last 7 days (or however much is retained)
  - Filter to observer-relevant lines: subscription events, reconnect attempts, RPC errors, machine restarts, OOM warnings
  - Cross-reference against the catastrophic windows: are events clustered at 03-06 UTC and 16-18 UTC? Is there a specific log line that recurs every ~12 hours?
  - If a smoking gun is found, document timestamps + frequency + likely cause
- **Decision rule:**
  - **Single recurring event explains ≥50% of catastrophic-window misses** → localized fix scoped tomorrow (could be Rust code change, config tweak, or platform setting).
  - **Multiple events each <50%** → systemic reliability work; instrumentation first.
  - **No clear pattern in logs** → instrumentation gap; Path B (subscription heartbeat instrumentation in observer) becomes tomorrow's first action.
- **Time budget:** ~2h. Past that, the diagnostic itself isn't the lever — surface and pause.
- **Outcome handling:** writeup to `docs/research/lane11_path_a_logs.md`. Negative results (no smoking gun) are valid and important — they route to Path B.

### Lane 11 — observer collection-leak diagnostic ✅ done 2026-05-05 evening (eviction hypothesis REJECTED, hour-of-day pattern found)

**RESULT:** Single dominant mechanism IS present (>50% of misses), but it's NOT the eviction hypothesis. Missing mints have median feature_unique_buyers=8 vs captured median=9 — they're not sparse-activity zombies. The `<3 trades` eviction config tweak path is REJECTED.

**The actual mechanism:** **observer subscription downtime during specific UTC hours.** Hours 03-06 UTC and 16-18 UTC see miss rates of **59-90%** (one hour peaks at 90.1%), while hours 07-14 UTC see **1-7%** miss rates. ~57% of all misses come from these 5-6 specific hour windows. Pattern is too clustered to be random — likely cause is RPC subscription drops, network instability windows, scheduled maintenance, or rate limits at specific UTC times.

**Encouraging trend:** miss rate cut from 51.6% (older 3-day window) to 30.7% (last 3 days). Whatever changed on the 2026-05-02 observer rebuild has helped — but ~31% leak remains.

**Bonus finding:** zero classifiable bundled mints in the missing set. **Selection bias starts at the ingest layer** — observer literally never sees a meaningful fraction of non-bundled graduations in the first place. Bundled mints' concentrated activity gets captured reliably; non-bundled mints' more spread-out activity falls through subscription gaps.

**Decision per pre-registered rule: targeted fix justified, but the fix is observer reliability work** (subscription health monitoring, RPC redundancy, auto-reconnect), NOT a config tweak. Two paths:
- **Path A (recommended start):** investigate fly logs + Solana RPC status for 03-06 UTC and 16-18 UTC windows. ~2h to find a smoking gun if one exists.
- **Path B (escalation):** build observer reliability instrumentation (heartbeat, subscription state tracker), re-run Lane 11 with proper telemetry in a week.

Pre-registration for the Path A sub-investigation should land tomorrow before any logs work.

Writeup: [docs/research/lane11_collection_leak.md](../docs/research/lane11_collection_leak.md).

### Lane 11 (original spec) — observer collection-leak diagnostic (formally pre-registered 2026-05-05 evening, ~1-2h budget)

Layer 1 of the original 3-layer selection-bias hypothesis. ~40% of resolved post-grad outcomes (494 mints out of 1,272 with no predictions row) also have no curve file. These graduated, are trackable post-bond via on-chain DEX prices, but the observer never produced a curve for them. A retrained model can only fire on mints the observer captures, so this caps coverage regardless of model quality.

- **Hypothesis:** the ~40% leak has a **dominant single mechanism** (>50% of misses explained by one root cause), not a long tail of small causes. **Strongly suspected mechanism: the `<3 trades` zombie eviction** in observer.rs. Non-bundled mints have sparse early trades by definition (no bundle = no instant flood of trades) — they're prime candidates to be evicted before accumulating enough activity to graduate. If true, this is a config tweak (raise the eviction threshold or extend the eviction window), not a Rust rewrite.
- **Sample:**
  - **Missing set:** 494 mints with resolved post_grad_outcomes but no curve file
  - **Control set:** 100 captured non-bundled graduators (curve file present), matched by graduation time bucket
- **Method:** stratify the missing set vs control by:
  - **Graduation timestamp** — clustered (subscription-drop windows) or distributed (pattern-independent)?
  - **post_grad_outcomes feature columns** — captured at graduation moment. If `feature_unique_buyers` and `feature_n_whales` are systematically lower on missing mints, that's evidence of sparse early activity → eviction.
  - **Creator address (first_buyer)** — specific creators repeatedly missed?
  - **Observer eviction logs** — if accessible (`fly logs` or in-DB log table), correlate gaps in coverage with `<3 trades` zombie evictions.
- **Decision rule (frozen pre-run):**
  - **Single mechanism explains >50% of misses** → targeted fix (config tweak or scoped Rust change). Concrete next step: pre-register the fix.
  - **Multiple mechanisms each <50%** → broader observer reliability work. Instrumentation first, then reliability hardening.
  - **No clear pattern** → instrumentation gap; can't diagnose further without better observer telemetry. That becomes the first work.
- **Time budget:** 1-2h for pattern analysis. If pattern is obvious quickly, decision lands same session. If it requires Rust code dives, surface and pause — different rhythm of work.
- **Outcome handling:** writeup to `docs/research/lane11_collection_leak.md`. Negative results published. Particularly worth surfacing if the hypothesis is rejected — the "easy fix" path goes away, retrain ships against the capped corpus, and Layer 1 becomes a longer-running concern.

### Lane 10 — earliness validation on Lane 9 model ✅ done 2026-05-05 evening

**RESULT (marginal but tripped):** GBM ACT-eligible rate on non-bundled @ age=30 = **11.8%** (31/263). k-NN reference = 5.7% (15/263). **Ratio = 2.07×** — just above the ≥2× threshold.

**Decision per pre-registered rule applied fresh: retrain solves earliness AND accuracy. TIER: ships earliness.**

Honest framing: doubles ACT-eligible fires on non-bundled @ age=30 (from ~6% to ~12% of population). Crosses the bar but doesn't transform — 88% of non-bundled mints still don't fire ACT at age=30. The lateness problem is ameliorated, not solved.

The earliness gain is consistent at age=60 too (2.35×), so the improvement isn't an age=30 artifact.

**Implication for tomorrow's retrain scoping:**
- Single-workstream retrain confirmed (no separate "early-confidence" intervention needed in scope)
- Post-retrain product framing: "ACT-eligible coverage doubles on non-bundled at age=30; 88% caught later as WATCH"
- Separate research question opens for future: "what would push earliness from 12% to 25%?" — pre-bond bot-detection, age 5-15s smart-money, label change, etc. Not in retrain scope.
- Lane 1 Layer 1 (observer collection leak ~40%) is next-priority fix — a retrained model can only fire on mints the observer captures.

Writeup: [docs/research/lane10_earliness_validation.md](../docs/research/lane10_earliness_validation.md).

### Lane 10 (original spec) — earliness validation on Lane 9 model (formally pre-registered 2026-05-05 evening)

Lane 9 measured AUC on sustain prediction. AUC tells us the model is more accurate at predicting whether a mint sustains. It does NOT tell us when the model becomes confident. A model can be more accurate at age=60 without being confident earlier. Since "earlier alerts" is the user-facing problem, AUC alone is necessary-but-not-sufficient validation of the retrain.

- **Hypothesis:** the Lane 9 retrained GBM produces **≥2× the ACT-eligible fires** (`grad_prob ≥ 0.7 AND cur_mult ≤ 2.0`) on non-bundled mints at **age_bucket=30** specifically, vs the current k-NN. Absolute target: >15.8% of non-bundled held-out mints at age=30 are ACT-eligible under GBM (Lane 8 baseline of 7.9% × 2).
- **Sample:** the same non-bundled held-out set Lane 9 used (same train/test split, random_state=42).
- **Method:** for each non-bundled mint in the held-out set with both age_bucket=30 and age_bucket=60 rows present:
  - At age_bucket=30: get k-NN's `predicted_prob` (live model output, already in predictions table). Get GBM's `predict_proba` using Lane 9 features extracted at age=30. Get cur_mult from `entry_mult` (the value at first-tick in that bucket). Mark ACT-eligible if `grad_prob ≥ 0.7 AND cur_mult ≤ 2.0`.
  - Same for age_bucket=60 (for context — which model gets more fires earlier).
  - Count ACT-eligible fires per (model × age_bucket × population) cell.
- **Decision thresholds (frozen pre-run, applied AFTER writeup):**
  - **GBM ACT-eligible rate at age=30 ≥ 2× k-NN rate (≥15.8% absolute):** retrain addresses BOTH accuracy AND earliness. Single workstream — retrain ships, problem solved at the model layer.
  - **1.2-2× (9.5%-15.8%):** partial earliness gain. Retrain helps but lateness problem persists. Need separate "earlier confidence" intervention (different feature set tuned for age-30 specifically? different label like `early_grad_prob`?).
  - **< 1.2× (<9.5%):** retrain is an accuracy-only fix. Lateness problem is structural and requires its own diagnosis. Re-scope.
- **Discipline:**
  - Mechanical execution. Apply decision rule fresh post-writeup.
  - No deploy decisions tonight.
  - Negative results published.
- **Outcome handling:** writeup to `docs/research/lane10_earliness_validation.md`. The result determines the framing of tomorrow's retrain implementation scope.

### Lane 8 — suppression-matrix bias ✅ done 2026-05-05 evening (REJECTED)

**RESULT:** Non-bundled post-peak (max_mult/cur ≥ 2.0) filter rate = **11.8%**. Below the 20% "matrix is fine" threshold. **Decision per pre-registered rule: matrix is FINE. Diagnosis complete.**

The 3-layer selection-bias hypothesis collapses to 2 confirmed layers:
- ✅ Layer 1 (collection leak ~40%) — confirmed
- ✅ Layer 2 (feature-vector bias, +14.16pp closeable) — Lane 9 confirmed
- ❌ Layer 3 (suppression matrix bias) — REJECTED

**Bonus finding:** of the 88 non-bundled ≥0.7 candidates that PASS both filters (would fire as ACT), 95.5% sustained. Of the 1,031 that filter to WATCH, 64.0% sustained. The matrix is actively discriminating outcome quality. Not biased — actually doing useful work.

**Implication for retrain scoping:** drop the matrix-redesign workstream. Single-track retrain (Lane 9 features + Lane 6 candidates) is the only intervention needed at the model-pipeline layer. Layer 1 (collection ingest debugging) is a parallel concern but separate from retrain.

**Lateness is the real bottleneck:** 87.5% of non-bundled ≥0.7 candidates have cur_mult > 2.0 by the time the model fires. The fix is the model getting confident earlier (Lane 9's feature-engineering thesis), not the matrix relaxing its filter. Pre-register "how much earlier the retrained model crosses 0.7" as a secondary metric in retrain scoping.

Writeup: [docs/research/lane8_suppression_bias.md](../docs/research/lane8_suppression_bias.md).

### Lane 8 (original spec) — suppression-matrix bias (formally pre-registered 2026-05-05 afternoon)

Surfaced as a bonus finding in the selection-bias investigation: half of non-bundled graduators reach grad_prob ≥0.7 at age_bucket=60, but the live model fires on ~zero of them. The gap is the suppression matrix (post-peak filter: `max_mult / current_mult ≥ 2.0`; entry-quality filter: `current_mult > 2.0`). The suspicion is that those filters were tuned implicitly on a bundled-dominant population — bundled mints stay near their peak through bonding, non-bundled mints pump pre-graduation and trip post-peak by the time grad_prob matures.

- **Hypothesis:** the suppression matrix in `web/alert_push.py:maybe_push` is implicitly tuned for bundled-pump dynamics. Specifically: for non-bundled graduators that crossed grad_prob ≥0.7 in the live model, ≥50% were filtered out by either the post-peak gate (`max_mult / current_mult ≥ 2.0`) or the entry-quality gate (`current_mult > 2.0`).
- **Method:**
  - Pull non-bundled graduators in the last 30 days where `predictions.predicted_prob >= 0.7` at any age_bucket. n target ≥100.
  - For each, replay the snapshot at the bucket where prob crossed 0.7, extract `current_mult` and `max_mult`.
  - Apply each suppression rule independently. Count fires that would have been filtered, by which rule.
  - Compare to the same analysis on bundled graduators: do bundled ≥0.7 fires get filtered at materially lower rates?
- **Decision thresholds:**
  - **>50% of non-bundled ≥0.7 candidates filtered by post-peak/entry gates AND non-bundled filter rate is at least 1.5× the bundled filter rate**: matrix is biased. Architectural fix in retrain scoping: population-aware filtering (different thresholds for bundled vs non-bundled), OR a fundamentally different suppression signal (e.g. trade-volume momentum in last 60s instead of `max_mult / current_mult` ratios).
  - **<30% of non-bundled filtered**: matrix isn't the bottleneck. The fire-bias is elsewhere.
  - **30-50% range**: matrix contributes but isn't dominant. Worth tuning but not redesigning.
- **Outcome handling:** writeup to `docs/research/lane8_suppression_bias.md`. **Strict no-bandaid rule:** don't relax thresholds based on this finding alone. Architectural redesign goes through pre-registered retrain scoping; tactical threshold tweaks don't.
- **Why pre-register NOW:** so future-Claude (or future-anyone) doesn't see the 50%-reach-0.7 finding, conclude "loosen the post-peak filter," and ship a bandaid. The architectural framing is the bar.

### Selection-bias investigation — collection bug vs feature-vector bias (formally pre-registered 2026-05-05 afternoon)

Two competing sub-hypotheses, deliberately separated because they have different fixes.

**Methodology sharpening (added 2026-05-05):** the original spec said "replay the snapshot through the current k-NN." That's UNRELIABLE per `project_replay_unsupported.md` — corpus drift means today's INDEX doesn't return what the model produced at score time. Use `predictions.predicted_prob` directly instead — that's the live grad_prob the model actually output at score time, locked into the predictions table by the no-COALESCE-on-conflict UPSERT pattern. No replay. The drift problem doesn't apply when we have the historical value preserved.

Caveat per the user's note: this gives a directional answer, not a clean validation metric. We're disambiguating between two structural hypotheses; directional is enough.

- **H_collection (observer underrepresenting non-bundled in firehose).**
  - **Test:** for the 1,256 mints in `post_grad_outcomes` that have NO row in `predictions`, check whether they disproportionately lack predictions because the observer never saw them. Cross-reference against `_processed` set in the kNN index — if those 1,256 mints' curve files are ALSO missing from `/data/observer-curves`, that's a collection problem. If the curves exist but no predictions row, the score path missed them (different problem).
  - **Decision:** if ≥30% of the 1,256 missing-predictions mints also have no curve file, observer collection is materially leaking. Fix path: ingest debugging.
  - If ≤10%, collection is fine.

- **H_feature_vector (observer captures, kNN puts them in low-confidence neighborhoods).**
  - **Test:** for non-bundled graduators in the last 7 days that DO have a predictions row, pull `predictions.predicted_prob` at age_bucket=30 and age_bucket=60. Distribution analysis: what's the mean/median grad_prob the model produced on these? Compare to the same distribution on bundled graduators.
  - **Decision per user's pre-set ranges:**
    - Mean grad_prob 0.3-0.5 on non-bundled graduators: feature-vector bias. The model is close-but-not-clearing-0.7. Fix: better features (Lane 6 candidates).
    - Mean grad_prob 0.05-0.15: deeper issue. Either features genuinely don't separate non-bundled from non-graduates, or there's a corpus issue.
    - Mean grad_prob ≥0.5: the model IS confident on non-bundled graduators but downstream gates (entry-quality, suppression) are filtering them. Fix is downstream of grad_prob.

- **Prior (user's intuition, 2026-05-04):** feature-vector bias is more likely. Observer indexes ~150k curves regardless of whether we fire on them; 87% of graduates being non-bundled means the observer sees them. The model just isn't confident on them. Worth confirming with data, not assuming.

- **Outcome handling:** writeup to `docs/research/selection_bias_investigation.md`. Both sub-hypotheses run together; the result determines which retrain intervention applies.

### Retrain scoping — CONSOLIDATED (2026-05-05 evening, post-Lane 1/2/6/7/8/9/10/11/Path A)

The diagnostic stack is closed. This section is the implementation plan tomorrow opens with — coherent, dependencies clear, no cross-referencing required.

#### One-paragraph why

The current k-NN model is calibrated on a corpus where 87% of graduations are non-bundled (Lane 1), but the live model fires almost exclusively on the bundled 13% (Lane 1). Lane 9 confirmed feature engineering closes the AUC gap on non-bundled by +14pp (0.60→0.74) using curve-replayed features at age 30/60. Lane 10 confirmed this also doubles ACT-eligible fires at age=30 (5.7%→11.8%, ratio 2.07×). The retrain replaces the current k-NN's training feature set with the feature-rich one Lane 9 validated, and ships once the held-out validation passes the criteria below.

#### Training set

- **Sample:** rows from `post_grad_outcomes` joined to `predictions`, with `manufactured_pump` and `bundle_detected` non-null. **n ≈ 2,975** under current data; will grow as more outcomes resolve.
- **Bundled and non-bundled both included.** Do NOT split into separate models — Lane 9's top-5 ranking overlap = 3 (ambiguous toward "same signal"); Lane 2's was 4 (clearly same). No evidence supports per-population specialization.
- **Do NOT filter `hard_bot_signal` mints out.** Lane 6 finding: hidden mints rug 10-15× more than shown — model should LEARN the penalty, not have it hard-masked.
- **Train/test split:** 80/20 stratified on (bundled × sustained_30m). Random_state=42 for reproducibility with Lane 9.
- **Coverage caveat:** Lane 11 found ~30% of non-bundled graduators have no curve file (observer collection leak). The retrain trains on the captured 65.6% subset. The live deployed model can fire on at most ~70% of non-bundled grads until Layer 1 (collection leak) is fixed. Layer 1 is a parallel workstream — see "Companion fixes" below.

#### Label

- **Primary:** `sustained_30m` (binary, ≥80% of grad price held at 30m post-bond) — the trader-relevant outcome.
- **Secondary (for k-NN comparison):** `actual_graduated` — apples-to-apples vs the current k-NN's task.
- Deferred: `peak_mult_within_30m_post_bond ≥ 2.0` (the runner outcome) — sequence after retrain-1 ships.

#### Feature set (validated by Lane 9)

The Lane 6 candidates extracted from curve replay at age 30/60 (Lane 9 confirmed top features):

**High-importance (top of Lane 9's stratified ranking):**
- `max_mult_at_age` — peak multiplier so far
- `vsol_velocity_60s` — momentum
- `top3_buyer_pct` — concentration
- `sol_spent_first_2s` — early-load
- `entry_mult` — current multiplier

**Lane 6 candidates (extracted by Lane 9's curve-replay):**
- `repeat_buyer_rate`, `dust_buy_rate`, `sol_spent_first_5s`, `vsol_velocity_30s`, `vsol_acceleration`, `sell_ratio`, `buys_per_buyer`, `bundle_pct`, `n_smart_in` (from the curated smart-wallet index)

**Existing schema features:**
- `age_bucket`, `entry_mult`, `was_calibrated`, `manufactured_pump`, `bundle_detected`, `dex_paid`, `fee_delegated`

**Lane 6 candidates not yet validated** (require additional implementation):
- `unknown_buyer_pct`, `low_history_pct` — need wallet-history database; deferred to retrain-2
- `creator_runner`, `creator_5x_rate`, `creator_n_launches` — already populated in predictions for some rows; can include where available

**Post-grad snapshot features (use during training only, NOT at score time):** they're in `post_grad_outcomes.feature_*` but captured at graduation. Including them in training would leak future state into prediction. Score-time analogues from Lane 9's curve replay are the proxy.

### Bucket cutoffs bimodal-cliff finding — third pre-fix structural diagnosis (2026-05-06 evening, pre-cutover)

Daemon's first successful rebuild fired at T+24h calibrated-shadow window with n=2,336 calibrated samples. Cutoffs populated, but `high_min ≈ med_min` (1e-9 epsilon apart) — distribution-shape collapse, not a daemon bug.

**Root cause:** isotonic regression on the 14h training window produced a step-function output with a hard ceiling at `6/53 = 0.11320754716981132`. **8.60% of all calibrated predictions land at exactly this single value.** Above the ceiling: 5 distinct values, each n=1. Below: smooth 91% tail. p95 and p99 both lock onto the ceiling because 8.6% mass swallows both percentiles.

**Pre-registered fallbacks (a/b/c) all fail against this data:**
- (a) Adjust percentiles → 8.6% > top-3% > top-0.5%; both still hit ceiling
- (b) 2-bucket at ceiling → ~30 alerts/hour, way too noisy
- (c) Hold cutover → doesn't fix anything; training data shape won't change without retrain

**Revised spec (option e, approved + implemented + tested):**
- `HIGH = calibrated > ceiling_value` → above-ceiling outliers (~5/week)
- `MED = calibrated == ceiling_value AND raw_GBM ≥ raw_gbm_p97` → at-ceiling top-rank (~10/day)
- `LOW = otherwise`

This respects the bimodal data shape: above-ceiling outliers (rare, "model unusually confident"), at-ceiling top-rank (daily signal "strongest in the at-ceiling cluster"), below-ceiling rest. Same volume profile as the original spec's HIGH/MED/LOW intent, achieved through a structure that matches reality.

**Future simplification path:** the cliff is a 14h-training-window artifact. Post-cutover, accumulating data smooths the upper tail of isotonic. The daemon includes an automatic fallback: when no calibrated value clears 1% mass on rebuild, it switches to standard percentile cutoffs (top-1% HIGH, top-5% MED). The bimodal-cliff workaround sunsets when the data outgrows it.

**Status:** spec approved, implementation in tree (`bucket_cutoffs.py` rewritten with bimodal-aware logic + `bucket_for(cal, raw_gbm)` signature; wiring updated; test suite extended with bimodal + regression-vs-old-logic + smooth-fallback tests; all passing). Track B held pending cutover deploy.

**Receipts moat — third pre-fix diagnosis in 48 hours:**
1. 2026-05-05 morning — deployed kNN saturation (committed eaab3f5)
2. 2026-05-05 evening — calibrated GBM over-confidence vs `actual_graduated` (Gate 5 over-confident branch fires, committed 2aeba1d)
3. **2026-05-06 evening — calibrated GBM bimodal cliff (this commit)**

Each diagnosis publicly committed before the corresponding fix shipped. Single events are dismissible; consecutive ones in a tight window are evidence of the discipline pattern functioning under live conditions.

Writeup: [docs/research/bucket_cutoffs_bimodal_finding.md](docs/research/bucket_cutoffs_bimodal_finding.md).

### Deployed k-NN saturation — structural alert failure diagnosed 2026-05-06 morning (pre-cutover)

Discovered during Track A calibrated-shadow window @ uptime 2.14h: the deployed k-NN's absolute-threshold alert system has been structurally failing since deploy. Not transient. Not a Track A regression.

**Evidence:**
- 4h post-Track-A: 286 in-lane predictions, **zero crossed `predicted_prob >= 0.50`** (let alone the 0.70 alert threshold)
- Pre-Track-A baseline: 3-7 alerts per 6h sometimes, zero alerts per 6h other times — irregular firing with no transparent cause
- Calibrated GBM shadow distribution (n=151) confirms the structural cause: 74.8% of calibrated values land in [0,0.05) because the live graduation rate genuinely sits there. Any honestly-calibrated model anchors below ~0.20.

**Diagnosis:** alert quality isn't a threshold-tuning problem. It's a base-rate-calibrated-bucket problem. No absolute threshold gives consistent daily alert volume because the underlying live distribution doesn't support 0.70-confidence calls.

**What Track B cutover fixes:** HIGH/MED/LOW bucket framing (top-1% / top-5% percentile of calibrated scores) is the only honest way to fire alerts when live base rate is ~5%. Same predictive signal, fundamentally different alert UX. Self-correcting via 24h cutoff rebuild.

**Single-user-cohort caveat:** 1 active grad_prob alert rule today (likely the developer). Felt impact n=1, structural diagnosis applies to any user on the same threshold.

**Strategic implication:** Track B isn't "ship the new model" — it's "fix the alert system that's been silently failing as long as the product has existed." Cutover urgency higher than originally framed; B2B narrative sharper.

Writeup: [docs/research/deployed_knn_saturation_diagnosis.md](../docs/research/deployed_knn_saturation_diagnosis.md). Timestamped pre-cutover so the receipts trail proves diagnosis preceded fix.

### gbm_shadow sticky-load-failure fix (formally pre-registered 2026-05-05 late evening, before deploy)

**Bug found by reading `web/gbm_shadow.py:42-77` after the 21h dual-write peek showed only ~94 min of clean data.** `_MODEL_LOAD_FAILED` is a module-level sticky boolean — once it flips to True (e.g., on a transient `/data` volume mount race at first call), every subsequent call short-circuits and returns None for the lifetime of the process. There's no retry. The "94 min of clean data, then 19h of nothing, then 94 min again" pattern is a process restart unlatching the bug, not a Lane 11/12 observer issue.

The lazy-load-on-first-call pattern compounds it: first call timing depends on observer activity (when the first mint hits the lane gate), not deploy timing — racing with volume mount in unpredictable ways.

- **Hypothesis:** the silent-failure mechanism is `_MODEL_LOAD_FAILED` latching after a transient first-call failure. Retry-with-backoff + eager warmup + status surfacing eliminate it.

- **Three-part fix (all in `web/gbm_shadow.py` + one-line wire from `main.py`):**
  1. **Replace sticky boolean with retry-with-backoff.** Track `_last_load_attempt_at` and `_consecutive_load_failures`. Backoff: 60s → 5min → 30min → 2h, capped. Never give up entirely. Worst case becomes 2h, not 19h.
  2. **Eager `_warmup()` at app startup.** Call from `main.py` startup hook so the load attempt happens immediately, not on first prediction. Logs at startup show exactly when model became available (or didn't).
  3. **Surface load state to `/api/status`.** Add `gbm_shadow.stats_snapshot()` fields to status response. External monitoring can verify `model_loaded=True` without querying predictions table.

- **Verification (decision rule, applied at 24h post-deploy):**
  - Monitor `/api/status` for `model_loaded=True` consistently.
  - Run dual-write 12-24h after deploy; verify shadow conversion stays ~100% on post-deploy predicted_at filter.
  - **PASS:** conversion >95% across at least one full 24h cycle (covers all UTC hours including catastrophic 03-06 + 16-18) → bug closed.
  - **FAIL:** conversion drops below 95% in any 1h window during first 24h → bug not fully solved; surface and re-investigate.

- **Time bound:** ~30-45 min including local syntax check + fly deploy + verification curl.

- **Independent of:** the calibration-finding (Finding 2 from the 21h peek). Even with reliable dual-write, the AUC-based ship-replace gates don't catch distribution-shift over-confidence. Gate amendment is its own next-session item, not blocked by this fix.

### Dual-write conversion verification ✅ done 2026-05-05 late evening (100% on post-deploy predictions; "68%" was measurement artifact)

**RESULT (per pre-registered rule, applied fresh):** ≥95% gate passes decisively. No action required.

| Window (relative to deploy times) | total | with shadow | conversion |
|---|---:|---:|---:|
| 42-50 min ago (post-deploy 2) | 14 | 14 | **100%** |
| 50-65 min ago (post-deploy 1, pre-deploy 2) | 21 | 21 | **100%** |
| 75-90 min ago (pre-deploy 1) | 18 | 1 | 5.6% |

**Mechanism behind the early 68%/28% reading:** the dual-write columns are populated only on POST-deploy INSERTs. Pre-deploy rows had NULL shadow (column didn't exist at insert time). Those rows can only acquire a shadow value via a post-deploy UPSERT — which requires the mint to still be in the in-prediction-window (15-90s age). Mints that aged past 90s before deploy never got re-scored, so their NULL shadow stuck. Any query window that includes both pre- and post-deploy `predicted_at` values will show artificially low conversion.

**Validation method (correct one):** filter strictly to `predicted_at > deploy_timestamp`. With that filter, conversion is 100% (35/35 across the two post-deploy windows).

**Update for future verification:** subsequent dual-write conversion checks should use a strict post-deploy predicted_at filter, not a calendar-time filter. Pre-deploy NULL rows are noise, not signal.

### Dual-write conversion verification (formally pre-registered 2026-05-05 late evening, before query)

The first 30 min post-deploy showed 26/38 (68%) shadow conversion. If non-conversion is persistent, the dual-write data is biased: GBM-vs-k-NN comparison is on a non-random subset of mints (likely systematically different — e.g. sparse-trade or partially-enriched). Decision-grade output from the 24-48h dual-write window depends on knowing whether the early 68% was a warmup transient or a structural gap.

- **Hypothesis:** shadow conversion has normalized to **≥95%** as live mints accumulate enough trade history for full feature extraction; the 68% was a deploy-warmup transient (model load + index cold-walk + fresh observer state) that resolves naturally.

- **Method:** SQL on predictions table for last 3 hours of post-deploy data (excludes deploy moment + immediate warmup). Compute gbm_shadow_features_complete=1 / total ratio for in-lane predictions (age_bucket ∈ {30, 60}). If <95%, group non-converters by which feature(s) are NULL most frequently in the lane6_features dict, and check whether non-converters cluster on age_bucket / bundled / manufactured_pump.

- **Decision rule (frozen pre-query):**
  - **≥95% conversion:** dual-write data is clean. No action; the 24-48h window proceeds toward decision-grade output as planned.
  - **80-95% conversion:** flag in the dual-write writeup as a bias caveat (e.g. "GBM scored 87% of in-lane predictions; 13% non-conversion clustered on [feature/segment]"). Don't pause, but disclosure is mandatory.
  - **<80% conversion:** structural gap. Bias risk to the cutover decision. Investigate root cause (most-missing feature, trigger conditions) and fix BEFORE the 24-48h window resolves.

- **Time bound:** 5-10 min. Cheap query + analysis.

### runner_prob recalibration (formally pre-registered 2026-05-05 late evening, before deploy)

Per Lane 13's mechanism 2 (curve overshoot from fast rebuild cadence): 15-min calibration rebuilds chase recent samples, the curve corrects past historical over-confidence, day-to-day variance produces large swings (-35pp → +5pp in 18h on 2x_from_now). Lane 13's recommended fix: slow rebuild cadence → let anchors stabilize before next refresh.

- **Hypothesis:** slowing the calibration rebuild cadence from 15 min to 1-2 h reduces per-window calibration variance without introducing new lag-related pathology. The trajectory across rolling 24h windows should flatten toward zero with smaller swings.

- **Method:** change calibration daemon rebuild interval, observe `/api/scope`'s rolling 24h calibration delta over the next 3-7 days. Re-run Lane 13 stability analysis at the end of the window. The change is reversible (env var or config), low-risk.

- **Three sub-actions (all 2026-05-05 evening):**
  1. **Slow calibration rebuild cadence 15min → 1-2h.** ~5 lines of config change in the calibration daemon. Mechanism 2 fix.
  2. **Sharpen /api/scope caveat** from "overconfident by ~12pp" framing to non-stationary framing: "magnitude calibration shows non-stationary behavior over recent rolling windows; treat as a ranking signal, not a literal probability." Docs/copy change only.
  3. **Defensive 1-line fix on the dead-code "scale toward (1,1)" branch.** Per Lane 7 audit: branch is currently dead because curves include 1.05 anchor. If curves ever lose that anchor, the branch wakes up and produces saturation bypass. `if xN < 1: return yN` prevents the footgun. One line.

- **Decision rule (applied at end of 3-7 day window):**
  - **Variance drop ≥30% on rolling-window |delta|** AND no new pathology (e.g. no lag-induced stale-anchor issues showing up in /api/accuracy) → ship the slower rebuild as the new default; close the recalibration thread.
  - **Variance drop 10-30%** → partial gain; consider further slowing (4h?) before declaring done. Re-pre-register if so.
  - **Variance unchanged or worsened (<10%)** → mechanism wasn't M2 (curve overshoot from fast rebuild). Escalate to mechanism 3 (oscillating instability) or storage-path bug hunt per Lane 13's escalation tree.

- **Time bound:** change ships now; evaluation lands ~3-7 days from now. Not blocking on anything else. Lane 12 instrumentation can run in parallel.

### Retrain v1 dual-write window (formally pre-registered 2026-05-05 late evening, before deploy)

After cutover steps 1-3 ship (pkl push + dual-write wiring + verify), GBM scores are computed in shadow alongside k-NN. k-NN remains source of truth; GBM is logged to new column `grad_prob_gbm_shadow` only. 24-48h observation window before flipping alerts.

- **Hypothesis:** GBM and k-NN produce convergent predictions on shared features, with GBM scoring higher on non-bundled mints (Lane 9 expectation: ~+14pp AUC closure ≈ ~5-15pp avg score lift on the non-bundled population that the deployed k-NN under-scores).

- **Method:** dual-write GBM and k-NN on every score event for 24-48h. Persist both to predictions table. End-of-window analysis: distribution of (grad_prob_gbm - grad_prob_knn) deltas per population (bundled / non-bundled), per age_bucket (30 / 60), error rates, latency.

- **Decision rules at end of window (all four "go" conditions must be met):**
  1. **Convergent on bundled, divergent in expected direction on non-bundled** → cutover green
  2. **GBM ≥0.15 abs delta on >20% of bundled mints** → PAUSE, investigate bundled-specific feature interaction the test split didn't catch (possible: recent feature drift or live-vs-training feature distribution mismatch)
  3. **GBM nulls/errors on >2% of live mints** → bug in live feature extraction vs training; fix before cutover
  4. **GBM scoring adds >250ms p95 latency to score path** → optimization pass before cutover

- **Time bound:** 24h minimum, **48h preferred** to capture the full daily cycle including Lane 11's catastrophic observer windows (03-06 UTC + 16-18 UTC).

- **Cutover criteria:** all four go conditions met + fresh-eyes review of the dual-write writeup before the actual flip. Cutover is its own decision moment, not a continuation of dual-write.

- **Safety properties locked in pre-deploy (cutover steps 1-3):**
  - Additive only — k-NN remains source of truth for grad_prob and all downstream surfaces
  - GBM scoring wrapped in try/except — errors never break the live k-NN pipeline
  - New column `grad_prob_gbm_shadow` (additive); existing `predicted_prob` semantics unchanged
  - Feature-flag rollback via env var `GBM_SHADOW_ENABLED` (default off → enable post-deploy → disable instantly if anything goes sideways)

- **Parallel workstreams during the dual-write window** (don't gate each other):
  - **Lane 12 observer instrumentation** — INFO-level logging on `src/observer.rs` websocket events; captures Layer 1 mechanism over the same 24-48h calendar window
  - **runner_prob recalibration** (Lane 13 fix) — slow rebuild 15min → 1-2h + sharpen /api/scope caveat + defensive 1-line dead-code fix; ~1h total

### Retrain v1 ✅ done 2026-05-05 late evening — SHIP-REPLACE: GBM with full features

**RESULT (per pre-registered ship-replace gates, applied fresh on random + temporal split):** All four gates PASS.

| Gate | Random (n=621 test) | Temporal (last 20%) | Status |
|---|---:|---:|---|
| 1 (≥10pp non-bundled AUC improvement vs deployed k-NN) | +21.52pp | +16.53pp | PASS |
| 2 (≤3pp bundled regression — Lane 14 tightened) | +19.27pp | +0.37pp | PASS |
| 3 (≥1.5× earliness ratio at age=30 non-bundled) | 1.53× | 1.87× | PASS |
| 4 (architecture: GBM beats k-NN-full by ≥10pp on nb) | +11.88pp | n/a | PASS |

**Per pre-registered architecture rule: SHIP-REPLACE with single-track GBM (full features).**

**Key numbers:**
- GBM non-bundled AUC: 0.7864 (random) / 0.7898 (temporal) — vs deployed k-NN 0.5712 / 0.6245
- GBM bundled AUC: 0.6860 (random) / 0.5337 (temporal) — vs deployed k-NN 0.4933 / 0.5300
- Corpus: 3,101 rows from 4,884 base joinable rows; 1,694 missing curve files (observer-leak ceiling per Lane 11)
- Sample timespan: 4.2 days; temporal validation falls back from "last 7d" to last-20%

**Lane 14 update:** the small-sample bundled regression (-5.92pp at n=65) flipped to **+7.66pp improvement at n=575** vs Run B baseline. Lane 14's Branch 1 (sample noise) prediction confirmed; the hybrid action's monitoring instrumentation is no longer load-bearing for the ship decision.

**Stopping point:** model artifact persisted at `/tmp/lane2/gbm_v1.pkl` + meta at `gbm_v1_meta.json`. Live cutover (flipping `/api/live` and TG alerts to GBM scores) waits for fresh-eyes review tomorrow morning.

**Tomorrow's first action (~30 min):** review decision artifact → push pkl to Fly → wire GBM scoring path → dual-write phase → cutover.

Decision artifact: [docs/research/retrain_v1_decision.md](docs/research/retrain_v1_decision.md).

#### UI threshold update — paired with calibrated-GBM cutover (formally pre-registered 2026-05-05 evening, before code/copy change)

**Context.** Calibrated GBM puts 93% of predictions in [0,0.1), <0.5% above 0.3 (vs deployed k-NN's 98% in [0,0.1)). The deployed `predicted_prob ≥0.7` WATCH threshold won't translate naively — virtually nothing post-cutover crosses 0.7. UI must change at the same moment as the model cutover; otherwise alerts go silent, OR alerts are decoupled from the model semantics, OR the UI displays an absolute-probability number whose meaning has shifted under it.

**Three options surveyed** (per the calibration writeup):
1. **Lower thresholds.** Most invisible to users; quietly accepts that "70%" was always a model-scale artifact rather than a probability claim.
2. **Ranking buckets.** "HIGH / MEDIUM / LOW" matches what the model actually does (rank). Avoids absolute-probability semantic problems entirely.
3. **Absolute probability + base rate framing.** "calibrated probability: 0.07 (live base rate ~5%)". Maximally honest, requires more user cognitive load.

**Decision (frozen):** ship a **#2+#3 hybrid**. HIGH/MED/LOW bucket is the headline (what users act on). Calibrated probability + base-rate context is supporting detail. Drop the bare "X% to graduate" framing entirely — it was a model-scale artifact even with k-NN.

**Why hybrid:**
- Headline matches the model's real capability (ranking, validated by AUC + top-N preservation).
- Magnitude detail preserved for sophisticated users who want to size positions or compare across mints.
- Compounds with yesterday's UX honesty pass (warming gates, runner_prob "directional only" caveat, bundle disclosure footer, n≥30 act_slice rule). "We don't show numbers that aren't calibrated" becomes "we don't show numbers that misrepresent the model's actual capability."
- Avoids the worst case of #1 alone: "WATCH fires when graduation probability ≥0.05" reads strange in copy and quietly admits the threshold was never what users thought.

**Specification (hybrid, locked here):**

- **Bucket cutoffs (calibrated probability):**
  - **HIGH:** calibrated_prob ≥ top-1% percentile of last 7-day distribution (computed daily, seeded from the post-fix dual-write window). Roughly: calibrated_prob ≥ 0.20 at current live distribution (top-1% of last 1,258 dual-write rows is ~0.20).
  - **MEDIUM:** top-5% percentile, currently ~0.10.
  - **LOW:** below MEDIUM. Not displayed in alerts; visible on dashboard as "low confidence."
  - Cutoffs are dynamic (rebuilt nightly) so they self-correct as live distribution drifts. Lock the rebuild cadence at 24h to avoid Lane 13's curve-overshoot pattern from a faster rebuild.

- **Alert template (TG WATCH):**
  - Headline: `WATCH 🟢 HIGH` / `WATCH 🟡 MED` (drop bare LOW from alerts)
  - Subline: `graduates: ~XX% (live rate ~5%)` — the calibrated_prob and the comparator base rate, both sourced from /api/scope.
  - Drop "X% probability" as headline copy; if shown, always paired with base-rate comparator.

- **Dashboard `/`:**
  - Primary column: bucket badge (HIGH/MED/LOW with consistent color encoding).
  - Secondary column: calibrated_prob displayed with one decimal of precision (0.07, 0.18) — never 0.0728103.
  - Hover/expand: "calibrated probability vs live base rate" tooltip explaining the metric.

- **/api/scope predictions section:**
  - Replace `graduation_prob` description from "probability the mint graduates" to: "calibrated probability that the mint reaches the bonding-curve graduation threshold (vsol≥115). Live base rate: ~5% on in-lane mints. Display layer surfaces this as HIGH/MED/LOW ranking buckets; absolute probability available for sizing decisions."
  - Add `calibration_layer: "isotonic_v1"` field documenting the post-2026-05-05-cutover calibration. Versioned so future calibration updates don't quietly redefine the field semantics.
  - Keep `valid_window_s: 60`, `label_source: "vsol_threshold_115"`.

- **/api/live response:**
  - Add `grad_prob_bucket: "HIGH"|"MED"|"LOW"` field alongside existing `grad_prob`.
  - Existing consumers reading `grad_prob` continue to work; the value is now calibrated (so absolute magnitudes shift). Document the field-meaning change in the Cutover writeup explicitly.

- **Tamper-evident ledger leaf format:**
  - Bump leaf version (V2 → V3) to record that `grad_prob` semantic moved from k-NN raw to calibrated-GBM. Old commits remain verifiable; new commits use V3.
  - V3 leaf adds `calibration_layer` field locked into the merkle leaf.

**Risk surfaces (named for transparency):**

1. **Absolute-prob consumers in the wild.** External services or scripts that depend on `grad_prob ≥0.7` will silently stop firing post-cutover. Mitigation: `/api/scope` documents the field-meaning change; pre-cutover, post a notice on `/changelog` (or equivalent) flagging the semantic shift. Treat as a breaking change to the field definition, even though the schema didn't break.

2. **Bucket cutoffs feel arbitrary at first.** "Top-1% percentile" is a relative measure — different from absolute thresholds. Mitigation: in /api/scope, document the cutoff method explicitly + show current cutoff values + commit to a daily rebuild log so cutoffs are auditable.

3. **The bucket label distribution skews bundled.** The 88% bundled in lane-eligible predictions persists post-calibration. WATCH HIGH alerts may surface 88% bundled mints. Mitigation: name the population skew in the WATCH alert subline (or in `/api/scope`), don't pretend it's a balanced sample. "WATCH HIGH on bundled-mint slice" is honest framing.

**Decision rule (post-launch validation, applied at +7 days):**
- **Clean:** users react to bucket label, not absolute number. Alert engagement steady-or-better vs pre-cutover. /api/scope traffic shows external consumers reading the new fields. → Cutover holds.
- **Mixed:** alert engagement drops but bucket label is being read. → Investigate copy/template; the magnitude framing may need work. Don't roll back.
- **Regression:** alert engagement drops AND external consumers report breakage. → Roll back to k-NN scoring (the model is still wired in shadow); re-pre-register before next attempt.

**Out of scope (explicitly):**
- Replacing the underlying signal architecture (k-NN, GBM, calibration) — that's the upstream cutover.
- Changing other product fields' display (rug_prob, runner_prob_*, post_grad_survival_prob).
- Migration of historical predictions UI (old data displays with old framing; new data with new).

**Time bound:** ~30-45 min code + copy work, when the cutover ships. Pre-registered here, locked, no scope-creep at deploy time.

#### Gate 5 verdict ✅ done 2026-05-05 evening — OVER-CONFIDENT branch fires → isotonic recalibration cascade

**RESULT (per Gate 5 pre-registered rule, applied fresh post-data):** OVER-CONFIDENT branch fires unambiguously.

| Bin | n | predicted | actual_graduated | delta |
|---|---:|---:|---:|---:|
| [0.0-0.1) | 20 | 0.073 | 0.050 | -2.3pp ✓ clean |
| [0.1-0.3) | 414 | 0.220 | 0.031 | **-18.9pp** |
| [0.3-0.5) | 416 | 0.374 | 0.058 | **-31.7pp** |
| [0.5-0.7) | 52 | 0.583 | 0.058 | **-52.5pp** |

**Branch evaluation (12h+ post-fix dual-write, n=904 resolved):**
- Bins ≥0.3: 2/2 fire over-confident (-31.7pp on n=416, -52.5pp on n=52). **Majority = 2/2 = 100%.**
- Multi-fire check: zero bins fire under-confident → no Mixed verdict.
- Transition-zone check: -31.7pp and -52.5pp are far past ±5pp threshold → no boundary noise.
- Sample-size check: n=416, n=52 both ≥ 30 floor. Verdict is decision-grade, not premature.
- Label-mismatch caveat: check uses `actual_graduated` (training label was `sustained_30m`, a stricter outcome). Since `actual_sustained ≤ actual_graduated`, the calibration gap vs the proper label is **at least** the magnitudes shown — possibly worse.

**Pre-registered action: isotonic recalibration cascade.** Train an isotonic regression layer on the post-fix dual-write resolved outcomes mapping raw GBM probabilities → calibrated probabilities. Ship raw GBM + calibration step (analogous to k-NN + Platt step in the deployed pipeline). Preserves ranking gain (Gates 1-4 already passed), fixes magnitude.

**Updated cutover sequence:**
1. **Continue 24h gbm_shadow verification window** — already passing through catastrophic UTC windows; remaining ~4-10 hours confirmatory.
2. **Train isotonic layer** on the 14h+ clean post-fix data. sklearn `IsotonicRegression` fit on `(raw_gbm_score, sustained_30m)` pairs from `post_grad_outcomes` join. Local train/held-out split for validation. ~30-45 min.
3. **Re-validate Gate 5 on calibrated output.** Apply the same ±5pp threshold to the calibrated scores on held-out. PASS condition: all bins ≥0.3 within ±5pp.
4. **Save `gbm_v1_isotonic.pkl` artifact locally** (NOT pushed to Fly until calibrated cutover sequence is ready).
5. **Cutover sequence (calibrated GBM):** push pkl + isotonic layer to Fly → wire calibration step into gbm_shadow scoring path → brief calibrated-shadow window → fresh-eyes review → flip alerts to calibrated GBM.

**On the k-NN saturation finding** (separate, durable observation): k-NN's deployed model is calling 887/904 = 98% of resolved post-fix predictions in [0,0.1). Calibrated WHERE it scores (+1.8pp clean), but the entire distribution is squished against zero. Product works today because ranking within the compressed band still fires often enough. Calibrated GBM should produce a similar overall rate distribution (isotonic maps to live rates) but with better mint-to-bucket assignment — that's where the +14pp AUC ranking gain shows up post-calibration.

#### Gate 5 — calibration check (added 2026-05-04 evening, pre-registered before data lands)

**Why this gate exists.** Gates 1-4 are AUC-based — they measure ranking, not absolute calibration. Late-day spot-check on the dual-write window (~21h period, mostly silently broken — see "gbm_shadow silent failure" entry) showed GBM appears over-confident vs `actual_graduated` by ~18pp on bin [0.1-0.3) and ~30pp on bin [0.3-0.5). The check used the wrong label (training was `sustained_30m`, check was `actual_graduated`), and `actual_sustained ≤ actual_graduated`, so the calibration gap vs the proper label is **at least** these magnitudes, plausibly worse. Mechanism: distribution shift between training corpus (resolved post-grad outcomes, mostly graduated) and live distribution (most live mints don't graduate). Standard ML problem; gates 1-4 don't catch it. Without Gate 5, cutover ships ranking gain plus inflated absolute probabilities — exactly the runner_prob mis-scaling pattern we already caveated today, but on the headline graduation field.

**Hypothesis.** GBM's absolute probability calibration vs `sustained_30m` on dual-write resolved outcomes is within ±5pp on bins ≥0.3.

**Sample.** Predictions table rows where:
- `predicted_at >= gbm_shadow_fix_deploy_timestamp` (the post-fix clean window — pre-fix data is corrupted by silent-failure pattern)
- `grad_prob_gbm_shadow IS NOT NULL` AND `gbm_shadow_features_complete = 1`
- Joined to `post_grad_outcomes` on mint where `sustained_30m IS NOT NULL`
- **Sample-size requirement: n ≥ 30 per probability bin in the analyzed range.** If bins ≥0.5 don't reach n=30 within the dual-write window, extend the window before applying the rule.

**Method.**
- Bin GBM scores into [0.0-0.1), [0.1-0.3), [0.3-0.5), [0.5-0.7), [0.7-1.0]
- Per bin, compute mean predicted probability (avg of `grad_prob_gbm_shadow`)
- Per bin, compute actual sustained_30m rate (mean of `sustained_30m`)
- Compute signed delta per bin (predicted − actual)
- Stratify by `bundle_detected` to ensure check holds across both populations (per Lane 1, ranking gain is concentrated in non-bundled — calibration could differ similarly)

**Decision rule (4-branch, with Lane 14 multi-fire discipline).**

- **Clean: `|delta| ≤ 5pp on all bins ≥0.3`.** GBM is well-calibrated against the live distribution. Cutover proceeds with raw GBM.
- **Over-confident: `delta < -5pp on majority of bins ≥0.3` (predicted > actual).** GBM's absolute probabilities are inflated. Cutover requires **isotonic recalibration cascade**: train an isotonic regression layer on the dual-write resolved outcomes that maps raw GBM probabilities → calibrated probabilities. Ship raw GBM + calibration step (analogous to current k-NN + Platt step in the deployed pipeline). Preserves the ranking gain, fixes the magnitude.
- **Under-confident: `delta > +5pp on majority of bins ≥0.3` (predicted < actual).** Same recalibration cascade, opposite direction. Action identical: ship with isotonic layer.
- **Mixed: some bins clean, others off (≥1 bin clean AND ≥1 bin off by >5pp on bins ≥0.3).** Calibration is non-stationary or bin-dependent. Cutover BLOCKED. Ship GBM as supplementary score only (display alongside k-NN with explicit "directional only, magnitude pending" caveat — same pattern as runner_prob today). Pre-register a separate investigation into the mechanism before any further cutover attempt.

**Multi-fire discipline (Lane 14 lesson).** Branches are evidence-types, not exclusive verdicts. If two branches both fire (e.g., over-confident on bins [0.1-0.3) but under-confident on bin [0.5-0.7)), the verdict is "Mixed" — even if the count of bins matches "Over-confident" majority. The asymmetric direction of error itself is a signal of distribution-shift mechanism, not a signal of overall direction.

**Transition zone (Lane 13 lesson).** Crossings at the ±5pp boundary count only if magnitude > 1pp on both sides. A bin where delta is +0.2pp and another where delta is -0.3pp doesn't cross the threshold meaningfully — both are within noise of "clean."

**Sample-size escape hatch.** If the dual-write window completes the 24h verification gate (gbm_shadow fix verification) but Gate 5 doesn't have n≥30 in bins ≥0.5, the calibration analysis is premature, NOT a failure. Extend the window. Don't apply the decision rule on under-powered bins.

**What Gate 5 changes about the cutover sequence.** All 5 gates (1-4 AUC-based, 5 calibration-based) must pass for raw cutover. Gate 5 alternatives (recalibration cascade, supplementary-only) are explicit fallbacks with their own ship paths. Don't conflate "Gate 5 fails" with "cutover blocked" — Gate 5 specifically routes to one of three deploy patterns based on which branch fires.

**Discipline contract (re-affirmed):**
- This gate is frozen at this commit; no revising downward after seeing the result.
- Pre-registration captures the multi-fire and transition-zone refinements from Lane 13/14 explicitly.
- Negative or ambiguous results get the same treatment as positive ones — published in `docs/research/` regardless.

#### Architecture: k-NN with full feature set is default; GBM only if it clears the ship-replace bar

Lane 2 + Lane 9 evidence:
- GBM with full features: AUC 0.74 on non-bundled (+14pp over k-NN's 0.60 with old feature set)
- GBM with same features but predicting graduation (apples-to-apples vs k-NN): comparison still pending

Three architecture options (decision lands at retrain time, not pre-committed):
1. **k-NN with the full feature set above** — simplest deploy, online-update properties preserved. **Default unless GBM clears the bar.**
2. **GBM (sklearn HistGB or LightGBM)** — replace k-NN only if non-bundled held-out AUC improvement ≥10pp at threshold ≥0.5 AND no worse than 5pp drop on bundled.
3. ~~Two-model per-population architecture~~ — REJECTED by current evidence (Lane 2 overlap=4, Lane 9 overlap=3, no signal of fundamentally different feature mechanisms).

#### Ship-replace decision criteria (frozen here)

New model ships ONLY IF all three:

1. **Non-bundled AUC improvement ≥10pp** at threshold ≥0.5 on a fresh held-out window (last 7 days of data not in training)
2. **Bundled performance regression ≤5pp** (avoids breaking existing fires)
3. **Earliness check (Lane 10 secondary metric):** ACT-eligible rate at age_bucket=30 on non-bundled is **at least 1.5× current k-NN** (lower bar than Lane 10's 2× since this is post-retrain validation, not directional confirmation)

#### Ship-augment (fallback)

If new model wins on non-bundled but loses on bundled, run both: existing k-NN for bundled-pattern fires, new model for non-bundled-shaped queries. More complex routing; only if ship-replace fails on bundled regression.

#### Don't-ship (also valid)

Investigate which of (architecture / features / label) is the failure mode. Pre-register the next iteration BEFORE training again.

#### Companion fixes (sequenced AFTER retrain ships, in parallel)

**Layer 1 — observer collection leak (Lane 11 + Path A).**
- Pattern: observer flush activity drops 0.35× during 03-06 UTC and 16-18 UTC. ~30% of non-bundled graduators never enter the corpus.
- Mechanism not yet identified at code level. Smoking gun is the 12-hour daily cycle.
- **Tomorrow's first companion-fix action:** add INFO-level instrumentation in `src/observer.rs` for websocket connect/disconnect events, reconnection attempts (success/failure/duration), and rolling trade-rate heartbeat. After 24-48h of logs in the catastrophic windows, fix is scoped.
- **Until Layer 1 fixed:** retrained model coverage caps at ~70% of non-bundled grads. Acceptable — fix in parallel.

**Layer 3 — suppression matrix bias.** ~~REJECTED by Lane 8.~~ Matrix filters 11.8% of non-bundled ≥0.7 candidates (below 20% threshold). Drop from retrain scope. Single-track implementation.

**runner_prob calibration (Lane 7).** Mis-scaled overconfident by ~12pp on bins ≥0.5. /api/scope and /accuracy now flag this as "directional, magnitude recalibration pending" (shipped 2026-05-05 evening). Recalibration via existing `apply_calibration` infrastructure — not in retrain scope. Likely fix: ensure calibration daemon builds curves for runner_prob_*_from_now tiers and that score_full's apply_calibration handles saturating raws. Audit + fix is its own sub-task.

#### Positioning note

The ACT-eligible path is structurally a **non-bundled product**. Bundled ≥0.7 candidates trip entry-quality at 100% (Lane 8). Lane 8's 95.5%-sustain ACT-path quality is "high-quality slice of non-bundled mints caught before they pump," NOT "high-quality slice across populations." Two products emerging:
- **WATCH** — broad-population grad_prob alert; sustain mixes ~32%/53% bundled vs non-bundled
- **ACT** — non-bundled mints caught before they pump past 2× launch; 95.5% sustain on the population that gets there

Land this in user-facing positioning copy when retrain ships. Don't drift back to "WATCH is the product."

#### Concrete tomorrow-morning sequence

1. **Read `docs/research/today.md` and this scoping doc.**
2. ~~Live-pipeline prep~~ ✅ **DONE 2026-05-05 evening.** All 17 Lane 6 features were already in m_out (observer publishes them in ActiveMintSummary, snapshot carries them through). Added `m_out["lane6_features"]` namespace dict (additive only, zero existing-field changes verified on 55 live mints). Tomorrow's retrain reads `m_out["lane6_features"]` as a clean namespace.
3. **Curve-replay extraction at scale.** Lane 9's diagnostic ran on 3,076 rows; production retrain wants the full 4,849+ joinable population. Run extraction over the full set, address the ~1,773-row gap (mostly the same observer collection leak — accept the cap for retrain-1).
4. **Train + validate.** Both k-NN and GBM on the feature-rich set, with sustained_30m label. Apply ship-replace criteria above.
5. **Decide: ship / augment / don't-ship.** Pre-register implementation details if shipping.

In parallel:
- **Add observer instrumentation** to nail Layer 1 mechanism (per Path A's recommendation)
- **runner_prob recalibration audit** — check apply_calibration coverage for runner_prob_*_from_now tiers, audit saturation handling

#### Discipline

Every model file written gets a corresponding pre-registration update HERE before training begins. No "we tried it and it worked" without prior commitment to what "worked" means. Same rule that produced today's coherent outcome.

### Retrain scoping (original placeholder — superseded by draft above)

- **Hypothesis:** the current k-NN architecture, retrained on the FULL 4,256 outcomes (not the slice the current index covers) and with Lane 6's 17 unused features added, materially outperforms the current model on non-bundled graduators specifically. Optionally: a GBM trained on the same labeled set outperforms the retrained k-NN (Lane 2's architecture comparison).
- **Method (pre-registration only — not the actual training run):**
  - **Training set:** all 4,256 resolved post_grad_outcomes (not just the ~150k observer-curve corpus). Bundled and non-bundled both included; do NOT filter `hard_bot_signal` mints out of training (Lane 6 finding: hidden mints have 10-15× higher rug rates, and hiding them throws away predictive signal).
  - **Feature set:** existing 6 + Lane 6's top 3 (`max_mult`, `vsol_acceleration`, `top3_buyer_pct`+`repeat_buyer_rate`) + the four flagged for non-bundled separation (`unknown_buyer_pct`, `low_history_pct`, `n_smart_in`, `sell_ratio`). Pre-register additional features as separate hypotheses if added beyond this set.
  - **Label choices to evaluate in parallel:**
    - **A) Graduation prediction** (current): `actual_graduated` binary. Reproduces the current model's task with new features.
    - **B) Sustained_30m direct**: trader-relevant outcome. Skips the graduation→sustain decomposition. Lane 2's GBM proof-of-concept also runs here.
  - **Architectures to compare:** k-NN (existing), gradient-boosted trees (Lane 2 proof-of-concept). Optionally logistic regression as a baseline.
  - **Validation:** stratified k-fold over the 4,256 outcomes, with explicit reporting of performance on the non-bundled subset (the population we're currently missing). Calibration curves per architecture.
- **Decision criteria (frozen at this pre-registration):**
  - **Ship-replace:** new model beats current k-NN's non-bundled performance by ≥10pp (e.g. current k-NN identifies 15% of non-bundled graduators with grad_prob ≥0.5; new model needs ≥25% at the same threshold). AND beats overall calibration on bundled population by no worse than 5pp drop.
  - **Ship-augment:** if new model is better on non-bundled but materially worse on bundled, run both in parallel — retain the existing model for bundled-pattern fires, route non-bundled-shaped queries to the new model. (More complex, only if ship-replace fails.)
  - **Don't ship:** new model isn't materially better. Investigate whether the failure is the architecture, the features, or the label choice. Pre-register next iteration before training again.
- **Strict pre-registration:** every model file gets a corresponding entry in this section before training begins. No "we tried it and it worked" without the prior commitment to what "worked" means.

### `post_grad_runner_prob` (peak ≥ N× within window)

`post_grad_survival_prob` measures "didn't dump within 30m at ≥80% of grad price." That captures the PumpSwap-rug failure mode cleanly, but doesn't directly measure "ran post-bond." A mint that drifts sideways at 0.95× scores high on sustain prob; so does Aw5SxKyY's +524%. Different outcomes for a trader.

A `post_grad_runner_prob` model — peak ≥ Nx within configurable window — is a phase-3 item, after we know whether sustain alone moves the needle (see pre-registered gate criterion above).
