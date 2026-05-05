# Lane 2 — GBM stratified by bundle_detected

**Run date:** 2026-05-05 afternoon
**Pre-registration:** [BACKLOG.md → "Lane 2 — GBM on full joinable set"](../../BACKLOG.md)
**Decision thresholds (frozen pre-run):** GBM beats k-NN by ≥3% AUC → architecture replacement candidate. Stratified top-5 overlap ≤2 → different signal mechanisms. ≥4 → same signal, richer features needed.

---

## TL;DR

Two trainings, one finding:

- **Run A (full features incl. post-grad snapshot columns):** GBM beats k-NN by **+12.88pp AUC overall**, **+12.56pp on non-bundled**. But the GBM is leaning heavily on post-grad features (`pg_unique_buyers`, `pg_vsol_velocity`) that **are not available at age 30/60 score time** — they're captured at graduation. So this run is informative about ceiling, not about a deployable model.
- **Run B (pre-graduation features only — what a live model could actually use today):** GBM beats k-NN by **+3.56pp overall**, only **+2.58pp on non-bundled**. Below the retrain-scoping ship-replace bar (≥10pp on non-bundled).

**Stratified ranking overlap = 4** in both runs → "same signal mechanism, richer features needed" — not "different architectures."

**Decision:** the architecture (k-NN vs GBM) is NOT the lever. Better features ARE. Specifically, Lane 6's 17 candidate features need to enter the training set via curve replay — that's the next concrete work, before any retrain ships.

---

## Setup

- **Sample:** 4,849 rows (one per `(mint × age_bucket)` pair, where both 30 and 60 buckets exist for ~80% of mints). Rows have `manufactured_pump` and `bundle_detected` populated.
- **Bundled split:** 574 bundled (11.8%) / 4,275 non-bundled (88.2%) — consistent with Lane 1's 13.4% bundled finding.
- **Label balance:** 50.7% sustained_30m. Roughly balanced — no severe class imbalance issue.
- **Train/test:** 80/20 stratified split on (bundled × label) → 4 strata, both axes represented in each.
- **k-NN reference:** the existing model's `predicted_prob` (graduation prob) is asymmetrically used here — we're asking whether it predicts the SUSTAIN outcome. Not a fair comparison in absolute terms, but it's the comparison the retrain decision actually needs ("does the existing prediction signal contain sustain information, or does sustain need its own model?").
- **GBM:** `sklearn.HistGradientBoostingClassifier` (LightGBM unavailable on host — `libomp` missing — sklearn's HistGB is the same algorithm family, comparable for this proof-of-concept).
- **Sanity check:** L1 logistic regression on the same features. Should agree with GBM on signal direction; disagreement = non-monotone interactions.

## Run A — full feature set (incl. post-grad snapshot columns)

### Features

- Pre-grad: `age_bucket`, `entry_mult`, `was_calibrated`, `manufactured_pump`, `bundle_detected`, `dex_paid`, `fee_delegated`
- Post-grad snapshot from `post_grad_outcomes`: `pg_smart_money`, `pg_n_whales`, `pg_unique_buyers`, `pg_vsol_velocity`, `pg_fee_delegated`

### Held-out metrics

| Model | AUC | log_loss | Brier |
|---|---:|---:|---:|
| **GBM** | **0.7362** | 0.5914 | 0.2061 |
| L1-LR | 0.5896 | 0.6731 | — |
| k-NN ref | 0.6074 | 0.9305 | 0.2794 |

**GBM AUC delta over k-NN: +12.88pp.**

### Per-population AUC

| Population | GBM | k-NN | Delta |
|---|---:|---:|---:|
| Bundled (n_te ≈ 115) | 0.745 | 0.567 | +17.8pp |
| Non-bundled (n_te ≈ 855) | 0.723 | 0.598 | +12.6pp |

### Stratified top-5 feature importance (permutation)

| Rank | Full sample | Bundled subset | Non-bundled subset |
|---|---|---|---|
| 1 | pg_unique_buyers | pg_unique_buyers | pg_unique_buyers |
| 2 | pg_vsol_velocity | pg_vsol_velocity | pg_vsol_velocity |
| 3 | entry_mult | entry_mult | entry_mult |
| 4 | age_bucket | age_bucket | age_bucket |
| 5 | bundle_detected | fee_delegated | manufactured_pump |

**Top-5 overlap (bundled ∩ non-bundled) = 4.** Decision per pre-registration: "same signal mechanism, richer features needed."

### Why Run A is misleading on its own

The top 2 features (`pg_unique_buyers`, `pg_vsol_velocity`) are captured at graduation time by `post_grad_tracker`. At age 30/60 score time these specific values DON'T EXIST — the mint hasn't graduated yet. So Run A's GBM has access to information a live model would not.

The right reading: this confirms there IS exploitable signal in unique-buyer-count and vsol-velocity for sustain prediction. The score-time analogues of these features (`unique_buyers` from observer, `vsol_velocity_30s` and `vsol_velocity_60s` — both Lane 6 candidates) plausibly carry similar signal at predict time, but are not currently in the training set or the live feature vector.

## Run B — pre-graduation features only (honest live-prediction baseline)

### Features

- `age_bucket`, `entry_mult`, `was_calibrated`, `manufactured_pump`, `bundle_detected`, `dex_paid`, `fee_delegated` (7 features only)

### Held-out metrics

| Model | AUC | log_loss |
|---|---:|---:|
| **GBM** | **0.6430** | 0.6415 |
| L1-LR | 0.5802 | 0.6759 |
| k-NN ref | 0.6074 | 0.9305 |

**GBM AUC delta over k-NN: +3.56pp overall, +2.58pp on non-bundled.**

### Per-population

| Population | GBM | k-NN | Delta |
|---|---:|---:|---:|
| Bundled | 0.624 | 0.567 | +5.7pp |
| Non-bundled | 0.624 | 0.598 | +2.6pp |

### Stratified top-5

| Rank | Full | Bundled | Non-bundled |
|---|---|---|---|
| 1 | entry_mult | entry_mult | entry_mult |
| 2 | bundle_detected | fee_delegated | age_bucket |
| 3 | manufactured_pump | age_bucket | bundle_detected |
| 4 | age_bucket | was_calibrated | manufactured_pump |
| 5 | fee_delegated | manufactured_pump | fee_delegated |

**Top-5 overlap = 4.** Same conclusion as Run A: same signal, richer features needed.

## Decision per pre-registered thresholds

- **GBM beats k-NN by ≥3% AUC overall:** ✅ tripped (3.56pp). GBM is technically a candidate.
- **GBM beats k-NN by ≥10pp on non-bundled subset (retrain ship-replace bar):** ❌ failed (2.58pp). Below the bar set in retrain scoping.
- **Top-5 ranking overlap ≤ 2:** ❌ failed (overlap = 4). NOT "different signal mechanisms."
- **Top-5 ranking overlap ≥ 4:** ✅ tripped. "Same signal, richer features needed."

**Net decision:** architecture replacement is not justified by this comparison alone. Feature engineering is the lever. Retrain on the fuller feature set (Lane 6's 17 unused features, especially the score-time analogues of what Run A surfaced as important) and re-run this comparison.

## Why this bounds the answer rather than answering it

- Run A says: "with rich features, sustain has 0.74 AUC potential." That's an upper bound — given perfect features.
- Run B says: "with the schema features alone, GBM is barely above k-NN." That's a lower bound — given the worst-case poor feature set.

The truth is between. Lane 6's 17 candidate features at score time (especially `unique_buyers`, `vsol_velocity_30s`, `vsol_velocity_60s`, `top3_buyer_pct`, `repeat_buyer_rate`, `n_smart_in`) are exactly the score-time analogues of what Run A leaned on. A retrain with those features in the training set should land somewhere in the 0.65-0.74 AUC range. Where it lands within that range determines the ship-replace decision.

## Implications for retrain scoping

Update [BACKLOG.md → "Retrain scoping draft"](../../BACKLOG.md):

1. **Architecture decision: hold for now.** Don't pre-commit GBM. Run the architecture comparison again WITH the full Lane 6 feature set after curve replay.
2. **Feature engineering is the bottleneck.** The next concrete work is curve replay — extract Lane 6's 17 features for each row in the training set by replaying each mint's curve at age 30/60.
3. **Top-5 overlap = 4 means single-model is fine.** Don't pre-commit two-population specialization. The signal mechanism is the same; the lever is feature richness.
4. **Sustained_30m IS predictable.** Run A's 0.74 AUC on held-out data is meaningful upper bound. The model is not asking an unanswerable question.

## Caveats

- LightGBM unavailable; HistGradientBoostingClassifier substituted. Same algorithm family but slightly different defaults.
- 4,849 rows includes BOTH `age_bucket=30` and `age_bucket=60` for many mints. The two are not statistically independent. K-fold splits on (mint, bucket) pairs, not on mints — a mint contributing to both train and test is possible. For a strict deployment evaluation, group-k-fold by mint is needed.
- L1-LR's `penalty=l1` syntax is deprecated in sklearn 1.8 (warnings shown in run output). Doesn't affect numerical results.
- The k-NN reference is asymmetric: it predicts graduation, not sustain. So "k-NN AUC = 0.61 on sustain" is NOT a fair comparison in the strict sense. It IS the right comparison for the retrain decision: "does the current model output contain sustain signal?" — answer: a little, but not much.

## Numerical summary saved to `/tmp/lane2/summary.json` and `/tmp/lane2/summary_pregrad.json`

For the next agent / future-Claude picking this up: the dataframes, models, and metrics are reproducible from `/tmp/lane2/lane2_data.csv` (which is now also at `/data/lane2_data.csv` on Fly if needed for repeat runs). Training scripts are at `/tmp/lane2/train.py` and `/tmp/lane2/train_pregrad.py`.
