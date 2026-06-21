"""
trader_positions — the per-user position store for the TG bot's trader.

Each row records one buy → (eventually) one sell. The fields capture
*everything the sell-side needs* (creator, is_cashback, token_program)
plus *everything the receipt needs* (signatures, lamports in/out, ts).

We deliberately do NOT enforce uniqueness on (user_id, mint). A user can
scale into the same mint multiple times — each buy is its own row, each
gets its own sell. The orchestrator can aggregate by mint when displaying
"your open position in $X" but the store is per-buy.

Lives in the same /data/trader.sqlite that trader_wallets uses, separate
from the observer database. WAL mode is enabled by trader_wallets.

Public API:
    init_schema()
    create_position(user_id, mint, ...)  -> position_id (int)
    get_position(position_id)            -> dict | None
    list_open_positions(user_id)         -> list[dict]
    list_open_positions_for_mint(mint)   -> list[dict]
    mark_buy_failed(position_id, reason)
    mark_sold(position_id, *, sell_signature, sell_sol_lamports)
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional


# Use the same DB that trader_wallets uses. The path is resolved lazily
# so tests can monkeypatch TRADER_DB_PATH before init_schema runs.
def _db_path() -> Path:
    override = os.environ.get("TRADER_DB_PATH")
    if override:
        return Path(override)
    return Path(os.environ.get("TRADER_DATA_DIR", "/data")) / "trader.sqlite"


def _conn() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(path), timeout=10)
    c.row_factory = sqlite3.Row
    return c


# ── Schema ──────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trader_positions (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                         TEXT NOT NULL,
    mint                            TEXT NOT NULL,
    payer_pubkey                    TEXT NOT NULL,
    creator                         TEXT NOT NULL,
    is_cashback_coin                INTEGER NOT NULL DEFAULT 0,
    token_program                   TEXT NOT NULL,

    -- Buy economics (lamports / raw token units; same units the Rust binary uses)
    buy_sol_lamports                INTEGER NOT NULL,
    token_amount                    INTEGER NOT NULL,
    entry_price_lamports_per_token  REAL NOT NULL,
    entry_mcap_sol                  REAL,
    slippage_bps                    INTEGER,
    max_sol_cost_lamports           INTEGER,

    -- Buy submission audit trail
    buy_signature                   TEXT NOT NULL,   -- empty string for dry-run rows
    buy_phase                       TEXT NOT NULL,   -- 'submitted' / 'dry-run' / 'failed'
    buy_route                       TEXT NOT NULL,   -- 'pumpfun-pregrad' / 'jupiter'
    buy_tier                        TEXT,            -- ACT / WATCH / SCOUT, or NULL for manual buys
    buy_signal_source               TEXT,            -- e.g. 'composite_score', 'manual'
    buy_timestamp                   INTEGER NOT NULL,

    -- Status lifecycle
    status                          TEXT NOT NULL DEFAULT 'open',  -- open / sold / failed
    fail_reason                     TEXT,

    -- Sell side (populated when the position closes)
    sell_signature                  TEXT,
    sell_sol_lamports               INTEGER,
    sell_timestamp                  INTEGER,
    realized_pnl_lamports           INTEGER          -- sell - buy_sol_lamports
);

CREATE INDEX IF NOT EXISTS idx_trader_positions_user
    ON trader_positions(user_id, status);
CREATE INDEX IF NOT EXISTS idx_trader_positions_mint
    ON trader_positions(mint, status);
CREATE INDEX IF NOT EXISTS idx_trader_positions_buy_sig
    ON trader_positions(buy_signature);
"""


def init_schema():
    with contextlib.closing(_conn()) as c, c:
        c.executescript(_SCHEMA)


# ── Writers ─────────────────────────────────────────────────────────────

def create_position(
    *,
    user_id: str | int,
    mint: str,
    payer_pubkey: str,
    creator: str,
    is_cashback_coin: bool,
    token_program: str,
    buy_sol_lamports: int,
    token_amount: int,
    entry_price_lamports_per_token: float,
    entry_mcap_sol: Optional[float],
    slippage_bps: Optional[int],
    max_sol_cost_lamports: Optional[int],
    buy_signature: str,
    buy_phase: str,                      # 'submitted' / 'dry-run' / 'failed'
    buy_route: str,                      # 'pumpfun-pregrad' / 'jupiter'
    buy_tier: Optional[str],
    buy_signal_source: Optional[str],
    buy_timestamp: Optional[int] = None,
) -> int:
    """Insert one row, return its rowid. Idempotency is the caller's job —
    repeated buys of the same mint are intentional (scale-in), each gets
    its own row."""
    if buy_phase not in ("submitted", "dry-run", "failed"):
        raise ValueError(f"buy_phase must be submitted/dry-run/failed, got {buy_phase!r}")
    init_schema()
    ts = buy_timestamp if buy_timestamp is not None else int(time.time())
    status = "failed" if buy_phase == "failed" else "open"
    with contextlib.closing(_conn()) as c, c:
        cur = c.execute("""
            INSERT INTO trader_positions (
                user_id, mint, payer_pubkey, creator, is_cashback_coin, token_program,
                buy_sol_lamports, token_amount,
                entry_price_lamports_per_token, entry_mcap_sol,
                slippage_bps, max_sol_cost_lamports,
                buy_signature, buy_phase, buy_route, buy_tier, buy_signal_source,
                buy_timestamp, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(user_id), mint, payer_pubkey, creator,
            1 if is_cashback_coin else 0, token_program,
            int(buy_sol_lamports), int(token_amount),
            float(entry_price_lamports_per_token),
            entry_mcap_sol if entry_mcap_sol is None else float(entry_mcap_sol),
            slippage_bps if slippage_bps is None else int(slippage_bps),
            max_sol_cost_lamports if max_sol_cost_lamports is None else int(max_sol_cost_lamports),
            buy_signature, buy_phase, buy_route, buy_tier, buy_signal_source,
            ts, status,
        ))
        return int(cur.lastrowid)


def mark_buy_failed(position_id: int, reason: str):
    """Move an 'open' row to 'failed' status with a reason. Used when the
    buy was inserted optimistically but submission later failed."""
    with contextlib.closing(_conn()) as c, c:
        c.execute("""
            UPDATE trader_positions
               SET status = 'failed', fail_reason = ?
             WHERE id = ?
        """, (reason, int(position_id)))


def mark_sold(
    position_id: int,
    *,
    sell_signature: str,
    sell_sol_lamports: int,
    sell_timestamp: Optional[int] = None,
):
    """Close out a position with the sell outcome. Computes realized PnL
    as (sell_sol_lamports - buy_sol_lamports). Idempotent for the same
    sell_signature (caller's job to not double-sell)."""
    ts = sell_timestamp if sell_timestamp is not None else int(time.time())
    with contextlib.closing(_conn()) as c, c:
        row = c.execute(
            "SELECT buy_sol_lamports FROM trader_positions WHERE id = ?",
            (int(position_id),),
        ).fetchone()
        if row is None:
            raise KeyError(f"no position with id={position_id}")
        pnl = int(sell_sol_lamports) - int(row["buy_sol_lamports"])
        c.execute("""
            UPDATE trader_positions
               SET status = 'sold',
                   sell_signature = ?,
                   sell_sol_lamports = ?,
                   sell_timestamp = ?,
                   realized_pnl_lamports = ?
             WHERE id = ?
        """, (sell_signature, int(sell_sol_lamports), ts, pnl, int(position_id)))


# ── Readers ─────────────────────────────────────────────────────────────

def get_position(position_id: int) -> Optional[dict]:
    with contextlib.closing(_conn()) as c:
        row = c.execute(
            "SELECT * FROM trader_positions WHERE id = ?",
            (int(position_id),),
        ).fetchone()
        return dict(row) if row else None


def list_open_positions(user_id: str | int) -> list[dict]:
    with contextlib.closing(_conn()) as c:
        rows = c.execute("""
            SELECT * FROM trader_positions
             WHERE user_id = ? AND status = 'open'
             ORDER BY buy_timestamp DESC
        """, (str(user_id),)).fetchall()
        return [dict(r) for r in rows]


def list_open_positions_for_mint(mint: str) -> list[dict]:
    """Cross-user lookup — used by the monitor when a mint's price moves
    so we can find every wallet that needs to sell."""
    with contextlib.closing(_conn()) as c:
        rows = c.execute("""
            SELECT * FROM trader_positions
             WHERE mint = ? AND status = 'open'
             ORDER BY buy_timestamp ASC
        """, (mint,)).fetchall()
        return [dict(r) for r in rows]
