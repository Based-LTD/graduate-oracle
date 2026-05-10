# Case Study 01 — Calibrated bucket vs. component-data composition

**Pre-registration commit.** Methodology, acceptance criteria, and outcome branches frozen here BEFORE any data collection. Per the receipts pattern: this commit must predate the data-collection window's start. If a frozen criterion needs revision after this commit, the revision must (1) be publicly committed before the data resolves the criterion, (2) refine or split rather than relax, and (3) explicitly surface the design flaw being corrected. Same shape as Finding 8's interim-criterion amendment (commit `f3f1f3e`, 2026-05-08).

**Series:** Case Study 01 (first of N planned commercial-comparison studies). The instrumentation harness built for this study is designed to be **reusable** — future studies are one config file + pre-reg writeup + scheduled run on the same harness. Each study extends the moat without artisanal rebuilding.

---

## Strategic context

graduate-oracle is positioned as a calibrated +1 prediction layer above component-data firehoses. The B2B integration thesis: trading-tool integrators wanting `should_we_alert(mint) → bool` cannot get that single field from raw component APIs without building their own composition, calibration, and receipts trail.

**GMGN's OpenAPI** exposes ~80 component fields per mint and a server-composed `--filter-preset strict` boolean inclusion. It does **NOT** expose a calibrated graduation probability. This study tests empirically whether graduate-oracle's calibrated lane-60s bucket commitment outperforms GMGN's server-side strict-preset composition on the same overlapping mint set.

**A negative outcome (graduate-oracle does not outperform) would mean the calibrated-bucket-alone product spec is wrong**, and the product must be reshaped around what the data shows actually adds value. The user has explicitly committed to this reshaping if the data goes that way. Per the Finding 7 sunset precedent (`7658639`, 2026-05-08), discipline holds whether the verdict supports the thesis or contradicts it. Three model-class attempts at sustain prediction all failed pre-registered acceptance and the feature was permanently retired — a closing-the-loop verdict that strengthens the receipts moat. **This study will execute its branches with the same discipline.**

---

## Hypothesis (frozen)

**H1 (primary):** Graduate-oracle's HIGH+MED bucket has higher graduation-precision than GMGN's `--filter-preset strict --type new_creation` on the overlapping mint set, by a margin of ≥10pp on samples with n≥30.

**H0 (null):** Graduate-oracle's HIGH+MED precision ≤ GMGN strict-preset precision OR difference within the noise band (|Δ| < 5pp).

---

## Methodology (frozen)

### Data collection

- **Window:** 48 hours, starting after Finding 8 interim gate verdict resolves at 2026-05-09T16:45Z. Planned start: 2026-05-09 ~17:00 UTC. Planned end: 2026-05-11 ~17:00 UTC.
- **Cadence:** 60s polls of both APIs over the full window.
- **Sources:**
  - graduate-oracle: **direct sqlite read** on the production daemon (no API rate limit; full feature snapshot per in-lane prediction). DB at `/data/data.sqlite`.
  - GMGN: `gmgn-cli market trenches --chain sol --type new_creation --filter-preset strict --raw` per poll, 60s cadence, normal auth.
- **Storage:** dedicated sqlite table `case_study_01_observations`, cleanly separable from production scoring tables. Schema frozen at harness-build time.

### Per-mint capture

For each mint that appears in EITHER source during the window, capture:

**1. From graduate-oracle:**
- mint address
- lane-60s prediction snapshot: `grad_prob`, `grad_prob_bucket`, `creator_history.grad_rate`, `vsol_velocity_30s`, `vsol_velocity_60s`, `bundle_pct`, `top_buyer_pct`, `smart_money_in`, `is_suspect`
- `predicted_at` timestamp
- eventual `graduated` flag (resolved against vSOL≥115 threshold within 24h post-prediction)
- peak multiplier observed

**2. From GMGN (snapshot at graduate-oracle's predicted_at, ±60s):**
- mint address
- `progress`, `smart_degen_count`, `renowned_count`, `top70_sniper_hold_rate`, `creator_created_open_ratio`, `bundler_rate`, `rug_ratio`, `holder_count`
- inclusion in strict-preset list at this snapshot (boolean)
- eventual graduation observed (mint reaches `completed` bucket within 24h post-prediction)

**3. Joint:**
- membership in graduate-oracle's HIGH+MED bucket (boolean)
- membership in GMGN strict-preset (boolean)
- both products' "called this a winner" prediction at lane-60s

### Outcome resolution

A mint is RESOLVED when one of:
- `graduated` flag flips true (reached bonding-curve threshold)
- 24h has elapsed since `predicted_at` with no graduation (declared failed)

Mints unresolved at the 48h window close + 24h grace period are EXCLUDED from the precision comparison (still reported as a sample-size note).

### Exclusion rules (frozen)

A mint is excluded from the primary precision comparison if:
- It is unresolved at the analysis cutoff (48h + 24h grace)
- The graduate-oracle prediction was made outside lane-60s (age >75s at prediction time)
- GMGN snapshot was not obtained within ±120s of graduate-oracle's predicted_at
- The mint was already graduated at first observation (cannot test a prediction)

---

## Acceptance criteria (frozen)

The study has THREE pre-registered outcome branches, each with a pre-drafted public writeup template (committed alongside this pre-reg, ready to fill numbers at outcome time):

### Branch A — Thesis supported

**Conditions ALL of:**
1. Sample size: ≥30 mints in graduate-oracle MED+HIGH bucket on overlap
2. Precision: graduate-oracle MED+HIGH precision ≥ GMGN strict-preset precision + 10pp
3. The precision lift holds when restricted to mints where BOTH products made a positive call (both said "winner")

**Action on Branch A:** Public writeup ships ("graduate-oracle's calibrated bucket outperforms component-composition by Xpp on the same mint set"). Case study becomes a B2B-integration prospect artifact. Twitter thread variant ships. Reusable harness configuration is documented for Study 02.

Template: [`case_study_01_gmgn_results_branch_a_template.md`](case_study_01_gmgn_results_branch_a_template.md).

### Branch B — Thesis undermined

**Conditions ANY of:**
1. Sample size sufficient (n≥30) AND graduate-oracle precision ≤ GMGN strict-preset precision
2. Sample size sufficient AND difference within ±5pp (calibrated bucket is not adding meaningful value over composition)

**Action on Branch B:** Public writeup ships with the negative finding. Product-reshaping discussion opens with concrete data: what does the data say IS adding value? (Maybe a hybrid bucket-plus-component output; maybe a different lane window; maybe a different prediction shape.) Pre-registered iteration-limit applies: this study triggers a product-spec reopen, not "let's run another comparison until we win."

Template: [`case_study_01_gmgn_results_branch_b_template.md`](case_study_01_gmgn_results_branch_b_template.md).

### Branch C — Insufficient sample / ambiguous

**Conditions:**
- n<30 in graduate-oracle MED+HIGH bucket on overlap
- OR resolution rate <70% at the 24h grace cutoff

**Action on Branch C:** Public writeup ships acknowledging the inconclusive result. Decision tree is pre-registered:

| Subcondition | Action |
|---|---|
| Overlap mint count was <30 in 48h | Extend collection by another 48h (one extension only — iteration-limit applies) |
| Resolution rate was the limiter | Redefine outcome resolution to a longer grace window (capped at 72h) and re-run analysis on existing data |
| Neither (both limiters fired) | Case study is published as inconclusive and the experimental design is itself the finding (graduate-oracle's lane-60s prediction rate doesn't generate enough overlap with GMGN's strict-preset to make this comparison feasible — that's a real finding about the addressable market shape) |

Template: [`case_study_01_gmgn_results_branch_c_template.md`](case_study_01_gmgn_results_branch_c_template.md).

---

## Pre-drafted writeup branches

The actual writeup texts (Branch A, B, C variants) are committed alongside this pre-registration as templates. The terminal numbers (precision figures, sample counts) are the only fields that change at outcome time. **Same publish-then-post pattern as Finding 8's pre-drafted TG/X messages and Finding 7h's pre-drafted PASS/FAIL branch operational changes.**

---

## Reusable harness design (notes for implementer)

The instrumentation built for this study should be parameterized for future studies:

- **Upstream sources:** swap `gmgn-cli` for any other firehose API (Pump.fun analytics, Phantom intelligence, Birdeye, etc.) via config-driven adapter pattern.
- **Comparison criteria:** swap "MED+HIGH bucket vs strict-preset" for any pair of boolean predicates over the captured feature snapshot.
- **Outcome resolution:** swap "graduation in 24h" for any post-hoc outcome metric (sustain, runner, peak mult).
- **Window length:** parameterized 24h-90d.

**Future studies the same harness supports** (pre-registered scope, no specific commitments yet):

- **Study 02:** graduate-oracle vs Pump.fun's own analytics (if/when they ship native predictions — defensive positioning before they decide build-vs-buy)
- **Study 03:** graduate-oracle vs Phantom's wallet-side intelligence (different B2B integration target, different competitive layer)
- **Study 04:** longitudinal — rolling HIGH/MED bucket precision over 90+ days, published quarterly
- **Study 05:** post-graduation behavior comparison (when/if a survival metric exists; today the field is permanently sunset per Finding 7i)
- **Study 06:** k-NN-historical vs GBM-calibrated head-to-head on the same lane (internal model evolution receipts)

The harness shape: one daemon, configurable upstream sources, configurable comparison criteria, frozen acceptance criteria per study. **Each study is one config file + one pre-reg writeup + one scheduled run.** That makes the commercial-receipts cadence systematic rather than artisanal.

---

## Schedule (frozen)

| Phase | Time | Action |
|---|---|---|
| Pre-reg commit | This commit | This document + 3 branch templates + BACKLOG entry pushed to graduate-oracle |
| Harness build | After this commit lands | Implementer scaffolds the reusable comparison daemon |
| Wait window | Until 2026-05-09T16:45Z | Finding 8 interim verdict closes |
| Collection start | After Finding 8 verdict | 48h daemon run begins (~2026-05-09T17:00Z) |
| Collection end | +48h | Daemon stops; analysis begins (~2026-05-11T17:00Z) |
| Grace window | +24h | Outcome resolution for last mints (~2026-05-12T17:00Z) |
| Branch verdict | +1h after grace | Pre-registered branch executes |
| Public writeup | +1h after branch | Outcome-appropriate template filled + committed |
| Twitter / TG | +30min after writeup | Pre-drafted variant ships |

Total elapsed from pre-reg commit to writeup: ~120 hours (~5 days).

---

## Discipline note

This study is not "marketing content with data attached." It's a methodologically pre-registered comparison whose result the team has publicly committed to publishing in any direction. The user named the $10M acquisition north star contingent on this kind of receipts work compounding over 12+ months; this study is part of that compounding.

If the data shows graduate-oracle's calibrated bucket adds a measurable precision lift over component composition: **that's the empirical foundation for the B2B integration sales motion.**

If the data shows it doesn't: **that's the empirical foundation for reshaping the product so it does.** Either outcome moves the moat forward. Same shape as Finding 7i — the sunset verdict made the receipts moat stronger, not weaker, because the discipline held under an unwanted result.

---

## Receipts trail

| Commit | Action |
|---|---|
| **(this commit) Case Study 01 pre-registration + 3 branch templates + BACKLOG entry** | Methodology, criteria, branches, harness scope all frozen before any data |
| (next) Phase 2 — reusable harness scaffold | Built after this commit lands; idle until Finding 8 verdict |
| (later) Collection daemon start | After 2026-05-09T16:45Z |
| (later) Branch verdict + writeup | After ~2026-05-11T18:00Z |

**Pre-registration committed:** 2026-05-08
**Data collection scheduled to start:** ~2026-05-09T17:00Z (after Finding 8 interim verdict)
**Branch verdict scheduled:** ~2026-05-11T18:00Z
**Public writeup scheduled:** ~2026-05-11T19:00Z

---

## Amendment 01 — Branch C addendum (publish-then-post, pre-verdict)

**Committed:** 2026-05-10 (paired with Finding 8 interim verdict commit; published before the original Branch C condition resolves at the 2026-05-12T17:45Z grace cutoff).

**Surfaces a design flaw in the original Branch C decision tree:** existing subconditions assume insufficient overlap with GMGN means the case-study scope was wrong-shape (low overlap) or the resolution window was wrong-shape (slow graduations). Both are *case-study-internal* causes. They do NOT cover the case where graduate-oracle's source pipe (HIGH+MED bucket emission) produces zero predictions in the collection window — that's *case-study-external*, an upstream-infrastructure block.

The Finding 8 interim verdict (Variant 5B fired at 2026-05-09T16:45Z) confirmed empirically that the bucket emission produces 0 MED+HIGH per 48h under current `bimodal_cliff` mode. The Case Study 01 trigger fired at the same timestamp and inherited this 0-emission state. After 8 hours of runtime, the case study harness has correctly collected 0 observations because there are 0 HIGH+MED predictions to collect. The daemon is healthy (verified via py-spy on PID 675 in `collection_loop` line 266); the upstream pipe is the limiter.

### What this amendment is

A Branch C SUB-condition added before the original Branch C condition resolves. The added subcondition is **strictly narrower** (more specific) than "n<30 in graduate-oracle MED+HIGH bucket on overlap" — it adds a precondition that distinguishes upstream-infrastructure-blocked from genuine low-overlap.

### What this amendment is NOT

- **NOT a relaxation.** The original Branch C "n<30 → extend by 48h" subcondition is preserved untouched for cases where the n<30 cause is genuine low overlap (n<30 of >0 emitted predictions overlap with GMGN's strict-preset). The amendment only narrows the action when the cause is upstream-infrastructure-blocked.
- **NOT a post-hoc rationalization.** Amendment commits at T+8h into the 48h collection window; the Branch C condition doesn't resolve until grace-cutoff at 2026-05-12T17:45Z (~64h later). Reader can verify amendment timestamp predates Branch C resolution.
- **NOT an exit clause to avoid an unwanted result.** The amendment does NOT skip the public writeup. It only routes the writeup to a different shape (upstream-cause writeup vs low-overlap writeup), both of which are publicly published.

### The added subcondition (frozen here, before Branch C resolves)

**Subcondition C-iv (upstream-infrastructure-blocked):**

If at the analysis cutoff (48h + 24h grace), n<30 in graduate-oracle MED+HIGH bucket on overlap AND the bucket-emission floor is below the case-study-supportable threshold:

```sql
-- Verification query (frozen, deterministic)
SELECT COUNT(*) FROM predictions
 WHERE predicted_at >= 1778342754   -- trigger_ts
   AND predicted_at <  1778515554   -- trigger_ts + 48h
   AND grad_prob_bucket IN ('HIGH','MED')
   AND age_bucket <= 75;
-- If this count < 30, the upstream pipe (not the overlap with GMGN) is the limiter.
```

then the case study cause is **upstream-infrastructure-blocked**, NOT insufficient overlap with GMGN.

### Action under Subcondition C-iv (frozen)

1. **STOP the daemon early** at the analysis cutoff (do not extend the 48h window — extension under the same upstream-blocked state would only collect more zero-rows, wasting harness time and the implementer's analysis attention).
2. **Public writeup ships** documenting the upstream-block as the case study finding. The writeup is `case_study_01_gmgn_results_branch_c_template.md` with an upstream-block addendum (deferred until verdict; pre-drafted shape: "Case Study 01 was upstream-blocked by Finding 8's bucket emission rate; the case study cannot answer the calibrated-vs-component question on this collection window because the calibrated bucket emitted 0 predictions; result is publicly published as inconclusive-due-to-upstream and the case study is **re-armed** with a new trigger contingent on Finding 8 follow-up resolution").
3. **Re-arm condition (frozen):** the next case study trigger fires at:
   - Finding 8 sub-branch (a) recalibration ships AND produces ≥10 MED predictions in the first 24h post-deploy, OR
   - Finding 8 sub-branch (b) Path E ships AND produces ≥10 MED predictions in the first 24h post-deploy.
   - Whichever fires first; new `start_at_ts` is computed at re-arm time as `path_resolution_ts + 24h`.
4. **The re-arm is ONE-SHOT.** If the re-armed case study collection window also produces n<30 due to upstream-infrastructure-block (a second time), the case study is **permanently published as inconclusive-due-to-upstream**, the experimental design itself becomes the finding (consistent with the original Branch C "both limiters fired" subcondition), and graduate-oracle's product positioning is reshaped without the case-study comparison data point. **Iteration-limit applies at the case-study level** — same shape as Path E's iteration-limit pre-registration on Finding 8 itself.

### What changes operationally as a result

- Daemon continues running its `collection_loop` unchanged (no daemon-state mutation; cleaner than reaching into the running process). At analysis cutoff, the operator manually stops it via `supervisorctl stop case_study_harness` rather than letting it idle through grace.
- The `case_study_01_observations` sqlite table remains queryable (it is empty; that *is* the data).
- Re-arm involves: edit `case_study_harness/configs/study_01_gmgn.toml` `start_at_ts` to the new computed timestamp, redeploy (supervisord restart of `case_study_harness`).

### Receipts trail (Case Study 01, with amendment)

| Commit | Action |
|---|---|
| `5bc8f33` Case Study 01 pre-registration + 3 branch templates + BACKLOG entry | Methodology, criteria, branches, harness scope all frozen before any data |
| (Phase 2 scaffold commit, post-pre-reg) Reusable harness scaffold | Built after pre-reg; idle until Finding 8 verdict |
| Trigger fired 2026-05-09T16:45:54Z | Daemon began `collection_loop`; 0 observations through T+8h due to upstream emission state |
| **(this commit) Case Study 01 — Branch C amended pre-verdict; Subcondition C-iv (upstream-infrastructure-blocked) added** | Amendment commits at T+8h into the 48h window; verdict not until T+72h grace cutoff; amendment is strictly narrower than original Branch C |

---

## Cross-references

- [`post_grad_metric_broken_since_launch.md`](post_grad_metric_broken_since_launch.md) — Finding 7 chain (sunset precedent for branch-execution discipline under unwanted outcomes)
- [`bucket_calibration_aliasing.md`](bucket_calibration_aliasing.md) — Finding 8 chain (interim criterion amendment precedent for pre-verdict refinement)
- BACKLOG.md "Case Study 01" entry (mirrors this pre-registration's frozen scope for backlog visibility)
- Memory: `feedback_pre_registration_branches.md` — discipline rules including iteration-limit, publish-then-post, pre-verdict amendment, recursion-applies-to-discipline-itself
- Future: `case_study_02_*.md` etc. as reusable harness extends to additional studies
