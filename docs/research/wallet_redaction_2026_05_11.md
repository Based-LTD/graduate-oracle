# Wallet redaction — Option A (field-level) deploy receipt

**Deploy:** 2026-05-11T05:17:57Z → 2026-05-11T05:19:18Z UTC (epoch 1778476677 → 1778476758). `flyctl deploy --app graduate-oracle --remote-only`. ~81s, rolling-update, single machine, smoke + health passed.

**Evidence base:** Audit 09 verdict ([commit `34ce847`](audit_09_smart_money_lift_results.md)) — PARTIAL with substantive caveat. 7.37× graduation-rate lift at `smart_money_in ≥ 7` stratum vs Control; CIs strongly non-overlapping; clean monotonic 2x-runner trend (41.1% → 69.9% across strata). The wallet reputation index empirically carries strong predictive signal. The strict-monotonicity test failure (Mid 6.1% > High 5.6% on grad_rate within overlapping CIs) is plausibly sampling variation; the substantive signal is overwhelming.

**Strategic framing:** `project_wallet_index_is_the_moat.md` (memory, 2026-05-10). The 200k+ wallet reputation index built by `wallet_intel.py` is the load-bearing moat asset. Specific wallet addresses are proprietary index data; aggregate counts and signal-layer metrics are public.

---

## What this deploy redacted (Option A — field-level)

| Field | Before | After |
|---|---|---|
| `smart_money_examples[]` | List of wallet addresses (3+ per mint) | `[]` |
| `cluster.clustered_wallets[]` | List of wallets in confirmed pair-clusters | `[]` |

**Field names preserved** — `smart_money_examples` and `clustered_wallets` still appear in the API response with empty-array values. Any consumer expecting an array iterates an empty array; no breakage. Backward-compatible by design.

**Aggregate metrics preserved (intentionally):**

- `smart_money_in` (count) — the signal-layer metric that the receipts trail relies on
- `cluster.n_clustered_pairs`, `cluster.cluster_density`, `cluster.max_pair_count` — cluster-strength aggregates that surface the "confirmed pile-in by a known group" signal without revealing which wallets

---

## Dashboard verification

Post-deploy sample (`GET /api/live?limit=1` at 2026-05-11T05:20Z):

```
sample mint:  EK4cBucm..

REDACTED (per Option A):
  smart_money_examples:        []
  cluster.clustered_wallets:   []

AGGREGATES PRESERVED:
  smart_money_in:              7
  cluster.n_clustered_pairs:   0
  cluster.cluster_density:     0.0
  cluster.max_pair_count:      0
```

Frontend (`web/static/app.js:301`) maps over `smart_money_examples` to render a tooltip; an empty array maps to empty string — no UI breakage, just no wallet addresses displayed.

---

## Wider leak surfaces (NOT in this deploy's scope)

A wallet-address scan of `/api/live` after deploy surfaced five additional fields still exposing wallet-shaped data:

| Field | Sample value | Risk profile |
|---|---|---|
| `top_buyers[]` | Wallet addresses of top buyers per mint | **Highest** — the leaderboard is COMPUTED from this list; an attacker with `top_buyers[]` over time + outcomes can reconstruct the reputation index |
| `first_buyer` | Single wallet (usually the mint creator) | Moderate — creator wallets are partially recoverable from Solana RPC but our index value-adds the historical attribution |
| `creator_history.creator` | Creator wallet (same as first_buyer in most cases) | Moderate — same as above |
| `fee_delegation.delegates[].wallet` | Pump.fun creator-fee delegate wallets | Low — these are typically the dev's hot wallet, less moat-bearing than the smart-money index |
| `fee_delegation.primary_delegate` | Primary fee delegate wallet | Low — same as above |

The narrow Option A redaction (this deploy) closes the smart-money-tier exposure. **`top_buyers[]` remains the biggest single leak** — closing it is a methodology call that depends on whether the user wants to (a) ship a tighter follow-up (Option B), (b) keep `top_buyers[]` exposed for transparency reasons, or (c) tier the redaction differently. Deferred to user.

---

## Frontend behavior under each broader-redaction option

Verified by grepping `web/static/app.js` for usage:

| Field | Frontend usage | Behavior if redacted to `[]` / `null` |
|---|---|---|
| `top_buyers[]` (app.js:1025) | `(m.top_buyers || []).slice(0, 6).map(...)` to render badges | Empty array → no badges. Graceful. |
| `first_buyer` (app.js:420) | `if (m.first_buyer) { ... }` to show "first buyer" hint | Null → hint not rendered. Graceful. |
| `creator_history.creator` (app.js:331, 1083, 1233) | Tooltip text + fallback display | Null → tooltip text uses fallback wording. Graceful. |

**No dashboard breakage from broader redaction.** Whichever scope the user picks for a follow-up, the frontend already handles graceful degradation.

---

## Public commit chain

| Commit | Action |
|---|---|
| `34ce847` Audit 09 results | Empirical validation of the wallet-index-as-moat hypothesis (7.37× grad-rate lift, 1.70× 2x-runner lift, monotonic peak_mult trend) |
| **(this commit) Wallet redaction Option A deploy receipt** | Field-level redaction of `smart_money_examples` + `clustered_wallets`; backward-compatible; aggregates preserved; broader leak surfaces inventoried for user-owned follow-up decision |
| (pending user-owned) Broader redaction OR no-redaction commit | Whichever the user calls for the remaining wallet-shaped surfaces |

---

## Source modifications (pump-jito-sniper, not version-controlled but documented here)

- `web/main.py` near line 746 — `m_out["smart_money_examples"] = []` with comment referencing Audit 09 + memory rule
- `web/main.py` near line 765 — `_cluster = cluster_intel.cluster_signal(...)`; `_cluster["clustered_wallets"] = []`; assignment to `m_out["cluster"]`

Comments embedded reference the audit verdict commit (`34ce847`) and the memory rule, so any future code reader hits the rationale before considering a revert.

---

## Cross-references

- [`audit_09_smart_money_lift_results.md`](audit_09_smart_money_lift_results.md) — empirical validation; Option 3 recommendation
- Memory: `project_wallet_index_is_the_moat.md` — strategic decision
- Memory: `feedback_methodology_calls_user_owned.md` — broader-scope decision deferred to user
