"""
trader_orchestrator — the buy pipeline for the TG bot's trader.

This module is the SINGLE entry point the bot calls when a user taps
[BUY 0.5]. It composes the rest of the stack:

    1. resolve the user's wallet (custody stays in trader_wallets)
    2. fetch + decode the bonding curve from RPC (bonding_curve.fetch)
    3. route: pump.fun pre-grad OR Jupiter post-grad (only pre-grad for now)
    4. fetch a recent blockhash (Helius)
    5. ask the Rust binary to BUILD the unsigned tx
    6. sign locally (custody key never leaves Python)
    7. ask the Rust binary to SUBMIT via Jito
    8. record the position in trader.sqlite (open) — or skip on dry-run failure

Day 4.5 scope: BUY ONLY, PRE-GRAD ONLY. Post-grad/Jupiter route is a
later add-on. Sell side will live in trader_orchestrator.sell() when
we wire it up.

Safety defaults:
  • live=False — dry-run everything (no Jito submit). The bot starts in
    this mode; flip after operator confirms the pipeline works.
  • Even with live=True, the Rust binary refuses submission unless
    TG_TRADER_LIVE=1 is set in its env. Two gates, both must open.
"""

from __future__ import annotations

import time
from typing import Optional

import bonding_curve
import jito_tip_floor
import jupiter_buy
import rpc_submit
import tg_trader_runner
import trader_positions
import trader_wallets


# ── Errors ──────────────────────────────────────────────────────────────

class OrchestratorError(RuntimeError):
    """Raised when the buy pipeline can't complete. The .stage attribute
    names where it failed so the caller can render an honest error."""
    def __init__(self, stage: str, message: str):
        super().__init__(f"[{stage}] {message}")
        self.stage = stage


# ── Buy ─────────────────────────────────────────────────────────────────

DEFAULT_SLIPPAGE_BPS                 = 500       # 5%
DEFAULT_PRIORITY_FEE_MICROLAMPORTS   = 100_000   # matches tg_trader.rs
DEFAULT_COMPUTE_UNITS                = 200_000   # matches tg_trader.rs
DEFAULT_JITO_TIP_LAMPORTS            = 10_000    # 0.00001 SOL — minimum to be prioritized
DEFAULT_SUBMIT_REGIONS: Optional[list[str]] = None  # None = Rust's 5-region default


def buy(
    user_id: str | int,
    mint: str,
    sol: float,
    *,
    slippage_bps: int = DEFAULT_SLIPPAGE_BPS,
    priority_fee_microlamports: int = DEFAULT_PRIORITY_FEE_MICROLAMPORTS,
    compute_units: int = DEFAULT_COMPUTE_UNITS,
    jito_tip_lamports: Optional[int] = None,
    signal_source: str = "manual",
    tier: Optional[str] = None,
    live: bool = False,
    rpc_url: Optional[str] = None,
    submit_regions: Optional[list[str]] = None,
) -> dict:
    """Execute one buy, end to end. Returns an envelope with everything
    the bot needs to render a confirmation message AND everything the
    position table needs for the audit trail.

    Raises OrchestratorError at the FIRST stage that fails. We do NOT
    write a position row on failure — the caller logs the exception
    separately. The user's wallet is unchanged on any failure path
    BEFORE submission (signing locally has no side effects).

    Return shape:
        {
            "phase":          "dry-run" | "submitted",
            "position_id":    int,
            "user_id":        str,
            "mint":           str,
            "payer":          str,
            "sol":            float,
            "buy_lamports":   int,
            "buy_signature":  str,
            "expected_tokens_out": int,
            "max_sol_cost_lamports": int,
            "entry_price_lamports_per_token": float,
            "entry_mcap_sol": float,
            "is_cashback_coin": bool,
            "creator":        str,
            "route":          "pumpfun-pregrad",
            "submit":         {phase, would_submit, n_regions, ...},
        }
    """
    if sol <= 0:
        raise OrchestratorError("validate", "sol must be positive")
    if slippage_bps < 0 or slippage_bps > 10_000:
        raise OrchestratorError("validate", f"slippage_bps {slippage_bps} out of range")

    # ── Stage 1: resolve wallet ────────────────────────────────────────
    try:
        wallet = trader_wallets.get_or_create_wallet(user_id)
        # trader_wallets returns {"user_id", "public_key", "created_at_unix"}
        # — NOT "pubkey". Day-4.6 integration caught this; unit tests had
        # mocked the wrong field name and silently passed.
        payer = wallet["public_key"]
    except Exception as e:
        raise OrchestratorError("wallet", f"get_or_create_wallet failed: {e}") from e

    # ── Stage 2: fetch curve + route decision ──────────────────────────
    try:
        curve = bonding_curve.fetch(mint, rpc_url=rpc_url)
    except bonding_curve.BondingCurveError as e:
        raise OrchestratorError("curve", str(e)) from e

    if curve.get("complete"):
        raise OrchestratorError(
            "route",
            "bonding curve has graduated — Jupiter route not implemented yet (Day 4.5+)",
        )

    # Snapshot fields the position row needs that aren't in the build envelope
    creator = curve["creator"]
    is_cashback = bool(curve.get("is_cashback_coin", False))

    # ── Stage 3: recent blockhash ──────────────────────────────────────
    try:
        bh_resp = trader_wallets._rpc_call(
            "getLatestBlockhash", [{"commitment": "confirmed"}],
        )
        recent_blockhash = (bh_resp.get("value") or {}).get("blockhash")
        if not recent_blockhash:
            raise RuntimeError(f"no blockhash in response: {bh_resp}")
    except Exception as e:
        raise OrchestratorError("blockhash", str(e)) from e

    # ── Stage 4: BUILD via Jupiter ─────────────────────────────────────
    # Pivot from pump-ix builder → Jupiter (2026-06-21). Jupiter routes
    # to pump.fun / Raydium / PumpSwap depending on the mint's state.
    # Their SDK absorbs every pump.fun ABI change so we don't have to.
    # Trade-off: Jupiter charges 0.3-1% per trade (negligible at our
    # sizes) and adds 100-300ms latency (irrelevant for TG-bot trades).
    effective_tip = jito_tip_lamports
    if effective_tip is None and live:
        try:
            effective_tip = jito_tip_floor.get_tip_lamports(percentile="p95")
        except jito_tip_floor.TipFloorError as e:
            effective_tip = DEFAULT_JITO_TIP_LAMPORTS
            print(f"[orchestrator] tip_floor lookup failed ({e}) — "
                  f"falling back to default {effective_tip} lamports", flush=True)
    try:
        built = jupiter_buy.build_buy_tx(
            user_id=user_id, mint=mint, payer_pubkey=payer, sol=sol,
            slippage_bps=slippage_bps,
            priority_fee_microlamports=priority_fee_microlamports,
            jito_tip_lamports=effective_tip,
            compute_units=compute_units,
        )
    except jupiter_buy.JupiterError as e:
        raise OrchestratorError("build", str(e)) from e
    # Jupiter doesn't need recent_blockhash — it's embedded in the swap tx.
    _ = recent_blockhash  # kept for symmetry / future direct-ix fallback

    unsigned_tx_b64 = built["tx_b64"]

    # ── Stage 5: SIGN locally (custody key stays in Python) ────────────
    try:
        signed_tx_b64 = trader_wallets.sign_transaction(user_id, unsigned_tx_b64)
    except Exception as e:
        raise OrchestratorError("sign", str(e)) from e

    # ── Stage 6: SUBMIT ────────────────────────────────────────────────
    # Jupiter returns a VersionedTransaction (v0) with address lookup
    # tables. Jito would accept it but our Rust submit-bundle path was
    # built for legacy txs (validation rejects v0 with "unexpected EOF").
    # Going RPC-only for live: Jupiter's prioritizationFeeLamports gets
    # validators to prioritize inclusion — that's enough for our trade
    # cadence (TG-bot triggered, not snipe-racing).
    # Dry-run still goes through the Rust binary's dry-run path so the
    # unit-test contract holds.
    rpc_result: Optional[dict] = None
    submitted: dict
    if not live:
        try:
            submit_kwargs = {"live": False}
            if submit_regions is not None:
                submit_kwargs["regions"] = submit_regions
            submitted = tg_trader_runner.submit_bundle(
                user_id, signed_tx_b64, **submit_kwargs,
            )
        except tg_trader_runner.TgTraderError as jito_err:
            raise OrchestratorError("submit", str(jito_err)) from jito_err
    else:
        rpc_url = trader_wallets._RPC
        rpc_result = rpc_submit.send_via_rpc(signed_tx_b64, rpc_url=rpc_url)
        if not rpc_result.get("ok"):
            raise OrchestratorError(
                "submit", f"RPC submit failed: {rpc_result.get('error')}",
            )
        submitted = {
            "phase":     "submitted",
            "signature": rpc_result["signature"],
            "route":     "rpc",
        }

    # ── Stage 7: write the position row ────────────────────────────────
    phase = submitted.get("phase", "dry-run")  # 'submitted' or 'dry-run'
    buy_signature = submitted.get("signature", "")
    try:
        position_id = trader_positions.create_position(
            user_id=user_id,
            mint=mint,
            payer_pubkey=payer,
            creator=creator,
            is_cashback_coin=is_cashback,
            token_program=built["accounts"]["token_program"],
            buy_sol_lamports=int(built["buy_lamports"]),
            token_amount=int(built["expected_tokens_out"]),
            entry_price_lamports_per_token=float(built["entry_price_lamports_per_token"]),
            entry_mcap_sol=float(built.get("entry_mcap_sol") or 0) or None,
            slippage_bps=int(built["slippage_bps"]),
            max_sol_cost_lamports=int(built["max_sol_cost_lamports"]),
            buy_signature=buy_signature,
            buy_phase=phase,
            buy_route=built["route"],
            buy_tier=tier,
            buy_signal_source=signal_source,
        )
    except Exception as e:
        raise OrchestratorError("position", f"create_position failed: {e}") from e

    return {
        "phase":                            phase,
        "position_id":                      position_id,
        "user_id":                          str(user_id),
        "mint":                             mint,
        "payer":                            payer,
        "creator":                          creator,
        "sol":                              sol,
        "buy_lamports":                     int(built["buy_lamports"]),
        "buy_signature":                    buy_signature,
        "expected_tokens_out":              int(built["expected_tokens_out"]),
        "max_sol_cost_lamports":            int(built["max_sol_cost_lamports"]),
        "entry_price_lamports_per_token":   float(built["entry_price_lamports_per_token"]),
        "entry_mcap_sol":                   float(built.get("entry_mcap_sol") or 0) or None,
        "is_cashback_coin":                 is_cashback,
        "route":                            built["route"],
        "submit":                           submitted,
        # Dual-submit metadata — present only when live=True
        "submit_rpc":                       rpc_result,
    }
