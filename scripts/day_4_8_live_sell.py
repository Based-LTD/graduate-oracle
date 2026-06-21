#!/usr/bin/env python3
"""
Day 4.8c — first LIVE sell using the operator wallet.

Companion to scripts/day_4_7_live.py. Where day_4_7_live opens a
position, this one closes it. Mirrors the safety gates:
  • --confirm flag required for live submission
  • TG_TRADER_LIVE=1 env required
  • Defaults to dry-run preview (no submission, no DB mutation)

By default sells the most recently opened position (status='open',
ordered by buy_timestamp DESC). Pass --position-id to target a
specific row, or --mint to target the most recent open position
for that mint.

Usage:
    # Dry-run preview of selling the most recent open position
    python3 scripts/day_4_8_live_sell.py

    # Live sell (full close)
    TG_TRADER_LIVE=1 python3 scripts/day_4_8_live_sell.py --confirm

    # Live partial sell (50% of position)
    TG_TRADER_LIVE=1 python3 scripts/day_4_8_live_sell.py --pct 0.5 --confirm

    # Target a specific position by id
    TG_TRADER_LIVE=1 python3 scripts/day_4_8_live_sell.py --position-id 17 --confirm
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent.resolve()
REPO = HERE.parent
sys.path.insert(0, str(REPO / "web"))


def _store_dir() -> Path:
    return Path.home() / ".local" / "share" / "graduate-trader"


def _load_master_key(store: Path) -> str:
    p = store / "master.key"
    if not p.is_file():
        print(f"[fatal] no master.key at {p}")
        print(f"        run scripts/provision_operator_wallet.py first")
        sys.exit(2)
    return p.read_text().strip()


def main():
    parser = argparse.ArgumentParser(description="Day 4.8c live micro-sell")
    parser.add_argument("--position-id", type=int, default=None,
                        help="specific position row to sell (default: most-recent open)")
    parser.add_argument("--mint", default=None,
                        help="instead of --position-id, find most-recent open of this mint")
    parser.add_argument("--user-id", default="operator")
    parser.add_argument("--pct", type=float, default=1.0,
                        help="fraction to sell (default 1.0 = full close)")
    parser.add_argument("--slippage-bps", type=int, default=500)
    parser.add_argument("--tip-lamports", type=int, default=None,
                        help="override Jito tip (default: Jito p95+20pct live floor)")
    parser.add_argument("--confirm", action="store_true",
                        help="REQUIRED to actually submit")
    args = parser.parse_args()

    store = _store_dir()
    db_path = store / "trader.sqlite"
    if not db_path.is_file():
        print(f"[fatal] no operator wallet at {db_path}")
        sys.exit(2)
    os.environ["TRADER_DB_PATH"] = str(db_path)
    os.environ["TRADER_MASTER_KEY"] = _load_master_key(store)

    import trader_orchestrator as orch
    import trader_positions as tp
    import trader_wallets as tw

    # ── Pick the position ───────────────────────────────────────────────
    if args.position_id is not None:
        pos = tp.get_position(args.position_id)
        if pos is None:
            print(f"[fatal] no position with id={args.position_id}")
            sys.exit(2)
        if pos["status"] != "open":
            print(f"[fatal] position {args.position_id} is {pos['status']!r}, not open")
            sys.exit(2)
    elif args.mint is not None:
        rows = tp.list_open_positions_for_mint(args.mint)
        rows = [r for r in rows if str(r["user_id"]) == str(args.user_id)]
        if not rows:
            print(f"[fatal] no open positions for mint {args.mint[:12]}…")
            sys.exit(2)
        pos = rows[0]  # most recent (DESC ordering in trader_positions)
    else:
        rows = tp.list_open_positions(args.user_id)
        if not rows:
            print(f"[fatal] no open positions for user_id={args.user_id!r}")
            sys.exit(2)
        pos = rows[0]

    pubkey = tw.wallet_for(args.user_id)["public_key"]
    pre_balance = tw.get_balance_sol(pubkey)

    print("─" * 60)
    print(f"  DAY 4.8c LIVE SELL  ({'CONFIRM' if args.confirm else 'DRY-RUN PREVIEW'})")
    print("─" * 60)
    print(f"  pubkey         = {pubkey}")
    print(f"  balance        = {pre_balance:.6f} SOL")
    print(f"  position_id    = {pos['id']}")
    print(f"  mint           = {pos['mint']}")
    print(f"  bought         = {pos['buy_sol_lamports']/1e9:.6f} SOL")
    print(f"  tokens held    = {pos['token_amount']:,}")
    print(f"  sell pct       = {args.pct}")
    print(f"  slippage_bps   = {args.slippage_bps}")
    print(f"  TG_TRADER_LIVE = {os.environ.get('TG_TRADER_LIVE', '(unset)')}")
    print()

    if args.confirm and os.environ.get("TG_TRADER_LIVE") != "1":
        print("  ✗ --confirm passed but TG_TRADER_LIVE != '1'. Refusing.")
        sys.exit(3)

    print(f"[orchestrator] orch.sell(live={args.confirm}) …")
    started = time.time()
    try:
        result = orch.sell(
            args.user_id, pos["id"],
            sell_pct=args.pct,
            slippage_bps=args.slippage_bps,
            jito_tip_lamports=args.tip_lamports,
            live=args.confirm,
        )
    except orch.OrchestratorError as e:
        print(f"  ✗ FAILED at stage [{e.stage}]: {e}")
        sys.exit(5)
    elapsed = time.time() - started

    print(f"  ✓ phase                 = {result['phase']}")
    print(f"  ✓ tokens_sold           = {result['tokens_sold']:,}")
    print(f"  ✓ expected_sol_out      = "
          f"{result['expected_sol_out_lamports']/1e9:.6f} SOL")
    print(f"  ✓ min_sol_out           = "
          f"{result['min_sol_out_lamports']/1e9:.6f} SOL")
    print(f"  ✓ route                 = {result['route']}")
    print(f"  ✓ orchestrator elapsed  = {elapsed:.2f}s")

    if not args.confirm:
        print()
        print("─" * 60)
        print("  DRY-RUN PREVIEW COMPLETE — no submission, no DB write")
        print("─" * 60)
        return

    sig = result.get("sell_signature", "")
    print(f"  ✓ sell_signature        = {sig}")
    print(f"  solscan: https://solscan.io/tx/{sig}")
    print()

    # ── Confirm on-chain ─────────────────────────────────────────────
    print("[confirm] polling Solana (60s timeout)…")
    import jito_confirm
    res = jito_confirm.wait_for_confirmation(
        signature=sig, rpc_url=os.environ.get("RPC_HTTP", "https://api.mainnet-beta.solana.com"),
        bundle_ids=[], timeout_s=60.0, poll_interval_s=1.0,
    )
    if res.landed:
        print(f"  ✅ CONFIRMED via {res.source} in slot {res.slot} after {res.elapsed_s}s")
    elif res.failed:
        print(f"  ✗ Tx FAILED on-chain: {res.err}")
        sys.exit(6)
    else:
        print(f"  ⚠ Not confirmed within {res.elapsed_s}s — check Solscan")
        sys.exit(7)

    # ── Verify position state + balance ───────────────────────────────
    post_balance = tw.get_balance_sol(pubkey)
    delta = post_balance - pre_balance
    row = tp.get_position(pos["id"])
    print()
    print(f"  pre-sell balance    = {pre_balance:.6f} SOL")
    print(f"  post-sell balance   = {post_balance:.6f} SOL")
    print(f"  balance delta       = {delta:+.6f} SOL  (net of fees + slippage)")
    print(f"  position status     = {row['status']}")
    print(f"  realized PnL        = "
          f"{(row['realized_pnl_lamports'] or 0)/1e9:+.6f} SOL")
    print()
    print("─" * 60)
    print("  ✅ DAY 4.8c LIVE SELL SUCCEEDED")
    print("─" * 60)


if __name__ == "__main__":
    main()
