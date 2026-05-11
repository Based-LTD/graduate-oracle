# Audit 12-A — Composite retroactive validation (results)

**Verdict commit.** Numbers filled in from the stratified analysis run at 2026-05-11T05:26:55Z UTC. Methodology and frozen acceptance criteria committed in [`audit_12a_composite_retroactive_validation_prereg.md`](audit_12a_composite_retroactive_validation_prereg.md) (commit `ca4de62`, ~25 minutes before this results commit).

**Verdict:** **METHODOLOGY-EXPOSES-LEAKAGE** (does not match any of the pre-registered branches cleanly). The pre-reg explicitly warned that `actual_max_mult` in the composite would introduce leakage (results writeup § Limitations). The retroactive analysis surfaces that the composite formula's quartile stratification IS dominated by `actual_max_mult`, producing a massive 19.77× grad-rate lift at Q4 vs Q1 BUT with a 2x_runner_rate non-monotonicity at Q1→Q2 (40.3% → 19.4%) that exposes the leakage as the load-bearing separator rather than the formula's interaction structure.

**Practical implication:** retroactive analysis with the current data shape CANNOT cleanly answer the forward-prediction question the composite formula is designed for. Audit 12 forward collection (with snapshot max_mult at predicted_at + MC floor stratification) is the appropriate test. The composite formula IS NOT invalidated by this audit — but neither is it validated. The audit's contribution is identifying the structural data limitation.

---

## Window + sample

| Field | Value |
|---|---|
| Collection window | 2026-04-11T05:27Z → 2026-05-11T05:27Z (30d) |
| Total joined+resolved rows | **12,812** |
| Resolution rate | 100% (query filters `actual_max_mult IS NOT NULL`) |
| Min per-quartile n | 3,167 (Q2) — far above the n≥50 floor |
| Freshness factor at age=60 | 0.909 (constant across all rows in lane-60s) |
| Effective stratification var | `feature_smart_money × actual_max_mult` |
| Composite quartile boundaries | Q1≤0.909, Q2≤9.692, Q3≤20.425, Q4>20.425 |

---

## Stratified results

| Quartile | n | grad_rate | 95% CI (grad) | 2x_rate | 95% CI (2x) | peak_p50 | peak_p90 | rug_rate |
|---|---:|---:|---|---:|---|---:|---:|---:|
| Q1 (lowest) | 3,240 | 0.7% | [0.5%, 1.1%] | **40.3%** | [38.7%, 42.0%] | 1.21× | 6.42× | 3.0% |
| Q2 | 3,167 | 0.9% | [0.6%, 1.3%] | **19.4%** | [18.1%, 20.8%] | 1.00× | 2.46× | 1.0% |
| Q3 | 3,203 | 1.2% | [0.8%, 1.6%] | 76.6% | [75.1%, 78.0%] | 2.31× | 3.33× | 2.2% |
| Q4 (highest) | 3,202 | **14.6%** | [13.5%, 15.9%] | **100.0%** | [99.9%, 100.0%] | 4.41× | 11.47× | 5.3% |

---

## Comparative lift vs Q1

| Quartile | lift_grad | lift_2x |
|---|---:|---:|
| Q2 | 1.24× | 0.48× |
| Q3 | 1.56× | 1.90× |
| Q4 | **19.77×** | 2.48× |

---

## Monotonicity test (CI-aware per Audit 09 design note)

| Metric | Sequence (Q1→Q2→Q3→Q4) | Strict-monotonic | CI-aware monotonic |
|---|---|---:|---:|
| `graduation_rate` | 0.7% → 0.9% → 1.2% → 14.6% | ✅ | ✅ |
| `2x_runner_rate` | **40.3% → 19.4%** → 76.6% → 100.0% | ❌ | ❌ |

The grad_rate trend is clean. The **2x_runner_rate Q1→Q2 inversion (40.3% → 19.4%)** is the smoking gun: Q1 is more likely to have 2x-runners than Q2, which is structurally impossible for an honest formula — but is the EXPECTED artifact of `actual_max_mult` leakage in the composite, as the pre-reg's Limitations section warned.

---

## Verdict (per pre-registered criteria)

Walking each branch's frozen conditions:

### Branch VALIDATED
- ✅ n ≥ 50 per quartile
- ✅ Resolution rate 100%
- ❌ CI-aware monotonic on BOTH grad_rate AND 2x_rate (2x fails Q1→Q2)
- ✅ lift_grad(Q4) ≥ 2.0× over Q1 (19.77×!), non-overlapping CIs

Fails on condition 3.

### Branch PARTIAL
- ✅ Sample size sufficient
- ✅ CI-aware monotonic on grad_rate
- ❌ lift_grad(Q4) in [1.3, 2.0) — actual lift is **19.77×, far above 2.0**
- ❌ Lift reaches 2× only on `2x_runner_rate` — 2x at Q4 is 2.48× but trend is non-monotonic

Fails — the lift is MUCH larger than the PARTIAL range can accommodate. PARTIAL was designed for "some signal, lift below threshold" — this is "huge signal, but non-monotonic via leakage."

### Branch NOT VALIDATED
- ❌ Flat trend (lift within ±20%) — NO, 19.77×
- ❌ Inverse trend on grad_rate — NO, clearly positive
- ❌ CIs overlap across ALL boundaries — NO, Q1 and Q4 strongly non-overlapping

Does not fit either.

### Branch INCONCLUSIVE
- ❌ n < 50 anywhere — NO, min is 3,167

Does not fit.

---

## Verdict assignment: METHODOLOGY-EXPOSES-LEAKAGE

None of the pre-registered branches fit. The data exhibits a pattern the criteria didn't anticipate: **massive grad-rate lift coexists with non-monotonic 2x_rate**, and both are explained by the leakage warning in the pre-reg's Limitations section.

Specifically, the composite formula `smart_money × actual_max_mult × 0.909` is dominated by `actual_max_mult` (which spans 1× to 100+× with high variance) rather than by `smart_money` (which spans 0–9 with low variance). Quartile stratification therefore primarily separates by `actual_max_mult`, which is structurally correlated with outcome. The lift on grad_rate is real but **attributable to outcome-information-in-the-input**, not to the formula's structural form.

The 2x_runner_rate inversion at Q1→Q2 confirms this: Q1 catches (high-sm × low-mult) mints whose mints had attention but didn't pump — yet some still hit 2× (because high-sm correlates with attention, which sometimes manifests as a small pump). Q2 catches mostly (low-sm × low-mult) mints with neither attention nor pump — fewer 2× outcomes. Q3 and Q4 sweep up mints with progressively larger max_mult, mechanically hitting 2× by definition.

**This is the structural data limitation the pre-reg's Limitations section anticipated. The retroactive audit cannot cleanly test the composite-formula-as-forward-predictor with the available data shape.**

---

## What the audit DOES tell us (substantive findings)

### 1. The composite formula HAS separation power — but only when retroactively informed by outcomes

A 19.77× lift on grad_rate is not noise. It is real separation. But the separation is partly explained by `actual_max_mult` being on both sides of the equation. The audit cannot quantify how much of the 19.77× lift is structural (the formula's interaction term adds signal beyond smart_money alone) vs leakage (max_mult is correlated with grad_rate by definition).

### 2. The formula is dominated by max_mult, not smart_money

`smart_money_in` ranges 0–9; `actual_max_mult` ranges 1–100+. Their product is dominated by the latter. Quartile stratification mostly captures max_mult quartiles. The composite's "interaction structure" doesn't add as much information as the unequal dynamic ranges suggest.

### 3. The 2x_rate inversion is forensically useful

The Q1→Q2 inversion (40.3% → 19.4%) is a falsifiable prediction of the leakage hypothesis: if composite were a forward predictor, 2x_rate should monotonically increase. Observing the inversion is evidence that the formula's separation is leakage-driven, not forward-predictive. This is itself a finding worth shipping in the receipts trail.

### 4. Q4 is suspicious by construction

Q4 grad_rate = 14.6% with 2x_rate = 100.0% means **every Q4 mint had actual_max_mult ≥ 2.0** — almost by quartile definition since composite includes max_mult. The 14.6% graduation rate is the only Q4 signal that isn't tautological; the 100% 2x rate is structurally determined.

---

## Comparison to Audit 09

Audit 09 (smart_money_in stratification alone, no `actual_max_mult` in the predictor) found 7.37× lift on grad_rate at sm≥7 stratum — **based on forward-safe snapshot data only**. That lift is forward-predictive.

Audit 12-A (composite including `actual_max_mult` in the predictor) finds 19.77× lift — **partly forward-predictive, partly leakage**. We cannot separate the two retroactively.

Bound: the forward-predictive component of the composite is somewhere between Audit 09's 7.37× (smart_money alone, no leakage) and Audit 12-A's 19.77× (with leakage). The 19.77×–7.37× = 12.4× of additional lift is the upper bound on what the multiplicative composite interaction can add over smart_money alone, including the leakage.

A **forward Audit 12** with snapshot max_mult at predicted_at would resolve the question. The retroactive audit cannot.

---

## Implication for Audit 12 forward collection

The pre-reg's NOT VALIDATED branch action said: "Defer Audit 12 forward collection until decomposition is understood — running a 14d forward collection on a formula whose structure doesn't work retroactively is a low-value investment."

**The retroactive audit does NOT support that deferral.** The composite formula's structural separation is unclear, not absent. The forward collection is the way to answer the question. Methodology recommendation:

1. **Proceed with Audit 12 forward collection as originally scheduled** (post-Case-Study-01 verdict on Tuesday). Audit 12-A's verdict is "we couldn't answer this retroactively," not "the formula doesn't work."
2. **Pre-register Audit 12-B (decomposition)** as a parallel investigation: which composite terms contribute? If smart_money alone explains most of the lift, the multiplicative composite is redundant. If max_mult-at-predicted-at adds material lift beyond smart_money alone, the composite is the right surface.
3. **Update Audit 12's pre-reg** to incorporate the leakage discipline observed here — explicit forward-snapshot-only computation, no use of post-outcome fields in the predictor.

---

## Methodology design note (forward-looking, not a post-hoc amendment)

**For audits where the predictor includes outcome-correlated inputs**, retroactive analysis is structurally limited. Future pre-regs should either:
- Use forward-only inputs (snapshot-at-predicted-at fields), OR
- Use post-outcome inputs but explicitly frame as "post-hoc structural attribution" not "forward prediction," OR
- Pre-register decomposition tests that isolate the forward-safe vs leakage-correlated contributions

This is acknowledged as a design note for future audits. **NOT a post-hoc amendment of this audit's verdict.**

Filed for the audit-program design review alongside Audit 09's CI-aware-monotonicity recommendation.

---

## What ships next

- This commit (results + leakage interpretation)
- (Per user direction) Audit 12 forward collection proceeds as planned post-Case-Study-01 verdict Tuesday
- Pre-register Audit 12-B (composite decomposition test) before or alongside Audit 12 forward
- Audit-program design review: leakage discipline note for predictors with outcome-correlated inputs

---

## Receipts trail

| Commit | Action |
|---|---|
| `ca4de62` Audit 12-A pre-registration — composite retroactive validation | Methodology + frozen criteria + limitations including leakage warning |
| **(this commit) Audit 12-A results — METHODOLOGY-EXPOSES-LEAKAGE verdict** | Stratified table + Wilson CIs + CI-aware monotonicity + leakage interpretation + forward-audit recommendation |
| (next) Audit 12-B pre-registration — composite decomposition | Isolate smart_money vs max_mult vs interaction contributions |
| (next-next) Audit 12 forward-collection start | Tuesday post-Case-Study-01 verdict |

---

## Wallet redaction compatibility

This audit uses only aggregate `feature_smart_money` counts and outcome fields. **No wallet addresses are referenced, enumerated, or surfaced.** Fully compatible with the wallet redaction deployed at `06480be`.

---

## Cross-references

- [`audit_12a_composite_retroactive_validation_prereg.md`](audit_12a_composite_retroactive_validation_prereg.md) — pre-registration with leakage warning
- [`audit_09_smart_money_lift_results.md`](audit_09_smart_money_lift_results.md) — smart_money alone lift (7.37×, forward-safe predictor)
- [`audit_12_hot_launch_composite_validation_prereg.md`](audit_12_hot_launch_composite_validation_prereg.md) — forward audit (the appropriate test)
- [`audit_12_amendment_01_coverage_gap_dual_measurement.md`](audit_12_amendment_01_coverage_gap_dual_measurement.md) — dual-axis measurement for forward audit
- [`wallet_redaction_2026_05_11.md`](wallet_redaction_2026_05_11.md) — parallel deploy
- Memory: `feedback_pre_registration_branches.md` — discipline for "doesn't fit any branch" surface (same pattern as Audit 09 PARTIAL by exclusion)
- Memory: `feedback_methodology_calls_user_owned.md` — forward-audit decision is user-owned
