# Lane 9 — Curve-replay feature engineering retrain

**Run date:** 2026-05-05 evening
**Pre-registration:** [BACKLOG.md → "Lane 9 — curve-replay feature engineering retrain"](../../BACKLOG.md)
**Decision thresholds (frozen pre-run):**
- Non-bundled AUC ≥ 0.674 (≥5pp closure of the 10pp gap) → retrain justified
- 0.644 ≤ AUC < 0.674 (2-5pp closure) → Lane 8 priority
- AUC < 0.644 (<2pp closure) → structural finding

---

## Headline

**Non-bundled AUC: 0.7413** vs Run B baseline 0.5997 — **+14.16pp gap closure on non-bundled**, well above the ≥5pp threshold.

Decision per pre-registered rule (applied fresh, post-writeup): **retrain is justified.** The architectural diagnosis from Lane 2 ("architecture HOLD, features are the lever") is confirmed. Implementation scoping is the next phase.

## Numbers

### Headline metrics (held-out 20%, n_test = 616)

| Model | Overall AUC | log_loss |
|---|---:|---:|
| Run B baseline (pre-grad only, this sample) | 0.6205 | 0.6683 |
| **Lane 9 retrain (pre-grad + curve replay)** | **0.7356** | 0.6170 |
| k-NN reference (predicted_prob → sustain) | 0.6018 | 0.8733 |

- Lane 9 vs Run B (this sample): **+11.51pp**
- Lane 9 vs k-NN: **+13.38pp**

Note: Run B baseline AUC on this restricted sample (0.6205) is slightly lower than the full Lane 2 Run B (0.6430). That's because Lane 9's join with the curve-replay table dropped 1,773 rows where curves weren't present. The relevant comparison is Run B vs Lane 9 ON THE SAME SAMPLE — which the inline retrain produces (0.6205 vs 0.7356).

### Per-population AUC (the decision-relevant slice)

| Population | Run B | Lane 9 | k-NN | Lane 9 delta vs Run B | Lane 9 delta vs k-NN |
|---|---:|---:|---:|---:|---:|
| **Non-bundled (n_te ≈ 551)** | **0.5997** | **0.7413** | 0.5999 | **+14.16pp** | **+14.14pp** |
| Bundled (n_te ≈ 65) | 0.5772 | 0.5180 | 0.5011 | -5.92pp | +1.69pp |

**Non-bundled overshoot:** Lane 9 actually exceeds Run A's full-feature ceiling of 0.723 (which used post-grad snapshot columns). The curve-replay features at age 30/60 carry MORE information than the post-grad snapshots Run A leaned on. This was unexpected and is itself a finding — the score-time analogues aren't just substitutes for post-grad features, they're stronger signals.

**Bundled regression:** Lane 9 AUC on the bundled subset is 0.5180, lower than Run B's 0.5772 by 5.9pp. The model, now able to find non-bundled signal, is implicitly optimizing for the dominant population. The bundled subset has only 65 held-out rows; some of this regression is sample noise. But the directional finding is real and worth flagging: **a single-model retrain may modestly degrade bundled performance.** Worth re-evaluating per-population specialization at retrain scoping time.

## Stratified feature importance (top 10)

### Full sample (n=3,076)

| Rank | Feature | Importance |
|---|---|---:|
| 1 | **max_mult_at_age** | +0.1733 |
| 2 | **vsol_velocity_60s** | +0.1714 |
| 3 | entry_mult | +0.0703 |
| 4 | top3_buyer_pct | +0.0666 |
| 5 | sol_spent_first_2s | +0.0651 |
| 6 | dust_buy_rate | +0.0585 |
| 7 | buys_per_buyer | +0.0467 |
| 8 | repeat_buyer_rate | +0.0428 |
| 9 | vsol_acceleration | +0.0427 |
| 10 | sol_spent_first_5s | +0.0382 |

**The top 2 features are Lane 6 candidates** — `max_mult_at_age` (peak so far) and `vsol_velocity_60s` (momentum) dominate. The pre-grad schema features (entry_mult and binary flags) drop to rank 3+. This is the inversion: Run B was dominated by entry_mult; Lane 9 surfaces curve-shape features as the real signal.

### Bundled subset (n=323)

| Rank | Feature | Importance |
|---|---|---:|
| 1 | top3_buyer_pct | +0.0954 |
| 2 | sol_spent_first_2s | +0.0452 |
| 3 | sol_spent_first_5s | +0.0446 |
| 4 | sell_ratio | +0.0402 |
| 5 | entry_mult | +0.0390 |
| 6 | vsol_velocity_60s | +0.0328 |
| 7 | buys_per_buyer | +0.0328 |

Bundled signal mechanism: **early concentration + early load + sell pressure**. Top3 concentration in the first 2-5 seconds of trading, plus sell ratio. This matches the bundled-pump intuition: bundlers load up early, then start dumping.

### Non-bundled subset (n=2,753)

| Rank | Feature | Importance |
|---|---|---:|
| 1 | **max_mult_at_age** | +0.1842 |
| 2 | **vsol_velocity_60s** | +0.1618 |
| 3 | top3_buyer_pct | +0.0771 |
| 4 | sol_spent_first_2s | +0.0742 |
| 5 | entry_mult | +0.0718 |
| 6 | dust_buy_rate | +0.0658 |
| 7 | vsol_acceleration | +0.0495 |
| 8 | sol_spent_first_5s | +0.0479 |
| 9 | repeat_buyer_rate | +0.0438 |
| 10 | sell_ratio | +0.0405 |

Non-bundled signal mechanism: **peak+momentum dominates**, with concentration as secondary. This is the diagnostic that was missing from the pre-grad-only feature set — peak-so-far and 60s momentum are exactly the curve-shape features the model needed to discriminate non-bundled graduators from non-bundled rugs.

### Top-5 overlap analysis

- **Shared between bundled and non-bundled top-5:** `entry_mult`, `sol_spent_first_2s`, `top3_buyer_pct` (3 features)
- Bundled-only top-5: `sell_ratio`, `sol_spent_first_5s`
- Non-bundled-only top-5: `max_mult_at_age`, `vsol_velocity_60s`

**Overlap = 3.** Per Lane 2's decision rule, this is the AMBIGUOUS zone. The signal mechanisms ARE somewhat different: non-bundled relies on momentum + peak, bundled on early concentration + sell pressure. Hold on the per-population specialization decision until the retrain implementation.

## What changed vs Run B

The 11.51pp overall jump (and 14.16pp on non-bundled) came from adding 13 curve-replay features. The biggest contributors per importance:

1. **max_mult_at_age** (Lane 6 #1 candidate) — peak multiplier so far, captures whether the mint has shown explosive movement
2. **vsol_velocity_60s** (Lane 6 candidate) — 60-second vsol growth, momentum indicator
3. **top3_buyer_pct** (Lane 6 #3 candidate) — concentration metric, separates organic from bundled
4. **sol_spent_first_2s** (Lane 6 candidate) — early-load signal
5. **dust_buy_rate** (Lane 6 candidate) — bot-pattern detector

The Lane 6 priority guesses (max_mult, vsol_acceleration, top3_buyer_pct + repeat_buyer_rate) are validated as top-importance features. The four flagged for non-bundled separation (`unknown_buyer_pct`, `low_history_pct`, `n_smart_in`, `sell_ratio`) — only `sell_ratio` and `n_smart_in` could be computed from curves alone (the others need wallet history). `n_smart_in` ranks low (sample-size limited at n=96 wallets matched). `sell_ratio` ranks 10 in non-bundled — moderate but not dominant.

## Sample caveats

- 3,076 rows after curve-join (vs Lane 2's 4,849). 35% no-curve gap — same observer collection issue Lane 1's H_collection finding identified. The model trains on the captured subset; performance on the missing 35% is unknown.
- Held-out test n=616. Bundled subset n_te=65, which is small enough that the per-population AUC numbers have wide confidence intervals.
- Same train/test split (random_state=42) as Run B, but the SAMPLE is different (curve-joined subset). Apples-to-apples baseline within Lane 9 is the inline Run B retrain (0.6205), not the original Lane 2 Run B (0.6430).
- LightGBM unavailable; sklearn HistGradientBoostingClassifier used. Same algorithm family.
- The k-NN reference (predicted_prob predicting sustain) is asymmetric — predicted_prob is graduation-prob, not sustain-prob. We're asking whether the existing model's output contains sustain signal. Answer: barely (AUC 0.60 on this sample).

## Decision (applied fresh, per discipline contract)

| Threshold | Bound | Lane 9 result | Decision |
|---|---:|---:|---|
| ≥5pp closure | Non-bundled AUC ≥ 0.674 | 0.7413 | **TRIPPED** |
| 2-5pp closure | 0.644 ≤ AUC < 0.674 | (skipped) | — |
| <2pp closure | AUC < 0.644 | (skipped) | — |

**Decision per pre-registered rule: retrain is justified. Implementation scoping is the next phase.**

## What this does NOT decide (per discipline contract)

- **No deploy decisions tonight regardless of result.** Rollout scoping is its own pre-registered work, sequenced after this writeup.
- **Architecture choice (k-NN vs GBM) for the retrain is still open.** Lane 2's overlap=4 suggested "richer features in same architecture"; Lane 9's overlap=3 is more ambiguous. Re-run the architecture comparison on the feature-rich set — that's its own pre-registration.
- **Per-population specialization (single vs two-model)** stays open. The 5.9pp regression on bundled is a flag worth investigating before committing to a single combined model. Could be sample noise (n_te=65); could be real signal that two-model architecture or interaction terms are warranted.
- **Lane 8 (suppression matrix bias)** stays in priority but doesn't jump to the top — features alone closed the gap, so the matrix isn't the FIRST thing to fix. It's still worth running; non-bundled mints reaching grad_prob ≥0.7 at age 60 but not firing is still a real bottleneck.

## Implementation-scoping seeds (NOT decisions, just the next questions)

For tomorrow's retrain scoping work:

1. **Curve-replay must run for the full corpus, not just the joined subset.** The 1,687 mints without joinable curves represent a real gap — either we re-extract from observer-curves with a wider net, or we accept the model trains on the captured subset only. Either is a defensible choice; pre-register before execution.
2. **Bundled regression needs investigation.** -5.92pp is enough to flag; not enough to act on without a fresh test on a larger held-out sample. Sub-hypothesis: is bundled performance regressing because the model has more features overall, or because the new features actively confuse bundled prediction? Test by ablating features one at a time.
3. **Live deployment requires extracting Lane 6 features at score time.** Currently they're computed in observer-rs and partially in `_enrich_mint`, but not all are surfaced to the m_out dict that score_full consumes. The retrain needs corresponding live-pipeline work to ensure feature parity at predict time.
4. **The k-NN existing model's predicted_prob still has value as an INPUT to a stacked retrain.** At AUC 0.60 on sustain it's not nothing. Worth pre-registering whether to include it as a feature (would lift the AUC further but make the new model dependent on the old one).

## Numerical summary saved to `/tmp/lane2/summary_lane9.json`

Reproducibility: training script at `/tmp/lane2/train_lane9.py`, feature extraction at `/tmp/lane9_extract.py` (also on Fly at `/tmp/lane9_extract.py`). Joinable feature CSV at `/tmp/lane2/lane9_features.csv` (also on Fly at `/tmp/lane9_features.csv`).
