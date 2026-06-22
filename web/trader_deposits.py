"""
trader_deposits — background watcher for incoming SOL deposits + the
trade-state schema that the rest of the trading bot will build on.

This module owns three concerns:

  1. **Deposit detection.** A daemon polls each user wallet every
     POLL_INTERVAL_S, compares the on-chain balance to the last-seen
     balance, and logs any positive delta as a deposit event.

  2. **Position schema.** Defines the table the multi-tenant trader
     binary will write positions into, with a `phase` column from day
     one so we don't migrate when through-graduation routing ships
     on Day 5.5 (pre_grad → migrating → post_grad → closed).

  3. **Withdrawal audit table.** Every withdrawal request — granted,
     submitted, confirmed, failed — gets a durable row.

Live deposit / withdrawal *execution* lives in trader_wallets.py to keep
the custody key handling concentrated there. This module is purely the
"watch the chain + persist what happened" side.

CLI tests
─────────
  python -m trader_deposits init
  python -m trader_deposits scan <user_id>
  python -m trader_deposits poll-once
  python -m trader_deposits events <user_id>
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional


# Reuse trader_wallets' DB so the custody + state tables sit together
# in /data/trader.sqlite. One file = one backup unit = one trust boundary.
try:
    from trader_wallets import _db_path as _db_path_fn  # type: ignore
except ImportError:
    # Fallback when run as a script outside the package (e.g. `python -m`)
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from trader_wallets import _db_path as _db_path_fn  # type: ignore


def _db_path() -> Path:
    return _db_path_fn()


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_db_path(), timeout=10)
    c.row_factory = sqlite3.Row
    return c


# ── Configuration ────────────────────────────────────────────────────────

POLL_INTERVAL_S = 30
RPC_TIMEOUT_S = 8.0
_RPC = (os.environ.get("RPC_HTTP") or "https://api.mainnet-beta.solana.com").rstrip("/")


# ── Schema ───────────────────────────────────────────────────────────────

_SCHEMA = """
-- Last-seen balance per user wallet. Updated every poll so we can
-- compute deltas without hitting historical-tx APIs. Survives restart.
CREATE TABLE IF NOT EXISTS trader_wallet_balances (
    user_id            TEXT PRIMARY KEY,
    last_balance_lamports INTEGER NOT NULL DEFAULT 0,
    last_polled_at_unix INTEGER NOT NULL
);

-- Every positive balance delta. NOT the on-chain tx — just our
-- observation that "balance went up by X at time T". Reliable enough
-- for a credit ledger because the chain is the source of truth.
CREATE TABLE IF NOT EXISTS trader_deposit_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    lamports        INTEGER NOT NULL,
    detected_at_unix INTEGER NOT NULL,
    balance_after_lamports INTEGER NOT NULL,
    -- We don't try to pin to a specific signature here. The deposit
    -- watcher operates on getBalance deltas because that's cheap and
    -- the per-sig API costs us more RPC. If we ever need sig pinning
    -- for compliance, we can resolve it lazily via getSignaturesForAddress.
    signature       TEXT
);
CREATE INDEX IF NOT EXISTS idx_tde_user   ON trader_deposit_events(user_id, detected_at_unix DESC);

-- Every withdrawal request. Written BEFORE submission so we have an
-- intent record even if the process crashes mid-flight.
CREATE TABLE IF NOT EXISTS trader_withdrawals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    to_address      TEXT NOT NULL,
    lamports        INTEGER NOT NULL,
    -- Status lifecycle: 'requested' → 'submitted' → ('confirmed' | 'failed' | 'timed_out')
    status          TEXT NOT NULL DEFAULT 'requested',
    signature       TEXT,
    error_message   TEXT,
    requested_at_unix INTEGER NOT NULL,
    submitted_at_unix INTEGER,
    confirmed_at_unix INTEGER
);
CREATE INDEX IF NOT EXISTS idx_tw_user   ON trader_withdrawals(user_id, requested_at_unix DESC);
CREATE INDEX IF NOT EXISTS idx_tw_status ON trader_withdrawals(status);

-- trader_positions table is OWNED by web/trader_positions.py.
-- Earlier drafts of this file declared a competing schema (phase/closed_at_unix
-- columns) that collided with the new one (status/sell_timestamp). Removed
-- entirely 2026-06-22 — only trader_positions.init_schema() defines the table.
"""


def init_schema():
    """Idempotent. Safe to call on every process start. Adds tables to the
    same /data/trader.sqlite file managed by trader_wallets.init_schema()."""
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.closing(sqlite3.connect(path, timeout=10)) as c, c:
        c.executescript(_SCHEMA)


# ── RPC: getBalance ──────────────────────────────────────────────────────

def _get_balance_lamports(pubkey: str) -> int:
    """Returns current confirmed balance in lamports. Raises on RPC errors."""
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "getBalance",
        "params": [pubkey, {"commitment": "confirmed"}],
    }).encode()
    req = urllib.request.Request(
        _RPC, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=RPC_TIMEOUT_S) as r:
        resp = json.loads(r.read())
    val = resp.get("result", {}).get("value")
    if val is None:
        raise RuntimeError(f"RPC returned no balance: {resp}")
    return int(val)


# ── Core deposit scan ────────────────────────────────────────────────────

def scan_user(user_id: str | int) -> dict:
    """Scans a single user's wallet for new deposits. Compares current
    balance vs last-seen, writes a deposit event for any positive delta,
    updates the last-seen balance. Returns a dict summary of what
    happened.

    Idempotent semantics: a negative delta (the user spent SOL through
    our trader, or made an outbound transfer some other way) updates
    the last-seen without writing an event. Zero delta is a no-op.
    """
    user_id = str(user_id)
    # Resolve wallet
    with contextlib.closing(_conn()) as c:
        row = c.execute(
            "SELECT public_key FROM trader_wallets WHERE user_id = ?", (user_id,)
        ).fetchone()
    if not row:
        return {"user_id": user_id, "status": "no_wallet"}
    pubkey = row["public_key"]

    # Read on-chain balance + last-seen balance in DB
    try:
        current = _get_balance_lamports(pubkey)
    except Exception as e:
        return {"user_id": user_id, "status": "rpc_error", "error": str(e)}

    now = int(time.time())
    with contextlib.closing(_conn()) as c, c:
        last_row = c.execute(
            "SELECT last_balance_lamports FROM trader_wallet_balances WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        last = int(last_row["last_balance_lamports"]) if last_row else 0
        delta = current - last
        # Persist new last-seen regardless of direction.
        c.execute(
            "INSERT INTO trader_wallet_balances (user_id, last_balance_lamports, last_polled_at_unix) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET last_balance_lamports=excluded.last_balance_lamports, "
            "last_polled_at_unix=excluded.last_polled_at_unix",
            (user_id, current, now),
        )
        if delta > 0:
            c.execute(
                "INSERT INTO trader_deposit_events "
                "(user_id, lamports, detected_at_unix, balance_after_lamports) "
                "VALUES (?, ?, ?, ?)",
                (user_id, delta, now, current),
            )
    return {
        "user_id":               user_id,
        "status":                "deposit" if delta > 0 else ("withdraw_or_spend" if delta < 0 else "no_change"),
        "delta_lamports":        delta,
        "delta_sol":             delta / 1e9,
        "balance_lamports":      current,
        "balance_sol":           current / 1e9,
        "previous_balance":      last,
    }


def poll_once() -> dict:
    """Scans EVERY wallet in the system. Returns aggregate stats.

    This is the cheap-and-stupid version: one RPC call per user. At
    100 users it's 100 calls per cycle, which is fine on Helius. If
    we ever hit 10k+ users we'll batch via `getMultipleAccounts` (250
    pubkeys per call), but that optimization is deferred until volume
    actually exists.
    """
    init_schema()
    with contextlib.closing(_conn()) as c:
        rows = c.execute("SELECT user_id FROM trader_wallets").fetchall()
    n_users = len(rows)
    n_deposits = 0
    n_errors = 0
    total_deposit_lamports = 0
    for r in rows:
        result = scan_user(r["user_id"])
        if result["status"] == "deposit":
            n_deposits += 1
            total_deposit_lamports += result["delta_lamports"]
        elif result["status"] == "rpc_error":
            n_errors += 1
        # Be polite — small inter-call pause so we don't hammer Helius
        time.sleep(0.05)
    return {
        "users_scanned":          n_users,
        "deposits_detected":      n_deposits,
        "total_deposited_sol":    total_deposit_lamports / 1e9,
        "rpc_errors":             n_errors,
        "polled_at_unix":         int(time.time()),
    }


# ── Audit / reporting ────────────────────────────────────────────────────

def recent_events(user_id: str | int, limit: int = 20) -> list[dict]:
    user_id = str(user_id)
    with contextlib.closing(_conn()) as c:
        rows = c.execute(
            "SELECT id, lamports, detected_at_unix, balance_after_lamports, signature "
            "FROM trader_deposit_events WHERE user_id = ? "
            "ORDER BY detected_at_unix DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Daemon ───────────────────────────────────────────────────────────────

_STARTED = False
_START_LOCK = threading.Lock()


def _loop():
    print("[trader_deposits] daemon started · "
          f"{POLL_INTERVAL_S}s tick · rpc={_RPC}", flush=True)
    while True:
        try:
            stats = poll_once()
            if stats["deposits_detected"] > 0 or stats["rpc_errors"] > 0:
                print(
                    f"[trader_deposits] scanned={stats['users_scanned']} "
                    f"deposits={stats['deposits_detected']} "
                    f"total_sol={stats['total_deposited_sol']:.4f} "
                    f"errors={stats['rpc_errors']}",
                    flush=True,
                )
        except Exception as e:
            print(f"[trader_deposits] tick failed: {e}", flush=True)
        time.sleep(POLL_INTERVAL_S)


def start():
    """Idempotent — safe to call from a startup hook even if it's
    already running. Spins up a background daemon thread; the main
    web process keeps serving requests unaffected."""
    global _STARTED
    if _STARTED:
        return
    with _START_LOCK:
        if _STARTED:
            return
        if not os.environ.get("TRADER_MASTER_KEY", "").strip():
            print("[trader_deposits] DORMANT — TRADER_MASTER_KEY not set",
                  flush=True)
            return
        _STARTED = True
        init_schema()
        threading.Thread(target=_loop, daemon=True,
                         name="trader-deposits").start()


# ── CLI ──────────────────────────────────────────────────────────────────

def _cli():
    args = sys.argv[1:]
    if not args:
        print(__doc__); sys.exit(0)
    cmd = args[0]
    if cmd == "init":
        init_schema()
        print(f"OK · schema initialized at {_db_path()}")
    elif cmd == "scan":
        if len(args) < 2:
            print("usage: trader_deposits scan <user_id>"); sys.exit(2)
        print(json.dumps(scan_user(args[1]), indent=2))
    elif cmd == "poll-once":
        print(json.dumps(poll_once(), indent=2))
    elif cmd == "events":
        if len(args) < 2:
            print("usage: trader_deposits events <user_id>"); sys.exit(2)
        for ev in recent_events(args[1]):
            print(json.dumps(ev, indent=2))
    else:
        print(f"unknown command: {cmd}"); print(__doc__); sys.exit(2)


if __name__ == "__main__":
    _cli()
