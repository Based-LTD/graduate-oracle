# Case Study 01 — Amendment 02: composite-vs-GMGN re-arm

**Pre-verdict amendment.** Committed publicly BEFORE the Case Study 01 verdict resolves (Tuesday 2026-05-12T17:45Z grace cutoff). Same publish-then-post discipline as Case Study 01 Amendment 01 (commit `4d56f53` — Subcondition C-iv) and the Finding 8 amendment (`f3f1f3e`).

**Parent pre-reg:** [`case_study_01_gmgn_comparison_prereg.md`](case_study_01_gmgn_comparison_prereg.md)
**Sister amendment:** Amendment 01 (Subcondition C-iv — upstream-infrastructure-blocked re-arm trigger)
**Sister audits informing this amendment:**
- [`audit_09_smart_money_lift_results.md`](audit_09_smart_money_lift_results.md) — wallet index empirically validated as carrying signal (7.37× grad lift)
- [`audit_12a_composite_retroactive_validation_results.md`](audit_12a_composite_retroactive_validation_results.md) — retroactive composite test couldn't resolve forward-prediction question due to leakage; forward audit recommended

**Amendment commits at:** [TIMESTAMP at commit time, 2026-05-11]
**Re-armed collection scheduled to start:** when Subcondition C-iv re-arm fires (Path E sub-branch produces ≥10 MED in first 24h post-deploy, currently expected to satisfy at 2026-05-11T06:55Z) — or immediately at re-arm event, whichever lands first.

---

## What this amendment changes

The original Case Study 01 comparison axis (per the parent pre-reg) was:

> **`grad_prob ≥ 0.5` filter (graduate-oracle HIGH+MED bucket) vs GMGN `--filter-preset strict --type new_creation`**

When the C-iv subcondition re-arms the case study (per Amendment 01), this amendment **pivots the comparison axis** to test the composite signal instead of the calibrated grad_prob bucket:

> **NEW: `composite_score ≥ P90 + MC ≥ $5k` filter vs GMGN `--filter-preset strict --type new_creation`**

Everything else from the parent pre-reg is preserved unchanged: forward 14d collection, 2×2 overlap matrix (graduate-oracle arm × GMGN arm), 24h grace outcome resolution, branch acceptance criteria adapted from the parent.

---

## Why this amendment

Three independent findings since the parent pre-reg combined to make the composite-vs-GMGN comparison more strategically valuable than the original grad_prob-vs-GMGN comparison:

### 1. Composite is empirically the user-facing product signal

User direction (2026-05-10): the hot-launch composite + MC floor filter is "actually incredible" — possibly the most important product feature. Memory `project_dual_track_signal_strategy.md` formalized this: `grad_prob` = moat infrastructure; composite = product surface. A B2B integration comparison that tests the PRODUCT signal against GMGN's product signal is more meaningful than testing the moat-infrastructure signal.

### 2. Audit 09 validated the wallet-index input to the composite

Audit 09 results (commit `34ce847`) showed `smart_money_in ≥ 7` produces 7.37× graduation lift over `smart_money_in = 0` with strongly non-overlapping CIs. The composite's `smart_money_in` term is empirically the load-bearing piece. Testing the composite vs GMGN is testing whether the composite formula (with its empirically-validated input) outperforms GMGN's component-composition.

### 3. Audit 12-A surfaced retroactive limits; forward collection is the appropriate test

Audit 12-A results (commit `66e1138`) showed retroactive analysis cannot cleanly resolve the composite-as-forward-predictor question because `actual_max_mult` leakage dominates. Forward collection is the right test. The re-armed Case Study 01 is the natural place to run that forward test, paired with the GMGN benchmark.

**This amendment is strictly more aligned with the strategic context than the original comparison.** The original `grad_prob ≥ 0.5` filter remains a valid baseline; if needed, the re-armed harness can capture both filters per mint and report dual results. But the headline comparison shifts to composite-vs-GMGN.

---

## What this amendment is

A **comparison-axis pivot** of the case study's re-arm execution, NOT a relaxation of any acceptance criterion. The strict thresholds (≥10pp precision lift for Branch A, ≥30 sample size, ≥70% resolution rate) are preserved with identical numerical bars on the new comparison axis.

---

## What this amendment is NOT

**NOT a relaxation of acceptance criteria.** All numerical thresholds preserved.

**NOT a post-hoc rationalization.** Amendment commits 2026-05-11, before the re-armed collection starts. Re-arm doesn't fire until the C-iv subcondition is satisfied (Path E sub-branch + ≥10 MED in 24h — expected ~2026-05-11T06:55Z). Amendment predates re-armed data by ≥1 hour at minimum and likely days.

**NOT breaking the iteration-limit.** The original parent pre-reg's iteration-limit (one extension only, then methodology-as-finding) applies to the re-armed run as well. This amendment does not authorize multiple re-arms; it only changes WHAT the re-armed run tests.

---

## The new comparison filter (frozen)

### Graduate-oracle arm — composite signal

A mint enters the graduate-oracle arm when:

```sql
composite_score >= P90_composite   -- 90th percentile of composite
                                   -- across the rolling 24h sample
                                   -- (computed at observation time;
                                   -- not retroactive)
AND mc_usd >= 5000                 -- minimum $5,000 market cap
```

`composite_score = smart_money_in × max_mult × (1 / (1 + age_s / 600))` per the live dashboard formula (verified `web/static/app.js:683`, 2026-05-11).

**`max_mult` is the snapshot value at observation time**, NOT the lifecycle peak. This is the forward-safe computation the retroactive Audit 12-A could not perform.

**P90 threshold rolling cadence:** P90 is recomputed every 60s from the trailing 24h sample of composite scores observed by the harness. Cold-start (first 24h of re-armed collection): use P95 of available samples until 24h elapses. After 24h: P90 of trailing 24h.

### GMGN arm — strict-preset

Unchanged from the parent pre-reg:

```
gmgn-cli market trenches --chain sol --type new_creation --filter-preset strict --raw
```

Mints in this CLI response at observation time are in the GMGN arm.

### Both arms — 2×2 overlap matrix

Identical structure to the original parent pre-reg + Amendment 01's coverage-gap measurement. For each mint that enters EITHER arm during the 14d window:

| | grad_oracle arm only | GMGN arm only | Both arms | Neither |
|---|---|---|---|---|
| Outcome: graduated | A_graduated | C_graduated | Both_graduated | Neither_graduated |
| Outcome: not graduated | A_not | C_not | Both_not | Neither_not |

Compute per arm: precision (graduations / total in arm), coverage (per-arm graduations / all-window graduations).

---

## Acceptance criteria (adapted from parent + Amendment 01)

### Branch A — Composite-on-grad-oracle-arm wins

**Conditions ALL of:**
1. Sample size: n ≥ 30 in graduate-oracle arm
2. Composite-arm precision ≥ GMGN-arm precision + 10pp
3. Precision lift holds when restricted to mints in BOTH arms (composite + GMGN both positive)

**Action:** Public writeup ships. Composite-as-product is empirically validated as outperforming GMGN's component-composition.

### Branch A' — Composite as complementary coverage layer

**Conditions ALL of:**
1. Sample size sufficient (n ≥ 30 in each arm)
2. Composite-arm precision within ±10pp of GMGN-arm precision
3. **Composite-only coverage ≥ 30%** of all window graduators (composite catches mints GMGN misses entirely)

**Action:** Public writeup ships with dual-product framing. Both signals add value through complementary coverage. Same shape as Audit 12 Amendment 01's Branch A' framing.

### Branch B — GMGN wins or no incremental composite signal

**Conditions ANY of:**
1. Composite-arm precision < GMGN-arm precision − 10pp AND composite-only coverage < 20%
2. Both arms have ≥30% precision AND coverage difference within ±10pp (composite collapses onto GMGN's mint set)

**Action:** Public writeup ships with negative finding. Product reshapes around what the data shows IS adding value relative to GMGN.

### Branch C — Inconclusive

**Conditions:**
- n < 30 in composite arm at the 24h grace cutoff
- OR resolution rate < 70%

**Action:** Per parent pre-reg's Branch C decision tree, AS FURTHER NARROWED BY AMENDMENT 01's Subcondition C-iv. If upstream-infrastructure-blocked AGAIN (the case study's second iteration also produces 0-emit), case study publishes permanently inconclusive and the methodology design itself becomes the finding. Iteration-limit applies at the case-study level.

---

## Compatibility with wallet redaction (commit `06480be`)

The composite computation depends on:
- `smart_money_in` (count, aggregate — preserved post-redaction ✓)
- `max_mult` (per-mint observation — not a wallet identifier ✓)
- `age_s` (per-mint observation — not a wallet identifier ✓)

**The composite filter operates on aggregate counts and per-mint observations, NOT on wallet addresses.** The case study harness reading composite data does not require the redacted `smart_money_examples[]` or `clustered_wallets[]` fields. Fully compatible with the wallet redaction deploy.

---

## What changes operationally

- Re-armed case study harness uses a new composite filter adapter (not yet built; mechanical addition to `case_study_harness/sources/` per parent pre-reg's reusable-harness design)
- New filter computes composite_score per snapshot tick, applies P90 + MC floor, captures eligible mints
- Resolver remains unchanged (24h graduation + peak_mult outcome)
- 14d window unchanged
- 60s tick cadence unchanged

The case_study_harness's existing `joiner.py` (per the post-mortem at `case_study_01_harness_bug_postmortem.md`) handles the 2×2 matrix and the "absence is data" contract identically — no joiner changes needed for the new comparison axis.

---

## What stays the same (parent pre-reg invariants)

- 14d forward collection window (per parent)
- 60s poll cadence (per parent)
- 2×2 overlap matrix capture (per Amendment 01)
- Outcome resolution at +24h grace (per parent)
- Iteration-limit at the case-study level (one extension/re-arm only)
- `grad_prob` track unchanged — Path E live; T+7d acceptance window continues
- Receipts moat unchanged — tamper-evident merkle ledger, public commits, frozen acceptance criteria

---

## Receipts trail (Case Study 01 chain, with Amendments 01 + 02)

| Commit | Action |
|---|---|
| `5bc8f33` Case Study 01 pre-registration | Methodology, criteria, branches frozen pre-data |
| `08fb96c` Phase 2 — reusable harness scaffold | Harness shipped (silent enrichment bug introduced — see `51a409f` postmortem) |
| `87edcb7` Finding 8 interim verdict resolved | Variant 5B fired; upstream bucket emission blocked → C-iv pathway opens |
| `4d56f53` Case Study 01 — Amendment 01: Subcondition C-iv | Upstream-infrastructure-blocked path added pre-verdict |
| `147777d` Path E deploy receipt | Bucket emission restored via fixed-percentile cutoffs on raw GBM |
| `51a409f` Case Study 01 harness bug postmortem + fix + Amendment 02 | Enrichment query schema fix; backfill cursor |
| `81a834f` Case Study 01 harness fix deploy receipt | Observations populated post-fix |
| `c2b3a8a` Audit 12 pre-reg + Amendment 01 (4-branch criterion) | Coverage-gap dual measurement framework |
| `34ce847` Audit 09 results — wallet index lift validated | 7.37× grad lift, monotonic 2x trend; empirical foundation for redaction |
| `06480be` Wallet redaction Option A deploy receipt | Field-level redaction; compatible with this amendment |
| `66e1138` Audit 12-A results — METHODOLOGY-EXPOSES-LEAKAGE | Retroactive composite test surfaces leakage; forward audit recommended |
| **(this commit) Case Study 01 — Amendment 02: composite-vs-GMGN re-arm** | Comparison-axis pivot for re-armed collection; preserves all acceptance thresholds |
| (next, after C-iv fires) Re-armed harness adapter + start | Composite filter adapter built; collection begins |
| (later) Re-armed verdict at +14d + 24h grace | Branch A / A' / B / C outcome published |

---

## Cross-references

- [`case_study_01_gmgn_comparison_prereg.md`](case_study_01_gmgn_comparison_prereg.md) — parent pre-reg
- [`case_study_01_amendment_02_composite_vs_gmgn_re_arm.md`](case_study_01_amendment_02_composite_vs_gmgn_re_arm.md) — this amendment (Amendment 02)
- [`case_study_01_harness_bug_postmortem.md`](case_study_01_harness_bug_postmortem.md) — Amendment 02 = current file? wait, the file is named...

Note: this is the SECOND amendment to Case Study 01's parent pre-reg. Numbering scheme reuses across the chain:
- Amendment 01 (in `case_study_01_gmgn_comparison_prereg.md` § Amendment 01): Subcondition C-iv added pre-verdict
- "Amendment 02" inside the harness-bug postmortem doc: re-flagged pre-verdict — that one was a DATA-SIDE pre-reg amendment narrowing the eligible-window
- **This file (Amendment 02 — composite-vs-GMGN re-arm):** METHODOLOGY-side pre-reg amendment that pivots the comparison axis

The numbering is therefore informal. The receipts-trail discipline cares about commit timestamps, not amendment serial numbers. This commit's timestamp predates the re-armed data by design; that's the verifiable receipt.

---

## Cross-references (final)

- Memory: `project_dual_track_signal_strategy.md` — grad_prob = moat; composite = product
- Memory: `project_wallet_index_is_the_moat.md` — wallet index = load-bearing asset
- Memory: `feedback_pre_registration_branches.md` — pre-verdict amendment discipline
- Memory: `feedback_methodology_calls_user_owned.md` — comparison-axis decision is user-owned (greenlit by user for this amendment)
- [`audit_12_hot_launch_composite_validation_prereg.md`](audit_12_hot_launch_composite_validation_prereg.md) — sister forward audit on the same composite signal
- [`audit_12_amendment_01_coverage_gap_dual_measurement.md`](audit_12_amendment_01_coverage_gap_dual_measurement.md) — coverage-gap framework applied here
