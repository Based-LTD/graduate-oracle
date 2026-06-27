"""
$GRADUATE public API · v1

Versioned, authenticated endpoints for paying users. Existing un-authed dashboard
endpoints (/api/live, /api/wallets) remain in main.py for the local UI.

All v1 endpoints require an API key via Authorization: Bearer <key> or X-API-Key.
Daily call quotas enforced per tier (see db.TIERS).

Get a free key: POST /api/keys/new {"email":"you@example.com"}
"""
import json
import sqlite3
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path as PPath, Query, Request, Response
from fastapi.responses import JSONResponse

import db
import webhooks as webhooks_mod
from auth import require_api_key, require_tier, attach_rate_limit_headers, composite_signal_dep

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_PATH = ROOT / "observer-active.json"
CURVES_DIR = ROOT / "observer-curves"

router = APIRouter(prefix="/api/v1", tags=["v1"])


def _read_snapshot():
    # Delegate to main.py's mtime-cached reader (10× perf win per the comment
    # there). The local version that lived here was un-cached and re-parsed
    # 420 KB JSON on every paying-API request — a big chunk of the 4-5s
    # latency gap between /api/live and /api/v1/live.
    from main import _read_snapshot as _read_snapshot_cached  # noqa: PLC0415
    return _read_snapshot_cached()


# ── /api/v1/live — current scored mints ─────────────────────────────────────
@router.get(
    "/live",
    summary="Live graduation probabilities",
    description="Returns currently-tracked non-mayhem pump.fun mints with k-NN graduation probability scores. Updates every ~5 seconds.",
)
def live(
    request: Request,
    limit: int = Query(60, ge=1, le=200),
    min_prob: float = Query(0.0, ge=0.0, le=1.0, description="Filter to mints with at least this graduation probability."),
    _key=Depends(require_api_key),
):
    # FAST PATH — pull scored mints from the precompute cache (refreshed
    # every ~2s by the background daemon). Skips snapshot disk read and
    # JSON parse entirely on the hot path. /api/v1/live was previously
    # reading the snapshot from disk on every request and then ignoring
    # it, which added 3-5s of pure waste vs the dashboard endpoint.
    from main import _score_cache, _score_cache_lock, _start_precompute_thread, _score_mints_cached  # noqa: PLC0415
    _start_precompute_thread()
    served_at_ms = int(time.time() * 1000)
    with _score_cache_lock:
        cached_result   = _score_cache.get("result")
        cached_epoch_ms = _score_cache.get("epoch_ms")
        cached_n_tracked = _score_cache.get("n_tracked", 0)
    if cached_result is None:
        # Cold start — fall back to slow path so first request after boot
        # doesn't 503. Subsequent requests use the cache.
        snap = _read_snapshot()
        if not snap:
            raise HTTPException(503, detail={"error": "no_snapshot", "hint": "observer-daemon not running yet"})
        cached_result = _score_mints_cached(snap)
        cached_epoch_ms = snap.get("snapshot_epoch_ms", 0)
        cached_n_tracked = snap.get("n_tracked", 0)
    mints = cached_result
    if min_prob > 0:
        mints = [m for m in mints if (m.get("grad_prob") or 0) >= min_prob]
    mints = mints[:limit]
    snap_age_s = max(0, int((served_at_ms - (cached_epoch_ms or 0)) / 1000))
    payload = {
        "snapshot_age_s":   snap_age_s,
        "data_freshness_seconds": snap_age_s,
        "served_at_unix_ms": served_at_ms,
        "n_tracked_total":  cached_n_tracked,
        "count":            len(mints),
        "mints":            mints,
    }
    # Skip FastAPI's pydantic/jsonable_encoder step — pre-serialize ourselves
    # for the hot path. ~30-50ms savings on the 219KB payload size.
    return Response(
        content=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        media_type="application/json",
    )


# ── /api/v1/probe/{mint} — single-mint score ────────────────────────────────
@router.get(
    "/probe/{mint}",
    summary="Score a single mint",
    description=(
        "Returns the same enriched mint payload as /api/v1/live (grad probability, "
        "k-NN context, all bot/quality/activity signals, top buyers list). Bypasses "
        "the dashboard's auto-hide filter — even mints we'd hide from the dashboard "
        "are returned, with a `hide_reason` field set so callers can decide for themselves."
    ),
)
def probe(
    request: Request,
    mint: str = PPath(..., description="Full Solana mint address"),
    _key=Depends(require_api_key),
):
    from main import _enrich_mint  # late import to avoid circular
    served_at_ms = int(time.time() * 1000)
    snap = _read_snapshot()
    if not snap:
        raise HTTPException(503, detail={"error": "no_snapshot"})
    target = next((m for m in snap.get("mints", []) if m.get("mint") == mint), None)
    if not target:
        return {
            "mint": mint,
            "found_in_live": False,
            "served_at_unix_ms": served_at_ms,
            "hint": "mint not currently tracked in the active observer set",
        }
    enriched, hide_reason = _enrich_mint(target)
    enriched["found_in_live"] = True
    enriched["hide_reason"] = hide_reason
    enriched["served_at_unix_ms"] = served_at_ms
    snap_epoch_ms = snap.get("snapshot_epoch_ms", 0)
    enriched["data_freshness_seconds"] = max(0, int((served_at_ms - snap_epoch_ms) / 1000)) if snap_epoch_ms else None
    return enriched


# ── /api/v1/smart_money_active ──────────────────────────────────────────────
@router.get(
    "/smart_money_active",
    summary="Live mints with smart-money wallets currently positioned",
    description=(
        "Filtered view of /live: only mints where one or more leaderboard wallets "
        "(min_total≥8, smart_score≥0.30) are *currently* in the top buyers. "
        "Sorted by smart-money count (most wallets first), tie-broken by graduation "
        "probability. Useful for tail-trading proven wallets in real time."
    ),
)
def smart_money_active(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    min_smart: int = Query(1, ge=1, le=20,
                           description="Minimum smart-money wallet count for a mint to surface."),
    _tier=Depends(require_tier("builder")),
):
    from main import _score_mints_cached
    snap = _read_snapshot()
    if not snap:
        raise HTTPException(503, detail={"error": "no_snapshot"})
    mints = _score_mints_cached(snap)
    mints = [m for m in mints if (m.get("smart_money_in") or 0) >= min_smart]
    mints.sort(key=lambda m: (-(m.get("smart_money_in") or 0),
                              -(m.get("grad_prob") or 0)))
    return {
        "count": len(mints[:limit]),
        "min_smart_money_in": min_smart,
        "mints": mints[:limit],
    }


# ── /api/v1/runners — live mints with high from-now upside ──────────────────
@router.get(
    "/runners",
    summary="Live mints with high runner odds from current price",
    description=(
        "Filtered view of /live ranked by `runner_prob_5x_from_now` — i.e. probability "
        "the mint reaches ≥5× ITS CURRENT PRICE (not its launch price). This is the "
        "trader-relevant sort: highest expected upside if you buy at the price you see."
    ),
)
def runners(
    request: Request,
    limit: int = Query(30, ge=1, le=100),
    tier: str = Query("5x", regex="^(2x|3x|5x|10x|20x)$",
                      description="Which runner tier to rank by (from-now semantics)."),
    min_prob: float = Query(0.05, ge=0.0, le=1.0,
                            description="Minimum from-now probability for the chosen tier."),
    _tier=Depends(require_tier("builder")),
):
    from main import _score_mints_cached
    snap = _read_snapshot()
    if not snap:
        raise HTTPException(503, detail={"error": "no_snapshot"})
    mints = _score_mints_cached(snap)
    key = f"runner_prob_{tier}_from_now"
    filtered = [m for m in mints if (m.get(key) or 0) >= min_prob]
    filtered.sort(key=lambda m: -(m.get(key) or 0))
    return {
        "tier":              tier,
        "ranking_field":     key,
        "min_prob":          min_prob,
        "count":             len(filtered[:limit]),
        "mints":             filtered[:limit],
    }


# ── /api/v1/signals — machine-consumable cross event stream ─────────────────
@router.get(
    "/signals",
    summary="Composite cross signal stream (cursor-paginated)",
    description=(
        "The machine-consumable signal feed. Returns composite-receipts crossings "
        "(ACT / WATCH tier) in the order they became deliverable, newest-cursor last. "
        "Poll with the `cursor` from the previous response as `since` to get only "
        "new signals — zero missed crossings, zero duplicates across polls.\n\n"
        "Each signal is the same event delivered to the Telegram bot, with full "
        "feature payload. `tier` is ACT (gp_60 ≥ 0.25, ~75% historical grad rate) "
        "or WATCH (gp_60 in [0.10, 0.25), ~28%). Cursor is an opaque integer "
        "(internally the deliverability timestamp) — treat it as opaque, store it, "
        "pass it back. Recommended poll interval: 1-3s for execution use.\n\n"
        "Wallet-redaction safe: aggregate counts + scalars only, no wallet addresses."
    ),
)
def signals(
    request: Request,
    since: int = Query(0, ge=0,
                       description="Cursor from prior response. 0 (default) returns the last hour."),
    limit: int = Query(100, ge=1, le=500),
    tier: str = Query("all", regex="^(ACT|WATCH|SCOUT|all)$",
                      description="Filter by tier. 'all' = ACT + WATCH + SCOUT."),
    _key=Depends(composite_signal_dep()),
):
    now = int(time.time())
    # since=0 → bootstrap to last hour so a fresh consumer doesn't replay
    # the whole table. After that they pass back the returned cursor.
    floor_ts = since if since > 0 else (now - 3600)
    tiers = ("ACT", "WATCH", "SCOUT") if tier == "all" else (tier,)
    placeholders = ",".join("?" for _ in tiers)
    try:
        with sqlite3.connect(db.DB_PATH, timeout=5) as c:
            c.row_factory = sqlite3.Row
            rows = c.execute(f"""
                SELECT mint, predicted_at, composite_score, threshold_at_cross,
                       smart_money_in, max_mult_at_cross, age_s_at_cross,
                       mc_at_cross_usd, tg_tier, tg_pushed_at
                  FROM composite_predictions
                 WHERE tg_pushed_at IS NOT NULL
                   AND tg_pushed_at > ?
                   AND tg_tier IN ({placeholders})
              ORDER BY tg_pushed_at ASC, mint ASC
                 LIMIT ?
            """, (floor_ts, *tiers, limit)).fetchall()
            rows = [dict(r) for r in rows]
            # Tie-safe boundary: if the page is full and the next row shares
            # the last row's tg_pushed_at, pull the rest of that timestamp so
            # a same-second tie can't be split across pages (zero-miss
            # guarantee). Crossings are ~10-30/hour so this is bounded tiny.
            if len(rows) == limit:
                boundary = rows[-1]["tg_pushed_at"]
                extra = c.execute(f"""
                    SELECT mint, predicted_at, composite_score, threshold_at_cross,
                           smart_money_in, max_mult_at_cross, age_s_at_cross,
                           mc_at_cross_usd, tg_tier, tg_pushed_at
                      FROM composite_predictions
                     WHERE tg_pushed_at = ?
                       AND tg_tier IN ({placeholders})
                       AND mint > ?
                  ORDER BY mint ASC
                """, (boundary, *tiers, rows[-1]["mint"])).fetchall()
                rows.extend(dict(r) for r in extra)
    except Exception as e:
        raise HTTPException(503, detail={"error": "signal_query_failed", "detail": str(e)})

    signals_out = []
    for r in rows:
        thr = r["threshold_at_cross"] or 0
        signals_out.append({
            "mint":               r["mint"],
            "tier":               r["tg_tier"],
            "composite_score":    r["composite_score"],
            "threshold_at_cross": thr,
            "score_ratio":        (r["composite_score"] / thr) if thr else None,
            "smart_money_in":     r["smart_money_in"],
            "max_mult_at_cross":  r["max_mult_at_cross"],
            "age_s_at_cross":     r["age_s_at_cross"],
            "mc_at_cross_usd":    r["mc_at_cross_usd"],
            "cross_at":           r["predicted_at"],
            "delivered_at":       r["tg_pushed_at"],
        })
    next_cursor = rows[-1]["tg_pushed_at"] if rows else floor_ts
    return {
        "count":       len(signals_out),
        "cursor":      next_cursor,
        "tier_filter": tier,
        "signals":     signals_out,
        "_doc":        "Pass `cursor` back as `since` on the next poll. Dedupe by mint as a belt-and-suspenders.",
    }


# ── /api/v1/wallet/{address} — single wallet stats ──────────────────────────
@router.get(
    "/wallet/{address}",
    summary="Wallet pump.fun history",
    description="Returns lifetime stats for a wallet (using observed first-12-char prefix) including graduation/runner/rug counts and smart score.",
)
def wallet(
    request: Request,
    address: str = PPath(..., description="Full wallet address (we match by 12-char prefix)"),
    _key=Depends(require_api_key),
):
    import wallet_intel
    if wallet_intel.INDEX is None:
        raise HTTPException(503, detail={"error": "wallet_index_warming_up"})
    wallet_intel.INDEX.maybe_refresh()
    short = address[:12]
    rec = wallet_intel.INDEX._wallets.get(short)
    if not rec:
        return {"wallet": address, "found": False, "hint": "no observed buys for this prefix in our dataset"}
    return {
        "wallet": address,
        "found": True,
        "stats": {**rec, "wallet": short},
    }


# ── /api/v1/wallet/link — Phantom signMessage wallet ownership proof ────────
@router.post(
    "/wallet/link",
    summary="Link a Solana wallet to your API key (proves ownership via Phantom signMessage)",
    description=(
        "Proves you own a Solana wallet so we can credit its $ORACLE holdings "
        "against your tier (post-launch). The client signs the EXACT message "
        "`graduate-oracle :: link wallet to api_key_prefix=<KEY_PREFIX> :: <WALLET>` "
        "with Phantom's signMessage, then submits {wallet, signature_b58, key_prefix}. "
        "We verify the ed25519 signature against the wallet's public key, then "
        "persist the wallet on your api_key. Idempotent; re-call to relink."
    ),
)
def wallet_link(
    request: Request,
    payload: dict = Body(..., example={
        "wallet": "<your-wallet>",
        "signature_b58": "<base58 signature>",
        "key_prefix": "grad_…",
    }),
    _key=Depends(require_api_key),
):
    import token_utility
    key_rec = request.state.api_key
    wallet = (payload or {}).get("wallet", "").strip()
    sig_b58 = (payload or {}).get("signature_b58", "").strip()
    key_prefix = (payload or {}).get("key_prefix", "").strip()
    if not wallet or not sig_b58 or not key_prefix:
        raise HTTPException(400, detail={"error": "missing_fields",
                                          "required": ["wallet", "signature_b58", "key_prefix"]})
    if key_prefix != key_rec.get("key_prefix"):
        raise HTTPException(400, detail={"error": "key_prefix_mismatch",
                                          "hint": "the signed message must match your own api_key prefix"})
    msg = f"graduate-oracle :: link wallet to api_key_prefix={key_prefix} :: {wallet}".encode("utf-8")
    if not token_utility.verify_phantom_signature(wallet, msg, sig_b58):
        raise HTTPException(400, detail={"error": "signature_invalid",
                                          "hint": "ed25519 verification failed"})
    try:
        with sqlite3.connect(db.DB_PATH, timeout=5) as c:
            c.execute("UPDATE api_keys SET wallet=? WHERE id=?",
                      (wallet, key_rec["id"]))
            c.commit()
    except Exception as e:
        raise HTTPException(503, detail={"error": "link_save_failed", "detail": str(e)})
    return {
        "linked": True,
        "wallet": wallet,
        "_doc": ("Wallet linked. The background loop will read its $ORACLE "
                  "balance and update token_held_tier on the next refresh "
                  "(~10 min). DORMANT until ORACLE_MINT is set at launch."),
    }


# ── /api/v1/wallets/leaderboard ─────────────────────────────────────────────
@router.get(
    "/wallets/leaderboard",
    summary="Smart money / sniper leaderboard",
    description="Top wallets ranked by smart_score (graduation rate − rug rate, sample-size weighted) or sniper-rug rate.",
)
def leaderboard(
    request: Request,
    kind: str = Query("smart", regex="^(smart|sniper|graduate)$"),
    limit: int = Query(50, ge=1, le=200),
    min_total: int = Query(8, ge=1),
    _tier=Depends(require_tier("pro")),
):
    import wallet_intel
    if wallet_intel.INDEX is None:
        raise HTTPException(503, detail={"error": "wallet_index_warming_up"})
    wallet_intel.INDEX.maybe_refresh()
    return {
        "kind": kind,
        "n_wallets_indexed": wallet_intel.INDEX.n_wallets,
        "n_curves_indexed": wallet_intel.INDEX.n_curves,
        "wallets": wallet_intel.INDEX.leaderboard(kind=kind, min_total=min_total, limit=limit),
    }


# ── /api/v1/webhooks — pro-tier event push ──────────────────────────────────
@router.post(
    "/webhooks",
    summary="Register a webhook subscription (Pro tier)",
    description=(
        "Register a URL we'll POST signed event payloads to. Each delivery includes "
        "an `X-Graduate-Signature` header containing HMAC-SHA256(secret, body). "
        "The `secret` field is returned ONCE on creation — store it; we don't expose "
        "it on subsequent reads. Use `events=\"*\"` to subscribe to every event kind, "
        "or a comma-separated list (e.g. `runner_dev,smart_money_in`)."
    ),
)
def webhooks_register(
    request: Request,
    payload: dict = Body(..., example={"url": "https://your.app/hook", "events": "*"}),
    _tier=Depends(require_tier("pro")),
):
    url = (payload or {}).get("url", "").strip()
    events = (payload or {}).get("events", "*").strip() or "*"
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, detail={"error": "invalid_url", "hint": "url must be http(s)://..."})
    rec = webhooks_mod.register_webhook(
        api_key_id=request.state.api_key["id"],
        url=url,
        events=events,
    )
    return rec


@router.get(
    "/webhooks",
    summary="List your webhook subscriptions",
)
def webhooks_list(request: Request, _tier=Depends(require_tier("pro"))):
    rows = webhooks_mod.list_webhooks(api_key_id=request.state.api_key["id"])
    return {"count": len(rows), "webhooks": rows}


@router.delete(
    "/webhooks/{webhook_id}",
    summary="Deactivate a webhook subscription",
)
def webhooks_delete(
    request: Request,
    webhook_id: int = PPath(..., ge=1),
    _tier=Depends(require_tier("pro")),
):
    ok = webhooks_mod.delete_webhook(
        api_key_id=request.state.api_key["id"],
        webhook_id=webhook_id,
    )
    if not ok:
        raise HTTPException(404, detail={"error": "webhook_not_found"})
    return {"deleted": True, "id": webhook_id}


@router.get(
    "/webhooks/deliveries",
    summary="Recent delivery attempts (debugging)",
    description="Last N delivery attempts across all your webhooks, including status code and any error.",
)
def webhooks_deliveries(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    _tier=Depends(require_tier("pro")),
):
    rows = webhooks_mod.list_deliveries(api_key_id=request.state.api_key["id"], limit=limit)
    return {"count": len(rows), "deliveries": rows}


# ── /api/v1/alert/{mint} — PUBLIC receipt endpoint (no auth) ────────────────
# A single-mint public proof endpoint. Anyone can verify what the bot
# alerted on for a given mint, when, and the signal features at cross
# moment. Designed as a tweetable receipt link for the "X% call" narrative.
@router.get(
    "/alert/{mint}",
    summary="Public proof — signal record for a single mint",
    description=(
        "Returns the timestamped composite-signal record for a given mint. "
        "PUBLIC (no auth) — designed as a verifiable receipt URL for "
        "post-hoc claims. Includes alert tier, composite score, smart money "
        "count, market cap at cross, and (if resolved) the actual outcome."
    ),
)
def alert_proof(mint: str):
    import composite_predictions as _cp
    with sqlite3.connect(db.DB_PATH, timeout=5) as c:
        _cp._ensure_schema(c)
        c.row_factory = sqlite3.Row
        r = c.execute("""
            SELECT mint, predicted_at, tg_pushed_at, tg_tier,
                   composite_score, threshold_at_cross,
                   smart_money_in, age_s_at_cross,
                   mc_at_cross_usd, max_mult_at_cross,
                   outcome_resolved_at, did_graduate,
                   peak_mult_24h, did_sustain_30m
              FROM composite_predictions
             WHERE mint = ?
        """, (mint,)).fetchone()
    if not r:
        return JSONResponse(
            {"mint": mint, "found": False,
             "message": "No composite-signal record for this mint."},
            status_code=404,
        )
    d = dict(r)
    score_ratio = (d["composite_score"] / d["threshold_at_cross"]
                   if d["threshold_at_cross"] else None)
    peak_from_entry = (d["peak_mult_24h"] / d["max_mult_at_cross"]
                       if d["peak_mult_24h"] and d["max_mult_at_cross"] else None)
    return {
        "mint":                  d["mint"],
        "found":                 True,
        "predicted_at_unix":     d["predicted_at"],
        "predicted_at_iso":      _iso(d["predicted_at"]),
        "tg_pushed_at_unix":     d["tg_pushed_at"],
        "tg_pushed_at_iso":      _iso(d["tg_pushed_at"]),
        "tier":                  d["tg_tier"],
        "composite_score":       d["composite_score"],
        "threshold_at_cross":    d["threshold_at_cross"],
        "score_ratio":           score_ratio,
        "smart_money_in":        d["smart_money_in"],
        "age_s_at_cross":        d["age_s_at_cross"],
        "mc_at_cross_usd":       d["mc_at_cross_usd"],
        "max_mult_at_cross":     d["max_mult_at_cross"],
        "outcome": {
            "resolved":            d["outcome_resolved_at"] is not None,
            "resolved_at_unix":    d["outcome_resolved_at"],
            "did_graduate":        d["did_graduate"],
            "peak_mult_24h":       d["peak_mult_24h"],
            "peak_mult_from_entry": peak_from_entry,
            "did_sustain_30m":     d["did_sustain_30m"],
        },
        "verify_on_chain": {
            "dexscreener": f"https://dexscreener.com/solana/{d['mint']}",
            "pump_fun":    f"https://pump.fun/coin/{d['mint']}",
            "solscan":     f"https://solscan.io/token/{d['mint']}",
        },
    }


def _iso(unix_ts):
    if unix_ts is None:
        return None
    from datetime import datetime, timezone
    return datetime.fromtimestamp(int(unix_ts), tz=timezone.utc).isoformat()


# ── /api/v1/stats — platform-wide ───────────────────────────────────────────
# ── /api/v1/composite_predictions ───────────────────────────────────────────
# Tamper-evident audit log for the dashboard's hot-launch composite signal
# (smart_money_in × max_mult × freshness + MC floor). Per the composite-
# receipts-logging pre-reg + Audit 09 verdict establishing wallet index as
# moat. Paginated read-only; auth-gated like /api/v1/predictions. Wallet-
# redaction safe — rows carry only aggregate counts + scalars + outcome
# booleans; no wallet addresses.
@router.get(
    "/composite_predictions",
    summary="Composite-signal predictions log",
    description=(
        "Returns rows from the composite_predictions table — every time the "
        "hot-launch composite signal (smart_money_in × max_mult × freshness) "
        "crossed the rolling-24h P90 threshold AND market cap was above the "
        "$5,000 floor, dedupe-by-mint first-cross. Outcome columns "
        "(did_graduate, peak_mult_24h, did_sustain_30m) are NULL until 24h "
        "post-cross, then populated by the background resolver from existing "
        "outcome tables. Paired with /api/v1/composite_ledger for "
        "tamper-evident merkle root verification."
    ),
)
def composite_predictions_list(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    only_resolved: bool = Query(False, description="If true, return only crosses with outcomes resolved."),
    _key=Depends(composite_signal_dep()),
):
    import composite_predictions as _cp
    with sqlite3.connect(db.DB_PATH, timeout=5) as c:
        _cp._ensure_schema(c)
        c.row_factory = sqlite3.Row
        where = "WHERE outcome_resolved_at IS NOT NULL" if only_resolved else ""
        rows = c.execute(f"""
            SELECT mint, predicted_at, composite_score, threshold_at_cross,
                   smart_money_in, max_mult_at_cross, age_s_at_cross,
                   mc_at_cross_usd,
                   outcome_resolved_at, did_graduate, peak_mult_24h,
                   did_sustain_30m
              FROM composite_predictions
              {where}
             ORDER BY predicted_at DESC
             LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()
        total = c.execute(f"SELECT COUNT(*) FROM composite_predictions {where}").fetchone()[0]
    return {
        "count":   len(rows),
        "total":   total,
        "limit":   limit,
        "offset":  offset,
        "daemon":  _cp.snapshot(),
        "rows":    [dict(r) for r in rows],
    }


# ── /api/v1/composite_ledger ────────────────────────────────────────────────
@router.get(
    "/composite_ledger",
    summary="Composite predictions merkle commits",
    description=(
        "Returns hourly merkle root commits over composite_predictions rows. "
        "Same tamper-evident discipline as /api/ledger/commits (grad_prob "
        "track), applied to the composite track. Leaves use a composite-"
        "specific format starting at version 1."
    ),
)
def composite_ledger_commits(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    _key=Depends(require_api_key),
):
    import composite_predictions as _cp
    with sqlite3.connect(db.DB_PATH, timeout=5) as c:
        _cp._ensure_schema(c)
        c.row_factory = sqlite3.Row
        rows = c.execute("""
            SELECT id, period_start, period_end, merkle_root_hex, n_crosses,
                   first_pred_ts, last_pred_ts, computed_at, leaf_version
              FROM composite_prediction_commits
             ORDER BY period_start DESC
             LIMIT ?
        """, (limit,)).fetchall()
    return {
        "count":  len(rows),
        "commits": [dict(r) for r in rows],
    }


# ── /api/v1/_heap_audit — internal memory introspection ─────────────────────
# Surfaces top python object types by count + key in-process structure sizes.
# Auth-gated; emergency diagnostic only. Added 2026-05-12 for the second-leak
# diagnosis (web process crashing every ~1h post composite-receipts deploy).
@router.get("/_heap_audit", summary="Internal: heap audit", include_in_schema=False)
def _heap_audit(request: Request):
    # TEMPORARY: emergency-diagnosis endpoint, no auth (no sensitive data
    # surfaced — only aggregate counts + type names). Will be removed
    # after the second-leak diagnosis ships.
    import gc, sys, os
    from collections import Counter
    # Top object types by count
    type_counts = Counter()
    type_bytes = Counter()
    for obj in gc.get_objects():
        try:
            type_counts[type(obj).__name__] += 1
            type_bytes[type(obj).__name__] += sys.getsizeof(obj)
        except Exception:
            pass
    top_count = type_counts.most_common(25)
    top_bytes = sorted(type_bytes.items(), key=lambda x: -x[1])[:25]
    # Key module sizes
    sizes = {}
    try:
        import grad_prob
        if grad_prob.INDEX:
            sizes["grad_prob.INDEX.n_curves"] = getattr(grad_prob.INDEX, "n_curves", None)
    except Exception: pass
    try:
        import wallet_intel
        if wallet_intel.INDEX:
            sizes["wallet_intel.INDEX.n_wallets"] = getattr(wallet_intel.INDEX, "n_wallets", None)
            sizes["wallet_intel.INDEX.n_curves"]  = getattr(wallet_intel.INDEX, "n_curves", None)
    except Exception: pass
    try:
        import creator_history
        if creator_history.INDEX:
            sizes["creator_history.INDEX.n_creators"] = creator_history.INDEX.n_creators
            sizes["creator_history.INDEX._processed_size"] = len(creator_history.INDEX._processed)
            sizes["creator_history.INDEX.stats_size"]      = len(creator_history.INDEX.stats)
    except Exception: pass
    try:
        import composite_predictions
        snap = composite_predictions.snapshot()
        sizes["composite_predictions.n_samples"] = snap.get("n_samples")
    except Exception: pass
    try:
        import status_module
        sizes["status_module._LATENCY.len"] = len(getattr(status_module, "_LATENCY", []))
    except Exception: pass
    try:
        import predictions
        sizes["predictions._pred_queue.qsize"] = predictions._pred_queue.qsize()
    except Exception: pass
    try:
        import bucket_cutoffs
        sizes["bucket_cutoffs._state"] = bool(bucket_cutoffs.snapshot())
    except Exception: pass
    try:
        with open(f"/proc/{os.getpid()}/status") as f:
            for line in f:
                if line.startswith(("VmRSS", "VmPeak", "VmSize", "Threads")):
                    k, v = line.split(":", 1)
                    sizes[f"proc.{k}"] = v.strip()
    except Exception: pass
    # Numpy/sklearn allocations are NOT tracked by gc.get_objects(); manually
    # scan known module-level numpy arrays (rug_predictor cache, gbm_shadow
    # model state, etc.) to attribute the unaccounted heap.
    try:
        import numpy as np
        np_total_mb = 0.0
        np_count = 0
        for obj in gc.get_objects():
            if isinstance(obj, np.ndarray):
                np_total_mb += obj.nbytes / 1024 / 1024
                np_count += 1
        sizes["numpy_arrays.count"] = np_count
        sizes["numpy_arrays.total_mb"] = round(np_total_mb, 1)
    except Exception as e:
        sizes["numpy_arrays.err"] = str(e)
    # rug_predictor cache specifically
    try:
        import rug_predictor
        pc = rug_predictor._predict_cache
        if pc.get("rows_normalized") is not None:
            sizes["rug_predictor.rows_normalized_mb"] = round(pc["rows_normalized"].nbytes / 1024 / 1024, 2)
            sizes["rug_predictor.rows_normalized_shape"] = list(pc["rows_normalized"].shape)
    except Exception as e:
        sizes["rug_predictor.err"] = str(e)
    return {
        "top_by_count": [{"type": t, "count": c} for t, c in top_count],
        "top_by_bytes_mb": [{"type": t, "bytes_mb": b / 1024 / 1024} for t, b in top_bytes],
        "structure_sizes": sizes,
        "gc_counts": gc.get_count(),
        "gc_stats": gc.get_stats(),
    }


@router.get(
    "/stats",
    summary="Platform-wide aggregate stats",
)
def stats(request: Request, _key=Depends(require_api_key)):
    import wallet_intel
    snap = _read_snapshot()
    return {
        "live_mints_tracked": (snap or {}).get("n_tracked", 0),
        "historical_curves_indexed": wallet_intel.INDEX.n_curves if wallet_intel.INDEX else 0,
        "wallets_indexed": wallet_intel.INDEX.n_wallets if wallet_intel.INDEX else 0,
        "tiers_available": list(db.TIERS.keys()),
        "your_tier": request.state.api_key["tier"],
        "your_quota": request.state.quota_info,
    }
