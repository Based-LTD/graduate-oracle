# Lane 11 — Observer collection-leak diagnostic

**Run date:** 2026-05-05 evening
**Pre-registration:** [BACKLOG.md → "Lane 11 — observer collection-leak diagnostic"](../../BACKLOG.md)
**Decision criteria (frozen pre-run):**
- Single mechanism explains >50% of misses → targeted fix
- Multiple mechanisms each <50% → broader observer reliability work
- No clear pattern → instrumentation gap

---

## Headline

**Single dominant mechanism IS present — but it's NOT the zombie-eviction config tweak we hoped for.**

The leak is **observer subscription downtime during specific UTC hours.** Hours 03-06 UTC and 16-18 UTC see miss rates of **59-90%**, while hours 07-14 UTC see **1-7%** miss rates. Roughly **57% of all misses come from these specific 5-6 hour windows.**

**Decision per pre-registered rule:** single mechanism explains >57% of misses → targeted fix. But the fix is **observer reliability work** (subscription health monitoring, RPC redundancy, auto-reconnect), NOT a config tweak. The "easy fix" path (raise `min_trades_to_save` threshold) is rejected.

## What the eviction hypothesis predicted vs what we found

**If the `<3 trades` eviction were the mechanism**, we'd expect missing mints to have systematically lower trade activity at graduation than captured mints — sparse-trade zombies that evicted before they could mature.

**What we actually see:**

| Feature | Captured non-bundled (n=1,789) | Missing non-bundled (n=845) |
|---|---:|---:|
| feature_unique_buyers (median) | 9 | **8** |
| feature_unique_buyers (mean) | 22.5 | **24.6** |
| feature_unique_buyers (p25) | 6 | **5** |
| feature_unique_buyers (p75) | 13 | **13** |
| feature_vsol_velocity (median) | 105.4 | **96.5** |

The distributions are essentially the same. Missing mints are NOT sparse-activity. **The eviction hypothesis is rejected.**

Only 2.3% of missing mints have NO/zero feature_unique_buyers, and only 19.8% have feature_unique_buyers < 5. The vast majority of missing mints had real activity at graduation — but observer didn't capture their curves anyway.

## What the data DID show: hour-of-day pattern

| Hour UTC | Missing | Captured | Miss rate |
|---:|---:|---:|---:|
| 00 | 85 | 170 | 33.3% |
| 01 | 77 | 138 | 35.8% |
| 02 | 77 | 123 | 38.5% |
| **03** | **119** | **82** | **59.2%** |
| **04** | **114** | **71** | **61.6%** |
| **05** | **136** | **15** | **90.1%** |
| **06** | **115** | **68** | **62.8%** |
| 07 | 16 | 135 | **10.6%** |
| 08 | 6 | 140 | **4.1%** |
| 09 | 2 | 123 | **1.6%** |
| 10 | 3 | 136 | **2.2%** |
| 11 | 2 | 139 | **1.4%** |
| 12 | 2 | 150 | **1.3%** |
| 13 | 10 | 135 | **6.9%** |
| 14 | 4 | 151 | **2.6%** |
| 15 | 35 | 146 | 19.3% |
| **16** | **106** | **53** | **66.7%** |
| **17** | **142** | **71** | **66.7%** |
| **18** | **146** | **84** | **63.5%** |
| 19 | 115 | 132 | 46.6% |
| 20 | 120 | 104 | 53.6% |
| 21 | 43 | 200 | 17.7% |
| 22 | 30 | 185 | 14.0% |
| 23 | 38 | 188 | 16.8% |

**Two clear bands:**
- **High-loss windows (>50% miss):** 03-06 UTC, 16-18 UTC. Some hours peak at 90%.
- **Stable windows (<10% miss):** 07-14 UTC. Observer is essentially fully capturing during these 8 hours.

This is not eviction — it's the observer being **disconnected, throttled, or otherwise impaired during specific hours of the day.** The pattern is too clean and too clustered to be random failures or per-mint characteristics.

**Hypotheses for the mechanism (none individually pre-registered yet):**
- **RPC provider maintenance windows:** Solana RPC nodes (Helius, etc.) have scheduled maintenance, often at specific UTC times. logsSubscribe streams may drop during these windows.
- **Network instability:** specific UTC times correlate with regional network maintenance, peering changes, or BGP re-convergence.
- **Fly machine restarts/deploys:** if our deploys consistently happen at certain hours and the observer doesn't fully resync after restart, that maps directly to this pattern.
- **Rate limiting:** RPC provider rate limits could trip at peak-usage hours.

## Trend over time (encouraging sub-finding)

| Window | Missing | Captured | Miss rate |
|---|---:|---:|---:|
| Last 3 days (May 2-4) | 1,130 | 2,552 | **30.7%** |
| Before 3 days | 413 | 387 | **51.6%** |

**The leak is getting better.** May 2 was the observer trade-loss + clean restart per [memory: project_observer_clean_restart](../..//memory). Whatever changed then has cut the miss rate from ~52% to ~31%. But ~31% is still material. The remaining loss correlates strongly with the hour-of-day pattern above.

## Bonus finding: classification of missing mints

| Missing subset | Count | Share of missing |
|---|---:|---:|
| Classifiable non-bundled | 848 | 55.0% |
| Classifiable bundled | 0 | 0.0% |
| Unknown classification (no predictions row) | 535 | 34.7% |
| (Other / partial classification) | 160 | 10.4% |

**Zero of the 1,543 missing mints are bundled.** Bundled mints all get captured. The leak is essentially 100% non-bundled — which is consistent with bundled mints having more concentrated trade activity that triggers observer attention faster (and avoids any eviction window).

This sharpens the bias finding: it's not just "the model misses non-bundled," it's **"the observer literally never sees a meaningful fraction of non-bundled graduators in the first place."** The selection bias starts at the ingest layer.

## Decision per pre-registered rule

**Single mechanism explains >50% of misses** (57% from 03-06 UTC + 16-18 UTC windows alone). **Targeted fix is justified.**

But the fix character has changed: NOT a config tweak (eviction threshold), but **observer subscription reliability** during specific hours. Two paths from here:

### Path A: Diagnose the specific cause, fix it directly (narrow scope)
Investigate observer logs for the 03-06 UTC and 16-18 UTC windows of recent days. Look for:
- Subscription drop events
- Reconnection attempts
- RPC errors with timestamps
- Fly machine activity (restarts, deploys, healthchecks failing)
- ~~Helius status page for scheduled maintenance windows~~ **(checked 2026-05-05 evening — see below)**

If the cause is identifiable from logs (e.g., "every day at 05:00 UTC the logsSubscribe websocket reconnects with 30+ seconds of unbridged time"), the fix is targeted.

### Helius + Solana status check (5-min sub-investigation, 2026-05-05 evening)

Hypothesis: the catastrophic UTC windows correspond to scheduled maintenance at our upstream RPC provider. Tested by reading the Helius public status page (`helius.statuspage.io`) and Solana mainnet status (`status.solana.com/history.json`).

**Helius status:**
- All systems operational, 99.99-100% uptime over 90 days
- Recent incidents (last 30 days): 5 isolated, sporadic — not recurring patterns
  - Apr 29 13:47 UTC (devnet WS), Apr 29 02:55 UTC (devnet bad-request), Apr 26 22:03 UTC (devnet latency), Apr 25 04:48 UTC (Frankfurt RPC partial), Apr 22 17:24 UTC (LaserStream FRA)
- No scheduled maintenance listed
- **No 12-hour repeating pattern detected**

**Solana status:** zero incidents reported in last 30 days on mainnet.

**Conclusion: external upstream maintenance does NOT explain the pattern.** The fix-scoping shifts:

- ❌ NOT "RPC redundancy / failover during scheduled maintenance"
- ✅ More likely: our own observer's subscription/reconnection behavior. Possibilities:
  - Long-lived websocket connections being severed by load balancers on a periodic schedule (AWS NLB / Cloudflare connection rotation can be 12h)
  - Memory pressure or connection-pool growth causing observer to slow / drop messages on a daily cycle
  - Fly machine auto-suspend or healthcheck-induced restarts at specific hours
  - Rate-limit window resets that misalign with continuous traffic

This makes Path A's fly logs analysis MORE valuable — it has to find the cause locally rather than blame the upstream. Estimated time: ~1-2h with focused log examination during the catastrophic windows of recent days.

### Path B: Build observer reliability instrumentation (broader scope)
If logs don't immediately reveal the cause, instrument the observer:
- Heartbeat metric: trades/second over 1-minute windows, alerted on >2-minute gaps
- Subscription state tracker: log every connect / disconnect / reconnect with reason
- Per-RPC-method success/failure rates with timestamps
- Cross-reference to mint discovery rate

Then re-run Lane 11 in a week with proper telemetry.

**Recommendation (NOT a decision — that's tomorrow's pre-registration):** start with Path A. ~2 hours of `fly logs` examination and Solana RPC dashboard checking should reveal whether there's a smoking gun. If not, escalate to Path B.

## What this changes for retrain scoping

The Layer 1 (collection leak) workstream still happens, but its character is different than originally framed:

- ❌ NOT "raise the eviction threshold"
- ✅ INVESTIGATE the 03-06 UTC and 16-18 UTC subscription drops
- ✅ Likely a separate sub-investigation needed before any fix can be pre-registered

**This means Layer 1 is harder than the eviction-fix easy path would have been**, but it's also more targeted than "broad observer reliability work." Specific windows = specific cause to find. Pre-registration for the sub-investigation should land tomorrow before any logs work.

The retrain itself doesn't depend on Layer 1 fixing — model can train on the captured 65.6% subset. Just acknowledge that **the live deployed model can't fire on the missing 30%+ of non-bundled graduations until Layer 1 is fixed.**

## Caveats

- The "miss rate" calculation assumes `post_grad_outcomes` itself has fair coverage. If mints in the leak windows ALSO don't make it into post_grad_outcomes, the actual leak is even worse. This was assumed but not verified — `post_grad_outcomes` is populated by post_grad_tracker which polls Jupiter prices independently of the observer's curve capture, so its coverage is likely better, but not perfect.
- The 535 "unknown classification" missing mints (no predictions row) include some that may be bundled — we just can't tell. The "100% non-bundled in missing" claim applies only to the classifiable subset.
- Hour-of-day pattern is computed across all days. The pattern could be driven primarily by 1-2 anomalous days. The CV of 22.8% on daily miss rates suggests some clustering but not extreme.
- The trend (52% → 31%) over 3 days is suggestive but n_old (May 2 and earlier in the window) is small.
- The eviction hypothesis was tested via feature counts, which is a proxy — not a direct test of "did this mint get evicted." The direct test would correlate observer eviction events to specific mints, which requires log analysis. The proxy is reasonable evidence but not conclusive.

## Numerical summary saved to `/tmp/lane11_summary.json` (on Fly)
