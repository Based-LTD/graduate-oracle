# Daily snapshot pause — RESUMED 2026-05-07

The daily snapshot cadence was paused 2026-05-04 during the calibrated-GBM cutover transition. **It resumes today (2026-05-07).**

This file now records the pause + resume transparently, replacing the original "paused indefinitely" framing.

## Pause window

```
Paused:   2026-05-04
Resumed:  2026-05-07 (this commit)
Duration: ~3.5 days
```

## What happened during the pause

The cutover sequence shipped the calibrated GBM v1 + isotonic cascade + HIGH/MED/LOW bucket framework. **Eight findings were caught** during and after the cutover, all publicly timestamped with diagnoses predating their fixes:

- **Findings 1-5** (pre-cutover): LOG_THRESHOLD validation gap, alert-rule kind mismatch, GBM bimodal cliff, kNN saturation, multi-issue surfacing.
- **Finding 6**: verification-by-content meta-rule landed (counting alerts ≠ verifying alert content).
- **Finding 7** (chain): `post_grad_survival_prob` discovered to have been publishing artifacts since launch (snapshot-source bug; 3 of 5 features writing zero). Two metric replacements (Path C, Path D2) failed pre-registered acceptance criteria. Path E sunset executed. Root cause located + corrected fix shipped (Finding 7f). Currently in clean-corpus auto-lift gate.
- **Finding 8**: bucket calibration aliasing during daemon recompute window (697-in-1h MED burst diagnosed). EMA smoothing + persistence sidecar shipped. Interim 48h TG re-enable gate at 2026-05-09T16:45Z; full 7d acceptance at 2026-05-15T16:45Z.

13 commits in the last 28 hours, all timestamped, all on `github.com/Based-LTD/graduate-oracle`.

## Resuming with explicit gate framing

Today's snapshot ([`2026-05-07/`](2026-05-07/)) ships with the receipts narrative attached: aggregate stats are independent and valid; per-mint sustain is sunset and warming on clean corpus; bucket distribution is in active acceptance gate. **The pause didn't end because everything is fixed — it ended because the receipts trail is stronger NOW than when the pause started, and continuing the pause obscures the active discipline rather than surfacing it.**

When the acceptance gates close (sustain auto-lift validates OR escalates to Finding 7g/7h; Finding 8 interim and full gates pass OR Path E executes), the disclaimers in `summary.md` retire and the cadence continues at the same shape.

## Scope changes from pre-pause snapshots (per original resume plan)

- **Keep:** `calibration.json` + `summary.md`. The receipts/accuracy moat.
- **Trim:** `smart-leaderboard.json` — aggregate distributions only in future snapshots, no specific wallet addresses (the wallet reputation index remains proprietary). NOT included in 2026-05-07 snapshot to keep this clean.
- **Remove:** `sniper-watchlist.json` — real-time signal data competitors could front-run. NOT included in 2026-05-07 snapshot.
- **Add (this resume):** explicit pre-registered-gate disclosure in `summary.md`. Both the sustain auto-lift gate and the Finding 8 interim/full TG re-enable gates have public commit hashes pointing at the frozen acceptance criteria.

## Pre-pause snapshots remain as historical record

The snapshots in `data/2026-04-28/`, `data/2026-04-29/`, and `data/2026-04-30/` remain in the repo as historical record, **not** as current claims. They reflect the pre-cutover deployed kNN scorer's calibration at those times. `git log` on these directories shows actual commit timestamps.

## For live data while gates are running

- **Live calibration metrics:** https://graduateoracle.fun/api/accuracy
- **Live ledger commits:** https://graduateoracle.fun/api/ledger/commits
- **Live API status:** https://graduateoracle.fun/api/status
- **Methodology:** [`docs/methodology.md`](../docs/methodology.md)
- **Active research log:** [`docs/research/`](../docs/research/)
- **Pre-registered decisions and frozen criteria:** [`BACKLOG.md`](../BACKLOG.md)

## Discipline note

Pausing transparently rather than silently breaking the cadence was part of the receipts story. **Resuming transparently with active-gate framing is the same discipline applied at the un-pause point.** When the data we publish would be misleading, we don't publish it. When publishing it WITH disclosed-active-gate framing is more useful than continuing to pause, we publish it with that framing.

The cadence resuming with revised scope is part of the same trail: methodology evolves, the receipts evolve with it, and the audit trail captures both.
