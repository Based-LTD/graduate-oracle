# Daily snapshots

Auto-committed daily by the prod web service. Each `YYYY-MM-DD/` folder holds
the day's frozen calibration, leaderboards, and paper trading results.

| File | What it contains |
|---|---|
| `calibration.json` | `/api/accuracy` snapshot — both lifetime cross-validation and forward production calibration |
| `paper-trades.json` | every paper trade closed that day, with prob/entry/exit/pnl |
| `smart-leaderboard.json` | top 100 smart-money wallets, frozen |
| `sniper-watchlist.json` | top 100 worst snipers, frozen |
| `summary.md` | human-readable daily overview |

Run `git log data/` to see the project's accuracy track record over time.
