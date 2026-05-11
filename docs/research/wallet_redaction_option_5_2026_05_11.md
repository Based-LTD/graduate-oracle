# Wallet redaction — Option 5 (broader scope) deploy receipt

**Deploy:** 2026-05-11T05:39:57Z → 2026-05-11T05:41:22Z UTC (epoch 1778477997 → 1778478082). `flyctl deploy --app graduate-oracle --remote-only`. ~85s, rolling-update, single machine, smoke + health passed.

**Predecessor:** [Option A deploy receipt (`06480be`)](wallet_redaction_2026_05_11.md) — closed 2 of 7 wallet-shaped surfaces (`smart_money_examples`, `clustered_wallets`).

**This deploy closes the remaining 5 surfaces.** All 7 wallet-shaped fields in `/api/live` now redacted; the wallet-reputation index that Audit 09 empirically validated as the load-bearing moat is no longer reconstructable from API responses.

---

## What this deploy redacted (Option 5 — broader scope)

| Field | Before | After |
|---|---|---|
| `top_buyers[]` | List of top-buyer wallet addresses (5–10 per mint) | `[]` |
| `first_buyer` | Single wallet address (chronologically first buyer) | `null` |
| `creator_history.creator` | Creator wallet (same as `first_buyer` in most cases) | `null` |
| `fee_delegation.primary_delegate` | Primary fee-delegate wallet | `null` |
| `fee_delegation.delegates[].wallet` | List of fee-delegate wallets per mint | `null` (per delegate) |

**Field names preserved + container types preserved** (lists stay lists, dicts stay dicts) — same backward-compat shape as Option A. Consumer code iterating over empty arrays or null-checking nullable fields handles redaction gracefully.

**Aggregate metrics preserved (the signal layers the receipts trail and product depend on):**

- `smart_money_in` (count of leaderboard wallets in top buyers)
- `cluster.{n_clustered_pairs, cluster_density, max_pair_count}` (cluster strength aggregates)
- `creator_history.{n_launches, grad_rate, rate_5x, runner_creator, good_creator}` (creator-stats aggregates)
- `fee_delegation.{n_delegates, total_bps, is_fully_delegated}` (delegation strength aggregates)
- `wallet_balance.{n_known_balance, n_whale_wallets, avg_buyer_sol, max_buyer_sol}` (wallet-balance aggregates)

---

## Live verification (post-deploy `GET /api/live?limit=2` at 2026-05-11T05:42Z)

Both sampled mints show full redaction across all 7 surfaces:

```
sample mint 1:  EkHkqbJc..
sample mint 2:  2EBbDgY2..

REDACTED:
  smart_money_examples:           []
  cluster.clustered_wallets:      []
  top_buyers:                     []
  first_buyer:                    None
  creator_history.creator:        None
  fee_delegation.primary_delegate: None

AGGREGATES PRESERVED (creator_history fields are null on these two mints
because neither creator has a prior-launch hit in the index — empty is
expected, not redaction-related):
  creator_history.n_launches:     None (no history hit)
  fee_delegation.* aggregates:    structure intact
```

**Wallet-shaped-string scan across both mints: NO remaining surfaces.** The `mint` field (mint address, not wallet) is the only base58 string in the response, as expected.

---

## Placement discipline

The redaction block runs in `web/main.py` `_enrich_mint()` **BEFORE** `alert_push.maybe_push(m_out)`. This ensures:

1. **`/api/live` API response is redacted** (read by the dashboard + paid integrators)
2. **Persisted snapshot file is redacted** (read by the TG bot, which renders alerts to subscribers)

Both consumer paths see the redacted values. The TG alert path is a paid-subscriber surface — closing it under the same redaction is consistent with the "redact from all public surfaces" intent.

Internal computations (cluster signal, wallet_balance enrichment, creator_history lookup, fee_delegation enrichment) all complete BEFORE the redaction block using the LIVE values from the local `top_buyers` variable and `m` dict — those internal flows are untouched.

---

## Backward-compatibility verification

Frontend dependencies on each redacted field (per `web/static/app.js` grep, pre-deploy):

| Field | Frontend usage | Behavior with redacted value |
|---|---|---|
| `top_buyers[]` (app.js:1025) | `(m.top_buyers || []).slice(0, 6).map(...)` to render badges | Empty array → 0 badges. Graceful. |
| `first_buyer` (app.js:420) | `if (m.first_buyer) { ... }` to show "first buyer" hint | `null` → hint not rendered. Graceful. |
| `creator_history.creator` (app.js:331, 1083, 1233) | Tooltip text + fallback display via `c.creator` | `null` → tooltip uses fallback wording (creator-stats aggregates still render: `n_launches`, `grad_rate`, etc.). Graceful. |
| `fee_delegation.primary_delegate` / `delegates[].wallet` | Not directly read in app.js (aggregates only) | No frontend impact. |

**No dashboard breakage.** Verified by visual inspection of frontend code; live dashboard test pending user confirmation but no regression expected.

---

## Source modifications (pump-jito-sniper, not version-controlled but documented here)

Single consolidated edit block in `web/main.py` `_enrich_mint()` just before `alert_push.maybe_push(m_out)`:

```python
# --- Wallet redaction (Option 5 — broader scope) ---
# [comment referencing audit + memory rule + Option A precedent]
m_out["top_buyers"]  = []
m_out["first_buyer"] = None
if isinstance(m_out.get("creator_history"), dict):
    m_out["creator_history"]["creator"] = None
if isinstance(m_out.get("fee_delegation"), dict):
    m_out["fee_delegation"]["primary_delegate"] = None
    _delegates = m_out["fee_delegation"].get("delegates")
    if isinstance(_delegates, list):
        for _d in _delegates:
            if isinstance(_d, dict):
                _d["wallet"] = None
```

Comments embedded reference the audit verdict commit (`34ce847`), the Option A receipt (`06480be`), and the memory rule (`project_wallet_index_is_the_moat.md`).

---

## Public commit chain (complete wallet-redaction arc)

| Commit | Action |
|---|---|
| `34ce847` Audit 09 results | Empirical validation: 7.37× grad lift, 1.70× 2x lift, monotonic peak_mult trend |
| `06480be` Wallet redaction Option A deploy receipt | Field-level redaction (`smart_money_examples` + `clustered_wallets`); 2 of 7 surfaces |
| **(this commit) Wallet redaction Option 5 deploy receipt** | Broader scope (`top_buyers`, `first_buyer`, `creator_history.creator`, `fee_delegation.primary_delegate` + `delegates[].wallet`); remaining 5 of 7 surfaces. **All 7 wallet-shaped surfaces in /api/live now closed.** |

The wallet-reputation index that Audit 09 validated as the moat is no longer reconstructable from API responses. The composite signal (smart_money_in count, cluster density, creator stats) remains publicly exposed at the aggregate level — that's what the receipts trail relies on and what the product surfaces to users.

---

## Cross-references

- [`wallet_redaction_2026_05_11.md`](wallet_redaction_2026_05_11.md) — Option A receipt
- [`audit_09_smart_money_lift_results.md`](audit_09_smart_money_lift_results.md) — empirical foundation
- Memory: `project_wallet_index_is_the_moat.md` — strategic decision (2026-05-10)
- Memory: `feedback_methodology_calls_user_owned.md` — broader scope was user-greenlit (2026-05-11 message: "GREENLIGHT from me. Implementer can ship this as a second commit today.")
