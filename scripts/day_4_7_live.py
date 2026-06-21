#!/usr/bin/env python3
"""
Day 4.7 — first LIVE micro-buy with the persistent operator wallet.

This is the first time real SOL leaves the operator wallet. Two safety
gates must BOTH be open:

    1. --confirm flag passed   (you, the human, must explicitly opt in)
    2. TG_TRADER_LIVE=1        (the Rust binary's hard gate)

Default behavior (no --confirm) is a dry-run preview that shows what
WOULD happen — useful for sanity-checking the chosen mint/sol/tip
before committing.

After a successful live submission, the script polls the Solana RPC for
on-chain confirmation of the signature and reports the result.

Usage:
    # Dry-run preview (default, safe)
    python3 scripts/day_4_7_live.py <mint>

    # Actually submit — REAL SOL LEAVES YOUR WALLET
    TG_TRADER_LIVE=1 python3 scripts/day_4_7_live.py <mint> --confirm

    # Custom buy size + tip
    TG_TRADER_LIVE=1 python3 scripts/day_4_7_live.py <mint> \\
        --sol 0.005 --tip-lamports 50000 --confirm

Default: --sol 0.001 (1/1000 SOL), --tip-lamports 10000 (0.00001 SOL),
total cost ≈ 0.00101 SOL + tx fee (~5_000 lamports). At ~0.1 SOL funded,
that's ~99 attempts before refunding.
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
    key_path = store / "master.key"
    if not key_path.is_file():
        print(f"[fatal] no master.key at {key_path}")
        print(f"        run scripts/provision_operator_wallet.py first")
        sys.exit(2)
    return key_path.read_text().strip()


def _extract_bundle_ids(submit_envelope: dict) -> list[str]:
    """Pull the bundle IDs out of the per-region submit responses.
    Multiple regions may return the same ID; dedup'd here."""
    import json as _j
    ids: list[str] = []
    for region in submit_envelope.get("regions") or []:
        if not isinstance(region, dict) or not region.get("ok"):
            continue
        body_str = region.get("body") or ""
        try:
            body = _j.loads(body_str)
        except Exception:
            continue
        bid = body.get("result")
        if isinstance(bid, str) and bid not in ids:
            ids.append(bid)
    return ids


def main():
    parser = argparse.ArgumentParser(description="Day 4.7 live micro-buy")
    parser.add_argument("mint", help="pump.fun pre-grad mint")
    parser.add_argument("--sol", type=float, default=0.001,
                        help="SOL to buy with (default 0.001 = 1/1000 SOL)")
    parser.add_argument("--slippage-bps", type=int, default=500,
                        help="Slippage in bps (default 500 = 5%%)")
    parser.add_argument("--tip-lamports", type=int, default=None,
                        help="Jito tip in lamports. Default: query Jito's live "
                             "tip floor at p95 + 20%% headroom (~lands 95%% of "
                             "competing bundles in current window).")
    parser.add_argument("--user-id", default="operator",
                        help="Operator user_id (default 'operator')")
    parser.add_argument("--confirm", action="store_true",
                        help="REQUIRED to actually submit. Otherwise dry-run only.")
    args = parser.parse_args()

    # ── Set up env for the persistent operator wallet ──────────────────
    store = _store_dir()
    db_path = store / "trader.sqlite"
    if not db_path.is_file():
        print(f"[fatal] no operator wallet at {db_path}")
        print(f"        run scripts/provision_operator_wallet.py first")
        sys.exit(2)
    os.environ["TRADER_DB_PATH"] = str(db_path)
    os.environ["TRADER_MASTER_KEY"] = _load_master_key(store)

    import trader_wallets as tw
    import trader_orchestrator as orch
    import jito_tip_floor
    import jito_confirm

    # Loud warning if running against public RPC — public mainnet times out
    # 10-30% of the time under load. Helius (or any paid endpoint) is the
    # only way to get sub-second reliable RPC.
    rpc_url_check = os.environ.get("RPC_HTTP", "")
    if "helius" not in rpc_url_check and "mainnet-beta.solana.com" in rpc_url_check or not rpc_url_check:
        print("[WARN] Using PUBLIC Solana RPC. Reliable submission needs Helius.")
        print(f"       Set RPC_HTTP env var, e.g.:")
        print(f"       export RPC_HTTP='https://mainnet.helius-rpc.com/?api-key=YOUR_KEY'")
        print()

    # ── Pre-flight balance + safety checks ──────────────────────────────
    wallet = tw.wallet_for(args.user_id)
    if not wallet:
        print(f"[fatal] no wallet for user_id={args.user_id!r}. "
              f"Run provision_operator_wallet.py first.")
        sys.exit(2)
    pubkey = wallet["public_key"]
    balance_sol = tw.get_balance_sol(pubkey)
    rpc_url = os.environ.get("RPC_HTTP", "https://api.mainnet-beta.solana.com")

    print("─" * 60)
    print(f"  DAY 4.7 LIVE MICRO-BUY  ({'CONFIRM' if args.confirm else 'DRY-RUN PREVIEW'})")
    print("─" * 60)
    # Resolve effective tip — either user-provided OR Jito's live p95+20%.
    if args.tip_lamports is not None:
        effective_tip = args.tip_lamports
        tip_source = "user-provided"
    else:
        try:
            effective_tip = jito_tip_floor.get_tip_lamports(percentile="p95")
            tip_source = "Jito p95+20% (live)"
        except jito_tip_floor.TipFloorError as e:
            print(f"[WARN] tip_floor lookup failed: {e}")
            effective_tip = 100_000
            tip_source = "fallback (tip_floor down)"

    print(f"  pubkey         = {pubkey}")
    print(f"  balance        = {balance_sol:.6f} SOL")
    print(f"  rpc            = {rpc_url}")
    print(f"  mint           = {args.mint}")
    print(f"  sol            = {args.sol}")
    print(f"  slippage_bps   = {args.slippage_bps}")
    print(f"  jito_tip       = {effective_tip:,} lamports "
          f"({effective_tip/1e9:.6f} SOL) — {tip_source}")
    print(f"  TG_TRADER_LIVE = {os.environ.get('TG_TRADER_LIVE', '(unset)')}")
    print()

    # Estimated max debit: buy + slippage + tip + fee headroom
    max_debit_lamports = int(args.sol * (1 + args.slippage_bps / 10_000) * 1e9) \
                         + effective_tip + 10_000
    max_debit_sol = max_debit_lamports / 1e9
    print(f"  max debit est. = {max_debit_sol:.6f} SOL "
          f"(buy + slippage + tip + ~10k fee headroom)")

    if balance_sol < max_debit_sol * 1.5:
        print(f"\n  ⚠ Balance {balance_sol:.4f} SOL is < 1.5× max debit "
              f"{max_debit_sol:.4f} SOL. Fund more before going live.")
        if args.confirm:
            sys.exit(3)

    if args.confirm and os.environ.get("TG_TRADER_LIVE") != "1":
        print(f"\n  ✗ --confirm passed but TG_TRADER_LIVE != '1'. "
              f"The Rust binary's safety gate is closed. Submission would be "
              f"refused. Set TG_TRADER_LIVE=1 to proceed.")
        sys.exit(4)

    # ── Run the orchestrator ─────────────────────────────────────────────
    live_flag = args.confirm
    print(f"\n[orchestrator] orch.buy(live={live_flag}) …")
    try:
        result = orch.buy(
            args.user_id, args.mint, args.sol,
            slippage_bps=args.slippage_bps,
            jito_tip_lamports=effective_tip,
            signal_source="day_4_7_operator_live",
            live=live_flag,
        )
    except orch.OrchestratorError as e:
        print(f"  ✗ FAILED at stage [{e.stage}]: {e}")
        sys.exit(5)

    print(f"  ✓ phase             = {result['phase']}")
    print(f"  ✓ position_id       = {result['position_id']}")
    print(f"  ✓ signature         = {result['buy_signature']}")
    print(f"  ✓ submit.phase      = {result['submit']['phase']}")
    sig = result["buy_signature"]
    print()

    if not live_flag:
        print("─" * 60)
        print("  DRY-RUN PREVIEW COMPLETE — no submission, no SOL spent")
        print("─" * 60)
        print()
        print("To submit for real:")
        print(f"  TG_TRADER_LIVE=1 python3 {Path(__file__).name} \\")
        print(f"    {args.mint} --sol {args.sol} --tip-lamports {args.tip_lamports} --confirm")
        return

    # ── Live: dual-source confirmation (Jito + Solana RPC) ─────────────
    bundle_ids = _extract_bundle_ids(result["submit"])
    n_accepted = result["submit"].get("n_accepted", 0)
    print(f"[submit] Jito: accepted in {n_accepted}/5 regions"
          + (f", bundle_id={bundle_ids[0][:24]}…" if bundle_ids else ""))
    rpc_path = result.get("submit_rpc")
    if rpc_path:
        if rpc_path.get("ok"):
            print(f"[submit] RPC:  submitted via sendTransaction")
        else:
            print(f"[submit] RPC:  FAILED — {rpc_path.get('error', '?')[:200]}")
    print(f"  signature      = {sig}")
    print(f"  solscan        = https://solscan.io/tx/{sig}")
    print()
    print("[confirm] dual-polling Jito + Solana (90s timeout)…")
    res = jito_confirm.wait_for_confirmation(
        signature=sig, rpc_url=rpc_url, bundle_ids=bundle_ids,
        timeout_s=90.0, poll_interval_s=1.0,
    )
    if res.landed:
        print(f"  ✅ CONFIRMED via {res.source} in slot {res.slot} after {res.elapsed_s}s")
    elif res.failed:
        print(f"  ✗ Bundle landed but tx FAILED on-chain ({res.source}): {res.err}")
        print(f"    Common causes: slippage exceeded, ATA mismatch, curve graduated mid-flight")
        sys.exit(6)
    else:  # timed out — bundle never landed
        print(f"  ⚠ Not confirmed within {res.elapsed_s}s. Bundle accepted by Jito ({n_accepted}/5 "
              f"regions) but never won an auction slot.")
        print(f"     Most likely cause: tip {effective_tip:,} lamports below current competition.")
        print(f"     Check live floor: python3 web/jito_tip_floor.py --snapshot")
        sys.exit(7)

    # ── Post-confirmation balance check ────────────────────────────────
    print()
    post_balance = tw.get_balance_sol(pubkey)
    debit = balance_sol - post_balance
    print(f"  pre-buy balance  = {balance_sol:.6f} SOL")
    print(f"  post-buy balance = {post_balance:.6f} SOL")
    print(f"  actual debit     = {debit:.6f} SOL")
    print()
    print("─" * 60)
    print("  ✅ DAY 4.7 LIVE MICRO-BUY SUCCEEDED")
    print("─" * 60)


if __name__ == "__main__":
    main()
