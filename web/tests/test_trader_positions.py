"""
Unit tests for web/trader_positions.py.

Each test gets its own temp SQLite file via TRADER_DB_PATH so the suite
is fully isolated from /data/trader.sqlite. We exercise:
  - schema creation
  - create_position writes every field
  - list_open_positions filters by user + status
  - list_open_positions_for_mint cross-user lookup
  - mark_sold computes PnL and flips status
  - mark_buy_failed flips status with a reason
  - scale-in case: two buys of the same mint = two rows
  - failed buy starts in 'failed' status

Run with:
    python3 -m unittest web.tests.test_trader_positions -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _good_args(**overrides) -> dict:
    """Default keyword args for create_position. Override any field per test."""
    defaults = dict(
        user_id="42",
        mint="ABC" * 12,                          # 36 chars
        payer_pubkey="11111111111111111111111111111112",
        creator="11111111111111111111111111111113",
        is_cashback_coin=False,
        token_program="TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
        buy_sol_lamports=500_000_000,             # 0.5 SOL
        token_amount=17_883_333_333_333,
        entry_price_lamports_per_token=27.96,
        entry_mcap_sol=27.96e-6 * 1_000_000_000_000_000 / 1e9,
        slippage_bps=500,
        max_sol_cost_lamports=525_000_000,
        buy_signature="fake-sig-001",
        buy_phase="submitted",
        buy_route="pumpfun-pregrad",
        buy_tier="ACT",
        buy_signal_source="composite_score",
        buy_timestamp=1_700_000_000,
    )
    defaults.update(overrides)
    return defaults


class TestTraderPositions(unittest.TestCase):

    def setUp(self):
        # Fresh temp DB per test — wholly isolated
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        os.environ["TRADER_DB_PATH"] = self.tmp.name
        # Force re-import so the module picks up the env var at top of test
        import importlib
        global tp
        import trader_positions as _tp
        importlib.reload(_tp)
        tp = _tp
        tp.init_schema()

    def tearDown(self):
        os.environ.pop("TRADER_DB_PATH", None)
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    # ── create_position ─────────────────────────────────────────────────
    def test_create_position_returns_integer_id(self):
        pid = tp.create_position(**_good_args())
        self.assertIsInstance(pid, int)
        self.assertGreater(pid, 0)

    def test_created_position_reads_back_with_every_field(self):
        args = _good_args()
        pid = tp.create_position(**args)
        row = tp.get_position(pid)
        self.assertIsNotNone(row)
        for k in ("user_id", "mint", "payer_pubkey", "creator", "token_program",
                  "buy_sol_lamports", "token_amount",
                  "entry_price_lamports_per_token", "slippage_bps",
                  "max_sol_cost_lamports", "buy_signature", "buy_route",
                  "buy_tier", "buy_signal_source", "buy_timestamp"):
            self.assertEqual(row[k], args[k], f"mismatch on field {k}")
        # bool round-trips via int
        self.assertEqual(bool(row["is_cashback_coin"]), args["is_cashback_coin"])
        # status defaults to 'open' for a successful buy
        self.assertEqual(row["status"], "open")
        self.assertIsNone(row["sell_signature"])

    def test_buy_phase_failed_starts_in_failed_status(self):
        pid = tp.create_position(**_good_args(buy_phase="failed"))
        row = tp.get_position(pid)
        self.assertEqual(row["status"], "failed")

    def test_buy_phase_dry_run_is_open(self):
        pid = tp.create_position(**_good_args(buy_phase="dry-run"))
        row = tp.get_position(pid)
        self.assertEqual(row["status"], "open")
        self.assertEqual(row["buy_phase"], "dry-run")

    def test_invalid_buy_phase_rejected(self):
        with self.assertRaises(ValueError):
            tp.create_position(**_good_args(buy_phase="bogus"))

    def test_user_id_int_coerces_to_string(self):
        pid = tp.create_position(**_good_args(user_id=12345))
        row = tp.get_position(pid)
        self.assertEqual(row["user_id"], "12345")

    # ── scale-in: two buys = two rows ───────────────────────────────────
    def test_two_buys_of_same_mint_create_two_rows(self):
        pid1 = tp.create_position(**_good_args(buy_signature="sig-1"))
        pid2 = tp.create_position(**_good_args(buy_signature="sig-2"))
        self.assertNotEqual(pid1, pid2)
        opens = tp.list_open_positions("42")
        self.assertEqual(len(opens), 2)

    # ── list_open_positions ─────────────────────────────────────────────
    def test_list_open_positions_filters_by_user(self):
        tp.create_position(**_good_args(user_id="42", buy_signature="a"))
        tp.create_position(**_good_args(user_id="42", buy_signature="b"))
        tp.create_position(**_good_args(user_id="99", buy_signature="c"))
        opens_42 = tp.list_open_positions("42")
        opens_99 = tp.list_open_positions("99")
        self.assertEqual(len(opens_42), 2)
        self.assertEqual(len(opens_99), 1)

    def test_list_open_positions_excludes_sold_and_failed(self):
        pid_a = tp.create_position(**_good_args(buy_signature="a"))
        pid_b = tp.create_position(**_good_args(buy_signature="b"))
        pid_c = tp.create_position(**_good_args(buy_signature="c"))
        # Close out a, fail b
        tp.mark_sold(pid_a, sell_signature="sell-a", sell_sol_lamports=750_000_000)
        tp.mark_buy_failed(pid_b, reason="jito timeout")
        opens = tp.list_open_positions("42")
        self.assertEqual(len(opens), 1)
        self.assertEqual(opens[0]["id"], pid_c)

    def test_list_open_positions_orders_newest_first(self):
        tp.create_position(**_good_args(buy_signature="old", buy_timestamp=1_000))
        tp.create_position(**_good_args(buy_signature="new", buy_timestamp=2_000))
        opens = tp.list_open_positions("42")
        self.assertEqual(opens[0]["buy_signature"], "new")
        self.assertEqual(opens[1]["buy_signature"], "old")

    # ── list_open_positions_for_mint ────────────────────────────────────
    def test_list_for_mint_crosses_users(self):
        tp.create_position(**_good_args(user_id="42", buy_signature="a"))
        tp.create_position(**_good_args(user_id="99", buy_signature="b"))
        rows = tp.list_open_positions_for_mint("ABC" * 12)
        self.assertEqual(len(rows), 2)
        user_ids = {r["user_id"] for r in rows}
        self.assertEqual(user_ids, {"42", "99"})

    def test_list_for_mint_excludes_other_mints(self):
        tp.create_position(**_good_args(mint="ABC" * 12, buy_signature="a"))
        tp.create_position(**_good_args(mint="DEF" * 12, buy_signature="b"))
        rows = tp.list_open_positions_for_mint("ABC" * 12)
        self.assertEqual(len(rows), 1)

    # ── mark_sold ───────────────────────────────────────────────────────
    def test_mark_sold_computes_realized_pnl(self):
        pid = tp.create_position(**_good_args(buy_sol_lamports=500_000_000))
        tp.mark_sold(pid, sell_signature="sell-sig", sell_sol_lamports=750_000_000)
        row = tp.get_position(pid)
        self.assertEqual(row["status"], "sold")
        self.assertEqual(row["sell_signature"], "sell-sig")
        self.assertEqual(row["sell_sol_lamports"], 750_000_000)
        self.assertEqual(row["realized_pnl_lamports"], 250_000_000)

    def test_mark_sold_negative_pnl(self):
        pid = tp.create_position(**_good_args(buy_sol_lamports=500_000_000))
        tp.mark_sold(pid, sell_signature="rug", sell_sol_lamports=100_000_000)
        row = tp.get_position(pid)
        self.assertEqual(row["realized_pnl_lamports"], -400_000_000)

    def test_mark_sold_on_missing_position_raises(self):
        with self.assertRaises(KeyError):
            tp.mark_sold(99999, sell_signature="x", sell_sol_lamports=0)

    # ── mark_buy_failed ─────────────────────────────────────────────────
    def test_mark_buy_failed_sets_status_and_reason(self):
        pid = tp.create_position(**_good_args())
        tp.mark_buy_failed(pid, "jito rejected all regions")
        row = tp.get_position(pid)
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["fail_reason"], "jito rejected all regions")


class TestAutoExitConfig(unittest.TestCase):
    """Day 4.11 — auto-exit schema, user defaults, per-position overrides,
    and monitor-state writes."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        os.environ["TRADER_DB_PATH"] = self.tmp.name
        import importlib
        global tp
        import trader_positions as _tp
        importlib.reload(_tp)
        tp = _tp
        tp.init_schema()

    def tearDown(self):
        os.environ.pop("TRADER_DB_PATH", None)
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    # ── User defaults ────────────────────────────────────────────────
    def test_user_settings_returns_package_defaults_when_no_row(self):
        s = tp.get_user_settings("42")
        self.assertEqual(s["tp_ladder"], tp.DEFAULT_TP_LADDER)
        self.assertEqual(s["sl_pct"],        tp.DEFAULT_SL_PCT)
        self.assertEqual(s["tsl_pct"],       tp.DEFAULT_TSL_PCT)
        self.assertEqual(s["breakeven_pct"], tp.DEFAULT_BREAKEVEN_PCT)

    def test_user_settings_roundtrip(self):
        tp.set_user_settings(
            "42",
            tp_ladder=[{"pct": 100, "sell_pct": 30}, {"pct": 500, "sell_pct": 100}],
            sl_pct=-40, tsl_pct=25, breakeven_pct=15,
        )
        s = tp.get_user_settings("42")
        self.assertEqual(len(s["tp_ladder"]), 2)
        self.assertEqual(s["tp_ladder"][0]["pct"], 100)
        self.assertEqual(s["sl_pct"], -40)
        self.assertEqual(s["tsl_pct"], 25)
        self.assertEqual(s["breakeven_pct"], 15)

    def test_user_settings_partial_update_preserves_unset_fields(self):
        tp.set_user_settings("42", sl_pct=-30)
        s = tp.get_user_settings("42")
        self.assertEqual(s["sl_pct"], -30)
        # Others should be defaults
        self.assertEqual(s["tsl_pct"], tp.DEFAULT_TSL_PCT)
        # Now update tsl only — sl should stay at -30
        tp.set_user_settings("42", tsl_pct=20)
        s = tp.get_user_settings("42")
        self.assertEqual(s["sl_pct"], -30)
        self.assertEqual(s["tsl_pct"], 20)

    # ── Per-position overrides ──────────────────────────────────────────
    def test_get_position_auto_exit_returns_none_for_missing(self):
        self.assertIsNone(tp.get_position_auto_exit(99999))

    def test_set_position_auto_exit_persists_ladder(self):
        pid = tp.create_position(**_good_args())
        tp.set_position_auto_exit(pid,
            tp_ladder=[{"pct": 75, "sell_pct": 60}], sl_pct=-25, tsl_pct=15,
        )
        cfg = tp.get_position_auto_exit(pid)
        self.assertEqual(len(cfg["tp_ladder"]), 1)
        self.assertEqual(cfg["tp_ladder"][0]["pct"], 75)
        self.assertEqual(cfg["sl_pct"], -25)
        self.assertEqual(cfg["tsl_pct"], 15)
        self.assertEqual(cfg["next_tp_index"], 0)
        self.assertFalse(cfg["sl_armed_at_breakeven"])

    def test_set_position_auto_exit_partial_update_preserves_others(self):
        pid = tp.create_position(**_good_args())
        tp.set_position_auto_exit(pid, sl_pct=-40, tsl_pct=25)
        tp.set_position_auto_exit(pid, sl_pct=-20)  # update only sl
        cfg = tp.get_position_auto_exit(pid)
        self.assertEqual(cfg["sl_pct"], -20)
        self.assertEqual(cfg["tsl_pct"], 25)  # unchanged

    # ── Monitor state ────────────────────────────────────────────────
    def test_update_monitor_state_advances_high_water_mark(self):
        pid = tp.create_position(**_good_args())
        tp.update_position_monitor_state(pid,
            high_water_mark_lamports=1_200_000_000,
            last_monitor_check_at=1_700_000_000,
        )
        cfg = tp.get_position_auto_exit(pid)
        self.assertEqual(cfg["high_water_mark_lamports"], 1_200_000_000)
        row = tp.get_position(pid)
        self.assertEqual(row["last_monitor_check_at"], 1_700_000_000)

    def test_update_monitor_state_advances_next_tp_index(self):
        pid = tp.create_position(**_good_args())
        tp.update_position_monitor_state(pid, next_tp_index=1)
        cfg = tp.get_position_auto_exit(pid)
        self.assertEqual(cfg["next_tp_index"], 1)

    def test_update_monitor_state_arms_breakeven_once(self):
        pid = tp.create_position(**_good_args())
        cfg = tp.get_position_auto_exit(pid)
        self.assertFalse(cfg["sl_armed_at_breakeven"])
        tp.update_position_monitor_state(pid, sl_armed_at_breakeven=True)
        self.assertTrue(tp.get_position_auto_exit(pid)["sl_armed_at_breakeven"])

    # ── Exit reason ────────────────────────────────────────────────────
    def test_set_exit_reason_stamps_row(self):
        pid = tp.create_position(**_good_args())
        tp.set_exit_reason(pid, "tp1")
        row = tp.get_position(pid)
        self.assertEqual(row["exit_reason"], "tp1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
