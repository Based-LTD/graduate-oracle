# Lane 1 — Bundled-pump corpus check

**Run date:** 2026-05-04
**Pre-registration:** [BACKLOG.md → "Lane 1 — bundled-pump corpus check"](../../BACKLOG.md)
**Decision threshold (frozen pre-run):** ≥90% bundled → Lane 2 + Lane 5 unlock. <80% bundled → Lane 3 + investigation. 80-90% → ambiguous.

---

## Hypothesis (pre-registered)

≥90% of resolved post-grad outcomes are `manufactured_pump=1 AND bundle_detected=1`. If true, the WATCH model isn't a generalized graduation predictor — it's specifically a bundled-pump-graduation predictor, and the corpus reflects pump.fun's actual graduation pool, not a model bias.

## Result

**Bundled share: 13.4% (398 of 2,975 resolved+classified outcomes).**

Hypothesis **REJECTED.** The corpus is overwhelmingly NOT bundled. Pump.fun's graduation pool is mostly non-bundled mints; the small recent sample of rule-8 fires (7/7 bundled) was therefore not representative — it was the **model selecting** for bundled features, not the market consisting of bundled launches.

## Four-cell table

| Bucket | n | n_sustained | sustain_rate (≥80% of grad price at 30m) | n_runner_2x | runner_2x_rate (peak ≥ 2× of grad price within 30m) |
|---|---:|---:|---:|---:|---:|
| **bundled** (manufactured_pump=1 AND bundle_detected=1) | **398** | 126 | **31.7%** | 66 | 16.6% |
| **not_bundled** (otherwise) | **2,577** | 1,368 | **53.1%** | 387 | 15.0% |

## What this means

1. **Pump.fun's graduation pool is ~87% non-bundled.** The earlier "the corpus IS bundled, and that's the world" framing is wrong. The world is mostly non-bundled mints; the model just isn't catching them.

2. **Non-bundled graduations sustain meaningfully better.** 53.1% vs 31.7% sustain rate — non-bundled mints are **1.7× more likely to hold price 30 min post-bond.** Runner-2x rates are roughly equal (15.0% vs 16.6%), so the upside potential is similar; the asymmetry is in downside (bundled rugs harder).

3. **The model has systemic selection bias.** It's calibrated on a corpus where most graduators are non-bundled, but at score time it picks heavily from the bundled subset (per today's 7/7 rule-8 fires). That's the bias to investigate, per pre-registered rule for <80%.

## Coverage caveats

- Total resolved post-grad outcomes: **4,413**
- With predictions row carrying both flags: **2,975** (67% coverage of resolved set)
- With predictions row but flags missing: 182 (4%)
- Without any predictions row: **1,256 (28% gap)** — these are mints that either graduated before predictions logging started, or were never scored in the 30/60s lane (graduated too fast / hidden by dashboard filters)

The 28% no-predictions-row gap is a known limitation. If those 1,256 mints disproportionately fall into one bucket (e.g. they're all fast-graduating bundled rockets the model never gets a chance to score), the bundled share for the FULL graduation pool could be higher than 13.4%. But to push the share to ≥90% would require nearly all 1,256 missing mints to be bundled, which is implausible given the population-wide ratio. Even pessimistic assumptions don't move us out of the <80% decision band.

## Decision per pre-registered rule

**13.4% < 80% threshold** → unlock **Lane 3 (control-group base rates by time-of-day)** + **investigation** of why the observer/model is underrepresenting non-bundled graduations.

Two natural sub-hypotheses for the investigation, neither pre-registered yet (will be when Lane 3 / investigation gets formal pre-registration):

- **H_collection:** the observer is missing non-bundled launches due to a feed/subscription bug. Test: compare the rate at which non-bundled graduates appear in our predictions table vs in pump.fun's on-chain graduation events over the same window.
- **H_feature_bias:** the 6-feature k-NN vector inadvertently pattern-matches bundled feature signatures (extreme top_buyer_pct, fast vsol_growth, low n_trades). Test: stratify the 13.4% bundled vs 86.6% non-bundled by the 6 features at age 30s, see if the distributions are separable. If yes, the model is picking the separable cluster; the other cluster needs different features.

## What this lane does NOT decide

- It does NOT amend the gate-validation criterion (frozen against `post_grad_survival_prob`).
- It does NOT propose any feature changes to the live model.
- It does NOT propose any change to current alert thresholds or copy.
- It DOES change the working framing: WATCH alerts are not "the model picking bundled pumps because that's what graduates" — they're "the model picking the bundled subset of a much larger graduation pool, and the bundled subset is the WORSE-quality half post-bond."

## Implication for "are we still excited"

The earlier "narrower product than we thought" framing was based on the assumption that bundled graduations are the world. They're not — they're 13%. The product as it currently fires is selecting the worse-sustaining 13% and missing the 87% with materially better post-bond outcomes. That's a fixable problem, not a fundamental ceiling. It elevates the priority of:

- Lane 6's feature-engineering candidates (17 unused features may include exactly what's needed to distinguish non-bundled graduators)
- The corpus retrain (clean corpus + new feature set may close the selection gap)
- Lane 3 / time-of-day base rates (quantifies the gap and may identify when non-bundled mints are mostly graduating)

The product framing pivots from "we have a narrow but defensible signal" to "we have a fixable selection bias against the better half of graduations."
