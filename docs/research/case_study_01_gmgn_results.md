# Case Study 01 — Results (Branch C terminal: structural non-comparability)

**This is the terminal state of the pre-registered Branch C decision tree.** Methodology, branches, and acceptance criteria were frozen pre-collection at `01d083d` (public mirror; source-repo prereg_commit `5bc8f33`). This file is the frozen `case_study_01_gmgn_results_branch_c_template.md` with terminal numbers filled and the fired subcondition executed. No acceptance criterion was relaxed. No new methodology decision was made — **publishing this is implementer execution of the branch the pre-registration already committed to** (per `feedback_methodology_calls_user_owned.md`: executing a pre-registered branch is implementer work, not a fresh call).

---

## Headline

**Case Study 01 is inconclusive on the head-to-head precision axis — by structural design, not by data shortfall. The graduate-oracle arm and the GMGN strict-preset arm evaluate the same mints at non-overlapping lifecycle stages, so a head-to-head precision comparison is structurally impossible to construct. Per the pre-registered Branch C decision tree (Subcondition C-iv re-arm → second-iteration 0-emit on the comparison arm → Amendment 02 iteration-limit), the case study publishes permanently inconclusive on this axis, with the methodology design itself as the finding.**

The inconclusive verdict is the receipts. A frozen-criteria study that returns "this comparison cannot be constructed, here is exactly why" is more credible than one that manufactures a verdict by relaxing its join window or its acceptance bar.

---

## The numbers (terminal)

Collection ran **2026-05-10T19:26Z → 2026-05-16T03:15Z** (original 48h window + Amendment 01 Subcondition C-iv re-arm extension), plus rolling 24h outcome-resolution grace. Harness process confirmed running throughout; GMGN CLI confirmed functional (manual invocation 2026-05-16 returned a valid populated `new_creation` payload).

```
Total graduate-oracle observations captured:     53
Mints in graduate-oracle lane-60s scope:         53   (age_bucket 30/60; config age_max=75)
GMGN snapshots successfully joined in ±120s:     47/53  (89% — join timing works)
Mints in GMGN strict-preset at observation time:  0/53
Mints in BOTH arms (overlap):                     0
Resolved within 24h grace:                       52/53  (98%)
Graduations:                                      4     (HIGH bucket 1/12, MED bucket 3/41)
Peak-mult dist (resolved, n=46):  median 3.3×, 13 ≥5×, 1 ≥10×
```

### Resolution rate is NOT the limiter

98% resolution rules out the templated Subcondition C2 (resolution-rate floor). The graduate-oracle arm collected and resolved cleanly. The limiter is **zero overlap**, and that zero is structural.

---

## Branch C subcondition fired

### Pre-registered path: Amendment 01 Subcondition C-iv → iteration-limit → C3-class terminal

The fired path, traced against the frozen documents:

1. **Original 48h window** (`5465b1f` scaffold; trigger 2026-05-09T16:45Z) produced 0-emit on the comparison arm (no graduate-oracle mint in GMGN strict-preset).
2. **Amendment 01 (`772e5a6`, Subcondition C-iv)** — pre-registered the upstream-infrastructure-blocked re-arm: if the comparison arm produces 0-emit, re-arm collection once. Re-arm fired; collection extended ~6 days.
3. **Second iteration also produced 0-emit** — 0/53 overlap across the full re-armed window.
4. **Amendment 02 (`0ad7249`) iteration-limit** — explicitly froze: "the original parent pre-reg's iteration-limit (one extension only, then methodology-as-finding) applies to the re-armed run as well." Second-iteration 0-emit is the terminal condition.

This maps onto the frozen template's **Subcondition C3** ("both limiters fired → the experimental design itself is the finding → case study published inconclusive"), with a **sharper, more specific structural mechanism than C3's template language**. C3's template anticipated "not enough overlap density." The actual mechanism is stronger: overlap is not merely sparse, it is **structurally zero by lifecycle-stage non-comparability**.

### The structural mechanism (the finding)

- graduate-oracle predicts in **lane-60s**: mints aged 30–60s (config `age_max=75`). This is frozen scope per `feedback_lane_60s_only.md`.
- GMGN's `--filter-preset strict` is **maturity-gated**: it filters `new_creation` on holder count, bundler rate, progress, and related thresholds that a 30–180s-old mint cannot satisfy.
- The join window is `predicted_at ± 120s`. At that instant the mint is ≤~3 minutes old on-chain.
- A mint that young is **structurally incapable** of being in GMGN strict-preset. By the time it could qualify, it is minutes old — outside lane-60s, so graduate-oracle no longer emits on it.

47/53 GMGN snapshots joined correctly within the timing window — the harness instrumentation worked exactly as designed. The zero is not a harness failure, not a credentials failure, not a field-alias bug (all three were ruled out: gmgn-cli runs, returns valid data, addresses parse). **The two systems sample the same mint population at disjoint points in the mint lifecycle.** A head-to-head precision comparison between them is not hard — it is ill-posed.

---

## What this finding IS — and the load-bearing caveat

This empirically establishes **lifecycle non-comparability and the pre-bond timing edge**: graduate-oracle commits a prediction before maturity-gated tools like GMGN strict can see the mint at all. That maps directly to `project_pre_bond_edge_positioning.md`.

**The caveat that makes this airtight rather than a premature victory lap (this section is load-bearing, not a footnote):**

This finding proves graduate-oracle fires **earlier**. It does **not** prove graduate-oracle fires **more accurately**. "We see it before GMGN" is only a moat if the early call is *predictive* — and predictive accuracy is **not measured by this case study**. It is owned by the separate forward-validation track, which has not reached verdict.

Stated precisely, and the way every downstream artifact (X, TG, B2B deck) must state it:

> **Case Study 01 establishes lifecycle non-comparability and the pre-bond timing edge — graduate-oracle commits its call before maturity-gated competitors can observe the mint. Whether that early signal is *accurate* is a separate question, measured by the forward-validation track, verdict pending. The timing edge is a moat only conditional on that forward-validation resolving positive.**

Decoupling these two claims is a hard rule. This session already produced one n=7 over-claim retraction (`feedback_no_bandaids_filter_extension.md`); shipping "we fire earlier" as if it meant "we fire better" would be the same error at the receipts-brand level, and the brand would have to walk it back publicly. Coupled, the claim is exact and defensible. Uncoupled, it is cope dressed as a moat.

---

## What this study does NOT claim

- Does **not** claim graduate-oracle outperforms GMGN. The comparison could not be constructed; no precision delta exists to report.
- Does **not** claim graduate-oracle underperforms GMGN. Same reason.
- Does **not** claim the early signal is accurate. That is the forward-validation track's question, pending.
- Does **not** relax the pre-registered Branch A/B thresholds to force a verdict. Doing so would be the exact post-hoc rationalization the discipline forbids.
- Does **not** mean the methodology failed. The methodology produced a clean, data-anchored "this comparison is ill-posed, here is the structural reason" — a valid and informative terminal outcome of frozen acceptance criteria.

---

## Note on Amendment 02 (composite-vs-GMGN axis)

Amendment 02 (`0ad7249`) pivoted the intended comparison axis from `grad_prob` bucket to `composite_score ≥ P90 + MC ≥ $5k`. The composite filter adapter was never built; the harness ran the original `grad_prob` axis throughout. **This does not change the verdict**: composite is also computed in lane-60s, so it inherits the identical lifecycle non-comparability against a maturity-gated strict preset. Building the adapter would not have produced overlap. The structural finding holds on either axis — which strengthens, not weakens, it.

---

## What gets done (per frozen template, Subcondition C3 branch)

- This commit is the **terminal state**. The GMGN head-to-head axis is closed inconclusive-by-design.
- The 53-observation graduate-oracle arm is clean lane-60s outcome data; it remains available to the forward-validation track (no GMGN dependency).
- Reusable harness gains a pre-registered **overlap-feasibility pre-check** for Studies 02–04: before committing to a precision comparison, verify the two sources sample overlapping lifecycle stages. Surface lifecycle non-comparability in the first ~6h, not after 6 days. Same recursive shape as the iteration-limit rule — pre-register feasibility, not just acceptance criteria.
- The product-reshape question from Branch B does **not** auto-fire. C3 is its own scope: addressable-comparison shape, not bucket precision.

---

## Reusable harness performance

The instrumentation performed exactly as designed: captured both feeds, joined per frozen specs (47/53 within the ±120s window), resolved 98%, and produced the data that drove the terminal Branch C fire. The inconclusive verdict is data-driven and structure-driven, not infrastructure-driven. The lesson is methodological, not engineering: **pre-register an overlap-feasibility checkpoint** so lifecycle non-comparability surfaces in hours, not after a multi-day window.

---

## Receipts trail

| Commit | Action |
|---|---|
| `01d083d` | Case Study 01 pre-registration + 3 branch templates + reusable harness scope (frozen methodology) |
| `5465b1f` | Phase 2 scaffold; daemon idle awaiting 2026-05-09T16:45Z trigger |
| `f11ed62` | gmgn-cli credentials prelude |
| `3a0df04` | gmgn-cli response-shape + field-alias fix |
| `772e5a6` | Branch C Amendment 01 — Subcondition C-iv (upstream-block re-arm), pre-verdict |
| `84a75b7` | Harness silent-enrichment-bug postmortem + fix, pre-verdict |
| `06fc113` | Harness fix deploy receipt; observations populated |
| `0ad7249` | Amendment 02 — composite-vs-GMGN axis pivot + iteration-limit reaffirmed, pre-verdict |
| `e36559a` | Phase 2 instrumentation (`go_entry_mult` for Audit 12-B Phase 2) |
| **(this commit)** | **Case Study 01 results — Branch C terminal: structural non-comparability (C-iv 2nd-iteration 0-emit → iteration-limit). Inconclusive-by-design; pre-bond timing edge established, accuracy claim explicitly deferred to forward-validation.** |

---

## Cross-channel artifacts (publish-then-post; caveat-carrying variants)

These replace the frozen template's draft variants — the template's language predated the structural finding and did not carry the accuracy-decoupling caveat. The caveat is mandatory in every downstream artifact.

**X thread variant:**

```
case study 01 — terminal verdict: inconclusive by structural design.

we pre-registered a head-to-head precision test vs GMGN strict-preset.
ran it twice (original window + a pre-registered re-arm). both times:
zero overlap. 0 of 53.

not a bug — the harness joined 47/53 snapshots fine, gmgn-cli works.
the reason is the finding: we predict at 30-60s. GMGN strict is
maturity-gated and can't see a mint that young. by the time it can,
we no longer fire. the two tools sample the same mints at
non-overlapping lifecycle stages. the comparison is ill-posed.

what this proves: we fire earlier — before maturity-gated tools can
observe the mint at all.

what this does NOT prove: that the early call is accurate. that's a
separate track (forward-validation), verdict pending. the timing edge
is a moat only if that resolves positive. saying otherwise would be
cope dressed as a moat, and we don't do that.

per pre-registered Branch C iteration-limit, this publishes
permanently inconclusive on the head-to-head axis. inconclusive at
frozen criteria, with the structural reason anchored, is the receipt.

prereg: 01d083d
results: github.com/Based-LTD/graduate-oracle/blob/main/docs/research/case_study_01_gmgn_results.md
```

**TG channel pinned variant:**

```
📊 Case Study 01 — terminal verdict: inconclusive by structural design.

Pre-registered head-to-head vs GMGN strict-preset. Ran twice; 0 of 53 overlap both times. Not a harness failure (47/53 joined fine) — we predict at 30–60s, GMGN strict is maturity-gated and structurally can't see a mint that young. The tools sample the same mints at disjoint lifecycle stages; the comparison is ill-posed.

Proves: we fire EARLIER. Does NOT prove: the early call is accurate — that's the forward-validation track, verdict pending. Timing edge is a moat only conditional on that.

Per pre-registered Branch C iteration-limit: published permanently inconclusive on this axis. The structural finding, anchored in data, is the receipt.

Full writeup: github.com/Based-LTD/graduate-oracle/blob/main/docs/research/case_study_01_gmgn_results.md
```

---

## Discipline note

PASS/FAIL/INCONCLUSIVE was pre-registered with all three branches scoped and a decision tree for each, including the iteration-limit and the upstream-block re-arm. That advance scoping is what makes this terminal inconclusive credible: the framing was committed before the data, not crafted after it to explain a null. Same shape as the iteration-limit memory rule — pre-register the stop-iterating point and the inconclusive handling before the data lands. The one addition the data forced — the accuracy-vs-timing decoupling caveat — tightens a claim rather than relaxing a criterion, and is itself logged against the n=7 over-claim lesson from this session.
