"""
Unit tests for web/fee_skim.py.

The compute_fee_split tests are pure (no env). The apply_fee tests
mock trader_wallets.internal_send_sol so nothing leaves any wallet.
"""

from __future__ import annotations

import importlib
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


OPERATOR = "11111111111111111111111111111112"
BUYBACK = "11111111111111111111111111111113"


def _reload_with_env(**env_overrides):
    """Reimport fee_skim with the given env overrides applied."""
    for k, v in env_overrides.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    import fee_skim as _fs
    importlib.reload(_fs)
    return _fs


class TestComputeFeeSplit(unittest.TestCase):

    def test_default_1pct_split_5050(self):
        """100 bps total = 1% of trade. 50/50 split → 0.5% each."""
        fs = _reload_with_env()
        split = fs.compute_fee_split(1_000_000_000)  # 1 SOL
        self.assertEqual(split["total_fee_lamports"],    10_000_000)  # 0.01 SOL
        self.assertEqual(split["operator_fee_lamports"], 5_000_000)   # 0.005 SOL
        self.assertEqual(split["buyback_fee_lamports"],  5_000_000)
        self.assertEqual(split["fee_bps_applied"],       100)

    def test_zero_trade_returns_zeros(self):
        fs = _reload_with_env()
        split = fs.compute_fee_split(0)
        self.assertEqual(split["total_fee_lamports"], 0)

    def test_rounding_remainder_goes_to_buyback(self):
        """Tiny trade where total fee = 1 lamport. Operator share = 0,
        buyback share = 1. Should not lose the lamport to rounding."""
        fs = _reload_with_env()
        split = fs.compute_fee_split(100)  # 1% = 1 lamport
        self.assertEqual(split["total_fee_lamports"],
                         split["operator_fee_lamports"] + split["buyback_fee_lamports"])
        self.assertEqual(split["total_fee_lamports"], 1)

    def test_custom_fee_bps_env(self):
        """TRADER_FEE_BPS=200 makes the fee 2%."""
        fs = _reload_with_env(TRADER_FEE_BPS="200")
        split = fs.compute_fee_split(1_000_000_000)
        self.assertEqual(split["total_fee_lamports"], 20_000_000)
        # cleanup
        _reload_with_env(TRADER_FEE_BPS=None)

    def test_invalid_bps_raises(self):
        fs = _reload_with_env()
        with self.assertRaises(ValueError):
            fs.compute_fee_split(1_000_000_000, fee_bps=-1)
        with self.assertRaises(ValueError):
            fs.compute_fee_split(1_000_000_000, fee_bps=20_000)

    def test_custom_operator_share(self):
        """30% to operator, 70% to buyback."""
        fs = _reload_with_env(FEE_OPERATOR_SHARE_PCT="0.3")
        split = fs.compute_fee_split(1_000_000_000)
        self.assertEqual(split["operator_fee_lamports"], 3_000_000)
        self.assertEqual(split["buyback_fee_lamports"],  7_000_000)
        _reload_with_env(FEE_OPERATOR_SHARE_PCT=None)


class TestIsEnabled(unittest.TestCase):

    def test_disabled_when_wallets_missing(self):
        fs = _reload_with_env(FEE_OPERATOR_WALLET=None, FEE_BUYBACK_WALLET=None)
        self.assertFalse(fs.is_enabled())

    def test_disabled_when_only_one_set(self):
        fs = _reload_with_env(FEE_OPERATOR_WALLET=OPERATOR, FEE_BUYBACK_WALLET=None)
        self.assertFalse(fs.is_enabled())
        _reload_with_env(FEE_OPERATOR_WALLET=None)

    def test_enabled_when_both_set(self):
        fs = _reload_with_env(FEE_OPERATOR_WALLET=OPERATOR,
                              FEE_BUYBACK_WALLET=BUYBACK)
        self.assertTrue(fs.is_enabled())
        _reload_with_env(FEE_OPERATOR_WALLET=None, FEE_BUYBACK_WALLET=None)


class TestApplyFee(unittest.TestCase):

    def test_disabled_returns_skipped_without_sending(self):
        """If fee wallets not configured, apply_fee must NOT send anything."""
        fs = _reload_with_env(FEE_OPERATOR_WALLET=None, FEE_BUYBACK_WALLET=None)
        with patch("trader_wallets.internal_send_sol") as mock_send:
            result = fs.apply_fee(user_id="42", trade_sol_lamports=1_000_000_000,
                                  trade_kind="buy", trade_signature="SIG")
            mock_send.assert_not_called()
        self.assertFalse(result["applied"])
        self.assertIn("not configured", result["skipped_reason"])

    def test_dry_run_returns_skipped_without_sending(self):
        fs = _reload_with_env(FEE_OPERATOR_WALLET=OPERATOR,
                              FEE_BUYBACK_WALLET=BUYBACK)
        with patch("trader_wallets.internal_send_sol") as mock_send:
            result = fs.apply_fee(user_id="42", trade_sol_lamports=1_000_000_000,
                                  trade_kind="buy", trade_signature="SIG",
                                  dry_run=True)
            mock_send.assert_not_called()
        self.assertFalse(result["applied"])
        self.assertEqual(result["skipped_reason"], "dry_run=True")
        _reload_with_env(FEE_OPERATOR_WALLET=None, FEE_BUYBACK_WALLET=None)

    def test_happy_path_sends_both_transfers(self):
        fs = _reload_with_env(FEE_OPERATOR_WALLET=OPERATOR,
                              FEE_BUYBACK_WALLET=BUYBACK)
        captured = []
        def fake_send(user_id, to_address, lamports):
            captured.append((to_address, lamports))
            return f"SIG-{len(captured)}"
        with patch("trader_wallets.internal_send_sol", side_effect=fake_send):
            result = fs.apply_fee(user_id="42", trade_sol_lamports=1_000_000_000,
                                  trade_kind="buy", trade_signature="BUY-SIG")
        self.assertTrue(result["applied"])
        self.assertEqual(result["operator_signature"], "SIG-1")
        self.assertEqual(result["buyback_signature"],  "SIG-2")
        # Two transfers: operator first, then buyback, with correct amounts
        self.assertEqual(captured[0], (OPERATOR, 5_000_000))
        self.assertEqual(captured[1], (BUYBACK,  5_000_000))
        _reload_with_env(FEE_OPERATOR_WALLET=None, FEE_BUYBACK_WALLET=None)

    def test_transfer_failure_does_not_raise(self):
        """If a fee transfer fails, the function must NOT raise — the
        trade already succeeded and the user should not see a scary
        error. We just log + put it in the error field."""
        fs = _reload_with_env(FEE_OPERATOR_WALLET=OPERATOR,
                              FEE_BUYBACK_WALLET=BUYBACK)
        with patch("trader_wallets.internal_send_sol",
                   side_effect=RuntimeError("network down")):
            result = fs.apply_fee(user_id="42", trade_sol_lamports=1_000_000_000,
                                  trade_kind="buy", trade_signature="SIG")
        self.assertFalse(result["applied"])
        self.assertIn("network down", result["error"])
        _reload_with_env(FEE_OPERATOR_WALLET=None, FEE_BUYBACK_WALLET=None)

    def test_zero_fee_skipped(self):
        """Trade smaller than 100 lamports → fee rounds to 0 → skipped."""
        fs = _reload_with_env(FEE_OPERATOR_WALLET=OPERATOR,
                              FEE_BUYBACK_WALLET=BUYBACK)
        with patch("trader_wallets.internal_send_sol") as mock_send:
            result = fs.apply_fee(user_id="42", trade_sol_lamports=50,
                                  trade_kind="buy", trade_signature="SIG")
            mock_send.assert_not_called()
        self.assertFalse(result["applied"])
        self.assertEqual(result["skipped_reason"], "fee rounds to 0 lamports")
        _reload_with_env(FEE_OPERATOR_WALLET=None, FEE_BUYBACK_WALLET=None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
