#!/usr/bin/env python3
"""
Day 4.7a — provision a persistent operator wallet for live micro-buys.

Creates a stable trader_wallets store on YOUR LAPTOP (not /data, not Fly).
The wallet survives across runs so you can fund it once and reuse it for
every live test. Master key + encrypted private key both live in:

    ~/.local/share/graduate-trader/

Re-runs are idempotent — if the wallet already exists, this prints the
existing pubkey and balance instead of creating a new one.

The encrypted private key never leaves disk. The master key is generated
on first run and saved alongside. To rotate, delete the directory.

Usage:
    python3 scripts/provision_operator_wallet.py
    python3 scripts/provision_operator_wallet.py --user-id alt-account
    python3 scripts/provision_operator_wallet.py --balance-only

Then fund the printed pubkey (0.05 SOL is plenty for several micro-buys
at 0.001 SOL each + Jito tips + fees).
"""

from __future__ import annotations

import argparse
import os
import secrets
import stat
import sys
from pathlib import Path

# Make web/ importable
HERE = Path(__file__).parent.resolve()
REPO = HERE.parent
sys.path.insert(0, str(REPO / "web"))


def _store_dir() -> Path:
    """Where the persistent operator wallet lives."""
    return Path.home() / ".local" / "share" / "graduate-trader"


def _load_or_create_master_key(store: Path) -> str:
    """Read the master key from disk, or generate + save on first run.
    The file is chmodded 600 so only the owner can read it."""
    key_path = store / "master.key"
    if key_path.is_file():
        return key_path.read_text().strip()
    # First run — generate.
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    store.mkdir(parents=True, exist_ok=True)
    key_path.write_text(key)
    key_path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 600
    return key


def main():
    parser = argparse.ArgumentParser(description="Day 4.7a operator wallet provisioning")
    parser.add_argument("--user-id", default="operator",
                        help="Stable user_id for the operator wallet (default 'operator')")
    parser.add_argument("--balance-only", action="store_true",
                        help="Don't create — just print the pubkey + on-chain balance")
    args = parser.parse_args()

    store = _store_dir()
    store.mkdir(parents=True, exist_ok=True)
    db_path = store / "trader.sqlite"

    # Set env BEFORE importing trader_wallets so it reads our paths.
    os.environ["TRADER_DB_PATH"] = str(db_path)
    os.environ["TRADER_MASTER_KEY"] = _load_or_create_master_key(store)

    import trader_wallets as tw

    if args.balance_only:
        existing = tw.wallet_for(args.user_id)
        if not existing:
            print(f"no wallet exists for user_id={args.user_id!r}")
            print(f"run without --balance-only to provision one")
            sys.exit(1)
        pk = existing["public_key"]
        bal = tw.get_balance_sol(pk)
        print(f"user_id   = {args.user_id}")
        print(f"pubkey    = {pk}")
        print(f"balance   = {bal:.6f} SOL")
        return

    # Idempotent: existing wallet → reuse; else generate.
    existing = tw.wallet_for(args.user_id)
    if existing:
        action = "reused existing"
        pk = existing["public_key"]
    else:
        new_w = tw.generate_wallet(args.user_id)
        action = "generated NEW"
        pk = new_w["public_key"]

    # On-chain balance (if any). New wallets read 0.
    try:
        bal = tw.get_balance_sol(pk)
    except Exception as e:
        bal = None
        print(f"[warn] balance check failed: {e}", file=sys.stderr)

    print("─" * 60)
    print(f"  OPERATOR WALLET — {action}")
    print("─" * 60)
    print(f"  user_id   = {args.user_id}")
    print(f"  pubkey    = {pk}")
    if bal is not None:
        print(f"  balance   = {bal:.6f} SOL")
    print(f"  storage   = {db_path}")
    print(f"  key file  = {store / 'master.key'} (chmod 600)")
    print()
    print("Next steps:")
    print(f"  1. Fund this pubkey with at least 0.05 SOL:")
    print(f"     {pk}")
    print()
    print(f"  2. Verify funding landed:")
    print(f"     python3 scripts/provision_operator_wallet.py --balance-only")
    print()
    print(f"  3. When balance ≥ 0.05 SOL, run the Day 4.7 live test (next step).")
    print()
    print("Security:")
    print("  • The master.key file is your wallet's encryption secret. Treat as")
    print("    seed-phrase-equivalent. Back up the entire ~/.local/share/graduate-trader/")
    print("    directory if you want to recover this wallet on another machine.")
    print("  • The encrypted private key is in trader.sqlite. Without master.key,")
    print("    it cannot be decrypted.")


if __name__ == "__main__":
    main()
