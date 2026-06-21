"""
jupiter_buy — buy a pump.fun mint via Jupiter's swap API.

Why Jupiter instead of building pump.fun ix directly:
  • Pump.fun ships breaking ABI changes (account order, new required
    accounts like buyback_fee_recipient). Building the ix ourselves
    means chasing those changes forever.
  • Same Jupiter call handles pre-grad pump.fun AND post-grad
    Raydium/PumpSwap. One code path, two markets.
  • Industry standard — Trojan, Photon, Axiom all route through Jupiter.

API flow (Jupiter v1):
  1. POST/GET /swap/v1/quote — quote the swap, get route + outAmount
  2. POST     /swap/v1/swap  — get a base64 VersionedTransaction signed
                              for the given user pubkey

Both endpoints are free, no API key required for typical usage.

Returns shape mirrors tg_trader_runner.build_buy_tx so the orchestrator
swap is minimal:
  {
    route, tx_b64, buy_lamports, slippage_bps, max_sol_cost_lamports,
    expected_tokens_out, entry_price_lamports_per_token, accounts: {...},
    jupiter_route, price_impact_pct, jupiter_quote
  }
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Optional


# Both endpoints serve the same data. `lite-api` doesn't require an API key
# and has higher rate limits for unauthenticated traffic. We default to it.
JUP_BASE = os.environ.get("JUP_API_BASE", "https://lite-api.jup.ag").rstrip("/")

WSOL_MINT = "So11111111111111111111111111111111111111112"


class JupiterError(RuntimeError):
    """Raised when Jupiter's quote or swap endpoint fails or returns a
    malformed response."""


def _http_json(method: str, url: str, *,
               body: Optional[dict] = None,
               timeout_s: float = 6.0,
               retries: int = 2,
               backoff_base_s: float = 0.5) -> dict:
    """POST/GET JSON with retry on transient failures."""
    last_err: Optional[Exception] = None
    for attempt in range(retries + 1):
        if attempt > 0:
            time.sleep(backoff_base_s * (2 ** (attempt - 1)))
        try:
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(
                url, data=data, method=method,
                headers={
                    "Content-Type": "application/json",
                    "Accept":       "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout_s) as r:
                payload = r.read()
            resp = json.loads(payload)
            return resp
        except Exception as e:
            last_err = e
            continue
    raise JupiterError(f"{method} {url} failed after {retries+1} attempts: {last_err}")


def quote(
    *,
    mint: str,
    sol_lamports: int,
    slippage_bps: int = 500,
    only_direct_routes: bool = True,
    timeout_s: float = 4.0,
) -> dict:
    """Get a Jupiter swap quote (SOL → mint).

    Args:
      mint:                output mint (the pump.fun token)
      sol_lamports:        how much SOL (in lamports) to swap in
      slippage_bps:        max slippage in basis points (500 = 5%)
      only_direct_routes:  prefer a single-hop route for speed/simplicity.
                            Set False to allow multi-hop (rarely needed for
                            pump.fun, which is direct).
    """
    params = {
        "inputMint":         WSOL_MINT,
        "outputMint":        mint.strip(),
        "amount":            str(int(sol_lamports)),
        "slippageBps":       str(int(slippage_bps)),
        "swapMode":          "ExactIn",
        "onlyDirectRoutes":  "true" if only_direct_routes else "false",
        # Restrict to pump.fun-relevant DEXes so we don't get rerouted
        # through obscure pools mid-flight. Comma-separated.
        # Removing this would let Jupiter use any AMM it knows.
        # "dexes":             "Pump.fun,Raydium,PumpSwap",
    }
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{JUP_BASE}/swap/v1/quote?{qs}"
    resp = _http_json("GET", url, timeout_s=timeout_s)
    if "error" in resp:
        raise JupiterError(f"quote error: {resp['error']}")
    if not resp.get("outAmount"):
        raise JupiterError(f"unexpected quote response: {resp}")
    return resp


def build_buy_tx(
    *,
    user_id: str | int,
    mint: str,
    payer_pubkey: str,
    sol: float,
    slippage_bps: int = 500,
    priority_fee_microlamports: Optional[int] = None,
    jito_tip_lamports: Optional[int] = None,
    compute_units: Optional[int] = None,
    timeout_s: float = 8.0,
) -> dict:
    """Build an unsigned versioned buy tx via Jupiter.

    Returns an envelope shaped like tg_trader_runner.build_buy_tx's so
    the orchestrator can drop it in. Key differences from the pump-ix
    builder:
      • tx is VERSIONED (v0) with address lookup tables, not legacy
      • route info is in `jupiter_route` (e.g. "Pump.fun")
      • accounts list is implicit (inside the ALT) — we don't surface
        every PDA; receipts include the ammKey + the route summary
    """
    sol_lamports = int(sol * 1e9)
    if sol_lamports <= 0:
        raise JupiterError(f"sol {sol} converts to non-positive lamports")

    # ── 1. Quote ──────────────────────────────────────────────────────
    q = quote(mint=mint, sol_lamports=sol_lamports,
              slippage_bps=slippage_bps, timeout_s=timeout_s/2)
    out_amount = int(q["outAmount"])
    other_amount_threshold = int(q.get("otherAmountThreshold", out_amount))
    price_impact_pct = float(q.get("priceImpactPct") or 0)
    route_labels = [step["swapInfo"]["label"] for step in q.get("routePlan") or []]
    primary_route = route_labels[0] if route_labels else "unknown"

    # ── 2. Swap (build the unsigned tx) ───────────────────────────────
    # Jupiter's `prioritizationFeeLamports` and `computeUnitPriceMicroLamports`
    # are MUTUALLY EXCLUSIVE — setting both returns 400. Pick one path:
    #   • Jito tip → prioritizationFeeLamports as dict (Jupiter appends a
    #     Jito tip ix inside the swap tx; no separate ix needed)
    #   • Plain priority fee → prioritizationFeeLamports as int
    #   • Neither → Jupiter uses its default
    # `dynamicComputeUnitLimit: True` lets Jupiter simulate + size the CU
    # limit correctly — safer than guessing.
    swap_req: dict = {
        "quoteResponse":               q,
        "userPublicKey":               payer_pubkey,
        "wrapAndUnwrapSol":            True,
        "dynamicComputeUnitLimit":     True,
    }
    # Use prioritizationFeeLamports as a flat int. We map "jito tip" to
    # priority fee at parity — for plain-RPC submission, the priority fee
    # is what gets the tx included quickly. Adding a Jito tip dict here
    # would bloat the tx past Solana's 1232-byte limit (Day 4.7 finding).
    fee_lamports = jito_tip_lamports if jito_tip_lamports is not None else priority_fee_microlamports
    if fee_lamports is not None and fee_lamports > 0:
        swap_req["prioritizationFeeLamports"] = int(fee_lamports)
    # compute_units argument is intentionally ignored — Jupiter sizes the
    # CU limit dynamically and trying to set both fields here is what
    # caused the 400 in the first place. Kept in the signature for
    # backward compat with the pump-ix builder's keyword args.
    _ = compute_units

    swap = _http_json("POST", f"{JUP_BASE}/swap/v1/swap",
                      body=swap_req, timeout_s=timeout_s)
    if "swapTransaction" not in swap:
        raise JupiterError(f"swap endpoint missing swapTransaction: {swap}")
    tx_b64 = swap["swapTransaction"]

    # Compute the same trader-friendly fields the pump-ix builder returns
    # so the orchestrator can write a uniform position row.
    buy_lamports = sol_lamports
    max_sol_cost_lamports = int(sol_lamports * (1 + slippage_bps / 10_000))
    # entry_price = SOL per raw token. We use otherAmountThreshold (the worst
    # case) so PnL accounting is conservative.
    entry_price = (sol_lamports / out_amount) if out_amount else 0

    return {
        "route":                          f"jupiter:{primary_route}",
        "tx_b64":                         tx_b64,
        "buy_lamports":                   buy_lamports,
        "slippage_bps":                   slippage_bps,
        "max_sol_cost_lamports":          max_sol_cost_lamports,
        "expected_tokens_out":            out_amount,
        "min_tokens_out":                 other_amount_threshold,
        "entry_price_lamports_per_token": entry_price,
        "entry_mcap_sol":                 None,  # Jupiter doesn't return MC
        "is_cashback_coin":               None,  # not relevant — Jupiter handles
        # Jupiter-specific diagnostics for receipts / debug
        "jupiter_route":                  route_labels,
        "price_impact_pct":               price_impact_pct,
        "jupiter_quote":                  {
            "outAmount":               q.get("outAmount"),
            "otherAmountThreshold":    q.get("otherAmountThreshold"),
            "contextSlot":             q.get("contextSlot"),
            "primary_route":           primary_route,
        },
        # For schema compatibility with the pump-ix builder (positions table
        # expects accounts.token_program). Jupiter handles token program
        # internally, but we still need to pass SOMETHING for the position
        # row. SPL Token is the safe default — sell side can detect on read.
        "accounts": {
            "token_program": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
        },
    }
