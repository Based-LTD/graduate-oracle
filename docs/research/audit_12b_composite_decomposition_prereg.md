# Audit 12-B — Composite decomposition (pre-registration)

**Pre-registration commit.** Methodology and frozen acceptance criteria committed BEFORE the decomposition analysis runs. Per the receipts pattern: this commit predates the results commit and any downstream composite-formula-revision decision. Same publish-then-post discipline as Audit 09 (`4992703` → `34ce847`), Audit 12-A (`ca4de62` → `66e1138`), and Case Study 01 amendments.

**Series:** Audit 12-B is the natural follow-up to Audit 12-A (METHODOLOGY-EXPOSES-LEAKAGE verdict, commit `66e1138`). 12-A could not cleanly attribute the 19.77× composite-quartile graduation lift to the formula's structural form vs `actual_max_mult` leakage. **12-B decomposes the composite into its three terms and tests each independently to identify the load-bearing component.**

The audit splits into two phases: retroactive sub-analyses (smart_money + freshness, both forward-safe at predicted_at) and a forward arm (max_mult-at-predicted_at, which retroactive cannot test). The retroactive phase ships within hours; the forward phase runs alongside the Case Study 01 re-arm (per [Amendment 02](case_study_01_amendment_02_composite_vs_gmgn_re_arm.md)) so the 14-day forward window has zero additional time cost.

---

## Strategic context

User direction (2026-05-11): pre-register Audit 12-B alongside Audit 12 forward, so the decomposition runs in parallel with (not gated on) the forward composite-vs-GMGN comparison. Two reasons:

1. **Avoid running blind on the forward formula.** If smart_money_in carries 80%+ of the composite's separation power, the multiplicative composite is mostly redundant — and the right product simplification is "alert on smart_money_in ≥ N + MC ≥ $5k" without the mult/freshness terms. That decision needs the decomposition.
2. **Make the moat narrower and sharper.** A simpler product spec (smart_money alone + MC floor) is more defensible, easier to communicate to B2B integrators, and harder to reproduce by competitors (because the wallet index is the proprietary input, not a tunable formula).

The composite formula was a heuristic at launch. The audit program treats it as an empirical claim subject to validation — and validates it term by term.

---

## Hypotheses (frozen)

The composite formula is `smart_money_in × max_mult × (1 / (1 + age_s/600))`. Three decomposition hypotheses:

### H1 — smart_money_in is the load-bearing term

**Claim:** `smart_money_in` alone explains ≥75% of the composite's separation power on graduation_rate. Audit 09 already validated this independently (7.37× grad lift at sm≥7, monotonic 2x trend, no leakage). H1 sharpens the claim into a marginal-lift framework against the other terms.

### H2 — max_mult-at-predicted_at adds independent lift beyond smart_money

**Claim:** Conditional on smart_money stratum, mints with higher `max_mult_at_predicted_at` (the snapshot value at the lane-60s prediction commit, NOT lifecycle peak) graduate at materially higher rates. Marginal lift ≥ 1.3× at the top max_mult-at-60s quintile vs the bottom, holding smart_money_in fixed.

### H3 — freshness factor adds independent lift beyond H1 + H2

**Claim:** Conditional on (smart_money, max_mult-at-60s) strata, age-at-predicted_at differences within the lane-60s window (15–75s) produce measurable differences in outcome. The freshness factor at lane-60s is constrained (`f(15s)=0.976`, `f(60s)=0.909`, `f(75s)=0.889` — narrow range), so H3 expects a small but non-zero effect.

The null hypotheses are the obvious complements: H1' = smart_money explains <75% of separation; H2' = max_mult adds no marginal lift; H3' = freshness adds no marginal lift.

---

## Methodology (frozen)

### Phase 1 — Retroactive sub-analyses (smart_money + freshness)

**Both forward-safe** — `feature_smart_money` is snapshot at age=60 in `mint_checkpoints`; `age_bucket` (== checkpoint_age_s) is the prediction's commit age. No leakage from outcomes.

#### Phase 1a — smart_money_in standalone (already validated by Audit 09)

Audit 09 already ran this stratification (4 strata: `{0, 1–3, 4–6, 7+}`) on `n=12,773` mints over 30d. Result: 7.37× grad lift at sm≥7, CI-aware monotonic on 2x_rate, strict-monotonic on grad_rate at coarse strata. **Audit 12-B Phase 1a inherits the Audit 09 dataset and verdict directly** — no fresh query needed; the result IS the H1 evidence baseline.

#### Phase 1b — freshness factor stratification (new query)

For each mint in the same 30d window used by Audit 09, stratify by `age_bucket` (the prediction's commit age) at lane-60s: `{15s, 30s, 60s, 75s}` (these are the discrete `checkpoint_age_s` values mint_checkpoints captures).

For each age-stratum:
- n
- graduation_rate
- 2x_runner_rate
- peak_mult_p50
- 95% Wilson binomial CIs
- Comparison: `lift_grad(age=N) / lift_grad(age=60)` (using age=60 as the lane-60s commitment reference)

**Acceptance for H3 retroactive:** If lift across age-strata is within ±10% of age=60 baseline AND CIs overlap, freshness factor is empirically irrelevant at lane-60s (the freshness formula's narrow output range matches a narrow outcome effect). If lift varies >10% with non-overlapping CIs, freshness has measurable signal.

### Phase 2 — Forward arm (max_mult-at-predicted_at)

**Cannot be done retroactively** — `actual_max_mult` is lifecycle peak, NOT the value at predicted_at. The Case Study 01 harness (re-armed per [Amendment 02](case_study_01_amendment_02_composite_vs_gmgn_re_arm.md)) MUST capture `max_mult_at_predicted_at` as a snapshot field for Audit 12-B Phase 2 to function.

#### Phase 2 instrumentation requirement

Add a new field to the case_study_harness's grad_oracle source adapter: **`max_mult_at_predicted_at`**. This is the running-max multiplier observed in the curve data from mint birth up to `predicted_at`. The Rust observer-daemon writes curve snapshots to disk; the harness reads them to compute the running max at the prediction timestamp. This is mechanical extraction, not new computation — the data exists; it just isn't currently surfaced into the sqlite tables.

If the observer's curve data is insufficient to reconstruct max_mult_at_60s (e.g., snapshots are too sparse), Phase 2 falls back to using `current_mult` as captured in the snapshot file at the moment the score_precompute pipeline writes the prediction (which IS the max-up-to-predicted_at value the dashboard displays at lane-60s). This is the cleanest forward-safe substitute.

Pre-registered fallback: `max_mult_at_predicted_at = current_mult` (snapshot value at lane-60s commit). If this proves insufficient for the Phase 2 question, Audit 12-B Phase 2 publishes a methodology-as-finding writeup; no further extraction attempts are pre-authorized (iteration-limit).

#### Phase 2 stratification

For each mint in the 14d Case Study 01 re-armed forward window:

- Conditional on smart_money stratum `{0, 1–3, 4–6, 7+}` (per Audit 09's frozen strata)
- Sub-stratify by `max_mult_at_predicted_at` quintile within each smart_money stratum
- Compute marginal lift: top max_mult quintile vs bottom quintile within fixed smart_money stratum
- Aggregate across smart_money strata: weighted average marginal lift

**Acceptance for H2:** Top max_mult quintile vs bottom, holding smart_money fixed, produces ≥1.3× lift on grad_rate, monotonic across quintiles within at least 3 of 4 smart_money strata.

### Marginal-lift attribution

After Phase 1 + Phase 2 complete, attribute composite separation power across the three terms. Use a multivariate logistic regression with `smart_money_in`, `max_mult_at_predicted_at`, and `age_at_predicted_at` as predictors of `graduated`. Report standardized coefficients + 95% CIs. The term with the largest standardized coefficient (in absolute value, p<0.05) is the load-bearing component.

---

## Acceptance criteria (frozen)

### Branch ALL-LIFTING — All three terms add independent signal

**Conditions ALL of:**
1. Phase 1a (smart_money) validates per Audit 09 (already true: 7.37× lift)
2. Phase 1b (freshness) shows >10% lift variation across age-strata with non-overlapping CIs
3. Phase 2 (max_mult-at-60s) shows ≥1.3× marginal lift in 3+ smart_money strata

**Action:** Composite formula's full structure (all three terms) is empirically validated. No product simplification justified. Continue with composite as headline signal in dual-track strategy. Audit 12 forward proceeds with full composite filter.

### Branch SMART-MONEY-DOMINATES — smart_money is the load-bearing term; others are noise

**Conditions ALL of:**
1. Phase 1a validates smart_money (already true)
2. Phase 1b shows freshness lift within ±10% (no signal)
3. Phase 2 shows max_mult-at-60s marginal lift < 1.3× in 3+ smart_money strata

**Action:** **Recommend product simplification.** Replace composite filter with `smart_money_in ≥ N + MC ≥ $5k`. Sharper, more defensible product spec; easier B2B integration story ("we surface mints with N+ tracked-trader wallets"). Pre-register Audit 12-C: simpler-filter validation against composite + GMGN.

### Branch PARTIAL-LIFTING — smart_money + one other; third is noise

**Conditions:**
- Phase 1a validates smart_money (always true given Audit 09)
- Exactly ONE of (Phase 1b freshness, Phase 2 max_mult) shows acceptance-grade lift; the other does not

**Action:** Product simplification to the two-term composite. Drop the term that didn't validate. Pre-register the simplified filter for forward validation.

### Branch INCONCLUSIVE

- Phase 2 forward collection produces n < 100 with non-zero `max_mult_at_predicted_at` data in the 14d window
- OR the multivariate regression has unstable coefficients (singular covariance matrix, model fit issues)

**Action:** Per Case Study 01 Subcondition C-iv shape — re-arm with extended collection (one extension only). If still inconclusive, methodology-as-finding writeup.

---

## Limitations (frozen, surfaced for verdict context)

### `max_mult_at_predicted_at` reconstruction risk

Phase 2 depends on capturing `max_mult_at_predicted_at` (snapshot, not lifecycle peak) at prediction commit time. If the observer/curves pipeline doesn't preserve sufficient granularity, the fallback (`current_mult` at snapshot time) is the next-best forward-safe substitute. Pre-registered iteration-limit: no further extraction attempts past this fallback.

### freshness factor's narrow output range

At lane-60s, the freshness factor ranges 0.889–0.976 (15s–75s commit ages). The Phase 1b stratification may not have enough dynamic range to produce detectable signal even if the freshness factor IS causally meaningful. Pre-registered interpretive note: a Phase 1b null result is consistent with EITHER "freshness has no effect" OR "freshness has effect smaller than this audit's power can detect."

### Audit 09's PARTIAL verdict carries forward

Audit 09's verdict was PARTIAL (strict-monotonicity failure within overlapping CIs at the top stratum). Phase 1a Audit 12-B inherits this nuance. The H1 claim "smart_money explains ≥75% of composite separation" is robust to the Audit 09 nuance; the strict-monotonicity verdict is a methodology design note, not a substantive caveat.

---

## Schedule

| Phase | Time | Action |
|---|---|---|
| Pre-reg commit | This commit (2026-05-11) | Methodology + frozen criteria + limitations |
| Phase 1a verdict | Already in (`34ce847`) | Audit 09 results inherited |
| Phase 1b retroactive query | Within ~30 min of this commit | Freshness stratification on Audit 09's dataset |
| Phase 1b results commit | Within ~45 min of pre-reg | Freshness verdict |
| Phase 2 instrumentation | Alongside Case Study 01 re-arm (post-2026-05-11T06:55Z) | Add `max_mult_at_predicted_at` to harness adapter |
| Phase 2 collection | 14d after re-arm (2026-05-25 ~07:00Z verdict) | Forward window |
| Phase 2 verdict | After 14d collection + multivariate regression | Marginal-lift attribution complete |
| Final verdict + decomposition writeup | Same commit as Phase 2 verdict | Branch decision + product-simplification implications |

---

## Compatibility with parallel work

**Wallet redaction:** Audit 12-B uses only aggregate `feature_smart_money` count, `actual_graduated` boolean, `actual_max_mult` peak, `age_bucket` integer. **No wallet addresses referenced.** Fully compatible with the deployed Option A + Option 5 redactions.

**Case Study 01 re-arm:** Phase 2 instrumentation rides alongside the re-armed harness (per Amendment 02). The harness already captures `current_mult` at prediction commit; the new `max_mult_at_predicted_at` field is mechanical. 14d window shared; no additional collection time.

**Audit 12 forward (the original 4-branch coverage-gap audit):** Audit 12 tests composite + MC floor vs GMGN strict-preset on a 14d forward window. Audit 12-B tests composite decomposition on the SAME 14d window. The two audits answer different questions on shared data; the case_study_harness writes one set of observations that both audits read.

---

## Receipts trail

| Commit | Action |
|---|---|
| `4992703` Audit 09 pre-registration | Methodology for smart_money stratification |
| `34ce847` Audit 09 results | Phase 1a evidence for H1 (inherited here) |
| `ca4de62` Audit 12-A pre-registration | Retroactive composite test methodology |
| `66e1138` Audit 12-A results — METHODOLOGY-EXPOSES-LEAKAGE | Motivates this decomposition audit |
| `fce6ce3` Case Study 01 Amendment 02 — composite-vs-GMGN re-arm | Phase 2 collection lives in the re-armed harness |
| **(this commit) Audit 12-B pre-registration — composite decomposition** | Methodology + frozen criteria + Phase 1+2 split + product-simplification action tree |
| (next, within ~45 min) Phase 1b freshness retroactive results | Inline append to results doc |
| (later, +14d) Phase 2 forward results + final verdict | Multivariate regression + branch decision |

---

## Cross-references

- [`audit_09_smart_money_lift_prereg.md`](audit_09_smart_money_lift_prereg.md) — Phase 1a precedent
- [`audit_09_smart_money_lift_results.md`](audit_09_smart_money_lift_results.md) — Phase 1a evidence base
- [`audit_12a_composite_retroactive_validation_prereg.md`](audit_12a_composite_retroactive_validation_prereg.md) — Audit 12-A pre-reg (motivates this follow-up)
- [`audit_12a_composite_retroactive_validation_results.md`](audit_12a_composite_retroactive_validation_results.md) — METHODOLOGY-EXPOSES-LEAKAGE verdict
- [`audit_12_hot_launch_composite_validation_prereg.md`](audit_12_hot_launch_composite_validation_prereg.md) — sister forward audit (different question)
- [`case_study_01_amendment_02_composite_vs_gmgn_re_arm.md`](case_study_01_amendment_02_composite_vs_gmgn_re_arm.md) — Phase 2 instrumentation home
- Memory: `project_dual_track_signal_strategy.md` — strategic framing
- Memory: `feedback_pre_registration_branches.md` — discipline
- Memory: `feedback_methodology_calls_user_owned.md` — product-simplification decision will be user-owned at verdict time
