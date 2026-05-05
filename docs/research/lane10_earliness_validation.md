# Lane 10 — Earliness validation on Lane 9 model

**Run date:** 2026-05-05 evening
**Pre-registration:** [BACKLOG.md → "Lane 10 — earliness validation on Lane 9 model"](../../BACKLOG.md)
**Decision criteria (frozen pre-run):**
- ≥2× ACT-eligible rate at age=30 (or ≥15.8% absolute) → retrain solves earliness AND accuracy
- 1.2-2× → partial earliness gain, separate intervention needed
- <1.2× → accuracy-only fix, lateness is structural

---

## Headline

**Non-bundled @ age=30: GBM ACT-eligible rate 11.8% vs k-NN 5.7%. Ratio = 2.07×.**

**Decision per pre-registered rule applied fresh: retrain solves earliness AND accuracy. TIER: ships earliness.**

But the result is **marginal** — just barely past the 2× threshold. Worth being honest about: a slightly different sample or split could land it in the "partial earliness" zone. Worth re-validating on a larger held-out sample post-retrain implementation.

## Numbers

### Pre-registered cell: non-bundled @ age_bucket=30

| Metric | k-NN (current) | GBM (Lane 9 retrain) |
|---|---:|---:|
| ACT-eligible (`grad_prob ≥ 0.7 AND cur_mult ≤ 2.0`) | **15 / 263 = 5.7%** | **31 / 263 = 11.8%** |
| Ratio | — | **2.07×** |

Population sustain rate in this cell: **58.6%**. So the GBM's 31 ACT-eligible fires would have an EXPECTED sustain rate around 58.6% (assuming GBM doesn't discriminate within the cell — likely higher since GBM is selecting on confidence).

### All four cells (age × population)

| Cell | n_test | k-NN ACT% | GBM ACT% | Ratio | Sustain in cell |
|---|---:|---:|---:|---:|---:|
| **non-bundled @ age=30** | 263 | 5.7% | **11.8%** | **2.07×** | 58.6% |
| non-bundled @ age=60 | 288 | 3.1% | 7.3% | 2.35× | 55.2% |
| bundled @ age=30 | 25 | 0.0% | 0.0% | (n/a) | 24.0% |
| bundled @ age=60 | 40 | 0.0% | 2.5% | (n/a) | 40.0% |

The earliness gain is **consistent across both age buckets** for non-bundled (2.07× at age=30, 2.35× at age=60). Bundled subset is essentially unchanged — bundled mints almost never have `cur_mult ≤ 2.0` regardless of model (per Lane 8: 100% of bundled ≥0.7 candidates trip the entry-quality filter).

## What this means in product terms

Pre-retrain: of 100 non-bundled mints at age=30, ~6 fire as ACT.
Post-retrain: of 100 non-bundled mints at age=30, ~12 fire as ACT.

**The retrain doubles ACT-eligible fires on non-bundled mints at age=30**, but the absolute rate (12%) means **88% of non-bundled mints still don't qualify for ACT at age=30.** The lateness problem is ameliorated, not solved.

That's the honest framing. The retrain's earliness gain is real and crosses the pre-registered bar, but the user-facing problem ("most non-bundled mints have already pumped past 2× by the time we fire") persists for the majority. The product gets MEANINGFULLY better, not transformed.

## Why this barely tripped (not why it overshot)

The retrain ceiling appears bounded by the structural constraint that **the model needs trade volume to be confident**, and most non-bundled graduators have meaningful price action by age=30. The Lane 9 features (max_mult_at_age, vsol_velocity_60s, top3_buyer_pct, sol_spent_first_2s) help the model decide earlier — but only for the subset of mints that have informative early-window features. For the rest, even the GBM needs more time.

This is consistent with Run B vs Lane 9 in Lane 9's writeup: the AUC delta on non-bundled was +14pp, but the ACT-eligibility delta is only +6pp. AUC measures discrimination across the full prob range; ACT-eligibility measures specifically whether the model crosses 0.7 BEFORE cur_mult crosses 2.0. The latter is a stricter test — and the retrain partially passes it.

## What ships, what doesn't

**Ships in retrain story:**
- Retrain doubles ACT-eligible fires on the population we were missing
- Retrain crosses the pre-registered earliness threshold (2.07× > 2×)
- 95.5%-sustain ACT path (Lane 8 finding) effectively grows by ~6 percentage points of the non-bundled population

**Doesn't ship in retrain story (because it's not true):**
- "The retrain solves the lateness problem." It doesn't. 88% still don't fire ACT at age=30.
- "Earlier alerts on every non-bundled graduator." No — earlier alerts on the subset where features can be confident at age=30. Roughly twice as many of those as before.

## Implications for tomorrow's retrain implementation scope

1. **Single workstream confirmed.** Retrain ships, addresses both accuracy and earliness within the structural ceiling.
2. **Honest product framing for the post-retrain era:** "we now fire ACT on roughly 12% of non-bundled mints at age=30, up from 6%. Coverage doubled. The ones we DON'T catch at age=30 are caught later as WATCH (still alerted, framed as catching late)."
3. **A separate "earlier confidence at age=30" research question opens:** what would push the 12% to 25%+? Possibilities:
   - Pre-bond bot-detection features that fire BEFORE volume confirms (currently none in the feature set)
   - Smart-money-in signal at age 5-15s rather than waiting until age=30
   - A different label that biases toward early confidence (e.g. `runner_within_first_60s`)
   This is its own pre-registration when retrain ships. Not in scope for retrain itself.
4. **Lane 1's collection leak (Layer 1) becomes the next-priority fix.** A retrained model can only fire on mints the observer captures. ~40% non-bundled graduators currently invisible to observer. Even after retrain ships, that's the ceiling on coverage. Tomorrow's parallel workstream.

## Caveats

- Sample n=263 non-bundled at age=30 in held-out. The ratio is 2.07× — small absolute counts (15 vs 31). A swing of ~2 fires in either direction would change the verdict to "partial earliness" or "ships well above 2×". Worth re-validating on a larger held-out sample post-retrain implementation.
- `entry_mult` from predictions table is the FIRST-tick value in each age_bucket. Live fire happens at that or later snapshots. The actual cur_mult at GBM-fire-time may be slightly higher than entry_mult (mint pumped between first tick and fire tick). This makes the analysis CONSERVATIVE — actual ACT-eligibility may be lower than the 11.8% estimate.
- The k-NN reference uses `predictions.predicted_prob` directly — the live model's output. This is fair: it's what the live system actually fires on. But k-NN's predicted_prob is graduation prob, while GBM was trained on sustained_30m. Different labels. The comparison "ACT-eligible under each model" is the right comparison for the user-facing question (does the user fire ACT?), but it's not architecturally apples-to-apples.
- LightGBM unavailable; sklearn HistGB used (same algorithm family).

## Numerical summary saved to `/tmp/lane2/summary_lane10.json`
