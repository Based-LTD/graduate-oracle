# Audit 12-B Phase 1b — Freshness factor retroactive results

**Verdict commit.** Per the pre-reg [`audit_12b_composite_decomposition_prereg.md`](audit_12b_composite_decomposition_prereg.md) (commit `b0f400e`). Phase 1b methodology was frozen pre-data; this commit fills in the numbers + verdict + a substantive surprise the pre-reg didn't anticipate.

**Verdict (Phase 1b only):** **H3 PASSES** — freshness factor has empirically detectable, materially large signal on `2x_runner_rate` across the prediction-age stratification. The freshness FORMULA at lane-60s (the only window the pre-reg considered) underweights age-at-prediction by ~8× relative to the empirical effect.

**Surprise finding (NOT a methodology amendment; a substantive data observation):** the predictions table contains mints first-scored at ages spanning 30s–1500s. This wider distribution is itself potentially valuable — the older-age strata may represent the **observer-ingestion-lag population** (mints we became aware of late but still scored successfully), which is a distinct product opportunity rather than data-to-filter-out.

---

## Window + sample

| Field | Value |
|---|---|
| Collection window | 2026-04-11T05:47Z → 2026-05-11T05:47Z (30d) |
| Total joined+resolved rows | **12,812** (same dedup convention as Audit 09 + 12-A: first prediction per mint) |
| Min first-prediction age | 30s |
| Max first-prediction age | 1500s (25 min) |

### First-prediction age distribution

The pre-reg framed this audit around lane-60s ages `{15, 30, 60, 75}`. The data tells a different story:

| age_bucket (s) | n | % of total | freshness factor at age |
|---:|---:|---:|---:|
| 30 | 10,322 | 80.6% | 0.9524 |
| 60 | 1,923 | 15.0% | 0.9091 |
| 120 | 135 | 1.1% | 0.8333 |
| 180 | 86 | 0.7% | 0.7692 |
| 300 | 111 | 0.9% | 0.6667 |
| 600 | 59 | 0.5% | 0.5000 |
| 900 | 47 | 0.4% | 0.4000 |
| 1500 | 129 | 1.0% | 0.2857 |

**Sum at lane-60s ages: 95.6%. Sum at older ages: 4.4% (n=567).** The older-age population is small but the absolute sample is large enough to be informative.

**Why do older-age rows exist?** Each row is the FIRST prediction per mint (dedup by min(predicted_at)). A first-prediction at age=300 means the scorer didn't see the mint until age=300. Per memory `project_observer_clean_restart.md` ("observer captures only 30% of trades via logsSubscribe"), observer-ingestion lag is a known issue — some mints aren't surfaced to the scoring pipeline until later in their lifecycle. **The older-age strata in this audit are plausibly the observer-lag-population, not lane-60s-discipline violations.**

This reframes the audit's H3 question: **does the freshness formula correctly weight age-at-prediction when the prediction happens to fire late, not just within lane-60s?** That's a more meaningful product question.

---

## Age-stratified outcomes (full distribution)

| age (s) | n | grad_rate | 95% CI | 2x_rate | 95% CI | peak_p50 | rug_rate | composite freshness wt |
|---:|---:|---:|---|---:|---|---:|---:|---:|
| 30 | 10,322 | **4.59%** | [4.20, 5.01] | **63.6%** | [62.7, 64.5] | 2.44× | 3.1% | 0.952 |
| 60 | 1,923 | 3.38% | [2.66, 4.29] | **45.3%** | [43.1, 47.6] | 1.89× | 2.8% | 0.909 |
| 120 | 135 | 4.44% | [2.05, 9.36] | 28.9% | [21.9, 37.0] | 1.13× | 0.7% | 0.833 |
| 180 | 86 | 3.49% | [1.19, 9.76] | 32.6% | [23.6, 43.0] | 1.45× | 1.2% | 0.769 |
| 300 | 111 | 2.70% | [0.92, 7.65] | 19.8% | [13.5, 28.2] | 1.10× | 0.9% | 0.667 |
| 600 | 59 | 6.78% | [2.67, 16.18] | 16.9% | [9.5, 28.5] | 1.21× | 1.7% | 0.500 |
| 900 | 47 | 2.13% | [0.38, 11.11] | 29.8% | [18.7, 44.0] | 1.16× | 0.0% | 0.400 |
| 1500 | 129 | 2.33% | [0.79, 6.61] | 18.6% | [12.8, 26.2] | 1.09× | 0.0% | 0.286 |

### Comparative lift vs age=60 baseline (the canonical lane-60s commit age)

| age (s) | lift_grad | lift_2x | abs deviation grad | abs deviation 2x |
|---:|---:|---:|---:|---:|
| 30 | 1.36× | **1.40×** | 35.9% | 40.4% |
| 60 | 1.00× | 1.00× | 0.0% | 0.0% |
| 120 | 1.31× | 0.64× | 31.5% | 36.4% |
| 180 | 1.03× | 0.72× | 3.2% | 27.6% |
| 300 | 0.80× | 0.44× | 20.0% | 56.4% |
| 600 | 2.01× | 0.37× | 100.6% | 62.7% |
| 900 | 0.63× | 0.66× | 37.1% | 34.2% |
| 1500 | 0.69× | 0.41× | 31.2% | 58.9% |

### CI-aware monotonicity check (across full age range)

- **`grad_rate` sequence:** 4.59% → 3.38% → 4.44% → 3.49% → 2.70% → 6.78% → 2.13% → 2.33%
  - Non-monotonic. CI overlap is widespread at older ages (small n inflates CI width). No CI pair is non-overlapping across the full sequence.
- **`2x_rate` sequence:** 63.6% → 45.3% → 28.9% → 32.6% → 19.8% → 16.9% → 29.8% → 18.6%
  - Roughly monotonic decline with mild non-monotonicity at age=180, 900. **12 of the 28 pairwise CI comparisons are non-overlapping** — substantial separation across ages.

---

## H3 verdict (per pre-reg Phase 1b criteria)

Pre-reg acceptance: ">10% lift variation across age-strata AND non-overlapping CIs → freshness has signal."

- Max lift variation: 100.6% on grad_rate (age=600), 62.7% on 2x_rate (age=600). **✓ above 10% threshold.**
- Non-overlapping CIs on grad_rate: none across full range (CI widening at small-n strata).
- Non-overlapping CIs on 2x_rate: 12 pairs (extensive separation). **✓**

**H3 verdict: PASSES** under the strict pre-reg criterion. Freshness factor has empirically detectable signal on `2x_runner_rate`.

---

## Substantive interpretation

### 1. Freshness signal is real, larger than the formula implies

At lane-60s (age=30 vs age=60), the **freshness formula** weights differ by 4.7% (0.952 vs 0.909). The **empirical 2x_rate** differs by 40% (63.6% vs 45.3%). The age-at-prediction signal is roughly 8× larger than the formula encodes.

Beyond lane-60s, the divergence between formula and empirical effect grows. At age=300, formula weight is 0.667 (30% lower than age=60); empirical 2x_rate is 0.198 (56% lower than age=60). The formula understates age's impact.

**Two readings, both plausible:**
1. The freshness formula's half-life of 600s is too long — a steeper decay (half-life ~200s) would better match the empirical signal
2. Freshness is a proxy for an underlying mechanism (observer-detection state, mint-lifecycle phase, attention-window) that the formula approximates loosely; the true mechanism produces larger effects than a smooth multiplicative weight implies

Either reading motivates **Audit 12-C** (composite weight calibration) as a follow-up after Phase 2 completes.

### 2. The older-age strata are potentially valuable signal, not selection-bias noise

The user's framing (2026-05-11): **the age data could be really valuable.** This audit's data supports that read:

- **Age=600s, n=59:** grad_rate = 6.78%, peak_p50 = 1.21× — graduation rate at this age is HIGHER than the lane-60s baseline (3.38%). Small sample but suggestive.
- **Age=120s, n=135:** grad_rate = 4.44% — comparable to age=30. Late-observed mints in this range still graduate at usual rates.
- **Across all older ages combined (age >75s, n=567):** grad_rate = aggregate ~3.4%, 2x_rate = aggregate ~22% — meaningful base rate, not noise.

These are mints we MISSED at lane-60s (observer ingestion lag) but successfully scored at the older age — and they have real outcomes. **This is a product opportunity:** the scorer is producing useful predictions on observer-lag mints, even though the lane-60s framing doesn't formally include them.

**Pre-reg framing reset (post-data observation, NOT a verdict amendment):** the Phase 1b H3 question isn't only "does freshness work at lane-60s?" — it's "does the freshness formula correctly value age-at-prediction across the full distribution of when we actually score mints?" The data says yes (signal is real), but not via the formula's shape (formula underweights age).

### 3. Selection bias caveat (acknowledged, not dispositive)

A possible alternative explanation for the age signal: **survivor selection at older ages.** A mint first-predicted at age=300 has already had 5 minutes to either pump or fizzle without the scorer observing it. The pool of "mints whose first prediction is at age=300" is biased toward mints that the observer didn't catch — possibly low-volume, low-attention mints. These would have lower 2x_rates by composition rather than by the freshness factor itself.

This caveat is real but **does not dispositively dismiss the age signal.** The audit cannot cleanly disentangle (a) freshness-as-formula-factor from (b) observer-selection-at-late-ages from (c) the mint-population-at-this-age has-different-fundamentals. Future audits could attempt this disentangling (e.g., compare outcomes for same mints predicted at multiple ages, when available; or compare observer-lag-mints to fast-observed-mints with similar features).

For Phase 1b's H3 verdict, the lift is real and large; the mechanism is incompletely understood; both readings (formula-weighting-wrong AND/OR selection-bias) point to the same product implication: **age-at-prediction matters, and a follow-up audit can sharpen the mechanism.**

---

## Phase 2 implications

Phase 2 (max_mult-at-predicted_at forward arm) was designed to test H2 conditional on smart_money strata. Phase 1b's surprise — that the age-at-prediction distribution is wider than the pre-reg assumed — has implications:

- The forward harness (per Case Study 01 Amendment 02) should capture `age_at_predicted_at` as a snapshot field alongside `max_mult_at_predicted_at`. The pre-reg implicitly assumed lane-60s; the forward harness should capture the actual prediction age so Phase 2's multivariate regression can include age as a continuous covariate.
- Phase 2's stratification should not assume age=60 universally. If the forward window also produces older-age first-predictions, the regression should accommodate them.

This is a forward-looking note for Phase 2 design, not an amendment to Phase 1b's verdict.

---

## What this verdict means for the broader Audit 12-B branching

Pre-reg branches:
- **ALL-LIFTING** (all 3 terms add signal): still possible if Phase 2 also passes
- **SMART-MONEY-DOMINATES** (others are noise): less likely given freshness's clean lift on 2x_rate
- **PARTIAL-LIFTING** (smart_money + one other): possible if Phase 2 fails
- **INCONCLUSIVE**: would require Phase 2 failures

**Phase 1b's H3 PASS tilts the audit toward ALL-LIFTING or PARTIAL-LIFTING (smart_money + freshness).** Phase 2 (max_mult-at-predicted_at, forward 14d) decides the final verdict.

---

## Receipts trail

| Commit | Action |
|---|---|
| `4992703` Audit 09 pre-reg | Phase 1a methodology precedent |
| `34ce847` Audit 09 results | Phase 1a evidence (inherited for H1) |
| `b0f400e` Audit 12-B pre-registration | Frozen methodology + branch criteria + Phase 1+2 split |
| **(this commit) Audit 12-B Phase 1b results — freshness retroactive** | H3 PASSES on 2x_rate; surprise: predictions table contains older-age mints (observer-lag population); formula underweights age by ~8× |
| (later, +14d) Phase 2 forward results | max_mult marginal lift; final decomposition verdict |
| (possible) Audit 12-C — composite weight calibration | If Phase 2 + this verdict combined suggest formula re-weighting |

---

## Cross-references

- [`audit_12b_composite_decomposition_prereg.md`](audit_12b_composite_decomposition_prereg.md) — pre-reg
- [`audit_09_smart_money_lift_results.md`](audit_09_smart_money_lift_results.md) — Phase 1a inherited
- [`audit_12a_composite_retroactive_validation_results.md`](audit_12a_composite_retroactive_validation_results.md) — sister retroactive audit
- [`case_study_01_amendment_02_composite_vs_gmgn_re_arm.md`](case_study_01_amendment_02_composite_vs_gmgn_re_arm.md) — Phase 2 instrumentation home
- Memory: `project_observer_clean_restart.md` — observer ingestion lag context (explains older-age first-predictions)
- Memory: `feedback_lane_60s_only.md` — original lane-60s framing; this audit shows the real prediction-age distribution is wider in practice
- Memory: `feedback_pre_registration_branches.md` — pre-data branch verdict
