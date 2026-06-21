"""
Unit tests for web/tg_trader_runner.py.

The wrapper is a thin Python ↔ Rust bridge. These tests cover:
  - successful invocation + response parsing
  - error parsing (ok=false → TgTraderError)
  - binary resolution + missing binary path
  - timeout handling
  - malformed JSON output
  - typed wrappers (health, version, dry_run_buy) build correct commands

We MOCK subprocess.run rather than spawning the real binary, so the tests
run in milliseconds and don't depend on a compiled artifact existing on
the test machine. Integration with the real binary is covered separately
when the binary is built in CI/Docker.

Run with:
    python3 -m unittest web.tests.test_tg_trader_runner -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch, MagicMock

# Make sibling import work whether we're invoked as a module or a script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tg_trader_runner as runner


def _fake_completed(stdout: str, returncode: int = 0, stderr: str = "") -> MagicMock:
    """Build a subprocess.CompletedProcess-like mock."""
    m = MagicMock()
    m.stdout = stdout
    m.stderr = stderr
    m.returncode = returncode
    return m


class TestBinaryResolution(unittest.TestCase):
    """The runner needs to find tg-trader; verify the lookup precedence."""

    def test_env_override_wins(self):
        with patch("os.path.isfile", return_value=True), \
             patch("os.access", return_value=True), \
             patch.dict(os.environ, {"TG_TRADER_BIN": "/custom/path/tg-trader"}):
            self.assertEqual(runner._resolve_binary(), "/custom/path/tg-trader")

    def test_missing_binary_raises(self):
        """No env, no standard path, no $PATH → should raise loudly."""
        with patch("os.path.isfile", return_value=False), \
             patch("shutil.which", return_value=None), \
             patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError) as ctx:
                runner._resolve_binary()
            self.assertIn("tg-trader", str(ctx.exception).lower())


class TestInvokeHappyPath(unittest.TestCase):
    """Successful command → returns the `data` dict."""

    def setUp(self):
        # Pin a fake binary path so tests don't depend on filesystem state
        self._patch = patch.object(runner, "_resolve_binary", return_value="/fake/tg-trader")
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_returns_data_payload_on_success(self):
        response = json.dumps({"ok": True, "data": {"status": "alive"}})
        with patch("subprocess.run", return_value=_fake_completed(response)):
            result = runner.invoke({"cmd": "health"})
            self.assertEqual(result, {"status": "alive"})

    def test_strips_trailing_newlines(self):
        response = json.dumps({"ok": True, "data": {"v": 1}}) + "\n\n"
        with patch("subprocess.run", return_value=_fake_completed(response)):
            result = runner.invoke({"cmd": "version"})
            self.assertEqual(result, {"v": 1})

    def test_handles_empty_data_field(self):
        """ok=true but data omitted → return empty dict, NOT crash."""
        response = json.dumps({"ok": True})
        with patch("subprocess.run", return_value=_fake_completed(response)):
            result = runner.invoke({"cmd": "noop"})
            self.assertEqual(result, {})


class TestInvokeErrorPaths(unittest.TestCase):
    """Every error mode must raise TgTraderError, never propagate stdlib errors."""

    def setUp(self):
        self._patch = patch.object(runner, "_resolve_binary", return_value="/fake/tg-trader")
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_ok_false_raises_with_error_message(self):
        response = json.dumps({"ok": False, "error": "bad mint"})
        with patch("subprocess.run", return_value=_fake_completed(response)):
            with self.assertRaises(runner.TgTraderError) as ctx:
                runner.invoke({"cmd": "dry-run-buy"})
            self.assertIn("bad mint", str(ctx.exception))

    def test_ok_false_without_error_field_gets_generic_message(self):
        response = json.dumps({"ok": False})
        with patch("subprocess.run", return_value=_fake_completed(response)):
            with self.assertRaises(runner.TgTraderError) as ctx:
                runner.invoke({"cmd": "dry-run-buy"})
            # Should NOT crash on missing error field; should produce SOME message
            self.assertTrue(str(ctx.exception))

    def test_nonzero_exit_raises(self):
        with patch("subprocess.run", return_value=_fake_completed("", returncode=1, stderr="segfault")):
            with self.assertRaises(runner.TgTraderError) as ctx:
                runner.invoke({"cmd": "health"})
            self.assertIn("exited 1", str(ctx.exception))
            self.assertIn("segfault", str(ctx.exception))

    def test_empty_stdout_raises(self):
        """Binary returned 0 but said nothing — protocol violation."""
        with patch("subprocess.run", return_value=_fake_completed("")):
            with self.assertRaises(runner.TgTraderError) as ctx:
                runner.invoke({"cmd": "health"})
            self.assertIn("empty", str(ctx.exception).lower())

    def test_malformed_json_raises(self):
        with patch("subprocess.run", return_value=_fake_completed("not json at all")):
            with self.assertRaises(runner.TgTraderError) as ctx:
                runner.invoke({"cmd": "health"})
            self.assertIn("non-JSON", str(ctx.exception))

    def test_timeout_raises(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("tg-trader", 1.0)):
            with self.assertRaises(runner.TgTraderError) as ctx:
                runner.invoke({"cmd": "health"}, timeout_s=1.0)
            self.assertIn("timed out", str(ctx.exception))

    def test_binary_not_found_raises(self):
        with patch("subprocess.run", side_effect=FileNotFoundError("no such file")):
            with self.assertRaises(runner.TgTraderError) as ctx:
                runner.invoke({"cmd": "health"})
            self.assertIn("missing", str(ctx.exception))


class TestTypedWrappers(unittest.TestCase):
    """The convenience functions must build commands with the correct shape."""

    def setUp(self):
        self._patch = patch.object(runner, "_resolve_binary", return_value="/fake/tg-trader")
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_health_sends_correct_command(self):
        captured = {}
        def fake_run(args, input, **kw):
            captured["input"] = input
            return _fake_completed(json.dumps({"ok": True, "data": {"status": "alive"}}))
        with patch("subprocess.run", side_effect=fake_run):
            runner.health()
        sent = json.loads(captured["input"].strip())
        self.assertEqual(sent, {"cmd": "health"})

    def test_version_sends_correct_command(self):
        captured = {}
        def fake_run(args, input, **kw):
            captured["input"] = input
            return _fake_completed(json.dumps({"ok": True, "data": {}}))
        with patch("subprocess.run", side_effect=fake_run):
            runner.version()
        sent = json.loads(captured["input"].strip())
        self.assertEqual(sent, {"cmd": "version"})

    def test_dry_run_buy_default_slippage_omitted(self):
        """When no slippage passed, the field should NOT appear in the wire format."""
        captured = {}
        def fake_run(args, input, **kw):
            captured["input"] = input
            return _fake_completed(json.dumps({"ok": True, "data": {}}))
        with patch("subprocess.run", side_effect=fake_run):
            runner.dry_run_buy(42, "ABC" * 12, 0.5)  # user_id as int — must coerce
        sent = json.loads(captured["input"].strip())
        self.assertEqual(sent["cmd"], "dry-run-buy")
        self.assertEqual(sent["user_id"], "42")          # int → string
        self.assertEqual(sent["sol"], 0.5)
        self.assertNotIn("slippage_bps", sent)           # NOT present when default

    def test_dry_run_buy_explicit_slippage_passed_through(self):
        captured = {}
        def fake_run(args, input, **kw):
            captured["input"] = input
            return _fake_completed(json.dumps({"ok": True, "data": {}}))
        with patch("subprocess.run", side_effect=fake_run):
            runner.dry_run_buy("42", "ABC" * 12, 0.5, slippage_bps=200)
        sent = json.loads(captured["input"].strip())
        self.assertEqual(sent["slippage_bps"], 200)

    def test_dry_run_buy_strips_mint_whitespace(self):
        """A copy-pasted mint may have surrounding whitespace — we strip it."""
        captured = {}
        def fake_run(args, input, **kw):
            captured["input"] = input
            return _fake_completed(json.dumps({"ok": True, "data": {}}))
        with patch("subprocess.run", side_effect=fake_run):
            runner.dry_run_buy("42", "  " + ("ABC" * 12) + "\n", 0.5)
        sent = json.loads(captured["input"].strip())
        self.assertEqual(sent["mint"], "ABC" * 12)


class TestBuildBuyTx(unittest.TestCase):
    """The pump.fun pre-grad tx builder wrapper."""

    def setUp(self):
        self._patch = patch.object(runner, "_resolve_binary", return_value="/fake/tg-trader")
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def _fresh_curve(self) -> dict:
        return {
            "virtual_sol_reserves":   30_000_000_000,
            "virtual_token_reserves": 1_073_000_000_000_000,
            "real_sol_reserves":      0,
            "real_token_reserves":    793_100_000_000_000,
            "token_total_supply":     1_000_000_000_000_000,
            "complete":               False,
            "creator":                "11111111111111111111111111111113",
            "is_cashback_coin":       False,
        }

    def test_refuses_graduated_curve_before_subprocess(self):
        """Routing guardrail: we never even spawn the subprocess if the
        curve is already complete — the orchestrator should have routed to
        Jupiter. This is belt-and-braces with the Rust-side rejection."""
        curve = self._fresh_curve()
        curve["complete"] = True
        # Patch subprocess.run so a regression here is loud, not silent
        with patch("subprocess.run") as mock_run:
            with self.assertRaises(runner.TgTraderError) as ctx:
                runner.build_buy_tx(
                    42, "ABC" * 12, "11111111111111111111111111111112",
                    0.5, curve, "11111111111111111111111111111111",
                )
            self.assertIn("Jupiter", str(ctx.exception))
            mock_run.assert_not_called()

    def test_sends_correct_command_shape(self):
        captured = {}
        def fake_run(args, input, **kw):
            captured["input"] = input
            return _fake_completed(json.dumps({"ok": True, "data": {"route": "pumpfun-pregrad"}}))
        curve = self._fresh_curve()
        with patch("subprocess.run", side_effect=fake_run):
            runner.build_buy_tx(
                42, "  " + ("ABC" * 12) + "\n", "  11111111111111111111111111111112  ",
                0.5, curve, " 11111111111111111111111111111111\n",
                slippage_bps=300,
                priority_fee_microlamports=200_000,
                compute_units=250_000,
            )
        sent = json.loads(captured["input"].strip())
        self.assertEqual(sent["cmd"], "build-buy-tx")
        self.assertEqual(sent["user_id"], "42")
        # Leading/trailing whitespace stripped
        self.assertEqual(sent["mint"], "ABC" * 12)
        self.assertEqual(sent["payer"], "11111111111111111111111111111112")
        self.assertEqual(sent["recent_blockhash"], "11111111111111111111111111111111")
        self.assertEqual(sent["sol"], 0.5)
        self.assertEqual(sent["slippage_bps"], 300)
        self.assertEqual(sent["priority_fee_microlamports"], 200_000)
        self.assertEqual(sent["compute_units"], 250_000)
        # Bonding curve must be sent verbatim — Rust deserializes by name
        self.assertEqual(sent["bonding_curve"]["virtual_sol_reserves"],
                         30_000_000_000)
        self.assertEqual(sent["bonding_curve"]["complete"], False)
        self.assertEqual(sent["bonding_curve"]["creator"],
                         "11111111111111111111111111111113")

    def test_optional_fields_omitted_when_unset(self):
        captured = {}
        def fake_run(args, input, **kw):
            captured["input"] = input
            return _fake_completed(json.dumps({"ok": True, "data": {}}))
        with patch("subprocess.run", side_effect=fake_run):
            runner.build_buy_tx(
                42, "ABC" * 12, "11111111111111111111111111111112",
                0.5, self._fresh_curve(), "11111111111111111111111111111111",
            )
        sent = json.loads(captured["input"].strip())
        # All optional kwargs MUST be absent when not passed — relying on
        # serde's `#[serde(default)]` to apply Rust defaults.
        self.assertNotIn("slippage_bps", sent)
        self.assertNotIn("priority_fee_microlamports", sent)
        self.assertNotIn("compute_units", sent)

    def test_returns_data_payload(self):
        envelope = {
            "route": "pumpfun-pregrad",
            "tx_b64": "BASE64-PLACEHOLDER",
            "expected_tokens_out": 17_883_333_333_333,
            "max_sol_cost_lamports": 525_000_000,
        }
        with patch("subprocess.run",
                   return_value=_fake_completed(json.dumps({"ok": True, "data": envelope}))):
            result = runner.build_buy_tx(
                42, "ABC" * 12, "11111111111111111111111111111112",
                0.5, self._fresh_curve(), "11111111111111111111111111111111",
            )
            self.assertEqual(result, envelope)

    def test_propagates_rust_validation_errors(self):
        """If Rust rejects (e.g. zero reserves, bad pubkey), surface as TgTraderError."""
        bad = json.dumps({"ok": False, "error": "bonding curve has zero reserves"})
        with patch("subprocess.run", return_value=_fake_completed(bad)):
            with self.assertRaises(runner.TgTraderError) as ctx:
                runner.build_buy_tx(
                    42, "ABC" * 12, "11111111111111111111111111111112",
                    0.5, self._fresh_curve(), "11111111111111111111111111111111",
                )
            self.assertIn("zero reserves", str(ctx.exception))


class TestBuildSellTx(unittest.TestCase):
    """The pump.fun pre-grad sell tx builder wrapper."""

    def setUp(self):
        self._patch = patch.object(runner, "_resolve_binary", return_value="/fake/tg-trader")
        self._patch.start()
        self.addCleanup(self._patch.stop)

    PAYER = "11111111111111111111111111111112"
    CREATOR = "11111111111111111111111111111113"
    BLOCKHASH = "11111111111111111111111111111111"
    MINT = "ABC" * 12  # 36 chars — passes the Rust validator

    def test_sends_correct_command_shape(self):
        captured = {}
        def fake_run(args, input, **kw):
            captured["input"] = input
            return _fake_completed(json.dumps({"ok": True, "data": {"route": "pumpfun-pregrad-sell"}}))
        with patch("subprocess.run", side_effect=fake_run):
            runner.build_sell_tx(
                42, "  " + self.MINT + "\n", "  " + self.PAYER, "  " + self.CREATOR,
                17_000_000_000_000, 450_000_000, " " + self.BLOCKHASH + "\n",
                is_cashback_coin=True,
                priority_fee_microlamports=200_000,
                compute_units=300_000,
            )
        sent = json.loads(captured["input"].strip())
        self.assertEqual(sent["cmd"], "build-sell-tx")
        self.assertEqual(sent["user_id"], "42")
        self.assertEqual(sent["mint"], self.MINT)
        self.assertEqual(sent["payer"], self.PAYER)
        self.assertEqual(sent["creator"], self.CREATOR)
        self.assertEqual(sent["token_amount"], 17_000_000_000_000)
        self.assertEqual(sent["min_sol_output_lamports"], 450_000_000)
        self.assertEqual(sent["is_cashback_coin"], True)
        self.assertEqual(sent["recent_blockhash"], self.BLOCKHASH)
        self.assertEqual(sent["priority_fee_microlamports"], 200_000)
        self.assertEqual(sent["compute_units"], 300_000)

    def test_cashback_defaults_to_false(self):
        """When kwarg omitted, is_cashback_coin MUST be False (not absent),
        because the Rust ix layout flips on this flag."""
        captured = {}
        def fake_run(args, input, **kw):
            captured["input"] = input
            return _fake_completed(json.dumps({"ok": True, "data": {}}))
        with patch("subprocess.run", side_effect=fake_run):
            runner.build_sell_tx(
                42, self.MINT, self.PAYER, self.CREATOR,
                17_000_000_000_000, 450_000_000, self.BLOCKHASH,
            )
        sent = json.loads(captured["input"].strip())
        self.assertEqual(sent["is_cashback_coin"], False)

    def test_optional_fields_omitted_when_unset(self):
        captured = {}
        def fake_run(args, input, **kw):
            captured["input"] = input
            return _fake_completed(json.dumps({"ok": True, "data": {}}))
        with patch("subprocess.run", side_effect=fake_run):
            runner.build_sell_tx(
                42, self.MINT, self.PAYER, self.CREATOR,
                17_000_000_000_000, 450_000_000, self.BLOCKHASH,
            )
        sent = json.loads(captured["input"].strip())
        self.assertNotIn("priority_fee_microlamports", sent)
        self.assertNotIn("compute_units", sent)

    def test_accepts_zero_min_sol_output(self):
        """0 = forced exit (any non-zero output OK). Must not be transformed."""
        captured = {}
        def fake_run(args, input, **kw):
            captured["input"] = input
            return _fake_completed(json.dumps({"ok": True, "data": {}}))
        with patch("subprocess.run", side_effect=fake_run):
            runner.build_sell_tx(
                42, self.MINT, self.PAYER, self.CREATOR,
                17_000_000_000_000, 0, self.BLOCKHASH,
            )
        sent = json.loads(captured["input"].strip())
        self.assertEqual(sent["min_sol_output_lamports"], 0)

    def test_returns_data_payload(self):
        envelope = {
            "route": "pumpfun-pregrad-sell",
            "tx_b64": "BASE64-PLACEHOLDER",
            "token_amount": 17_000_000_000_000,
            "min_sol_output_lamports": 450_000_000,
            "is_cashback_coin": False,
        }
        with patch("subprocess.run",
                   return_value=_fake_completed(json.dumps({"ok": True, "data": envelope}))):
            result = runner.build_sell_tx(
                42, self.MINT, self.PAYER, self.CREATOR,
                17_000_000_000_000, 450_000_000, self.BLOCKHASH,
            )
            self.assertEqual(result, envelope)

    def test_propagates_rust_validation_errors(self):
        bad = json.dumps({"ok": False, "error": "token_amount must be > 0"})
        with patch("subprocess.run", return_value=_fake_completed(bad)):
            with self.assertRaises(runner.TgTraderError) as ctx:
                runner.build_sell_tx(
                    42, self.MINT, self.PAYER, self.CREATOR,
                    0, 450_000_000, self.BLOCKHASH,
                )
            self.assertIn("token_amount", str(ctx.exception))

    def test_coerces_token_amount_to_int(self):
        """If the caller passes a float by mistake, we coerce to int rather
        than silently send a float and confuse Rust's u64 deserializer."""
        captured = {}
        def fake_run(args, input, **kw):
            captured["input"] = input
            return _fake_completed(json.dumps({"ok": True, "data": {}}))
        with patch("subprocess.run", side_effect=fake_run):
            runner.build_sell_tx(
                42, self.MINT, self.PAYER, self.CREATOR,
                17_000_000_000_000.0, 450_000_000, self.BLOCKHASH,
            )
        sent = json.loads(captured["input"].strip())
        self.assertEqual(sent["token_amount"], 17_000_000_000_000)
        self.assertIsInstance(sent["token_amount"], int)


class TestSubmitBundle(unittest.TestCase):
    """The Jito submit-bundle wrapper. Tests mock subprocess.run — we never
    actually call Jito here."""

    def setUp(self):
        self._patch = patch.object(runner, "_resolve_binary", return_value="/fake/tg-trader")
        self._patch.start()
        self.addCleanup(self._patch.stop)

    SIGNED_B64 = "AAAA" * 50  # 200 chars of fake base64 — never actually decoded by the wrapper

    def test_sends_correct_command_shape(self):
        captured = {}
        def fake_run(args, input, **kw):
            captured["input"] = input
            return _fake_completed(json.dumps({"ok": True, "data": {"phase": "dry-run"}}))
        with patch("subprocess.run", side_effect=fake_run):
            runner.submit_bundle(
                42, "  " + self.SIGNED_B64 + "\n",
                regions=["ny", "amsterdam"],
                live=False,
            )
        sent = json.loads(captured["input"].strip())
        self.assertEqual(sent["cmd"], "submit-bundle")
        self.assertEqual(sent["user_id"], "42")
        self.assertEqual(sent["signed_tx_b64"], self.SIGNED_B64)  # whitespace stripped
        self.assertEqual(sent["regions"], ["ny", "amsterdam"])
        self.assertEqual(sent["live"], False)

    def test_live_defaults_to_false(self):
        """SAFETY: caller MUST opt into live=True explicitly. Default false."""
        captured = {}
        def fake_run(args, input, **kw):
            captured["input"] = input
            return _fake_completed(json.dumps({"ok": True, "data": {}}))
        with patch("subprocess.run", side_effect=fake_run):
            runner.submit_bundle(42, self.SIGNED_B64)
        sent = json.loads(captured["input"].strip())
        self.assertEqual(sent["live"], False)

    def test_regions_omitted_when_unset(self):
        """When regions=None, key is absent so Rust uses its default 5-region list."""
        captured = {}
        def fake_run(args, input, **kw):
            captured["input"] = input
            return _fake_completed(json.dumps({"ok": True, "data": {}}))
        with patch("subprocess.run", side_effect=fake_run):
            runner.submit_bundle(42, self.SIGNED_B64)
        sent = json.loads(captured["input"].strip())
        self.assertNotIn("regions", sent)

    def test_live_true_passes_through(self):
        captured = {}
        def fake_run(args, input, **kw):
            captured["input"] = input
            return _fake_completed(json.dumps({"ok": True, "data": {"phase": "submitted"}}))
        with patch("subprocess.run", side_effect=fake_run):
            runner.submit_bundle(42, self.SIGNED_B64, live=True)
        sent = json.loads(captured["input"].strip())
        self.assertEqual(sent["live"], True)

    def test_returns_data_payload(self):
        envelope = {
            "phase":        "dry-run",
            "would_submit": False,
            "signature":    "fake-sig",
            "n_signatures": 1,
            "n_regions":    5,
        }
        with patch("subprocess.run",
                   return_value=_fake_completed(json.dumps({"ok": True, "data": envelope}))):
            result = runner.submit_bundle(42, self.SIGNED_B64, live=False)
            self.assertEqual(result, envelope)

    def test_propagates_safety_gate_error(self):
        """When the binary refuses (live=True + env unset), Rust returns
        ok=false with a TG_TRADER_LIVE message — surface it."""
        err_json = json.dumps({
            "ok":    False,
            "error": "submit-bundle called with live=true but TG_TRADER_LIVE env var != \"1\" — refusing to submit.",
        })
        with patch("subprocess.run", return_value=_fake_completed(err_json)):
            with self.assertRaises(runner.TgTraderError) as ctx:
                runner.submit_bundle(42, self.SIGNED_B64, live=True)
            msg = str(ctx.exception)
            self.assertIn("TG_TRADER_LIVE", msg)
            self.assertIn("refusing", msg)

    def test_uses_longer_default_timeout_than_build_commands(self):
        """Real submission can take seconds (5 regions × HTTP RTT). Default
        timeout should be ≥ 10s so legitimate live calls don't spuriously
        time out. Build commands default to 8s — submit-bundle is longer."""
        # We can't directly inspect the timeout passed without spying on
        # subprocess.run, so we set up a fake that records the timeout kwarg.
        captured = {}
        def fake_run(args, input, timeout, **kw):
            captured["timeout"] = timeout
            return _fake_completed(json.dumps({"ok": True, "data": {}}))
        with patch("subprocess.run", side_effect=fake_run):
            runner.submit_bundle(42, self.SIGNED_B64, live=False)
        self.assertGreaterEqual(captured["timeout"], 10.0,
            f"submit_bundle default timeout {captured['timeout']}s is too short")


class TestProtocolContract(unittest.TestCase):
    """The protocol must remain stable so the Rust side and Python side don't drift."""

    def setUp(self):
        self._patch = patch.object(runner, "_resolve_binary", return_value="/fake/tg-trader")
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_invoke_sends_one_line_with_trailing_newline(self):
        captured = {}
        def fake_run(args, input, **kw):
            captured["input"] = input
            return _fake_completed(json.dumps({"ok": True, "data": {}}))
        with patch("subprocess.run", side_effect=fake_run):
            runner.invoke({"cmd": "health"})
        # Protocol: one command per line, ending in \n
        self.assertTrue(captured["input"].endswith("\n"))
        self.assertEqual(captured["input"].count("\n"), 1)

    def test_invoke_uses_compact_json(self):
        """Compact JSON keeps the wire format predictable + small."""
        captured = {}
        def fake_run(args, input, **kw):
            captured["input"] = input
            return _fake_completed(json.dumps({"ok": True, "data": {}}))
        with patch("subprocess.run", side_effect=fake_run):
            runner.invoke({"cmd": "dry-run-buy", "user_id": "1", "mint": "x", "sol": 0.5})
        # No spaces after separators — confirms compact encoding
        self.assertNotIn(", ", captured["input"])
        self.assertNotIn(": ", captured["input"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
