"""
bonding_curve — fetch + decode the pump.fun bonding curve account.

This is the input feed for the Rust tg-trader's `build-buy-tx` command.
Rust stays pure (no RPC); Python owns reads. We:

    1. derive the PDA from the mint pubkey
    2. issue one getAccountInfo call to Helius / our RPC
    3. borsh-decode the data into a BondingCurve dict matching the schema
       that web/tg_trader_runner.BondingCurve expects

We also decode the optional v2 fields (is_mayhem_mode, is_cashback_coin)
when present. is_cashback_coin matters because pump.fun's sell ix has a
different account layout for cashback coins — the saved position must
preserve this flag.

Struct layout (from src/pump.rs:38-53):

    offset  type      field
    ─────────────────────────────────────────────────────
    0..8    [u8;8]    discriminator       (must equal BONDING_CURVE_DISCRIMINATOR)
    8..16   u64 LE    virtual_token_reserves
    16..24  u64 LE    virtual_sol_reserves
    24..32  u64 LE    real_token_reserves
    32..40  u64 LE    real_sol_reserves
    40..48  u64 LE    token_total_supply
    48      bool      complete             (1 byte: 0 / 1)
    49..81  pubkey    creator              (32 bytes)
    81      bool      is_mayhem_mode       (v2, optional)
    82      bool      is_cashback_coin     (v2, optional)

Total = 81 bytes (v1) or 83 bytes (v2).
"""

from __future__ import annotations

import base64
import json
import os
import urllib.request
from typing import Optional

# pump.fun program — same constant the Rust binary uses (src/pump.rs:14)
PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

# First 8 bytes of every bonding curve account. Anchor-style discriminator.
# Matches src/pump.rs:34 (note: pump.rs has it as the bytes 0x17 0xb7 ...).
BONDING_CURVE_DISCRIMINATOR = bytes([0x17, 0xb7, 0xf8, 0x37, 0x60, 0xd8, 0xac, 0x60])

# RPC default — picks up the same env var trader_wallets uses, with the
# same mainnet fallback. The orchestrator (and only the orchestrator)
# should override this at call time when using a private Helius URL.
_RPC = (os.environ.get("RPC_HTTP") or "https://api.mainnet-beta.solana.com").rstrip("/")


class BondingCurveError(RuntimeError):
    """Raised when the curve fetch or decode fails. Includes a clear
    reason so the orchestrator can decide whether to retry (transient
    network) or abort (curve missing / wrong discriminator)."""


# ─── PDA derivation ─────────────────────────────────────────────────────

def derive_pda(mint_str: str) -> str:
    """Derive the bonding curve PDA from a mint pubkey.

    Raises BondingCurveError if the mint isn't a valid pubkey. We don't
    return None on failure — the caller is the orchestrator, and a bad
    mint at this point should fail loudly, not silently fall through.
    """
    try:
        from solders.pubkey import Pubkey
        mint_pk = Pubkey.from_string(mint_str)
        program_pk = Pubkey.from_string(PUMP_PROGRAM)
        pda, _bump = Pubkey.find_program_address(
            [b"bonding-curve", bytes(mint_pk)], program_pk,
        )
        return str(pda)
    except Exception as e:
        raise BondingCurveError(f"derive_pda failed for mint {mint_str!r}: {e}") from e


# ─── Decode ──────────────────────────────────────────────────────────────

def decode(data_bytes: bytes) -> dict:
    """Decode raw account bytes into a BondingCurve dict.

    Raises BondingCurveError if the data is too short or the discriminator
    is wrong (which would mean we read a non-bonding-curve account — usually
    indicates a bug in PDA derivation).
    """
    if len(data_bytes) < 81:
        raise BondingCurveError(
            f"bonding curve data too short: {len(data_bytes)} bytes (need ≥81)"
        )
    if data_bytes[:8] != BONDING_CURVE_DISCRIMINATOR:
        raise BondingCurveError(
            f"wrong discriminator: got {data_bytes[:8].hex()}, "
            f"expected {BONDING_CURVE_DISCRIMINATOR.hex()}"
        )

    # u64 LE fields
    def _u64(off: int) -> int:
        return int.from_bytes(data_bytes[off:off + 8], "little")

    virtual_token_reserves = _u64(8)
    virtual_sol_reserves   = _u64(16)
    real_token_reserves    = _u64(24)
    real_sol_reserves      = _u64(32)
    token_total_supply     = _u64(40)
    complete               = bool(data_bytes[48])

    # creator: 32 raw bytes → base58 pubkey
    from solders.pubkey import Pubkey
    creator = str(Pubkey.from_bytes(data_bytes[49:81]))

    # Optional v2 fields. Default False when absent (matches pump.rs behavior:
    # #[borsh(skip)] fields default to false, then we set from data bytes if
    # present).
    is_mayhem_mode   = bool(data_bytes[81]) if len(data_bytes) >= 82 else False
    is_cashback_coin = bool(data_bytes[82]) if len(data_bytes) >= 83 else False

    return {
        "virtual_sol_reserves":   virtual_sol_reserves,
        "virtual_token_reserves": virtual_token_reserves,
        "real_sol_reserves":      real_sol_reserves,
        "real_token_reserves":    real_token_reserves,
        "token_total_supply":     token_total_supply,
        "complete":               complete,
        "creator":                creator,
        "is_mayhem_mode":         is_mayhem_mode,
        "is_cashback_coin":       is_cashback_coin,
    }


# ─── Fetch ──────────────────────────────────────────────────────────────

def fetch(mint_str: str, *, rpc_url: Optional[str] = None,
          timeout: float = 6.0, retries: int = 3,
          backoff_base_s: float = 1.0) -> dict:
    """Fetch + decode the bonding curve for the given mint.

    Returns a BondingCurve dict with the curve state PLUS a `token_program`
    field (the mint's owning program). This is critical for the buy/sell
    tx assembly: pump.fun has mints owned by SPL Token (most), SPL
    Token-2022 (cashback / mayhem mints). If we pass the wrong program
    to the create-ATA ix, the tx fails on-chain with IncorrectProgramId.

    Implementation: ONE getMultipleAccounts call returns BOTH the curve
    PDA + the mint account. We decode the curve from one and read the
    owner field of the other.

    Retries transient RPC failures (timeouts, network errors) with
    exponential backoff. Public mainnet RPC is heavily rate-limited and
    will time out 10-30% of the time under load — without retries the
    orchestrator would die at this stage frequently. With 3 retries and
    1s/2s/4s backoff, single-call success rate stays >99%.

    Discriminator mismatches and missing accounts are NOT retried —
    those are deterministic failures (wrong mint, graduated, etc.).
    """
    import time as _time
    pda = derive_pda(mint_str)
    url = (rpc_url or _RPC).rstrip("/")
    # One RPC, two accounts — curve PDA + mint. The mint's owner is the
    # token program (SPL Token vs SPL Token-2022).
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method":  "getMultipleAccounts",
        "params":  [[pda, mint_str.strip()],
                    {"encoding": "base64", "commitment": "confirmed"}],
    }
    body = json.dumps(payload).encode()

    last_err: Optional[Exception] = None
    resp: Optional[dict] = None
    for attempt in range(retries):
        if attempt > 0:
            _time.sleep(backoff_base_s * (2 ** (attempt - 1)))
        try:
            req = urllib.request.Request(
                url, data=body, headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.loads(r.read())
            break  # success
        except Exception as e:
            last_err = e
            continue
    else:
        raise BondingCurveError(
            f"RPC getMultipleAccounts failed after {retries} attempts: {last_err}"
        ) from last_err

    if "error" in resp:
        raise BondingCurveError(f"RPC error: {resp['error']}")

    # getMultipleAccounts returns a 2-element value array: [curve_account, mint_account]
    value = (resp.get("result") or {}).get("value")
    if not isinstance(value, list) or len(value) != 2:
        raise BondingCurveError(f"unexpected getMultipleAccounts shape: {value!r}")
    curve_acct, mint_acct = value[0], value[1]

    if curve_acct is None:
        # Two causes:
        #   • mint isn't a pump.fun mint (PDA doesn't exist)
        #   • mint graduated and the curve account was closed
        raise BondingCurveError(
            f"bonding curve account does not exist for mint {mint_str} (pda={pda}) — "
            "mint is either not on pump.fun or has graduated and closed the curve"
        )
    if mint_acct is None:
        raise BondingCurveError(
            f"mint account {mint_str} does not exist on-chain"
        )

    # Decode curve state
    data_field = curve_acct.get("data")
    if not isinstance(data_field, list) or len(data_field) < 2 or data_field[1] != "base64":
        raise BondingCurveError(f"unexpected curve data encoding: {data_field!r}")
    try:
        raw = base64.b64decode(data_field[0])
    except Exception as e:
        raise BondingCurveError(f"base64 decode failed: {e}") from e
    state = decode(raw)

    # Add token_program from the mint account's owner field. This is what
    # the production sniper does (src/sniper.rs:detect_token_program) —
    # pump.fun mints can be SPL Token or SPL Token-2022, and passing the
    # wrong one to create-ATA causes IncorrectProgramId on-chain.
    token_program = mint_acct.get("owner")
    if not isinstance(token_program, str) or len(token_program) < 32:
        raise BondingCurveError(
            f"mint account has no valid owner field: {mint_acct!r}"
        )
    state["token_program"] = token_program
    return state


# ─── CLI ────────────────────────────────────────────────────────────────

def _cli():
    import sys
    if len(sys.argv) < 2:
        print("usage: python -m bonding_curve <mint>")
        sys.exit(2)
    try:
        state = fetch(sys.argv[1])
        print(json.dumps(state, indent=2))
    except BondingCurveError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _cli()
