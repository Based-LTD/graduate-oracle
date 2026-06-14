"""CLI wallet-link flow — browser handoff signature pattern.

Builders who want token-holder access via the CLI run:

    npx goracle signup --wallet 7xKp...

The CLI:
  1. POSTs /api/signup/wallet/init -> server stores a pending link
     row keyed by `link_id`, returns the browser URL + the exact message
     to be signed.
  2. Opens the browser to /cli-link/<link_id> — that page renders a
     Phantom "Sign Message" button, signs the message, and POSTs the
     signature to /api/signup/wallet/sign.
  3. Polls /api/signup/wallet/<link_id> every 2s. Once the browser
     completes, the poll returns the API key + tier.

The server-side verification flow:
  - Verify the signature is valid for the wallet's pubkey + the message
  - Read $GO on-chain balance via token_utility.get_oracle_balance()
  - If balance >= threshold for requested tier → mint a fresh API key
    with wallet bound, token_held_tier set to the resolved tier
  - Mark the link fulfilled with the key prefix (NOT the raw key — that
    only goes back via the poll response, once)

DORMANT state: if ORACLE_MINT is the launch placeholder, init returns a
helpful "token launches at <Proof Launch URL>" message — the flow does
not block, it just informs the CLI which prints the same message.
"""
import contextlib
import secrets
import sqlite3
import threading
import time
from typing import Optional

import db
import token_utility


LINK_TTL_S = 30 * 60   # link expires 30 min after creation
RAW_KEY_TTL_S = 5 * 60 # plaintext key is poll-visible for 5 min after fulfillment

_initialized = False
_init_lock = threading.Lock()


def migrate_schema() -> None:
    """Idempotent — adds the pending_wallet_links table."""
    global _initialized
    with _init_lock:
        if _initialized: return
        with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=10)) as c, c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS pending_wallet_links (
                    link_id            TEXT PRIMARY KEY,
                    wallet             TEXT NOT NULL,
                    tier               TEXT NOT NULL,    -- requested tier (tg_paid|builder)
                    message            TEXT NOT NULL,    -- exact message bytes (for verify)
                    signature_b58      TEXT,             -- set when browser posts
                    status             TEXT NOT NULL,    -- pending|fulfilled|expired|insufficient_balance|invalid_signature
                    new_key            TEXT,             -- plaintext, only readable until raw_key_expires_at
                    new_key_prefix     TEXT,
                    new_key_id         INTEGER,
                    resolved_tier      TEXT,             -- actual tier granted (could be lower than requested)
                    balance_atomic     INTEGER,
                    created_at         INTEGER NOT NULL,
                    fulfilled_at       INTEGER,
                    raw_key_expires_at INTEGER
                )
            """)
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_pending_links_status_created "
                "ON pending_wallet_links(status, created_at)"
            )
        _initialized = True


# ── Init: CLI calls this first ──────────────────────────────────────────
def create_link(wallet: str, tier: str) -> dict:
    """Stores a pending link, returns {link_id, message, browser_url,
    expires_at, dormant?}. Does NOT touch the chain."""
    migrate_schema()
    if not wallet or len(wallet) < 32:
        raise ValueError("wallet must be a 32-44 char base58 Solana address")
    if tier not in ("tg_paid", "builder"):
        raise ValueError(f"tier must be tg_paid or builder (got {tier!r})")

    link_id = "lnk_" + secrets.token_urlsafe(16)
    nonce   = secrets.token_urlsafe(12)
    now     = int(time.time())

    # Exact message Phantom will show. Multi-line, plain ASCII, no
    # special chars that some wallets might strip. Wallet + nonce make
    # it cross-replay-safe.
    message = (
        f"graduate-oracle.fun · CLI signup\n"
        f"wallet: {wallet}\n"
        f"tier:   {tier}\n"
        f"link:   {link_id}\n"
        f"nonce:  {nonce}"
    )

    with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=10)) as c, c:
        c.execute(
            "INSERT INTO pending_wallet_links (link_id, wallet, tier, message, "
            "status, created_at) VALUES (?, ?, ?, ?, 'pending', ?)",
            (link_id, wallet, tier, message, now),
        )

    dormant = (token_utility.ORACLE_MINT == "<TBD_AT_LAUNCH>")
    return {
        "link_id":     link_id,
        "message":     message,
        "browser_url": f"https://graduateoracle.fun/cli-link/{link_id}",
        "expires_at":  now + LINK_TTL_S,
        "dormant":     dormant,
        "dormant_note": (
            "$GO token launches Monday on Proof Launch. Pre-launch "
            "balances all read as 0; the flow will reject any wallet "
            "until the mint is live."
        ) if dormant else None,
    }


# ── Sign: browser posts signature here ───────────────────────────────────
def submit_signature(link_id: str, signature_b58: str) -> dict:
    """Browser POSTs signature; server verifies, checks balance, mints
    key if eligible. Returns {status, ...}."""
    migrate_schema()
    now = int(time.time())
    with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=10)) as c, c:
        c.row_factory = sqlite3.Row
        row = c.execute(
            "SELECT * FROM pending_wallet_links WHERE link_id = ?", (link_id,),
        ).fetchone()
        if not row:
            return {"status": "not_found"}
        if row["status"] != "pending":
            return {"status": row["status"], "note": "link already resolved"}
        if (now - row["created_at"]) > LINK_TTL_S:
            c.execute(
                "UPDATE pending_wallet_links SET status='expired' WHERE link_id=?",
                (link_id,),
            )
            return {"status": "expired"}

        # 1. Verify signature
        ok = token_utility.verify_phantom_signature(
            row["wallet"], row["message"].encode("utf-8"), signature_b58,
        )
        if not ok:
            c.execute(
                "UPDATE pending_wallet_links SET status='invalid_signature', "
                "signature_b58=? WHERE link_id=?",
                (signature_b58, link_id),
            )
            return {"status": "invalid_signature"}

        # 2. Check $GO balance + resolve tier
        balance = token_utility.get_oracle_balance(row["wallet"])
        token_tier = token_utility.tier_from_balance(balance)
        # Did they reach (at least) the tier they requested?
        rank = token_utility._TIER_ORDER
        try:
            req_idx = rank.index(row["tier"])
            got_idx = rank.index(token_tier)
        except ValueError:
            req_idx, got_idx = 0, 0
        if got_idx < req_idx:
            c.execute(
                "UPDATE pending_wallet_links SET status='insufficient_balance', "
                "signature_b58=?, balance_atomic=? WHERE link_id=?",
                (signature_b58, balance, link_id),
            )
            return {
                "status":          "insufficient_balance",
                "balance_atomic":  balance,
                "balance_ui":      balance / (10 ** token_utility.ORACLE_DECIMALS) if balance else 0,
                "requested_tier":  row["tier"],
                "threshold_ui":    token_utility.HOLDING_TIERS_UI.get(row["tier"]),
            }

        # 3. Mint key bound to the wallet
        new_key = db.create_key(
            tier="free",  # starts free; effective_tier() promotes via token_held_tier
            wallet=row["wallet"],
            label="goracle-cli-wallet",
            expires_in_days=365,
        )
        # Persist the token_held_tier immediately so the user doesn't have
        # to wait for the next background refresh tick.
        c.execute(
            "UPDATE api_keys SET token_held_tier=?, token_held_amount=?, "
            "token_held_refreshed_at=? WHERE id=?",
            (token_tier, balance, now, new_key["id"]),
        )
        c.execute(
            "UPDATE pending_wallet_links SET status='fulfilled', "
            "signature_b58=?, balance_atomic=?, new_key=?, new_key_prefix=?, "
            "new_key_id=?, resolved_tier=?, fulfilled_at=?, "
            "raw_key_expires_at=? WHERE link_id=?",
            (
                signature_b58, balance, new_key["key"], new_key["prefix"],
                new_key["id"], token_tier, now, now + RAW_KEY_TTL_S, link_id,
            ),
        )
        return {
            "status":         "fulfilled",
            "resolved_tier":  token_tier,
            "balance_ui":     balance / (10 ** token_utility.ORACLE_DECIMALS),
        }


# ── Poll: CLI calls this until status changes ────────────────────────────
def poll_link(link_id: str) -> dict:
    """CLI poll endpoint. Returns the current status + key/tier on fulfilled.
    Plaintext key is only returned while `raw_key_expires_at > now`."""
    migrate_schema()
    now = int(time.time())
    with contextlib.closing(sqlite3.connect(db.DB_PATH, timeout=10)) as c, c:
        c.row_factory = sqlite3.Row
        row = c.execute(
            "SELECT * FROM pending_wallet_links WHERE link_id = ?", (link_id,),
        ).fetchone()
        if not row:
            return {"status": "not_found"}

        # Auto-expire pending links past TTL.
        if row["status"] == "pending" and (now - row["created_at"]) > LINK_TTL_S:
            c.execute(
                "UPDATE pending_wallet_links SET status='expired' WHERE link_id=?",
                (link_id,),
            )
            return {"status": "expired"}

        out = {
            "status":         row["status"],
            "tier_requested": row["tier"],
            "wallet":         row["wallet"],
            # Echo the exact signed message so the browser doesn't have to
            # reconstruct it (any drift = signature fails verification).
            "message":        row["message"],
        }
        if row["status"] == "fulfilled":
            out["resolved_tier"] = row["resolved_tier"]
            out["balance_ui"]    = (row["balance_atomic"] or 0) / (10 ** token_utility.ORACLE_DECIMALS)
            # Plaintext key only while still within the raw-key TTL.
            if row["raw_key_expires_at"] and row["raw_key_expires_at"] > now:
                out["key"]        = row["new_key"]
                out["key_prefix"] = row["new_key_prefix"]
            else:
                out["key_expired_for_poll"] = True
        elif row["status"] == "insufficient_balance":
            out["balance_ui"]   = (row["balance_atomic"] or 0) / (10 ** token_utility.ORACLE_DECIMALS)
            out["threshold_ui"] = token_utility.HOLDING_TIERS_UI.get(row["tier"])
        return out
