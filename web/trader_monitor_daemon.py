"""
trader_monitor_daemon — long-running auto-exit monitor process.

Runs trader_monitor.tick() in a loop for every admin in ADMIN_TG_IDS.
Started by supervisord; restarts on crash.

Safety:
  • Off by default. Set TRADER_ENABLED=1 to actually fire sells.
    Without it, the loop runs in DRY-RUN mode (evaluation only, no
    network submits). This lets the daemon start safely on prod even
    before operators are ready to trust it.
  • Per-tick errors are caught + logged but never crash the loop.
  • Tick interval is conservative (10s) to stay well under Jupiter's
    rate limits even with 100+ open positions.
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

# Make web/ importable when this is run as a top-level script
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


TICK_INTERVAL_S  = float(os.environ.get("TRADER_MONITOR_TICK_S", "10"))
LIVE             = os.environ.get("TRADER_ENABLED", "").strip() == "1"
LIVE_TRIGGERS    = os.environ.get("TRADER_MONITOR_LIVE", "").strip() == "1"


def _admin_user_ids() -> list[str]:
    """Return the list of telegram_ids (as strings — that's what
    trader_wallets / trader_positions use) we should monitor for.
    Same env var the bot uses."""
    out = []
    for raw in os.environ.get("ADMIN_TG_IDS", "").split(","):
        raw = raw.strip()
        if raw.isdigit():
            out.append(raw)
    return out


def main():
    admins = _admin_user_ids()
    if not admins:
        print("[trader_monitor_daemon] ADMIN_TG_IDS empty — nothing to monitor. "
              "Sleeping forever.", flush=True)
        # Don't exit (supervisord would restart-loop). Just idle.
        while True:
            time.sleep(60)

    print(f"[trader_monitor_daemon] starting", flush=True)
    print(f"  TRADER_ENABLED       = {LIVE}", flush=True)
    print(f"  TRADER_MONITOR_LIVE  = {LIVE_TRIGGERS}", flush=True)
    print(f"  tick interval        = {TICK_INTERVAL_S}s", flush=True)
    print(f"  monitored user_ids   = {admins}", flush=True)
    if LIVE and not LIVE_TRIGGERS:
        print(f"  ⚠ DRY-RUN MODE — triggers detected but no sells will fire", flush=True)
        print(f"    Set TRADER_MONITOR_LIVE=1 to enable real submissions", flush=True)

    import trader_monitor  # lazy — keeps the daemon's import light

    n = 0
    while True:
        for user_id in admins:
            try:
                result = trader_monitor.tick(
                    user_id,
                    live=LIVE_TRIGGERS,   # only actually sell when explicit
                    dry_run=not LIVE_TRIGGERS,
                )
                if result["n_actions_taken"] > 0 or result["n_unquotable"] > 0:
                    # Log only when something interesting happened
                    print(f"[trader_monitor_daemon] user={user_id} "
                          f"open={result['n_open']} acts={result['n_actions_taken']} "
                          f"unquotable={result['n_unquotable']}", flush=True)
                    for a in result["actions"]:
                        kind = a["action"]["kind"]
                        pid = a["position_id"]
                        applied = a.get("applied", False)
                        sig = (a.get("sell_result") or {}).get("signature", "")
                        print(f"  pid={pid} {kind} applied={applied} sig={sig[:16]}", flush=True)
            except Exception as e:
                print(f"[trader_monitor_daemon] user={user_id} tick failed: {e}", flush=True)
                traceback.print_exc()
        n += 1
        time.sleep(TICK_INTERVAL_S)


if __name__ == "__main__":
    main()
