"""
Sniper scaffold for $GRADUATE signal.

Polls /api/v1/live every 3 seconds. When a fresh mint crosses your
configured probability threshold AND passes a couple of common-sense
filters, it logs an "ENTRY" line. By default it does NOT actually buy —
it's paper-mode. Wire up your own RPC + wallet keypair to make it live.

Why paper-mode by default: an automated buyer that triggers on every 80%+
mint will cost you money. Real-money sniping wants additional layers — at
minimum: position sizing, slippage caps, blacklists, max concurrent
positions. Use this script as a chassis, not as production code.

Get a free API key in one click: https://graduateoracle.fun/api
"""
import os
import time
import requests

API_KEY    = os.environ.get("GRADUATE_API_KEY", "grad_REPLACE_ME")
THRESHOLD  = float(os.environ.get("THRESHOLD", "0.80"))     # min grad-prob to enter
MAX_AGE_S  = int(os.environ.get("MAX_AGE_S", "120"))        # only consider mints younger than this
POLL_S     = int(os.environ.get("POLL_S", "3"))             # seconds between polls
POSITION   = float(os.environ.get("POSITION_SOL", "0.05"))  # paper position size

ENDPOINT = "https://graduateoracle.fun/api/v1/live"
HEADERS  = {"Authorization": f"Bearer {API_KEY}"}

# In-memory dedup so we don't "enter" the same mint repeatedly.
seen: set[str] = set()


def should_enter(mint: dict) -> bool:
    prob = mint.get("grad_prob") or 0
    age  = mint.get("age_s") or 0
    if prob < THRESHOLD: return False
    if age  > MAX_AGE_S: return False
    if mint.get("is_suspect"):    return False  # any single bot-flag = pass
    if (mint.get("current_mult") or 0) > 2.0:   return False  # already pumped
    return True


def execute(mint: dict):
    """Replace this with your real Jupiter/Raydium swap call. Default is print-only."""
    addr = mint["mint"]
    print(
        f"[ENTRY] {addr[:14]}…  "
        f"prob={mint['grad_prob']:.0%}  "
        f"vsol={mint['current_vsol_sol']:.0f}  "
        f"age={int(mint['age_s'])}s  "
        f"position={POSITION} SOL  "
        f"flags={mint.get('bot_flags') or []}"
    )


def main():
    if API_KEY == "grad_REPLACE_ME":
        print("Set GRADUATE_API_KEY env var. Get a key at https://graduateoracle.fun/api")
        return
    print(f"sniper · threshold={THRESHOLD}  max_age={MAX_AGE_S}s  poll={POLL_S}s")
    while True:
        try:
            r = requests.get(ENDPOINT, headers=HEADERS, params={"limit": 60}, timeout=10)
            r.raise_for_status()
            for m in r.json().get("mints", []):
                addr = m.get("mint")
                if not addr or addr in seen: continue
                if should_enter(m):
                    execute(m)
                    seen.add(addr)
        except Exception as e:
            print(f"poll failed: {e}")
        time.sleep(POLL_S)


if __name__ == "__main__":
    main()
