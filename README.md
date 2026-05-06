# $GRADUATE — pump.fun graduation oracle

**Live graduation probability + smart-money intelligence for every pump.fun mint.**

A real-time scoring engine over a 60,000+ historical curve dataset, served as a
public API. Every mint that lands on pump.fun gets a probability of graduation
(reaching the bonding-curve threshold and migrating to Raydium), plus
buyer-quality metrics drawn from a 200,000+ wallet reputation index.

This repository is the **public face** of the project: documentation, working
example code, regular accuracy snapshots (paused during model transitions —
see [`data/PAUSED.md`](data/PAUSED.md)), the active research log, and the
methodology behind the model. The scoring engine itself is closed-source.

```
↪  Live dashboard:  https://graduateoracle.fun
↪  API docs:        https://graduateoracle.fun/api
↪  Telegram bot:    https://t.me/graduate_oracle_bot
↪  X / Twitter:     https://x.com/GraduateOracle
```

---

## What this repo is for

| If you are a… | Start here |
|---|---|
| **Trader** | Open the [live dashboard](https://graduateoracle.fun) or chat with [@graduate_oracle_bot](https://t.me/graduate_oracle_bot). |
| **Developer building a bot** | [`examples/sniper.py`](examples/sniper.py), [`examples/tg-alerter.py`](examples/tg-alerter.py). Get a free API key in one click at [graduateoracle.fun/api](https://graduateoracle.fun/api). |
| **Quant / researcher** | Read [`docs/methodology.md`](docs/methodology.md). Daily accuracy snapshots in [`data/`](data/). |
| **Token holder** | The accuracy ledger and paper-trading P&L are committed here daily — `git log data/` to see how the model has actually performed over time. |

---

## What's in this repo

```
graduate-oracle/
├── docs/
│   └── methodology.md              ← high-level model + data approach
├── examples/
│   ├── sniper.py                   ← buy-on-signal scaffold (paper-mode by default)
│   ├── paper-bot.py                ← replicate our paper trading harness
│   ├── tg-alerter.py               ← custom Telegram alerts on signal
│   └── agent.py                    ← consume the firehose for an LLM agent
├── data/
│   └── YYYY-MM-DD/
│       ├── calibration.json        ← lifetime + forward accuracy snapshot
│       ├── paper-trades.json       ← every paper trade closed that day
│       ├── smart-leaderboard.json  ← top wallets, frozen
│       └── sniper-watchlist.json   ← worst snipers, frozen
├── LICENSE                         ← MIT (examples + docs · scoring engine is proprietary)
└── README.md
```

---

## Quick start (60 seconds)

```bash
# 1. Get a free key — instant, no email required
open https://graduateoracle.fun/api

# 2. Hit the live firehose
curl -H "Authorization: Bearer grad_your_key_here" \
     "https://graduateoracle.fun/api/v1/live?limit=10&min_prob=0.7"

# 3. Probe a single mint
curl -H "Authorization: Bearer grad_your_key_here" \
     "https://graduateoracle.fun/api/v1/probe/<mint_address>"
```

Every endpoint returns the same enriched shape: graduation probability, k-NN
context, buyer-quality signals, sybil/bot detection flags, and the activity
health of the curve.

---

## How accurate is it?

We publish two layers of proof, both updated continuously:

**1. Cross-validated lifetime calibration.** For a sample of every historical
curve we've ever indexed, we predict its outcome using a leave-one-out k-NN
(the test curve is masked from the neighbor pool, so it can't predict itself).
Results: <https://graduateoracle.fun/api/accuracy>

**2. Forward production predictions.** Every live prediction the API ever
returns is recorded in append-only storage. When the mint resolves
(graduates → migrates to Raydium, or dies → flushed by the observer with
no migration), the prediction is updated with the actual outcome. After 30
days of accumulation this gives us a number that is, by construction,
impossible to fudge.

Daily snapshots of both numbers land in [`data/`](data/) on every commit.

---

## Pricing

| Tier | Cost | What you get |
|---|---|---|
| **Free** | 0 SOL | 200 calls/day · 1 alert rule · 5 watchlist mints |
| **Pro** | 0.1 SOL/mo | 50,000 calls/day · WebSocket · unlimited alerts/watchlist · webhooks |

Pay in SOL via Phantom. No card. No email. Activates within ~30 seconds of
on-chain confirmation. Cancel by not renewing.

[Get a key →](https://graduateoracle.fun/api)

---

## License

The contents of this repository are released under the [MIT License](LICENSE).
The closed-source scoring engine, bot-detection thresholds, and live
indexing infrastructure are not included here.

You may freely fork, modify, and use any code in this repo. You may not
claim any affiliation with $GRADUATE that you do not have.
