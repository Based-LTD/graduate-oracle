# Audit 12 — Hot-launch composite + MC floor signal validation

**Pre-registration commit.** Methodology, acceptance criteria, and outcome
branches frozen here BEFORE any data collection. Per the receipts pattern,
this commit must predate the data-collection window's start. Same shape
as Case Study 01 pre-reg (5bc8f33) and Audit 02/03 drafts. If a frozen
criterion needs revision after this commit, the revision must (1) be
publicly committed before the data resolves the criterion, (2) refine
or split rather than relax, and (3) explicitly surface the design flaw
being corrected.

**Series:** Audit 12 of the measurement program. Promoted to Tier 1
priority after the user empirically tested the dashboard sort/filter
feature (shipped 2026-05-10) and reported the hot-launch composite + MC
floor signal as "actually incredible" — possibly the most important
product feature. This audit validates that observation rigorously
before any product pivot.

---

## Strategic context

Graduate Oracle is currently positioned around `grad_prob` as the
headline calibrated probability. The user's empirical observation
(2026-05-10) suggests an alternative signal — **hot-launch composite
(smart_money_in × max_mult × freshness) + market-cap floor (~$5k+)** —
may produce materially better trading signal than `grad_prob` alone.

This audit tests that hypothesis. Outcomes have direct product-spec
implications:

- **If composite + MC floor wins decisively:** product reshapes around
  the composite as headline. `grad_prob` stays as a methodology artifact
  feeding the receipts moat, but is no longer the surface-level value
  proposition. The model isn't the moat; the data + correct surfacing is.
- **If `grad_prob` wins or ties:** composite stays as one of several
  sort options. No pivot.
- **If both fail to beat baseline:** the deeper question is what IS
  carrying signal in this product. Triggers downstream investigation.

The audit also intersects directly with **Case Study 01** (graduate-
oracle calibrated bucket vs GMGN strict-preset). If composite raw-
component signal beats `grad_prob`, that's evidence Case Study 01's
Branch B (thesis undermined → reshape product) is the more likely
outcome at Tuesday verdict.

---

## Hypothesis (frozen)

**H1 (primary):** Hot-launch composite signal paired with a
market-cap floor produces ≥10pp better hit rate than `grad_prob` ≥
0.5 filter, on the same 14-day mint set, where "hit rate" is defined
as (graduation OR achieving 2x within 24h post-cross).

**H0 (null):** Hit-rate difference within ±5pp, OR `grad_prob` filter
wins.

The 10pp / 5pp thresholds are pre-registered to match the discipline
of Case Study 01 and Audit 03 — significant lift, not noise.

---

## Methodology (frozen)

### Composite definition

```
composite_score(mint) = smart_money_in × max_mult × freshness_factor
where freshness_factor = 1 / (1 + age_s / 600)
```

This matches the dashboard `_applySort(mints, 'hot')` definition shipped
in commit (path E deploy 147777d, dashboard sort/filter feature).

### Filter conditions

**Composite arm:**
- `composite_score(mint) ≥ THRESHOLD_COMPOSITE`
- `market_cap.usd ≥ MC_FLOOR_USD`
- mint is in /api/v1/live (observer-tracked)

**grad_prob arm:**
- `grad_prob ≥ 0.5`
- `grad_prob_bucket IN ('HIGH', 'MED')` (in-lane prediction made)

### Frozen parameter values

- `THRESHOLD_COMPOSITE`: top-20-percentile of historical composite scores
  across the last 14 days. Captured at audit-start time and frozen for
  the audit window.
- `MC_FLOOR_USD`: $5,000 (matches user's reported empirical floor)
- Forward window: 14 days from data-collection start

### Outcome resolution

For each mint that crosses ANY filter threshold during the window:
- Track for 24h after threshold-cross
- Capture: `outcome_graduated` (boolean), `outcome_max_mult_24h` (float),
  `hit_rate_event` (graduated OR max_mult ≥ 2.0)
- "Hit" = `hit_rate_event = True`

### Sample-size targets

- Minimum 100 mints crossing the composite filter over 14 days
- Minimum 100 mints crossing the grad_prob filter over 14 days
- Minimum 30 hits in each arm

### Exclusion rules (frozen)

- Mints with `hide_reason` set at threshold-cross time excluded (already-
  flagged stale activity)
- Mints unresolved at audit-cutoff (14d + 24h grace) excluded from primary
  precision comparison (reported as sample-size note)
- Mints crossing BOTH filters at any point: counted in both arms;
  precision comparison handles this with per-mint outcome dedup

---

## Acceptance criteria (frozen)

Three pre-registered outcome branches, each with a pre-drafted writeup
template (committed in this same commit cycle alongside the pre-reg, per
Case Study 01 publish-then-post discipline).

### Branch A — Composite wins (product pivot)

**Conditions ALL of:**
1. Sample size: composite arm n ≥ 100, grad_prob arm n ≥ 100
2. Composite arm hit rate ≥ grad_prob arm hit rate + 10pp
3. The lift holds when restricted to mints unique to composite arm
   (not shared with grad_prob arm)

**Action on Branch A:**
- Public writeup ships ("composite + MC floor outperforms grad_prob by Xpp")
- Product reshape pre-registration drafted: composite elevated to headline,
  grad_prob retained as methodology artifact + receipts feed
- Dashboard UX redesign to reflect new headline
- Case Study 01 Branch B verdict more likely if it hasn't already fired
- Audit 11 (broad-except inventory) maintains priority; calibration
  backlog ticket remains investigative

### Branch B — grad_prob wins or ties (no pivot)

**Conditions ANY of:**
1. Sample size sufficient AND composite hit rate ≤ grad_prob hit rate
2. Sample size sufficient AND difference within ±5pp

**Action on Branch B:**
- Public writeup ships with the negative finding for the composite hypothesis
- Composite stays as one sort option among many; no pivot
- Validates the calibration moat: model adds value over raw composition
- Case Study 01 Branch A verdict more likely

### Branch C — Insufficient sample / both fail baseline

**Conditions:**
- Either arm has n < 100 at audit cutoff
- OR both arms hit rate < 30% (failing to beat naive base rate)

**Action on Branch C:**
- Public writeup with the inconclusive result
- Pre-registered decision tree:
  - n < 100: extend collection by 14 days (one extension only)
  - Hit rates < 30%: surface deeper question — what IS carrying signal in
    this product? Pre-register Audit 13 (broader feature exploration)

---

## Pre-drafted writeup templates

The actual writeup texts (Branch A, B, C variants) are committed
alongside this pre-registration at:

- `audit_12_results_branch_a_template.md`
- `audit_12_results_branch_b_template.md`
- `audit_12_results_branch_c_template.md`

Terminal numbers (precision figures, sample counts) are the only fields
that fill in at outcome time. Same publish-then-post pattern as Finding
8's pre-drafted TG/X messages and Case Study 01's branch templates.

---

## Schedule (frozen)

| Phase | Time | Action |
|---|---|---|
| Pre-reg commit | Now (2026-05-10) | This document committed + pushed |
| Harness extension | +30min | Extend case_study_harness with Audit 12 adapter |
| Wait window | Until Case Study 01 verdict resolves (~Tuesday 2026-05-12) | Avoid concurrent collection with Case Study 01 to keep methodology clean |
| Collection start | After Case Study 01 verdict | 14d daemon run begins |
| Collection end | +14d | Daemon stops; analysis begins |
| Grace window | +24h | Outcome resolution for last mints |
| Branch verdict | +1h after grace | Pre-registered branch executes |
| Public writeup | +1h after branch | Outcome-appropriate writeup committed |

Total elapsed from pre-reg commit to writeup: ~17 days.

---

## Reusable harness extension

The case_study_harness scaffold (commit 08fb96c) extends for Audit 12
with one new adapter file: `case_study_harness/sources/composite.py`.
This adapter:
- Reads `/data/data.sqlite` predictions table (same as grad_oracle.py)
- Computes composite score per mint at observation time
- Returns mints crossing composite + MC floor filter

Outcome resolver, joiner, and run loop are unchanged. Same shape as
Audit 01 → 02 → 03 progression: each audit is one new adapter + one
new config.

Per the broad-except feedback rule (feedback_broad_except_silent_failure.md),
the new adapter MUST use narrow except handlers + assert-on-startup +
structured logging on miss. Lesson learned from Case Study 01's 25h
silent-bug incident.

---

## Discipline note

This audit is not "validate what we already believe." It's a
methodologically pre-registered comparison whose result the team has
publicly committed to publishing in any direction. The user named the
$10M acquisition north star contingent on this kind of receipts work
compounding over 12+ months; this audit is part of that compounding.

If the data shows hot-launch composite + MC floor adds a measurable
lift over `grad_prob`: that's the empirical foundation for a product
pivot that elevates raw-data + correct-surfacing as the moat.

If the data shows it doesn't: that's the empirical foundation for
defending the calibration architecture and keeping `grad_prob` as
headline. Either outcome moves the moat forward.

---

**Pre-registration committed at:** [commit hash inserted at commit time]
**Pre-registration committed by:** [implementer]
**Data collection scheduled to start:** After Case Study 01 verdict
**Branch verdict scheduled:** ~17 days post-collection-start
**Public writeup scheduled:** ~1h after branch verdict
