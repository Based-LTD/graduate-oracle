"""
Unit tests for web/trader_orchestrator.py.

Every external dependency is mocked:
  - trader_wallets.get_or_create_wallet  (wallet lookup)
  - trader_wallets._rpc_call             (blockhash fetch)
  - trader_wallets.sign_transaction      (custody)
  - bonding_curve.fetch                  (RPC + decode)
  - jupiter_buy.build_buy_tx             (Jupiter quote + swap-tx, post-pivot)
  - tg_trader_runner.submit_bundle       (Rust dry-run path)
  - rpc_submit.send_via_rpc              (live path)

Position DB is a per-test temp file via TRADER_DB_PATH.

The tests cover:
  - happy dry-run path end-to-end
  - happy live-mocked path
  - every stage's failure path raises OrchestratorError with the right stage
  - graduated curves are rejected (route stage)
  - input validation rejects bad sol / slippage
  - cashback flag propagates from curve → position row
  - tier + signal_source persist

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
    """Mock return value for jupiter_buy.build_buy_tx — matches the
    Jupiter envelope the orchestrator now consumes."""
    base = {
        "route":                          "jupiter:Pump.fun",
        "tx_b64":                         "UNSIGNED-B64",
        "buy_lamports":                   500_000_000,
        "slippage_bps":                   500,
        "max_sol_cost_lamports":          525_000_000,
        "expected_tokens_out":            17_883_333_333_333,
        "min_tokens_out":                 17_000_000_000_000,
        "entry_price_lamports_per_token": 27.96,
        "entry_mcap_sol":                 27.96,
        "is_cashback_coin":               False,
        "jupiter_route":                  ["Pump.fun"],
        "price_impact_pct":               0.0,
        "jupiter_quote":                  {
            "outAmount":               "17883333333333",
            "otherAmountThreshold":    "17000000000000",
            "contextSlot":             427000000,
            "primary_route":           "Pump.fun",
        },
        "accounts": {
            "token_program":           "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
        },
    }
    base.update(overrides)
    return base


def _dry_run_submit(signature: str = "SIG-DRY") -> dict:
    """Mock for the Rust dry-run submit_bundle path."""
    return {
        "phase":         "dry-run",
        "would_submit":  False,
        "signature":     signature,
        "n_signatures":  1,
        "tx_bytes":      300,
        "regions":       ["ny", "amsterdam", "frankfurt", "tokyo", "slc"],
        "n_regions":     5,
    }


def _live_rpc_submit(signature: str = "SIG-LIVE") -> dict:
    """Mock for rpc_submit.send_via_rpc — the live submission path."""
    return {
        "phase":          "submitted_via_rpc",
        "ok":             True,
        "signature":      signature,
        "rpc_url":        "https://mocked-rpc",
        "skip_preflight": True,
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

    def _patches(self, *, curve=None, envelope=None,
                 dry_run_submit=None, rpc_submit_result=None):
        """Build the standard set of patches for the success path.

        Dry-run path uses orch.tg_trader_runner.submit_bundle (Rust dry-run).
        Live path uses orch.rpc_submit.send_via_rpc (the real submission)."""
        return [
            patch.object(orch.trader_wallets, "get_or_create_wallet",
                         return_value={"public_key": PAYER}),
            patch.object(orch.trader_wallets, "_rpc_call",
                         return_value={"value": {"blockhash": BLOCKHASH}}),
            patch.object(orch.trader_wallets, "sign_transaction",
                         return_value="SIGNED-B64"),
            patch.object(orch.bonding_curve, "fetch",
                         return_value=curve or _fresh_curve()),
            patch.object(orch.jupiter_buy, "build_buy_tx",
                         return_value=envelope or _build_envelope()),
            patch.object(orch.tg_trader_runner, "submit_bundle",
                         return_value=dry_run_submit or _dry_run_submit()),
            patch.object(orch.rpc_submit, "send_via_rpc",
                         return_value=rpc_submit_result or _live_rpc_submit()),
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
        self.assertEqual(result["route"], "jupiter:Pump.fun")
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
            for p in self._patches(rpc_submit_result=_live_rpc_submit("SIG-LIVE")):
                stack.enter_context(p)
            result = orch.buy(42, MINT, 0.5, live=True)
        # Live path uses rpc_submit which returns phase="submitted_via_rpc",
        # but the orchestrator normalizes that to "submitted" in its envelope.
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
        """For dry-runs, submit_regions is forwarded to the Rust submit_bundle."""
        captured = {}
        def fake_submit(user_id, signed_tx_b64, *, regions=None, live=False, **kw):
            captured["regions"] = regions
            captured["live"]    = live
            return _dry_run_submit()

        from contextlib import ExitStack
        with ExitStack() as stack:
            for p in self._patches():
                stack.enter_context(p)
            # Replace the submit_bundle mock with our spy
            stack.enter_context(patch.object(orch.tg_trader_runner, "submit_bundle",
                                             side_effect=fake_submit))
            orch.buy(42, MINT, 0.5, submit_regions=["ny", "frankfurt"])
        self.assertEqual(captured["regions"], ["ny", "frankfurt"])
        self.assertEqual(captured["live"], False)

    def test_signing_step_is_called_with_unsigned_tx_from_builder(self):
        """Custody check: sign_transaction MUST receive the unsigned tx
        Jupiter produced — not something else."""
        captured = {}
        def fake_sign(user_id, tx_b64):
            captured["tx_b64"] = tx_b64
            return "SIGNED-B64"

        from contextlib import ExitStack
        with ExitStack() as stack:
            for p in self._patches(envelope=_build_envelope(tx_b64="MY-UNSIGNED")):
                stack.enter_context(p)
            # Override sign with our spy
            stack.enter_context(patch.object(orch.trader_wallets, "sign_transaction",
                                             side_effect=fake_sign))
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

    def test_orchestrator_error_carries_user_facing_msg(self):
        """Every OrchestratorError must expose .user_facing_msg —
        the TG bot renders this directly to users, so it can't be empty
        or contain Python tracebacks/internals."""
        for stage in ("validate", "wallet", "balance", "curve", "blockhash",
                      "build", "sign", "submit", "post_submit_accounting",
                      "position", "unknown_stage_xyz"):
            err = orch.OrchestratorError(stage, "some technical detail")
            self.assertTrue(err.user_facing_msg,
                            f"empty user_facing_msg for stage={stage!r}")
            self.assertNotIn("Traceback", err.user_facing_msg)
            self.assertNotIn("Exception", err.user_facing_msg)
            self.assertEqual(err.stage, stage)
            self.assertEqual(err.detail, "some technical detail")

    def test_validate_rejects_zero_sol(self):
        with self.assertRaises(orch.OrchestratorError) as ctx:
            orch.buy(42, MINT, 0)
        self.assertEqual(ctx.exception.stage, "validate")
        # Verify the user-facing message is filled in
        self.assertIn("Invalid trade", ctx.exception.user_facing_msg)

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

    def test_balance_check_blocks_buy_when_insufficient_live(self):
        """Live buy with balance < (sol + slippage + tip + tx_overhead)
        must raise OrchestratorError(balance), saving the wasted tx fee."""
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch.object(orch.trader_wallets, "get_or_create_wallet",
                                             return_value={"public_key": PAYER}))
            # 100k lamports balance, but we want to buy 0.5 SOL
            stack.enter_context(patch.object(orch.trader_wallets, "get_balance_lamports",
                                             return_value=100_000))
            stack.enter_context(patch.object(orch.jito_tip_floor, "get_tip_lamports",
                                             return_value=50_000))
            with self.assertRaises(orch.OrchestratorError) as ctx:
                orch.buy(42, MINT, 0.5, live=True)
            self.assertEqual(ctx.exception.stage, "balance")
            self.assertIn("insufficient", str(ctx.exception))

    def test_balance_check_passes_when_sufficient_live(self):
        """Sufficient balance → buy proceeds past stage 1.5 without raising."""
        from contextlib import ExitStack
        with ExitStack() as stack:
            for p in self._full_happy_patches_list():
                stack.enter_context(p)
            stack.enter_context(patch.object(orch.trader_wallets, "get_balance_lamports",
                                             return_value=1_000_000_000))  # 1 SOL
            stack.enter_context(patch.object(orch.jito_tip_floor, "get_tip_lamports",
                                             return_value=50_000))
            # Should reach build stage and succeed
            result = orch.buy(42, MINT, 0.5, live=True)
            self.assertEqual(result["phase"], "submitted")

    def test_balance_check_skipped_on_dry_run(self):
        """Dry-runs should NOT block on balance — they're for testing the
        pipeline without owning funds."""
        from contextlib import ExitStack
        with ExitStack() as stack:
            for p in self._patches_list_no_balance_or_rpc():
                stack.enter_context(p)
            # No balance mock — if check ran, this would AttributeError or
            # hit real RPC. Default live=False should skip the check.
            result = orch.buy(42, MINT, 0.5)
            self.assertEqual(result["phase"], "dry-run")

    def _full_happy_patches_list(self):
        """Builder for the FULL set of patches needed for a successful live
        buy (used by balance check tests)."""
        return [
            patch.object(orch.trader_wallets, "get_or_create_wallet",
                         return_value={"public_key": PAYER}),
            patch.object(orch.bonding_curve, "fetch",
                         return_value=_fresh_curve()),
            patch.object(orch.trader_wallets, "_rpc_call",
                         return_value={"value": {"blockhash": BLOCKHASH}}),
            patch.object(orch.jupiter_buy, "build_buy_tx",
                         return_value=_build_envelope()),
            patch.object(orch.trader_wallets, "sign_transaction",
                         return_value="SIGNED-B64"),
            patch.object(orch.rpc_submit, "send_via_rpc",
                         return_value=_live_rpc_submit()),
        ]

    def _patches_list_no_balance_or_rpc(self):
        """Dry-run happy-path patches without balance/rpc — used to verify
        the balance check is correctly skipped on dry-runs."""
        return [
            patch.object(orch.trader_wallets, "get_or_create_wallet",
                         return_value={"public_key": PAYER}),
            patch.object(orch.bonding_curve, "fetch",
                         return_value=_fresh_curve()),
            patch.object(orch.trader_wallets, "_rpc_call",
                         return_value={"value": {"blockhash": BLOCKHASH}}),
            patch.object(orch.jupiter_buy, "build_buy_tx",
                         return_value=_build_envelope()),
            patch.object(orch.trader_wallets, "sign_transaction",
                         return_value="SIGNED-B64"),
            patch.object(orch.tg_trader_runner, "submit_bundle",
                         return_value=_dry_run_submit()),
        ]

    # NOTE: curve fetch failure used to raise OrchestratorError("curve").
    # Post Day-4.25 it no longer does — we tolerate missing/closed curves
    # and let Jupiter route. See test_curve_fetch_failure_does_not_block_jupiter
    # in the happy-path class for the replacement coverage.

    def test_graduated_curve_proceeds_via_jupiter(self):
        """Post Day-4.25: graduated curves are NO LONGER rejected. Jupiter
        routes them via Raydium/PumpSwap. Verify the flow completes."""
        from contextlib import ExitStack
        with ExitStack() as stack:
            for p in self._full_happy_patches_list():
                stack.enter_context(p)
            # Swap the curve patch to return a complete=True curve
            stack.enter_context(patch.object(orch.bonding_curve, "fetch",
                                             return_value=_fresh_curve(complete=True)))
            stack.enter_context(patch.object(orch.trader_wallets, "get_balance_lamports",
                                             return_value=1_000_000_000))
            stack.enter_context(patch.object(orch.jito_tip_floor, "get_tip_lamports",
                                             return_value=50_000))
            # Should NOT raise — should complete successfully
            result = orch.buy(42, MINT, 0.5, live=True)
            self.assertEqual(result["phase"], "submitted")

    def test_curve_fetch_failure_does_not_block_jupiter(self):
        """If the curve account is closed entirely (very old grads),
        we proceed with stub data and let Jupiter quote."""
        from contextlib import ExitStack
        with ExitStack() as stack:
            for p in self._full_happy_patches_list():
                stack.enter_context(p)
            stack.enter_context(patch.object(orch.bonding_curve, "fetch",
                side_effect=orch.bonding_curve.BondingCurveError("curve closed")))
            stack.enter_context(patch.object(orch.trader_wallets, "get_balance_lamports",
                                             return_value=1_000_000_000))
            stack.enter_context(patch.object(orch.jito_tip_floor, "get_tip_lamports",
                                             return_value=50_000))
            result = orch.buy(42, MINT, 0.5, live=True)
            self.assertEqual(result["phase"], "submitted")

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
            stack.enter_context(patch.object(orch.jupiter_buy, "build_buy_tx",
                                             side_effect=orch.jupiter_buy.JupiterError("quote failed")))
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
            stack.enter_context(patch.object(orch.jupiter_buy, "build_buy_tx",
                                             return_value=_build_envelope()))
            stack.enter_context(patch.object(orch.trader_wallets, "sign_transaction",
                                             side_effect=RuntimeError("key decrypt failed")))
            with self.assertRaises(orch.OrchestratorError) as ctx:
                orch.buy(42, MINT, 0.5)
            self.assertEqual(ctx.exception.stage, "sign")

    def test_submit_failure_dry_run_routes_to_submit_stage(self):
        """Dry-run Jito submit failure → OrchestratorError(submit)."""
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch.object(orch.trader_wallets, "get_or_create_wallet",
                                             return_value={"public_key": PAYER}))
            stack.enter_context(patch.object(orch.bonding_curve, "fetch",
                                             return_value=_fresh_curve()))
            stack.enter_context(patch.object(orch.trader_wallets, "_rpc_call",
                                             return_value={"value": {"blockhash": BLOCKHASH}}))
            stack.enter_context(patch.object(orch.jupiter_buy, "build_buy_tx",
                                             return_value=_build_envelope()))
            stack.enter_context(patch.object(orch.trader_wallets, "sign_transaction",
                                             return_value="SIGNED-B64"))
            stack.enter_context(patch.object(orch.tg_trader_runner, "submit_bundle",
                                             side_effect=orch.tg_trader_runner.TgTraderError("jito 500")))
            with self.assertRaises(orch.OrchestratorError) as ctx:
                orch.buy(42, MINT, 0.5)  # live=False
            self.assertEqual(ctx.exception.stage, "submit")

    def test_submit_failure_live_rpc_routes_to_submit_stage(self):
        """Live RPC submit failure → OrchestratorError(submit)."""
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch.object(orch.trader_wallets, "get_or_create_wallet",
                                             return_value={"public_key": PAYER}))
            stack.enter_context(patch.object(orch.bonding_curve, "fetch",
                                             return_value=_fresh_curve()))
            stack.enter_context(patch.object(orch.trader_wallets, "_rpc_call",
                                             return_value={"value": {"blockhash": BLOCKHASH}}))
            stack.enter_context(patch.object(orch.jupiter_buy, "build_buy_tx",
                                             return_value=_build_envelope()))
            stack.enter_context(patch.object(orch.trader_wallets, "sign_transaction",
                                             return_value="SIGNED-B64"))
            stack.enter_context(patch.object(orch.rpc_submit, "send_via_rpc",
                                             return_value={"ok": False, "error": "RPC simulated failure"}))
            with self.assertRaises(orch.OrchestratorError) as ctx:
                orch.buy(42, MINT, 0.5, live=True)
            self.assertEqual(ctx.exception.stage, "submit")
            self.assertIn("RPC", str(ctx.exception))

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


# ── Sell ────────────────────────────────────────────────────────────────

def _sell_envelope(**overrides) -> dict:
    """Mock return for jupiter_buy.build_sell_tx."""
    base = {
        "route":                          "jupiter:Pump.fun",
        "tx_b64":                         "UNSIGNED-SELL-B64",
        "token_amount_in":                17_883_333_333_333,
        "slippage_bps":                   500,
        "expected_sol_out_lamports":      750_000_000,  # 0.75 SOL
        "min_sol_out_lamports":           712_500_000,
        "exit_price_lamports_per_token":  4.19e-5,
        "jupiter_route":                  ["Pump.fun"],
        "price_impact_pct":               0.5,
        "jupiter_quote":                  {
            "outAmount":               "750000000",
            "otherAmountThreshold":    "712500000",
            "contextSlot":             427000000,
            "primary_route":           "Pump.fun",
        },
    }
    base.update(overrides)
    return base


class TestSellPath(_Base):
    """Sell tests — set up a buy first, then exercise sell()."""

    def _seed_open_position(self) -> int:
        """Insert one open position row and return its id."""
        return tp.create_position(
            user_id="42",
            mint=MINT,
            payer_pubkey=PAYER,
            creator=CREATOR,
            is_cashback_coin=False,
            token_program="TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
            buy_sol_lamports=500_000_000,
            token_amount=17_883_333_333_333,
            entry_price_lamports_per_token=27.96,
            entry_mcap_sol=None,
            slippage_bps=500,
            max_sol_cost_lamports=525_000_000,
            buy_signature="BUY-SIG",
            buy_phase="submitted",
            buy_route="jupiter:Pump.fun",
            buy_tier=None,
            buy_signal_source="manual",
        )

    def test_sell_dry_run_returns_envelope_without_position_write(self):
        pid = self._seed_open_position()
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch.object(orch.trader_wallets, "get_or_create_wallet",
                                             return_value={"public_key": PAYER}))
            stack.enter_context(patch.object(orch.trader_wallets, "sign_transaction",
                                             return_value="SIGNED-SELL-B64"))
            stack.enter_context(patch.object(orch.jupiter_buy, "build_sell_tx",
                                             return_value=_sell_envelope()))
            result = orch.sell("42", pid)  # live=False default
        self.assertEqual(result["phase"], "dry-run")
        self.assertEqual(result["tokens_sold"], 17_883_333_333_333)
        self.assertEqual(result["expected_sol_out_lamports"], 750_000_000)
        # Position must still be open
        row = tp.get_position(pid)
        self.assertEqual(row["status"], "open")

    def test_sell_live_marks_position_sold_with_pnl(self):
        pid = self._seed_open_position()
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch.object(orch.trader_wallets, "get_or_create_wallet",
                                             return_value={"public_key": PAYER}))
            stack.enter_context(patch.object(orch.trader_wallets, "sign_transaction",
                                             return_value="SIGNED-SELL-B64"))
            stack.enter_context(patch.object(orch.jupiter_buy, "build_sell_tx",
                                             return_value=_sell_envelope()))
            stack.enter_context(patch.object(orch.rpc_submit, "send_via_rpc",
                                             return_value=_live_rpc_submit("SELL-SIG")))
            result = orch.sell("42", pid, live=True)
        self.assertEqual(result["phase"], "submitted")
        self.assertEqual(result["sell_signature"], "SELL-SIG")
        self.assertEqual(result["new_status"], "sold")
        row = tp.get_position(pid)
        self.assertEqual(row["status"], "sold")
        self.assertEqual(row["sell_signature"], "SELL-SIG")
        self.assertEqual(row["sell_sol_lamports"], 750_000_000)
        # realized_pnl = sell - buy = 0.75 - 0.5 SOL
        self.assertEqual(row["realized_pnl_lamports"], 250_000_000)

    def test_sell_partial_keeps_position_open_with_remaining_tokens(self):
        pid = self._seed_open_position()
        from contextlib import ExitStack
        with ExitStack() as stack:
            stack.enter_context(patch.object(orch.trader_wallets, "get_or_create_wallet",
                                             return_value={"public_key": PAYER}))
            stack.enter_context(patch.object(orch.trader_wallets, "sign_transaction",
                                             return_value="SIGNED-SELL-B64"))
            stack.enter_context(patch.object(orch.jupiter_buy, "build_sell_tx",
                                             return_value=_sell_envelope(token_amount_in=8_941_666_666_666)))
            stack.enter_context(patch.object(orch.rpc_submit, "send_via_rpc",
                                             return_value=_live_rpc_submit("PARTIAL-SIG")))
            result = orch.sell("42", pid, sell_pct=0.5, live=True)
        self.assertEqual(result["new_status"], "open")
        self.assertEqual(result["tokens_sold"], 8_941_666_666_666)
        row = tp.get_position(pid)
        self.assertEqual(row["status"], "open")
        # token_amount halved (one rounding loss permitted)
        self.assertAlmostEqual(row["token_amount"], 8_941_666_666_667, delta=1)

    def test_sell_rejects_invalid_pct(self):
        for bad in (0, -0.1, 1.1, 2.0):
            with self.assertRaises(orch.OrchestratorError) as ctx:
                orch.sell("42", 1, sell_pct=bad)
            self.assertEqual(ctx.exception.stage, "validate")

    def test_sell_rejects_unknown_position(self):
        with self.assertRaises(orch.OrchestratorError) as ctx:
            orch.sell("42", 99999)
        self.assertEqual(ctx.exception.stage, "position")

    def test_sell_rejects_position_owned_by_other_user(self):
        pid = self._seed_open_position()
        with self.assertRaises(orch.OrchestratorError) as ctx:
            orch.sell("999", pid)
        self.assertEqual(ctx.exception.stage, "position")

    def test_sell_rejects_already_closed_position(self):
        pid = self._seed_open_position()
        tp.mark_sold(pid, sell_signature="prior", sell_sol_lamports=600_000_000)
        with self.assertRaises(orch.OrchestratorError) as ctx:
            orch.sell("42", pid)
        self.assertEqual(ctx.exception.stage, "position")
        self.assertIn("sold", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
