"""
Paper trading bot · clone of the strategy that powers the public PAPER P&L
tab on graduateoracle.fun.

Polls the live $GRADUATE API for fresh signals, opens fictitious positions
when grad-probability crosses your threshold, then tracks each position's
real Jupiter price until +20% take-profit, -25% stop-loss, or 30 min
timeout. Logs every entry and exit to a local JSONL file.

This is the same logic running on our public dashboard — copy it, fork it,
swap thresholds, plug it into your wallet, whatever. The point of this file
is to show that the paper P&L numbers we publish are reproducible, not magic.

Get a free API key: https://graduateoracle.fun/api
"""
import json
import os
import time
import requests

API_KEY        = os.environ.get("GRADUATE_API_KEY", "grad_REPLACE_ME")
THRESHOLD      = float(os.environ.get("THRESHOLD", "0.80"))
TARGET_PROFIT  = 0.20      # +20% take profit
STOP_LOSS      = 0.25      # -25% stop loss
TIMEOUT_S      = 1800      # 30 min max hold
POSITION_SOL   = 0.10      # fixed sizing — overlapping positions don't double-spend
TICK_S         = 5
LEDGER_FILE    = os.environ.get("LEDGER_FILE", "paper-trades.jsonl")

LIVE_URL    = "https://graduateoracle.fun/api/v1/live"
JUP_URL     = "https://lite-api.jup.ag/price/v3"
HEADERS     = {"Authorization": f"Bearer {API_KEY}"}

positions: dict[str, dict] = {}   # mint -> {entry_at, entry_price, ...}
realized_pnl_sol = 0.0


def fetch_live(min_prob: float) -> list[dict]:
    r = requests.get(LIVE_URL, headers=HEADERS, params={"limit": 80, "min_prob": min_prob}, timeout=10)
    r.raise_for_status()
    return r.json().get("mints", [])


def fetch_prices(mints: list[str]) -> dict[str, float]:
    if not mints: return {}
    r = requests.get(JUP_URL, params={"ids": ",".join(mints)}, timeout=10)
    if r.status_code != 200: return {}
    out = {}
    for mint, info in (r.json() or {}).items():
        if isinstance(info, dict) and info.get("usdPrice") is not None:
            try:    out[mint] = float(info["usdPrice"])
            except (ValueError, TypeError): pass
    return out


def append_ledger(event: dict):
    event["ts"] = int(time.time())
    with open(LEDGER_FILE, "a") as f:
        f.write(json.dumps(event) + "\n")


def open_position(m: dict, price: float):
    positions[m["mint"]] = {
        "entry_at": int(time.time()),
        "entry_price": price,
        "entry_prob": m.get("grad_prob"),
        "entry_mult": m.get("current_mult"),
    }
    append_ledger({"event": "OPEN", "mint": m["mint"],
                   "price_usd": price, "grad_prob": m.get("grad_prob")})
    print(f"OPEN {m['mint'][:14]}… prob={m.get('grad_prob'):.0%} entry=${price:.2e}")


def close_position(mint: str, price: float, reason: str):
    global realized_pnl_sol
    pos = positions.pop(mint, None)
    if not pos: return
    pnl_pct = price / pos["entry_price"] - 1
    pnl_sol = POSITION_SOL * pnl_pct
    realized_pnl_sol += pnl_sol
    held = int(time.time()) - pos["entry_at"]
    append_ledger({"event": "CLOSE", "mint": mint, "reason": reason,
                   "exit_price_usd": price, "pnl_pct": pnl_pct,
                   "pnl_sol": pnl_sol, "held_secs": held,
                   "cumulative_pnl_sol": realized_pnl_sol})
    print(f"CLOSE {mint[:14]}… {reason} pnl={pnl_pct*100:+.1f}% "
          f"({pnl_sol:+.4f} SOL · cum {realized_pnl_sol:+.4f})")


def tick():
    # Open new positions
    for m in fetch_live(THRESHOLD):
        if m.get("is_suspect"): continue
        if m["mint"] in positions: continue
        # Snapshot the entry price from Jupiter
        prices = fetch_prices([m["mint"]])
        p = prices.get(m["mint"])
        if p and p > 0:
            open_position(m, p)
    if not positions: return
    # Check exits
    cur = fetch_prices(list(positions.keys()))
    now = int(time.time())
    for mint, pos in list(positions.items()):
        price = cur.get(mint)
        held = now - pos["entry_at"]
        if price is None or price <= 0:
            if held >= TIMEOUT_S:
                close_position(mint, pos["entry_price"], "stale_no_quote")
            continue
        ratio = price / pos["entry_price"]
        if ratio <= 1 - STOP_LOSS:
            close_position(mint, price, "stop_loss")
        elif ratio >= 1 + TARGET_PROFIT:
            close_position(mint, price, "target_20")
        elif held >= TIMEOUT_S:
            close_position(mint, price, "timeout")


def main():
    if API_KEY == "grad_REPLACE_ME":
        print("Set GRADUATE_API_KEY. Get a key at https://graduateoracle.fun/api")
        return
    print(f"paper-bot · threshold={THRESHOLD}  target=+{TARGET_PROFIT*100:.0f}%  "
          f"stop=-{STOP_LOSS*100:.0f}%  position={POSITION_SOL} SOL  "
          f"ledger={LEDGER_FILE}")
    while True:
        try:    tick()
        except Exception as e:  print(f"tick failed: {e}")
        time.sleep(TICK_S)


if __name__ == "__main__":
    main()
