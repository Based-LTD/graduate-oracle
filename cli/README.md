# goracle

> graduate-oracle CLI — pipe pump.fun graduation alerts into your bot, from your terminal.

Real-time graduation alert API for sniper bots and fast traders. Sign up, pay in SOL, and stream the live signal — without ever leaving the shell.

## What this is

`goracle` is the command-line interface to **graduate-oracle**, a real-time pump.fun graduation alert API. We detect bonding-curve graduations *before* they complete — median runway between our ≥0.70 confidence call and the curve closing is **seconds**, enough for any sub-second execution stack to enter on the curve before migration.

Built for:

- **Sniper bots** — pipe `goracle live --json` into your trade engine
- **Fast traders** — TG one-tap entry within the runway window
- **DEX aggregators / routing** — graduation-aware liquidity migration
- **Trading terminals** — embed the alert feed as a paid feature

Not for: manual Phantom users (window is too tight for app-switching).

Every prediction is logged with a SHA256 commitment **before** the outcome is observable. Pre-launch audit on <https://graduateoracle.fun/verdict>.

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

### `goracle signup [tier] [--plan monthly|yearly] [--email] [--wallet]`

Mint a new API key and pay for it via in-terminal QR. Defaults: `tier=builder`, `plan=monthly`. Founding rate locks forever.

To skip SOL pay and sign up as a $GO token-holder: `goracle signup builder --wallet 7xKp...` — opens a browser, signs a message with Phantom, server checks your $GO balance, mints a key bound to your wallet.

If your terminal closes mid-payment, `goracle resume` picks up where you left off.

### `goracle accuracy [--json]`

Live receipts — no key required. Shows median runway, 30-day calibration accuracy, and monthly trajectory.

```bash
$ goracle accuracy
graduate-oracle · live receipts
predictions at age 30s or 60s, calibrated, ≥ 0.70 confidence

Last 30 days:
  median runway:  15s   (between our call and graduation completing)
  hit rate:       91.2% (of those calls graduate within 24h)
  resolved:       295 calls

Lifetime:
  hit rate: 57.4%
  resolved: 4,096 calls

Monthly trajectory:
  2026-04: 54.6% (n=1,670)
  2026-05: 57.9% (n=2,323)
  2026-06: 92.2% (n=103)

Receipts: https://graduateoracle.fun/accuracy
Pre-launch audit: https://graduateoracle.fun/verdict
```

### `goracle probe <mint> [--json]`

Score a single mint right now. Returns the full feature breakdown — confidence, current multiplier, vSOL, buyer count, all the flags.

```bash
$ goracle probe ABC123...pump
ABC123…pump  ABC123QXYZ...pump

  grad_prob:    87.3%  (age 45s)
  current mult: 1.42×
  vSOL:         42.3 SOL
  unique buys:  127
  dex paid:     yes
```

### `goracle live [--threshold 0.70] [--max-mult] [--limit] [--once] [--json]`

Stream every currently-tracked mint in a live-updating terminal table. Filter by confidence and entry quality, sort by confidence, top N. **`--json` is the pipe-into-your-bot mode** — single fetch, raw JSON, no UI.

```bash
# UI mode (refreshes every 2s)
$ goracle live --threshold 0.70

# Bot pipeline mode
$ goracle live --threshold 0.70 --json | jq '.mints[]'
```

### `goracle me [--json]`

Show your saved key, current tier, expiry, today's API call count.

### `goracle config [--clear] [--set key=value]`

Inspect the config file, override a setting (`--set apiBase=https://staging.graduateoracle.fun`), or wipe everything.

## How payment works

This CLI doesn't hold your funds and doesn't ask for a private key.

1. `goracle signup` POSTs to `https://graduateoracle.fun/api/upgrade`, which mints a fresh API key bound to a payment intent and returns a [Solana Pay](https://solanapay.com) deeplink + memo.
2. The CLI renders the deeplink as a QR. Scan it with Phantom (or Solflare / Backpack) on mobile, OR send the SOL manually with the memo.
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

Hold **500,000 $GO** in a linked Solana wallet for TG signal access, or **2,500,000 $GO** for Builder API access. Token-holder tier is real-time RPC-checked; sell → drops back. No double-charge.

Token launches on [Proof Launch](https://prooflaunch.fun). See [graduateoracle.fun/for-terminals](https://graduateoracle.fun/for-terminals) for tokenomics.

## The receipts story

graduate-oracle indexes every pump.fun mint and publishes every prediction with a SHA256 commitment **before** the outcome is observable. The pre-launch audit (see [/verdict](https://graduateoracle.fun/verdict)) caught a measurement bug in our original headline metric and we corrected it before launch. The receipts chain working in public is the moat.

## Links

- Site: <https://graduateoracle.fun>
- Docs: <https://graduateoracle.fun/docs>
- Live receipts: <https://graduateoracle.fun/accuracy>
- Pre-launch audit: <https://graduateoracle.fun/verdict>
- TG bot: <https://t.me/graduate_oracle_bot>

## License

MIT
