# Audit 09 — Smart-money lift validation (pre-registration)

**Pre-registration commit.** Methodology and frozen acceptance criteria committed BEFORE the stratified analysis query runs. Per the receipts pattern: this commit must predate the results commit and the user's downstream redaction decision. If a frozen criterion needs revision after this commit, the revision must (1) be publicly committed before the results commit lands, (2) refine/split rather than relax, and (3) explicitly surface the design flaw being corrected.

**Series:** Audit 09 of the measurement program. Promoted to **top priority** by user direction (2026-05-10) as the gating question for: (i) whether to redact wallet data publicly per `project_wallet_index_is_the_moat.md`, (ii) whether the dual-track composite hypothesis holds (`project_dual_track_signal_strategy.md`), (iii) whether wallet_intel infrastructure deserves continued investment, (iv) whether Audit 12 should be framed as composite-vs-formula or composite-vs-smart_money_in-alone.

This audit ships before any wallet redaction decision so the redaction is grounded in evidence, not hypothesis.

---

## Strategic context

Memory `project_wallet_index_is_the_moat.md` (committed 2026-05-10) captures the user's hypothesis: the 200k+ wallet reputation index built by `wallet_intel.py` is the load-bearing asset of the product. Composite signal works empirically because it surfaces "what these specific wallets are buying right now." Implication: wallet addresses should be redacted from public surfaces because they ARE the moat.

Before any redaction lands, this audit empirically validates whether `smart_money_in` — the feature derived directly from that wallet reputation index — predicts mint outcomes independently of other features. If yes → redaction is justified. If no → composite is working for non-wallet reasons; redaction would remove user-facing data without protecting any actual moat.

The discipline principle (per `feedback_postmortem_survivorship_bias.md` and the broader publish-then-post pattern): **verify the moat exists before defending it.**

---

## Hypothesis (frozen)

**H1 (primary):** `feature_smart_money` (count of wallets from the reputation index transacting on a mint in its first 60s) predicts mint outcomes (graduation, 2× peak multiplier) independently and monotonically. Specifically: graduation rate increases monotonically across strata `{0, 1–3, 4–6, 7+}`, with `grad_rate(smart_money ≥ 4)` at least 2× `grad_rate(smart_money = 0)`.

**H0 (null):** No monotonic relationship OR `grad_rate(smart_money ≥ 4)` lift < 2× over the smart_money = 0 control stratum.

The 2× threshold is pre-registered to match the discipline of Case Study 01 (≥10pp precision lift) and Audit 12 (≥10pp precision lift + ≥20% coverage). It is a meaningful lift, not a noise band.

---

## Methodology (frozen)

### Data source

Production `/data/data.sqlite` on graduate-oracle.fly.dev. Retroactive analysis on existing tables — no forward instrumentation needed:

- `predictions.actual_graduated` — boolean, outcome resolution per mint
- `predictions.actual_max_mult` — peak multiplier observed within outcome window
- `predictions.predicted_at` — UNIX timestamp of prediction event
- `mint_checkpoints.feature_smart_money` — wallet-index count at checkpoint age (joined at `checkpoint_age_s=60` for lane-60s consistency)
- `mint_checkpoints.actual_rugged` — boolean rug flag
- `mint_checkpoints.max_drop_pct_5min` — for rug-rate sub-metric

Verified pre-pre-reg (column-presence query, 2026-05-10): all relevant columns exist; 7d sample yields 8,966 mints with full join + resolved outcome. 30d window will yield more.

### Collection window

Last **30 days** of predictions where `predicted_at >= (now - 30*86400)`. If 30d yields insufficient resolved outcomes (`actual_max_mult IS NULL` for too many rows), fall back to last available window with ≥80% resolution. Report actual window + n in results.

### Filtering

```sql
SELECT p.mint, p.predicted_at, p.actual_graduated, p.actual_max_mult,
       mc.feature_smart_money, mc.feature_n_whales,
       mc.actual_rugged, mc.max_drop_pct_5min
  FROM predictions p
  JOIN mint_checkpoints mc
    ON mc.mint = p.mint
   AND mc.checkpoint_age_s = 60
 WHERE p.predicted_at >= [WINDOW_START]
   AND mc.feature_smart_money IS NOT NULL
   AND p.actual_max_mult IS NOT NULL   -- resolved outcomes only
```

Dedupe per mint (multiple predictions per mint exist; take the first lane-60s prediction per mint).

### Stratification

`feature_smart_money` bucketed into 4 strata:

| Stratum | Range |
|---|---|
| Control | `feature_smart_money = 0` |
| Low | `feature_smart_money IN (1, 2, 3)` |
| Mid | `feature_smart_money IN (4, 5, 6)` |
| High | `feature_smart_money >= 7` |

### Per-stratum metrics

For each stratum:

1. **n** — count of mints
2. **graduation_rate** = `count(actual_graduated = 1) / n`
3. **2x_runner_rate** = `count(actual_max_mult >= 2.0) / n`
4. **peak_mult_p50** — median of `actual_max_mult`
5. **peak_mult_p90** — 90th percentile of `actual_max_mult`
6. **rug_rate** = `count(actual_rugged = 1 OR max_drop_pct_5min >= 70) / n` (devil-candle definition: rug flag in mint_checkpoints OR ≥70% drop in first 5min)
7. **95% binomial confidence interval** on graduation_rate and 2x_runner_rate (Wilson interval for robustness at small per-stratum n)

### Comparative lift

Computed against control stratum:

- `lift_grad(stratum) = grad_rate(stratum) / grad_rate(control)`
- `lift_2x(stratum) = 2x_runner_rate(stratum) / 2x_runner_rate(control)`

### Monotonicity test

Trend across {Control → Low → Mid → High} for both `graduation_rate` and `2x_runner_rate`. A trend is **monotonic** if each subsequent stratum has rate ≥ previous stratum's rate (allowing equal values; strict equality not required).

---

## Acceptance criteria (frozen)

Three pre-registered outcome branches:

### Branch VALIDATED — Wallet index carries signal

**Conditions ALL of:**
1. Sample size: n ≥ 50 per stratum (n ≥ 200 total)
2. Resolution rate: ≥80% of joined predictions have resolved `actual_max_mult`
3. Monotonic trend across strata for `graduation_rate` AND `2x_runner_rate`
4. `lift_grad(High)` ≥ 2.0× over Control AND non-overlapping 95% CIs at the High vs Control comparison

**Action (per user direction):** User can confidently make the wallet-redaction call. Wallet data IS the moat empirically; redacting the addresses protects the moat.

### Branch PARTIAL — Some signal, lift below threshold

**Conditions ALL of:**
1. Sample size sufficient (per Branch VALIDATED)
2. Monotonic trend across strata for `graduation_rate`
3. `lift_grad(High)` ≥ 1.3× but < 2.0× over Control, OR
4. Lift trend reaches 2× only on `2x_runner_rate` and not on `graduation_rate`

**Action:** Narrower redaction discussion — user-level call. Possible refinements: redact only top-tier wallets (the ones contributing to the High stratum), or redact `smart_money_examples` per-mint outputs while keeping aggregate counts public. **Discuss with user before deploy; the decision is methodology-shaped (which redaction layer protects which signal layer) and is user-owned per `feedback_methodology_calls_user_owned.md`.**

### Branch NOT VALIDATED — No signal / flat / inverse

**Conditions ANY of:**
1. Trend is flat — `lift_grad(High)` within ±20% of Control (no meaningful lift)
2. Trend is inverse — `grad_rate` DECREASES across strata
3. Confidence intervals overlap across all strata (no statistical separation)

**Action:** No redaction needed. The composite signal works for non-wallet-attribution reasons (possibly `n_whales`, `max_mult` proxy, freshness, or some interaction). Wallet index is NOT the load-bearing moat. **This is the empirically-most-surprising outcome** and would substantially redirect product positioning. Pre-register a follow-up audit (Audit 09b) decomposing the composite into per-feature contributions before any product-spec changes.

### Branch INCONCLUSIVE — Insufficient sample or unresolved outcomes

**Conditions:**
- n < 50 in any stratum at the 30d cutoff
- OR resolution rate < 80%

**Action:** Extend the window to 60d. One extension only. If 60d still produces n < 50 in any stratum, the audit is officially inconclusive and the **methodology design itself becomes the finding** — "smart_money_in distribution at our prediction lane doesn't produce enough per-stratum mass for a stratified analysis in 60d." That's a real finding about feature distribution; surfaces follow-up Audit 09c on feature-distribution shape.

---

## Pre-drafted writeup

The results writeup `audit_09_smart_money_lift_results.md` ships as a separate commit AFTER this pre-reg commit, with the stratification table + CI bounds + verdict + redaction-decision context filled in. Per the publish-then-post discipline, the pre-reg commit predates the results commit, and the results commit predates the user's redaction decision.

---

## Schedule

| Phase | Time | Action |
|---|---|---|
| Pre-reg commit | This commit | Methodology + frozen criteria committed before query runs |
| Results commit | Within ~60 min | Stratified analysis run; numbers filled in; verdict |
| Redaction decision | After results commit | User makes call grounded in evidence |

---

## Receipts trail

| Commit | Action |
|---|---|
| **(this commit) Audit 09 pre-registration — smart-money lift validation** | Methodology, hypothesis, frozen acceptance criteria, branch decision tree, pre-redaction discipline note |
| (next commit) Audit 09 results | Stratified table + CIs + verdict + redaction-call context |
| (downstream, user-owned) Wallet redaction deploy (or no-redaction commit) | Grounded in audit verdict, not hypothesis |

---

## Cross-references

- Memory: `project_wallet_index_is_the_moat.md` — the strategic hypothesis this audit validates
- Memory: `feedback_postmortem_survivorship_bias.md` — discipline reason for verifying before defending
- Memory: `feedback_methodology_calls_user_owned.md` — redaction decision is user-owned; implementer's role is to produce evidence
- Memory: `feedback_pre_registration_branches.md` — pre-reg discipline rules
- [`case_study_01_gmgn_comparison_prereg.md`](case_study_01_gmgn_comparison_prereg.md) — sister audit, same publish-then-post pattern
- [`audit_12_hot_launch_composite_validation_prereg.md`](audit_12_hot_launch_composite_validation_prereg.md) — composite signal validation; this audit answers a prerequisite question (is smart_money the load-bearing piece of the composite?)
