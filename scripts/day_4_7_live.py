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


def _poll_signature(rpc_url: str, signature: str, *,
                    timeout_s: float = 30.0, poll_s: float = 1.5) -> dict:
    """Poll getSignatureStatuses until confirmed or timeout. Returns
    {confirmed, slot, err, elapsed_s}."""
    import json
    import urllib.request
    deadline = time.time() + timeout_s
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method":  "getSignatureStatuses",
        "params":  [[signature], {"searchTransactionHistory": True}],
    }
    body = json.dumps(payload).encode()
    started = time.time()
    while time.time() < deadline:
        try:
            req = urllib.request.Request(
                rpc_url, data=body, headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                resp = json.loads(r.read())
            value = (resp.get("result") or {}).get("value") or []
            status = value[0] if value and value[0] else None
            if status and status.get("confirmationStatus") in ("confirmed", "finalized"):
                return {
                    "confirmed": True,
                    "slot":      status.get("slot"),
                    "err":       status.get("err"),
                    "elapsed_s": round(time.time() - started, 2),
                }
        except Exception:
            pass
        time.sleep(poll_s)
    return {"confirmed": False, "elapsed_s": round(time.time() - started, 2)}


def main():
    parser = argparse.ArgumentParser(description="Day 4.7 live micro-buy")
    parser.add_argument("mint", help="pump.fun pre-grad mint")
    parser.add_argument("--sol", type=float, default=0.001,
                        help="SOL to buy with (default 0.001 = 1/1000 SOL)")
    parser.add_argument("--slippage-bps", type=int, default=500,
                        help="Slippage in bps (default 500 = 5%%)")
    parser.add_argument("--tip-lamports", type=int, default=10_000,
                        help="Jito tip in lamports (default 10000 = 0.00001 SOL)")
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
    print(f"  pubkey         = {pubkey}")
    print(f"  balance        = {balance_sol:.6f} SOL")
    print(f"  rpc            = {rpc_url}")
    print(f"  mint           = {args.mint}")
    print(f"  sol            = {args.sol}")
    print(f"  slippage_bps   = {args.slippage_bps}")
    print(f"  jito_tip       = {args.tip_lamports} lamports ({args.tip_lamports/1e9:.6f} SOL)")
    print(f"  TG_TRADER_LIVE = {os.environ.get('TG_TRADER_LIVE', '(unset)')}")
    print()

    # Estimated max debit: buy + slippage + tip + fee headroom
    max_debit_lamports = int(args.sol * (1 + args.slippage_bps / 10_000) * 1e9) \
                         + args.tip_lamports + 10_000
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
            jito_tip_lamports=args.tip_lamports,
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

    # ── Live: poll for on-chain confirmation ───────────────────────────
    print("[on-chain] polling getSignatureStatuses for confirmation …")
    print(f"  https://solscan.io/tx/{sig}")
    print()
    status = _poll_signature(rpc_url, sig)
    if status.get("confirmed"):
        if status.get("err"):
            print(f"  ✗ Tx confirmed but FAILED on-chain: {status['err']}")
            print(f"    slot={status.get('slot')}  elapsed={status['elapsed_s']}s")
            sys.exit(6)
        print(f"  ✅ CONFIRMED in slot {status.get('slot')} after {status['elapsed_s']}s")
    else:
        print(f"  ⚠ Not confirmed within {status['elapsed_s']}s — could mean:")
        print(f"     • Jito dropped the bundle (tip too low for competition)")
        print(f"     • RPC propagation lag (check Solscan link above)")
        print(f"     • Real failure (check signature manually)")
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
