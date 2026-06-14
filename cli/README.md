# goracle

> graduate-oracle CLI — the pump.fun graduation prediction API, from your terminal.

Sign up, pay in SOL, and use the live API without ever leaving the shell.

## Install

```bash
# zero install (recommended)
npx goracle signup builder

# or global
npm install -g goracle
```

Requires Node 18+.

## Quickstart

```bash
$ npx goracle signup builder
graduate-oracle signup — builder (monthly)
Creating payment intent…

Scan with Phantom (mobile)
[QR code rendered in terminal]

Or send manually:
  Amount:    0.4 SOL
  To wallet: G491Ae...
  Memo:      g_M96YSm04BT0

⚠  Your API key (saved to ~/.config/goracle/config.json):
   grad_xxxxxxxxxxxxxx
   Activates the moment payment confirms (~30s after broadcast).

Polling /api/upgrade/g_M96YSm04BT0 every 5s…
  status → pending
  status → fulfilled
✓ Payment confirmed.
  Tier:   builder
```

That's it. You're paid, your key is saved, and the API is live.

## Commands

### `goracle signup [tier] [--plan monthly|yearly] [--email you@x.com]`

Mint a new API key and pay for it via in-terminal QR. Defaults: `tier=builder`, `plan=monthly`. Founding rate locks forever.

If your terminal closes mid-payment, `goracle resume` picks up where you left off.

### `goracle accuracy [--json]`

Live calibration receipt — no key required. Shows the 30-day hit rate (mints we score ≥70% in their first 60 seconds that actually graduated), lifetime number, and month-over-month trajectory.

```bash
$ goracle accuracy
graduate-oracle · live calibration
predictions at age 30s or 60s with grad_prob ≥ 0.70

Last 30 days:
  hit rate: 90.5%
  resolved: 295 calls
  graduated: 267

Lifetime:
  hit rate: 57.4%
  resolved: 4,096 calls

Monthly trajectory:
  2026-04: 54.6% (n=1,670)
  2026-05: 57.9% (n=2,323)
  2026-06: 92.2% (n=103)
```

Every prediction is publicly hashed before the outcome is known. Verify the chain at <https://graduateoracle.fun/verdict>.

### `goracle probe <mint> [--json]`

Score a single mint right now. Returns the full feature breakdown — grad_prob, current multiple, vSOL, buyer count, all the flags (bundled, manufactured, dex-paid, fee-delegated).

```bash
$ goracle probe ABC123...pump
ABC123…pump  ABC123QXYZ...pump

  grad_prob:    87.3%  (age 45s)
  bucket:       HIGH
  current mult: 1.42×
  vSOL:         42.3 SOL
  unique buys:  127
  dex paid:     yes
```

### `goracle live [--threshold 0.70] [--max-mult 2.0] [--limit 25] [--once] [--json]`

Stream every currently-tracked mint in a live-updating terminal table. Filter by `grad_prob` and entry quality, sort by confidence, top N.

```bash
$ goracle live --threshold 0.70 --max-mult 2.0
graduate-oracle · live  tracked=1,247  indexed=969,847  snapshot=3s
filter: grad_prob ≥ 0.7, mult ≤ 2, top 25

grad    mult   vSOL   buys   age   mint              flags
─────   ────   ────   ────   ───   ───────────────   ─────
89.2%   1.31×  38.4   89     38s   ABC123…pump       DEX FEE
84.7%   1.18×  29.1   62     41s   DEF456…pump       BNDL
...

refreshing every 2s · Ctrl-C to exit
```

`--once` for a single fetch; `--json` for piping to `jq`.

### `goracle me [--json]`

Show your saved key, current tier, expiry, and today's API call count.

### `goracle config [--clear] [--set key=value]`

Inspect the config file, override a setting (e.g. `--set apiBase=https://staging.graduateoracle.fun` for testing), or wipe everything.

## How payment works

This CLI doesn't hold your funds and doesn't ask for a private key.

1. `goracle signup` POSTs to `https://graduateoracle.fun/api/upgrade`, which:
   - Mints a fresh API key bound to a payment intent
   - Returns a [Solana Pay](https://solanapay.com) deeplink + memo
2. The CLI renders the deeplink as a QR. You scan it with Phantom (or Solflare / Backpack) on mobile, OR send the SOL manually with the memo.
3. The CLI polls `/api/upgrade/{memo}` until the on-chain transaction confirms.
4. Tier upgrade is applied automatically. Your key is saved to local config.

The intent is short-lived (~30 min TTL). If you close the terminal before paying, run `goracle resume` to pick back up.

## Tiers

| Tier      | Calls/day | Webhooks | Price            |
|-----------|-----------|----------|------------------|
| Builder   | 5,000     | —        | 0.4 SOL/month    |
| Pro       | 50,000    | ✓        | 1 SOL/month      |
| Enterprise| custom    | ✓        | contact          |

Yearly plans get 17% off. Founding rates lock forever — your price never goes up.

## Pay with $GO instead

Hold **500,000 $GO** in a linked Solana wallet for TG composite signal access, or **2,500,000 $GO** for Builder API access. Token-holder tier is real-time RPC-checked; sell → drops back. No double-charge.

Token launches on [Proof Launch](https://prooflaunch.fun). See [graduateoracle.fun/for-terminals](https://graduateoracle.fun/for-terminals) for tokenomics.

## What graduate-oracle is

A calibrated probability score on every pump.fun mint in its first 60 seconds, plus 5 differentiated signals (smart-money entries, runner-creator detection, copy-trade triggers).

Every prediction is logged with a SHA256 commitment **before** the outcome is observable. Live calibration is published at `/api/accuracy` and visible at <https://graduateoracle.fun/accuracy>. Pre-registered 14-day verdicts run quarterly — chain at <https://graduateoracle.fun/verdict>.

## Links

- Site: <https://graduateoracle.fun>
- Docs: <https://graduateoracle.fun/docs>
- Live receipt: <https://graduateoracle.fun/accuracy>
- Pre-registered verdict: <https://graduateoracle.fun/verdict>
- TG bot: <https://t.me/graduate_oracle_bot>

## License

MIT
