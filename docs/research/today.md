# Day-close: 2026-05-04

For tomorrow-Claude (or whoever picks up next): **the noon framing of today is wrong**, the evening framing is the load-bearing one. Don't read backwards from this morning's conclusions — read this first.

## What we believed at three points today

- **Noon:** "Our model fires on bundled pumps because that's what graduates." (Today's earliest finding from 7/7 rule-8 fires being bundled.)
- **5pm:** "We have a narrow but defensible bundled-pump predictor — the bundled population IS pump.fun's graduation pool." (Reframe based on accepting that the corpus represents the graduation pool.)
- **Evening (load-bearing):** **pump.fun's graduation pool is 87% non-bundled, and non-bundled mints sustain at 1.7× the rate of bundled ones (53.1% vs 31.7%). Our model has a real selection bias and is firing on the worse-sustaining 13% slice.** This was the result of Lane 1.

## Why the evening framing is BETTER news, not worse

The 5pm framing was a permanent ceiling: "narrow product on a narrow population." The evening framing is a fixable problem: "selection-biased model missing the better half of the available signal." The product's potential ceiling went up, not down. The lever is the retrain.

## Today's actual deliverables

Honesty pass on dashboard:
- Hero: 93% LOO removed from above-the-fold. Title, meta tags, band legend cleaned. Live state shows "warming · 0/30 resolved" honestly.
- /accuracy: 3-card co-billing (graduates backtest / graduates live / sustains 30m post-bond). Sustains is materially lower than graduations and now visible as such.
- TG /accuracy mirrors the page.
- WATCH alert template: graduation + sustains co-billed equal weight. "Model still bullish on graduation" framing dropped.

R&D Lane 1 (gating, complete):
- Pump.fun's graduation pool is 87% non-bundled. Hypothesis rejected. Writeup at [lane1_bundled_corpus.md](lane1_bundled_corpus.md).

R&D Lane 6 (complete):
- 17 features computed but unused by the k-NN. Top 3: `max_mult`, `vsol_acceleration`, `top3_buyer_pct + repeat_buyer_rate`. Several others (`unknown_buyer_pct`, `low_history_pct`, `n_smart_in`, `sell_ratio`) plausibly separate non-bundled graduators from non-bundled rugs — directly relevant to fixing Lane 1's selection bias. Writeup at [lane6_unused_features.md](lane6_unused_features.md).

R&D Lane 4 (dropped, not deferred):
- Smart-money post-grad correlation. Was load-bearing under noon framing; lower priority under evening framing. Sandbox couldn't access fly ssh + Helius RPC; "what blocked" writeup at [lane4_smart_money_post_grad.md](lane4_smart_money_post_grad.md) preserves re-execution scaffold for any future pickup.

Foundation (smaller, also today):
- activated_at column migration with multi-rule-cutoff invariant warnings.
- Versioned merkle leaf format (V1 + V2) with backwards-compat verification.
- Replay-unsupported memory (`project_replay_unsupported.md`) — historical model replay against today's index is fundamentally underspecified; don't try.
- Gate validation script + pre-registered criterion live at `/api/gate_validation`.
- BACKLOG.md formalized as the source of truth for pre-registered decisions.

## What load is on the wheelbarrow now

**The retrain is the highest-leverage move on the entire roadmap.** It has a clearer specification than it did this morning:

1. Train on the FULL 4,256 resolved outcomes, not the slice the current k-NN was indexed on. The old corpus has selection bias baked in.
2. Include Lane 6's 17 unused features. Top priority: `max_mult`, `vsol_acceleration`, `top3_buyer_pct`+`repeat_buyer_rate`, plus `unknown_buyer_pct` / `low_history_pct` / `n_smart_in` / `sell_ratio` (the four flagged for separating non-bundled graduators from rugs).
3. Consider two label choices in parallel: graduation (current) and sustained_30m directly (Lane 2's GBM experiment). The latter is the trader-relevant outcome.
4. Don't hide `hard_bot_signal` mints from training. They have 10-15× higher rug rates than shown mints — the model should LEARN that penalty rather than hide it.
5. Pre-register the architecture comparison (k-NN with cleaner inputs vs GBM) before any model file gets written.

This is a multi-day ship, not a multi-week wait. The retrain isn't gated on "more data accruing"; it's gated on doing the work with what we already have.

## Tomorrow's first move

Scope the retrain. Pre-registration in `BACKLOG.md` under "Retrain scoping" already drafts:
- Training set (full 4,256 outcomes, no bot-signal filter)
- Feature set (existing 6 + Lane 6's 7 priority candidates)
- Label choices (A: graduation, B: sustained_30m)
- Architectures to compare (k-NN existing, GBM new)
- Decision criteria (ship-replace, ship-augment, don't-ship — frozen at pre-registration time)

**Read BACKLOG.md "Retrain scoping" section before doing anything else.**

## Tomorrow's second move (or split off concurrently)

Pre-registration is also in place for:
- **Lane 3** (control-group base rates by time-of-day) — quantifies whether the selection bias is time-of-day-dependent
- **Selection-bias investigation** — splits cleanly into H_collection (observer underrepresenting non-bundled in firehose) vs H_feature_vector (observer captures, but kNN puts them in low-confidence neighborhoods). User's prior: feature-vector bias is more likely. Confirm with data.

Don't run these tonight. They need fresh eyes on the pre-registrations before execution.

## What is NOT changed

- **Gate validation criterion** stays frozen against `post_grad_survival_prob`. R&D output never amends pre-registered criteria.
- **Live alerts** stay running. The honesty pass is the only user-facing change today; no model/threshold/copy changes from the R&D findings.
- **No new pricing, marketing, or audience messaging.** The product framing is more hopeful than it was this morning, but until the retrain validates, the credibility-asset-with-unvalidated-trading-signal description still holds.

## State of system as of session close

- /accuracy displays three honest numbers (or warming).
- TG /accuracy mirrors.
- Gate validation runs hourly, currently warming at 0/30.
- All R&D writeups in `docs/research/`.
- All pre-registrations in `BACKLOG.md`.
- Memory (`project_watch_grad_vs_runner.md`) carries the corrected evening framing.

The system is running. Data accumulates. The retrain is the next active intervention, scheduled for tomorrow's first move.

---

**Today's bookend:** started with "are we even attempting something possible?" Ended with "we're attempting more than we realized, and it's more achievable than we thought." That's a good day's work.
