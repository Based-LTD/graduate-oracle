# Case Study 01 — Results (Branch C: insufficient sample / ambiguous)

**TEMPLATE — committed pre-data-collection alongside `case_study_01_gmgn_comparison_prereg.md` per the publish-then-post discipline.** This file's terminal numbers fill in at outcome time; the structure, framing, decision tree, and acceptance-criterion verdict are frozen pre-collection.

**Branch C fires when:**
- n<30 in graduate-oracle MED+HIGH bucket on overlap, OR
- Resolution rate <70% at the 24h grace cutoff

**Action on Branch C fire:** rename this file to `case_study_01_gmgn_results.md`, fill the bracketed `[NUMBER]` fields with terminal data, **execute the pre-registered decision tree subcondition that fired**, commit + push.

---

## Headline

**Case Study 01 inconclusive: `[REASON]`. Per pre-registered Branch C decision tree, `[ACTION_TAKEN]`.**

Methodology pre-registered at [`case_study_01_gmgn_comparison_prereg.md`](case_study_01_gmgn_comparison_prereg.md), commit `[PREREG_COMMIT_HASH]`. Data collection ran `2026-05-09T[HH]:00Z` to `2026-05-11T[HH]:00Z` (48h window) plus 24h outcome-resolution grace.

The inconclusive verdict is itself the receipts: a frozen acceptance-criteria-driven study that produces an "I don't know" answer with explicit data anchoring the not-knowing is more credible than a study that reaches a verdict by post-hoc relaxation.

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

### Resolution rate

```
Total mints in overlap:       [OVERLAP_N]
Resolved within 24h grace:    [N_RESOLVED]  ([RESOLUTION_RATE]%)
```

---

## Branch C subcondition fired

**Pick exactly ONE of the following, based on actual data:**

### Subcondition C1 — Overlap mint count was <30 in 48h

graduate-oracle's lane-60s MED+HIGH overlap with GMGN strict-preset reached only `[OVERLAP_N]` mints (threshold: ≥30). The 48h window did not produce sufficient overlap to make a precision comparison statistically interpretable.

**Pre-registered action: extend collection by another 48h** (one extension only — iteration-limit applies). The harness is reconfigured to continue running; new total window becomes 96h. Branch verdict re-evaluates after the extended window plus grace.

If the extended window also produces <30 overlap, this becomes a finding about the addressable market shape (subcondition C3 below), not a methodology issue to fix.

**Status at this commit:** extension running. Updated verdict scheduled for `[EXTENDED_VERDICT_DATE]`.

### Subcondition C2 — Resolution rate was the limiter

`[RESOLUTION_RATE]`% of overlap mints resolved within the 24h grace window (threshold: ≥70%). The remaining `[N_UNRESOLVED]` mints were neither confirmed graduated nor confirmed failed at analysis time, leaving the precision comparison statistically incomplete.

**Pre-registered action: redefine outcome resolution to a longer grace window** (capped at 72h) and re-run analysis on existing data. The 48h collection window stays; only the resolution window extends.

If the 72h-grace re-analysis still produces <70% resolution, this becomes a finding about the post-prediction outcome timeline rather than a precision question (the prediction may be valid but the outcome shape doesn't fit a 24-72h window).

**Status at this commit:** 72h-grace re-analysis running. Updated verdict scheduled for `[EXTENDED_VERDICT_DATE]`.

### Subcondition C3 — Both limiters fired (the experimental design itself is the finding)

`[OVERLAP_N]` overlap mints AND `[RESOLUTION_RATE]`% resolution rate; both below threshold. This is not a methodology issue — it's a real finding about graduate-oracle's lane-60s prediction shape relative to GMGN's strict-preset.

**The finding:** graduate-oracle's lane-60s prediction rate doesn't generate enough overlap with GMGN's strict-preset to make this comparison feasible at 48h windows, AND the post-prediction outcome resolution doesn't reach the 70% bar in 24h grace. Together, these limit the addressable market for "graduate-oracle vs GMGN as direct competitors" — they may not be operating on overlapping enough mint sets to compete on the same precision metric.

This is a structurally different finding than "graduate-oracle outperforms" or "graduate-oracle underperforms" — it's "the comparison itself is harder to construct than expected." That's still a real receipts result.

**Pre-registered action: case study is published as inconclusive; spec-reopen scoped to address the addressable market shape, not the bucket precision.**

Specifically: Studies 02-04 (Pump.fun analytics, Phantom intelligence, longitudinal HIGH/MED) need their own pre-registered overlap-feasibility checks BEFORE precision comparisons. The harness should produce an early-feasibility checkpoint before continuing to a full precision comparison.

---

## What this DOES NOT claim

- This study does NOT support OR undermine the calibrated-bucket-alone thesis. Insufficient data, by frozen acceptance criteria.
- This study does NOT relax the Branch A/B thresholds to "force a verdict." Threshold relaxation here would be exactly the post-hoc rationalization the discipline pattern forbids.
- This study does NOT mean "the methodology is bad." The methodology produced a clean inconclusive — it correctly identified that the data couldn't resolve the question at the chosen window/grace shape. Inconclusive is a valid outcome of frozen acceptance criteria.

---

## What gets done

**If Subcondition C1 fired (extend by 48h):**
- Harness continues running per existing config; window extends to 96h
- Re-analysis runs at extended-window + 24h grace
- Updated branch verdict (A, B, or C) ships after extended analysis
- This commit stays as the "interim Branch C / extension underway" record

**If Subcondition C2 fired (extend grace to 72h):**
- Collection daemon stops at original 48h end; data stays
- Resolver re-runs against existing observations with 72h grace
- Updated branch verdict (A, B, or C) ships after re-analysis
- This commit stays as the "interim Branch C / grace-extension underway" record

**If Subcondition C3 fired (both limiters; case closed inconclusive):**
- This commit is the terminal state
- Studies 02-04 add early-feasibility checkpoints to their pre-registrations
- Reusable harness adds an "overlap-density check" as a pre-collection step
- The product-reshape question (from Branch B) does NOT auto-fire; Branch C3 is its own scope ("addressable market shape" not "bucket precision")

---

## Reusable harness performance

The instrumentation worked correctly — it captured both feeds, joined per pre-reg specs, and produced the data that drove the Branch C fire. The harness performed as designed; the inconclusive verdict is data-driven, not infrastructure-driven.

**For Study 02:** add an **overlap-density pre-check** at the start of any comparison study. Before committing to a 48h precision comparison, verify that the two sources produce ≥X overlap-per-hour during the first ~6h of collection. If overlap density is too low, surface the feasibility finding immediately rather than running a full study to discover it.

This is a harness-level lesson from Case Study 01 that applies to all subsequent studies. It's also a refinement of the pre-registration discipline at the experimental-design level: **pre-register feasibility checks, not just acceptance criteria.** Same recursive shape as Finding 8's interim-criterion amendment (frozen criteria can themselves be audited and refined pre-verdict).

---

## Receipts trail

| Commit | Action |
|---|---|
| `[PREREG_COMMIT_HASH]` Case Study 01 pre-registration + 3 branch templates | Frozen methodology, criteria, branches, harness scope |
| `[HARNESS_COMMIT_HASH]` Phase 2 — reusable harness scaffold | Built after pre-reg landed |
| `[COLLECTION_START_HASH]` Collection daemon start | After 2026-05-09T16:45Z |
| **(this commit) Case Study 01 results — Branch C: `[SUBCONDITION_LABEL]`** | `[SUBCONDITION_OUTCOME_SUMMARY]` |
| `[EXTENDED_VERDICT_HASH]` (if C1/C2) Extended analysis updated verdict | Final A/B/C call after extension |

---

## Cross-channel artifacts

**X thread variant** (pre-drafted; ships after this commit):

```
case study 01 update — inconclusive at the pre-registered acceptance criteria.

[ONE-LINE WHY: e.g., "overlap mint count <30 in 48h window" / "resolution
rate <70% at 24h grace" / "both limiters fired"]

per pre-registered Branch C decision tree: [ACTION_TAKEN].

inconclusive is a valid outcome of frozen criteria. relaxing the
thresholds to force a verdict would be exactly the post-hoc
rationalization the discipline forbids. publishing the inconclusive
finding with the data anchoring it is what the receipts moat is.

receipts:
  github.com/Based-LTD/graduate-oracle/blob/main/docs/research/case_study_01_gmgn_results.md
prereg: [PREREG_COMMIT_HASH]
results: [RESULTS_COMMIT_HASH]
```

**TG channel pinned variant:**

```
📊 Case Study 01 update — inconclusive at the pre-registered acceptance criteria.

[ONE-LINE WHY]

Per pre-registered Branch C: [ACTION_TAKEN]. The discipline holds — frozen criteria, no post-hoc relaxation, the inconclusive verdict is the publication.

Full writeup: github.com/Based-LTD/graduate-oracle/blob/main/docs/research/case_study_01_gmgn_results.md
```

---

## Discipline note (frozen template language)

Inconclusive verdicts are a third valid branch in any pre-registration that includes them. Studies that pre-register only PASS/FAIL implicitly assume they'll always have enough data; studies that pre-register PASS/FAIL/INCONCLUSIVE acknowledge that data quality is itself a binding constraint.

This template's existence — pre-drafted with three subconditions and pre-registered actions for each — is what makes the eventual inconclusive publication credible. The framing wasn't crafted post-hoc to explain why the data didn't resolve; it was committed in advance with the inconclusive scenario fully scoped, including the decision-tree branches.

**Same shape as the iteration-limit pre-registration rule (memory file): pre-register the stop-iterating point AND the inconclusive-result handling BEFORE the data lands.**
