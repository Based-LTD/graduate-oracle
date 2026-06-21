"""
Unit tests for web/trader_portfolio.py.

Mocks jupiter_buy.quote_sell so tests don't hit Jupiter. Uses a temp
TRADER_DB_PATH for the positions DB.
"""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


MINT = "ABC" * 12
PAYER = "11111111111111111111111111111112"
CREATOR = "11111111111111111111111111111113"


def _seed_position(tp, user_id="42", buy_lamports=500_000_000,
                   tokens=17_000_000_000_000, mint=MINT, sig="SIG"):
    return tp.create_position(
        user_id=user_id, mint=mint, payer_pubkey=PAYER, creator=CREATOR,
        is_cashback_coin=False,
        token_program="TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
        buy_sol_lamports=buy_lamports, token_amount=tokens,
        entry_price_lamports_per_token=buy_lamports / tokens,
        entry_mcap_sol=None, slippage_bps=500,
        max_sol_cost_lamports=int(buy_lamports * 1.05),
        buy_signature=sig, buy_phase="submitted", buy_route="jupiter:Pump.fun",
        buy_tier=None, buy_signal_source="manual",
    )


def _jupiter_quote(out_lamports: int, route: str = "Pump.fun") -> dict:
    return {
        "outAmount": str(out_lamports),
        "otherAmountThreshold": str(int(out_lamports * 0.95)),
        "routePlan": [{"swapInfo": {"label": route,
                                    "ammKey": "mock",
                                    "inputMint": "x",
                                    "outputMint": "y",
                                    "inAmount":  "1",
                                    "outAmount": str(out_lamports)},
                       "percent": 100}],
    }


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        os.environ["TRADER_DB_PATH"] = self.tmp.name
        global tp, pf
        import trader_positions as _tp
        importlib.reload(_tp)
        tp = _tp
        tp.init_schema()
        import trader_portfolio as _pf
        importlib.reload(_pf)
        pf = _pf

    def tearDown(self):
        os.environ.pop("TRADER_DB_PATH", None)
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass


class TestValuePosition(_Base):

    def test_winner_position_has_positive_pnl(self):
        pid = _seed_position(tp, buy_lamports=500_000_000)
        row = tp.get_position(pid)
        with patch.object(pf.jupiter_buy, "quote_sell",
                          return_value=_jupiter_quote(750_000_000)):
            enriched = pf.value_position(row)
        self.assertEqual(enriched["current_sol_value_lamports"], 750_000_000)
        self.assertEqual(enriched["unrealized_pnl_lamports"], 250_000_000)
        self.assertAlmostEqual(enriched["unrealized_pnl_pct"], 0.5)
        self.assertEqual(enriched["current_route"], "Pump.fun")
        self.assertIsNone(enriched["valuation_error"])

    def test_loser_position_has_negative_pnl(self):
        pid = _seed_position(tp, buy_lamports=500_000_000)
        row = tp.get_position(pid)
        with patch.object(pf.jupiter_buy, "quote_sell",
                          return_value=_jupiter_quote(100_000_000)):
            enriched = pf.value_position(row)
        self.assertEqual(enriched["unrealized_pnl_lamports"], -400_000_000)
        self.assertAlmostEqual(enriched["unrealized_pnl_pct"], -0.8)

    def test_dead_mint_surfaces_valuation_error(self):
        """Jupiter has no route for rugged/closed mints. Must NOT crash —
        surface the error in valuation_error so the bot can render
        'no quote available' instead of a broken row."""
        pid = _seed_position(tp)
        row = tp.get_position(pid)
        with patch.object(pf.jupiter_buy, "quote_sell",
                          side_effect=pf.jupiter_buy.JupiterError("no route")):
            enriched = pf.value_position(row)
        self.assertIsNone(enriched["current_sol_value_lamports"])
        self.assertIsNone(enriched["unrealized_pnl_lamports"])
        self.assertIn("no route", enriched["valuation_error"])

    def test_zero_token_position_short_circuits(self):
        """Defensive — if a row somehow has 0 tokens, value should NOT
        call Jupiter, just mark the error."""
        # Seed an unusual row with zero tokens
        with patch("jupiter_buy.quote_sell") as mock_quote:
            row = {"mint": MINT, "token_amount": 0, "buy_sol_lamports": 1_000_000}
            enriched = pf.value_position(row)
            mock_quote.assert_not_called()
        self.assertEqual(enriched["valuation_error"], "zero token amount or zero buy")


class TestPortfolioSummary(_Base):

    def test_summary_aggregates_pnl_across_positions(self):
        p1 = _seed_position(tp, buy_lamports=500_000_000, sig="a")
        p2 = _seed_position(tp, buy_lamports=300_000_000, sig="b")
        # p1 doubles, p2 halves
        quote_map = {
            tp.get_position(p1)["mint"]: _jupiter_quote(1_000_000_000),
            tp.get_position(p2)["mint"]: _jupiter_quote(150_000_000),
        }
        def fake_quote(*, mint, token_amount, **kw):
            return quote_map[mint]
        # Both seeded with same MINT — so quote_map only has one key.
        # Override: just return a fixed value per call.
        responses = [_jupiter_quote(1_000_000_000), _jupiter_quote(150_000_000)]
        with patch.object(pf.jupiter_buy, "quote_sell",
                          side_effect=responses):
            summary = pf.portfolio_summary("42")
        self.assertEqual(summary["n_open"], 2)
        self.assertEqual(summary["n_with_valuation"], 2)
        self.assertEqual(summary["total_cost_basis_lamports"], 800_000_000)
        self.assertEqual(summary["total_current_value_lamports"], 1_150_000_000)
        self.assertEqual(summary["total_unrealized_pnl_lamports"], 350_000_000)

    def test_summary_skips_unquotable_positions_from_totals(self):
        """Positions that can't be priced (dead mints) must NOT be
        counted in cost_basis or pnl — they'd skew the math."""
        _seed_position(tp, buy_lamports=500_000_000, sig="alive")
        _seed_position(tp, buy_lamports=300_000_000, sig="dead")
        responses = [
            _jupiter_quote(750_000_000),                            # alive position
            pf.jupiter_buy.JupiterError("no route"),               # dead position
        ]
        def side_effect(*a, **kw):
            r = responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        with patch.object(pf.jupiter_buy, "quote_sell", side_effect=side_effect):
            summary = pf.portfolio_summary("42")
        self.assertEqual(summary["n_open"], 2)
        self.assertEqual(summary["n_with_valuation"], 1)
        self.assertEqual(summary["n_unquotable"], 1)
        self.assertEqual(summary["total_current_value_lamports"], 750_000_000)
        # Cost basis should only include the alive position for PnL math
        self.assertEqual(summary["total_unrealized_pnl_lamports"], 250_000_000)

    def test_summary_empty_user_has_zero_pnl(self):
        summary = pf.portfolio_summary("nobody")
        self.assertEqual(summary["n_open"], 0)
        self.assertEqual(summary["total_unrealized_pnl_lamports"], 0)
        self.assertEqual(summary["total_unrealized_pnl_pct"], 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
