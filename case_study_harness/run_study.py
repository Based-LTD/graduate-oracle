"""Case Study Harness — main daemon entry point.

Reads a study config (TOML), waits for the configured trigger event,
runs collection for the configured window, resolves outcomes during
the grace window, and exits cleanly. Writes observations to a separate
sqlite file (default /data/case_studies.sqlite) — never touches the
production scoring DB for writes.

Per Case Study 01 pre-reg (commit 5bc8f33): no parameter tweaks, no
early stops, no live rules toggle. Pure read-only instrumentation.

Run:
    python3 -m case_study_harness.run_study --config case_study_harness/configs/study_01_gmgn.toml
"""
import argparse
import contextlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

# Python 3.11+ tomllib is stdlib; older versions need tomli
try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore

from .joiner import Joiner
from .resolver import Resolver
from .sources.grad_oracle import GradOracleSource
from .sources.gmgn import GmgnSource


def log(msg: str):
    """Stdout logging with explicit flush — captured by supervisord."""
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(f"[case_study_harness] {ts} {msg}", flush=True)


def _write_gmgn_credentials():
    """Write ~/.config/gmgn/.env from env vars at daemon startup.

    Reads GMGN_API_KEY and GMGN_PRIVATE_KEY from the environment (set
    via Fly secrets in production). Skips with a warning if either is
    missing — daemon's trigger-wait phase still works; collection_loop
    will log gmgn-cli credentials errors if they're unset at trigger time.

    Idempotent; safe to call on every daemon restart. Permissions tightened
    to 700 (dir) and 600 (file) so other processes on the same host can't
    read the credentials.
    """
    api_key = os.environ.get("GMGN_API_KEY")
    private_key = os.environ.get("GMGN_PRIVATE_KEY")
    if not api_key or not private_key:
        log("WARNING: GMGN_API_KEY and/or GMGN_PRIVATE_KEY not set in env. "
            "gmgn-cli will fail at collection time. Set via "
            "`fly secrets set GMGN_API_KEY=... GMGN_PRIVATE_KEY=...` "
            "before trigger fires.")
        return
    config_dir = Path.home() / ".config" / "gmgn"
    config_dir.mkdir(parents=True, exist_ok=True)
    try:
        config_dir.chmod(0o700)
    except Exception:
        pass  # may not be allowed on some hosts; not fatal
    env_file = config_dir / ".env"
    # Quote private key to preserve PEM newlines if multi-line. Most
    # .env parsers (including Node's dotenv) handle quoted multi-line
    # values correctly.
    env_file.write_text(
        f"GMGN_API_KEY={api_key}\n"
        f'GMGN_PRIVATE_KEY="{private_key}"\n'
    )
    try:
        env_file.chmod(0o600)
    except Exception:
        pass
    log(f"wrote gmgn credentials to {env_file} (chmod 600)")


def init_output_schema(out_db: str, table_name: str):
    """Create the output table if it doesn't exist. Schema frozen per
    pre-reg; future studies use parallel tables (case_study_NN_observations)."""
    with contextlib.closing(sqlite3.connect(out_db, timeout=10)) as c, c:
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                mint TEXT NOT NULL,
                grad_oracle_predicted_at INTEGER NOT NULL,
                go_grad_prob REAL,
                go_grad_prob_bucket TEXT,
                go_age_bucket INTEGER,
                go_runner_prob_2x REAL,
                go_runner_prob_5x REAL,
                go_expected_peak_mult REAL,
                go_feat_smart_money INTEGER,
                go_feat_n_whales INTEGER,
                go_feat_unique_buyers INTEGER,
                go_feat_vsol_velocity REAL,
                go_feat_fee_delegated INTEGER,
                go_feat_creator_n_launches INTEGER,
                go_feat_creator_5x_rate REAL,
                go_feat_bundle_pct REAL,
                go_feat_dex_paid INTEGER,
                gmgn_snapshot_at INTEGER,
                gmgn_in_strict_preset INTEGER,
                gmgn_progress REAL,
                gmgn_holder_count INTEGER,
                gmgn_smart_degen_count INTEGER,
                gmgn_renowned_count INTEGER,
                gmgn_top70_sniper_hold_rate REAL,
                gmgn_creator_created_open_ratio REAL,
                gmgn_bundler_rate REAL,
                gmgn_rug_ratio REAL,
                gmgn_raw_excerpt TEXT,
                join_diff_s INTEGER,
                outcome_resolved_at INTEGER,
                outcome_graduated INTEGER,
                outcome_peak_mult REAL,
                captured_at INTEGER NOT NULL,
                PRIMARY KEY (mint, grad_oracle_predicted_at)
            )
        """)
        c.execute(f"CREATE INDEX IF NOT EXISTS idx_{table_name}_pred_at ON {table_name}(grad_oracle_predicted_at)")
        c.execute(f"CREATE INDEX IF NOT EXISTS idx_{table_name}_outcome ON {table_name}(outcome_graduated)")


def insert_observation(out_db: str, table_name: str, obs: dict):
    """Insert one observation row; INSERT OR IGNORE on PK conflict (mint,
    predicted_at). Idempotent — same prediction won't double-insert."""
    cols = ",".join(obs.keys())
    placeholders = ",".join("?" * len(obs))
    with contextlib.closing(sqlite3.connect(out_db, timeout=10)) as c, c:
        c.execute(f"INSERT OR IGNORE INTO {table_name} ({cols}) VALUES ({placeholders})",
                  tuple(obs.values()))


def update_outcome(out_db: str, table_name: str, obs: dict):
    """Update an existing row's outcome columns after resolver populates them."""
    if obs.get("outcome_graduated") is None:
        return
    with contextlib.closing(sqlite3.connect(out_db, timeout=10)) as c, c:
        c.execute(f"""
            UPDATE {table_name}
               SET outcome_resolved_at = ?,
                   outcome_graduated = ?,
                   outcome_peak_mult = ?
             WHERE mint = ? AND grad_oracle_predicted_at = ?
        """, (obs.get("outcome_resolved_at"),
              obs.get("outcome_graduated"),
              obs.get("outcome_peak_mult"),
              obs["mint"],
              obs["grad_oracle_predicted_at"]))


def load_observations(out_db: str, table_name: str) -> list[dict]:
    with contextlib.closing(sqlite3.connect(out_db, timeout=10)) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(f"SELECT * FROM {table_name}").fetchall()
    return [dict(r) for r in rows]


def wait_for_trigger(trigger_ts: int, poll_interval_s: int = 60):
    """Block until current time >= trigger_ts. Logs progress every ~10 min."""
    log(f"wait_for_trigger: target = {trigger_ts} "
        f"({time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(trigger_ts))})")
    last_log = 0
    while True:
        now = int(time.time())
        if now >= trigger_ts:
            log(f"trigger fired at {now} "
                f"({time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now))})")
            return
        remaining = trigger_ts - now
        if now - last_log >= 600:  # log every ~10 min
            log(f"trigger-wait: {remaining}s remaining (~{remaining/3600:.1f}h)")
            last_log = now
        time.sleep(min(poll_interval_s, remaining))


def collection_loop(cfg: dict):
    """48h collection per pre-reg. 60s tick: pull both sources, join,
    insert into output table. Concurrent resolver sweep every 5 min."""
    out_db = cfg["output"]["db_path"]
    table_name = cfg["output"]["table_name"]

    grad = GradOracleSource(
        db_path=cfg["sources"]["grad_oracle"]["db_path"],
        age_max=cfg["sources"]["grad_oracle"]["age_max"],
        buckets=tuple(cfg["sources"]["grad_oracle"]["buckets"]),
    )
    gmgn = GmgnSource(
        chain=cfg["sources"]["gmgn"]["chain"],
        type_arg=cfg["sources"]["gmgn"]["type_arg"],
        filter_preset=cfg["sources"]["gmgn"]["filter_preset"],
        cli_path=cfg["sources"]["gmgn"].get("cli_path", "gmgn-cli"),
    )
    joiner = Joiner(tolerance_s=cfg["join"]["tolerance_s"])
    resolver = Resolver(
        prod_db_path=cfg["sources"]["grad_oracle"]["db_path"],
        grace_window_s=cfg["outcome"]["grace_window_s"],
    )

    init_output_schema(out_db, table_name)

    collection_start = int(time.time())
    collection_end = collection_start + cfg["window"]["duration_s"]
    log(f"collection start={collection_start} end={collection_end}; "
        f"window={cfg['window']['duration_s']}s")

    # Backfill cursor: pull from the original trigger_ts forward, not
    # int(time.time()), so a daemon restart mid-collection doesn't skip
    # predictions emitted between the original trigger and the restart.
    # Per the 2026-05-10 grad_oracle enrichment-bug postmortem: with the
    # original `grad.pull()` (cursor=now) seed, predictions emitted before
    # any restart were unrecoverable. With `since_ts=trigger_ts`, the first
    # post-restart pull replays all post-trigger predictions and inserts
    # them; GMGN side may be NULL (snapshots are in-memory only and don't
    # survive restart), but the absence is itself data per the joiner
    # contract, and the grad-side count is what the C-iv subcondition
    # verifies against.
    trigger_ts = cfg["window"]["start_at_ts"]
    log(f"backfill seed: setting cursor to trigger_ts={trigger_ts} "
        f"(rather than int(time.time())) so restarts don't skip "
        f"post-trigger predictions emitted before restart")
    initial_preds = grad.pull(since_ts=trigger_ts)
    log(f"backfill seed returned {len(initial_preds)} preds; "
        f"cursor now at {grad.cursor}")
    for p in initial_preds:
        obs = joiner.match(p, [])  # empty snapshot_buffer — no GMGN match possible at backfill time
        insert_observation(out_db, table_name, obs)
    if initial_preds:
        log(f"backfill seed: inserted {len(initial_preds)} backfill rows "
            f"(GMGN side NULL — snapshot buffer was empty at backfill time)")

    # Rolling buffer of recent GMGN snapshots within tolerance window
    snapshot_buffer: list[dict] = []
    last_resolver_sweep = collection_start
    n_observations = 0
    n_resolved = 0

    while int(time.time()) < collection_end:
        loop_start = int(time.time())

        # 1. Pull GMGN snapshot (60s cadence)
        snap = gmgn.snapshot()
        if snap.get("error"):
            log(f"GMGN snapshot error: {snap['error']} (continuing)")
        snapshot_buffer.append(snap)
        # Trim buffer to window of [now - 2*tolerance, now]
        cutoff = loop_start - 2 * cfg["join"]["tolerance_s"]
        snapshot_buffer = [s for s in snapshot_buffer if s["snapshot_at"] >= cutoff]

        # 2. Pull new graduate-oracle predictions
        try:
            preds = grad.pull()
        except Exception as e:
            log(f"grad_oracle pull error: {e} (continuing)")
            preds = []

        # 3. Join each pred against snapshot buffer; insert
        for p in preds:
            obs = joiner.match(p, snapshot_buffer)
            insert_observation(out_db, table_name, obs)
            n_observations += 1
        if preds:
            log(f"inserted {len(preds)} observations (total: {n_observations})")

        # 4. Resolver sweep every 5 min
        if loop_start - last_resolver_sweep >= 300:
            try:
                obs_list = load_observations(out_db, table_name)
                newly, _still = resolver.sweep(obs_list)
                if newly:
                    for o in obs_list:
                        update_outcome(out_db, table_name, o)
                    n_resolved += newly
                    log(f"resolver: {newly} newly resolved (total: {n_resolved})")
            except Exception as e:
                log(f"resolver error: {e} (continuing)")
            last_resolver_sweep = loop_start

        # 5. Sleep to honor 60s cadence
        elapsed = int(time.time()) - loop_start
        sleep_s = max(0, cfg["window"]["tick_s"] - elapsed)
        if sleep_s:
            time.sleep(sleep_s)

    log(f"collection_end reached. observations={n_observations}, resolved_so_far={n_resolved}")
    return out_db, table_name, resolver


def grace_loop(cfg: dict, out_db: str, table_name: str, resolver: Resolver):
    """Post-collection grace window. Resolver-only mode for grace_window_s
    seconds. Resolves outcomes for observations whose pred_ts + grace
    has passed."""
    grace_end = int(time.time()) + cfg["outcome"]["grace_window_s"]
    log(f"grace_loop start; ends at {grace_end} "
        f"({time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(grace_end))})")
    while int(time.time()) < grace_end:
        try:
            obs_list = load_observations(out_db, table_name)
            newly, still = resolver.sweep(obs_list)
            if newly:
                for o in obs_list:
                    update_outcome(out_db, table_name, o)
                log(f"grace_loop: {newly} newly resolved; {still} still unresolved")
            if still == 0:
                log("grace_loop: all observations resolved; exiting early")
                break
        except Exception as e:
            log(f"grace_loop error: {e} (continuing)")
        time.sleep(300)
    log("grace_loop done")


def main():
    parser = argparse.ArgumentParser(description="Case Study Harness daemon")
    parser.add_argument("--config", required=True, help="Path to TOML config")
    args = parser.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        log(f"ERROR: config not found: {cfg_path}")
        sys.exit(1)
    with open(cfg_path, "rb") as f:
        cfg = tomllib.load(f)
    log(f"loaded config: {cfg.get('meta', {}).get('study_id', '?')} - "
        f"{cfg.get('meta', {}).get('title', '?')}")
    log(f"prereg_commit: {cfg.get('meta', {}).get('prereg_commit', '?')}")

    # Write GMGN credentials from env vars (Fly secrets) before trigger-wait.
    # Safe to call here even though collection doesn't start for hours —
    # writing creds at startup catches missing-secret errors early.
    _write_gmgn_credentials()

    # Trigger wait
    trigger_ts = cfg["window"]["start_at_ts"]
    wait_for_trigger(trigger_ts)

    # Collection
    out_db, table_name, resolver = collection_loop(cfg)

    # Grace
    grace_loop(cfg, out_db, table_name, resolver)

    log("daemon exiting cleanly. branch verdict to be evaluated by analysis script.")


if __name__ == "__main__":
    main()
