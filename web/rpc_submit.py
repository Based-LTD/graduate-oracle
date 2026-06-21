"""
rpc_submit — plain Solana RPC sendTransaction submission.

Why this module exists: Jito's bundle auction is unreliable for pump.fun
mints. Jito accepts the bundle (5/5 regions, 200 OK with bundle ID) but
silently drops it from consideration — `getBundleStatuses` returns empty.
Likely cause: Jito's internal pre-bundle simulation fails for pump.fun
edge cases (large account writes, dynamic creator_vault PDA, etc.).

Plain RPC submission bypasses this. We POST sendTransaction directly to
the Helius (or any) RPC. Priority fee (set via ComputeBudgetInstruction
in build_buy_tx) handles inclusion priority. No auction, no minimum tip,
no silent drops.

The orchestrator can fire BOTH paths (Jito + RPC) in parallel — they
share the same signed tx, so only one can actually land on-chain. The
dual-confirmation module then surfaces whichever wins the race.

The function deliberately mirrors tg_trader_runner.submit_bundle's
return shape so callers can swap between paths without restructuring.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Optional


def send_via_rpc(
    signed_tx_b64: str,
    *,
    rpc_url: str,
    skip_preflight: bool = True,
    max_retries: int = 0,
    preflight_commitment: str = "confirmed",
    timeout_s: float = 8.0,
) -> dict:
    """Submit a signed tx via plain RPC sendTransaction.

    Returns a dict shaped like submit_bundle's response for symmetry:
        {phase, signature, rpc_url, ok, error?, rpc_response?}

    `skip_preflight=True` is correct for our use case: we KNOW the
    tx is valid (we built it), and preflight adds 100-500ms latency
    and another point of failure (preflight runs at the RPC's node,
    which may have stale state).

    `max_retries=0`: Solana's built-in tx forwarding handles retries
    at the RPC node level. We don't want to spam-resubmit because the
    tx has a fixed blockhash that expires after ~60s.

    Raises nothing — failures are surfaced in the return dict's `ok`
    field. This matches the rest of the orchestrator's error handling
    pattern.
    """
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method":  "sendTransaction",
        "params":  [
            signed_tx_b64.strip(),
            {
                "encoding":            "base64",
                "skipPreflight":       skip_preflight,
                "maxRetries":          max_retries,
                "preflightCommitment": preflight_commitment,
            },
        ],
    }
    body = json.dumps(payload).encode()
    try:
        req = urllib.request.Request(
            rpc_url, data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            resp = json.loads(r.read())
    except Exception as e:
        return {
            "phase":   "submit_failed",
            "ok":      False,
            "error":   f"RPC sendTransaction call failed: {e}",
            "rpc_url": rpc_url,
        }

    if "error" in resp:
        return {
            "phase":   "submit_failed",
            "ok":      False,
            "error":   f"RPC sendTransaction error: {resp['error']}",
            "rpc_url": rpc_url,
            "rpc_response": resp,
        }

    sig = resp.get("result")
    if not isinstance(sig, str):
        return {
            "phase":   "submit_failed",
            "ok":      False,
            "error":   f"unexpected sendTransaction response: {resp}",
            "rpc_url": rpc_url,
            "rpc_response": resp,
        }

    return {
        "phase":         "submitted_via_rpc",
        "ok":            True,
        "signature":     sig,
        "rpc_url":       rpc_url,
        "skip_preflight": skip_preflight,
    }
