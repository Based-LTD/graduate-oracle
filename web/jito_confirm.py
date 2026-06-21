"""
jito_confirm — reliable confirmation polling for submitted bundles.

The naive approach (just poll Solana RPC's getSignatureStatuses) has
three real flake sources we hit in Day 4.7:

  1. Public mainnet RPC times out under load.
  2. RPC propagation can lag actual chain state by seconds.
  3. If the bundle was DROPPED (lost auction), getSignatureStatuses
     returns null forever — we can't distinguish "still propagating"
     from "never landed."

The fix: poll BOTH Solana RPC and Jito's getBundleStatuses, take
whichever resolves first. Jito knows immediately whether they included
the bundle (Landed / Failed / Invalid). Solana confirms once it's in
a block. We bail early on either:

  - Jito says "Failed" or "Invalid"   → return failure
  - Jito says "Landed" OR Solana confirms → return success
  - Both still pending after timeout   → return timeout

Pattern: short fixed poll interval (1s for the first 5s, then 2s).
Total timeout is configurable; default 90s (3× the prior 30s, since
landing can take 15-30s under normal congestion).
"""

from __future__ import annotations

import json
import time
import urllib.request
from typing import Optional


# Pick one Jito region for status queries — they all share state.
JITO_BUNDLE_API = "https://ny.mainnet.block-engine.jito.wtf/api/v1/bundles"


class ConfirmResult:
    """Result of a confirmation poll. Use the .landed / .failed flags
    to branch; the rest of the fields are diagnostic."""
    def __init__(self, *, landed: bool, failed: bool, timed_out: bool,
                 source: str, slot: Optional[int] = None,
                 err: Optional[str] = None, elapsed_s: float = 0):
        self.landed = landed
        self.failed = failed
        self.timed_out = timed_out
        self.source = source              # "jito" / "solana" / "timeout"
        self.slot = slot
        self.err = err
        self.elapsed_s = elapsed_s

    def __repr__(self):
        return (f"<ConfirmResult landed={self.landed} failed={self.failed} "
                f"timed_out={self.timed_out} source={self.source!r} "
                f"slot={self.slot} err={self.err!r} elapsed={self.elapsed_s}s>")


def _post_json(url: str, payload: dict, timeout_s: float = 4.0) -> dict:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        return json.loads(r.read())


def _check_jito_bundle(bundle_id: str) -> Optional[dict]:
    """Return Jito's status entry for the bundle, or None if not present
    or if the call failed. Statuses we care about:
      - confirmation_status: "processed"/"confirmed"/"finalized"
      - err: {"Ok": null} on success, else an error object
    """
    try:
        resp = _post_json(JITO_BUNDLE_API, {
            "jsonrpc": "2.0", "id": 1,
            "method":  "getBundleStatuses",
            "params":  [[bundle_id]],
        })
    except Exception:
        return None
    value = (resp.get("result") or {}).get("value") or []
    return value[0] if value and value[0] else None


def _check_solana_sig(rpc_url: str, signature: str) -> Optional[dict]:
    """Return Solana's status entry for the signature, or None if not
    present or if the call failed. Returns the value dict directly."""
    try:
        resp = _post_json(rpc_url, {
            "jsonrpc": "2.0", "id": 1,
            "method":  "getSignatureStatuses",
            "params":  [[signature], {"searchTransactionHistory": True}],
        })
    except Exception:
        return None
    value = (resp.get("result") or {}).get("value") or []
    return value[0] if value and value[0] else None


def wait_for_confirmation(
    *,
    signature: str,
    rpc_url: str,
    bundle_ids: Optional[list[str]] = None,
    timeout_s: float = 90.0,
    poll_interval_s: float = 1.0,
) -> ConfirmResult:
    """Wait for the bundle's tx to land on-chain (or fail).

    Polls Jito's getBundleStatuses for each bundle_id AND Solana's
    getSignatureStatuses for the signature on each tick. First positive
    answer wins; first negative answer (Jito reports Failed) also wins
    and short-circuits the wait.

    Args:
      signature:    the tx signature returned by build-buy-tx
      rpc_url:      Solana RPC URL (Helius strongly recommended)
      bundle_ids:   Jito bundle IDs returned from each region (multiple
                    regions may return the same ID; dedup'd internally).
                    Pass empty/None to skip the Jito polling leg.
      timeout_s:    total wait budget (default 90s — landing under
                    normal congestion is 15-30s; 90s covers tail latency)
      poll_interval_s: gap between polls (default 1s)
    """
    bundle_ids = list({b for b in (bundle_ids or []) if b})
    deadline = time.time() + timeout_s
    started = time.time()

    while time.time() < deadline:
        # 1. Check every Jito bundle status. If ANY reports Failed,
        #    short-circuit — no point polling Solana for a dropped bundle.
        for bid in bundle_ids:
            status = _check_jito_bundle(bid)
            if status is None:
                continue
            err = status.get("err")
            conf = status.get("confirmation_status")
            slot = status.get("slot")
            # Jito's err is {"Ok": null} on success. Any other shape =
            # the bundle landed but the tx errored on-chain.
            if err is not None and err != {"Ok": None} and err != {}:
                return ConfirmResult(
                    landed=False, failed=True, timed_out=False,
                    source="jito", slot=slot, err=str(err),
                    elapsed_s=round(time.time() - started, 2),
                )
            if conf in ("confirmed", "finalized"):
                return ConfirmResult(
                    landed=True, failed=False, timed_out=False,
                    source="jito", slot=slot,
                    elapsed_s=round(time.time() - started, 2),
                )
            # "processed" means seen but not yet confirmed — keep polling.

        # 2. Check Solana RPC. Independent of Jito; faster once propagation
        #    catches up but slower for the first ~5-10s.
        sol = _check_solana_sig(rpc_url, signature)
        if sol is not None:
            err = sol.get("err")
            conf = sol.get("confirmationStatus")
            slot = sol.get("slot")
            if err is not None:
                return ConfirmResult(
                    landed=False, failed=True, timed_out=False,
                    source="solana", slot=slot, err=str(err),
                    elapsed_s=round(time.time() - started, 2),
                )
            if conf in ("confirmed", "finalized"):
                return ConfirmResult(
                    landed=True, failed=False, timed_out=False,
                    source="solana", slot=slot,
                    elapsed_s=round(time.time() - started, 2),
                )

        time.sleep(poll_interval_s)

    return ConfirmResult(
        landed=False, failed=False, timed_out=True,
        source="timeout",
        elapsed_s=round(time.time() - started, 2),
    )
