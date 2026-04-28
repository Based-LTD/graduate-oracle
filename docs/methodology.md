# Methodology

This document describes how the $GRADUATE oracle works at a high level —
enough for a quant or curious trader to evaluate the approach. Specific
feature weights, distance metrics, bot-detection thresholds, and the
scoring code itself are proprietary and not published here.

---

## The data

We continuously observe the pump.fun firehose via a Solana websocket
subscription to the pump.fun program's `TradeEvent` Anchor logs. Every trade
on every mint, in real time. No sampling, no batching.

For each tracked mint we keep:

- Per-trade `(timestamp, is_buy, sol_amount, token_amount, virtual_sol_reserves, virtual_token_reserves, user_pubkey)`
- A derived multiplier from the first observed price
- Whether pump.fun launched it in "mayhem mode" (a flag we read directly off the bonding-curve account; the byte distinguishes structurally different tokens that we segregate from the main dataset)

A mint is **flushed** to disk once it goes silent for ≥30 minutes. That
flushed file is the canonical record we use for everything downstream.

**Current scale** (see [`data/`](../data/) for fresh snapshots):

| | |
|---|---|
| Historical curves indexed | 60,000+ |
| Wallets ranked | 200,000+ |
| Live mints tracked at any moment | ~1,500–2,000 |
| Snapshot refresh | every 2 seconds |

---

## The model

For a live mint at age `T`, we extract a feature vector
`x = (current_vsol, vsol_growth, log(n_trades), log(unique_buyers), top_buyer_pct, current_mult)`.

We then run a **k-nearest-neighbors** search against every historical curve
that lived past age `T`, using z-scored Euclidean distance with k = 50.

The graduation probability is simply: **fraction of those 50 neighbors that
ultimately graduated**.

That's it. No deep learning, no LSTM, no transformer-on-trades. The simplicity
is intentional — it makes the model legible, the calibration interpretable,
and the predictions defensible. There are no hidden weights to "tune" against
recent data; every prediction is a transparent vote of historical neighbors.

### Why k-NN?

1. **No retraining needed.** Adding new historical curves to the index is
   purely additive — no gradient descent, no overfit risk.
2. **Interpretable per-prediction.** Every score is "X of 50 comparable mints
   from the past graduated." That's a sentence a human can audit.
3. **Calibrates beautifully.** A k-NN classifier where the neighbors share a
   distribution with the test data is, by definition, a calibrated probability
   estimate. (See `lifetime` calibration on [/api/accuracy](https://graduateoracle.fun/api/accuracy).)

---

## What the API hides from `current_mult` alone

The score is the headline. But every API response also carries a stack of
**buyer-quality and bot-detection signals** computed independently from the
score:

| Field | What it measures |
|---|---|
| `top_buyer_pct` | Share of buy volume held by the single largest wallet |
| `top3_buyer_pct` | Same, top-3 combined |
| `repeat_buyer_rate` | Fraction of consecutive buys from the same wallet |
| `dust_buy_rate` | Fraction of buys under 0.01 SOL (often bot ladders) |
| `unknown_buyer_pct` | Fraction of top buyers we've never seen elsewhere |
| `low_history_pct` | Fraction of top buyers with ≤2 prior mints in our index |
| `sniper_pct` | Fraction of top buyers whose `fast_rate ≥ 70%` |
| `avg_top_buyer_smart` | Mean smart-score (graduation rate − rug rate) of top buyers |
| `sell_ratio` | Fraction of all trades that are sells |
| `buys_per_buyer` | Average buys per unique wallet |
| `bot_flags` | A list of any thresholds tripped (sybil farm, wallet cycling, etc.) |
| `is_suspect` | Single-flag warning marker for the UI |

When ≥2 flags fire — or any of the unambiguous "hard-hit" flags
(`sybil_buyers`, `diffuse_sybil`, `fresh_wallet_farm`, `sniper_dominated`,
`bad_history_buyers`, `no_sell_pressure`, `wallet_cycling`) — the mint is
hidden from the dashboard's primary feed entirely. The API returns the
mint to authenticated callers regardless, with `hide_reason` populated, so
power users can decide for themselves.

The exact thresholds are intentionally not documented. Publishing them would
let manufactured-pump operators tune their bots to sit just under each line.

---

## What the model is NOT

It's worth being explicit about the limits.

**Not a financial recommendation.** A 90% probability of graduation is a
historical pattern match, not a price prediction. A graduated mint can dump
50% on Raydium in the first 10 seconds. Always do your own research.

**Not gospel on never-before-seen distributions.** The k-NN is bounded by the
mints it has seen. A genuinely new token type — different bonding curve
mechanics, a different audience, a different launchpad — would need fresh
historical data before predictions on it become meaningful.

**Not a guarantee against rugs.** We aggressively filter manufactured pumps
that show coordinated wallet networks, but a sophisticated operator with a
fresh, previously-unindexed wallet farm can still slip through any single
filter cycle. We tighten thresholds when we see it happen — see git history
of [`data/`](../data/) for honest documentation of when we improved.

---

## Cross-validation methodology

For the lifetime calibration shown on the dashboard:

1. Sample 1,000 random curves per age bucket (`30s, 60s, 120s, 180s, 300s, 600s, 900s, 1500s`).
2. For each sampled curve `i`:
   - Compute its z-scored feature vector at the bucket's age.
   - Take all OTHER curves in the bucket as candidate neighbors. Mask `i` itself out of the pool.
   - Find the 50 closest by Euclidean distance.
   - Predict prob = (graduated neighbors) / 50.
3. Compare predicted prob to actual outcome (`max_virtual_sol ≥ 85 SOL`).
4. Aggregate by predicted-prob threshold (≥50, ≥60, ≥70, ≥80, ≥90 %).

Recomputed every 6 hours on the live index. Snapshots committed daily to
[`data/`](../data/).

---

## Forward-prediction methodology

In addition to lifetime cross-validation, we record every live prediction
the API has ever made. Schema:

```
predictions(mint, age_bucket, predicted_prob, predicted_at,
            actual_graduated, resolved_at, resolution_reason)
```

A row is inserted any time the live scorer returns a probability ≥ 50%, with
`(mint, age_bucket)` deduplication. Resolution runs every 5 minutes:

- The observer flushes a curve once it goes silent ≥30 min.
- The flushed file's `max_virtual_sol` decides graduated vs. died.
- The matching `predictions` row(s) are updated with the actual outcome.
- Predictions that never resolve within 24h are marked `expired` (= didn't graduate, since pump.fun mints either move within an hour or die).

This gives us a **forward calibration** number that is, by construction,
unfudgeable: every prediction was timestamped before its outcome was known.

---

## Versioning

The model itself is a moving target — we add curves daily, occasionally
adjust which features are used, and tighten bot-detection thresholds when we
observe new attack patterns. We don't version the scorer publicly because
the version number would mean nothing to a downstream consumer; the right
abstraction is the calibration snapshot itself.

If you need a frozen scoring snapshot for academic work, contact us on X and
we'll discuss.

---

*Last updated: 2026-04-28*
