"""
Unit tests for web/trader_orchestrator.py.

Every external dependency is mocked:
  - trader_wallets.get_or_create_wallet  (wallet lookup)
  - trader_wallets._rpc_call             (blockhash fetch)
  - trader_wallets.sign_transaction      (custody)
  - bonding_curve.fetch                  (RPC + decode)
  - tg_trader_runner.build_buy_tx        (Rust IPC)
  - tg_trader_runner.submit_bundle       (Rust IPC)

Position DB is a per-test temp file via TRADER_DB_PATH.

The tests cover:
  - happy dry-run path end-to-end
  - happy live-mocked path
  - every stage's failure path raises OrchestratorError with the right stage
  - graduated curves are rejected (route stage)
  - input validation rejects bad sol / slippage
  - cashback flag propagates from curve → position row
  - tier + signal_source persist
  - custom submit_regions pass through

Run with:
    python3 -m unittest web.tests.test_trader_orchestrator -v
"""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


MINT      = "ABC" * 12
PAYER     = "11111111111111111111111111111112"
CREATOR   = "11111111111111111111111111111113"
BLOCKHASH = "11111111111111111111111111111111"


def _fresh_curve(**overrides) -> dict:
    base = {
        "virtual_sol_reserves":   30_000_000_000,
        "virtual_token_reserves": 1_073_000_000_000_000,
        "real_sol_reserves":      0,
        "real_token_reserves":    793_100_000_000_000,
        "token_total_supply":     1_000_000_000_000_000,
        "complete":               False,
        "creator":                CREATOR,
        "is_mayhem_mode":         False,
        "is_cashback_coin":       False,
    }
    base.update(overrides)
    return base


def _build_envelope(**overrides) -> dict:
    """Mock return value for tg_trader_runner.build_buy_tx — mirrors the
    real Rust envelope shape so the orchestrator finds every field."""
    base = {
        "route":                          "pumpfun-pregrad",
        "tx_b64":                         "UNSIGNED-B64",
        "buy_lamports":                   500_000_000,
        "slippage_bps":                   500,
        "max_sol_cost_lamports":          525_000_000,
        "expected_tokens_out":            17_883_333_333_333,
        "entry_price_lamports_per_token": 27.96,
        "entry_mcap_sol":                 27.96,
        "is_cashback_coin":               False,
        "accounts": {
            "token_program":           "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
            "bonding_curve":           "x",
            "associated_bonding_curve":"y",
            "user_ata":                "z",
            "creator_vault":           "v",
            "event_authority":         "e",
            "global_volume_accumulator":"g",
            "user_volume_accumulator": "u",
            "fee_config":              "f",
            "bonding_curve_v2":        "b",
        },
    }
    base.update(overrides)
    return base


def _dry_run_submit(signature: str = "SIG-DRY") -> dict:
    return {
        "phase":         "dry-run",
        "would_submit":  False,
        "signature":     signature,
        "n_signatures":  1,
        "tx_bytes":      300,
        "regions":       ["ny", "amsterdam", "frankfurt", "tokyo", "slc"],
        "n_regions":     5,
    }


def _live_submit(signature: str = "SIG-LIVE", n_accepted: int = 5) -> dict:
    return {
        "phase":         "submitted",
        "would_submit":  True,
        "signature":     signature,
        "n_signatures":  1,
        "tx_bytes":      300,
        "regions":       [{"region": r, "ok": True, "status": 200, "body": "{}"}
                          for r in ("ny", "amsterdam", "frankfurt", "tokyo", "slc")],
        "n_regions":     5,
        "n_accepted":    n_accepted,
    }


class _Base(unittest.TestCase):
    """Each test gets a fresh temp position DB. The orchestrator module is
    reloaded so it picks up the new trader_positions DB path."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        os.environ["TRADER_DB_PATH"] = self.tmp.name
        global tp, orch
        import trader_positions as _tp
        importlib.reload(_tp)
        tp = _tp
        tp.init_schema()
        import trader_orchestrator as _orch
        importlib.reload(_orch)
        orch = _orch

    def tearDown(self):
        os.environ.pop("TRADER_DB_PATH", None)
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass


# ── Happy path ──────────────────────────────────────────────────────────

class TestHappyPath(_Base):

    def _patches(self, *, curve=None, submit=None, envelope=None):
        """Build the standard set of patches for the success path."""
        return [
            patch.object(orch.trader_wallets, "get_or_create_wallet",
                         return_value={"public_key": PAYER}),
            patch.object(orch.trader_wallets, "_rpc_call",
                         return_value={"value": {"blockhash": BLOCKHASH}}),
            patch.object(orch.trader_wallets, "sign_transaction",
                         return_value="SIGNED-B64"),
            patch.object(orch.bonding_curve, "fetch",
                         return_value=curve or _fresh_curve()),
            patch.object(orch.tg_trader_runner, "build_buy_tx",
                         return_value=envelope or _build_envelope()),
            patch.object(orch.tg_trader_runner, "submit_bundle",
                         return_value=submit or _dry_run_submit()),
        ]

    def test_dry_run_end_to_end(self):
        with self._patches()[0], self._patches()[1], self._patches()[2], \
             self._patches()[3], self._patches()[4], self._patches()[5]:
            # Re-stack the patches as a single with-block
            pass
        # Use ExitStack-style — simpler to just enter all manually:
        from contextlib import ExitStack
        with ExitStack() as stack:
            for p in self._patches():
                stack.enter_context(p)
            result = orch.buy(42, MINT, 0.5)
        self.assertEqual(result["phase"], "dry-run")
        self.assertEqual(result["user_id"], "42")
        self.assertEqual(result["mint"], MINT)
        self.assertEqual(result["buy_lamports"], 500_000_000)
        self.assertEqual(result["buy_signature"], "SIG-DRY")
        self.assertEqual(result["route"], "pumpfun-pregrad")
        self.assertGreater(result["position_id"], 0)

        # Position row was written with correct fields
        row = tp.get_position(result["position_id"])
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "open")
        self.assertEqual(row["buy_phase"], "dry-run")
        self.assertEqual(row["buy_signature"], "SIG-DRY")
        self.assertEqual(row["buy_sol_lamports"], 500_000_000)
        self.assertEqual(row["token_amount"], 17_883_333_333_333)
        self.assertEqual(row["creator"], CREATOR)
        self.assertEqual(row["is_cashback_coin"], 0)
        self.assertEqual(row["buy_signal_source"], "manual")

    def test_live_mocked_path_writes_submitted_phase(self):
        from contextlib import ExitStack
        with ExitStack() as stack:
            for p in self._patches(submit=_live_submit("SIG-LIVE")):
                stack.enter_context(p)
            result = orch.buy(42, MINT, 0.5, live=True)
        self.assertEqual(result["phase"], "submitted")
        self.assertEqual(result["buy_signature"], "SIG-LIVE")
        row = tp.get_position(result["position_id"])
        self.assertEqual(row["buy_phase"], "submitted")
        self.assertEqual(row["status"], "open")  # 'open' until sold

    def test_cashback_flag_propagates_from_curve_to_position(self):
        from contextlib import ExitStack
        with ExitStack() as stack:
            for p in self._patches(curve=_fresh_curve(is_cashback_coin=True)):
                stack.enter_context(p)
            result = orch.buy(42, MINT, 0.5)
        row = tp.get_position(result["position_id"])
        self.assertEqual(row["is_cashback_coin"], 1)
        self.assertEqual(result["is_cashback_coin"], True)

    def test_tier_and_signal_source_persist(self):
        from contextlib import ExitStack
        with ExitStack() as stack:
            for p in self._patches():
                stack.enter_context(p)
            result = orch.buy(42, MINT, 0.5, tier="ACT", signal_source="composite_score")
        row = tp.get_position(result["position_id"])
        self.assertEqual(row["buy_tier"], "ACT")
        self.assertEqual(row["buy_signal_source"], "composite_score")

    def test_custom_submit_regions_passed_through(self):
        captured = {}
        def fake_submit(user_id, signed_tx_b64, *, regions=None, live=False, **kw):
            captured["regions"] = regions
            captured["live"]    = live
            return _dry_run_submit()

        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch.object(orch.trader_wallets, "get_or_create_wallet",
                                             return_value={"public_key": PAYER}))
            stack.enter_context(patch.object(orch.trader_wallets, "_rpc_call",
                                             return_value={"value": {"blockhash": BLOCKHASH}}))
            stack.enter_context(patch.object(orch.trader_wallets, "sign_transaction",
                                             return_value="SIGNED-B64"))
            stack.enter_context(patch.object(orch.bonding_curve, "fetch",
                                             return_value=_fresh_curve()))
            stack.enter_context(patch.object(orch.tg_trader_runner, "build_buy_tx",
                                             return_value=_build_envelope()))
            stack.enter_context(patch.object(orch.tg_trader_runner, "submit_bundle",
                                             side_effect=fake_submit))
            orch.buy(42, MINT, 0.5, submit_regions=["ny", "frankfurt"])
        self.assertEqual(captured["regions"], ["ny", "frankfurt"])
        self.assertEqual(captured["live"], False)

    def test_signing_step_is_called_with_unsigned_tx_from_builder(self):
        """Custody check: sign_transaction MUST receive the unsigned tx
        the Rust builder produced — not something else."""
        captured = {}
        def fake_sign(user_id, tx_b64):
            captured["tx_b64"] = tx_b64
            return "SIGNED-B64"

        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch.object(orch.trader_wallets, "get_or_create_wallet",
                                             return_value={"public_key": PAYER}))
            stack.enter_context(patch.object(orch.trader_wallets, "_rpc_call",
                                             return_value={"value": {"blockhash": BLOCKHASH}}))
            stack.enter_context(patch.object(orch.trader_wallets, "sign_transaction",
                                             side_effect=fake_sign))
            stack.enter_context(patch.object(orch.bonding_curve, "fetch",
                                             return_value=_fresh_curve()))
            stack.enter_context(patch.object(orch.tg_trader_runner, "build_buy_tx",
                                             return_value=_build_envelope(tx_b64="MY-UNSIGNED")))
            stack.enter_context(patch.object(orch.tg_trader_runner, "submit_bundle",
                                             return_value=_dry_run_submit()))
            orch.buy(42, MINT, 0.5)
        self.assertEqual(captured["tx_b64"], "MY-UNSIGNED")


# ── Failure paths ───────────────────────────────────────────────────────

class TestFailurePaths(_Base):

    def _full_happy_patches(self):
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch.object(orch.trader_wallets, "get_or_create_wallet",
                                         return_value={"public_key": PAYER}))
        stack.enter_context(patch.object(orch.trader_wallets, "_rpc_call",
                                         return_value={"value": {"blockhash": BLOCKHASH}}))
        stack.enter_context(patch.object(orch.trader_wallets, "sign_transaction",
                                         return_value="SIGNED-B64"))
        stack.enter_context(patch.object(orch.bonding_curve, "fetch",
                                         return_value=_fresh_curve()))
        stack.enter_context(patch.object(orch.tg_trader_runner, "build_buy_tx",
                                         return_value=_build_envelope()))
        stack.enter_context(patch.object(orch.tg_trader_runner, "submit_bundle",
                                         return_value=_dry_run_submit()))
        return stack

    def test_validate_rejects_zero_sol(self):
        with self.assertRaises(orch.OrchestratorError) as ctx:
            orch.buy(42, MINT, 0)
        self.assertEqual(ctx.exception.stage, "validate")

    def test_validate_rejects_negative_sol(self):
        with self.assertRaises(orch.OrchestratorError) as ctx:
            orch.buy(42, MINT, -1.0)
        self.assertEqual(ctx.exception.stage, "validate")

    def test_validate_rejects_bad_slippage(self):
        with self.assertRaises(orch.OrchestratorError) as ctx:
            orch.buy(42, MINT, 0.5, slippage_bps=99_999)
        self.assertEqual(ctx.exception.stage, "validate")

    def test_wallet_lookup_failure_routes_to_wallet_stage(self):
        with patch.object(orch.trader_wallets, "get_or_create_wallet",
                          side_effect=RuntimeError("db down")):
            with self.assertRaises(orch.OrchestratorError) as ctx:
                orch.buy(42, MINT, 0.5)
            self.assertEqual(ctx.exception.stage, "wallet")

    def test_curve_fetch_failure_routes_to_curve_stage(self):
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch.object(orch.trader_wallets, "get_or_create_wallet",
                                             return_value={"public_key": PAYER}))
            stack.enter_context(patch.object(orch.bonding_curve, "fetch",
                                             side_effect=orch.bonding_curve.BondingCurveError("rpc down")))
            with self.assertRaises(orch.OrchestratorError) as ctx:
                orch.buy(42, MINT, 0.5)
            self.assertEqual(ctx.exception.stage, "curve")

    def test_graduated_curve_routes_to_route_stage(self):
        """The defining acceptance test for the Jupiter fork point."""
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch.object(orch.trader_wallets, "get_or_create_wallet",
                                             return_value={"public_key": PAYER}))
            stack.enter_context(patch.object(orch.bonding_curve, "fetch",
                                             return_value=_fresh_curve(complete=True)))
            with self.assertRaises(orch.OrchestratorError) as ctx:
                orch.buy(42, MINT, 0.5)
            self.assertEqual(ctx.exception.stage, "route")
            self.assertIn("graduated", str(ctx.exception).lower())

    def test_blockhash_failure_routes_to_blockhash_stage(self):
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch.object(orch.trader_wallets, "get_or_create_wallet",
                                             return_value={"public_key": PAYER}))
            stack.enter_context(patch.object(orch.bonding_curve, "fetch",
                                             return_value=_fresh_curve()))
            stack.enter_context(patch.object(orch.trader_wallets, "_rpc_call",
                                             side_effect=RuntimeError("rpc 500")))
            with self.assertRaises(orch.OrchestratorError) as ctx:
                orch.buy(42, MINT, 0.5)
            self.assertEqual(ctx.exception.stage, "blockhash")

    def test_build_failure_routes_to_build_stage(self):
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch.object(orch.trader_wallets, "get_or_create_wallet",
                                             return_value={"public_key": PAYER}))
            stack.enter_context(patch.object(orch.bonding_curve, "fetch",
                                             return_value=_fresh_curve()))
            stack.enter_context(patch.object(orch.trader_wallets, "_rpc_call",
                                             return_value={"value": {"blockhash": BLOCKHASH}}))
            stack.enter_context(patch.object(orch.tg_trader_runner, "build_buy_tx",
                                             side_effect=orch.tg_trader_runner.TgTraderError("bad mint")))
            with self.assertRaises(orch.OrchestratorError) as ctx:
                orch.buy(42, MINT, 0.5)
            self.assertEqual(ctx.exception.stage, "build")

    def test_sign_failure_routes_to_sign_stage(self):
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch.object(orch.trader_wallets, "get_or_create_wallet",
                                             return_value={"public_key": PAYER}))
            stack.enter_context(patch.object(orch.bonding_curve, "fetch",
                                             return_value=_fresh_curve()))
            stack.enter_context(patch.object(orch.trader_wallets, "_rpc_call",
                                             return_value={"value": {"blockhash": BLOCKHASH}}))
            stack.enter_context(patch.object(orch.tg_trader_runner, "build_buy_tx",
                                             return_value=_build_envelope()))
            stack.enter_context(patch.object(orch.trader_wallets, "sign_transaction",
                                             side_effect=RuntimeError("key decrypt failed")))
            with self.assertRaises(orch.OrchestratorError) as ctx:
                orch.buy(42, MINT, 0.5)
            self.assertEqual(ctx.exception.stage, "sign")

    def test_submit_failure_routes_to_submit_stage(self):
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch.object(orch.trader_wallets, "get_or_create_wallet",
                                             return_value={"public_key": PAYER}))
            stack.enter_context(patch.object(orch.bonding_curve, "fetch",
                                             return_value=_fresh_curve()))
            stack.enter_context(patch.object(orch.trader_wallets, "_rpc_call",
                                             return_value={"value": {"blockhash": BLOCKHASH}}))
            stack.enter_context(patch.object(orch.tg_trader_runner, "build_buy_tx",
                                             return_value=_build_envelope()))
            stack.enter_context(patch.object(orch.trader_wallets, "sign_transaction",
                                             return_value="SIGNED-B64"))
            stack.enter_context(patch.object(orch.tg_trader_runner, "submit_bundle",
                                             side_effect=orch.tg_trader_runner.TgTraderError("jito 500")))
            with self.assertRaises(orch.OrchestratorError) as ctx:
                orch.buy(42, MINT, 0.5)
            self.assertEqual(ctx.exception.stage, "submit")

    def test_failure_before_position_write_leaves_no_position_row(self):
        """Property check: if anything before stage 7 fails, the position
        table stays empty. No phantom rows."""
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch.object(orch.trader_wallets, "get_or_create_wallet",
                                             return_value={"public_key": PAYER}))
            stack.enter_context(patch.object(orch.bonding_curve, "fetch",
                                             side_effect=orch.bonding_curve.BondingCurveError("nope")))
            try:
                orch.buy(42, MINT, 0.5)
            except orch.OrchestratorError:
                pass
        self.assertEqual(tp.list_open_positions("42"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
