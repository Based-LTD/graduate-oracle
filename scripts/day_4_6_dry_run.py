#!/usr/bin/env python3
"""
Day 4.6 — real-mainnet dry-run validation for trader_orchestrator.buy().

Runs the FULL pipeline against real Helius + real bonding curve data, but
with `live=False` so the signed tx never reaches Jito. The point is to
catch integration issues the unit tests can't see:

  • Does bonding_curve.fetch decode a real curve account?
  • Does Helius's getLatestBlockhash succeed?
  • Does build_buy_tx accept the real BondingCurve dict shape?
  • Does sign_transaction handle the legacy Transaction the binary produced?
  • Does submit_bundle's dry-run path validate the signed tx end-to-end?
  • Does the position row land with every field populated?

NOTHING IS SUBMITTED. No SOL leaves the test wallet. The test wallet is
ephemeral — its key is generated fresh in a temp DB.

Usage:
    # From the repo root
    python3 scripts/day_4_6_dry_run.py <mint>

    # With a custom RPC (Helius)
    RPC_HTTP=https://mainnet.helius-rpc.com/?api-key=... \\
        python3 scripts/day_4_6_dry_run.py <mint>

    # With a custom buy size
    python3 scripts/day_4_6_dry_run.py <mint> --sol 0.05

The `tg-trader` binary must be on PATH or at target/release/tg-trader.
Build it with: cargo build --release --bin tg-trader
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

# Make web/ importable from anywhere
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "web"))


def main():
    parser = argparse.ArgumentParser(description="Day 4.6 mainnet dry-run validation")
    parser.add_argument("mint", help="pump.fun mint to test against (pre-grad)")
    parser.add_argument("--sol", type=float, default=0.01,
                        help="SOL amount to test with (default 0.01, NEVER submitted)")
    parser.add_argument("--user-id", default="day_4_6_test_user",
                        help="Test user id (creates an ephemeral wallet)")
    parser.add_argument("--slippage-bps", type=int, default=500,
                        help="Slippage in bps (default 500 = 5%%)")
    parser.add_argument("--keep-db", action="store_true",
                        help="Leave the temp DB in /tmp for inspection")
    args = parser.parse_args()

    # ── Isolate everything in temp paths so we never touch prod ─────────
    # 1. Temp position+wallets DB (both live in /data/trader.sqlite normally).
    tmp_db = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False).name
    os.environ["TRADER_DB_PATH"] = tmp_db
    os.environ.setdefault("TRADER_DATA_DIR", "/tmp")
    # 2. Fresh ephemeral Fernet master key — if the user has a prod
    #    TRADER_MASTER_KEY set, we OVERRIDE it for this run so the test
    #    wallet generated below CANNOT decrypt any prod-encrypted rows
    #    (and prod rows can't decrypt the test wallet). Total isolation.
    from cryptography.fernet import Fernet
    os.environ["TRADER_MASTER_KEY"] = Fernet.generate_key().decode()

    rpc = os.environ.get("RPC_HTTP", "https://api.mainnet-beta.solana.com")
    print(f"[setup] rpc          = {rpc}")
    print(f"[setup] mint         = {args.mint}")
    print(f"[setup] sol          = {args.sol} (NEVER submitted)")
    print(f"[setup] user_id      = {args.user_id}")
    print(f"[setup] temp DB      = {tmp_db}")
    print(f"[setup] master key   = (ephemeral — generated fresh, prod key shadowed)")
    print()

    # ── Import AFTER env is set so modules read the right paths ────────
    import trader_orchestrator as orch
    import trader_positions as tp
    import bonding_curve as bc

    # ── Stage-by-stage pre-flight (so failures are localized) ──────────
    print("[1/3] curve fetch — proves Helius RPC + decoder work")
    try:
        curve = bc.fetch(args.mint)
    except bc.BondingCurveError as e:
        print(f"      ✗ FAILED: {e}")
        sys.exit(1)
    print(f"      ✓ complete={curve['complete']}  "
          f"vSOL={curve['virtual_sol_reserves']/1e9:.2f}  "
          f"creator={curve['creator'][:8]}…  "
          f"cashback={curve['is_cashback_coin']}")
    if curve["complete"]:
        print(f"      ⚠ This curve has GRADUATED. Pick a pre-grad mint for dry-run.")
        sys.exit(2)
    print()

    # ── Stage 2: full orchestrator buy() with live=False ────────────────
    print("[2/3] orchestrator.buy(live=False) — full pipeline, no submission")
    try:
        result = orch.buy(
            args.user_id, args.mint, args.sol,
            slippage_bps=args.slippage_bps,
            signal_source="day_4_6_validation",
            live=False,
        )
    except orch.OrchestratorError as e:
        print(f"      ✗ FAILED at stage [{e.stage}]: {e}")
        sys.exit(3)

    # Pretty-print the envelope (truncated where helpful)
    print(f"      ✓ phase                = {result['phase']}")
    print(f"      ✓ position_id          = {result['position_id']}")
    print(f"      ✓ route                = {result['route']}")
    print(f"      ✓ buy_lamports         = {result['buy_lamports']:,}")
    print(f"      ✓ expected_tokens_out  = {result['expected_tokens_out']:,}")
    print(f"      ✓ max_sol_cost_lamports= {result['max_sol_cost_lamports']:,}")
    print(f"      ✓ entry_mcap_sol       = {result.get('entry_mcap_sol')}")
    print(f"      ✓ signature            = {result['buy_signature'][:16]}…")
    print(f"      ✓ submit.phase         = {result['submit']['phase']}")
    print(f"      ✓ submit.n_signatures  = {result['submit']['n_signatures']}")
    print(f"      ✓ submit.tx_bytes      = {result['submit']['tx_bytes']}")
    print()

    # ── Stage 3: position row verification ──────────────────────────────
    print("[3/3] position row — verify every field populated")
    row = tp.get_position(result["position_id"])
    if row is None:
        print("      ✗ FAILED: position row not found")
        sys.exit(4)

    required_fields = (
        "user_id", "mint", "payer_pubkey", "creator", "token_program",
        "buy_sol_lamports", "token_amount", "entry_price_lamports_per_token",
        "slippage_bps", "max_sol_cost_lamports",
        "buy_signature", "buy_phase", "buy_route", "buy_signal_source",
        "buy_timestamp", "status",
    )
    missing = [f for f in required_fields if row.get(f) in (None, "")]
    if missing:
        print(f"      ✗ FAILED: missing fields {missing}")
        sys.exit(5)
    print(f"      ✓ status               = {row['status']}")
    print(f"      ✓ buy_phase            = {row['buy_phase']}")
    print(f"      ✓ buy_signal_source    = {row['buy_signal_source']}")
    print(f"      ✓ payer_pubkey         = {row['payer_pubkey'][:12]}…")
    print(f"      ✓ creator              = {row['creator'][:12]}…")
    print(f"      ✓ is_cashback_coin     = {bool(row['is_cashback_coin'])}")
    print(f"      ✓ all required fields populated")
    print()

    # ── Summary ─────────────────────────────────────────────────────────
    print("─" * 60)
    print("✅  DRY-RUN VALIDATION PASSED")
    print("─" * 60)
    print()
    print("What this proved:")
    print("  • Helius RPC reachable + curve decoder accurate against live data")
    print("  • getLatestBlockhash succeeded")
    print("  • tg-trader binary built the buy tx + position envelope")
    print("  • trader_wallets signed the tx (custody key never left Python)")
    print("  • submit-bundle validated the signed tx end-to-end (dry-run)")
    print("  • position row written with every field populated")
    print()
    print("Next: Day 4.7 — operator micro-live (0.001 SOL real submission).")

    if not args.keep_db:
        os.unlink(tmp_db)
    else:
        print(f"\n  Temp DB kept at: {tmp_db}")
        print(f"  Inspect: sqlite3 {tmp_db} 'SELECT * FROM trader_positions;'")


if __name__ == "__main__":
    main()
