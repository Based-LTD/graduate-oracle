# Research log

This directory contains the per-investigation writeups behind the methodology in [`../methodology.md`](../methodology.md). Each "lane" is a discrete research question with a pre-registered hypothesis, a method, and a frozen decision rule applied to the result *after* the data lands.

The **point of publishing this** is the same point as the [tamper-evident merkle ledger](https://graduateoracle.fun/api/ledger/commits) that backs every prediction the oracle makes: you don't have to take our word for the methodology. You can read the questions we asked, the criteria we set in advance, and the answers we got — including the answers we didn't expect or didn't want.

---

## Discipline

Every lane in this directory follows the same shape:

1. **Pre-register before running.** The hypothesis, sample, method, and decision rule are written to [`../../BACKLOG.md`](../../BACKLOG.md) *before* execution. Once written, the rule is frozen — applied fresh to whatever result lands.
2. **Run.**
3. **Apply the decision rule fresh.** No "qualitative override" without explicit acknowledgment. When the data fits the pre-registered branches cleanly, the rule decides. When the data doesn't fit any pre-registered branch (which has happened, see [`lane13_calibration_stability.md`](lane13_calibration_stability.md) and [`lane14_bundled_regression.md`](lane14_bundled_regression.md)), we surface the divergence and update the rule for next time rather than retroactively rewriting the verdict.
4. **Publish the writeup regardless of outcome.** Negative results, blocked executions, and inverted framings get the same treatment as positive findings. See [`lane4_smart_money_post_grad.md`](lane4_smart_money_post_grad.md) for an example of a blocked-and-dropped lane published cleanly.

The discipline is the moat. Anyone can run experiments; the difference is whether the criterion was set before the result was visible.

---

## Index

| Lane | Subject | Outcome |
|---|---|---|
| [lane1_bundled_corpus.md](lane1_bundled_corpus.md) | Is pump.fun's graduation pool dominated by bundled/manufactured pumps? | **Hypothesis rejected.** 87% of graduations are non-bundled, and they sustain post-bond at 1.7× the rate of bundled ones. The model had selection bias. |
| [lane2_gbm_stratified.md](lane2_gbm_stratified.md) | Does a different model architecture (GBM) beat the deployed k-NN on the same features? | Architecture HOLD. The lever is feature engineering, not architecture substitution. |
| [lane4_smart_money_post_grad.md](lane4_smart_money_post_grad.md) | Do post-bond runners have ≥2× the rate of smart-money wallet entries vs post-bond rugs? | **Blocked then dropped.** Sandbox couldn't access required credentials; reframe under Lane 1 made the question lower-priority. Documented for future re-execution. |
| [lane6_unused_features.md](lane6_unused_features.md) | What features are computed by the observer but not consumed by the model? | 17 features identified. Top candidates: `max_mult`, `vsol_acceleration`, `top3_buyer_pct + repeat_buyer_rate`. |
| [lane7_runner_prob_calibration.md](lane7_runner_prob_calibration.md) | Are `runner_prob_*_from_now` API fields calibrated as advertised? | **Mis-scaled by ~12pp at high-confidence bins.** API caveat shipped same-day; calibration framing now explicitly states "directional only, magnitude non-stationary." |
| [lane7_audit_recalibration_mechanism.md](lane7_audit_recalibration_mechanism.md) | Why is `runner_prob` mis-scaled? | Both pre-registered mechanisms rejected. Found a third: historical curve drift. Re-scoped recalibration. |
| [lane7_recent_slice_rerun.md](lane7_recent_slice_rerun.md) | Does the third mechanism (drift) hold up — is recent calibration clean? | **Direction flipped.** Recent slice mis-scaled in the *opposite* direction. Calibration is non-stationary, not historical. |
| [lane8_suppression_bias.md](lane8_suppression_bias.md) | Is the WATCH alert's suppression matrix biased against non-bundled mints? | Rejected. Matrix filters 11.8%, well below the 20% threshold. Bonus finding: ACT-eligible candidates sustain at 95.5% — the matrix is actively discriminating quality, not biased. |
| [lane9_curve_replay_retrain.md](lane9_curve_replay_retrain.md) | Do curve-replay features (Lane 6) close the AUC gap on non-bundled mints? | **+14.16pp closure on non-bundled.** Retrain justified. Confirms architecture isn't the lever; features are. |
| [lane10_earliness_validation.md](lane10_earliness_validation.md) | Does the retrained model also fire earlier, not just more accurately? | **2.07× ACT-eligible at age=30s** — passes the threshold by a hair. Earliness improves but lateness problem only partially solved. |
| [lane11_collection_leak.md](lane11_collection_leak.md) | Why does the observer miss ~40% of non-bundled graduators? | Eviction hypothesis rejected. Real mechanism: hour-of-day clustering — observer's miss rate spikes during specific recurring UTC windows. |
| [lane11_path_a_logs.md](lane11_path_a_logs.md) | Are the catastrophic windows caused by an upstream provider's maintenance? | Rejected. Upstream services are clean. The 12-hour cycle is in our infrastructure, not theirs. |
| [lane13_calibration_stability.md](lane13_calibration_stability.md) | Is `runner_prob` calibration trending, oscillating, or sample-bias noise? | **Transition zone.** Strict pre-registered rule says "unclear"; qualitative read says curve overshoot from fast rebuild + sample noise. Surfaced the rule's gap and updated discipline pattern for future pre-registrations. |
| [lane14_bundled_regression.md](lane14_bundled_regression.md) | Does the new model regress on bundled-pump performance? | Two pre-registered branches fired simultaneously (sample noise + single-feature contributor). Hybrid action: ship single-track, monitor at scale, don't pre-commit two-model architecture. |
| [selection_bias_investigation.md](selection_bias_investigation.md) | Why is the model missing the 87% non-bundled population? Collection or feature-vector bias? | **Both confirmed.** Selection bias is a stack: collection-leak in observer (~40% of non-bundled never enter corpus) + feature-vector under-confidence (model reaches mean grad_prob 0.398 on non-bundled) + downstream gating (later rejected by Lane 8). |
| [retrain_v1_decision.md](retrain_v1_decision.md) | Does retrain v1 meet the frozen ship-replace gates? | All four gates pass. Single-track GBM cleared overall and stratified thresholds; bundled-AUC regression flipped from -5.9pp at small sample to +7.7pp at corpus scale. |
| [today.md](today.md) | Day-close summary for 2026-05-04. | Started uncertain about whether the product was viable; ended with a closed diagnostic stack, pre-registered implementation workstreams, and a corrected understanding of where the leverage actually lives. |

---

## How this connects to the rest of the public repo

- [`../methodology.md`](../methodology.md) — high-level model approach and the public-disclosure boundary (what's documented vs what stays proprietary)
- [`../../BACKLOG.md`](../../BACKLOG.md) — the source of truth for pre-registered decisions, frozen criteria, and active R&D pre-registrations
- [`../../data/`](../../data/) — daily accuracy snapshots and paper-trading P&L (`git log data/` shows the model's published performance over time)
- [`https://graduateoracle.fun/api/accuracy`](https://graduateoracle.fun/api/accuracy) — live calibration metrics, including warming states when sample sizes are below decision thresholds
- [`https://graduateoracle.fun/api/ledger/commits`](https://graduateoracle.fun/api/ledger/commits) — tamper-evident merkle ledger over every prediction the oracle has made

Predictions are committed cryptographically before resolution. Methodology is published. Pre-registered criteria are public. Negatives are surfaced. **Audit our work.**
