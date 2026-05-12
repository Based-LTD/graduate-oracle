# Audit 14 — Organic-followon flag validation (results)

**Verdict commit.** Pre-reg at [`audit_14_organic_followon_flag_prereg.md`](audit_14_organic_followon_flag_prereg.md) (commit `9f6cc4f`, ~1h before this commit).

**Verdict:** **PARTIAL under strict pre-registered branching, with a substantive secondary finding that reshapes the flag's interpretation.** Lift of 2.00× exactly at the high-magnitude stratum — at the upper boundary of the PARTIAL band but CIs overlap by 0.2pp. The strict reading is PARTIAL. The substantive reading: the flag is real but its semantic shifts from "rug filter" to **"high-variance / extreme-outcome filter"** because flag-fires also has higher graduation rate.

---

## Sample

- Window: last 20 days, mints with `actual_max_mult IS NOT NULL`
- 36,646 deduped candidates → sampled 5,000 random → processed 3,860 (failed lookups: 1,133 no_curve, 7 parse_fail)
- Curve JSONs parsed for `t_s`, `is_buy`, `sol_amount` (lamports)
- `sol_spent_first_2s` and `sol_spent_first_5s` computed retroactively from trade arrays

## Per-stratum metrics (frozen 2x3 design)

| flag_fires | magnitude | n | rug_rate | 95% CI | grad_rate | 95% CI | peak_p50 | peak_p90 |
|---|---|---:|---:|---|---:|---|---:|---:|
| False | low (sol_5s < 1) | 956 | 0.21% | [0.1, 0.8] | 12.45% | [10.5, 14.7] | 2.64× | 10.28× |
| False | mid (1 ≤ sol_5s < 4) | 316 | 0.00% | [0.0, 1.2] | 9.18% | [6.5, 12.9] | 2.72× | 9.26× |
| False | **high (sol_5s ≥ 4)** | **1,748** | **2.12%** | [1.5, 2.9] | 6.46% | [5.4, 7.7] | 2.61× | 7.28× |
| True | low (sol_5s < 1) | 273 | 0.37% | [0.1, 2.0] | 10.26% | [7.2, 14.4] | 1.95× | 6.27× |
| True | mid | 165 | 1.82% | [0.6, 5.2] | 7.27% | [4.2, 12.3] | 2.19× | 5.57× |
| True | **high (sol_5s ≥ 4)** | **402** | **4.23%** | [2.7, 6.7] | 12.44% | [9.6, 16.0] | 3.21× | 11.17× |

Sample size: per-cell n ≥ 200 at high-magnitude tier ✓ (1,748 + 402 = 2,150)

## H2 test (high-magnitude only) — the headline comparison

```
flag=fires, sol_5s ≥ 4:           n=402   rug=4.23%   CI=[2.7, 6.7]
flag=doesnt-fire, sol_5s ≥ 4:     n=1748  rug=2.12%   CI=[1.5, 2.9]

Rug-rate lift:                    2.00× exactly
CI overlap:                       Yes (boundary touches at 2.7%–2.9%)
```

## Verdict under pre-registered branches

| Branch | Conditions | Status |
|---|---|---|
| VALIDATED | (1) n ≥ 200/cell ✓, (2) lift ≥ 2.0× ✓ (at boundary), (3) non-overlapping CIs ✗ | **Fails on CI overlap** |
| PARTIAL | (1) sample OK ✓, (2) lift in [1.3, 2.0) ✗ (we have 2.00×, just outside range) | Boundary case |
| MAGNITUDE_DEPENDENT | high-mag passes VALIDATED, all-mag fails | Not strictly — high-mag also fails (CI) |
| NOT_VALIDATED | flat / inverse / overlap-across-all | Not — there IS a lift |

**Closest-fit branch: PARTIAL.** The lift is real but at the edge of statistical detectability with n=402 in the flag-fires high-magnitude cell. The 0.2pp CI gap (2.9% upper for non-fires vs 2.7% lower for fires) is small enough that with n doubling to ~800, the CIs would likely separate and VALIDATED would fire.

## Substantive finding (NOT in the pre-reg's anticipated outcomes)

**The flag is not purely a rug filter — it's a high-variance / extreme-outcome filter.** Look at the high-magnitude comparison ROW BY ROW:

| Metric | flag=False (high) | flag=True (high) | Direction |
|---|---|---|---|
| n | 1,748 | 402 | — |
| **rug_rate** | 2.12% | **4.23%** | Flag elevates rug (2.00×) |
| **grad_rate** | 6.46% | **12.44%** | Flag elevates graduation (1.93×!) |
| peak_p50 | 2.61× | 3.21× | Flag mints peak higher |
| peak_p90 | 7.28× | **11.17×** | Flag's top decile is much higher |

The flag fires on **mints with extreme outcomes both directions:**
- **2× more likely to rug** ✓ (matches the hypothesis)
- **2× more likely to graduate** (NOT anticipated by the pre-reg)
- **higher peak_mult percentiles** (extreme upside also more likely)

This is a coherent pattern: when all first-5s buying concentrates in the opening 2s with no organic follow-on, the mint is in a **coordinated-launch regime** that goes one of two extreme ways — either the coordination produces a successful pump-and-bond cycle (graduation) OR the coordination dumps before bonding (rug). The "middle" outcomes (modest peak then fizzle) are LESS likely because there's no organic interest to sustain mid-range pumps.

**The flag is therefore product-relevant as a variance amplifier, not a rug filter.** Using it as a hide/filter would lose graduations as much as it gains rug-avoidance.

## Recommended product action

The strict-PARTIAL verdict + substantive variance-amplifier finding combine to a clear recommendation:

**Do NOT integrate this flag as an auto-hide or auto-filter in the dashboard.** It would hide 12.44% of high-magnitude mints that graduate (a meaningful fraction of the dashboard's actionable wins) in exchange for avoiding the 4.23% that rug.

**DO surface the flag as an informational indicator alongside other signals.** Users can use it as a "this is going one of two ways" cue — pair with composite-score + sol_5s context for their own filter decisions. The current implementation (soft flag in `bot_flags` array, no auto-hide) already matches this recommendation; no implementation change needed.

**Audit 14b candidate (filed for future):** test alternative flag definitions that might better isolate rug-only signal:
- `no_organic_follow_on AND smart_money_in == 0` (no smart money + no organic = likely rug)
- `no_organic_follow_on AND top1 >= 0.40` (already similar to manufactured_pump; test interaction)
- `no_organic_follow_on AND vsol_velocity < some_threshold` (no follow-on + decelerating)

Sub-stratifying within the flag-fires-high-magnitude cell would tell us if there's a meaningful sub-population where the flag IS rug-specific.

## What this audit confirms about the implementation

- The flag IS firing on coordinated-launch mints (the pattern the pre-reg hypothesized exists)
- The implementation's behavior (soft flag, no auto-action) matches the appropriate product framing
- Audit 09's wallet-index moat (`34ce847`) is COMPLEMENTARY to this flag — they capture different facets of the same coordinated-launch signature
- The pre-reg's pre-registered MAGNITUDE_DEPENDENT branch was THE RIGHT shape to add (the live data shows the low/mid magnitude tiers don't carry useful signal); the implementation already produces this because flag fires when ratio > 0.95 AND sol_5s > 0, but doesn't require sol_5s ≥ 4. Considering whether to amend the production flag to require magnitude gate — likely yes for production-relevant filtering, but ALSO captured in this writeup as a user-owned decision

## Methodology design note (forward-looking)

Three audits this week have surfaced patterns where a binary feature has bidirectional outcome effects (both rug AND graduation elevated). Audit 14 is the third instance (after Audit 09's wallet-index correlating with both attention/runs AND occasional pumps, and Audit 12-A's composite leakage that elevated both grad and 2x). **Future binary-feature audits should report BOTH rug AND graduation lift in the headline**, not just the "negative" metric the hypothesis is about. This is a generalization of the CI-aware monotonicity design note.

Filed for the audit-program design review.

## Pkl + index dependency note (Wallet redaction compatibility)

The audit reads:
- `predictions` table (no addresses)
- `mint_checkpoints` table (no addresses)
- Curve JSON files (contain wallet addresses in `user_short` field but THIS AUDIT only reads `t_s`, `is_buy`, `sol_amount` — wallet addresses in trades are NEVER read by this script)

**No wallet addresses surfaced or logged at any point.** Output is purely aggregate. Compatible with deploys `06480be` + `d8af9ec`.

## Receipts trail

| Commit | Action |
|---|---|
| (just-prior) Implementation + Audit 14 pre-reg | Computed fields + flag live; methodology frozen |
| **(this commit) Audit 14 results — PARTIAL with variance-amplifier finding** | Stratified table + Wilson CIs + substantive interpretation + product recommendation |
| (future, conditional) Audit 14b — alternative flag definitions | If user wants to refine for rug-only specificity |

## Cross-references

- [`audit_14_organic_followon_flag_prereg.md`](audit_14_organic_followon_flag_prereg.md) — pre-reg
- [`audit_09_smart_money_lift_results.md`](audit_09_smart_money_lift_results.md) — sister audit; bidirectional-outcome pattern
- [`audit_12a_composite_retroactive_validation_results.md`](audit_12a_composite_retroactive_validation_results.md) — sister audit; bidirectional-outcome pattern at composite level
- Memory: `feedback_pre_registration_branches.md` — discipline; including the MAGNITUDE_DEPENDENT branch pre-registration was the right call here
- Memory: `feedback_no_bandaids.md` — strict PARTIAL verdict respects the discipline; no threshold tweaking; flag's product role REFRAMED rather than retuned
- Memory: `feedback_dont_dismiss_unexpected_data.md` — the "variance amplifier" finding came from looking at ALL columns in the stratified table, not just the rug column the hypothesis was about
