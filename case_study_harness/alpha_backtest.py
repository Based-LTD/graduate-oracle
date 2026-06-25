"""
★ ALPHA backtest harness.

Simulates trading on every resolved composite_prediction with a given
filter + exit-strategy config. Outputs realized PnL distribution.

The key reframe over previous analysis: outcome metric is EXPECTED TRADING
RETURN given realistic TP/SL/TSL behavior, NOT graduation rate. Daniel
caught this 2026-06-25 — graduation rate optimizes the wrong thing.

Trading model (approximate, based on peak_mult only — we don't have
time-series price data):
  • peak_from_entry = peak_mult_24h / max_mult_at_cross
  • If peak_from_entry >= TP1 threshold: TP1 fires (sell TP1_PCT)
  • If peak_from_entry >= TP2 threshold: TP2 fires on remaining
  • Remaining position (if TP2 didn't clear) approximates a TSL exit at
    peak_from_entry × (1 - TSL_PCT)
  • If peak_from_entry NEVER reaches TP1: assume the trade went underwater,
    SL fires at SL_PCT
  • Net trade PnL = weighted sum of partial sells

Limitations to acknowledge:
  • Doesn't model path-dependent SL-before-TP cases
  • Assumes peak comes from a SINGLE up-move (no V-shapes)
  • Doesn't account for slippage or fees (subtract ~2% per trade for honest results)
  • peak_mult_24h is 24h from LAUNCH; for late-age crosses the window is shorter
"""
import sqlite3
import os
import sys

DB_PATH = "/data/data.sqlite"


def simulate_trade(peak_from_entry, *, sl_pct, tp_ladder, tsl_pct):
    """Compute realized trade PnL given peak-from-entry and exit config.

    Returns net PnL as a fraction (e.g., 0.50 = +50%, -0.25 = -25%).

    tp_ladder: list of {"pct": float, "sell_pct": float}
      pct = gain trigger (e.g., 50 means +50% from entry)
      sell_pct = portion of remaining position to sell at this rung
    sl_pct: negative number, e.g., -25 for -25% SL
    tsl_pct: trailing stop percentage off the peak
    """
    # Convert to multiplier-from-entry units
    sl_mult = 1.0 + sl_pct / 100.0  # e.g., 0.75 for -25% SL
    if peak_from_entry < sl_mult:
        # Never reached profitable territory. SL fired at the configured level.
        # Approximation: SL captures at sl_mult.
        return sl_pct / 100.0

    # Walk the TP ladder
    remaining = 1.0  # fraction of position still held
    realized_pnl = 0.0
    for rung in tp_ladder:
        trig_mult = 1.0 + rung["pct"] / 100.0
        if peak_from_entry >= trig_mult:
            sell_frac = (rung["sell_pct"] / 100.0) * remaining
            realized_pnl += sell_frac * (trig_mult - 1.0)
            remaining -= sell_frac
        else:
            break

    if remaining > 0:
        # Position ran out of TP rungs and is riding. Approximate exit
        # via TSL at peak × (1 - tsl_pct). Net mult for remaining = peak * (1 - tsl_pct/100)
        tsl_exit_mult = peak_from_entry * (1.0 - tsl_pct / 100.0)
        # But TSL can't go below SL — clamp to sl_mult
        tsl_exit_mult = max(tsl_exit_mult, sl_mult)
        realized_pnl += remaining * (tsl_exit_mult - 1.0)

    return realized_pnl


def load_resolved_predictions(conn):
    """Returns list of dicts with mint, signal features, peak_from_entry."""
    rows = conn.execute("""
        SELECT mint, composite_score, threshold_at_cross, smart_money_in,
               max_mult_at_cross, age_s_at_cross, mc_at_cross_usd, tg_tier,
               peak_mult_24h, did_graduate
          FROM composite_predictions
         WHERE outcome_resolved_at IS NOT NULL
           AND peak_mult_24h IS NOT NULL
           AND max_mult_at_cross IS NOT NULL
           AND max_mult_at_cross > 0
    """).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        thr = d["threshold_at_cross"] or 0
        d["sr"] = (d["composite_score"] / thr) if thr > 0 else 0
        d["peak_from_entry"] = d["peak_mult_24h"] / d["max_mult_at_cross"]
        out.append(d)
    return out


def backtest(predictions, *, filter_fn, exit_config, label="filter"):
    """Run trades on all predictions matching filter_fn, return aggregate stats."""
    matched = [p for p in predictions if filter_fn(p)]
    if not matched:
        return {"label": label, "n": 0}

    pnls = [simulate_trade(p["peak_from_entry"], **exit_config) for p in matched]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    avg = sum(pnls) / len(pnls)
    median = sorted(pnls)[len(pnls) // 2]
    big_wins = [p for p in pnls if p >= 0.5]  # +50%+ trades
    huge_wins = [p for p in pnls if p >= 1.0]  # 2x+ trades

    return {
        "label": label,
        "n": len(matched),
        "win_rate": len(wins) / len(matched),
        "avg_pnl": avg,
        "median_pnl": median,
        "expected_return": avg,  # per-trade expected return
        "n_big_wins": len(big_wins),
        "n_huge_wins": len(huge_wins),
        "avg_win_pct": (sum(wins) / len(wins)) if wins else 0,
        "avg_loss_pct": (sum(losses) / len(losses)) if losses else 0,
    }


def fmt(s):
    if s["n"] == 0:
        return f'  {s["label"]:50s} n=0'
    return (f'  {s["label"]:50s} n={s["n"]:5,d}  '
            f'wr={s["win_rate"]*100:4.0f}%  '
            f'avg={s["avg_pnl"]*100:+6.1f}%  '
            f'med={s["median_pnl"]*100:+6.1f}%  '
            f'+50%+_wins={s["n_big_wins"]:4d}  '
            f'2x+={s["n_huge_wins"]:3d}')


def run():
    if not os.path.exists(DB_PATH):
        print(f"DB not found at {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    preds = load_resolved_predictions(conn)
    print(f"Loaded {len(preds):,} resolved predictions\n")

    # Exit config A: Daniel's current settings
    daniels = {
        "sl_pct": -25.0,
        "tsl_pct": 30.0,
        "tp_ladder": [{"pct": 50, "sell_pct": 50}, {"pct": 100, "sell_pct": 100}],
    }
    # Exit config B: looser SL
    loose_sl = {**daniels, "sl_pct": -50.0}
    # Exit config C: looser SL + looser TSL
    looser = {**daniels, "sl_pct": -50.0, "tsl_pct": 40.0}
    # Exit config D: tight TP1 (capture earlier)
    early_tp = {**daniels, "tp_ladder": [{"pct": 30, "sell_pct": 50}, {"pct": 100, "sell_pct": 100}]}

    # Filter A: current ★ ALPHA
    alpha = lambda p: (p["tg_tier"] in ("WATCH", "SCOUT")
                       and p["sr"] >= 3
                       and p["smart_money_in"] is not None
                       and 3 <= p["smart_money_in"] <= 9)
    # Filter B: ★ ALPHA + tighter SM
    alpha_sm78 = lambda p: (alpha(p) and 7 <= p["smart_money_in"] <= 8)
    # Filter C: ★ ALPHA + low MC (the upside thesis)
    alpha_low_mc = lambda p: (alpha(p) and (p["mc_at_cross_usd"] or 0) < 10000)
    # Filter D: ★ ALPHA + mid MC
    alpha_mid_mc = lambda p: (alpha(p) and 10000 <= (p["mc_at_cross_usd"] or 0) < 20000)
    # Filter E: ★ ALPHA + age >= 30s
    alpha_aged = lambda p: (alpha(p) and (p["age_s_at_cross"] or 0) >= 30)
    # Filter F: WATCH only (not SCOUT)
    alpha_watch = lambda p: (alpha(p) and p["tg_tier"] == "WATCH")
    # Filter G: SCOUT only
    alpha_scout = lambda p: (alpha(p) and p["tg_tier"] == "SCOUT")
    # Filter H: all alerts (no filter — baseline)
    all_alerts = lambda p: True

    configs = [
        ("Daniel's current (-25 SL, 30 TSL)", daniels),
        ("Looser SL (-50)",                    loose_sl),
        ("Looser SL+TSL (-50, 40)",            looser),
        ("Early TP1 (+30 instead of +50)",     early_tp),
    ]
    filters = [
        ("ALL resolved (base, no filter)",        all_alerts),
        ("★ ALPHA (current)",                     alpha),
        ("★ ALPHA + SM 7-8",                      alpha_sm78),
        ("★ ALPHA + MC < $10K (upside thesis)",   alpha_low_mc),
        ("★ ALPHA + MC $10-20K",                  alpha_mid_mc),
        ("★ ALPHA + age >= 30s",                  alpha_aged),
        ("★ ALPHA + WATCH only",                  alpha_watch),
        ("★ ALPHA + SCOUT only",                  alpha_scout),
    ]

    for cfg_label, cfg in configs:
        print(f"== Exit config: {cfg_label} ==")
        print(f"   SL={cfg['sl_pct']}%  TSL={cfg['tsl_pct']}%  TPs={cfg['tp_ladder']}")
        print()
        for f_label, f_fn in filters:
            stats = backtest(preds, filter_fn=f_fn, exit_config=cfg, label=f_label)
            print(fmt(stats))
        print()


if __name__ == "__main__":
    run()
