"""
trader_monitor — auto-exit engine for open positions.

Reads each open position, asks Jupiter what it's worth right now, and
fires a sell when TP / SL / TSL / breakeven conditions hit. Designed as
two layers:

  • `evaluate_position(pos, current_lamports)` — PURE function. Given a
    position row + current Jupiter quote, returns the action to take:
    one of None, ("tp", index, sell_pct), ("sl",), ("tsl",), ("breakeven",).
    No side effects — easy to unit-test exhaustively.

  • `tick(user_id)` — IMPURE driver. Loads open positions, fetches a
    Jupiter quote for each, calls evaluate_position, and executes the
    action via trader_orchestrator.sell + state writes. Designed to be
    called every N seconds by a long-running loop (in-process daemon
    or fly cron). Idempotent: if a TP already triggered we don't re-fire.

The HWM (high-water mark) is tracked as expected_sol_out for the
CURRENT remaining token_amount. After a partial sell we reset HWM to
None so the next tick re-baselines off the post-sale Jupiter quote.

Two reasons the monitor would NEVER fire a sell, surfaced loudly:
  1. Position config has no exit rules (tp_ladder=null, sl=null, tsl=null)
     → row in result["skipped"] with reason='no_rules'
  2. Jupiter can't quote (dead mint, rugged)
     → row in result["unquotable"] with the Jupiter error
"""

from __future__ import annotations

import time as _time
from typing import Optional, Tuple, Union

import jupiter_buy
import trader_positions

# Day 4.58 Fix 3: bonding-curve fetch cache. 3s TTL is short enough
# that the curve state is fresh for trigger decisions but long enough
# to suppress re-fetching across the per-position loop within one tick.
# Bypasses Jupiter for pre-grad pump.fun mints (~70% of trades).
_CURVE_CACHE: dict = {}              # mint -> (timestamp, curve_dict)
_CURVE_CACHE_TTL_S = 3.0


def _cached_curve_fetch(mint: str):
    """Read bonding curve state via the same RPC the orchestrator uses,
    cached briefly. Returns None on any failure (caller falls back to
    Jupiter). Never raises."""
    now = _time.monotonic()
    cached = _CURVE_CACHE.get(mint)
    if cached and (now - cached[0]) < _CURVE_CACHE_TTL_S:
        return cached[1]
    try:
        import bonding_curve as _bc
        import trader_wallets as _tw
        curve = _bc.fetch(mint, rpc_url=_tw._RPC)
        _CURVE_CACHE[mint] = (now, curve)
        # Opportunistic cleanup
        if len(_CURVE_CACHE) > 500:
            cutoff = now - 30.0
            for k in list(_CURVE_CACHE.keys()):
                if _CURVE_CACHE[k][0] < cutoff:
                    del _CURVE_CACHE[k]
        return curve
    except Exception:
        return None

# Default monitor cadence — once every 6 seconds. Slow enough not to
# hammer Jupiter; fast enough to catch fast pump.fun rugs. Tunable per
# tick() call.
DEFAULT_TICK_INTERVAL_S = 6.0


# ── Action types ────────────────────────────────────────────────────────
#
# evaluate_position returns one of these dicts (or None). The driver
# layer translates them into sell calls + state updates.

ActionType = dict  # {"kind": ..., ...}


def evaluate_position(
    pos: dict,
    current_sol_value_lamports: int,
    *,
    moonshot_mode: bool = False,
) -> Optional[ActionType]:
    """Decide what to do with this position at the current price.

    Returns one of:
      None                                  — nothing to do; just update HWM
      {"kind": "tp", "index": int, "sell_pct": float, "label": "tp1"}
                                            — partial or full TP sell
      {"kind": "sl"}                        — full sell, stop loss hit
      {"kind": "tsl"}                       — full sell, trailing stop hit
      {"kind": "breakeven_arm"}             — flip sl_armed_at_breakeven=1
                                              (then re-evaluate next tick)

    Pure function — no DB writes, no network. Easy to unit-test.

    Order of evaluation matters:
      1. Breakeven arm — once gain >= breakeven_pct, flip the flag.
         Doesn't sell; just changes the SL behavior next tick.
      2. SL — if armed at breakeven, SL=0%; else SL=sl_pct.
         If hit, sell 100%.
      3. TSL — if HWM > entry AND current < HWM*(1-tsl/100), sell 100%.
         (TSL never fires when underwater — SL handles that.)
      4. TP — if gain >= ladder[next_tp_index].pct, fire that rung.

    moonshot_mode: when True AND a TP rung has already fired (next_tp_index>0),
      breakeven-arm and trailing-stop evaluations are SKIPPED for this tick.
      SL still fires (catastrophic protection), remaining TP rungs still fire.
      Idea: after de-risking via the first TP, let the rest of the position
      ride for the post-correction second leg without tight stops.
    """
    entry_lamports = pos.get("buy_sol_lamports") or 0
    if entry_lamports <= 0 or current_sol_value_lamports <= 0:
        return None

    # Pull per-position auto-exit config. None means no override; we'd
    # need the orchestrator to have stamped this in at buy time.
    tp_ladder_json = pos.get("tp_ladder_json")
    sl_pct         = pos.get("sl_pct")
    tsl_pct        = pos.get("tsl_pct")
    breakeven_pct  = pos.get("breakeven_pct")
    next_tp_index  = pos.get("next_tp_index") or 0
    armed          = bool(pos.get("sl_armed_at_breakeven"))

    # Day 4.42: PRICE-PER-TOKEN based thresholds (was value-vs-cost).
    # Old logic: gain_pct = (current_value - entry_cost) / entry_cost.
    # Bug: after a partial TP fired, current_value reflects the SMALLER
    # remaining position, so the displayed gain dropped artificially.
    # TP2 at +300% then required price to rise to ~6× entry instead of 4×.
    #
    # New logic: compare CURRENT price-per-token vs ENTRY price-per-token.
    # Quantity-invariant — partial sells don't shift thresholds.
    entry_pp     = pos.get("entry_price_lamports_per_token") or 0
    token_amount = pos.get("token_amount") or 0
    if entry_pp > 0 and token_amount > 0:
        current_pp = current_sol_value_lamports / token_amount
        gain_pct = (current_pp - entry_pp) / entry_pp * 100
    else:
        # Defensive fallback for any legacy / corrupt row missing entry_pp
        current_pp = None
        gain_pct = (current_sol_value_lamports - entry_lamports) / entry_lamports * 100

    # HWM also tracked in price-per-token terms. Existing positions
    # without the new column get initialized from entry_pp on first read.
    hwm_pp = pos.get("hwm_price_per_token_lamports")
    if hwm_pp is None or hwm_pp <= 0:
        hwm_pp = entry_pp if entry_pp > 0 else None

    # Moonshot Mode: once any TP rung has fired, suppress BE-arm and TSL.
    # SL stays active (catastrophic protection). Remaining TP rungs still fire.
    moonshot_active = bool(moonshot_mode) and int(next_tp_index) > 0

    # 1. Breakeven arm — gain crossed the threshold for the first time
    if (not moonshot_active and breakeven_pct is not None and not armed
            and gain_pct >= float(breakeven_pct)):
        return {"kind": "breakeven_arm"}

    # 2. Stop loss — armed-at-breakeven flips effective SL to 0%.
    # Under moonshot_active, ignore the breakeven-armed flag so the
    # SL doesn't sit at entry — fall back to the original sl_pct, which
    # gives the position room to retrace and recover.
    if moonshot_active:
        effective_sl = sl_pct if sl_pct is not None else None
    else:
        effective_sl = 0.0 if armed else (sl_pct if sl_pct is not None else None)
    if effective_sl is not None and gain_pct <= float(effective_sl):
        return {"kind": "sl"}

    # 3. Trailing stop — suppressed entirely under moonshot_active.
    if (not moonshot_active and tsl_pct is not None and hwm_pp is not None
            and current_pp is not None and entry_pp > 0
            and hwm_pp > entry_pp):
        floor_pp = hwm_pp * (1.0 - float(tsl_pct) / 100.0)
        if current_pp < floor_pp:
            return {"kind": "tsl"}

    # 4. Take-profit ladder
    if tp_ladder_json:
        import json as _json
        try:
            ladder = _json.loads(tp_ladder_json)
        except Exception:
            ladder = None
        if isinstance(ladder, list) and next_tp_index < len(ladder):
            rung = ladder[next_tp_index]
            if isinstance(rung, dict):
                trig_pct = float(rung.get("pct", 0))
                sell_pct = float(rung.get("sell_pct", 100)) / 100.0
                if gain_pct >= trig_pct:
                    return {
                        "kind": "tp",
                        "index": next_tp_index,
                        "sell_pct": min(1.0, max(0.0, sell_pct)),
                        "label": f"tp{next_tp_index + 1}",
                    }
    return None


# ── Driver (tick) ───────────────────────────────────────────────────────

def tick(user_id: str | int, *, live: bool = False,
         dry_run: bool = False,
         slippage_bps: int = 500) -> dict:
    """Run one monitor pass over all open positions for `user_id`.

    Args:
      live:    True = real sell submissions via orchestrator. False =
               evaluation + HWM updates only, no sells. ALWAYS keep
               False during early testing.
      dry_run: True = compute actions but never call orchestrator.sell.
               Useful for unit tests / dev. live and dry_run are mutually
               exclusive — dry_run wins.
      slippage_bps: passed to orchestrator.sell when actions fire.

    Returns a dict with arrays of per-position outcomes:
      {
        n_open, n_actions_taken, n_skipped, n_unquotable,
        actions:    [{position_id, action, sell_result?, error?}, ...],
        unquotable: [{position_id, mint, error}, ...],
      }

    Never raises — per-position errors are localized into the result.
    """
    # Lazy import — orchestrator imports this module's neighbors, so
    # importing it at module load creates a cycle.
    import trader_orchestrator

    out = {
        "n_open":           0,
        "n_actions_taken":  0,
        "n_skipped":        0,
        "n_unquotable":     0,
        "actions":          [],
        "unquotable":       [],
    }

    open_positions = trader_positions.list_open_positions(user_id)

    # Read user settings ONCE per tick — stagnation check needs them per
    # position but the values don't change between positions in one pass.
    try:
        _user_cfg = trader_positions.get_user_settings(user_id)
    except Exception:
        _user_cfg = {}
    _stale_timeout_min = int(_user_cfg.get("stale_timeout_minutes") or 0)
    _stale_band_pct    = float(_user_cfg.get("stale_band_pct") or 3.0)
    _moonshot_mode     = bool(_user_cfg.get("moonshot_mode_enabled"))
    out["n_open"] = len(open_positions)
    now = int(_time.time())

    for pos in open_positions:
        pid = pos["id"]
        # 1. Get current value. Day 4.58 (Fix 3): for pre-graduation
        # pump.fun mints we can derive the price directly from the
        # bonding curve's virtual reserves (constant-product formula),
        # bypassing Jupiter entirely. ~70% of our trades are pre-grad,
        # so this dramatically reduces Jupiter load. For graduated
        # mints (no bonding curve) we fall back to Jupiter.
        current = 0
        used_curve = False
        if pos["mint"].endswith("pump"):
            try:
                curve = _cached_curve_fetch(pos["mint"])
                if curve and not curve.get("complete"):
                    vsol = int(curve.get("virtual_sol_reserves") or 0)
                    vtok = int(curve.get("virtual_token_reserves") or 0)
                    tok = int(pos["token_amount"])
                    if vsol > 0 and vtok > 0 and tok > 0:
                        # Constant-product: amount_out = vsol * tok / (vtok + tok)
                        current = (vsol * tok) // (vtok + tok)
                        # Pump.fun charges a 1% trade fee — apply to be honest
                        current = int(current * 0.99)
                        if current > 0:
                            used_curve = True
            except Exception as e:
                # Any curve fetch failure: fall through to Jupiter
                print(f"[monitor] curve fetch failed for {pos['mint'][:10]}: {e}",
                      flush=True)

        # Jupiter path — used for graduated mints, non-pump.fun mints,
        # or when the bonding-curve path failed.
        # Day 4.47: retry ONCE after a brief delay if the first attempt
        # fails. Catches transient Jupiter hiccups.
        last_err = None
        if not used_curve:
            for attempt in (1, 2):
                try:
                    q = jupiter_buy.quote_sell(
                        mint=pos["mint"],
                        token_amount=int(pos["token_amount"]),
                        slippage_bps=slippage_bps,
                        timeout_s=3.0,
                    )
                    current = int(q.get("outAmount") or 0)
                    if current > 0:
                        break
                except Exception as e:
                    last_err = e
                if attempt == 1:
                    import time as _t
                    _t.sleep(1.0)
        if current <= 0:
            err_msg = (str(last_err)[:200] if last_err
                       else "Jupiter quoted zero value")
            out["unquotable"].append({
                "position_id": pid, "mint": pos["mint"], "error": err_msg,
            })
            out["n_unquotable"] += 1
            # Still mark the check timestamp so we don't think we're stalled
            trader_positions.update_position_monitor_state(
                pid, last_monitor_check_at=now,
            )
            continue
        if current <= 0:
            out["n_unquotable"] += 1
            continue

        # 2. Update HWM — both the legacy value-based one (kept for
        # historical reporting) and the new price-per-token HWM that
        # the trailing-stop logic actually reads.
        hwm_value = pos.get("high_water_mark_lamports") or 0
        token_amount = pos.get("token_amount") or 0
        entry_pp = pos.get("entry_price_lamports_per_token") or 0
        current_pp = (current / token_amount) if token_amount > 0 else 0
        hwm_pp_existing = pos.get("hwm_price_per_token_lamports") or entry_pp

        updates = {"last_monitor_check_at": now}
        if current > hwm_value:
            updates["high_water_mark_lamports"] = current
            pos["high_water_mark_lamports"] = current
        if current_pp > hwm_pp_existing:
            updates["hwm_price_per_token_lamports"] = current_pp
            pos["hwm_price_per_token_lamports"] = current_pp
        trader_positions.update_position_monitor_state(pid, **updates)

        # 3. Evaluate action
        action = evaluate_position(pos, current, moonshot_mode=_moonshot_mode)

        # 3b. Stagnation check (Day 4.50). Only runs if no TP/SL/TSL fired
        # AND the user enabled it (stale_timeout_minutes > 0). Tracks an
        # anchor price; if price moves outside ±stale_band_pct of the
        # anchor, anchor resets to current price + now. If price stays
        # inside the band for stale_timeout_minutes, fire "stale" exit.
        if (action is None and _stale_timeout_min > 0
                and current_pp > 0 and entry_pp > 0):
            anchor_pp = pos.get("stale_anchor_pp")
            anchor_at = pos.get("stale_anchor_at")
            if not anchor_pp or not anchor_at:
                # First sighting of this position — set the anchor.
                trader_positions.update_position_monitor_state(
                    pid, stale_anchor_pp=current_pp, stale_anchor_at=now,
                )
            else:
                band_size = anchor_pp * (_stale_band_pct / 100.0)
                moved = abs(current_pp - anchor_pp) > band_size
                if moved:
                    # Price escaped the band — reset anchor; the position
                    # is alive, give it more time.
                    trader_positions.update_position_monitor_state(
                        pid, stale_anchor_pp=current_pp, stale_anchor_at=now,
                    )
                elif (now - int(anchor_at)) >= _stale_timeout_min * 60:
                    # Stayed flat past the timeout — call it dead.
                    action = {"kind": "stale", "label": "stale"}

        if action is None:
            out["n_skipped"] += 1
            continue

        # 4. Apply action
        record = {
            "position_id": pid, "action": action,
            "current_value_lamports": current,
            "entry_lamports": pos["buy_sol_lamports"],
        }

        kind = action["kind"]
        if kind == "breakeven_arm":
            # Pure state flip — no sell
            trader_positions.update_position_monitor_state(
                pid, sl_armed_at_breakeven=True,
            )
            record["applied"] = True
            try:
                import trader_notify
                trader_notify.notify_auto_exit(
                    user_id, position_id=pid, mint=pos["mint"],
                    kind="breakeven_arm", applied=True,
                )
            except Exception:
                pass
            out["actions"].append(record)
            out["n_actions_taken"] += 1
            continue

        # For TP / SL / TSL: invoke orchestrator.sell
        if dry_run:
            record["applied"] = False
            record["dry_run"] = True
            out["actions"].append(record)
            out["n_actions_taken"] += 1
            continue

        if kind == "tp":
            sell_pct = action["sell_pct"]
            label    = action["label"]
        else:  # sl / tsl
            sell_pct = 1.0
            label    = kind

        try:
            sell_result = trader_orchestrator.sell(
                user_id, pid, sell_pct=sell_pct,
                slippage_bps=slippage_bps, live=live,
            )
            record["applied"] = True
            record["sell_result"] = {
                "phase":      sell_result["phase"],
                "signature":  sell_result.get("sell_signature"),
                "new_status": sell_result.get("new_status"),
            }
            # Stamp the exit_reason. For partial TPs the position stays
            # open — we set the reason on the LAST rung only.
            if kind == "tp" and sell_result.get("new_status") == "sold":
                trader_positions.set_exit_reason(pid, label)
                # Advance TP index; if more rungs remain, leave it for next tick
                trader_positions.update_position_monitor_state(
                    pid, next_tp_index=action["index"] + 1,
                    high_water_mark_lamports=None,  # reset HWM (smaller position)
                )
            elif kind == "tp":
                trader_positions.update_position_monitor_state(
                    pid, next_tp_index=action["index"] + 1,
                    high_water_mark_lamports=None,
                )
                # When sl/tsl fires, position is fully closed and we stamp.
            elif kind in ("sl", "tsl"):
                trader_positions.set_exit_reason(pid, label)

            # ── Notify the user via Telegram ─────────────────────────
            try:
                import trader_notify
                trader_notify.notify_auto_exit(
                    user_id, position_id=pid, mint=pos["mint"], kind=label,
                    applied=True,
                    sell_signature=sell_result.get("sell_signature") or "",
                    sol_out_lamports=int(sell_result.get("expected_sol_out_lamports") or 0),
                )
                # When the final leg closes the position, send a full
                # PnL summary so the user sees the whole-trade outcome
                # (not just the per-leg ticks). Multi-leg accounting was
                # fixed in Day 4.35 so the totals are honest.
                if sell_result.get("new_status") == "sold":
                    trader_notify.notify_position_closed(user_id, pid)
            except Exception as ne:
                print(f"[trader_monitor] notify_auto_exit failed: {ne}", flush=True)
        except Exception as e:
            record["applied"] = False
            record["error"] = str(e)[:300]
            try:
                import trader_notify
                trader_notify.notify_auto_exit(
                    user_id, position_id=pid, mint=pos["mint"], kind=label,
                    applied=False, error=str(e)[:200],
                )
            except Exception:
                pass
        out["actions"].append(record)
        out["n_actions_taken"] += 1

    return out


# ── Long-running loop (optional driver) ─────────────────────────────────

def run_loop(user_id: str | int, *, live: bool = False,
             interval_s: float = DEFAULT_TICK_INTERVAL_S,
             max_ticks: Optional[int] = None) -> None:
    """Drive `tick()` in a sleep-loop. Useful as a separate process or
    a supervisord-managed daemon. Pass max_ticks for a bounded test
    run; default is infinite."""
    n = 0
    while True:
        try:
            tick(user_id, live=live)
        except Exception as e:
            # Catch-all so the loop never dies. Any per-position error
            # is already captured inside tick(); this is a last resort.
            print(f"[trader_monitor] tick failed: {e}", flush=True)
        n += 1
        if max_ticks is not None and n >= max_ticks:
            return
        _time.sleep(interval_s)
