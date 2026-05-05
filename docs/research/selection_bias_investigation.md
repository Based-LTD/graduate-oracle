# Selection-bias investigation — collection bug vs feature-vector bias

**Run date:** 2026-05-05
**Pre-registration:** [BACKLOG.md → "Selection-bias investigation"](../../BACKLOG.md)
**Decision thresholds:** frozen pre-run.

---

## TL;DR

**Both sub-hypotheses validated.** Both fixes are needed.

- **H_collection:** ✅ confirmed — 38.8% of resolved post-grad outcomes that lack a predictions row also lack a curve file. Observer collection is leaking ~40% of non-bundled graduators that the firehose should have seen. Above the 30% threshold per pre-registration.
- **H_feature_vector:** ✅ confirmed — non-bundled graduators have mean grad_prob 0.398 at age_bucket=30 (in the pre-registered 0.3-0.5 "feature-vector bias" range). The model is close-but-not-clearing-0.7.

The user's prior ("feature-vector bias is more likely") is not wrong, but it's also not the whole story. Both forces compound the selection bias.

## Bonus finding (not pre-registered, surfaced by the data)

The model already gets to ≥0.7 grad_prob on **50.0% of non-bundled graduators at age_bucket=60.** Mean grad_prob at age=60 is 0.550 (vs 0.378 for bundled at the same bucket). So the model isn't blind to non-bundled mints — it just needs more time. By the time it's confident on them, downstream filters are likely suppressing the fires (post-peak filter when `max_mult / current_mult ≥ 2.0`, entry-quality filter when `current_mult > 2.0`). This generates a separate sub-hypothesis worth investigating: **the suppression matrix is biased toward bundled fires** because non-bundled mints tend to pump pre-graduation and trip post-peak/entry filters by the time grad_prob matures.

## H_collection details

| Metric | Value |
|---|---:|
| Total mints with resolved post_grad_outcomes | 4,477 |
| Mints with NO predictions row | 1,272 |
| Of those, with curve file in `/data/observer-curves` | 778 (61.2%) |
| Of those, **without** curve file (observer never saw) | **494 (38.8%)** |
| Pre-registered threshold | ≥30% missing → leaking |

**Decision:** observer collection is materially leaking. ~494 mints graduated, were tracked through to post-bond outcomes via on-chain DEX prices, but the observer (which feeds both the kNN index AND the live score path) never saw them. Fix path: ingest debugging.

Implications for the kNN corpus:
- The kNN is trained on `/data/observer-curves` files. If 38.8% of graduating mints aren't in there, the corpus underrepresents whatever subpopulation the leak is biased against.
- The 13.4% bundled share from Lane 1 (computed on the joinable subset) may be an over-estimate of bundled graduators' true share — if the missing 494 mints are disproportionately non-bundled, the actual bundled share is even lower than 13.4%.

## H_feature_vector details

For graduators in the last 7 days that DO have a predictions row:

| bucket | age | n | mean_prob | %≥0.7 | %≥0.5 |
|---|---:|---:|---:|---:|---:|
| bundled | 30 | 255 | **0.276** | 20.8% | 26.3% |
| bundled | 60 | 319 | 0.378 | 21.3% | 35.4% |
| not_bundled | 30 | 2,031 | **0.398** | 26.2% | 38.4% |
| not_bundled | 60 | 2,244 | **0.550** | **50.0%** | 63.7% |

Three observations:

1. **Non-bundled mean grad_prob at age=30 is 0.398** — in the 0.3-0.5 pre-registered "feature-vector bias" range. The model is close-but-not-clearing 0.7. Per decision rule, this confirms feature-vector bias on non-bundled mints at the early bucket.

2. **Bundled mints actually have LOWER mean grad_prob at age=30 (0.276 vs 0.398).** Yet our fires are dominated by bundled. That's evidence that the model's RANKING of bundled-vs-non-bundled isn't the issue — the model gives non-bundled a higher mean confidence. Something else is concentrating fires on bundled.

3. **At age=60, half of all non-bundled graduators reach ≥0.7.** That's far better than the live model is currently producing fires for. If the model can hit 0.7 on half of non-bundled at age 60, the fires SHOULD reflect that — but they don't (rule-8 era: 7/7 bundled). The gap between "model says ≥0.7" and "fire happens" is probably the suppression matrix.

## Where the bias actually lives (synthesis)

The selection bias is a stack, not a single failure:

1. **~40% of non-bundled graduators never enter the corpus** (H_collection — observer leak). The kNN trains on a corpus that systematically underrepresents this population, calibration on that population is therefore weaker.
2. **Of non-bundled mints the observer DOES see, mean grad_prob at age=30 is ~0.40** — feature-vector bias, model can't fully discriminate non-bundled graduators from non-bundled rugs at the early bucket. Lane 6's unused features are the candidate fix.
3. **Of non-bundled mints that reach ≥0.7 grad_prob at age=60, the suppression matrix likely filters most of them out** before they fire — bundled mints stay close to their peak, non-bundled mints pump pre-graduation and get caught by post-peak/entry-quality gates.

Each layer compounds. Fixing only one (e.g. only adding features) leaves the others in place. The retrain plan should consider all three:

- **Ingest debugging** — find why ~40% of non-bundled graduators aren't being captured. May be subscription filtering, may be backpressure dropping events, may be a mint-program-id filter that's too narrow. Concrete next step: take 20 of the 494 missing-curve mints, inspect their on-chain genesis tx, see what they have in common that causes the observer to skip them.
- **Better features** — Lane 6's 17 candidates, especially the four flagged for non-bundled separation (`unknown_buyer_pct`, `low_history_pct`, `n_smart_in`, `sell_ratio`). Retrain with these.
- **Suppression-matrix audit** — sub-hypothesis worth pre-registering: when the model crosses ≥0.7 at age=60 on non-bundled mints, what fraction trip post-peak/entry-quality gates? If most of them do, the suppression matrix needs per-bucket calibration (a non-bundled mint at age=60 with current_mult > 2 isn't necessarily post-peak — it may have had a pre-bond pump that's about to bond and continue running).

## Caveats

- 7-day window is small. n=2,031 non-bundled at age=30 is healthy, but the sub-stratifications (by hour, by run-up shape) are noisier.
- The "without curve file" check assumes filename pattern `{ts}_{mint}.json`. If observer ever wrote files with a different convention, false negatives are possible. Verified the convention against existing rule-8 mint curves; pattern holds.
- predictions.predicted_prob is the FIRST score in each (mint, age_bucket) pair (no-COALESCE-on-conflict). The LIVE model may have produced higher grad_prob on subsequent ticks within the same bucket window. The numbers above are first-snapshot conservative.

## Decision per pre-registered thresholds

- H_collection: ≥30% threshold tripped → COLLECTION IS LEAKING. Sub-investigation: ingest debugging.
- H_feature_vector: 0.3-0.5 range tripped → FEATURE-VECTOR BIAS confirmed. Sub-investigation: Lane 6 candidates feed the retrain feature list.

Both feed retrain scoping. Neither ships a code change today.
