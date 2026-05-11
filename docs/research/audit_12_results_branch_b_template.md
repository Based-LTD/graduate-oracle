# Audit 12 — Branch B (grad_prob wins on both axes, no pivot)

**Template — fill in terminal numbers at verdict time. Pre-drafted before
any data was collected, per pre-registration audit_12_hot_launch_composite_validation_prereg.md
and tightened per amendment audit_12_amendment_01_coverage_gap_dual_measurement.md.**

**Tightened in Amendment 01 (2026-05-10):** Branch B now requires composite
to fail on BOTH precision AND coverage to qualify as "grad_prob wins." The
original criterion (composite precision ≤ grad_prob precision or within
±5pp) was incomplete — it would have classified the empirically-observed
"composite catches what grad_prob doesn't bother predicting on" pattern
as Branch B when the truthful classification is Branch A' (complementary
layer). Branch B is now reserved for the case where composite truly adds
nothing meaningful on either axis.

---

## Verdict (fill at outcome)

```
Audit 12 collection window: [START_DATE] → [END_DATE]
Branch fired:                 B — grad_prob wins on both axes
Verdict ships:                [VERDICT_DATE]
```

---

## Frozen conditions (Branch B fires when ANY of)

1. **Composite fails on BOTH axes simultaneously:**
   - Composite precision < grad_prob precision − 10pp
   - AND composite-only coverage < 20% of all window graduators
2. **Composite covers no incremental territory:** both arms have ≥30%
   precision but coverage difference within ±10pp (composite adds nothing
   meaningful that grad_prob doesn't also catch — the two arms collapse
   onto the same mint set)

Note: this is strictly tighter than the original criterion. A case where
composite has lower precision BUT high composite-only coverage now fires
Branch A' (complementary layer), not Branch B. Branch B requires composite
to add **no value on either axis** vs grad_prob.

---

## Result

Hot-launch composite + MC floor failed on BOTH the precision-lift axis
AND the composite-only coverage axis (OR collapsed onto grad_prob's mint
set). Composite did not produce meaningful incremental value.

```
Composite arm:
  n_total:                   [COMPOSITE_N]
  composite_precision:       [COMPOSITE_PRECISION]
  graduations:               [COMPOSITE_GRADS]

Grad_prob arm:
  n_total:                   [GRADPROB_N]
  grad_prob_precision:       [GRADPROB_PRECISION]
  graduations:               [GRADPROB_GRADS]

Coverage matrix:
  Composite-only graduators: [COMPOSITE_ONLY_GRADS]  ([COMPOSITE_ONLY_PCT]% of all window graduators)
  Grad_prob-only graduators: [GRADPROB_ONLY_GRADS]   ([GRADPROB_ONLY_PCT]% of all window graduators)
  Both-arm graduators:        [BOTH_GRADS]            ([BOTH_PCT]% of all window graduators)
  Neither-arm graduators:     [NEITHER_GRADS]         ([NEITHER_PCT]% of all window graduators)

Branch B conditions:
  Precision lift:            [LIFT_PP]pp  (composite − grad_prob; FAIL: < −10pp OR within ±5pp on collapse case)
  Composite-only coverage:   [COMPOSITE_ONLY_PCT]%  (FAIL: < 20%)
  Both axes failed:          ✓ (or coverage-collapse case: precision both ≥30% AND coverage diff within ±10pp)
```

---

## What this means

The user's empirical observation (hot-launch composite + MC floor produces
"actually incredible" signal) **did not validate at scale.** Either the
sample size of the original observation was too small to be representative,
or the composite signal works on a subset of mints that the broader
14-day window dilutes.

The calibrated probability output (`grad_prob`) is doing its job: adding
value over the raw component composition. The model IS the moat at this
lane; calibration matters.

---

## What stays the same

- `grad_prob` remains the headline calibrated probability output
- Composite stays available as a sort option among many on the dashboard
- MC floor filter remains a useful tool but is not elevated to default
- Product positioning unchanged from pre-audit

---

## What this validates

Branch B is a **defensive validation outcome** — it confirms the
calibration architecture is adding measurable value. This strengthens
the receipts moat narrative: "we tested an alternative hypothesis that
would have undermined our product, and the data said our product
architecture is right."

This also informs Case Study 01: if Audit 12 lands Branch B and Case
Study 01 verdicts Branch A (calibrated bucket beats GMGN strict-preset),
both findings reinforce each other. The calibration does what it's
supposed to do.

---

## Honest caveat

The user's original observation was empirically observed in production
use. Branch B doesn't mean the user was wrong — it means the lift was
not generalizable across a 14-day forward window at scale. Possible
explanations to surface in the writeup:

- The signal works on a specific mint type the broader window dilutes
- The user's natural pattern-matching adds skill the audit doesn't capture
- MC floor at $5k is a useful filter but not the load-bearing piece
- Time-of-day or market-cycle conditions during user's observation
  weren't representative

A future Audit 13 (composite signal decomposition) could investigate
which combinations of features within the composite + filters carry
signal at narrower time windows.

---

## What ships next

- Public commit: this writeup + numbers filled in
- No product pivot; composite stays as one sort option among many
- Audit 13 candidate: composite-feature decomposition (optional follow-up)
- X post / TG announcement: pre-drafted variant in `x_post_audit_12_branch_b.md`
  (frames as "we tested whether our product architecture was wrong; the
  data says it isn't")

---

**Verify yourself:** every commit in the Audit 12 chain timestamped; the
pre-registration predates this verdict by 14+ days; the dataset is
committed alongside this writeup. The Branch B template was committed
publicly BEFORE any data was collected — proof that the negative-finding
discipline applies even when the result confirms our existing thesis.
