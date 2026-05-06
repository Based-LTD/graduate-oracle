# Daily snapshots — paused 2026-05-04

The daily snapshot cadence (calibration metrics, leaderboards, watchlists) is **paused during the retrain transition (2026-05-04 → ~2026-05-07).** This document records the pause transparently rather than letting the cadence break silently.

## Why paused

Two reasons, both grounded in honesty discipline:

1. **The deployed scoring model is in transition.** A retrained GBM v1 with isotonic calibration cascade is currently in shadow mode and approaches user-visible cutover within ~24 hours of this notice. Publishing daily snapshots from the deployed kNN during this window would produce metrics that misrepresent both the soon-to-be-replaced model AND the not-yet-live calibrated successor.

2. **Recent investigations surfaced a calibration finding** ([docs/research/lane7_runner_prob_calibration.md](../docs/research/lane7_runner_prob_calibration.md), [docs/research/lane7_recent_slice_rerun.md](../docs/research/lane7_recent_slice_rerun.md)) showing `runner_prob_*_from_now` is non-stationary at high-confidence bins. The /api/scope description was updated with an honest caveat: the field is directionally accurate but magnitudes are not currently calibrated. Snapshots taken before this finding reflect that miscalibration. Republishing the same shape would compound the misleading-metric problem.

## What's in the historical record

The pre-pause snapshots in `data/2026-04-28/`, `data/2026-04-29/`, and `data/2026-04-30/` reflect:

- The deployed kNN scorer's calibration at that point in time
- Forward production hit rates including the runner_prob fields we have since flagged
- The wallet leaderboard and sniper watchlist data per the original snapshot scope

These remain in the repo as historical record, **not** as current claims. `git log` on these directories shows the actual commit timestamps.

## Resuming

Snapshots resume after Track B cutover lands — the user-visible flip from deployed kNN to calibrated GBM v1 + isotonic cascade. When they resume, scope will be revised:

- **Keep:** `summary.md` and `calibration.json` — the receipts/accuracy moat. Now reflecting calibrated GBM performance.
- **Trim:** `smart-leaderboard.json` — aggregate distributions only, no specific wallet addresses (the wallet reputation index remains proprietary per [docs/methodology.md](../docs/methodology.md)).
- **Remove:** `sniper-watchlist.json` — real-time signal data competitors could front-run.
- **Add:** Merkle ledger commit references — the actual cryptographic receipts trail. Live data already at https://graduateoracle.fun/api/ledger/commits.

## For live data during the pause

- **Live calibration metrics:** https://graduateoracle.fun/api/accuracy
- **Live ledger commits:** https://graduateoracle.fun/api/ledger/commits
- **Live API status:** https://graduateoracle.fun/api/status
- **Methodology:** [docs/methodology.md](../docs/methodology.md)
- **Active research log:** [docs/research/](../docs/research/)
- **Pre-registered decisions and frozen criteria:** [BACKLOG.md](../BACKLOG.md)

## Discipline note

Pausing transparently rather than silently breaking the cadence is itself part of the receipts story. Same principle as pre-registering criteria, publishing negative results, and maintaining the V1→V2→V3 leaf format invariant: when the data we publish would be misleading, we don't publish it. We name what changed, why, and when it resumes.

The cadence resuming with revised scope is part of the same trail: methodology evolves, the receipts evolve with it, and the audit trail captures both.
