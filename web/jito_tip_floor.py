"""
jito_tip_floor — query Jito's live tip percentile distribution.

Jito publishes the rolling tip distribution of LANDED bundles at:

    https://bundles.jito.wtf/api/v1/bundles/tip_floor

We use this to set tip strategy with real data instead of guessing.
Without it, our prior attempts had tips ranging from 10k to 2M
lamports with no signal — some way too low (auction rejected), some
way too high (waste of SOL). Now: pick a target percentile, get the
live floor, add a small headroom, ship.

The percentile semantics: "75th percentile landed tip" = 75% of
bundles that LANDED in the last sample window paid less than this
tip. Setting tip = 75th percentile means we land 75%+ of the time
against bundles competing for the same slot.

For our TG-triggered trades (not first-mover snipes), 95th percentile
+ 20% headroom is reliable and cheap (~140k lamports right now).
For competitive pump.fun snipes, we'd need 99th+ — that's a different
strategy than this module exposes.

The result is cached for `cache_ttl_s` seconds (default 15) so we
don't hammer Jito's endpoint on every trade.
"""

from __future__ import annotations

import json
import time
import urllib.request
from typing import Optional


TIP_FLOOR_URL = "https://bundles.jito.wtf/api/v1/bundles/tip_floor"

# Mapping of friendly percentile names to the API's JSON field names.
PERCENTILE_FIELDS = {
    "p25": "landed_tips_25th_percentile",
    "p50": "landed_tips_50th_percentile",
    "p75": "landed_tips_75th_percentile",
    "p95": "landed_tips_95th_percentile",
    "p99": "landed_tips_99th_percentile",
    "ema50": "ema_landed_tips_50th_percentile",
}

# Hard floor — Jito won't accept tips below 1000 lamports per their docs.
# If a percentile reads below this (very quiet period), use the floor.
MIN_TIP_LAMPORTS = 1_000

# Hard ceiling — sanity check. If we somehow compute > 50M lamports
# (0.05 SOL), refuse rather than burn money. Operator can override
# explicitly via tip_lamports in the call site.
MAX_TIP_LAMPORTS = 50_000_000


class TipFloorError(RuntimeError):
    """Raised when we can't fetch a valid tip floor and have no cache."""


# Module-level cache.
_cache: dict = {"ts": 0.0, "data": None}


def _fetch_live(timeout_s: float = 3.0) -> dict:
    """Hit Jito's tip_floor endpoint, return the parsed first row.
    Endpoint returns a JSON array with one element."""
    req = urllib.request.Request(TIP_FLOOR_URL, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        body = json.loads(r.read())
    if not isinstance(body, list) or not body:
        raise TipFloorError(f"unexpected tip_floor shape: {body!r}")
    return body[0]


def get_tip_lamports(
    *,
    percentile: str = "p95",
    headroom_pct: float = 0.20,
    cache_ttl_s: float = 15.0,
    min_lamports: int = MIN_TIP_LAMPORTS,
    max_lamports: int = MAX_TIP_LAMPORTS,
) -> int:
    """Get a live tip recommendation in lamports.

    Args:
      percentile: "p25"/"p50"/"p75"/"p95"/"p99"/"ema50". Default "p95"
                  → land against 95% of currently-landing competition.
      headroom_pct: extra fraction added on top (0.20 = +20%). Default
                    0.20 — small buffer so we land ABOVE the percentile,
                    not exactly at it.
      cache_ttl_s: serve cached value if last fetch is younger than this.
      min_lamports: floor — Jito won't accept below 1000 lamports anyway.
      max_lamports: ceiling — guard against runaway tips.

    Returns:
      Lamport amount to use as `jito_tip_lamports`.

    Raises:
      TipFloorError if the API call fails AND there's no usable cache.
    """
    if percentile not in PERCENTILE_FIELDS:
        raise ValueError(
            f"percentile must be one of {sorted(PERCENTILE_FIELDS)}, got {percentile!r}"
        )

    now = time.time()
    data = _cache["data"]
    if data is None or (now - _cache["ts"]) > cache_ttl_s:
        try:
            data = _fetch_live()
            _cache["data"] = data
            _cache["ts"] = now
        except Exception as e:
            if data is None:
                raise TipFloorError(f"tip_floor fetch failed: {e}") from e
            # Stale cache is better than failing — log and reuse.

    field = PERCENTILE_FIELDS[percentile]
    raw_sol = data.get(field)
    if raw_sol is None or raw_sol <= 0:
        raise TipFloorError(f"missing/zero field {field} in {data!r}")

    # API returns tip in SOL (e.g. 1.4e-4 SOL = 140,000 lamports).
    lamports = int(raw_sol * 1e9 * (1.0 + headroom_pct))
    lamports = max(min_lamports, min(max_lamports, lamports))
    return lamports


def get_snapshot() -> dict:
    """Return the full last-fetched tip floor row (all percentiles +
    the timestamp from Jito). Useful for debug + audit. Fetches fresh
    if nothing is cached."""
    if _cache["data"] is None:
        try:
            _cache["data"] = _fetch_live()
            _cache["ts"] = time.time()
        except Exception as e:
            raise TipFloorError(f"tip_floor fetch failed: {e}") from e
    return dict(_cache["data"])


# ── CLI ─────────────────────────────────────────────────────────────────

def _cli():
    import argparse, sys
    p = argparse.ArgumentParser(description="Print Jito tip floor recommendations")
    p.add_argument("--percentile", default="p95")
    p.add_argument("--headroom", type=float, default=0.20)
    p.add_argument("--snapshot", action="store_true",
                   help="Print the full tip-floor snapshot instead of one value")
    args = p.parse_args()

    if args.snapshot:
        snap = get_snapshot()
        print(json.dumps(snap, indent=2))
        print()
        print("Lamport equivalents:")
        for k, field in PERCENTILE_FIELDS.items():
            sol = snap.get(field) or 0
            print(f"  {k:<6} = {int(sol * 1e9):>9,d} lamports  ({sol:.7f} SOL)")
        return

    tip = get_tip_lamports(percentile=args.percentile, headroom_pct=args.headroom)
    print(f"{tip}  ({tip / 1e9:.6f} SOL, {args.percentile} + {int(args.headroom*100)}%)")


if __name__ == "__main__":
    _cli()
