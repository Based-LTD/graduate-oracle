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
    realized_pnl_lamports           INTEGER,          -- sell - buy_sol_lamports

    -- Auto-exit config (Day 4.11 — feature set "B"):
    --   tp_ladder_json: JSON array of {pct, sell_pct} dicts. Triggered in
    --     order; partial sells reduce token_amount but leave position open
    --     until the last rung. Example:
    --       [{"pct": 50, "sell_pct": 30},
    --        {"pct": 200, "sell_pct": 50},
    --        {"pct": 500, "sell_pct": 100}]
    --     means: +50% sell 30%, +200% sell another 50% of remaining,
    --     +500% sell what's left.
    --   sl_pct: percentage drawdown from entry that triggers full exit.
    --     Negative number (e.g. -50 = exit at 50% loss).
    --   tsl_pct: trailing stop, in percent below the high-water mark.
    --     Example: 30 = exit if price drops 30% from peak since buy.
    --   breakeven_pct: once price reaches this gain, move sl_pct to 0
    --     (= entry). One-shot — flips position.sl_pct_armed_at_breakeven=1.
    --   next_tp_index: which rung of the ladder fires next (0 = TP1).
    --   high_water_mark_lamports: max expected_sol_out (Jupiter quote) we
    --     have observed for the CURRENT remaining token_amount. Reset on
    --     partial sell.
    --   last_monitor_check_at: unix ts of last monitor poll for this row.
    --   exit_reason: when status='sold', why? 'manual' / 'tp1' / 'tp2' /
    --     'tp3' / 'sl' / 'tsl' / 'breakeven' / null.
    --   sl_armed_at_breakeven: 1 if breakeven flip already happened.
    tp_ladder_json                  TEXT,
    sl_pct                          REAL,
    tsl_pct                         REAL,
    breakeven_pct                   REAL,
    next_tp_index                   INTEGER NOT NULL DEFAULT 0,
    high_water_mark_lamports        INTEGER,
    last_monitor_check_at           INTEGER,
    exit_reason                     TEXT,
    sl_armed_at_breakeven           INTEGER NOT NULL DEFAULT 0
);

-- ── Per-user auto-exit defaults ──────────────────────────────────────
-- Applied to new positions when the buy() caller doesn't override.
-- Lets a user say "all my buys should TP at 2x then trail at 30%" once.
--   buy_presets_sol_json: 3-element JSON list of SOL amounts (e.g.
--     [0.01, 0.05, 0.25]). Drives the [Buy X SOL] inline button row
--     under composite alerts.
CREATE TABLE IF NOT EXISTS trader_user_settings (
    user_id              TEXT PRIMARY KEY,
    tp_ladder_json       TEXT,
    sl_pct               REAL,
    tsl_pct              REAL,
    breakeven_pct        REAL,
    buy_presets_sol_json TEXT,
    -- Execution / safety knobs (Day 4.20):
    --   slippage_bps:   max accepted slippage on swap (100=1%, 500=5%)
    --   jito_tip_mode:  'auto' (Jito p95 floor) | 'fast' (50k) | 'turbo' (200k) | 'ultra' (500k)
    --   max_trade_sol:  hard cap per buy. Refused if any single /buy exceeds. NULL = no cap.
    slippage_bps         INTEGER,
    jito_tip_mode        TEXT,
    max_trade_sol        REAL,
    updated_at           INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trader_positions_user
    ON trader_positions(user_id, status);
CREATE INDEX IF NOT EXISTS idx_trader_positions_mint
    ON trader_positions(mint, status);
CREATE INDEX IF NOT EXISTS idx_trader_positions_buy_sig
    ON trader_positions(buy_signature);
"""


# Migrations — columns added to an existing trader_positions table.
# Each is a (column_name, ALTER TABLE statement) pair. Applied in order;
# already-present columns are skipped silently. Safe to re-run on every
# init_schema().
_MIGRATIONS = [
    ("trader_positions", "tp_ladder_json",
        "ALTER TABLE trader_positions ADD COLUMN tp_ladder_json TEXT"),
    ("trader_positions", "sl_pct",
        "ALTER TABLE trader_positions ADD COLUMN sl_pct REAL"),
    ("trader_positions", "tsl_pct",
        "ALTER TABLE trader_positions ADD COLUMN tsl_pct REAL"),
    ("trader_positions", "breakeven_pct",
        "ALTER TABLE trader_positions ADD COLUMN breakeven_pct REAL"),
    ("trader_positions", "next_tp_index",
        "ALTER TABLE trader_positions ADD COLUMN next_tp_index INTEGER NOT NULL DEFAULT 0"),
    ("trader_positions", "high_water_mark_lamports",
        "ALTER TABLE trader_positions ADD COLUMN high_water_mark_lamports INTEGER"),
    ("trader_positions", "last_monitor_check_at",
        "ALTER TABLE trader_positions ADD COLUMN last_monitor_check_at INTEGER"),
    ("trader_positions", "exit_reason",
        "ALTER TABLE trader_positions ADD COLUMN exit_reason TEXT"),
    ("trader_positions", "sl_armed_at_breakeven",
        "ALTER TABLE trader_positions ADD COLUMN sl_armed_at_breakeven INTEGER NOT NULL DEFAULT 0"),
    ("trader_user_settings", "buy_presets_sol_json",
        "ALTER TABLE trader_user_settings ADD COLUMN buy_presets_sol_json TEXT"),
    ("trader_user_settings", "slippage_bps",
        "ALTER TABLE trader_user_settings ADD COLUMN slippage_bps INTEGER"),
    ("trader_user_settings", "jito_tip_mode",
        "ALTER TABLE trader_user_settings ADD COLUMN jito_tip_mode TEXT"),
    ("trader_user_settings", "max_trade_sol",
        "ALTER TABLE trader_user_settings ADD COLUMN max_trade_sol REAL"),
    # Honest-accounting columns (Day 4.22). Receipt previously showed only
    # gross sell-out, hiding 1%+1% fee skim → users thought wins were
    # losses (or vice versa). Now we record fees both sides so the sell
    # receipt can render gross/fees/net cleanly.
    ("trader_positions", "buy_fee_lamports",
        "ALTER TABLE trader_positions ADD COLUMN buy_fee_lamports INTEGER NOT NULL DEFAULT 0"),
    ("trader_positions", "sell_fee_lamports",
        "ALTER TABLE trader_positions ADD COLUMN sell_fee_lamports INTEGER NOT NULL DEFAULT 0"),
    ("trader_positions", "net_pnl_lamports",
        "ALTER TABLE trader_positions ADD COLUMN net_pnl_lamports INTEGER"),
    # Day 4.23 — market cap snapshots for the trade receipt
    ("trader_positions", "entry_mcap_lamports",
        "ALTER TABLE trader_positions ADD COLUMN entry_mcap_lamports INTEGER"),
    ("trader_positions", "exit_mcap_lamports",
        "ALTER TABLE trader_positions ADD COLUMN exit_mcap_lamports INTEGER"),
    ("trader_positions", "token_total_supply",
        "ALTER TABLE trader_positions ADD COLUMN token_total_supply INTEGER"),
    # Day 4.39 — auto-trade settings per-user
    ("trader_user_settings", "auto_trade_enabled",
        "ALTER TABLE trader_user_settings ADD COLUMN auto_trade_enabled INTEGER DEFAULT 0"),
    ("trader_user_settings", "auto_trade_size_lamports",
        "ALTER TABLE trader_user_settings ADD COLUMN auto_trade_size_lamports INTEGER DEFAULT 5000000"),  # 0.005 SOL
    ("trader_user_settings", "auto_trade_min_tier",
        "ALTER TABLE trader_user_settings ADD COLUMN auto_trade_min_tier TEXT DEFAULT 'ACT'"),
    ("trader_user_settings", "auto_trade_max_concurrent",
        "ALTER TABLE trader_user_settings ADD COLUMN auto_trade_max_concurrent INTEGER DEFAULT 3"),
    # Day 4.42 — price-per-token HWM. Replaces value-based HWM for
    # trailing-stop math so partial TP fills don't shift thresholds.
    ("trader_positions", "hwm_price_per_token_lamports",
        "ALTER TABLE trader_positions ADD COLUMN hwm_price_per_token_lamports REAL"),
    # Day 4.49 — auto-trade inactivity pause. Hours since last /trader
    # interaction before auto-buys stop firing. 0 = disabled (always
    # fire). DEFAULT 0 — traders run 24/7 unless they opt into the gate.
    ("trader_user_settings", "auto_trade_max_inactive_hours",
        "ALTER TABLE trader_user_settings ADD COLUMN auto_trade_max_inactive_hours INTEGER DEFAULT 0"),
    # Day 4.62 — Moonshot Mode. When True AND a TP rung has fired,
    # the monitor stops evaluating BE and TSL on the remaining position.
    # Pre-TP everything works as before — full defense. Post-TP the
    # position rides on the assumption that pump.fun coins often
    # correct then recover, and tight stops eat the second leg.
    # SL remains active so a catastrophic rug still exits.
    ("trader_user_settings", "moonshot_mode_enabled",
        "ALTER TABLE trader_user_settings ADD COLUMN moonshot_mode_enabled INTEGER DEFAULT 0"),
    # Day 4.50 — position stagnation timeout. If a coin's price hasn't
    # moved by more than `stale_band_pct` in `stale_timeout_minutes`,
    # auto-close the position. Frees up the concurrent-cap slot for a
    # live signal. 0 = disabled. Defaults: 20 min, 3% band.
    ("trader_user_settings", "stale_timeout_minutes",
        "ALTER TABLE trader_user_settings ADD COLUMN stale_timeout_minutes INTEGER DEFAULT 20"),
    ("trader_user_settings", "stale_band_pct",
        "ALTER TABLE trader_user_settings ADD COLUMN stale_band_pct REAL DEFAULT 3.0"),
    # Per-position anchor — the price-per-token we last considered a
    # "movement," plus when that was. Updated each tick when price
    # moves outside the band. Used by stagnation check.
    ("trader_positions", "stale_anchor_pp",
        "ALTER TABLE trader_positions ADD COLUMN stale_anchor_pp REAL"),
    ("trader_positions", "stale_anchor_at",
        "ALTER TABLE trader_positions ADD COLUMN stale_anchor_at INTEGER"),
]


def init_schema():
    with contextlib.closing(_conn()) as c, c:
        c.executescript(_SCHEMA)
        # Apply migrations to pre-existing tables. Check column existence
        # per-table before each ALTER so we never raise duplicate-column.
        existing_cols: dict[str, set] = {}
        for tbl, col, stmt in _MIGRATIONS:
            if tbl not in existing_cols:
                existing_cols[tbl] = {row["name"] for row in
                    c.execute(f"PRAGMA table_info({tbl})").fetchall()}
            if col not in existing_cols[tbl]:
                try:
                    c.execute(stmt)
                    existing_cols[tbl].add(col)
                except sqlite3.OperationalError as e:
                    if "duplicate column" not in str(e).lower():
                        raise

        # Day 4.50 — roll back the auto_trade_max_inactive_hours default
        # from 6h to 0 (OFF). The Day 4.49 deploy set 6h as default which
        # could pause active 24/7 traders. Reset only rows still at the
        # old default; users who explicitly chose 6 are unfortunately
        # also reset, but the new picker preserves their intent on next
        # visit. Idempotent — runs harmlessly when no rows match.
        try:
            c.execute(
                "UPDATE trader_user_settings "
                "SET auto_trade_max_inactive_hours = 0 "
                "WHERE auto_trade_max_inactive_hours = 6"
            )
        except sqlite3.OperationalError:
            pass  # column might not exist on first init — fine


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
    sell_fee_lamports: int = 0,
    sell_timestamp: Optional[int] = None,
):
    """Close out a position with the sell outcome. ACCUMULATES across
    multiple sell legs (TP rungs + final sell) — older bug overwrote.

    For a multi-rung exit (e.g. TP1 partial → TP2 final), this is called
    on the FINAL leg only. Partial legs use record_partial_sell() to
    accumulate their proceeds onto the row first.

    Stores two PnL numbers:
      • realized_pnl_lamports (GROSS): TOTAL sell across all legs - buy
      • net_pnl_lamports (NET): GROSS - buy_fee - TOTAL sell_fees. What
        the wallet actually netted. Receipts should ALWAYS show this one.

    Idempotent for the same sell_signature (caller's job to not double-sell).
    """
    ts = sell_timestamp if sell_timestamp is not None else int(time.time())
    with contextlib.closing(_conn()) as c, c:
        row = c.execute(
            "SELECT buy_sol_lamports, buy_fee_lamports, sell_sol_lamports, "
            "sell_fee_lamports FROM trader_positions WHERE id = ?",
            (int(position_id),),
        ).fetchone()
        if row is None:
            raise KeyError(f"no position with id={position_id}")
        buy = int(row["buy_sol_lamports"])
        try:
            buy_fee = int(row["buy_fee_lamports"] or 0)
        except (KeyError, TypeError):
            buy_fee = 0
        # Accumulate: any partial-leg proceeds already recorded on the
        # row get added to this final leg's proceeds.
        prior_sell = int(row["sell_sol_lamports"] or 0)
        prior_fee  = int(row["sell_fee_lamports"] or 0)
        total_sell = prior_sell + int(sell_sol_lamports)
        total_fee  = prior_fee  + int(sell_fee_lamports)
        gross = total_sell - buy
        net = gross - buy_fee - total_fee
        c.execute("""
            UPDATE trader_positions
               SET status = 'sold',
                   sell_signature = ?,
                   sell_sol_lamports = ?,
                   sell_timestamp = ?,
                   realized_pnl_lamports = ?,
                   sell_fee_lamports = ?,
                   net_pnl_lamports = ?
             WHERE id = ?
        """, (sell_signature, total_sell, ts, gross, total_fee, net,
              int(position_id)))


def record_partial_sell(
    position_id: int,
    *,
    leg_sol_lamports: int,
    leg_fee_lamports: int = 0,
):
    """Accumulate proceeds from a partial sell (e.g. TP1) onto the
    position row. Does NOT change status — partial sells leave the
    position open. The remaining token_amount is updated separately
    by the caller (orchestrator).

    When the final leg fires, mark_sold() will ADD its proceeds to the
    accumulated total, so the receipt reflects ALL legs, not just last.
    """
    with contextlib.closing(_conn()) as c, c:
        c.execute(
            "UPDATE trader_positions "
            "   SET sell_sol_lamports = COALESCE(sell_sol_lamports, 0) + ?, "
            "       sell_fee_lamports = COALESCE(sell_fee_lamports, 0) + ? "
            " WHERE id = ?",
            (int(leg_sol_lamports), int(leg_fee_lamports), int(position_id)),
        )


def compute_mcap_lamports(sol_lamports: int, tokens_raw: int,
                          token_total_supply_raw: int) -> Optional[int]:
    """Market cap in lamports given a trade snapshot.

    MC = price_per_raw_token × total_supply
       = (sol_lamports / tokens_raw) × token_total_supply_raw

    Returns None on invalid inputs. Caller divides by 1e9 to display SOL,
    multiplies by SOL/USD for USD."""
    if sol_lamports <= 0 or tokens_raw <= 0 or token_total_supply_raw <= 0:
        return None
    # Use float intermediate to avoid integer-division precision loss
    return int(sol_lamports * token_total_supply_raw / tokens_raw)


def set_entry_mcap(position_id: int, *, entry_mcap_lamports: Optional[int],
                   token_total_supply_raw: Optional[int]):
    """Stamp the entry market-cap snapshot onto the position row."""
    with contextlib.closing(_conn()) as c, c:
        c.execute(
            "UPDATE trader_positions SET entry_mcap_lamports = ?, "
            "token_total_supply = ? WHERE id = ?",
            (entry_mcap_lamports, token_total_supply_raw, int(position_id)),
        )


def set_exit_mcap(position_id: int, exit_mcap_lamports: Optional[int]):
    """Stamp the exit MC snapshot. Called from orchestrator.sell() after
    we have the sell quote."""
    with contextlib.closing(_conn()) as c, c:
        c.execute(
            "UPDATE trader_positions SET exit_mcap_lamports = ? WHERE id = ?",
            (exit_mcap_lamports, int(position_id)),
        )


def set_auto_trade_config(
    user_id: str | int, *,
    enabled: Optional[bool] = None,
    size_lamports: Optional[int] = None,
    min_tier: Optional[str] = None,
    max_concurrent: Optional[int] = None,
    max_inactive_hours: Optional[int] = None,
    stale_timeout_minutes: Optional[int] = None,
    stale_band_pct: Optional[float] = None,
):
    """Update one or more auto-trade fields for a user. None = leave alone."""
    init_schema()
    fields, vals = [], []
    if enabled is not None:
        fields.append("auto_trade_enabled = ?")
        vals.append(1 if enabled else 0)
    if size_lamports is not None:
        fields.append("auto_trade_size_lamports = ?")
        vals.append(int(size_lamports))
    if min_tier is not None:
        if min_tier not in ("ACT", "WATCH", "SCOUT"):
            raise ValueError(f"min_tier {min_tier!r} must be ACT/WATCH/SCOUT")
        fields.append("auto_trade_min_tier = ?")
        vals.append(min_tier)
    if max_concurrent is not None:
        fields.append("auto_trade_max_concurrent = ?")
        vals.append(int(max_concurrent))
    if max_inactive_hours is not None:
        fields.append("auto_trade_max_inactive_hours = ?")
        vals.append(int(max_inactive_hours))
    if stale_timeout_minutes is not None:
        fields.append("stale_timeout_minutes = ?")
        vals.append(int(stale_timeout_minutes))
    if stale_band_pct is not None:
        fields.append("stale_band_pct = ?")
        vals.append(float(stale_band_pct))
    if not fields:
        return
    vals.append(str(user_id))
    # Make sure a settings row exists for this user before updating
    with contextlib.closing(_conn()) as c, c:
        c.execute(
            "INSERT OR IGNORE INTO trader_user_settings (user_id) VALUES (?)",
            (str(user_id),),
        )
        c.execute(
            f"UPDATE trader_user_settings SET {', '.join(fields)} WHERE user_id = ?",
            vals,
        )


def count_open_positions(user_id: str | int) -> int:
    """How many open positions does this user have? Used by the auto-
    trade evaluator to enforce max_concurrent."""
    with contextlib.closing(_conn()) as c:
        row = c.execute(
            "SELECT COUNT(*) AS n FROM trader_positions "
            "WHERE user_id = ? AND status = 'open'",
            (str(user_id),),
        ).fetchone()
    return int((row["n"] if row else 0) or 0)


def set_buy_fee(position_id: int, buy_fee_lamports: int):
    """Stamp the fee paid at buy time onto the position. Called by the
    orchestrator after the fee skim returns (best-effort — failures here
    don't break the trade, but they mean the sell receipt undercounts
    fees later)."""
    with contextlib.closing(_conn()) as c, c:
        c.execute(
            "UPDATE trader_positions SET buy_fee_lamports = ? WHERE id = ?",
            (int(buy_fee_lamports), int(position_id)),
        )


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


# ── Auto-exit config ────────────────────────────────────────────────────

# Default TP ladder for new positions when neither the per-position arg
# nor the user-default override is set. 50% at +50% gain, 50% at +200%.
# Sized for the typical pump.fun winner: take the first runner profit
# fast, hold the moonshot half on a trailing stop.
DEFAULT_TP_LADDER = [
    {"pct": 50,  "sell_pct": 50},
    {"pct": 200, "sell_pct": 100},  # 100% of REMAINING — closes the position
]
DEFAULT_SL_PCT        = -50.0   # exit if down 50%
DEFAULT_TSL_PCT       = 30.0    # trail 30% off the high-water mark
DEFAULT_BREAKEVEN_PCT = 20.0    # at +20%, flip SL to entry
DEFAULT_BUY_PRESETS_SOL = [0.01, 0.05, 0.25]  # 3 inline-button amounts
DEFAULT_SLIPPAGE_BPS    = 500   # 5% — fine for typical pump.fun trades
DEFAULT_JITO_TIP_MODE   = "auto"  # query Jito p95 floor each tick
DEFAULT_MAX_TRADE_SOL   = 1.0   # safety cap — refuses single buys > 1 SOL


# Jito tip mode → lamports. "auto" returns None which the orchestrator
# resolves at call time via jito_tip_floor.get_tip_lamports().
JITO_TIP_MODE_LAMPORTS = {
    "auto":  None,
    "fast":   50_000,    # 0.00005 SOL — ~p90 in quiet markets
    "turbo": 200_000,    # 0.0002 SOL  — p95+ in normal market
    "ultra": 500_000,    # 0.0005 SOL  — p99 territory, race-to-block
}


def get_user_settings(user_id: str | int) -> dict:
    """Return the user's auto-exit defaults, falling back to package
    defaults when no row exists or specific fields are NULL.

    Shape: {tp_ladder, sl_pct, tsl_pct, breakeven_pct}.
      tp_ladder is the parsed Python list, not the JSON string.
    """
    import json as _json
    with contextlib.closing(_conn()) as c:
        row = c.execute(
            "SELECT * FROM trader_user_settings WHERE user_id = ?",
            (str(user_id),),
        ).fetchone()

    ladder_json = (row and row["tp_ladder_json"]) or None
    try:
        ladder = _json.loads(ladder_json) if ladder_json else None
    except Exception:
        ladder = None

    # SL/TSL: if user has a settings row, preserve None (explicit OFF).
    # If no row at all (first-time user), fall back to defaults.
    if row:
        sl = row["sl_pct"]
        tsl = row["tsl_pct"]
    else:
        sl = DEFAULT_SL_PCT
        tsl = DEFAULT_TSL_PCT
    # Breakeven distinguishes 3 states: column NULL means user disabled it
    # explicitly; no row at all means first-time user → fall back to default.
    if row:
        be = row["breakeven_pct"]  # may be None → "OFF"
    else:
        be = DEFAULT_BREAKEVEN_PCT

    # buy_presets_sol_json wasn't part of the original schema; older rows
    # won't have the column. Guard the access so first read on legacy
    # rows doesn't crash.
    presets = None
    try:
        if row and "buy_presets_sol_json" in row.keys():
            raw = row["buy_presets_sol_json"]
            if raw:
                presets = _json.loads(raw)
    except Exception:
        presets = None

    # Execution / safety fields. Guard column access in case of legacy
    # rows without the new columns.
    def _safe_get(col, default):
        try:
            if row and col in row.keys() and row[col] is not None:
                return row[col]
        except Exception:
            pass
        return default
    slippage_bps  = int(_safe_get("slippage_bps",  DEFAULT_SLIPPAGE_BPS))
    jito_tip_mode = str(_safe_get("jito_tip_mode", DEFAULT_JITO_TIP_MODE))
    if jito_tip_mode not in JITO_TIP_MODE_LAMPORTS:
        jito_tip_mode = DEFAULT_JITO_TIP_MODE
    max_trade_sol = _safe_get("max_trade_sol", DEFAULT_MAX_TRADE_SOL)
    if max_trade_sol is not None:
        max_trade_sol = float(max_trade_sol)

    # Auto-trade settings — Day 4.39. Default OFF for safety; user must
    # explicitly enable in /trader → Settings → Auto-Trade.
    at_enabled = bool(_safe_get("auto_trade_enabled", 0))
    at_size_lamports = int(_safe_get("auto_trade_size_lamports", 5_000_000))
    at_min_tier = _safe_get("auto_trade_min_tier", "ACT") or "ACT"
    at_max_concurrent = int(_safe_get("auto_trade_max_concurrent", 3))
    # Default is now 0 (OFF). Traders should be able to run 24/7 unless
    # they explicitly opt into the inactivity gate.
    at_max_inactive_h = int(_safe_get("auto_trade_max_inactive_hours", 0))
    stale_timeout_min = int(_safe_get("stale_timeout_minutes", 20))
    stale_band_pct    = float(_safe_get("stale_band_pct", 3.0))
    moonshot_mode    = bool(_safe_get("moonshot_mode_enabled", 0))

    return {
        "tp_ladder":     ladder if ladder is not None else list(DEFAULT_TP_LADDER),
        "sl_pct":        float(sl) if sl is not None else None,
        "tsl_pct":       float(tsl) if tsl is not None else None,
        "breakeven_pct": float(be) if be is not None else None,
        "buy_presets_sol": presets if (isinstance(presets, list) and presets)
                           else list(DEFAULT_BUY_PRESETS_SOL),
        "slippage_bps":   slippage_bps,
        "jito_tip_mode":  jito_tip_mode,
        "max_trade_sol":  max_trade_sol,
        "auto_trade_enabled":             at_enabled,
        "auto_trade_size_lamports":       at_size_lamports,
        "auto_trade_min_tier":            at_min_tier,
        "auto_trade_max_concurrent":      at_max_concurrent,
        "auto_trade_max_inactive_hours":  at_max_inactive_h,
        "stale_timeout_minutes":          stale_timeout_min,
        "stale_band_pct":                 stale_band_pct,
        "moonshot_mode_enabled":          moonshot_mode,
    }


def set_user_settings(
    user_id: str | int, *,
    tp_ladder: Optional[list] = None,
    sl_pct: Optional[float] = None,
    tsl_pct: Optional[float] = None,
    breakeven_pct: Optional[float] = None,
    buy_presets_sol: Optional[list] = None,
    slippage_bps: Optional[int] = None,
    jito_tip_mode: Optional[str] = None,
    max_trade_sol: Optional[float] = None,
    moonshot_mode_enabled: Optional[bool] = None,
    clear_max_trade_sol: bool = False,
    clear_breakeven_pct: bool = False,
    clear_sl_pct: bool = False,
    clear_tsl_pct: bool = False,
):
    """Upsert per-user auto-exit defaults. None values leave the existing
    field untouched (set only what changed)."""
    import json as _json
    import time as _time
    init_schema()
    with contextlib.closing(_conn()) as c, c:
        existing = c.execute(
            "SELECT * FROM trader_user_settings WHERE user_id = ?",
            (str(user_id),),
        ).fetchone()
        # Helper to safely read a possibly-missing column from the existing row.
        def _e(col, fallback=None):
            if not existing:
                return fallback
            try:
                if col in existing.keys():
                    return existing[col]
            except Exception:
                pass
            return fallback

        merged = {
            "tp_ladder_json": _json.dumps(tp_ladder) if tp_ladder is not None
                              else _e("tp_ladder_json"),
            "sl_pct":        (None if clear_sl_pct
                              else sl_pct if sl_pct is not None
                              else _e("sl_pct")),
            "tsl_pct":       (None if clear_tsl_pct
                              else tsl_pct if tsl_pct is not None
                              else _e("tsl_pct")),
            "breakeven_pct": (None if clear_breakeven_pct
                             else breakeven_pct if breakeven_pct is not None
                             else _e("breakeven_pct")),
            "buy_presets_sol_json": _json.dumps(buy_presets_sol) if buy_presets_sol is not None
                                    else _e("buy_presets_sol_json"),
            "slippage_bps":  slippage_bps  if slippage_bps  is not None else _e("slippage_bps"),
            "jito_tip_mode": jito_tip_mode if jito_tip_mode is not None else _e("jito_tip_mode"),
            # max_trade_sol can be None to disable the cap. clear_max_trade_sol
            # is the explicit "remove the cap" signal — pass-through None means
            # "leave unchanged."
            "max_trade_sol": (None if clear_max_trade_sol
                              else (max_trade_sol if max_trade_sol is not None
                                    else _e("max_trade_sol"))),
            "moonshot_mode_enabled": (1 if moonshot_mode_enabled
                                      else 0 if moonshot_mode_enabled is False
                                      else _e("moonshot_mode_enabled", 0)),
        }
        c.execute("""
            INSERT INTO trader_user_settings
                (user_id, tp_ladder_json, sl_pct, tsl_pct, breakeven_pct,
                 buy_presets_sol_json, slippage_bps, jito_tip_mode,
                 max_trade_sol, moonshot_mode_enabled, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                tp_ladder_json        = excluded.tp_ladder_json,
                sl_pct                = excluded.sl_pct,
                tsl_pct               = excluded.tsl_pct,
                breakeven_pct         = excluded.breakeven_pct,
                buy_presets_sol_json  = excluded.buy_presets_sol_json,
                slippage_bps          = excluded.slippage_bps,
                jito_tip_mode         = excluded.jito_tip_mode,
                max_trade_sol         = excluded.max_trade_sol,
                moonshot_mode_enabled = excluded.moonshot_mode_enabled,
                updated_at            = excluded.updated_at
        """, (str(user_id), merged["tp_ladder_json"], merged["sl_pct"],
              merged["tsl_pct"], merged["breakeven_pct"],
              merged["buy_presets_sol_json"], merged["slippage_bps"],
              merged["jito_tip_mode"], merged["max_trade_sol"],
              merged["moonshot_mode_enabled"], int(_time.time())))


def set_position_auto_exit(
    position_id: int, *,
    tp_ladder: Optional[list] = None,
    sl_pct: Optional[float] = None,
    tsl_pct: Optional[float] = None,
    breakeven_pct: Optional[float] = None,
):
    """Override the auto-exit config for a single position. Use for
    'I want this one specific buy to TP at 5x' style overrides.

    None values leave the existing field unchanged. Pass an empty list
    `[]` for tp_ladder to explicitly disable laddered TP on this row."""
    import json as _json
    fields, args = [], []
    if tp_ladder is not None:
        fields.append("tp_ladder_json = ?")
        args.append(_json.dumps(tp_ladder))
    if sl_pct is not None:
        fields.append("sl_pct = ?")
        args.append(float(sl_pct))
    if tsl_pct is not None:
        fields.append("tsl_pct = ?")
        args.append(float(tsl_pct))
    if breakeven_pct is not None:
        fields.append("breakeven_pct = ?")
        args.append(float(breakeven_pct))
    if not fields:
        return
    args.append(int(position_id))
    with contextlib.closing(_conn()) as c, c:
        c.execute(
            f"UPDATE trader_positions SET {', '.join(fields)} WHERE id = ?",
            args,
        )


def get_position_auto_exit(position_id: int) -> Optional[dict]:
    """Return the parsed auto-exit config for one position, or None if
    the row doesn't exist. tp_ladder is parsed Python list."""
    import json as _json
    row = get_position(position_id)
    if row is None:
        return None
    ladder = None
    if row.get("tp_ladder_json"):
        try:
            ladder = _json.loads(row["tp_ladder_json"])
        except Exception:
            ladder = None
    return {
        "tp_ladder":              ladder,
        "sl_pct":                 row.get("sl_pct"),
        "tsl_pct":                row.get("tsl_pct"),
        "breakeven_pct":          row.get("breakeven_pct"),
        "next_tp_index":          row.get("next_tp_index") or 0,
        "high_water_mark_lamports": row.get("high_water_mark_lamports"),
        "sl_armed_at_breakeven":  bool(row.get("sl_armed_at_breakeven")),
    }


def update_position_monitor_state(
    position_id: int, *,
    high_water_mark_lamports: Optional[int] = None,
    hwm_price_per_token_lamports: Optional[float] = None,
    next_tp_index: Optional[int] = None,
    sl_armed_at_breakeven: Optional[bool] = None,
    last_monitor_check_at: Optional[int] = None,
    stale_anchor_pp: Optional[float] = None,
    stale_anchor_at: Optional[int] = None,
):
    """Monitor-loop state writes. Each kwarg is optional — only set what
    changed. Used by the monitor to advance position state without
    touching unrelated fields."""
    fields, args = [], []
    if high_water_mark_lamports is not None:
        fields.append("high_water_mark_lamports = ?")
        args.append(int(high_water_mark_lamports))
    if hwm_price_per_token_lamports is not None:
        fields.append("hwm_price_per_token_lamports = ?")
        args.append(float(hwm_price_per_token_lamports))
    if next_tp_index is not None:
        fields.append("next_tp_index = ?")
        args.append(int(next_tp_index))
    if sl_armed_at_breakeven is not None:
        fields.append("sl_armed_at_breakeven = ?")
        args.append(1 if sl_armed_at_breakeven else 0)
    if last_monitor_check_at is not None:
        fields.append("last_monitor_check_at = ?")
        args.append(int(last_monitor_check_at))
    if stale_anchor_pp is not None:
        fields.append("stale_anchor_pp = ?")
        args.append(float(stale_anchor_pp))
    if stale_anchor_at is not None:
        fields.append("stale_anchor_at = ?")
        args.append(int(stale_anchor_at))
    if not fields:
        return
    args.append(int(position_id))
    with contextlib.closing(_conn()) as c, c:
        c.execute(
            f"UPDATE trader_positions SET {', '.join(fields)} WHERE id = ?",
            args,
        )


# Pre-baked strategy bundles. Applied in one tap from the hub.
STRATEGY_PRESETS: dict[str, dict] = {
    "conservative": {
        "label":     "🎯 Conservative",
        "blurb":     "Lock the small win. Tight risk.",
        "tp_ladder": [{"pct": 50, "sell_pct": 100}],
        "sl_pct":        -25.0,
        "tsl_pct":        15.0,
        "breakeven_pct":  10.0,
        "slippage_bps":   300,
        "jito_tip_mode":  "auto",
    },
    "balanced": {
        "label":     "⚖️ Balanced",
        "blurb":     "Take half off at +50%, run the rest with a trail.",
        "tp_ladder": [
            {"pct": 50,  "sell_pct": 50},
            {"pct": 200, "sell_pct": 100},
        ],
        "sl_pct":        -50.0,
        "tsl_pct":        30.0,
        "breakeven_pct":  20.0,
        "slippage_bps":   500,
        "jito_tip_mode":  "auto",
    },
    "yolo": {
        "label":     "🚀 YOLO",
        "blurb":     "Aim for the moonshot. Wide stops, big tip.",
        "tp_ladder": [
            {"pct": 200,  "sell_pct": 33},
            {"pct": 1000, "sell_pct": 100},
        ],
        "sl_pct":        -75.0,
        "tsl_pct":        50.0,
        "breakeven_pct":  50.0,
        "slippage_bps":  1000,
        "jito_tip_mode":  "turbo",
    },
}


def apply_strategy_preset(user_id: str | int, name: str):
    """Overwrite every exit/execution field in one shot. Buy presets are
    left untouched — the user keeps the amounts they're used to."""
    if name not in STRATEGY_PRESETS:
        raise ValueError(f"unknown strategy {name!r}; pick from {sorted(STRATEGY_PRESETS)}")
    p = STRATEGY_PRESETS[name]
    set_user_settings(
        user_id,
        tp_ladder=list(p["tp_ladder"]),
        sl_pct=p["sl_pct"],
        tsl_pct=p["tsl_pct"],
        breakeven_pct=p["breakeven_pct"],
        slippage_bps=p["slippage_bps"],
        jito_tip_mode=p["jito_tip_mode"],
    )


def set_exit_reason(position_id: int, reason: str):
    """Tag a position with WHY it was closed (set during sell, in addition
    to status='sold'). Values: 'manual' / 'tp1' / 'tp2' / 'tp3' / 'sl' /
    'tsl' / 'breakeven'."""
    with contextlib.closing(_conn()) as c, c:
        c.execute(
            "UPDATE trader_positions SET exit_reason = ? WHERE id = ?",
            (reason, int(position_id)),
        )
