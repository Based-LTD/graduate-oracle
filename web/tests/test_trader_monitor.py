"""
Unit tests for web/trader_monitor.py.

evaluate_position is a PURE function — exhaustive coverage across
all trigger combinations. tick() is the IMPURE driver; we mock
jupiter_buy.quote_sell + trader_orchestrator.sell and verify the
driver applies the right actions.

Run:
    python3 -m unittest web.tests.test_trader_monitor -v
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _pos(*, buy_lamports=1_000_000_000, token_amount=1_000_000,
         tp_ladder=None, sl_pct=None, tsl_pct=None, breakeven_pct=None,
         next_tp_index=0, hwm=None, armed=False,
         mint="ABC"*12, position_id=1, user_id="42") -> dict:
    """Build a position row dict like the DB would return."""
    return {
        "id":                          position_id,
        "user_id":                     user_id,
        "mint":                        mint,
        "buy_sol_lamports":            buy_lamports,
        "token_amount":                token_amount,
        "tp_ladder_json":              json.dumps(tp_ladder) if tp_ladder is not None else None,
        "sl_pct":                      sl_pct,
        "tsl_pct":                     tsl_pct,
        "breakeven_pct":               breakeven_pct,
        "next_tp_index":               next_tp_index,
        "high_water_mark_lamports":    hwm,
        "sl_armed_at_breakeven":       1 if armed else 0,
    }


import trader_monitor as mon


# ── evaluate_position — pure logic tests ────────────────────────────────

class TestEvaluatePositionTpLadder(unittest.TestCase):

    LADDER = [
        {"pct": 50,  "sell_pct": 30},
        {"pct": 200, "sell_pct": 50},
        {"pct": 500, "sell_pct": 100},
    ]

    def test_below_tp1_does_nothing(self):
        pos = _pos(tp_ladder=self.LADDER)
        # gain = +30% → below TP1 (50%)
        action = mon.evaluate_position(pos, 1_300_000_000)
        self.assertIsNone(action)

    def test_at_tp1_returns_partial_sell(self):
        pos = _pos(tp_ladder=self.LADDER)
        action = mon.evaluate_position(pos, 1_500_000_000)  # +50%
        self.assertIsNotNone(action)
        self.assertEqual(action["kind"], "tp")
        self.assertEqual(action["index"], 0)
        self.assertEqual(action["label"], "tp1")
        self.assertAlmostEqual(action["sell_pct"], 0.30)

    def test_after_tp1_advances_to_tp2(self):
        pos = _pos(tp_ladder=self.LADDER, next_tp_index=1)
        action = mon.evaluate_position(pos, 1_500_000_000)  # +50% — below tp2's 200%
        self.assertIsNone(action)
        action = mon.evaluate_position(pos, 3_000_000_000)  # +200%
        self.assertEqual(action["label"], "tp2")
        self.assertAlmostEqual(action["sell_pct"], 0.50)

    def test_tp_ladder_exhausted_no_more_triggers(self):
        pos = _pos(tp_ladder=self.LADDER, next_tp_index=3)  # past the end
        action = mon.evaluate_position(pos, 100_000_000_000)
        self.assertIsNone(action)


class TestEvaluatePositionStopLoss(unittest.TestCase):

    def test_above_sl_no_trigger(self):
        pos = _pos(sl_pct=-50.0)
        action = mon.evaluate_position(pos, 600_000_000)  # -40%
        self.assertIsNone(action)

    def test_at_sl_fires_full_sell(self):
        pos = _pos(sl_pct=-50.0)
        action = mon.evaluate_position(pos, 500_000_000)  # -50%
        self.assertEqual(action["kind"], "sl")

    def test_below_sl_fires_full_sell(self):
        pos = _pos(sl_pct=-50.0)
        action = mon.evaluate_position(pos, 400_000_000)  # -60%
        self.assertEqual(action["kind"], "sl")

    def test_breakeven_armed_flips_sl_to_zero(self):
        pos = _pos(sl_pct=-50.0, armed=True)
        # -1% → below the effective SL of 0%, fires immediately
        action = mon.evaluate_position(pos, 990_000_000)
        self.assertEqual(action["kind"], "sl")

    def test_no_sl_configured_never_fires(self):
        pos = _pos(sl_pct=None)
        action = mon.evaluate_position(pos, 100_000_000)  # -90%
        self.assertIsNone(action)


class TestEvaluatePositionTrailingStop(unittest.TestCase):

    def test_tsl_inactive_when_hwm_below_entry(self):
        """No trail logic when we're underwater — SL handles that."""
        pos = _pos(tsl_pct=30.0, hwm=900_000_000)  # HWM below entry (1B)
        action = mon.evaluate_position(pos, 600_000_000)
        self.assertIsNone(action)

    def test_tsl_inactive_above_floor(self):
        # HWM = 2 SOL, tsl 30% → floor = 1.4 SOL. Current 1.6 SOL → above floor.
        pos = _pos(tsl_pct=30.0, hwm=2_000_000_000)
        action = mon.evaluate_position(pos, 1_600_000_000)
        self.assertIsNone(action)

    def test_tsl_fires_when_below_floor(self):
        # HWM 2 SOL, tsl 30% → floor 1.4 SOL. Current 1.3 SOL → fires.
        pos = _pos(tsl_pct=30.0, hwm=2_000_000_000)
        action = mon.evaluate_position(pos, 1_300_000_000)
        self.assertEqual(action["kind"], "tsl")

    def test_tsl_no_config_never_fires(self):
        pos = _pos(tsl_pct=None, hwm=5_000_000_000)
        action = mon.evaluate_position(pos, 1)  # crashing
        # SL=None too — nothing should fire
        self.assertIsNone(action)


class TestEvaluatePositionBreakeven(unittest.TestCase):

    def test_breakeven_arm_at_threshold(self):
        pos = _pos(breakeven_pct=20.0, sl_pct=-50.0)
        action = mon.evaluate_position(pos, 1_200_000_000)  # +20%
        self.assertEqual(action["kind"], "breakeven_arm")

    def test_breakeven_not_armed_again_once_already_armed(self):
        pos = _pos(breakeven_pct=20.0, sl_pct=-50.0, armed=True)
        action = mon.evaluate_position(pos, 1_500_000_000)
        # +50% > 20% but already armed → no breakeven_arm action
        self.assertNotEqual(action["kind"] if action else "", "breakeven_arm")

    def test_breakeven_before_threshold_no_arm(self):
        pos = _pos(breakeven_pct=20.0, sl_pct=-50.0)
        action = mon.evaluate_position(pos, 1_150_000_000)  # +15%
        self.assertIsNone(action)


class TestEvaluatePositionEvaluationOrder(unittest.TestCase):
    """If multiple triggers would fire simultaneously, the order matters."""

    LADDER = [{"pct": 50, "sell_pct": 30}]

    def test_sl_fires_even_when_tp_also_eligible(self):
        """Edge case: SL = -20%, TP1 = +50%. If price crashes to -25%,
        TP isn't met but SL is — SL wins (no false TP)."""
        pos = _pos(tp_ladder=self.LADDER, sl_pct=-20.0)
        action = mon.evaluate_position(pos, 750_000_000)  # -25%
        self.assertEqual(action["kind"], "sl")

    def test_breakeven_arms_before_tp_fires(self):
        """At +50% gain (TP1 threshold), but breakeven not yet armed.
        Breakeven_arm runs first."""
        pos = _pos(tp_ladder=self.LADDER, breakeven_pct=20.0, armed=False)
        action = mon.evaluate_position(pos, 1_500_000_000)
        self.assertEqual(action["kind"], "breakeven_arm")

    def test_with_breakeven_already_armed_tp_fires(self):
        """Same price, but if armed already, breakeven step is skipped
        and TP fires."""
        pos = _pos(tp_ladder=self.LADDER, breakeven_pct=20.0, armed=True)
        action = mon.evaluate_position(pos, 1_500_000_000)
        self.assertEqual(action["kind"], "tp")


class TestEvaluatePositionDefensive(unittest.TestCase):

    def test_zero_entry_returns_none(self):
        pos = _pos(buy_lamports=0, sl_pct=-50.0)
        action = mon.evaluate_position(pos, 1_000_000_000)
        self.assertIsNone(action)

    def test_zero_current_returns_none(self):
        pos = _pos(sl_pct=-50.0)
        action = mon.evaluate_position(pos, 0)
        self.assertIsNone(action)

    def test_malformed_ladder_json_skipped_gracefully(self):
        pos = _pos(sl_pct=-50.0)
        pos["tp_ladder_json"] = "not valid json"
        # Should not crash — just no TP fires
        action = mon.evaluate_position(pos, 2_000_000_000)
        self.assertIsNone(action)


# ── tick — driver tests ────────────────────────────────────────────────

class _TickBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        os.environ["TRADER_DB_PATH"] = self.tmp.name
        global tp, mon_mod
        import trader_positions as _tp
        importlib.reload(_tp)
        tp = _tp
        tp.init_schema()
        import trader_monitor as _mon
        importlib.reload(_mon)
        mon_mod = _mon

    def tearDown(self):
        os.environ.pop("TRADER_DB_PATH", None)
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def _seed(self, **overrides) -> int:
        defaults = dict(
            user_id="42", mint="ABC"*12,
            payer_pubkey="11111111111111111111111111111112",
            creator="11111111111111111111111111111113",
            is_cashback_coin=False,
            token_program="TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
            buy_sol_lamports=1_000_000_000, token_amount=1_000_000,
            entry_price_lamports_per_token=1000.0, entry_mcap_sol=None,
            slippage_bps=500, max_sol_cost_lamports=1_050_000_000,
            buy_signature="BUY", buy_phase="submitted",
            buy_route="jupiter:Pump.fun",
            buy_tier=None, buy_signal_source="manual",
        )
        defaults.update(overrides)
        return tp.create_position(**defaults)


def _quote(out_lamports: int) -> dict:
    return {"outAmount": str(out_lamports),
            "otherAmountThreshold": str(int(out_lamports * 0.95)),
            "routePlan": []}


class TestTick(_TickBase):

    def test_no_open_positions_returns_zero_counts(self):
        out = mon_mod.tick("42")
        self.assertEqual(out["n_open"], 0)
        self.assertEqual(out["n_actions_taken"], 0)

    def test_updates_hwm_when_price_rises(self):
        pid = self._seed()
        tp.set_position_auto_exit(pid, tp_ladder=[{"pct": 999, "sell_pct": 100}])
        with patch.object(mon_mod.jupiter_buy, "quote_sell",
                          return_value=_quote(1_500_000_000)):
            out = mon_mod.tick("42", dry_run=True)
        cfg = tp.get_position_auto_exit(pid)
        self.assertEqual(cfg["high_water_mark_lamports"], 1_500_000_000)
        self.assertEqual(out["n_actions_taken"], 0)  # no rule triggered

    def test_tp_fire_dry_run_does_not_call_sell(self):
        pid = self._seed()
        tp.set_position_auto_exit(pid,
            tp_ladder=[{"pct": 50, "sell_pct": 50}])
        with patch.object(mon_mod.jupiter_buy, "quote_sell",
                          return_value=_quote(1_500_000_000)):  # +50%
            # Import orchestrator at module level to mock cleanly
            import trader_orchestrator
            with patch.object(trader_orchestrator, "sell") as mock_sell:
                out = mon_mod.tick("42", dry_run=True)
                mock_sell.assert_not_called()
        self.assertEqual(out["n_actions_taken"], 1)
        self.assertEqual(out["actions"][0]["action"]["kind"], "tp")
        self.assertTrue(out["actions"][0]["dry_run"])

    def test_unquotable_mint_logged_not_actioned(self):
        pid = self._seed()
        tp.set_position_auto_exit(pid, sl_pct=-50.0)
        with patch.object(mon_mod.jupiter_buy, "quote_sell",
                          side_effect=mon_mod.jupiter_buy.JupiterError("no route")):
            out = mon_mod.tick("42")
        self.assertEqual(out["n_unquotable"], 1)
        self.assertEqual(out["n_actions_taken"], 0)

    def test_breakeven_arm_writes_flag_without_selling(self):
        pid = self._seed()
        tp.set_position_auto_exit(pid, breakeven_pct=20.0, sl_pct=-50.0)
        import trader_orchestrator
        with patch.object(mon_mod.jupiter_buy, "quote_sell",
                          return_value=_quote(1_250_000_000)):  # +25%
            with patch.object(trader_orchestrator, "sell") as mock_sell:
                out = mon_mod.tick("42", live=True)
                mock_sell.assert_not_called()  # breakeven_arm is pure state
        self.assertEqual(out["n_actions_taken"], 1)
        self.assertEqual(out["actions"][0]["action"]["kind"], "breakeven_arm")
        # The flag must now be set on the position row
        cfg = tp.get_position_auto_exit(pid)
        self.assertTrue(cfg["sl_armed_at_breakeven"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
