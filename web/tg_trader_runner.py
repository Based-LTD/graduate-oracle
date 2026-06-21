"""
tg_trader_runner — Python ↔ Rust IPC bridge for the trading execution layer.

Architecture (the line between languages):

  Python owns:
    - Custody (trader_wallets.py — encrypted keys, withdraw passwords)
    - RPC reads (bonding curve state, recent blockhash, account balances)
    - Signing (uses solders, never lets a key into a subprocess)
    - User state (positions schema, deposits, withdrawals)
    - Regime gating decisions (reads /api/v1/conditions)
    - Route decision (pump.fun pre-grad vs Jupiter post-grad — see below)

  Rust owns (via the tg-trader binary):
    - pump.fun pre-grad buy tx assembly (build-buy-tx)
    - pump.fun pre-grad sell tx assembly (build-sell-tx — Day 4.3)
    - Jito multi-region bundle assembly + submission (submit-bundle — Day 4.4)
    - On-chain confirmation polling
    - TP/SL monitor loop (when Day 5 wires it)
    - Anything where sub-100ms execution matters

  Protocol: JSON-over-stdin, one command per line, one response per line.

This module is the Python side of that boundary. It NEVER passes a private
key to the subprocess. Signing happens in trader_wallets.sign_transaction
before bytes are handed off.

ROUTING — pre-grad vs post-grad:
    Buys for tokens whose bonding curve is still active (complete=false)
    go through `build_buy_tx()` here, which uses the pump.fun program
    instruction layout via the Rust binary.

    Buys for graduated tokens (complete=true) DO NOT pass through this
    binary's tx builder. Python fetches the swap tx directly from Jupiter's
    /swap endpoint (which returns a ready-to-sign VersionedTransaction),
    signs it locally, and hands the bytes to `submit-bundle` (Day 4.4).
    The orchestrator (Day 4.5) decides which path to take based on the
    on-chain `complete` flag it reads with the same RPC call that fetches
    the curve state.

CLI tests:
    python -m tg_trader_runner health
    python -m tg_trader_runner version
    python -m tg_trader_runner dry-run-buy 42 <mint> 0.5
    python -m tg_trader_runner build-buy-tx <json_payload_file>
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from typing import Optional, TypedDict


# ── On-chain types Python passes to the Rust tx builder ─────────────────

class BondingCurve(TypedDict, total=False):
    """Mirror of Rust's BondingCurveInput. Field names MUST match exactly —
    they are deserialized by serde on the Rust side.

    The Python orchestrator fills this from a single RPC read of the curve
    account (decoded with borsh on the Python side — see web/bonding_curve.py
    when that lands). We pass the parsed state in so Rust stays pure (no RPC).
    """
    virtual_sol_reserves: int
    virtual_token_reserves: int
    real_sol_reserves: int        # optional, default 0
    real_token_reserves: int      # optional, default 0
    token_total_supply: int       # optional, default 0
    complete: bool
    creator: str                  # base58
    is_cashback_coin: bool        # optional, default False


# Resolve the binary path. In the deploy image it's at /usr/local/bin/tg-trader.
# In dev (running on the same checkout) it's at target/release/tg-trader.
def _resolve_binary() -> str:
    override = os.environ.get("TG_TRADER_BIN")
    if override and os.path.isfile(override) and os.access(override, os.X_OK):
        return override
    for candidate in (
        "/usr/local/bin/tg-trader",
        os.path.join(os.path.dirname(__file__), "..", "target", "release", "tg-trader"),
    ):
        candidate = os.path.abspath(candidate)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    located = shutil.which("tg-trader")
    if located:
        return located
    raise RuntimeError(
        "tg-trader binary not found. Set TG_TRADER_BIN to its absolute path "
        "or run `cargo build --release --bin tg-trader` in the project root."
    )


# Per-binary single-shot invocation. Day 3 uses one command per process so
# there's no shared state to worry about. When Day 5's monitor lands, we'll
# add a long-lived process pool.
class TgTraderError(RuntimeError):
    """Raised when the binary returns ok=false or fails to launch."""


def invoke(cmd: dict, timeout_s: float = 8.0) -> dict:
    """Send a single JSON command to the binary; return its parsed response.

    Raises TgTraderError if:
      • the binary couldn't be launched
      • the binary exited with non-zero
      • the response JSON has ok=false (the error string is preserved)
      • the response wasn't valid JSON
    """
    bin_path = _resolve_binary()
    line = json.dumps(cmd, separators=(",", ":")) + "\n"
    try:
        proc = subprocess.run(
            [bin_path],
            input=line, text=True, capture_output=True,
            timeout=timeout_s, check=False,
        )
    except FileNotFoundError as e:
        raise TgTraderError(f"binary missing: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise TgTraderError(f"tg-trader timed out after {timeout_s}s: {e}") from e

    if proc.returncode != 0:
        raise TgTraderError(
            f"tg-trader exited {proc.returncode}: stderr={proc.stderr.strip()}"
        )
    stdout = proc.stdout.strip()
    if not stdout:
        raise TgTraderError("tg-trader returned empty response")
    # Response is one line of JSON
    first_line = stdout.splitlines()[0]
    try:
        resp = json.loads(first_line)
    except json.JSONDecodeError as e:
        raise TgTraderError(f"non-JSON response: {first_line[:200]} — {e}") from e
    if not resp.get("ok"):
        raise TgTraderError(resp.get("error") or "tg-trader returned ok=false")
    return resp.get("data") or {}


# ── Typed convenience wrappers ──────────────────────────────────────────

def health() -> dict:
    """Confirm the binary is alive + announces what commands it supports."""
    return invoke({"cmd": "health"})


def version() -> dict:
    """Library version + list of modules the binary can wire into."""
    return invoke({"cmd": "version"})


def dry_run_buy(user_id: str | int, mint: str, sol: float,
                slippage_bps: Optional[int] = None) -> dict:
    """Validates inputs but does NOT submit. Use this from a TG callback
    to confirm the binary will accept the buy before Jito submission."""
    cmd: dict = {
        "cmd":      "dry-run-buy",
        "user_id":  str(user_id),
        "mint":     mint.strip(),
        "sol":      float(sol),
    }
    if slippage_bps is not None:
        cmd["slippage_bps"] = int(slippage_bps)
    return invoke(cmd)


def build_buy_tx(
    user_id: str | int,
    mint: str,
    payer: str,
    sol: float,
    bonding_curve: BondingCurve,
    recent_blockhash: str,
    *,
    slippage_bps: Optional[int] = None,
    priority_fee_microlamports: Optional[int] = None,
    compute_units: Optional[int] = None,
    jito_tip_lamports: Optional[int] = None,
    timeout_s: float = 8.0,
) -> dict:
    """Assemble a pump.fun pre-grad buy tx. Returns an envelope with `tx_b64`
    (base64-encoded unsigned legacy Transaction), `expected_tokens_out`,
    `max_sol_cost_lamports`, and the derived account list for the receipt
    commitment.

    Caller is responsible for:
      • Reading the bonding curve from Helius and decoding it (this fn does
        no RPC — it accepts the parsed state as input).
      • Refusing to call if `bonding_curve.complete` is True. The binary will
        reject too, but routing should happen in Python before this call.
      • Signing `tx_b64` with the user's wallet (custody stays in Python).
      • Handing the signed bytes to submit-bundle (Day 4.4).

    Raises TgTraderError on any validation or build failure.
    """
    if bonding_curve.get("complete"):
        raise TgTraderError(
            "build_buy_tx called for graduated curve — use Jupiter route. "
            "See module docstring under ROUTING."
        )
    cmd: dict = {
        "cmd":              "build-buy-tx",
        "user_id":          str(user_id),
        "mint":             mint.strip(),
        "payer":            payer.strip(),
        "sol":              float(sol),
        "bonding_curve":    dict(bonding_curve),
        "recent_blockhash": recent_blockhash.strip(),
    }
    if slippage_bps is not None:
        cmd["slippage_bps"] = int(slippage_bps)
    if priority_fee_microlamports is not None:
        cmd["priority_fee_microlamports"] = int(priority_fee_microlamports)
    if compute_units is not None:
        cmd["compute_units"] = int(compute_units)
    if jito_tip_lamports is not None:
        cmd["jito_tip_lamports"] = int(jito_tip_lamports)
    return invoke(cmd, timeout_s=timeout_s)


def submit_bundle(
    user_id: str | int,
    signed_tx_b64: str,
    *,
    regions: Optional[list[str]] = None,
    live: bool = False,
    timeout_s: float = 12.0,
) -> dict:
    """Submit a SIGNED tx to Jito multi-region bundle.

    Safety: even with `live=True`, the Rust binary refuses to actually send
    unless the process env var `TG_TRADER_LIVE=1` is also set. The default
    (live=False) returns a validation envelope without any network call —
    use this from tests, dry-runs, and the orchestrator's pre-flight check.

    The caller is responsible for:
      • Calling `build_buy_tx()` or `build_sell_tx()` first to get the
        unsigned base64.
      • Signing it via trader_wallets.sign_transaction (Python-side; custody
        key never enters this process).
      • Passing the signed result here as `signed_tx_b64`.

    Returns: {phase, would_submit, signature, n_signatures, tx_bytes,
              regions (per-region results when live), n_regions[, n_accepted]}
    Raises TgTraderError if validation fails or live=True with the env var
    unset.
    """
    cmd: dict = {
        "cmd":           "submit-bundle",
        "user_id":       str(user_id),
        "signed_tx_b64": signed_tx_b64.strip(),
        "live":          bool(live),
    }
    if regions is not None:
        cmd["regions"] = list(regions)
    return invoke(cmd, timeout_s=timeout_s)


def build_sell_tx(
    user_id: str | int,
    mint: str,
    payer: str,
    creator: str,
    token_amount: int,
    min_sol_output_lamports: int,
    recent_blockhash: str,
    *,
    is_cashback_coin: bool = False,
    priority_fee_microlamports: Optional[int] = None,
    compute_units: Optional[int] = None,
    timeout_s: float = 8.0,
) -> dict:
    """Assemble a pump.fun pre-grad sell tx. Returns an envelope with
    `tx_b64` (unsigned legacy Transaction, base64), the derived account
    list, and the trade economics for the receipt commitment.

    Inputs the caller is expected to compute upstream:
      • token_amount  — raw token units (NOT whole tokens; pump.fun mints have
        6 decimals). For a partial sell, multiply position.token_amount by
        the desired fraction.
      • min_sol_output_lamports — slippage floor. Compute from the curve's
        constant-product formula × (1 - slippage_bps/10000). 0 is accepted
        ("any non-zero output") but only safe for forced exits.
      • creator + is_cashback_coin — pull from the saved Position row,
        NOT a fresh curve read (post-graduation the curve account is gone).

    Raises TgTraderError on validation or build failure.
    """
    cmd: dict = {
        "cmd":                     "build-sell-tx",
        "user_id":                 str(user_id),
        "mint":                    mint.strip(),
        "payer":                   payer.strip(),
        "creator":                 creator.strip(),
        "token_amount":            int(token_amount),
        "min_sol_output_lamports": int(min_sol_output_lamports),
        "is_cashback_coin":        bool(is_cashback_coin),
        "recent_blockhash":        recent_blockhash.strip(),
    }
    if priority_fee_microlamports is not None:
        cmd["priority_fee_microlamports"] = int(priority_fee_microlamports)
    if compute_units is not None:
        cmd["compute_units"] = int(compute_units)
    return invoke(cmd, timeout_s=timeout_s)


# ── CLI ─────────────────────────────────────────────────────────────────

def _cli():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)
    sub = args[0]
    try:
        if sub == "health":
            print(json.dumps(health(), indent=2))
        elif sub == "version":
            print(json.dumps(version(), indent=2))
        elif sub == "dry-run-buy":
            if len(args) < 4:
                print("usage: tg_trader_runner dry-run-buy <user_id> <mint> <sol> [slippage_bps]")
                sys.exit(2)
            user_id, mint, sol = args[1], args[2], float(args[3])
            slip = int(args[4]) if len(args) > 4 else None
            print(json.dumps(dry_run_buy(user_id, mint, sol, slip), indent=2))
        elif sub == "build-buy-tx":
            if len(args) < 2:
                print("usage: tg_trader_runner build-buy-tx <payload.json>")
                print("  payload schema: {user_id, mint, payer, sol, bonding_curve,")
                print("                   recent_blockhash, [slippage_bps],")
                print("                   [priority_fee_microlamports], [compute_units]}")
                sys.exit(2)
            with open(args[1]) as f:
                payload = json.load(f)
            print(json.dumps(build_buy_tx(**payload), indent=2))
        elif sub == "build-sell-tx":
            if len(args) < 2:
                print("usage: tg_trader_runner build-sell-tx <payload.json>")
                print("  payload schema: {user_id, mint, payer, creator, token_amount,")
                print("                   min_sol_output_lamports, recent_blockhash,")
                print("                   [is_cashback_coin], [priority_fee_microlamports],")
                print("                   [compute_units]}")
                sys.exit(2)
            with open(args[1]) as f:
                payload = json.load(f)
            print(json.dumps(build_sell_tx(**payload), indent=2))
        elif sub == "submit-bundle":
            if len(args) < 2:
                print("usage: tg_trader_runner submit-bundle <payload.json>")
                print("  payload schema: {user_id, signed_tx_b64, [regions], [live]}")
                print("  NOTE: live=True requires TG_TRADER_LIVE=1 in the binary's env")
                sys.exit(2)
            with open(args[1]) as f:
                payload = json.load(f)
            print(json.dumps(submit_bundle(**payload), indent=2))
        else:
            print(f"unknown command: {sub}")
            sys.exit(2)
    except TgTraderError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _cli()
