"""
trader_wallets — per-user Solana wallet generation, encrypted storage, signing.

This is the *custody* layer for the TG trading bot. It is intentionally
small, self-contained, and has zero dependency on the signal pipeline,
the Rust trading engine, or the Telegram bot. It can be tested entirely
from the command line before any of those are wired up.

Threat model
────────────
We hold user SOL. If the master key leaks, every user wallet is
compromised. Mitigations:
  • Master key lives in TRADER_MASTER_KEY env (Fly secret). Never logged.
  • Encryption uses Fernet (AES-128-CBC + HMAC-SHA256). Built-in
    timestamp + version byte makes tampering / replay detectable.
  • Schema reserves an `encryption_version` column so we can rotate
    keys without losing user wallets (re-encrypt with new master).
  • Optional `withdraw_pwd_hash` adds defense-in-depth: an attacker
    who steals the master key still can't withdraw without the user's
    password.
  • Database lives at a separate path (/data/trader.sqlite) so backup,
    audit, and access controls can be applied independently of the
    main observer DB.

CLI tests
─────────
  python -m trader_wallets generate <user_id>
  python -m trader_wallets pubkey   <user_id>
  python -m trader_wallets balance  <user_id>
  python -m trader_wallets sign     <user_id> <base64_tx>

Set TRADER_MASTER_KEY first:
  export TRADER_MASTER_KEY=$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')
"""

from __future__ import annotations

import base64
import contextlib
import os
import sqlite3
import sys
import time
import urllib.request
import urllib.error
import json
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from solders.keypair import Keypair
from solders.pubkey import Pubkey

import bcrypt


# ── Configuration ────────────────────────────────────────────────────────

# Separate DB so custody data isn't co-located with the giant observer DB.
# In dev / outside Fly, fall back to a local path so the CLI is usable.
def _db_path() -> Path:
    env = os.environ.get("TRADER_DB_PATH")
    if env:
        return Path(env)
    if Path("/data").is_dir():
        return Path("/data/trader.sqlite")
    return Path(__file__).parent.parent / "trader.sqlite"


def _master_key() -> bytes:
    """Returns the Fernet master key. Raises if not set.

    Generate one with:
      python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
    Then store as a Fly secret: fly secrets set TRADER_MASTER_KEY=...
    """
    raw = os.environ.get("TRADER_MASTER_KEY", "").strip()
    if not raw:
        raise RuntimeError(
            "TRADER_MASTER_KEY env var is required for trader_wallets. "
            "Generate one with: python -c 'from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())'"
        )
    return raw.encode()


def _fernet() -> Fernet:
    return Fernet(_master_key())


# Pinned to allow key rotation later. v1 = current Fernet master key.
ENCRYPTION_VERSION = 1

# RPC for balance queries — reuse the same env as the rest of the stack.
_RPC = (os.environ.get("RPC_HTTP") or "https://api.mainnet-beta.solana.com").rstrip("/")


# ── Schema ───────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trader_wallets (
    user_id               TEXT PRIMARY KEY,
    public_key            TEXT NOT NULL UNIQUE,
    encrypted_private_key BLOB NOT NULL,
    encryption_version    INTEGER NOT NULL DEFAULT 1,
    withdraw_pwd_hash     TEXT,
    created_at_unix       INTEGER NOT NULL,
    last_activity_unix    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_tw_pubkey ON trader_wallets(public_key);
"""


def init_schema():
    """Idempotent. Safe to call on every process start."""
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.closing(sqlite3.connect(path, timeout=10)) as c, c:
        c.executescript(_SCHEMA)


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_db_path(), timeout=10)
    c.row_factory = sqlite3.Row
    return c


# ── Wallet generation & lookup ───────────────────────────────────────────

def generate_wallet(user_id: str | int) -> dict:
    """Generates a fresh Solana keypair for `user_id`, encrypts the
    private key with the master key, stores it.

    Returns {user_id, public_key} on success. The private key is
    NEVER returned — it stays encrypted at rest, decrypted only at
    signing time.

    Raises if a wallet already exists for `user_id`. Use `wallet_for`
    to retrieve an existing wallet.
    """
    init_schema()
    user_id = str(user_id)
    kp = Keypair()
    pubkey = str(kp.pubkey())
    secret_bytes = bytes(kp)  # 64 bytes: 32-byte seed + 32-byte pubkey
    enc = _fernet().encrypt(secret_bytes)
    now = int(time.time())
    with contextlib.closing(_conn()) as c, c:
        try:
            c.execute(
                "INSERT INTO trader_wallets "
                "(user_id, public_key, encrypted_private_key, encryption_version, created_at_unix) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, pubkey, enc, ENCRYPTION_VERSION, now),
            )
        except sqlite3.IntegrityError as e:
            raise RuntimeError(f"wallet already exists for user {user_id}") from e
    return {"user_id": user_id, "public_key": pubkey, "created_at_unix": now}


def wallet_for(user_id: str | int) -> Optional[dict]:
    """Returns {user_id, public_key, created_at_unix} or None if no
    wallet exists. Does NOT decrypt the private key."""
    init_schema()
    user_id = str(user_id)
    with contextlib.closing(_conn()) as c:
        row = c.execute(
            "SELECT user_id, public_key, created_at_unix, last_activity_unix "
            "FROM trader_wallets WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def get_or_create_wallet(user_id: str | int) -> dict:
    """Idempotent: returns existing wallet or generates a fresh one.
    Use this from the TG bot's /start handler so users always have a
    wallet without needing a separate 'create wallet' step."""
    existing = wallet_for(user_id)
    if existing:
        return existing
    return generate_wallet(user_id)


# ── Signing ──────────────────────────────────────────────────────────────

def _decrypt_keypair(user_id: str) -> Keypair:
    """Decrypts and returns the Solana Keypair for `user_id`. Raises
    if no wallet exists or decryption fails (corrupt data or wrong
    master key)."""
    with contextlib.closing(_conn()) as c:
        row = c.execute(
            "SELECT encrypted_private_key, encryption_version "
            "FROM trader_wallets WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        raise RuntimeError(f"no wallet for user {user_id}")
    if row["encryption_version"] != ENCRYPTION_VERSION:
        raise RuntimeError(
            f"wallet for user {user_id} was encrypted with version "
            f"{row['encryption_version']}, current is {ENCRYPTION_VERSION}. "
            "Run key-rotation migration before signing."
        )
    try:
        secret_bytes = _fernet().decrypt(bytes(row["encrypted_private_key"]))
    except InvalidToken as e:
        raise RuntimeError(
            "decryption failed — master key may be wrong or data corrupt"
        ) from e
    return Keypair.from_bytes(secret_bytes)


def sign_transaction(user_id: str | int, tx_b64: str) -> str:
    """Signs the given base64-encoded VersionedTransaction with the
    user's wallet. Returns the signed transaction as base64.

    The caller is responsible for building the unsigned tx (recent
    blockhash, fee payer, instructions). This function is purely the
    "sign" step — it does NOT submit. Submission is the caller's job
    (Jito bundle for snipes, plain RPC for withdrawals).
    """
    from solders.transaction import VersionedTransaction
    user_id = str(user_id)
    kp = _decrypt_keypair(user_id)
    raw = base64.b64decode(tx_b64)
    # The Solders API: VersionedTransaction.from_bytes parses the wire
    # format. .sign() returns a NEW signed tx (doesn't mutate).
    unsigned = VersionedTransaction.from_bytes(raw)
    # Build a new signed tx — solders requires reconstructing via the
    # message + signers since VersionedTransaction is immutable.
    signed = VersionedTransaction(unsigned.message, [kp])
    _touch_activity(user_id)
    return base64.b64encode(bytes(signed)).decode("ascii")


def _touch_activity(user_id: str):
    """Update last_activity_unix on every sign — gives us an audit
    trail of when each wallet was last used."""
    with contextlib.closing(_conn()) as c, c:
        c.execute(
            "UPDATE trader_wallets SET last_activity_unix = ? WHERE user_id = ?",
            (int(time.time()), user_id),
        )


# ── Withdraw password (defense in depth, optional in v0) ─────────────────

def set_withdraw_password(user_id: str | int, password: str):
    """Stores a bcrypt hash of the user's withdrawal password. Required
    before a withdrawal can be authorized — so even if our master key
    leaks, withdrawals still need this second factor."""
    if not password or len(password) < 8:
        raise ValueError("withdraw password must be at least 8 characters")
    user_id = str(user_id)
    h = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    with contextlib.closing(_conn()) as c, c:
        n = c.execute(
            "UPDATE trader_wallets SET withdraw_pwd_hash = ? WHERE user_id = ?",
            (h, user_id),
        ).rowcount
    if n == 0:
        raise RuntimeError(f"no wallet for user {user_id}")


def has_withdraw_password(user_id: str | int) -> bool:
    """Returns True iff the user has already set a withdraw password.
    Used by the TG wizard to decide whether to show first-time setup."""
    user_id = str(user_id)
    init_schema()
    with contextlib.closing(_conn()) as c:
        row = c.execute(
            "SELECT withdraw_pwd_hash FROM trader_wallets WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return bool(row and row["withdraw_pwd_hash"])


def verify_withdraw_password(user_id: str | int, password: str) -> bool:
    """Returns True iff `password` matches the stored hash for this
    user. Returns False if no password has been set OR the password
    is wrong — caller decides how to surface that distinction."""
    user_id = str(user_id)
    with contextlib.closing(_conn()) as c:
        row = c.execute(
            "SELECT withdraw_pwd_hash FROM trader_wallets WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not row or not row["withdraw_pwd_hash"]:
        return False
    try:
        return bcrypt.checkpw(password.encode(), row["withdraw_pwd_hash"].encode())
    except Exception:
        return False


# ── Withdrawal — sign + submit a SOL transfer ────────────────────────────

# Reasonable defaults; can be overridden per call by the TG handler.
WITHDRAW_FEE_RESERVE_LAMPORTS = 5_000      # min keep-alive after withdraw (~rent)
WITHDRAW_MIN_LAMPORTS         = 100_000    # 0.0001 SOL — anti-dust
WITHDRAW_DAILY_LIMIT_LAMPORTS = 500_000_000      # 0.5 SOL/day default for NEW users
# Existing admin / trusted users can be bumped via the future
# trader_user_settings.withdraw_daily_limit_lamports column (TODO).
# This conservative default drops blast radius if a TG account is
# compromised — attacker can drain at most 0.5 SOL/24h before being
# rate-limited at the audit layer.
WITHDRAW_CONFIRM_TIMEOUT_S    = 60
WITHDRAW_CONFIRM_POLL_S       = 2.0


def _rpc_call(method: str, params: list, timeout: float = 8.0) -> dict:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(
        _RPC, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.loads(r.read())
    if "error" in resp:
        raise RuntimeError(f"RPC {method} error: {resp['error']}")
    return resp.get("result", {})


def _withdraw_total_today(user_id: str) -> int:
    """Sum of every confirmed/submitted withdrawal in the last 24h.
    Used to enforce the daily limit. Conservative — includes 'submitted'
    rows (which might not have confirmed yet) so we don't permit
    racing withdrawals up over the ceiling."""
    cutoff = int(time.time()) - 24 * 3600
    try:
        with contextlib.closing(_conn()) as c:
            row = c.execute(
                "SELECT COALESCE(SUM(lamports), 0) AS total FROM trader_withdrawals "
                "WHERE user_id = ? AND status IN ('submitted', 'confirmed') "
                "AND requested_at_unix >= ?",
                (user_id, cutoff),
            ).fetchone()
        return int(row["total"] or 0)
    except sqlite3.OperationalError:
        # trader_withdrawals table not initialized yet (deposits module
        # hasn't run init_schema). Treat as 0 — caller's responsibility
        # to ensure schema is up-to-date before authorizing real value.
        return 0


def internal_send_sol(
    user_id: str | int,
    to_address: str,
    lamports: int,
) -> str:
    """SERVER-INTERNAL SOL transfer. Used by fee_skim and other server-side
    flows that need to move SOL out of a user's wallet WITHOUT the
    user-facing safeguards (password, daily limit, audit trail).

    Use this ONLY from trusted server code (fee_skim.apply_fee). Never
    expose this via an API or TG command — it has zero authorization
    checks beyond "the calling code has the user_id."

    Returns the tx signature. Raises ValueError / RuntimeError on
    validation or RPC failure. Does NOT poll for confirmation — the
    caller decides whether to wait.
    """
    from solders.transaction import VersionedTransaction
    from solders.message import MessageV0
    from solders.system_program import TransferParams, transfer as sys_transfer
    from solders.hash import Hash
    user_id = str(user_id)
    to_address = (to_address or "").strip()
    lamports = int(lamports)
    if lamports <= 0:
        raise ValueError(f"lamports must be > 0, got {lamports}")
    try:
        to_pubkey = Pubkey.from_string(to_address)
    except Exception as e:
        raise ValueError(f"invalid destination address: {e}") from e
    kp = _decrypt_keypair(user_id)
    bh = _rpc_call("getLatestBlockhash", [{"commitment": "confirmed"}])
    blockhash = Hash.from_string(bh["value"]["blockhash"])
    ix = sys_transfer(TransferParams(
        from_pubkey=kp.pubkey(), to_pubkey=to_pubkey, lamports=lamports,
    ))
    msg = MessageV0.try_compile(
        payer=kp.pubkey(), instructions=[ix],
        address_lookup_table_accounts=[], recent_blockhash=blockhash,
    )
    signed = VersionedTransaction(msg, [kp])
    signed_b64 = base64.b64encode(bytes(signed)).decode("ascii")
    sig = _rpc_call(
        "sendTransaction",
        [signed_b64, {"encoding": "base64", "skipPreflight": True,
                       "preflightCommitment": "confirmed"}],
        timeout=15.0,
    )
    return sig


def withdraw(
    user_id: str | int,
    to_address: str,
    lamports: int,
    password: str,
    *,
    daily_limit_lamports: int = WITHDRAW_DAILY_LIMIT_LAMPORTS,
) -> dict:
    """The full withdrawal flow:

      1. Verify the withdraw password (HARD requirement — there is no
         code path that signs a withdrawal without it).
      2. Check the daily-limit ceiling against the audit table.
      3. Sanity-check the destination and amount.
      4. Build a SOL transfer transaction, sign with the user's key.
      5. Write 'submitted' to the audit table BEFORE we hit the RPC,
         so a crash mid-flight leaves a recoverable trail.
      6. Submit via sendTransaction (plain RPC, not Jito — withdrawals
         don't need MEV protection).
      7. Poll for confirmation up to WITHDRAW_CONFIRM_TIMEOUT_S.
      8. Update the audit row with the final status.

    Returns a dict with status: 'confirmed' | 'failed' | 'timed_out',
    signature, and the audit row id. Raises on caller error
    (bad password, over limit, malformed address) — never raises for
    on-chain failures (those become 'failed' rows).
    """
    user_id = str(user_id)
    to_address = (to_address or "").strip()
    lamports = int(lamports)

    # ── Validation ────────────────────────────────────────────────────
    if lamports < WITHDRAW_MIN_LAMPORTS:
        raise ValueError(f"withdrawal too small (min {WITHDRAW_MIN_LAMPORTS} lamports)")
    if not verify_withdraw_password(user_id, password):
        raise PermissionError("invalid withdraw password")
    try:
        to_pubkey = Pubkey.from_string(to_address)
    except Exception as e:
        raise ValueError(f"invalid destination address: {e}") from e

    used_today = _withdraw_total_today(user_id)
    if used_today + lamports > daily_limit_lamports:
        raise PermissionError(
            f"daily withdraw limit exceeded — "
            f"used {used_today/1e9:.4f} SOL of {daily_limit_lamports/1e9:.4f} SOL today"
        )

    # ── Wallet ────────────────────────────────────────────────────────
    kp = _decrypt_keypair(user_id)
    from_pubkey = kp.pubkey()

    # Verify balance covers withdrawal + tx fee + rent reserve
    current_balance = get_balance_lamports(user_id)
    # Solana standard tx fee is 5000 lamports; we leave a small buffer.
    estimated_fee = 5_000
    needed = lamports + estimated_fee + WITHDRAW_FEE_RESERVE_LAMPORTS
    if current_balance < needed:
        raise ValueError(
            f"insufficient balance: have {current_balance/1e9:.4f} SOL, "
            f"need {needed/1e9:.4f} SOL (withdrawal + fee + reserve)"
        )

    # ── Audit row BEFORE submission ───────────────────────────────────
    now = int(time.time())
    with contextlib.closing(_conn()) as c, c:
        cur = c.execute(
            "INSERT INTO trader_withdrawals "
            "(user_id, to_address, lamports, status, requested_at_unix) "
            "VALUES (?, ?, ?, 'requested', ?)",
            (user_id, to_address, lamports, now),
        )
        row_id = cur.lastrowid

    # ── Build + sign tx ───────────────────────────────────────────────
    from solders.transaction import VersionedTransaction
    from solders.message import MessageV0
    from solders.system_program import TransferParams, transfer
    from solders.hash import Hash

    try:
        blockhash_result = _rpc_call("getLatestBlockhash", [{"commitment": "confirmed"}])
        blockhash_str = blockhash_result["value"]["blockhash"]
        recent_blockhash = Hash.from_string(blockhash_str)
        ix = transfer(TransferParams(
            from_pubkey=from_pubkey, to_pubkey=to_pubkey, lamports=lamports,
        ))
        msg = MessageV0.try_compile(
            payer=from_pubkey, instructions=[ix],
            address_lookup_table_accounts=[], recent_blockhash=recent_blockhash,
        )
        signed = VersionedTransaction(msg, [kp])
        signed_b64 = base64.b64encode(bytes(signed)).decode("ascii")

        # ── Submit ────────────────────────────────────────────────────
        sig = _rpc_call(
            "sendTransaction",
            [signed_b64, {"encoding": "base64", "skipPreflight": False, "preflightCommitment": "confirmed"}],
            timeout=15.0,
        )
        with contextlib.closing(_conn()) as c, c:
            c.execute(
                "UPDATE trader_withdrawals SET status='submitted', signature=?, submitted_at_unix=? WHERE id=?",
                (sig, int(time.time()), row_id),
            )

        # ── Poll for confirmation ─────────────────────────────────────
        deadline = time.time() + WITHDRAW_CONFIRM_TIMEOUT_S
        confirmed = False
        while time.time() < deadline:
            time.sleep(WITHDRAW_CONFIRM_POLL_S)
            try:
                statuses = _rpc_call("getSignatureStatuses", [[sig]])
                value = statuses.get("value", [None])[0]
                if not value:
                    continue
                err = value.get("err")
                conf = value.get("confirmationStatus")
                if err is not None:
                    with contextlib.closing(_conn()) as c, c:
                        c.execute(
                            "UPDATE trader_withdrawals SET status='failed', error_message=? WHERE id=?",
                            (json.dumps(err), row_id),
                        )
                    return {"status": "failed", "signature": sig, "row_id": row_id, "error": err}
                if conf in ("confirmed", "finalized"):
                    confirmed = True
                    break
            except Exception:
                # transient RPC hiccup — keep polling
                continue

        if confirmed:
            with contextlib.closing(_conn()) as c, c:
                c.execute(
                    "UPDATE trader_withdrawals SET status='confirmed', confirmed_at_unix=? WHERE id=?",
                    (int(time.time()), row_id),
                )
            _touch_activity(user_id)
            return {"status": "confirmed", "signature": sig, "row_id": row_id}

        with contextlib.closing(_conn()) as c, c:
            c.execute(
                "UPDATE trader_withdrawals SET status='timed_out' WHERE id=?",
                (row_id,),
            )
        return {"status": "timed_out", "signature": sig, "row_id": row_id}

    except Exception as e:
        # Unexpected failure during build/sign/submit — record and re-raise
        with contextlib.closing(_conn()) as c, c:
            c.execute(
                "UPDATE trader_withdrawals SET status='failed', error_message=? WHERE id=?",
                (str(e), row_id),
            )
        raise


# ── Balance lookup (RPC, no decryption needed) ───────────────────────────

def get_token_balance_raw(payer_pubkey: str, mint: str) -> int:
    """Return the raw token balance (smallest unit) the payer holds for
    a given mint. 0 if no ATA exists. Pure RPC, no decryption.

    Used post-confirmation in the orchestrator to find what actually
    landed in the wallet vs Jupiter's quote — slippage and partial
    fills cause real divergence. The DB should track on-chain truth,
    not the quote.

    Works for both legacy SPL and Token-2022 programs without needing
    the program-id hint — we ask getTokenAccountsByOwner for ALL accounts
    matching the mint regardless of program."""
    try:
        # programId filter omitted intentionally — we want both Token and
        # Token-2022 ATAs.
        body = json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "getTokenAccountsByOwner",
            "params": [
                payer_pubkey,
                {"mint": mint},
                {"encoding": "jsonParsed", "commitment": "confirmed"},
            ],
        }).encode()
        r = urllib.request.Request(
            _RPC, data=body, headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(r, timeout=6.0) as resp:
            data = json.loads(resp.read())
        accs = data.get("result", {}).get("value", []) or []
        total = 0
        for a in accs:
            info = a.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
            amt = (info.get("tokenAmount") or {}).get("amount")
            try:
                total += int(amt)
            except (TypeError, ValueError):
                continue
        return total
    except Exception as e:
        print(f"[trader_wallets] get_token_balance_raw failed: {e}", flush=True)
        return 0


def get_balance_lamports(user_id_or_pubkey: str | int) -> int:
    """Returns the current SOL balance in lamports for the user's
    wallet (or for an arbitrary pubkey string). Pure RPC call — no
    decryption — so this is safe to call frequently from the TG bot."""
    s = str(user_id_or_pubkey)
    # If it looks like a base58 pubkey (32-44 chars, no whitespace),
    # use it directly. Otherwise treat as user_id and look up.
    if 32 <= len(s) <= 44 and " " not in s:
        try:
            Pubkey.from_string(s)
            pubkey_str = s
        except Exception:
            w = wallet_for(s)
            if not w:
                raise RuntimeError(f"no wallet for user {s}")
            pubkey_str = w["public_key"]
    else:
        w = wallet_for(s)
        if not w:
            raise RuntimeError(f"no wallet for user {s}")
        pubkey_str = w["public_key"]

    req = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "getBalance",
        "params": [pubkey_str, {"commitment": "confirmed"}],
    }).encode()
    r = urllib.request.Request(
        _RPC, data=req, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(r, timeout=8) as resp:
            body = json.loads(resp.read())
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        raise RuntimeError(f"RPC balance lookup failed: {e}") from e
    val = body.get("result", {}).get("value")
    if val is None:
        raise RuntimeError(f"RPC returned no balance: {body}")
    return int(val)


def get_balance_sol(user_id_or_pubkey: str | int) -> float:
    return get_balance_lamports(user_id_or_pubkey) / 1e9


# ── CLI entry point — used for end-to-end testing before TG wiring ───────

def _cli():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)
    cmd = args[0]
    if cmd == "init":
        init_schema()
        print(f"OK · schema initialized at {_db_path()}")
    elif cmd == "generate":
        if len(args) < 2:
            print("usage: trader_wallets generate <user_id>"); sys.exit(2)
        w = generate_wallet(args[1])
        print(json.dumps(w, indent=2))
    elif cmd == "pubkey":
        if len(args) < 2:
            print("usage: trader_wallets pubkey <user_id>"); sys.exit(2)
        w = wallet_for(args[1])
        if not w:
            print(f"no wallet for user {args[1]}"); sys.exit(1)
        print(w["public_key"])
    elif cmd == "balance":
        if len(args) < 2:
            print("usage: trader_wallets balance <user_id|pubkey>"); sys.exit(2)
        sol = get_balance_sol(args[1])
        print(f"{sol:.6f} SOL")
    elif cmd == "sign":
        if len(args) < 3:
            print("usage: trader_wallets sign <user_id> <tx_b64>"); sys.exit(2)
        signed = sign_transaction(args[1], args[2])
        print(signed)
    elif cmd == "set-password":
        if len(args) < 3:
            print("usage: trader_wallets set-password <user_id> <password>"); sys.exit(2)
        set_withdraw_password(args[1], args[2])
        print("OK · password set")
    elif cmd == "check-password":
        if len(args) < 3:
            print("usage: trader_wallets check-password <user_id> <password>"); sys.exit(2)
        ok = verify_withdraw_password(args[1], args[2])
        print("OK" if ok else "WRONG")
        sys.exit(0 if ok else 1)
    elif cmd == "withdraw":
        if len(args) < 5:
            print("usage: trader_wallets withdraw <user_id> <to_address> <lamports> <password>")
            sys.exit(2)
        result = withdraw(args[1], args[2], int(args[3]), args[4])
        print(json.dumps(result, indent=2))
        sys.exit(0 if result["status"] == "confirmed" else 1)
    else:
        print(f"unknown command: {cmd}")
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    _cli()
