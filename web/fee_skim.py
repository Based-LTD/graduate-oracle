"""
fee_skim — collect the 1% trading fee, split 50/50 operator + $GO buyback.

Model: user pays 1% on top of their trade size (additive, not subtractive).
If they /buy 0.5 SOL, total wallet debit is ~0.505 SOL — 0.5 to Jupiter
+ 0.0025 to the operator fee wallet + 0.0025 to the $GO buyback wallet.

Why post-trade (separate tx) instead of inline (same tx)?
  • Jupiter swaps are v0 VersionedTransactions with address lookup tables.
    Adding our own ix to them is non-trivial.
  • Jupiter's built-in feeAccount param supports ONE recipient, not a
    50/50 split.
  • Post-trade transfer means the user's actual trade (the swap) never
    fails because of fee-collection bugs — clean separation of concerns.

The orchestrator calls apply_buy_fee / apply_sell_fee AFTER on-chain
confirmation. If the fee transfer itself fails, we log + return an
error but do NOT raise — the trade succeeded and the user shouldn't
see a scary error.

Operator pubkey + buyback pubkey come from env (FEE_OPERATOR_WALLET,
FEE_BUYBACK_WALLET). These are NEVER hardcoded — operator may rotate.
On startup or first call, if env vars aren't set, fee skim is DISABLED
and the orchestrator logs that fact. This keeps tests, dev, and pre-fee
deploys working.

IMPORTANT — Solana rent constraint:
  Recipients MUST be rent-exempt accounts (~890_880 lamports minimum).
  A fresh, empty pubkey will REJECT the first tiny transfer with
  `InsufficientFundsForRent`. Production fee recipients (operator's
  main wallet, $GO buyback program account) are always pre-funded
  and rent-exempt. If you set FEE_*_WALLET to a brand-new pubkey for
  testing, fund it once with 0.002 SOL first.
"""

from __future__ import annotations

import os
from typing import Optional


# Total fee in basis points. 100 = 1.00%. Configurable for promo windows.
DEFAULT_FEE_BPS = int(os.environ.get("TRADER_FEE_BPS", "100"))

# Split: half to operator, half to $GO buyback. Sums to FEE_BPS.
# Configurable so we can tune (e.g. 30/70 in favor of buyback later).
OPERATOR_SHARE_PCT = float(os.environ.get("FEE_OPERATOR_SHARE_PCT", "0.5"))


def is_enabled() -> bool:
    """Fee skim is OPT-IN — only active when both wallet pubkeys are set.
    Without them, every call returns 'disabled' and the orchestrator
    treats it as a no-op."""
    return bool(_operator_wallet()) and bool(_buyback_wallet())


def _operator_wallet() -> str:
    return (os.environ.get("FEE_OPERATOR_WALLET") or "").strip()


def _buyback_wallet() -> str:
    return (os.environ.get("FEE_BUYBACK_WALLET") or "").strip()


def compute_fee_split(trade_sol_lamports: int, *,
                      fee_bps: int = DEFAULT_FEE_BPS,
                      operator_share_pct: float = OPERATOR_SHARE_PCT) -> dict:
    """Pure function: how much fee, split how?

    Returns:
        {
          total_fee_lamports,
          operator_fee_lamports,
          buyback_fee_lamports,
          fee_bps_applied,
        }

    Lamports — integer math throughout. Any rounding leftover goes to
    the buyback side (slightly favors $GO).
    """
    if trade_sol_lamports <= 0:
        return {"total_fee_lamports": 0, "operator_fee_lamports": 0,
                "buyback_fee_lamports": 0, "fee_bps_applied": fee_bps}
    if fee_bps < 0 or fee_bps > 10_000:
        raise ValueError(f"fee_bps {fee_bps} out of range [0, 10000]")
    if not (0 <= operator_share_pct <= 1):
        raise ValueError(f"operator_share_pct {operator_share_pct} must be in [0, 1]")

    total = int(trade_sol_lamports * fee_bps / 10_000)
    op = int(total * operator_share_pct)
    bb = total - op  # remainder goes to buyback (handles rounding)
    return {
        "total_fee_lamports":    total,
        "operator_fee_lamports": op,
        "buyback_fee_lamports":  bb,
        "fee_bps_applied":       fee_bps,
    }


def apply_fee(
    *,
    user_id: str | int,
    trade_sol_lamports: int,
    trade_kind: str,            # 'buy' or 'sell' — for audit / logging
    trade_signature: str,       # for cross-reference; not on-chain memo
    dry_run: bool = False,
) -> dict:
    """Send the fee transfers from `user_id`'s wallet to the operator
    and buyback wallets. Returns an audit record.

    Result envelope:
      {
        enabled: bool,
        applied: bool,
        skipped_reason: str | None,
        total_fee_lamports, operator_fee_lamports, buyback_fee_lamports,
        operator_signature: str | None,
        buyback_signature:  str | None,
        error: str | None,
      }

    Never raises — fee failures must not bubble up and scare the user
    when their actual trade succeeded. Failures get logged with the
    trade_signature for later reconciliation.
    """
    split = compute_fee_split(trade_sol_lamports)
    out = {
        "enabled":             is_enabled(),
        "applied":             False,
        "skipped_reason":      None,
        "operator_signature":  None,
        "buyback_signature":   None,
        "error":               None,
        **split,
    }

    if not is_enabled():
        out["skipped_reason"] = "fee wallets not configured (set FEE_OPERATOR_WALLET + FEE_BUYBACK_WALLET)"
        return out
    if split["total_fee_lamports"] <= 0:
        out["skipped_reason"] = "fee rounds to 0 lamports"
        return out
    if dry_run:
        out["skipped_reason"] = "dry_run=True"
        return out

    # Send the two transfers. Import lazily to avoid a circular dep
    # with trader_wallets (which doesn't depend on this module, but the
    # import dance is cleaner this way).
    try:
        import trader_wallets
        op_sig = _send_sol(trader_wallets, user_id,
                           _operator_wallet(), split["operator_fee_lamports"],
                           memo=f"fee:{trade_kind}:operator:{trade_signature[:16]}")
        bb_sig = _send_sol(trader_wallets, user_id,
                           _buyback_wallet(), split["buyback_fee_lamports"],
                           memo=f"fee:{trade_kind}:buyback:{trade_signature[:16]}")
        out["operator_signature"] = op_sig
        out["buyback_signature"]  = bb_sig
        out["applied"] = True
    except Exception as e:
        # Trade succeeded — fee transfer failed. Log + return; orchestrator
        # surfaces in the result envelope but does NOT raise.
        out["error"] = f"fee transfer failed: {e}"
        print(f"[fee_skim] FAILED for {trade_kind} sig={trade_signature[:16]}: {e}",
              flush=True)
    return out


def _send_sol(trader_wallets_module, user_id: str | int,
              dest_pubkey: str, lamports: int, *, memo: str = "") -> str:
    """Send `lamports` SOL from `user_id`'s wallet to `dest_pubkey`.
    Returns the signature. Uses trader_wallets.internal_send_sol —
    server-internal path, no password / daily-limit checks.

    Memo is informational only (not on-chain yet). Future: add a memo
    ix for tx-log searchability.
    """
    _ = memo  # placeholder for future on-chain memo ix
    return trader_wallets_module.internal_send_sol(
        user_id=str(user_id),
        to_address=dest_pubkey,
        lamports=int(lamports),
    )
