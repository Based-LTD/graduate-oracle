# Audit 12-A — Composite retroactive validation (pre-registration)

**Pre-registration commit.** Methodology and frozen acceptance criteria committed BEFORE the stratified analysis query runs. Per the receipts pattern: this commit predates the results commit. Same publish-then-post discipline as Audit 09 (`4992703` → `34ce847`) and Case Study 01 amendment chain.

**Series:** Audit 12-A complements the original Audit 12 (`audit_12_hot_launch_composite_validation_prereg.md`, forward 14d collection). Audit 12-A is **retroactive** on existing `/data/data.sqlite` tables — ships in hours, not weeks, but with explicit methodology limits noted below. The two audits answer different questions:

- **Audit 12-A (this audit):** Does the composite-score formula's STRUCTURE separate winners from losers when applied retroactively to existing snapshots? Tests formula validity. Limitation: cannot use forward-only snapshot fields (MC floor, max_mult-at-predicted_at).
- **Audit 12 (forward):** Does composite + MC floor catch winners under live conditions? Tests product-grade signal under forward collection.

Audit 12-A reduces uncertainty about whether to invest the 14 days of forward collection on Audit 12. If retroactive validation shows the composite-formula structure carries no signal, Audit 12's forward window is questionable. If retroactive validates strongly, forward Audit 12 is the natural next step.

---

## Strategic context

User direction (2026-05-11): ship Audit 12-A retroactive composite validation in parallel with the wallet redaction deploy and Case Study 01 Amendment 02. Same shape as Audit 09 (commit `34ce847`).

The composite signal (`smart_money_in × max_mult × freshness_factor`) was empirically observed by the user (2026-05-10) as "actually incredible" — possibly the most important product feature. Audit 09 already validated the smart_money piece alone (7.37× graduation lift at sm≥7 stratum, monotonic 2x-runner trend). Audit 12-A extends that validation to the multiplicative composite structure: does the formula's interaction term `smart_money × max_mult` carry information beyond smart_money alone?

---

## Hypothesis (frozen)

**H1 (primary):** The composite-score formula, computed retroactively as `smart_money_in × actual_max_mult × freshness_at_age_60`, separates mints into outcome strata with at least a 2× lift in graduation rate between the top composite quartile (Q4) and the bottom (Q1), with monotonic trend across Q1→Q4 and non-overlapping 95% CIs at the Q4 vs Q1 comparison.

**H0 (null):** No monotonic relationship OR Q4-vs-Q1 graduation lift < 2× OR CIs overlap.

The 2× threshold and monotonicity discipline match Audit 09's frozen criteria, with **the Audit 09 methodology-design note explicitly applied**: monotonicity test uses CI-aware logic (each successive stratum's mean is ≥ previous stratum's mean OR the CIs overlap at the boundary). This avoids the strict-test failure mode that pushed Audit 09 to PARTIAL despite overwhelming substantive evidence.

---

## Methodology (frozen)

### Data source

Retroactive analysis on `/data/data.sqlite`. Same join shape as Audit 09 but with the multiplicative composite computed from snapshot + lifecycle-peak fields.

```sql
WITH first_pred AS (
    SELECT mint, MIN(predicted_at) AS first_at
      FROM predictions
     WHERE predicted_at >= [WINDOW_START]
       AND actual_max_mult IS NOT NULL
     GROUP BY mint
)
SELECT p.mint, p.predicted_at, p.actual_graduated, p.actual_max_mult,
       mc.feature_smart_money,
       mc.actual_rugged, mc.max_drop_pct_5min
  FROM first_pred fp
  JOIN predictions p ON p.mint = fp.mint AND p.predicted_at = fp.first_at
  JOIN mint_checkpoints mc ON mc.mint = p.mint AND mc.checkpoint_age_s = 60
 WHERE mc.feature_smart_money IS NOT NULL
   AND p.actual_max_mult IS NOT NULL
```

### Composite computation (frozen)

Per the live dashboard formula (`web/static/app.js:683`, verified 2026-05-11):

```js
const freshness = 1 / (1 + age / 600);   // half-life ~10 min
return sm * mult * freshness;
```

Retroactive computation per mint:

- `sm` = `mc.feature_smart_money` (snapshot at age=60s — forward-safe; was known at predicted_at)
- `mult` = `p.actual_max_mult` (lifecycle peak — **NOT** the value at predicted_at; see Limitation below)
- `age` = 60 (lane-60s commitment; freshness ≈ 0.909 constant across all rows)

```
composite_score = feature_smart_money * actual_max_mult * (1 / (1 + 60/600))
                = feature_smart_money * actual_max_mult * 0.909
```

Since `freshness_at_age_60` is constant across all rows in our lane-60s lane, it scales but doesn't differentiate. **The effective stratification variable is `feature_smart_money × actual_max_mult`.** Composite quartiles are computed on this product.

### Stratification

Composite-score quartiles computed across the full sample (after filtering to mints with non-null `feature_smart_money` and resolved `actual_max_mult`):

| Quartile | Range |
|---|---|
| Q1 (lowest) | composite ≤ 25th percentile |
| Q2 | 25th < composite ≤ 50th percentile |
| Q3 | 50th < composite ≤ 75th percentile |
| Q4 (highest) | composite > 75th percentile |

### Per-stratum metrics

Identical structure to Audit 09 for cross-audit comparability:

1. **n** — count of mints
2. **graduation_rate** = `count(actual_graduated = 1) / n`
3. **2x_runner_rate** = `count(actual_max_mult >= 2.0) / n`
4. **peak_mult_p50** — median of `actual_max_mult`
5. **peak_mult_p90** — 90th percentile of `actual_max_mult`
6. **rug_rate** = `count(actual_rugged = 1 OR max_drop_pct_5min >= 70) / n`
7. **95% binomial Wilson CIs** on graduation_rate and 2x_runner_rate

### Comparative lift

- `lift_grad(Q_n) = grad_rate(Q_n) / grad_rate(Q1)`
- `lift_2x(Q_n) = 2x_runner_rate(Q_n) / 2x_runner_rate(Q1)`

### Monotonicity test (CI-aware, per Audit 09 methodology note)

For each metric (graduation_rate, 2x_runner_rate):

- The trend is **CI-aware monotonic** if for each successive quartile pair (Q_i, Q_{i+1}), either the point estimate is monotonic OR the 95% CIs overlap at the boundary.
- The trend is **strictly monotonic** if point estimates are strictly non-decreasing across Q1→Q4.

Report both. Acceptance branching uses CI-aware monotonicity as the primary test; strict monotonicity is reported as a supplementary observation.

---

## Limitations (frozen, surfaced for verdict context)

### Leakage caveat — `actual_max_mult` is post-outcome

`actual_max_mult` is the lifecycle peak observed across the full resolution window (24h post-prediction). It is **not** the max_mult that was visible at predicted_at — at predicted_at, the live dashboard would have shown the running-max-up-to-age-60, which is typically near 1.0 for fresh mints. Using `actual_max_mult` in the retroactive composite means **the composite-score in this audit incorporates outcome information**.

This is a structural limitation of the available data, not a methodology choice. It has two consequences:

1. **The audit cannot test "would high-composite-at-predicted_at predict outcomes?"** That's the forward-prediction question and requires Audit 12's forward collection.
2. **The audit CAN test "does the composite formula's structural form (smart_money × peak_mult) correctly stratify graduators from non-graduators?"** That's a useful but distinct question. A passing result means the formula identifies winners after the fact; a failing result means the formula doesn't even structurally separate them.

This is **post-hoc structural attribution**, not forward prediction. Verdict framing reflects this distinction.

### Skipped: MC floor stratification

The user's original Audit 12-A spec requested stratification by MC floor (<$1k, $1-5k, $5-10k, >$10k). **No MC snapshot column exists in `predictions` or `mint_checkpoints`.** MC at predicted_at is not retrievable from sqlite alone. MC floor stratification is **deferred to Audit 12 (forward collection)** where the case_study_harness can capture MC snapshots at predicted_at via the existing observer/curves pipeline.

This is a data-availability constraint, not a methodology choice. The forward Audit 12 was designed for MC floor stratification; Audit 12-A focuses on the composite-formula structural test.

### Freshness factor is constant in this dataset

All predictions in the dataset are at `age_bucket = 60` (lane-60s commitment). `freshness_at_age_60 = 1 / (1 + 60/600) = 0.909` is a constant scalar that doesn't differentiate strata. The effective stratification variable reduces to `smart_money × actual_max_mult`. Forward Audit 12 with multi-age-window snapshots would test the freshness factor's contribution; Audit 12-A does not.

---

## Acceptance criteria (frozen, CI-aware monotonicity per Audit 09 design note)

### Branch VALIDATED — Composite formula structurally separates outcomes

**Conditions ALL of:**
1. Sample size: n ≥ 50 per quartile (n ≥ 200 total)
2. Resolution rate: 100% (query filters to resolved outcomes)
3. CI-aware monotonic trend across Q1→Q4 for graduation_rate AND 2x_runner_rate
4. `lift_grad(Q4)` ≥ 2.0× over Q1, AND non-overlapping 95% CIs at Q4 vs Q1

**Action:** Composite formula's structural form is empirically validated. This is evidence for proceeding with Audit 12 forward collection (which tests the live forward-prediction question with MC floor stratification). Also evidence for the dual-track strategy: composite isn't just `smart_money_in` rebranded; the multiplicative structure adds separation power.

### Branch PARTIAL — Some structural signal, lift below threshold

**Conditions ALL of:**
1. Sample size sufficient (per Branch VALIDATED)
2. CI-aware monotonic on graduation_rate
3. `lift_grad(Q4)` ≥ 1.3× but < 2.0×, OR lift reaches 2× only on `2x_runner_rate`

**Action:** Composite has SOME structural signal but doesn't dominate `smart_money_in` alone (Audit 09's lift was 7.37× on grad_rate at sm≥7; PARTIAL here would mean composite_score quartiling produces weaker separation than smart_money stratification). User-owned decision on whether Audit 12 forward collection is justified given the partial structural validation.

### Branch NOT VALIDATED — Composite formula adds no signal

**Conditions ANY of:**
1. `lift_grad(Q4)` within ±20% of Q1 (flat trend)
2. Inverse trend — composite_score inversely correlates with grad_rate
3. CIs overlap across all quartile boundaries (no statistical separation at any level)

**Action:** The composite formula's structural form does NOT correctly separate winners from losers, even with `actual_max_mult` leakage HELPING the formula. This would be evidence that the composite signal works for a DIFFERENT reason than the formula suggests (perhaps it's driven entirely by `smart_money_in`, and the multiplicative interaction adds noise rather than signal). Pre-register Audit 12-B: decomposition test isolating which terms contribute. **Defer Audit 12 forward collection until decomposition is understood — running a 14d forward collection on a formula whose structure doesn't work retroactively is a low-value investment.**

### Branch INCONCLUSIVE — Insufficient sample

n < 50 in any quartile at the 30d cutoff. Extend window to 60d (one extension); if still insufficient, audit publishes methodology-as-finding.

---

## Pre-drafted results writeup

Results writeup ships as a separate commit AFTER this pre-reg commit, with the stratification table + CI bounds + verdict + retrospective/forward distinction filled in. Per publish-then-post discipline, pre-reg predates results, which predate any downstream Audit 12 forward-collection decision.

---

## Schedule

| Phase | Time | Action |
|---|---|---|
| Pre-reg commit | This commit | Methodology + frozen criteria + limitations committed before query runs |
| Results commit | Within ~30 min | Stratified analysis run; numbers filled in; verdict |
| Audit 12 forward-collection decision | After results commit | User-owned — proceed with original 14d Audit 12 forward, OR defer pending Audit 12-B decomposition |

---

## Receipts trail

| Commit | Action |
|---|---|
| `c2b3a8a` Audit 12 pre-reg + Amendment 01 | 4-branch criterion frozen for forward collection |
| `34ce847` Audit 09 results — wallet index lift validated retroactively | Methodology design note (CI-aware monotonicity) |
| `06480be` Wallet redaction Option A deploy receipt | Audit 09 evidence base; this Audit 12-A is fully compatible (no wallet identifiers used) |
| **(this commit) Audit 12-A pre-registration — composite retroactive validation** | Frozen methodology + acceptance criteria + limitations; CI-aware monotonicity applied |
| (next commit) Audit 12-A results | Stratified table + Wilson CIs + CI-aware monotonicity verdict + structural-vs-forward framing |

---

## Compatibility with wallet redaction

This audit uses ONLY aggregate counts (`feature_smart_money`) and outcome fields (`actual_graduated`, `actual_max_mult`, `actual_rugged`). **No wallet addresses are referenced, enumerated, or surfaced.** Fully compatible with the wallet redaction deployed at `06480be`. The results writeup will continue this discipline.

---

## Cross-references

- [`audit_09_smart_money_lift_prereg.md`](audit_09_smart_money_lift_prereg.md) — parent stratified analysis pattern
- [`audit_09_smart_money_lift_results.md`](audit_09_smart_money_lift_results.md) — PARTIAL verdict; methodology design note (CI-aware monotonicity) applied here
- [`audit_12_hot_launch_composite_validation_prereg.md`](audit_12_hot_launch_composite_validation_prereg.md) — sister forward-collection audit (different question)
- [`audit_12_amendment_01_coverage_gap_dual_measurement.md`](audit_12_amendment_01_coverage_gap_dual_measurement.md) — coverage-gap dual measurement (applies to forward audit)
- [`wallet_redaction_2026_05_11.md`](wallet_redaction_2026_05_11.md) — parallel deploy; this audit is wallet-redaction-compatible
- Memory: `project_wallet_index_is_the_moat.md` — strategic context
- Memory: `feedback_pre_registration_branches.md` — publish-then-post discipline
- Memory: `feedback_methodology_calls_user_owned.md` — downstream Audit 12 forward decision is user-owned
