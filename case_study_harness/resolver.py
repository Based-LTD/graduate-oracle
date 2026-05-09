"""Resolver — walk unresolved observations, attempt outcome resolution
against the production DB. Outcome = graduation event observed (vSOL >= 115)
within 24h grace post-prediction. Mints unresolved at grace deadline are
declared failed.

Read-only against /data/data.sqlite (predictions table for actual_graduated,
mint_checkpoints for actual_max_mult). Writes only to the case-study output
DB (separate file, dedicated table).

Frozen 24h grace window per Case Study 01 pre-reg (commit 5bc8f33).
Branch C decision tree may extend grace to 72h post-collection.
"""
import contextlib
import sqlite3
import time
from typing import Optional


class Resolver:
    def __init__(self,
                 prod_db_path: str = "/data/data.sqlite",
                 grace_window_s: int = 24 * 3600,
                 vsol_grad_threshold: float = 115.0):
        self.prod_db_path = prod_db_path
        self.grace_window_s = grace_window_s
        self.vsol_grad_threshold = vsol_grad_threshold

    def _connect_prod_ro(self):
        return sqlite3.connect(f"file:{self.prod_db_path}?mode=ro",
                               uri=True, timeout=10)

    def resolve_one(self, obs: dict) -> dict:
        """Attempt to resolve a single observation. Returns updated dict
        with outcome_* fields populated if resolution is now possible.
        Mutates outcome_resolved_at when the resolver makes a decision."""
        if obs.get("outcome_graduated") is not None:
            return obs  # already resolved
        pred_ts = obs["grad_oracle_predicted_at"]
        mint = obs["mint"]
        now = int(time.time())
        grace_deadline = pred_ts + self.grace_window_s

        with contextlib.closing(self._connect_prod_ro()) as c:
            c.row_factory = sqlite3.Row
            # Check post_grad_outcomes for graduation event
            row = c.execute("""
                SELECT graduated_at FROM post_grad_outcomes WHERE mint = ?
            """, (mint,)).fetchone()
            graduated_at = row["graduated_at"] if row else None
            # Check actual_max_mult / actual_graduated on the predictions row
            pred_row = c.execute("""
                SELECT actual_graduated, actual_max_mult, resolved_at
                  FROM predictions WHERE mint = ? AND predicted_at = ?
            """, (mint, pred_ts)).fetchone()

        if graduated_at is not None and graduated_at <= grace_deadline:
            obs["outcome_graduated"] = 1
            obs["outcome_resolved_at"] = graduated_at
            obs["outcome_peak_mult"] = (pred_row["actual_max_mult"]
                                       if pred_row else None)
            return obs
        if pred_row and pred_row["actual_graduated"] is not None:
            # Defer to production resolution if it landed first
            obs["outcome_graduated"] = int(pred_row["actual_graduated"])
            obs["outcome_resolved_at"] = pred_row["resolved_at"] or now
            obs["outcome_peak_mult"] = pred_row["actual_max_mult"]
            return obs
        # Not resolved yet. If grace deadline passed: declare failed.
        if now >= grace_deadline:
            obs["outcome_graduated"] = 0
            obs["outcome_resolved_at"] = now
            obs["outcome_peak_mult"] = pred_row["actual_max_mult"] if pred_row else None
            return obs
        # Still within grace window; leave unresolved for now.
        return obs

    def sweep(self, observations: list[dict]) -> tuple[int, int]:
        """Walk observations and resolve as many as possible. Returns
        (n_newly_resolved, n_still_unresolved)."""
        newly_resolved = 0
        still_unresolved = 0
        for obs in observations:
            if obs.get("outcome_graduated") is not None:
                continue
            self.resolve_one(obs)
            if obs.get("outcome_graduated") is not None:
                newly_resolved += 1
            else:
                still_unresolved += 1
        return newly_resolved, still_unresolved
