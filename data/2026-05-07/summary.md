# Daily snapshot · 2026-05-07

**Snapshot resumed today after pause that ran 2026-05-04 through cutover.** This is the first post-cutover snapshot and it ships with explicit framing about which fields are settled and which are in active acceptance gates. See `data/PAUSED.md` for the resume rationale.

## Aggregate state at snapshot time

```
prediction_window_s:       30
forward.predictions_total: (see calibration.json)
post_graduation.graduates_tracked: 6,847
post_graduation.n_resolved_30m:    6,756
post_graduation.n_sustained:       3,164
post_graduation.sustain_rate_30m:  46.8%
```

The aggregate post-graduation sustain rate is the **independent measurement** — Jupiter price probes at 5/15/30 min checkpoints, not a model output. This number is unaffected by the per-mint `post_grad_survival_prob` field's current sunset (Finding 7 chain).

## Active acceptance gates (state on this snapshot)

Two pre-registered acceptance gates are running concurrently. Either is a possible blocker on user-visible recovery:

### 1. Sustain auto-lift gate (Finding 7f-validation-deferred)

- **Status:** `LIFT_ENABLED=False`. Per-mint sustain returns `warming_clean_corpus_accumulating` for all live mints.
- **Triggers:** corpus reaches ≥60 clean post-fix resolved rows + ≥3 distinct binary signatures (each ≥3 rows), OR 72h post-Finding-7f-deploy elapses (deadline 2026-05-10T16:04:25Z).
- **First validation run today:** CRIT 1 PASS (median NN distance 2.2782 ∈ [0.5, 3.0]) — metric works on clean data. CRITs 2+3 deferred at small corpus size.
- **Receipts:** `docs/research/post_grad_metric_broken_since_launch.md`.

### 2. Bucket calibration interim TG re-enable gate (Finding 8)

- **Status:** rules 9+10 deactivated. EMA smoothing fix shipped 2026-05-07T16:45Z.
- **Interim verdict (frozen):** evaluated 2026-05-09T16:45Z. Acceptance: max 1h MED count ≤30, ≥1 daemon recompute without burst, HIGH any value, no mass-coverage requirement.
- **Full 7d verdict (frozen):** evaluated 2026-05-15T16:45Z. Acceptance: rolling-7d MED in [21, 210], no hour > 10/hr, ≥16/24 hour coverage, HIGH in [1, 15].
- **Receipts:** `docs/research/bucket_calibration_aliasing.md`.

## What's in this snapshot

- **`calibration.json`** — full `/api/accuracy` payload. Includes lifetime k-NN cross-validation thresholds (still running on the deployed scoring index, parallel to the calibrated GBM cascade), forward production samples, post-graduation aggregate, and runner-prob calibration metrics.

## What's NOT in this snapshot (per the trim plan from PAUSED.md)

- **`smart-leaderboard.json`** — wallet leaderboard remains proprietary (the wallet reputation index is the moat). Aggregate distributions may resume in future snapshots; specific addresses won't.
- **`sniper-watchlist.json`** — real-time signal data competitors could front-run. Not republishing.

## Disclaimers honest about state

1. **Bucket distribution under active acceptance gate.** The HIGH/MED/LOW bucket framework is the user-facing alert routing, but the volume-target calibration's EMA smoothing fix shipped <30 minutes before this snapshot. The 7-day acceptance window starts 2026-05-08T16:45Z. Do not infer steady-state bucket behavior from snapshots taken before the acceptance window closes.
2. **Per-mint sustain prediction sunset.** `post_grad_survival_prob` returns `warming_clean_corpus_accumulating` until auto-lift validates. Aggregate `post_graduation.sustain_rate_30m=46.8%` IS valid (independent Jupiter measurement); per-mint signal is not.
3. **runner_prob_*_from_now still flagged.** Per Lane 13, this field is directionally accurate but magnitudes are not currently calibrated. /api/scope reflects this.

## Why resuming today

The pause was originally framed as "2026-05-04 → ~2026-05-07." We're at 2026-05-07. The receipts trail is **stronger now than when the pause started** — eight findings caught and resolved or in active acceptance gates, all publicly timestamped. Resuming with explicit acceptance-gate framing is more honest than extending the pause: it surfaces the active discipline rather than implying everything is broken.

The cadence resumes at the same shape (calibration.json + summary.md), with the disclaimers above replacing implicit "ship clean data" framing. When the acceptance gates close, the disclaimers retire; the data shape stays.
