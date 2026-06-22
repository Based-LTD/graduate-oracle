"""
wallet_truth — read the ACTUAL on-chain SOL deltas for a trade.

Receipts measure the swap leg (Jupiter quote in/out + our fee skim), which
is the truth of *the swap*. But the wallet's reality includes:

    • Solana base tx fee (5_000 lamports per signed tx)
    • Compute-budget priority fee
    • Jito tip transfer (sent to a tip account in the same tx)
    • ATA rent (one-time on first interaction with a new mint)
    • Slippage gap (Jupiter QUOTED outAmount vs ACTUAL on-chain outAmount)

This module reads `getTransaction(sig)` and returns the wallet's net SOL
delta from that tx — i.e. the only number that matches the user's
balance change. The fee-skim transfers happen in separate txs and are
accounted for separately via the stored sell_fee_lamports column.

Design constraints:
    • CALLED AT RECEIPT-RENDER TIME ONLY. Never on the hot path of a
      trade submission. Adds ~150ms but the tx has already confirmed.
    • Best-effort. Failures return None — the receipt falls back to
      the Jupiter-quote-based numbers it already shows.
    • The payer wallet is always account index 0 in a Solana tx
      (Solana enforces this).
"""

from __future__ import annotations

from typing import Optional

import trader_wallets


def fetch_payer_sol_delta_lamports(signature: str) -> Optional[int]:
    """Return (post - pre) SOL balance for the fee payer in this tx,
    in lamports. Positive = wallet gained SOL (typical for sell);
    negative = wallet lost SOL (typical for buy). Returns None on any
    RPC error or if the tx isn't found."""
    if not signature:
        return None
    try:
        resp = trader_wallets._rpc_call(
            "getTransaction",
            [
                signature,
                {
                    "encoding":                       "json",
                    "commitment":                     "confirmed",
                    "maxSupportedTransactionVersion": 0,
                },
            ],
            timeout=4.0,
        )
    except Exception as e:
        print(f"[wallet_truth] getTransaction({signature[:12]}…) failed: {e}",
              flush=True)
        return None

    result = (resp or {}).get("result")
    if not result:
        return None
    meta = result.get("meta") or {}
    pre = meta.get("preBalances") or []
    post = meta.get("postBalances") or []
    if not pre or not post:
        return None
    # Account index 0 is always the fee payer in a Solana tx
    try:
        return int(post[0]) - int(pre[0])
    except (IndexError, TypeError, ValueError):
        return None
