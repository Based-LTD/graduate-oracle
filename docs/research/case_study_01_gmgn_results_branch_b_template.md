# Case Study 01 — Results (Branch B: thesis undermined)

**TEMPLATE — committed pre-data-collection alongside `case_study_01_gmgn_comparison_prereg.md` per the publish-then-post discipline.** This file's terminal numbers fill in at outcome time; the structure, framing, and acceptance-criterion verdict are frozen pre-collection.

**Branch B fires when ANY of:**
1. Sample size sufficient (n≥30) AND graduate-oracle precision ≤ GMGN strict-preset precision
2. Sample size sufficient AND difference within ±5pp (calibrated bucket is not adding meaningful value over composition)

**Action on Branch B fire:** rename this file to `case_study_01_gmgn_results.md`, fill the bracketed `[NUMBER]` fields with terminal data, commit + push, then ship X thread + TG message — and **open the product-reshape discussion** with concrete data anchoring it.

---

## Headline

**Graduate-oracle's calibrated lane-60s bucket does not outperform GMGN's `--filter-preset strict` on the same mint set, n=`[N]`. The thesis that calibrated-bucket-alone is the differentiator is not supported by this data.**

The product-spec reopens with concrete data: what does the data say IS adding value? The receipts trail captures this honestly.

Methodology pre-registered at [`case_study_01_gmgn_comparison_prereg.md`](case_study_01_gmgn_comparison_prereg.md), commit `[PREREG_COMMIT_HASH]`. Data collection ran `2026-05-09T[HH]:00Z` to `2026-05-11T[HH]:00Z` (48h window) plus 24h outcome-resolution grace. This writeup commits at `[RESULTS_COMMIT_HASH]`, `[RESULTS_COMMIT_TIMESTAMP]`.

**Same shape as Finding 7i (sustain permanently sunset, 2026-05-08, commit `7658639`):** the discipline holds when the verdict contradicts the thesis.

---

## The numbers

### Sample state at analysis

```
Total mints captured during 48h window:   [TOTAL_CAPTURED]
Mints in graduate-oracle's lane-60s scope: [GO_LANE]
Mints in GMGN strict-preset:               [GMGN_STRICT]
Mints in BOTH (overlap):                   [OVERLAP_N]
Mints excluded per pre-reg rules:          [N_EXCLUDED]
  - unresolved at 24h grace:               [N_UNRESOLVED]
  - outside lane-60s (age >75s):           [N_OUT_OF_LANE]
  - GMGN snapshot >120s offset:            [N_TIMING_FAIL]
  - already graduated at first observation: [N_ALREADY_GRAD]
```

### Precision comparison

```
                         | precision (graduated/total) | n
─────────────────────────┼─────────────────────────────┼──────
graduate-oracle MED+HIGH | [GO_PRECISION]%             | [N_GO]
GMGN strict-preset       | [GMGN_PRECISION]%           | [N_GMGN]
both products positive   | [BOTH_PRECISION]%           | [N_BOTH]
```

**Precision difference on overlap:** graduate-oracle vs GMGN = **`[DIFF_PP]`pp** (`[GO_PRECISION]`% − `[GMGN_PRECISION]`% = `[DIFF_PP]`pp). Threshold for Branch A was ≥+10pp; observed difference is `[DIFF_PP]`pp.

**Branch B subcondition fired:** [pick one based on actual data]
- **(B1)** graduate-oracle precision ≤ GMGN: graduate-oracle is `[ABS_DIFF_PP]`pp BEHIND GMGN.
- **(B2)** Difference within ±5pp: graduate-oracle and GMGN are within `[ABS_DIFF_PP]`pp of each other; no meaningful precision lift from the calibrated bucket.

### Resolution rate

```
Total mints in overlap:       [OVERLAP_N]
Resolved within 24h grace:    [N_RESOLVED]  ([RESOLUTION_RATE]%)
```

Above the 70% resolution-rate floor; the comparison is statistically interpretable. The result is real, not a sampling artifact.

---

## What the result means

The calibrated lane-60s bucket alone is not adding measurable precision lift over GMGN's component-composition strict-preset on this 48h window. Two interpretations are possible:

**Interpretation 1 — calibration value is in the meta-data, not the bucket.**
The bucket may be no better at first-pass precision than GMGN's strict-preset, but graduate-oracle's per-prediction *receipts* (calibration receipt, public discipline trail, frozen acceptance criteria, tamper-evident merkle ledger) may still be the differentiated value for B2B integrators. A bucket that's equivalent on precision but auditable via public receipts is a different product than a bucket that's equivalent on precision and opaque. **The receipts moat is independent of bucket-precision lift.**

**Interpretation 2 — the product spec needs reshaping.**
If the calibrated bucket isn't adding precision over component composition, the product needs to be reshaped around what the data says IS adding value. Concrete possibilities (none committed yet; this is the spec-reopen scope):

- **Hybrid bucket-plus-component output:** ship the calibrated bucket alongside specific component fields that GMGN-class APIs don't expose well (e.g., the calibration receipt itself, the inline accuracy-at-this-confidence-cell, the rug-heuristic flag list). The product is then "calibrated bucket + receipts" rather than "calibrated bucket alone."
- **Different lane window:** test whether moving the prediction lane (e.g., to age 30s instead of 60s, or to a multi-checkpoint shape) restores precision lift.
- **Different prediction shape:** test whether a calibrated *probability* output (not bucketized) outperforms GMGN's strict-preset on a different decision threshold or scoring metric (Brier, log-loss).
- **Different competitive surface:** GMGN's strict-preset is one composition; Phantom, Birdeye, Pump.fun analytics may be more or less competitive surfaces. Study 02-04 explore those.

**The user committed to product-reshape if the data demanded it.** This commit is that reshape moment, with concrete data anchoring the discussion. No softening of the verdict; no "let's run another comparison until we win" iteration. Per pre-registered iteration-limit, this study triggers a product-spec reopen, not a re-comparison loop.

---

## What this DOES NOT claim

- The lane-60s graduation prediction is **not bad** — it has `[GO_PRECISION]`% precision, which compares to a base rate of `[OVERALL_BASE_RATE]`% across all in-lane mints. The model produces calibrated outputs; the issue is that GMGN's component-composition is comparably good.
- This study tests **one specific competitive surface** (GMGN strict-preset). Other competitors may have different precision profiles. Study 02-04 explore those.
- This study tests **precision**, not recall. graduate-oracle's recall (how many of actual graduates were caught) is not measured here and may differ.
- This study does NOT invalidate the broader prediction layer. It empirically establishes that on this specific competitive surface, calibrated-bucket-alone is not the differentiator. The receipts moat (publish-then-post discipline, public timestamps, frozen criteria) remains independent of this verdict.

---

## Product-reshape discussion (frozen scope)

This branch executing opens a product-spec reopen, not a "let's tweak the bucket" iteration. The reshape discussion is scoped to address: **"if the calibrated bucket alone isn't the empirical differentiator, what is the product?"**

Three concrete spec-reopen questions to answer:

**Q1: What's the actual differentiator we can demonstrate?** Is it the calibration-receipts trail (auditable predictions vs opaque ones)? The lane-60s commitment specificity (early-window prediction vs broad-window scanning)? The pre-fix-then-fix discipline producing trustworthy outputs over time? Each of these is testable with a different study shape.

**Q2: Does a different prediction shape produce a different result?** Bucketization may be losing information; calibrated probability output evaluated on continuous metrics (Brier, log-loss) may show different competitive standing. Worth a follow-up study with the same harness, different output shape.

**Q3: Is the right product B2B-licensed receipts trail rather than B2B-licensed bucket?** Sell the auditable-prediction infrastructure (publish-then-post receipts, pre-registered acceptance criteria, public commit trail) as the differentiator, with the calibrated bucket as one feature on top of that. Different commercial framing; different pricing; different prospect profile.

**These questions get scoped into Studies 02-04 + a separate Product Spec Review session.** No iteration in this commit — just naming the questions whose data this study generates.

---

## What gets reshaped, what stays

**Reshape:**
- Marketing framing: "calibrated bucket as the differentiator" → "auditable receipts trail as the differentiator, with calibrated bucket as one component"
- Sales pitch: "we have higher precision than [competitor]" → "we have publicly auditable receipts that anyone can verify, and the precision is competitive with the best component-composition alternatives"
- Pricing tiers (potentially): if the value is the receipts trail, Enterprise tier may be priced differently than if the value is the bucket precision
- Future studies (02+): reframe to test the receipts-trail value, not just bucket precision

**Stays:**
- The discipline pattern itself (pre-registration, publish-then-post, iteration-limit) — this verdict is the discipline pattern working as designed
- The sustain-permanent-sunset verdict from Finding 7i — independent of this study
- The Finding 8 EMA-fix and bucket-distribution gates — independent of this study
- The /api/scope honest-disclosure framing — independent of this study
- The aggregate `post_graduation.sustain_rate_30m` — independent (Jupiter measurement)

---

## Reusable harness performance

The instrumentation built for this study (case_study_harness/) ran cleanly over the 48h window. Same harness can be reconfigured for Study 02 (Pump.fun analytics), Study 03 (Phantom intelligence), Study 04 (longitudinal), with one TOML config file and one pre-reg writeup per study. **Study 02 should be re-scoped per the product-reshape discussion above** — likely shifts from "outperformance comparison" to "receipts-trail-value comparison" or similar.

---

## Receipts trail

| Commit | Action |
|---|---|
| `[PREREG_COMMIT_HASH]` Case Study 01 pre-registration + 3 branch templates | Frozen methodology, criteria, branches, harness scope |
| `[HARNESS_COMMIT_HASH]` Phase 2 — reusable harness scaffold | Built after pre-reg landed |
| `[COLLECTION_START_HASH]` Collection daemon start | After 2026-05-09T16:45Z |
| **(this commit) Case Study 01 results — Branch B: thesis undermined; product-reshape opens** | Frozen acceptance criteria not met; receipts moat strengthens via the discipline holding under unwanted outcome |

---

## Cross-channel artifacts

**X thread variant** (pre-drafted; ships after this commit):

```
case study 01 — calibrated bucket vs component composition.

graduate-oracle's lane-60s MED+HIGH bucket DOES NOT outperform GMGN's
--filter-preset strict on the same mint set ([DIFF_PP]pp difference,
n=[OVERLAP_N], 48h window).

this empirically demonstrates the calibrated-bucket-alone product spec
isn't the differentiator. the product reopens with concrete data
anchoring the discussion: receipts-trail value as the actual moat,
calibrated bucket as one component on top.

discipline pattern holds. methodology pre-registered, frozen criteria
applied fresh, branch B executed without softening.

receipts:
  github.com/Dspro-fart/graduate-oracle/blob/main/docs/research/case_study_01_gmgn_results.md
prereg: [PREREG_COMMIT_HASH]
results: [RESULTS_COMMIT_HASH]
```

**TG channel pinned variant:**

```
📊 Case Study 01 — empirical finding: graduate-oracle's calibrated bucket alone is NOT outperforming GMGN's strict-preset on lane-60s graduation prediction ([DIFF_PP]pp difference, n=[OVERLAP_N]).

Per pre-registered Branch B, this opens a product-reshape discussion. The receipts-trail discipline (publish-then-post, pre-registered criteria, public commit timeline) is the actual moat — bucket precision alone isn't.

Full writeup: github.com/Dspro-fart/graduate-oracle/blob/main/docs/research/case_study_01_gmgn_results.md
```

---

## Discipline note (frozen template language)

The receipts moat is *strengthened*, not weakened, by this verdict landing publicly. **Same shape as Finding 7i — three model-class attempts at sustain prediction all failed pre-registered acceptance and the feature was permanently retired.** A team that publishes negative findings with the same discipline as positive findings demonstrates the discipline is real, not theater.

This template was committed pre-data-collection (commit `[PREREG_COMMIT_HASH]`). The fact that the Branch B writeup existed in skeleton form before any data was observed is what makes the eventual publication credible — the framing wasn't crafted post-hoc to fit a result, it was committed to in advance with both directions covered.

**Sustain was upside, not required (Finding 7i framing). Calibrated-bucket-alone is also upside, not required — if the data shows the receipts trail is the actual value, that's the empirical foundation we build on.**
