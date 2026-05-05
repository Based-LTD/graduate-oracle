# Lane 4 — Smart-money post-grad correlation

**Run date:** 2026-05-04
**Status:** BLOCKED → DROPPED (under Lane 1 reframe)
**Pre-registration:** [BACKLOG.md → "Lane 4 — smart-money post-grad correlation"](../../BACKLOG.md)

**Decision thresholds (FROZEN):**
- Ratio (smart-money entries per runner / smart-money entries per rug) ≥ 2× **AND** n ≥ 15 in each bucket → confirmation signal exists. Pre-register a future two-stage gate as a separate validation hypothesis. **Does NOT ship.**
- Ratio 1.5×–2× → marginal, hold for 30 more samples.
- Ratio < 1.5× → no signal, drop the angle.

---

## Hypothesis (verbatim from BACKLOG)

> Post-bond runners (graduated, peak ≥ 2× from grad price within 30m on PumpSwap) have **at least 2×** the rate of smart-money wallet entries in the 0-30m post-grad window vs post-bond rugs.

## Result

**Status: BLOCKED — execution incomplete. No decision applied.**

The execution agent's sandbox did not have the credentials required to (a) read the live production database for post-grad outcomes, or (b) make the volume of JSON-RPC POSTs to a Solana RPC provider needed to cross-reference post-bond transactions against the smart-money index. No data was fabricated. Stopping at the resource boundary was the disciplined choice over inventing numbers.

## What was confirmed without executing

- The pre-registered method is well-formed and the data needed exists in the system. Re-execution from an environment with full credentials would produce a clean result.
- Post-grad outcomes table schema is documented and stable. Per-mint post-bond price trajectory data is recorded.
- The curated smart-money index has 84 active wallets. Pruned wallets (excluded for sustained negative P&L in past reviews) are tracked separately for audit purposes.
- A diagnostic stub was scaffolded for future re-execution.

## Population stats

Not collected — credential-gated DB and RPC access blocked the data pull.

## Aggregate table per bucket

Not produced — depends on the population pull and the on-chain smart-money cross-reference, both blocked.

## Decision per pre-registered thresholds

**No decision applied.** The pre-registered rule requires data; with no data, applying any of the three branches would be fraudulent. The criterion remains as stated above for future re-execution.

## Subsequent fate of this lane

Lane 4 was **dropped** later the same day, not deferred. The rationale: under the Lane 1 reframe, the smart-money correlation question shifted from load-bearing ("is there a confirmation signal at graduation moment that distinguishes runners from rugs?") to lower-priority. With Lane 1 confirming the model has a fixable selection bias (missing 87% of pump.fun's actual graduation pool), the higher-leverage work moved to fixing the model itself rather than searching for an external confirmation signal.

The blocked-then-dropped sequence is documented as evidence of disciplined re-prioritization: when an experiment is gated on resources you don't have AND another finding makes that experiment less central, the right move is to drop it cleanly with a writeup rather than execute it from inertia.

## Caveats and limitations (preserved for any future re-execution)

- **Sandbox restriction was environmental, not methodological.** The criterion is intact and ready for re-run.
- **Classification edge cases.** Pre-registered: runner = `max(price_5m, price_15m, price_30m) / grad_price ≥ 2.0`; rug = same ratio `< 1.0`; middle (1.0–2.0) is dropped to keep classification clean. Mints with NULL probe prices are dropped.
- **Smart-money definition is membership-based, not behavior-based.** A "smart-money entry" = any tx where one of the indexed wallets is the buyer signer (or executes a swap as fee-payer/owner of a token-account-init). Post-grad PumpSwap swaps use AMM-side signers, not curve-buy accounts; implementer should verify the parsed-tx field path with a single known-good case before bulk run.
- **Underestimation risk.** A wallet may interact with a mint via a delegate/program account, not its own keypair — these would be missed by a naive signer-only scan.
- **No survivorship lookback.** The 7-day window must be `now() - 7d` AT THE TIME OF EXECUTION, not a fixed timestamp. Re-runs should re-fetch.

## R&D discipline reminders honored

- No proposal to ship anything.
- No amendment to any pre-registered criterion.
- Stopped at the resource boundary instead of fabricating numbers.
- Negative-or-blocked result published per the spec's "writeup regardless" rule.
- Lane was subsequently DROPPED, not deferred — the disciplined response to "no longer load-bearing" is documented removal, not silent pause.
