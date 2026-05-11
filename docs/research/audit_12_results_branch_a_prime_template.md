# Audit 12 — Branch A' (composite as complementary product layer)

**Template — fill in terminal numbers at verdict time. Pre-drafted before
any data was collected, per pre-registration audit_12_hot_launch_composite_validation_prereg.md
and amendment audit_12_amendment_01_coverage_gap_dual_measurement.md.**

**This branch added in Amendment 01 (2026-05-10). The original 3-branch
criterion couldn't measure the empirically-observed pattern where composite
catches mints `grad_prob` doesn't even attempt to predict.**

---

## Verdict (fill at outcome)

```
Audit 12 collection window: [START_DATE] → [END_DATE]
Branch fired:                 A' — composite as complementary product layer
Verdict ships:                [VERDICT_DATE]
```

---

## Result

Hot-launch composite + MC floor signal **catches mints that `grad_prob`
misses entirely**, with precision comparable (within ±10pp) to `grad_prob`.
Both signals add value; they cover different mint populations.

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

Precision difference:        [PRECISION_DIFF]pp (within ±10pp ambiguity band)
Composite-only coverage:     [COMPOSITE_ONLY_PCT]% (≥20% threshold)
```

---

## What this means

The two signal layers are **complementary, not competing.** They operate
at different temporal scales (lane-60s calibration vs continuous coverage)
and catch different mint populations. Neither dominates the other on
precision; both add value through complementary coverage.

This is consistent with the empirical observations that motivated this
audit (project_hot_launch_composite_signal.md):

- **AT5rpTAqHsW... (2026-05-09):** 9 smart money, 6x runner — `grad_prob` silent (suspect filter); composite arm caught it
- **DaxgiZW81w4... (2026-05-09):** devil candle rug — `grad_prob` silent; composite arm caught it (composite ≠ "always positive prediction"; it's "high attention worthy")
- **HtoNB93hCvc3... (2026-05-10):** 11.78x organic graduator — no `grad_prob` prediction logged; composite arm caught it

The "no_predictions_logged_for_this_mint" case demonstrates that `grad_prob`
isn't just less accurate on these mints — it's not running on them at all.
Composite covers a gap the calibration layer explicitly excludes by design.

This validates the dual-track signal strategy (project_dual_track_signal_strategy.md):

- **`grad_prob` = moat infrastructure** — receipts trail, calibration
  discipline, audit-grade methodology. Continues unchanged.
- **Composite = product** — what users actually use to find signal. Ships
  as a first-class product layer alongside grad_prob.

---

## Product implications

1. **Both signals stay first-class.** Neither is sunset.
2. **Dashboard UX** — composite sort + MC floor remain available as user
   preferences. Default sort decision: composite OR `grad_prob`? Either
   defensible; favors composite because that's what users empirically use.
3. **B2B integration framing** — "we surface signals across two temporal
   layers: calibrated lane-60s commitment + continuous coverage." Sharper
   pitch than either alone.
4. **Receipts trail expands** — composite predictions get same logging
   discipline as `grad_prob` (per composite-receipts-logging task).

---

## What stays the same

- The lane-60s discipline for `grad_prob` (predict ONCE per mint at age 15-60s)
- The receipts moat (tamper-evident ledger, public commits, frozen acceptance criteria)
- Pre-bond positioning thesis (project_pre_bond_edge_positioning.md)
- The audit program (Audits 02-11 still queued)
- North star goal ($10M acquisition exit, project_10m_acquisition_goal.md)

---

## What ships next

- Public commit: this writeup + numbers filled in
- Update `/api/scope` to reflect dual-signal framing
- Dashboard UX confirmed (composite sort already shipped 2026-05-10)
- Audit 13 candidate: composite-feature decomposition (which composite
  term carries most signal? optimal MC floor?)
- X post / TG announcement: pre-drafted variant in `x_post_audit_12_branch_a_prime.md`

---

## The narrative this writeup supports

When you publish this verdict, the story is:

> *"We tested whether our composite signal could replace our calibrated
> probability as the headline product. The data said: neither replaces
> the other. They cover different mint populations, with comparable
> precision. We're shipping both as first-class signals — calibration
> for the receipts moat, composite for the product surface. The dual-track
> strategy is empirically validated."*

That's a stronger narrative than either "composite wins" or "grad_prob
wins." It demonstrates the team can hold two product hypotheses in tension
and run a rigorous comparison that supports both.

---

**Verify yourself:** every commit in the Audit 12 chain timestamped; the
pre-registration + amendment predate this verdict by 14+ days; the dataset
is committed alongside this writeup; the four-branch criterion structure
(A / A' / B / C) was committed before any audit data was collected.
