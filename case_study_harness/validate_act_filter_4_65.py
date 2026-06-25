"""
Day 4.65 ACT-filter validation harness.

Run 24-72h AFTER 2026-06-24 deploy to test whether the demoted-to-WATCH
cohort actually graduates at the rate the historical backtest predicted.

Hypothesis (from 15K-mint observer backtest):
  • sr 2-3 × SM 6-9 → 11.3% grad rate (BELOW base of 12.0%) → demote
  • sr 2-3 × SM 3-5 → 19.8% grad rate (ABOVE base) → questionable
  • sr ≥ 3 × SM 3-9 → 22-32% grad rate → keep as ACT
  • sr < 2 OR SM 0-2 OR SM 10+ → low → demote

Validating test: of alerts pushed AFTER the 4.65 deploy time, do the
DEMOTED cells actually graduate at the rates the backtest claims?
  • If yes: backtest holds, consider tightening to sr ≥ 3.0
  • If no (demoted alerts graduate at >15%): regime shifted, soften

Run on production:
    fly ssh console -a graduate-oracle -C \\
      'python3 /app/case_study_harness/validate_act_filter_4_65.py'

The 4.65 deploy timestamp is hardcoded — adjust if redeploying changes it.
Returns the demoted-cohort grad rate vs the kept-cohort grad rate, with
sample-size warnings if cells are too thin.
"""
import sqlite3
import time
from datetime import datetime, timezone

DEPLOY_TS = 1782368000   # ~2026-06-24 ~22:30 UTC; bump if backfilling later
MIN_AGE_FOR_RESOLUTION_S = 6 * 3600  # mints need ~6h to resolve grad outcome
DB_PATH = "/data/data.sqlite"


def run():
    now = int(time.time())
    cutoff = now - MIN_AGE_FOR_RESOLUTION_S
    print(f"== 4.65 ACT-filter validation ==")
    print(f"deploy_ts: {DEPLOY_TS} ({datetime.fromtimestamp(DEPLOY_TS, tz=timezone.utc).isoformat()})")
    print(f"window:    [{DEPLOY_TS}, {cutoff}]  ({(cutoff - DEPLOY_TS) / 3600:.1f}h of resolved data)")

    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row

    rows = c.execute(
        """
        SELECT mint, tg_tier, composite_score, threshold_at_cross,
               smart_money_in, did_graduate, peak_mult_24h,
               outcome_resolved_at, predicted_at
          FROM composite_predictions
         WHERE predicted_at BETWEEN ? AND ?
           AND outcome_resolved_at IS NOT NULL
        """,
        (DEPLOY_TS, cutoff),
    ).fetchall()

    if not rows:
        print("no resolved rows in window yet — wait longer")
        return

    # Cohorts as the new 4.65 filter would classify them
    def cell(r):
        thr = r["threshold_at_cross"] or 0
        sm = r["smart_money_in"]
        sr = (r["composite_score"] / thr) if thr > 0 else 0
        sm_in_band = sm is not None and 3 <= sm <= 9
        # Cells:
        #   "kept_act"  → pushed as ACT under 4.65 (sr >= 2 AND SM 3-9)
        #   "demoted"   → previously would have been ACT but filtered out
        #                 (we can't distinguish "would have been ACT" without
        #                  re-running the grad_prob check, so we approximate:
        #                  any alert tagged WATCH that has sr >= 2 OR sm extreme
        #                  was likely demoted by 4.65)
        if sr >= 2.0 and sm_in_band:
            return "kept_act_cell"
        elif (sr >= 2.0 and not sm_in_band) or (sr < 2.0 and 0 < sr):
            return "demoted_cell"
        return "other"

    by_cell = {}
    for r in rows:
        k = cell(r)
        by_cell.setdefault(k, []).append(r)

    base_rate = sum(r["did_graduate"] for r in rows) / len(rows)
    print(f"\nbase grad rate (all crosses in window): {base_rate * 100:.1f}%  (n={len(rows)})")

    print(f"\n== cohort comparison ==")
    for label in ("kept_act_cell", "demoted_cell", "other"):
        sub = by_cell.get(label, [])
        if not sub:
            print(f"  {label:18s}: n=0")
            continue
        grad = sum(r["did_graduate"] for r in sub) / len(sub)
        pk = sum(r["peak_mult_24h"] or 0 for r in sub) / len(sub)
        warn = "  ⚠ thin" if len(sub) < 30 else ""
        print(f"  {label:18s}: n={len(sub):4d}  grad={grad * 100:5.1f}%  avg_peak={pk:.2f}×{warn}")

    # Specific cell of interest: sr 2-3 × SM 6-9 — the big underperformer
    # in backtest that we just demoted. Is it actually underperforming live?
    sub = [
        r for r in rows
        if (r["threshold_at_cross"] or 0) > 0
        and 2.0 <= r["composite_score"] / r["threshold_at_cross"] < 3.0
        and r["smart_money_in"] is not None
        and 6 <= r["smart_money_in"] <= 9
    ]
    if sub:
        grad = sum(r["did_graduate"] for r in sub) / len(sub)
        print(f"\n== sr 2-3 × SM 6-9 (the big demoted bucket) ==")
        print(f"  n={len(sub)}  grad_rate={grad * 100:.1f}%")
        if len(sub) < 30:
            print("  ⚠ sample too thin to draw a conclusion — wait longer")
        elif grad < base_rate:
            print("  ✓ backtest confirmed — this cell underperforms. Tighter filter (sr ≥ 3) justified.")
        else:
            print("  ✗ regime shift — this cell is performing OK in live data. Don't tighten.")


if __name__ == "__main__":
    run()
