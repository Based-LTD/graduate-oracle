"""
trader_portfolio — live PnL enrichment for open positions.

Reads the on-disk position rows and asks Jupiter what each one is worth
RIGHT NOW. Returns enriched rows the TG bot can render directly:

    {
      ...all trader_positions row fields...
      "current_sol_value_lamports": int,    # Jupiter outAmount for selling now
      "unrealized_pnl_lamports":    int,    # current - buy
      "unrealized_pnl_pct":         float,  # pnl / buy
      "current_route":              str,    # "Pump.fun" / "Raydium" / etc.
      "valuation_error":            str|None, # set if Jupiter quote failed
    }

The valuation_error path matters: dead mints, rugs, and graduated tokens
without a Jupiter route will all fail to quote. We surface that as
"no quote available" rather than crashing the portfolio render.

Each position requires one Jupiter quote (~300ms). For 10 open positions
that's ~3s total. Acceptable for /portfolio; if it ever becomes a problem
we add concurrent quotes (asyncio).
"""

from __future__ import annotations

from typing import Optional

import jupiter_buy
import trader_positions

# Day 4.58: in-memory quote cache for /portfolio path. /portfolio +
# position-detail screens can be hit many times in a few seconds;
# without cache, every refresh round-trips to Jupiter even if nothing
# has changed. 5s TTL is short enough that displayed PnL stays
# meaningful but eliminates spam-refresh waste.
#
# Used ONLY by value_position. The monitor's own quote_sell call is
# NOT cached — monitor needs fresh price to fire SL/TP correctly.
import time as _time
_QUOTE_CACHE: dict = {}        # (mint, token_amount, slippage_bps) -> (ts, quote_dict)
_QUOTE_CACHE_TTL_S = 5.0


def _cached_quote_sell(mint: str, token_amount: int, slippage_bps: int,
                      timeout_s: float):
    """Wrap jupiter_buy.quote_sell with a 5s TTL cache. Same exceptions
    raised on cache miss as the underlying call. Caches successes only —
    failures bubble up to the caller and don't poison the cache."""
    now = _time.monotonic()
    key = (mint, int(token_amount), int(slippage_bps))
    cached = _QUOTE_CACHE.get(key)
    if cached and (now - cached[0]) < _QUOTE_CACHE_TTL_S:
        return cached[1]
    q = jupiter_buy.quote_sell(
        mint=mint, token_amount=int(token_amount),
        slippage_bps=slippage_bps, timeout_s=timeout_s,
    )
    _QUOTE_CACHE[key] = (now, q)
    # Opportunistic cleanup — drop entries older than 30s when cache
    # gets large, keeps memory bounded under sustained load.
    if len(_QUOTE_CACHE) > 500:
        cutoff = now - 30.0
        for k in list(_QUOTE_CACHE.keys()):
            if _QUOTE_CACHE[k][0] < cutoff:
                del _QUOTE_CACHE[k]
    return q


def value_position(pos: dict, *, slippage_bps: int = 500,
                   timeout_s: float = 3.0) -> dict:
    """Enrich a single position with live valuation. Always returns a dict
    (never raises) — if Jupiter fails, `valuation_error` is populated and
    the value fields are None.

    Uses _cached_quote_sell with 5s TTL — fast refresh paths reuse a
    recent quote instead of hammering Jupiter."""
    out = dict(pos)
    out["current_sol_value_lamports"] = None
    out["unrealized_pnl_lamports"]    = None
    out["unrealized_pnl_pct"]         = None
    out["current_route"]              = None
    out["valuation_error"]            = None

    token_amount = pos.get("token_amount") or 0
    buy_lamports = pos.get("buy_sol_lamports") or 0
    if token_amount <= 0 or buy_lamports <= 0:
        out["valuation_error"] = "zero token amount or zero buy"
        return out

    try:
        q = _cached_quote_sell(
            mint=pos["mint"],
            token_amount=int(token_amount),
            slippage_bps=slippage_bps,
            timeout_s=timeout_s,
        )
    except jupiter_buy.JupiterError as e:
        # Dead mint / rugged / no Jupiter route — surface, don't crash.
        msg = str(e)
        # Trim noise: Jupiter error bodies are long
        if len(msg) > 200:
            msg = msg[:200] + "…"
        out["valuation_error"] = msg
        return out

    current = int(q.get("outAmount") or 0)
    if current <= 0:
        out["valuation_error"] = "Jupiter quoted zero value"
        return out

    # Day 4.84 — correct PnL math for partial-sell positions.
    # Before: pnl = quote_for_remaining - original_full_cost. Bug — after a
    # partial TP fires the position has fewer tokens and the quote shrinks,
    # while buy_lamports stays at original full cost → position looked
    # underwater even when realized + remaining > cost.
    # After: total_value = realized_proceeds + quote_for_remaining;
    #        pnl = total_value - original_cost. Captures partial-leg wins.
    realized = int(pos.get("sell_sol_lamports") or 0)
    total_value = current + realized
    pnl = total_value - int(buy_lamports)
    pct = pnl / float(buy_lamports)

    route = [step["swapInfo"]["label"] for step in q.get("routePlan") or []]
    # Symbol + entry-MC enrichment for portfolio display (user-requested 2026-06-29)
    try:
        import metadata as _meta
        meta = _meta.enrich(pos["mint"]) or {}
        out["symbol"] = meta.get("symbol")
        out["name"]   = meta.get("name")
    except Exception:
        out["symbol"] = None
        out["name"]   = None
    out["entry_mcap_lamports"]        = pos.get("entry_mcap_lamports")
    out["realized_sol_lamports"]      = realized
    out["current_sol_value_lamports"] = current
    out["unrealized_pnl_lamports"]    = pnl
    out["unrealized_pnl_pct"]         = pct
    out["current_route"]              = route[0] if route else None
    return out


def value_open_positions(user_id: str | int, *, slippage_bps: int = 500,
                         timeout_s_per: float = 3.0) -> list[dict]:
    """Return every open position for `user_id`, enriched with live PnL.
    Order matches trader_positions.list_open_positions (newest first)."""
    rows = trader_positions.list_open_positions(user_id)
    return [value_position(r, slippage_bps=slippage_bps,
                           timeout_s=timeout_s_per) for r in rows]


def realized_summary(user_id: str | int) -> dict:
    """Aggregate stats across CLOSED positions for this user.
    Used by the hub + /history. Read-only — pure DB query, no Jupiter."""
    import sqlite3, contextlib
    db_path = trader_positions._db_path()
    with contextlib.closing(sqlite3.connect(db_path, timeout=10)) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT buy_sol_lamports, sell_sol_lamports, buy_fee_lamports, "
            "       sell_fee_lamports, net_pnl_lamports, exit_reason, "
            "       sell_timestamp "
            "  FROM trader_positions "
            " WHERE user_id = ? AND status = 'sold' "
            " ORDER BY sell_timestamp DESC",
            (str(user_id),),
        ).fetchall()
    n = len(rows)
    if n == 0:
        return {"n_trades": 0, "n_wins": 0, "n_losses": 0,
                "total_cost_lamports": 0, "total_received_lamports": 0,
                "total_fees_lamports": 0, "total_net_pnl_lamports": 0,
                "win_rate": 0.0, "best_trade": None, "worst_trade": None}
    n_wins = sum(1 for r in rows if (r["net_pnl_lamports"] or 0) > 0)
    n_losses = n - n_wins
    total_cost = sum(int(r["buy_sol_lamports"] or 0) for r in rows)
    total_recv = sum(int(r["sell_sol_lamports"] or 0) for r in rows)
    total_fees = sum(int((r["buy_fee_lamports"] or 0) + (r["sell_fee_lamports"] or 0))
                     for r in rows)
    total_net = sum(int(r["net_pnl_lamports"] or 0) for r in rows)
    nets = [int(r["net_pnl_lamports"] or 0) for r in rows]
    return {
        "n_trades":               n,
        "n_wins":                 n_wins,
        "n_losses":               n_losses,
        "win_rate":               (n_wins / n) if n else 0.0,
        "total_cost_lamports":    total_cost,
        "total_received_lamports": total_recv,
        "total_fees_lamports":    total_fees,
        "total_net_pnl_lamports": total_net,
        "best_trade":             max(nets) if nets else 0,
        "worst_trade":            min(nets) if nets else 0,
    }


def portfolio_summary(user_id: str | int, *, slippage_bps: int = 500) -> dict:
    """High-level totals for the /portfolio header. Returns:
        {
          n_open, n_with_valuation, n_unquotable,
          total_cost_basis_lamports, total_current_value_lamports,
          total_unrealized_pnl_lamports, total_unrealized_pnl_pct,
          positions: [enriched rows, newest first]
        }
    """
    enriched = value_open_positions(user_id, slippage_bps=slippage_bps)
    n_open = len(enriched)
    quoted = [p for p in enriched if p["current_sol_value_lamports"] is not None]
    n_quoted = len(quoted)

    cost = sum(p.get("buy_sol_lamports") or 0 for p in enriched)
    # Day 4.84 — header totals also include realized proceeds from partial sells.
    value = sum(p["current_sol_value_lamports"] for p in quoted)
    realized_total = sum((p.get("realized_sol_lamports") or 0) for p in quoted)
    total_quoted_cost = sum(p["buy_sol_lamports"] for p in quoted)
    pnl = (value + realized_total) - total_quoted_cost
    pct = (pnl / total_quoted_cost) if total_quoted_cost else 0.0

    return {
        "n_open":                          n_open,
        "n_with_valuation":                n_quoted,
        "n_unquotable":                    n_open - n_quoted,
        "total_cost_basis_lamports":       cost,
        "total_current_value_lamports":    value,
        "total_unrealized_pnl_lamports":   pnl,
        "total_unrealized_pnl_pct":        pct,
        "positions":                       enriched,
    }
