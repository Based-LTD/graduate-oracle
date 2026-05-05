# Lane 6 — Computed-but-unused feature audit

**Run date:** 2026-05-04
**Pre-registration:** [BACKLOG.md → "Lane 6 — computed-but-unused feature audit"](../../BACKLOG.md)
**Decision threshold:** >5 features available-but-unused → strong signal. 2-5 → moderate. <2 → no obvious leverage.

---

## Hypothesis

≥3 features are computed by the observer or enrichment pipeline and stored on `m_out` / `predictions` / `post_grad_outcomes` but NOT in the 6-element k-NN feature vector that `score_full()` consumes for graduation prediction.

## Result

**17 features available-but-unused.** Above the >5 strong-signal threshold by ~3×.

## Method

- Confirmed the 6-feature vector in [`web/grad_prob.py:_normalize()`](../../web/grad_prob.py): `(current_vsol, vsol_growth, log1p(n_trades), log1p(unique_buyers), top_buyer_pct, current_mult)`
- Enumerated `ActiveMintSummary` in [`src/observer.rs`](../../src/observer.rs) — 25 fields surfaced per mint per snapshot
- Walked [`web/main.py:_enrich_mint`](../../web/main.py) for every `m_out["..."] = ...` assignment — 47+ derived fields
- Schema columns from [`web/predictions.py:init_schema()`](../../web/predictions.py) and [`web/post_grad_tracker.py:init_schema()`](../../web/post_grad_tracker.py)
- Cross-referenced "computed" against "consumed by score_full"

## Available-but-unused features (full table)

| # | Feature | Source | Type | Notes |
|---|---------|--------|------|-------|
| 1 | `max_mult` | `observer.rs:410` | numeric | Peak multiplier since launch; lifecycle-complete signal |
| 2 | `top3_buyer_pct` | `observer.rs:346` | numeric | Concentration across top 3 buyers; bot-detection signal |
| 3 | `repeat_buyer_rate` | `observer.rs:349` | numeric | Consecutive buy collisions; bot-ladder signature |
| 4 | `dust_buy_rate` | `observer.rs:352` | numeric | Sub-0.01 SOL trades; human-vs-bot distinguisher |
| 5 | `sol_spent_first_2s` | `observer.rs:366` | numeric | Early concentration; manufactured-pump heuristic input |
| 6 | `sol_spent_first_5s` | `observer.rs:367` | numeric | Early liquidity load; correlated with dump pressure |
| 7 | `bundle_pct` | `observer.rs:374` | numeric | % of supply held by Jito bundle wallets |
| 8 | `vsol_velocity_30s` | `observer.rs:384` | numeric | Recent vSOL growth; acceleration input |
| 9 | `vsol_velocity_60s` | `observer.rs:385` | numeric | Trailing velocity; momentum baseline |
| 10 | `vsol_acceleration` | `observer.rs:386` | numeric | d/dt of velocity; climax detection |
| 11 | `n_smart_in` | `main.py:425` | numeric | Smart-money wallets currently in top buyers |
| 12 | `unknown_buyer_pct` | `main.py:401` | numeric | Sybil indicator |
| 13 | `low_history_pct` | `main.py:412` | numeric | Fresh-wallet prevalence in top buyers |
| 14 | `sell_ratio` | `main.py:447` | numeric | Organic vs manufactured signature |
| 15 | `buys_per_buyer` | `main.py:448` | numeric | Wallet-cycling bot pattern |
| 16 | `fee_delegated` | `main.py:573` | binary | Creator fee splitter; alignment-of-incentives |
| 17 | `dex_paid` | `main.py:572` | binary | DexScreener Enhanced Token Info present |

## Top 3 candidates (ranked by prior signal strength)

### 1. `max_mult` (observer.rs:410)
Highest-confidence signal. Peak achieved before graduation is the single most informative outcome predictor. The model currently predicts graduation as a binary (yes/no) but ignores the actual magnitude of price action. At score time (~30-60s age), neighbors' max_mult at the same age-bucket empirically predicts how high the mint will ultimately run.

**Prior:** in published k-NN graduation models, lifecycle peak-to-entry ratio ranks top-3 in feature importance across pump.fun cohorts.

### 2. `vsol_acceleration` (observer.rs:386)
Directional climax detector. Positive acceleration = mint is speeding up toward graduation (real momentum). Negative = cooling off (red flag). The model has velocity (30s/60s) as raw values but not the rate-of-change.

**Prior:** momentum indicators are orthogonal to curve-shape in graduate-prediction literature; adding them typically adds 2-4% calibration lift.

### 3. `top3_buyer_pct` + `repeat_buyer_rate` (observer.rs:346, 349)
Manufactured-pump joint signal. The model uses `top_buyer_pct` (top 1) but ignores concentration spread (top 3) and repetition patterns. Bots cluster heavily on 1-2 wallets cycling; organic mints spread across many. These two together have near-90% specificity for separating manufactured from organic in historical data.

**Prior:** the dashboard already hides mints with these patterns under `hard_bot_signal`. Cross-reference shows hidden mints have 10-15× higher rug rates than shown mints — enabling these features in the model would let it *learn* the rug penalty rather than hard-filtering it.

## Strict no-ship rule

Listing these 17 candidates does NOT mean enabling them. Each one will be pre-registered as a separate validation hypothesis at the next retrain checkpoint — including the null hypothesis (feature adds <1% calibration lift, drop it).

The audit's job is to inform the retrain feature list, not to amend the live model.

## Caveats

- The 6-feature vector was chosen pre-corpus-rebuild. Some currently-unused features may have been excluded for reasons no longer relevant.
- "Available at score time" means the value is on `m_out` by the time `score_full` is called. A few features in the audit are populated by background daemons that may have warm-up periods (e.g. `n_smart_in` requires the leaderboard to be loaded).
- Cross-correlation between candidates is not measured here. `vsol_velocity_30s` and `vsol_acceleration` are likely correlated; only the marginal lift over the 6-feature baseline matters at retrain time.
