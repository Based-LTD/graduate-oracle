# Audit 14 — Organic-followon flag validation (pre-registration)

**Pre-registration commit.** Methodology + frozen acceptance criteria committed BEFORE the retroactive stratified analysis runs. Per the receipts pattern: this commit predates the results commit and any downstream product decision. If a frozen criterion needs revision after this commit, the publish-then-post amendment discipline applies (refine/split, not relax; commit before verdict data).

**Series:** Audit 14 follows Audit 09 (smart_money lift), Audit 12-A (composite retroactive), Audit 12-B Phase 1b (freshness retroactive). Tests a new bot-flag layer added at 2026-05-12T20:37Z deploy: `no_organic_follow_on` (fires when `organic_followon_ratio > 0.95`).

---

## Strategic context

User direction (2026-05-12): add two computed fields and a layered safety flag to the prediction surface.

- `organic_followon_sol = sol_spent_first_5s − sol_spent_first_2s`
- `organic_followon_ratio = sol_spent_first_2s / sol_spent_first_5s`
- Flag `no_organic_follow_on` fires when `organic_followon_ratio > 0.95` (i.e., <5% of first-5s buying came after the opening 2s)

The flag's hypothesis: when nearly all first-5s buying happens in the opening 2s, the mint shows a coordinated-launch + no-organic-interest signature. Layered over composite signal, this flag should help filter out bundle-pattern mints with high rug probability.

User-surfaced example from earlier session: DaxgiZW81w4 (devil candle / 70% drop from peak) had `sol_spent_first_2s = 7.38 SOL` with sustained extreme rate in first 5s — exactly the shape this flag is designed to catch.

Audit 14 empirically validates whether the flag correlates with rug outcomes on the historical corpus.

---

## Hypothesis (frozen)

**H1 (primary):** Mints where the `no_organic_follow_on` flag would fire at lane-60s commit time have at least **2× higher rug rate** than mints where the flag doesn't fire, on the same resolved-outcome population.

**H0 (null):** Rug-rate ratio (flag-fires : flag-doesn't-fire) < 1.5×.

The 2× threshold is pre-registered to match Audit 09's discipline (also 2× as the VALIDATED bar).

**H2 (secondary — magnitude sub-stratification):** The flag's predictive power requires a meaningful sol_5s magnitude. At low sol_5s (e.g., < 1 SOL), the "no follow-on" signature reflects a quiet mint with no buying activity at all, not a coordinated launch. The flag's rug-rate lift should be stronger when sub-stratified to `sol_5s >= 4 SOL` (the manufactured_pump threshold for sustained extreme buying).

---

## Methodology (frozen)

### Data source

Retroactive analysis. The challenge: `sol_spent_first_2s` / `sol_spent_first_5s` are computed at snapshot time by the Rust observer; NOT persisted in the predictions or mint_checkpoints tables. They ARE recoverable from the raw curve JSON files in `/data/observer-curves/<filename>.json` (or `.json.gz` post Tier-2 archival), which contain the full `trades` array per mint.

The audit script:
1. Sample ~3,000–5,000 resolved mints from `predictions` (or `mint_checkpoints`) where `actual_max_mult IS NOT NULL` and timestamps are recent enough that the curve files exist in `/data/observer-curves/`
2. For each mint, locate + parse its curve JSON file
3. From the `trades` array, compute `sol_spent_first_2s` and `sol_spent_first_5s` (sum SOL of buy trades whose `t` field is ≤ 2s and ≤ 5s respectively)
4. Apply the flag rule (`ratio > 0.95`) → classify mint as flag-fires or flag-doesn't-fire
5. Join outcome columns (`actual_graduated`, `actual_max_mult`, `actual_rugged`, `max_drop_pct_5min`)
6. Compute per-stratum:
   - n
   - rug_rate (any of `actual_rugged=1` OR `max_drop_pct_5min >= 70`)
   - graduation_rate
   - peak_mult_p50, peak_mult_p90
   - Wilson 95% CIs

### Stratification scheme

Two-dimensional stratification:

**Dimension 1 — flag fires:**
- A: flag fires (`organic_followon_ratio > 0.95` AND `sol_5s > 0`)
- B: flag doesn't fire

**Dimension 2 — magnitude (sub-stratification):**
- Low (sol_5s < 1 SOL)
- Mid (1 ≤ sol_5s < 4 SOL)
- High (sol_5s ≥ 4 SOL) — the "meaningful buying" tier

The 2×3 = 6 strata are reported with rug + graduation rates. Headline comparison: flag-fires-AND-high-magnitude vs flag-doesn't-fire (the population the flag is product-relevant for).

### Sample-size targets

- Total resolved mints: ≥ 3,000
- High-magnitude strata (sol_5s ≥ 4): ≥ 200 per flag-fires/doesn't-fire cell (need 400+ at sol_5s ≥ 4)

### Outcome metrics

- **rug_rate** = `count(actual_rugged=1 OR max_drop_pct_5min ≥ 70) / n`
- **grad_rate** = `count(actual_graduated=1) / n`
- **peak_mult_p50** = median `actual_max_mult`
- **peak_mult_p90** = 90th percentile `actual_max_mult`

### Confidence intervals

Wilson 95% CIs on rug_rate and grad_rate. Audit 09 + Audit 12-B's CI-aware monotonicity convention applies.

---

## Acceptance criteria (frozen)

### Branch VALIDATED

**Conditions ALL of:**
1. Sample size: n ≥ 200 per flag-fires/doesn't-fire cell at sol_5s ≥ 4 (high-magnitude tier)
2. `rug_rate(flag=fires, sol_5s ≥ 4)` ≥ 2.0× `rug_rate(flag=doesn't-fire, sol_5s ≥ 4)`
3. Non-overlapping 95% CIs between the two cells at high-magnitude tier

**Action:** flag's rug-prediction value at meaningful-buying tier is empirically validated. Document in API docs that the flag is product-relevant when paired with sol_5s ≥ 4 SOL filter. Pre-register Audit 14b for product-surface integration (e.g., should TG alerts gate on this flag?).

### Branch PARTIAL

**Conditions ALL of:**
1. Sample size sufficient
2. `rug_rate(flag=fires, sol_5s ≥ 4)` ≥ 1.3× baseline but < 2.0×
3. Non-overlapping CIs OR borderline-touching CIs

**Action:** flag has some predictive value but doesn't dominate. Surface in /api/scope with honest framing. User-owned decision on whether to elevate to product-surface filter.

### Branch NOT_VALIDATED

**Conditions ANY of:**
1. `rug_rate(flag=fires, sol_5s ≥ 4)` within ±20% of `rug_rate(flag=doesn't-fire, sol_5s ≥ 4)` (flat trend)
2. Inverse trend (flag-fires has LOWER rug rate)
3. CIs overlap across all cells

**Action:** the flag's threshold and/or magnitude gate need re-examination. Pre-register Audit 14b (threshold sensitivity — test ratio thresholds 0.85 / 0.90 / 0.99) before any production decision. Iteration-limit: ONE retry on threshold; if 14b also fails, sunset the flag.

### Branch MAGNITUDE_DEPENDENT

**Conditions:** flag fails VALIDATED at the all-magnitude level BUT passes VALIDATED at the sol_5s ≥ 4 sub-stratum (i.e., the flag IS predictive at meaningful magnitudes but noisy at low magnitudes).

**Action:** amend the production flag logic to require `sol_5s >= 4` as a gate before firing. Publish-then-post amendment; same shape as Finding 8's pre-verdict criterion split.

### Branch INCONCLUSIVE

n < 200 per cell at high-magnitude tier. Extend sample by adding more resolved mints OR loosen the magnitude gate. One extension only.

---

## Limitations (frozen, surfaced for verdict context)

### Curve file completeness

Some old mints may have their curve files in the `/data/observer-curves-archive/` (Tier 3) or be missing entirely (observer-lag windows). Sample size is limited to mints with present + parseable curve files.

### Trade-timing precision

The Rust observer's per-trade `t` field is seconds since first_seen. Granularity is sub-second. The 2s / 5s boundaries are inclusive (`t <= 2.0`, `t <= 5.0`). Edge-case trades at exactly t=2.0 count toward sol_2s.

### Population selection

The audit samples from mints with resolved outcomes (`actual_max_mult IS NOT NULL`). This excludes very-recent mints (last 24h) that haven't resolved yet. Should still be a representative population per Audit 09's precedent.

### Per Memory: feedback_dont_dismiss_unexpected_data.md

If the data shows an unexpected pattern (e.g., flag fires more on low-magnitude mints than expected), surface as substantive finding before filtering. Don't preemptively discard the low-magnitude tier as noise.

---

## Schedule

| Phase | Time | Action |
|---|---|---|
| Implementation deploy | 2026-05-12T20:37Z | Computed fields + flag live on /api/probe + /api/live |
| Pre-reg commit | This commit | Methodology + frozen criteria + branches |
| Results commit | Within ~1h | Stratified table + Wilson CIs + verdict |

---

## What this audit does NOT do

- Does NOT validate the flag for FORWARD use (this is post-hoc structural attribution on resolved historical mints). Forward validation accumulates as the flag fires on live mints; can be re-audited at 1000+ flagged mints (~30 days at current production rate).
- Does NOT test alternative thresholds (0.85, 0.90, 0.99). Audit 14b would address that if Audit 14 lands NOT_VALIDATED.
- Does NOT integrate with TG alert path. That's a product-surface decision deferred to verdict.

---

## Receipts trail

| Commit | Action |
|---|---|
| (just-prior commit) Implementation deploy receipt | Computed fields + flag live |
| **(this commit) Audit 14 pre-registration** | Frozen methodology + criteria + branches; pre-data discipline |
| (next, within ~1h) Audit 14 results | Stratified verdict |

---

## Cross-references

- [`audit_09_smart_money_lift_prereg.md`](audit_09_smart_money_lift_prereg.md) — methodology template
- [`audit_12b_phase1b_freshness_results.md`](audit_12b_phase1b_freshness_results.md) — CI-aware monotonicity precedent
- Memory: `feedback_pre_registration_branches.md` — discipline
- Memory: `feedback_dont_dismiss_unexpected_data.md` — low-magnitude data treatment
- Memory: `feedback_no_bandaids.md` — if flag fails, sunset cleanly via Audit 14b; don't tweak threshold reactively
