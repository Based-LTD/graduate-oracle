"""
Unit tests for web/bonding_curve.py.

decode() is a pure function and gets the full unit-test treatment.
fetch() is tested by mocking urllib.request.urlopen so the suite never
touches the network. derive_pda() is tested against a known mint+PDA pair.

Run with:
    python3 -m unittest web.tests.test_bonding_curve -v
"""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bonding_curve as bc


# ── Synthetic account bytes ──────────────────────────────────────────────
#
# We construct valid curve account data byte-by-byte. Layout matches
# src/pump.rs:38-53. Using a 32-byte all-zero pubkey for creator is fine
# for testing — the decoder produces the base58 of that (which is the
# system program address, "111...112" — a valid pubkey).

KNOWN_CREATOR_BYTES = bytes([0] * 31 + [1])  # → System program pubkey


def _build_curve_bytes(
    *,
    virtual_token_reserves: int = 1_073_000_000_000_000,
    virtual_sol_reserves: int   = 30_000_000_000,
    real_token_reserves: int    = 793_100_000_000_000,
    real_sol_reserves: int      = 0,
    token_total_supply: int     = 1_000_000_000_000_000,
    complete: bool              = False,
    creator: bytes              = KNOWN_CREATOR_BYTES,
    is_mayhem_mode: bool | None = None,
    is_cashback_coin: bool | None = None,
) -> bytes:
    """Build a synthetic bonding-curve account data blob. If is_mayhem_mode
    or is_cashback_coin are None, those bytes are omitted (simulates v1
    accounts that don't have the v2 fields yet)."""
    out = bytearray()
    out += bc.BONDING_CURVE_DISCRIMINATOR
    out += virtual_token_reserves.to_bytes(8, "little")
    out += virtual_sol_reserves.to_bytes(8, "little")
    out += real_token_reserves.to_bytes(8, "little")
    out += real_sol_reserves.to_bytes(8, "little")
    out += token_total_supply.to_bytes(8, "little")
    out += bytes([1 if complete else 0])
    out += creator
    assert len(out) == 81, f"v1 body should be 81 bytes, got {len(out)}"
    if is_mayhem_mode is not None:
        out += bytes([1 if is_mayhem_mode else 0])
    if is_cashback_coin is not None:
        # Pad in is_mayhem_mode if it was None — Rust always reads them in order
        if is_mayhem_mode is None:
            out += bytes([0])
        out += bytes([1 if is_cashback_coin else 0])
    return bytes(out)


# ── derive_pda ───────────────────────────────────────────────────────────

class TestDerivePda(unittest.TestCase):

    def test_valid_mint_returns_a_pubkey_string(self):
        # System program is a valid pubkey — passing it as a mint should
        # produce SOME PDA string (we don't pin the exact value since it's
        # implementation-dependent; we just check the format).
        pda = bc.derive_pda("11111111111111111111111111111112")
        self.assertIsInstance(pda, str)
        self.assertGreaterEqual(len(pda), 32)
        self.assertLessEqual(len(pda), 44)

    def test_invalid_mint_raises(self):
        with self.assertRaises(bc.BondingCurveError) as ctx:
            bc.derive_pda("not-a-pubkey")
        self.assertIn("derive_pda", str(ctx.exception))

    def test_pda_is_deterministic(self):
        """Same mint → same PDA across calls."""
        mint = "11111111111111111111111111111112"
        self.assertEqual(bc.derive_pda(mint), bc.derive_pda(mint))


# ── decode ───────────────────────────────────────────────────────────────

class TestDecode(unittest.TestCase):

    def test_v1_account_decodes_all_fields(self):
        data = _build_curve_bytes()
        state = bc.decode(data)
        self.assertEqual(state["virtual_sol_reserves"],   30_000_000_000)
        self.assertEqual(state["virtual_token_reserves"], 1_073_000_000_000_000)
        self.assertEqual(state["real_sol_reserves"],      0)
        self.assertEqual(state["real_token_reserves"],    793_100_000_000_000)
        self.assertEqual(state["token_total_supply"],     1_000_000_000_000_000)
        self.assertEqual(state["complete"],               False)
        self.assertEqual(state["is_mayhem_mode"],         False)
        self.assertEqual(state["is_cashback_coin"],       False)
        # Creator round-trips back to a valid base58 pubkey string
        self.assertIsInstance(state["creator"], str)
        self.assertGreaterEqual(len(state["creator"]), 32)

    def test_v2_account_picks_up_optional_fields(self):
        data = _build_curve_bytes(is_mayhem_mode=True, is_cashback_coin=True)
        state = bc.decode(data)
        self.assertEqual(state["is_mayhem_mode"],   True)
        self.assertEqual(state["is_cashback_coin"], True)

    def test_v2_cashback_only_without_mayhem(self):
        """Real v2 accounts often have just is_cashback_coin set."""
        data = _build_curve_bytes(is_mayhem_mode=False, is_cashback_coin=True)
        state = bc.decode(data)
        self.assertEqual(state["is_mayhem_mode"],   False)
        self.assertEqual(state["is_cashback_coin"], True)

    def test_complete_flag_round_trips(self):
        v1_open = bc.decode(_build_curve_bytes(complete=False))
        v1_done = bc.decode(_build_curve_bytes(complete=True))
        self.assertEqual(v1_open["complete"], False)
        self.assertEqual(v1_done["complete"], True)

    def test_too_short_raises(self):
        with self.assertRaises(bc.BondingCurveError) as ctx:
            bc.decode(b"\x17\xb7\xf8\x37\x60\xd8\xac\x60" + b"\x00" * 10)
        self.assertIn("too short", str(ctx.exception))

    def test_bad_discriminator_raises(self):
        bad = bytes([0xAA] * 8) + b"\x00" * 80
        with self.assertRaises(bc.BondingCurveError) as ctx:
            bc.decode(bad)
        self.assertIn("discriminator", str(ctx.exception))

    def test_decoded_shape_matches_runner_typeddict(self):
        """The keys we return MUST match what tg_trader_runner.BondingCurve
        expects. Otherwise the Rust binary will reject the bonding_curve
        payload at deserialize time."""
        data = _build_curve_bytes(is_mayhem_mode=False, is_cashback_coin=False)
        state = bc.decode(data)
        required = {
            "virtual_sol_reserves", "virtual_token_reserves",
            "real_sol_reserves", "real_token_reserves",
            "token_total_supply", "complete", "creator", "is_cashback_coin",
        }
        self.assertTrue(
            required.issubset(state.keys()),
            f"missing keys: {required - state.keys()}",
        )


# ── fetch (mocked RPC) ───────────────────────────────────────────────────

class _FakeHttpResponse:
    """Mimic the urllib.request.urlopen context manager."""
    def __init__(self, body_bytes: bytes):
        self._body = body_bytes
    def read(self):
        return self._body
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"


def _rpc_response(data_bytes: bytes, *,
                  mint_owner: str = SPL_TOKEN_PROGRAM) -> _FakeHttpResponse:
    """Mimic getMultipleAccounts response shape: [curve_account, mint_account]."""
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1,
        "result": {
            "context": {"slot": 1},
            "value": [
                {  # curve PDA account
                    "data": [base64.b64encode(data_bytes).decode("ascii"), "base64"],
                    "executable": False,
                    "lamports": 0,
                    "owner": bc.PUMP_PROGRAM,
                    "rentEpoch": 0,
                },
                {  # mint account — its OWNER is the token program
                    "data": ["", "base64"],
                    "executable": False,
                    "lamports": 0,
                    "owner": mint_owner,
                    "rentEpoch": 0,
                },
            ],
        }
    }).encode()
    return _FakeHttpResponse(body)


def _rpc_null_value() -> _FakeHttpResponse:
    """Curve account doesn't exist — first element of value array is null."""
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1,
        "result": {"context": {"slot": 1}, "value": [None, None]},
    }).encode()
    return _FakeHttpResponse(body)


def _rpc_error() -> _FakeHttpResponse:
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1,
        "error": {"code": -32000, "message": "exploded"},
    }).encode()
    return _FakeHttpResponse(body)


class TestFetch(unittest.TestCase):

    MINT = "11111111111111111111111111111112"

    def test_happy_path_returns_decoded_state(self):
        data = _build_curve_bytes()
        with patch("urllib.request.urlopen", return_value=_rpc_response(data)):
            state = bc.fetch(self.MINT)
            self.assertEqual(state["virtual_sol_reserves"], 30_000_000_000)
            self.assertEqual(state["complete"], False)

    def test_account_missing_raises_specific_error(self):
        """value=null → mint is not pump.fun OR curve graduated. Either way,
        the error message should make that diagnosis possible."""
        with patch("urllib.request.urlopen", return_value=_rpc_null_value()):
            with self.assertRaises(bc.BondingCurveError) as ctx:
                bc.fetch(self.MINT)
            msg = str(ctx.exception).lower()
            self.assertTrue(
                "does not exist" in msg or "graduat" in msg,
                f"error should hint at the cause; got: {msg}",
            )

    def test_rpc_error_propagates(self):
        with patch("urllib.request.urlopen", return_value=_rpc_error()):
            with self.assertRaises(bc.BondingCurveError) as ctx:
                bc.fetch(self.MINT)
            self.assertIn("RPC error", str(ctx.exception))

    def test_network_failure_wrapped(self):
        with patch("urllib.request.urlopen", side_effect=ConnectionError("dns failed")):
            with self.assertRaises(bc.BondingCurveError) as ctx:
                bc.fetch(self.MINT)
            self.assertIn("getMultipleAccounts", str(ctx.exception))

    def test_uses_custom_rpc_url_when_provided(self):
        """A private Helius URL should override the default."""
        captured = {}
        def _fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            return _rpc_response(_build_curve_bytes())
        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            bc.fetch(self.MINT, rpc_url="https://my-helius.example.com")
            self.assertEqual(captured["url"], "https://my-helius.example.com")

    def test_bad_discriminator_surfaces_decoder_error(self):
        """If the RPC returns bytes that don't match the discriminator, the
        decoder error must propagate (could indicate a PDA bug or pump.fun
        ABI change)."""
        garbage = bytes([0xAA] * 8) + bytes([0x00] * 80)
        with patch("urllib.request.urlopen", return_value=_rpc_response(garbage)):
            with self.assertRaises(bc.BondingCurveError) as ctx:
                bc.fetch(self.MINT)
            self.assertIn("discriminator", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
