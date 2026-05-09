"""graduate-oracle source adapter.

Reads predictions table from /data/data.sqlite (production DB) in
read-only mode. Filters: age_bucket <= 75 AND grad_prob_bucket IN
('HIGH', 'MED'). Joins mint_checkpoints (checkpoint_age_s=60) for
the lane-60s feature snapshot when available.

Frozen behavior per Case Study 01 pre-reg (commit 5bc8f33).
"""
import contextlib
import sqlite3
import time
from typing import Optional


class GradOracleSource:
    name = "grad_oracle"

    def __init__(self, db_path: str = "/data/data.sqlite",
                 age_max: int = 75,
                 buckets: tuple = ("HIGH", "MED")):
        self.db_path = db_path
        self.age_max = age_max
        self.buckets = tuple(buckets)
        self.cursor: Optional[int] = None  # last seen predicted_at

    def _connect_ro(self):
        # Read-only URI mode prevents accidental writes to the prod DB.
        return sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=10)

    def pull(self, since_ts: Optional[int] = None) -> list[dict]:
        """Pull new predictions since the cursor (or since_ts override).
        On first call (cursor None and no since_ts), returns nothing —
        we only collect predictions emitted from now forward, never
        backfill historical ones (per pre-reg)."""
        cursor = since_ts if since_ts is not None else self.cursor
        if cursor is None:
            # First call: set cursor to now; no historical pull.
            self.cursor = int(time.time())
            return []
        placeholders = ",".join("?" * len(self.buckets))
        with contextlib.closing(self._connect_ro()) as c:
            c.row_factory = sqlite3.Row
            # Predictions table: capture the prediction event itself.
            rows = c.execute(f"""
                SELECT id, mint, predicted_at, predicted_prob,
                       grad_prob_bucket, age_bucket, runner_prob_2x,
                       runner_prob_5x, expected_peak_mult,
                       runner_prob_2x_from_now
                  FROM predictions
                 WHERE predicted_at > ?
                   AND age_bucket <= ?
                   AND grad_prob_bucket IN ({placeholders})
                 ORDER BY predicted_at
            """, (cursor, self.age_max, *self.buckets)).fetchall()
            preds = [dict(r) for r in rows]
            # Enrich each prediction with mint_checkpoints feature snapshot
            # at the closest checkpoint age <= age_bucket.
            for p in preds:
                ck = c.execute("""
                    SELECT feature_smart_money, feature_n_whales,
                           feature_unique_buyers, feature_vsol_velocity,
                           COALESCE(feature_fee_delegated, 0) AS feature_fee_delegated,
                           feature_creator_n_launches,
                           feature_creator_5x_rate,
                           feature_bundle_pct,
                           feature_dex_paid,
                           checkpoint_age_s
                      FROM mint_checkpoints
                     WHERE mint = ?
                       AND checkpoint_age_s <= ?
                     ORDER BY checkpoint_age_s DESC
                     LIMIT 1
                """, (p["mint"], p.get("age_bucket") or 60)).fetchone()
                if ck:
                    for k in ck.keys():
                        p[f"feat_{k}"] = ck[k]
        if preds:
            self.cursor = preds[-1]["predicted_at"]
        return preds
