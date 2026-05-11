# Audit 12 — Amendment 01: coverage-gap dual measurement

**Pre-verdict amendment.** Committed publicly BEFORE Audit 12 data collection
starts. Same publish-then-post discipline as Finding 8's interim criterion
amendment (commit f3f1f3e, 2026-05-08). The original audit_12 pre-reg ships
unchanged; this amendment adds a complementary measurement axis and re-shapes
the outcome branches accordingly.

**Parent pre-reg:** `audit_12_hot_launch_composite_validation_prereg.md`

**Amendment commits at:** [TIMESTAMP at commit time]
**Audit 12 collection scheduled to start:** after Case Study 01 verdict
(Tuesday 2026-05-12). Amendment lands before collection start; criteria
are amended pre-data per the publish-then-post rule.

---

## What the original criterion missed

The original Audit 12 pre-reg framed the question as a **head-to-head
precision comparison**: does the hot-launch composite + MC floor filter
have higher hit rate than the `grad_prob ≥ 0.5` filter on the same mint
set?

This is a valid question, but it does NOT capture the strongest empirical
signal we've observed. The three example mints (project_hot_launch_composite_signal.md)
all share a property the original criterion can't measure:

- **AT5rpTAqHsW...** — `grad_prob` filter would have excluded it (suspect flag → no prediction)
- **DaxgiZW81w4...** — `grad_prob` filter would have excluded it (suspect flag → no prediction)
- **HtoNB93hCvc3...** — **no prediction logged at all** (`no_predictions_logged_for_this_mint`)

In a head-to-head precision comparison, these mints are in the composite
arm ONLY — `grad_prob` arm never even attempted them. Comparing arm hit
rates would understate the value of "composite catches what `grad_prob`
doesn't bother predicting on."

**The original criterion conflated two distinct concerns into one verdict:**

- **Precision (Concern A):** of mints that cross the composite filter, what % graduate / pump?
- **Coverage (Concern B):** of all actual graduators / pumpers in the window, what % does the composite arm catch vs `grad_prob` arm vs both vs neither?

A composite signal could:
- Have lower precision than `grad_prob` (it's noisier per-mint) BUT
- Cover graduators that `grad_prob` never attempts (it sees what `grad_prob` misses by design)

That's strictly valuable as a product — even if precision is lower, the
mints it covers are mints with no other signal layer pointing at them.
The original criterion would call this "Branch B (no pivot)" when the
empirical reality is "complementary product layer that adds value."

This is verification-by-content applied recursively to acceptance criteria
themselves (per feedback_pre_registration_branches.md, "publish-then-post
amendment of frozen criteria"). Same shape as Finding 8's amendment that
split EMA-fix verification from alert-volume verification.

---

## The amendment (frozen here)

### Measurement matrix (frozen)

For the 14-day collection window, every mint that crosses EITHER filter
gets categorized into a 2x2 matrix:

```
                       Grad_prob arm        Composite arm
                       (grad_prob ≥ 0.5     (composite_score +
                        AND bucket          MC floor ≥ $5k)
                        IN HIGH/MED)
                       --------------------------------------
arm only               A_only              C_only
both arms              ---- Both: A∩C ----
neither                ---- Neither: N ----
```

Outcome resolution captures, per mint:
- `outcome_graduated` (boolean)
- `outcome_max_mult_24h` (float)
- `hit_rate_event` (graduated OR max_mult ≥ 2.0)

### Frozen metrics

Compute, per arm:

1. **Composite precision** (original Concern A): hit rate within Composite arm = hits / total_in_arm
2. **Grad_prob precision** (original Concern A baseline): hit rate within Grad_prob arm
3. **Composite-only coverage** (new): graduators in Composite arm AND NOT in Grad_prob arm, as % of all graduators in window
4. **Grad_prob-only coverage** (new): graduators in Grad_prob arm AND NOT in Composite arm, as % of all graduators in window
5. **Both-arm coverage**: graduators caught by both, as % of all graduators
6. **Neither-arm coverage**: graduators caught by neither (control), as % of all graduators

### Amended acceptance criteria (frozen)

The original 3-branch structure (A — composite wins → pivot; B — grad_prob
wins → no pivot; C — inconclusive) is REPLACED with 4 branches that
capture both precision and coverage:

### Branch A — Composite wins on both axes (full product pivot)

**Conditions ALL of:**
1. Sample size: composite arm n ≥ 100, grad_prob arm n ≥ 100
2. Composite precision ≥ grad_prob precision + 10pp
3. Composite-only coverage ≥ 20% of all window graduators

**Action:** product reshapes to elevate composite as headline (per
project_dual_track_signal_strategy.md framing: grad_prob = moat, composite
= product). Same shape as original Branch A.

### Branch A' — Composite wins on coverage only (complementary product layer)

**Conditions ALL of:**
1. Sample size sufficient (n ≥ 100 per arm)
2. Composite precision within ±10pp of grad_prob precision (not a head-to-head winner)
3. Composite-only coverage ≥ 30% of all window graduators (substantial complementary coverage)

**Action:** ship composite as a complementary product layer alongside
`grad_prob`, NOT a replacement. Dashboard shows both signals side-by-side.
Per project_dual_track_signal_strategy.md, this is the "dual-track holds"
outcome — both signals serve different purposes, both contribute to
receipts. **This is the empirically-most-likely outcome given the three
example observations.**

### Branch B — Grad_prob wins (no pivot)

**Conditions ANY of:**
1. Composite precision < grad_prob precision − 10pp AND composite-only coverage < 20%
2. Both arms have ≥30% precision but coverage difference within ±10pp (composite adds nothing meaningful that grad_prob doesn't also catch)

**Action:** composite stays as one sort option among many; no product
pivot. Same as original Branch B.

### Branch C — Inconclusive

**Conditions:**
- Either arm has n < 100 at audit cutoff
- OR both arms hit rate < 30% (failing to beat naive base rate)
- OR neither arm catches ≥30% of all graduators (i.e., most graduators slip through both filters)

**Action:** same as original Branch C — extend by 14 days OR pre-register Audit 13 (broader feature exploration) depending on sub-condition.

---

## Why this amendment is strictly stronger

This amendment is NOT a relaxation. It's strictly more informative:

1. **Branch A retains the original precision-lift threshold** (≥10pp). Nothing weakens.
2. **Branch A' is NEW** — captures the empirically-observed pattern where composite catches mints grad_prob misses entirely. Original criterion would have called this Branch B (no pivot); amended criterion correctly identifies it as Branch A' (complementary product layer).
3. **Branch B's conditions tighten** — now requires composite to fail BOTH on precision AND on coverage to qualify as "grad_prob wins." Higher bar.
4. **Branch C is unchanged.**

The amendment closes a real design flaw in the original criterion: it conflated "which arm produces higher precision" with "which arm produces value as a product." Those are different questions; the data needs to answer both.

---

## What this amendment is NOT

**Not a post-hoc rationalization.** Amendment commits before Audit 12
collection starts. The Audit 12 pre-reg shipped before the three example
observations had been formally synthesized; this amendment incorporates
those observations into the criterion design BEFORE any of the audit's
14-day collection produces data.

**Not a precedent for arbitrary amendments.** Same narrow rule as the
Finding 8 amendment (committed at f3f1f3e): amendment must (1) commit
publicly before verdict data resolves the original criterion, (2)
refine/split rather than relax, (3) surface the design flaw explicitly.
All three conditions met.

**Not breaking the iteration-limit pre-registration.** The original
Audit 12 pre-reg's escalation rules (Audit 13 follow-up if Branch C
fires) remain unchanged.

---

## Pre-drafted branch templates

The three original branch templates (A, B, C) are updated as part of
this amendment cycle to reflect the new 4-branch structure:

- `audit_12_results_branch_a_template.md` — updated for Branch A (full pivot)
- `audit_12_results_branch_a_prime_template.md` — NEW for Branch A' (complementary layer)
- `audit_12_results_branch_b_template.md` — updated tighter criteria
- `audit_12_results_branch_c_template.md` — unchanged

Implementer ships all four templates in the same commit as this amendment,
per Case Study 01 publish-then-post discipline.

---

## Schedule (unchanged from original pre-reg)

| Phase | Time | Action |
|---|---|---|
| Amendment commit | Now (2026-05-10) | This document + updated branch templates committed |
| Wait window | Until Case Study 01 verdict (~Tuesday) | Avoid concurrent collection methodology mismatch |
| Collection start | After Case Study 01 verdict | 14d daemon run begins |
| Verdict | +15d after collection start | Per amended 4-branch criteria |

---

## Receipts trail (Audit 12 chain, with amendment)

| Commit | Action |
|---|---|
| [parent pre-reg commit] Audit 12 pre-registration | 4-branch criterion frozen pre-data |
| **(this commit) Audit 12 — Amendment 01: coverage-gap dual measurement** | Adds Branch A' (complementary layer); original criterion was conflating precision and coverage |
| [eventual] Audit 12 verdict commit | Whichever branch fires; numbers filled into appropriate template |

---

## Source

2026-05-10, user direction during the strategic conversation that
established the dual-track signal strategy (project_dual_track_signal_strategy.md).
Three empirical observations (project_hot_launch_composite_signal.md)
surfaced the coverage-gap pattern; this amendment incorporates that
observation into Audit 12's criterion design before any audit data is
collected.

User quote on the strategic framing this amendment serves:
> *"That moat is actually stronger in my opinion. Trying to reach a goal
> that might not be possible (but could be) and having an awesome signal
> as the product of the reach. Thats really interesting."*

The grad_prob track produces the audit-grade calibration discipline that
forms the moat. The composite track produces the user-facing product that
emerges as a byproduct of building the data infrastructure for the moat.
This amendment ensures Audit 12 can measure that emergence rigorously
rather than collapsing it into a head-to-head precision comparison.
