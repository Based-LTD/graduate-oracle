# Audit 12 — Branch A (composite wins on both axes, full product pivot)

**Template — fill in terminal numbers at verdict time. Pre-drafted before
any data was collected, per pre-registration audit_12_hot_launch_composite_validation_prereg.md
and tightened per amendment audit_12_amendment_01_coverage_gap_dual_measurement.md.**

**Tightened in Amendment 01 (2026-05-10):** Branch A now requires composite
to win on BOTH precision AND coverage. The original single-axis criterion
(precision lift ≥10pp alone) is preserved as a sub-condition; the new
coverage-floor sub-condition (composite-only coverage ≥20%) prevents a
case where composite has higher hit rate but covers only the same mints
grad_prob already catches — a precision lift without complementary value.

---

## Verdict (fill at outcome)

```
Audit 12 collection window: [START_DATE] → [END_DATE]
Branch fired:                 A — composite wins on both axes
Verdict ships:                [VERDICT_DATE]
```

---

## Frozen conditions (Branch A fires when ALL of)

1. **Sample size:** composite arm n ≥ 100 AND grad_prob arm n ≥ 100
2. **Precision lift:** composite precision ≥ grad_prob precision + 10pp
3. **Composite-only coverage:** composite catches ≥20% of all window graduators that grad_prob misses (composite-only / total graduators)

---

## Result

Hot-launch composite + MC floor wins on BOTH axes against the
`grad_prob ≥ 0.5` filter on the same mint set: **[LIFT_PP]pp precision
lift AND [COMPOSITE_ONLY_PCT]% composite-only coverage**, both satisfying
the pre-registered Branch A thresholds.

```
Composite arm:
  n_total:                   [COMPOSITE_N]
  composite_precision:       [COMPOSITE_PRECISION]
  graduations:               [COMPOSITE_GRADS]
  2x runners:                [COMPOSITE_2X]

Grad_prob arm:
  n_total:                   [GRADPROB_N]
  grad_prob_precision:       [GRADPROB_PRECISION]
  graduations:               [GRADPROB_GRADS]
  2x runners:                [GRADPROB_2X]

Coverage matrix:
  Composite-only graduators: [COMPOSITE_ONLY_GRADS]  ([COMPOSITE_ONLY_PCT]% of all window graduators)
  Grad_prob-only graduators: [GRADPROB_ONLY_GRADS]   ([GRADPROB_ONLY_PCT]% of all window graduators)
  Both-arm graduators:        [BOTH_GRADS]            ([BOTH_PCT]% of all window graduators)
  Neither-arm graduators:     [NEITHER_GRADS]         ([NEITHER_PCT]% of all window graduators)

Branch A conditions:
  Precision lift:            [LIFT_PP]pp  (≥10pp threshold) ✓
  Composite-only coverage:   [COMPOSITE_ONLY_PCT]%  (≥20% threshold) ✓
```

---

## What this means

The hypothesis under test was: **the composite signal we already collect,
surfaced correctly, outperforms our calibrated probability output.**

The data confirms it. The model isn't the moat at this lane; the data + the
correct surfacing is.

This is the empirically-supported outcome that triggers the pre-registered
product pivot.

---

## Product pivot (frozen at pre-reg)

1. **Composite elevated to headline.** The product's primary value
   proposition becomes "the trader-quality composite signal on every new
   pump.fun mint" rather than "calibrated graduation probability." MC
   floor + composite become the default sort + filter.

2. **`grad_prob` retained as methodology artifact.** The calibrated
   probability stays in the API and on the receipts ledger. Removed from
   the headline UX. Continues as input to the receipts moat (every
   prediction timestamped, audit trail preserved).

3. **Dashboard UX redesigned.** Sort defaults shift from grad_prob to
   composite (hot launch). MC floor default shifts from `no min` to `$5k`.
   Live feed presents composite-rank as the primary ordering.

4. **Public framing.** The receipts moat narrative continues unchanged:
   we publish the methodology, we publish the failures, we publish when
   our own product hypothesis turns out wrong. The pivot itself becomes
   a piece of the receipts trail — same shape as Finding 7 sunset.

5. **Case Study 01 implications.** If Audit 12 lands Branch A and Case
   Study 01 hadn't already verdicted Branch B, that becomes the more
   likely Case Study 01 outcome. Same underlying truth: raw composition
   beats calibration at this lane.

---

## What stays

- The lane-60s discipline (predict ONCE per mint at age 15-60s) is
  unaffected — composite scoring is also a lane-60s commitment shape
- The receipts moat (tamper-evident ledger, public commits, frozen
  acceptance criteria) is unaffected
- The audit program continues; Audits 03, 09, 11 still queued

---

## What ships next

- Public commit: this writeup + numbers filled in
- Public commit: dashboard UX changes (composite headline, MC floor default)
- Public commit: pre-registration of next audit (composite-vs-other-features
  decomposition — what within the composite carries the most signal?)
- X post / TG announcement: pre-drafted variant in `x_post_audit_12_branch_a.md`

---

**Verify yourself:** every commit in the Audit 12 chain timestamped; the
pre-registration (audit_12_hot_launch_composite_validation_prereg.md)
predates this verdict by 14+ days; the dataset (case_study_harness output)
is committed alongside this writeup.
