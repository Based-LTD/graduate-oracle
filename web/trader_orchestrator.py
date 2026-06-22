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

import contextlib
import time
from typing import Optional

import bonding_curve
import fee_skim
import jito_tip_floor
import jupiter_buy
import rpc_submit
import tg_trader_runner
import trader_positions
import trader_wallets


# ── Errors ──────────────────────────────────────────────────────────────

# Stage → user-facing message template. {detail} is the raw error.
# These get rendered by the TG bot directly; the bot never shows raw
# stage names or Python tracebacks to users. Keep them short, plain,
# and actionable.
_USER_FACING: dict[str, str] = {
    "validate": "Invalid trade: {detail}",
    "wallet":   "Wallet not ready. Try /start, then retry.",
    "balance":  "{detail}",  # balance error messages are already user-friendly
    "curve":    "Couldn't read this coin from the chain. Try again in a moment.",
    "route":    "{detail}",
    "blockhash": "Network is slow — try again in a few seconds.",
    "build":    "Couldn't price this trade — Jupiter route may be unavailable. Try again.",
    "build_too_new": "This mint is too new for Jupiter — try again in 30 seconds.",
    "sign":     "Wallet signing failed. Contact support.",
    "submit":   "Couldn't submit the trade. Try again or contact support.",
    "position": "{detail}",
    "post_submit_accounting":
        "Trade went through but our records didn't update. Your tokens are "
        "safe — contact support with the signature.",
}


class OrchestratorError(RuntimeError):
    """Raised when the buy or sell pipeline can't complete.

    Three attributes for callers:
      • .stage           — short identifier of where it failed (e.g. "balance")
      • .detail          — the raw underlying error message
      • .user_facing_msg — short plain-English string safe to show users

    The TG bot renders .user_facing_msg; logs use the full str() form."""
    def __init__(self, stage: str, message: str):
        super().__init__(f"[{stage}] {message}")
        self.stage = stage
        self.detail = message
        template = _USER_FACING.get(stage, "Trade failed: {detail}")
        try:
            self.user_facing_msg = template.format(detail=message)
        except Exception:
            self.user_facing_msg = "Trade failed. Please try again."


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
    # NOTE: defaults are ONLY used when the user has no settings AND the
    # caller doesn't pass explicit kwargs. The buy() body resolves the
    # effective values per the order:
    #   per-call kwarg → user settings → package default.
    slippage_bps: Optional[int] = None,
    priority_fee_microlamports: int = DEFAULT_PRIORITY_FEE_MICROLAMPORTS,
    compute_units: int = DEFAULT_COMPUTE_UNITS,
    jito_tip_lamports: Optional[int] = None,
    signal_source: str = "manual",
    tier: Optional[str] = None,
    live: bool = False,
    rpc_url: Optional[str] = None,
    submit_regions: Optional[list[str]] = None,
    tp_ladder: Optional[list] = None,
    sl_pct: Optional[float] = None,
    tsl_pct: Optional[float] = None,
    breakeven_pct: Optional[float] = None,
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

    # Resolve user settings ONCE up-front so we use them in slippage, tip,
    # and max-trade gating consistently. None values fall through to package
    # defaults via get_user_settings.
    user_settings = trader_positions.get_user_settings(user_id)
    if slippage_bps is None:
        slippage_bps = user_settings.get("slippage_bps", DEFAULT_SLIPPAGE_BPS)
    if slippage_bps < 0 or slippage_bps > 10_000:
        raise OrchestratorError("validate", f"slippage_bps {slippage_bps} out of range")

    # Safety cap — refuse single buys above the user's configured max.
    max_trade_sol = user_settings.get("max_trade_sol")
    if max_trade_sol is not None and sol > float(max_trade_sol):
        raise OrchestratorError(
            "validate",
            f"single-trade size {sol} SOL exceeds your max-trade cap "
            f"of {float(max_trade_sol):.4f} SOL. Change it in /trader → Settings.",
        )

    # ── Stage 1: resolve wallet ────────────────────────────────────────
    try:
        wallet = trader_wallets.get_or_create_wallet(user_id)
        # trader_wallets returns {"user_id", "public_key", "created_at_unix"}
        # — NOT "pubkey". Day-4.6 integration caught this; unit tests had
        # mocked the wrong field name and silently passed.
        payer = wallet["public_key"]
    except Exception as e:
        raise OrchestratorError("wallet", f"get_or_create_wallet failed: {e}") from e

    # ── Stage 1.5: pre-flight balance check ────────────────────────────
    # Refuse buys when balance can't cover the trade. Each failed-on-chain
    # tx still costs ~5_000 lamports tx fee + Jupiter priority fee burn,
    # so a customer with 0.0001 SOL trying to buy 0.01 SOL would silently
    # bleed dust on every attempt. This stage stops that.
    #
    # Headroom = sol + (sol × slippage_bps/10_000) + estimated Jito tip
    # + 50_000 lamports floor for tx fee + priority fee + ATA rent.
    if live:
        try:
            balance_lamports = trader_wallets.get_balance_lamports(payer)
        except Exception as e:
            raise OrchestratorError("balance", f"balance check failed: {e}") from e
        sol_lamports = int(sol * 1e9)
        slip_headroom = int(sol_lamports * slippage_bps / 10_000)
        # Tip estimate matches what stage 4 will use (Jito p95+20%) — but
        # cheap to look up since jito_tip_floor caches.
        if jito_tip_lamports is not None:
            tip_estimate = jito_tip_lamports
        else:
            try:
                tip_estimate = jito_tip_floor.get_tip_lamports(percentile="p95")
            except jito_tip_floor.TipFloorError:
                tip_estimate = DEFAULT_JITO_TIP_LAMPORTS
        # 50_000 lamports floor covers tx fee (5k) + ATA rent (~2M sometimes
        # but usually pre-existing) + priority fee headroom. Conservative.
        tx_overhead = 50_000
        # Include the 1% fee (if enabled) so we don't approve a trade the
        # user can complete but can't pay the fee on.
        fee_estimate = fee_skim.compute_fee_split(sol_lamports)["total_fee_lamports"] \
                       if fee_skim.is_enabled() else 0
        required = sol_lamports + slip_headroom + tip_estimate + tx_overhead + fee_estimate
        if balance_lamports < required:
            raise OrchestratorError(
                "balance",
                f"insufficient balance: have {balance_lamports} lamports "
                f"({balance_lamports/1e9:.6f} SOL), need at least {required} "
                f"({required/1e9:.6f} SOL) for buy + slippage + tip + tx overhead",
            )

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
        # Honor the user's jito_tip_mode setting: 'auto' (Jito floor),
        # 'fast' (50k), 'turbo' (200k), 'ultra' (500k). The mode → lamport
        # map lives in trader_positions.JITO_TIP_MODE_LAMPORTS; "auto"
        # returns None there and we query the floor.
        mode = user_settings.get("jito_tip_mode", "auto")
        fixed = trader_positions.JITO_TIP_MODE_LAMPORTS.get(mode)
        if fixed is not None:
            effective_tip = fixed
        else:  # "auto"
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
    except jupiter_buy.JupiterNotTradableError as e:
        # Brand-new pump.fun mints aren't in Jupiter's index for the
        # first 30-90s after launch. Surface as a clean retry-later
        # message instead of a generic build failure.
        raise OrchestratorError("build_too_new", str(e)) from e
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

        # ── Stage 7b: stamp auto-exit config onto the new position ──────
        # Resolution order:
        #   1. Explicit per-buy kwarg (tp_ladder=, sl_pct=, etc.)
        #   2. User defaults (trader_positions.get_user_settings)
        #   3. Package defaults (DEFAULT_TP_LADDER etc.)
        # We always stamp something — even manual buys get sensible
        # auto-exits unless the caller explicitly disables (e.g. tp_ladder=[]).
        user_defaults = trader_positions.get_user_settings(user_id)
        eff_ladder    = tp_ladder      if tp_ladder      is not None else user_defaults["tp_ladder"]
        eff_sl        = sl_pct         if sl_pct         is not None else user_defaults["sl_pct"]
        eff_tsl       = tsl_pct        if tsl_pct        is not None else user_defaults["tsl_pct"]
        eff_be        = breakeven_pct  if breakeven_pct  is not None else user_defaults["breakeven_pct"]
        trader_positions.set_position_auto_exit(
            position_id,
            tp_ladder=eff_ladder,
            sl_pct=eff_sl,
            tsl_pct=eff_tsl,
            breakeven_pct=eff_be,
        )

        # ── Stage 7c: stamp entry market-cap snapshot ───────────────────
        # MC = (sol_paid / tokens_received) × total_supply. The curve
        # gives us token_total_supply for pump.fun pre-grad mints; for
        # post-grad/Raydium we'd need a separate lookup (Day 4.23+ TODO).
        try:
            total_supply = int(curve.get("token_total_supply") or 0)
            entry_mcap = trader_positions.compute_mcap_lamports(
                int(built["buy_lamports"]),
                int(built["expected_tokens_out"]),
                total_supply,
            )
            if entry_mcap and total_supply:
                trader_positions.set_entry_mcap(
                    position_id,
                    entry_mcap_lamports=entry_mcap,
                    token_total_supply_raw=total_supply,
                )
        except Exception as me:
            print(f"[orchestrator] set_entry_mcap failed: {me}", flush=True)
    except Exception as e:
        raise OrchestratorError("position", f"create_position failed: {e}") from e

    # ── Stage 8: collect fee (live only, never blocks the trade) ──────
    # Skim 1% of the trade size (split 50/50 operator + $GO buyback)
    # AFTER the buy confirms. If FEE_OPERATOR_WALLET / FEE_BUYBACK_WALLET
    # aren't set, this is a no-op. If the transfer fails, we log + put
    # the error in the result envelope but don't raise — the user's
    # trade succeeded and they shouldn't see a scary error.
    fee_result = None
    if live and buy_signature:
        fee_result = fee_skim.apply_fee(
            user_id=user_id,
            trade_sol_lamports=int(built["buy_lamports"]),
            trade_kind="buy",
            trade_signature=buy_signature,
            dry_run=False,
        )
        # Stamp the fee onto the position row so the sell receipt can
        # compute honest net PnL. Best-effort — DB hiccup here doesn't
        # break the trade.
        try:
            trader_positions.set_buy_fee(
                position_id,
                int(fee_result.get("total_fee_lamports") or 0),
            )
        except Exception as fe:
            print(f"[orchestrator] set_buy_fee failed: {fe}", flush=True)

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
        "fee":                              fee_result,
    }


# ── Sell ────────────────────────────────────────────────────────────────

def sell(
    user_id: str | int,
    position_id: int,
    *,
    sell_pct: float = 1.0,
    slippage_bps: int = DEFAULT_SLIPPAGE_BPS,
    priority_fee_microlamports: int = DEFAULT_PRIORITY_FEE_MICROLAMPORTS,
    jito_tip_lamports: Optional[int] = None,
    live: bool = False,
    rpc_url: Optional[str] = None,
) -> dict:
    """Close (or partially close) a position via Jupiter (mint → SOL).

    Args:
      user_id:          owner of the position (validated)
      position_id:      row id from trader_positions
      sell_pct:         fraction of the position's token_amount to sell.
                        Default 1.0 = full close. 0.5 = half. Must be in
                        (0, 1].
      slippage_bps:     500 = 5%
      jito_tip_lamports: live tips come from jito_tip_floor.p95 if None
      live:             False = dry-run (validation + Jupiter quote only,
                        no submission). True = real submission via RPC.

    Returns a result envelope similar to buy(): phase, signature,
    expected_sol_out_lamports, actual route, position_id.

    Raises OrchestratorError(stage=...) at the first failing stage. The
    position row is updated to status='sold' ONLY on successful live
    submission. Dry-runs do not write.
    """
    if not (0 < sell_pct <= 1.0):
        raise OrchestratorError("validate", f"sell_pct {sell_pct} must be in (0, 1]")
    if slippage_bps < 0 or slippage_bps > 10_000:
        raise OrchestratorError("validate", f"slippage_bps {slippage_bps} out of range")

    # ── Stage 1: load position ────────────────────────────────────────
    pos = trader_positions.get_position(int(position_id))
    if pos is None:
        raise OrchestratorError("position", f"no position with id={position_id}")
    if str(pos["user_id"]) != str(user_id):
        raise OrchestratorError("position",
            f"position {position_id} belongs to {pos['user_id']!r}, not {user_id!r}")
    if pos["status"] != "open":
        raise OrchestratorError("position",
            f"position {position_id} is {pos['status']!r}, not open — already sold/failed")

    mint = pos["mint"]
    payer = pos["payer_pubkey"]
    tokens_to_sell = int(pos["token_amount"] * sell_pct)
    if tokens_to_sell <= 0:
        raise OrchestratorError("validate",
            f"computed 0 tokens to sell (position has {pos['token_amount']}, pct {sell_pct})")

    # ── Stage 2: wallet sanity check ──────────────────────────────────
    try:
        wallet = trader_wallets.get_or_create_wallet(user_id)
        if wallet["public_key"] != payer:
            raise OrchestratorError("wallet",
                f"position payer {payer!r} doesn't match wallet pubkey "
                f"{wallet['public_key']!r}")
    except OrchestratorError:
        raise
    except Exception as e:
        raise OrchestratorError("wallet", f"wallet lookup failed: {e}") from e

    # ── Stage 3: tip strategy (live only) ──────────────────────────────
    effective_tip = jito_tip_lamports
    if effective_tip is None and live:
        try:
            effective_tip = jito_tip_floor.get_tip_lamports(percentile="p95")
        except jito_tip_floor.TipFloorError:
            effective_tip = DEFAULT_JITO_TIP_LAMPORTS

    # ── Stage 4: BUILD via Jupiter ─────────────────────────────────────
    try:
        built = jupiter_buy.build_sell_tx(
            user_id=user_id, mint=mint, payer_pubkey=payer,
            token_amount=tokens_to_sell,
            slippage_bps=slippage_bps,
            priority_fee_microlamports=priority_fee_microlamports,
            jito_tip_lamports=effective_tip,
        )
    except jupiter_buy.JupiterError as e:
        raise OrchestratorError("build", str(e)) from e

    unsigned_tx_b64 = built["tx_b64"]

    # ── Stage 5: SIGN ──────────────────────────────────────────────────
    try:
        signed_tx_b64 = trader_wallets.sign_transaction(user_id, unsigned_tx_b64)
    except Exception as e:
        raise OrchestratorError("sign", str(e)) from e

    # ── Stage 6: SUBMIT ────────────────────────────────────────────────
    if not live:
        # Dry-run: don't hit RPC, don't mutate position.
        return {
            "phase":                       "dry-run",
            "position_id":                 int(position_id),
            "user_id":                     str(user_id),
            "mint":                        mint,
            "tokens_sold":                 tokens_to_sell,
            "sell_pct":                    sell_pct,
            "expected_sol_out_lamports":   int(built["expected_sol_out_lamports"]),
            "min_sol_out_lamports":        int(built["min_sol_out_lamports"]),
            "exit_price_lamports_per_token": float(built["exit_price_lamports_per_token"]),
            "route":                       built["route"],
            "would_submit":                False,
        }

    rpc_url_eff = (rpc_url or trader_wallets._RPC)
    rpc_result = rpc_submit.send_via_rpc(signed_tx_b64, rpc_url=rpc_url_eff)
    if not rpc_result.get("ok"):
        raise OrchestratorError(
            "submit", f"RPC sell submit failed: {rpc_result.get('error')}",
        )
    sell_signature = rpc_result["signature"]

    # ── Stage 6.5: collect sell-side fee BEFORE we mark sold ──────────
    # Need the fee number to compute honest net PnL in mark_sold.
    sell_fee_result = fee_skim.apply_fee(
        user_id=user_id,
        trade_sol_lamports=int(built["expected_sol_out_lamports"]),
        trade_kind="sell",
        trade_signature=sell_signature,
        dry_run=False,
    )
    sell_fee_lamports = int((sell_fee_result or {}).get("total_fee_lamports") or 0)

    # Stamp exit MC snapshot. token_total_supply was captured at buy time.
    try:
        total_supply = int(pos.get("token_total_supply") or 0)
        if total_supply > 0:
            exit_mcap = trader_positions.compute_mcap_lamports(
                int(built["expected_sol_out_lamports"]),
                int(tokens_to_sell),
                total_supply,
            )
            if exit_mcap:
                trader_positions.set_exit_mcap(int(position_id), exit_mcap)
    except Exception as me:
        print(f"[orchestrator] set_exit_mcap failed: {me}", flush=True)

    # ── Stage 7: mark position as sold ─────────────────────────────────
    # Important: we use the EXPECTED out lamports for PnL accounting NOW.
    # The actual realized amount can be reconciled later by polling the
    # tx logs (added in Day 4.9 — graceful error envelope).
    try:
        if sell_pct >= 1.0:
            trader_positions.mark_sold(
                int(position_id),
                sell_signature=sell_signature,
                sell_sol_lamports=int(built["expected_sol_out_lamports"]),
                sell_fee_lamports=sell_fee_lamports,
            )
            new_status = "sold"
        else:
            # Partial sell: don't close the position. Reduce token_amount
            # by the sold portion and leave status='open'. (Day 4.9+ may
            # add partial_sell tracking on a separate table.)
            remaining = pos["token_amount"] - tokens_to_sell
            with contextlib.closing(_open_positions_db()) as c, c:
                c.execute(
                    "UPDATE trader_positions SET token_amount = ? WHERE id = ?",
                    (remaining, int(position_id)),
                )
            new_status = "open"
    except Exception as e:
        # Submission already happened — surface but don't raise so the
        # caller knows the tx landed. Use a dedicated stage so the bot
        # can render "tx ok, accounting failed" honestly.
        raise OrchestratorError(
            "post_submit_accounting",
            f"sell tx {sell_signature} landed but DB update failed: {e}",
        ) from e

    # Fee result was computed BEFORE mark_sold so net PnL accounting
    # could include it. Just pass it through to the caller.
    fee_result = sell_fee_result

    return {
        "phase":                       "submitted",
        "position_id":                 int(position_id),
        "user_id":                     str(user_id),
        "mint":                        mint,
        "tokens_sold":                 tokens_to_sell,
        "sell_pct":                    sell_pct,
        "sell_signature":              sell_signature,
        "expected_sol_out_lamports":   int(built["expected_sol_out_lamports"]),
        "min_sol_out_lamports":        int(built["min_sol_out_lamports"]),
        "exit_price_lamports_per_token": float(built["exit_price_lamports_per_token"]),
        "route":                       built["route"],
        "new_status":                  new_status,
        "would_submit":                True,
        "submit_rpc":                  rpc_result,
        "fee":                         fee_result,
    }


def _open_positions_db():
    """Direct sqlite connection to the trader DB for sell-side updates
    that don't have first-class API in trader_positions yet."""
    import sqlite3
    return sqlite3.connect(str(trader_positions._db_path()), timeout=10)
