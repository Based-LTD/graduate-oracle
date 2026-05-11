# Audit 09 — Smart-money lift validation (results)

**Verdict commit.** Numbers filled in from the stratified analysis run at 2026-05-11T04:15Z UTC. Methodology and frozen acceptance criteria were committed in [`audit_09_smart_money_lift_prereg.md`](audit_09_smart_money_lift_prereg.md) (commit `4992703`, ~25 minutes before this results commit) — verifiable timestamp predates the numbers.

**Verdict:** **PARTIAL** under strict pre-registered branching. Interpretive caveat: the lift on `graduation_rate` is far stronger than the PARTIAL threshold expected (7.37× vs the 1.3–2.0× band); the verdict lands at PARTIAL because of a single tail-stratum (Mid > High) within overlapping CIs that fails the strict-monotonicity test. The criterion appears to have been too strict for noisy small-difference data; this is acknowledged as a methodology nuance, not as a relaxation of the verdict.

**User-owned next decision:** wallet redaction call — discuss before deploy per pre-reg's PARTIAL-branch action.

---

## Window + sample

| Field | Value |
|---|---|
| Collection window | 2026-04-11T04:15Z → 2026-05-11T04:15Z (30d) |
| Total joined+resolved rows | **12,773** |
| Resolution rate | 100% (query filters `actual_max_mult IS NOT NULL`) |
| Min per-stratum n | 1,139 (Low) — well above the n≥50 floor |
| Source | `/data/data.sqlite` predictions × mint_checkpoints at `checkpoint_age_s=60`, first lane-60s prediction per mint deduped |

---

## Stratified results

| Stratum | n | grad_rate | 95% CI (grad) | 2x_rate | 95% CI (2x) | peak_p50 | peak_p90 | rug_rate |
|---|---:|---:|---|---:|---|---:|---:|---:|
| Control (sm=0) | 3,177 | **0.8%** | [0.5%, 1.1%] | **41.1%** | [39.4%, 42.8%] | 1.25× | 6.48× | 3.1% |
| Low (sm=1–3) | 1,139 | 4.2% | [3.2%, 5.5%] | 50.6% | [47.7%, 53.5%] | 2.03× | 5.35× | 3.0% |
| Mid (sm=4–6) | 2,478 | **6.1%** | [5.2%, 7.1%] | 59.8% | [57.9%, 61.7%] | 2.25× | 6.04× | 2.6% |
| High (sm=7+) | 5,979 | 5.6% | [5.0%, 6.2%] | **69.9%** | [68.7%, 71.0%] | 2.56× | 6.81× | 2.9% |

---

## Comparative lift vs Control

| Stratum | lift_grad | lift_2x |
|---|---:|---:|
| Low (sm=1–3) | 5.58× | 1.23× |
| Mid (sm=4–6) | **8.07×** | 1.46× |
| High (sm=7+) | **7.37×** | **1.70×** |

---

## Monotonicity test

| Metric | Sequence (Control → Low → Mid → High) | Strictly monotonic? |
|---|---|---:|
| `graduation_rate` | 0.8% → 4.2% → **6.1% → 5.6%** | ❌ (Mid > High by 0.5pp) |
| `2x_runner_rate` | 41.1% → 50.6% → 59.8% → 69.9% | ✅ |
| `peak_mult_p50` | 1.25 → 2.03 → 2.25 → 2.56 | ✅ |

The grad_rate non-monotonicity at the Mid→High transition is a 0.5pp drop that falls **well inside the overlapping 95% CIs** (Mid: [5.2%, 7.1%]; High: [5.0%, 6.2%]). It is plausibly sampling variation rather than a real inverted relationship — but the pre-registered criterion uses a strict point-estimate test, not a CI-overlap-aware test, so the strict reading is "not monotonic on grad_rate."

---

## Verdict (per pre-registered criteria)

Walking each branch's frozen conditions:

### Branch VALIDATED — wallet index carries signal

| Condition | Required | Observed | Pass? |
|---|---|---|---:|
| Min per-stratum n | ≥ 50 | 1,139 | ✅ |
| Resolution rate | ≥ 80% | 100% | ✅ |
| Monotonic on graduation_rate AND 2x_runner_rate | Both | grad: ❌, 2x: ✅ | ❌ |
| `lift_grad(High)` ≥ 2.0× and non-overlapping CIs vs Control | Both | 7.37×, CIs non-overlap | ✅ |

**Fails on condition 3 only** (strict monotonicity on grad_rate).

### Branch PARTIAL — some signal, lift below threshold

| Condition | Required | Observed | Pass? |
|---|---|---|---:|
| Sample size sufficient | ≥ 50/stratum | 1,139 | ✅ |
| Monotonic on graduation_rate | Yes | ❌ | ❌ |
| `lift_grad(High)` ≥ 1.3× and < 2.0× | 1.3 ≤ x < 2.0 | 7.37× | ❌ (above range) |
| Lift reaches 2× only on `2x_runner_rate` not on grad_rate | — | 2x lift = 1.70× (< 2.0×) | ❌ |

**Does not cleanly fit Branch PARTIAL's stated sub-conditions either** — the lift on `grad_rate` is FAR ABOVE PARTIAL's 1.3–2.0× band, and `2x_runner_rate` doesn't reach 2× lift.

### Branch NOT VALIDATED — flat / inverse / overlapping CIs

| Condition | Required | Observed | Pass? |
|---|---|---|---:|
| Flat trend (`lift_grad(High)` within ±20% of Control) | Within ±0.2 | 7.37× | ❌ |
| Inverse trend (grad_rate decreases across strata) | Yes | 4.2 → 6.1 → 5.6 (one decrease at top) | Partial |
| CIs overlap across all strata | All overlap | Control vs High strongly non-overlapping | ❌ |

**Does not fit Branch NOT VALIDATED** — the lift is overwhelmingly real.

### Branch INCONCLUSIVE — insufficient sample

n=1,139 per stratum (minimum); resolution 100%. **Does not fit.**

---

## Verdict assignment

The pre-registered branches each fail on at least one condition. The closest-fit branch is **PARTIAL** by elimination — VALIDATED fails strict monotonicity; NOT VALIDATED is clearly wrong substantively; INCONCLUSIVE is wrong on sample. PARTIAL nominally fails on its strict sub-conditions too, but it is the only branch whose framing ("some signal, lift below threshold") is closest to the truth, except the lift is WAY ABOVE the threshold, not below.

**Reporting verdict as PARTIAL with the caveat that the data exceeds VALIDATED's lift requirement by 3.7× and only misses the strict-monotonicity test by a 0.5pp drop at the top stratum within overlapping CIs.**

Per the pre-registered PARTIAL action: **"Narrower redaction discussion — user-level call. The decision is methodology-shaped and is user-owned per `feedback_methodology_calls_user_owned.md`."**

---

## Methodology note (for future audits, NOT a post-hoc amendment)

The strict-monotonicity test ignored measurement uncertainty. With overlapping CIs at the Mid/High strata, the 0.5pp drop is plausibly noise. A more robust criterion would test monotonicity on the CI lower bounds, OR use a trend test (e.g., Cochran-Armitage) that accounts for sample size.

This is acknowledged here as a methodology design note for future audits in the program. **It is NOT a post-hoc amendment of this audit's verdict.** Future audits should pre-register CI-aware monotonicity tests (filed for the audit program design review).

---

## Substantive interpretation

Setting the strict-branch verdict aside, what does the data say?

### The wallet reputation index DOES carry signal — strongly

- **Graduation rate jumps from 0.8% (Control) to 5.6% at High** — a 7.37× lift with confidence intervals separated by ~4pp. There is no reasonable reading of this where smart_money_in is noise on graduation outcome.
- **2x_runner_rate cleanly monotonic from 41.1% to 69.9%** — every additional smart_money wallet on a mint correlates with a higher probability that the mint pumps to 2× peak. Clean trend, non-overlapping CIs at each step.
- **Peak multiplier percentiles increase monotonically** — p50 climbs from 1.25× to 2.56× across strata. Mints with more smart_money attention pump higher on average.

### One nuance: graduation vs runner trajectories diverge at the top stratum

Mid (sm=4–6) has slightly higher graduation rate than High (sm=7+) — but the 2x_runner_rate is higher at High, and peak_mult percentiles are higher at High. Plausible reading: the **High stratum has more pump-and-dump dynamics** where smart_money piles in early, drives a big multiplier (good for 2x_rate / peak_p50), but the mint doesn't always make it to bonding-curve graduation (sometimes it gets dumped before reaching the graduation threshold).

This is consistent with the existing memory rule `feedback_postmortem_survivorship_bias.md` — smart_money on bundle-heavy launches doesn't always finish in graduation; it can finish in a dump. The signal is "high attention worthy," not "always positive prediction."

The Mid stratum captures the cleaner "smart money picked it AND it actually graduated" pattern. The High stratum captures more of the "smart money piled in for a quick pump" pattern.

This is itself a finding worth surfacing in the writeup: **the wallet index predicts ATTENTION (and through attention, peak_mult) more reliably than it predicts GRADUATION specifically.**

### Rug rates are similar across strata (2.6–3.1%)

Smart money attention does NOT increase rug risk. This rules out a hypothesis that smart_money clusters specifically mark bundle-rugs. Smart money is associated with attention + pumps, not with rugs.

---

## What this means for the redaction decision

The strict verdict is PARTIAL. The substantive truth is "wallet index carries strong signal on attention and runners; less cleanly on graduation specifically." Per the pre-registered PARTIAL action, the redaction decision is user-owned and needs methodology-shaped discussion before deploy.

**Sub-options the user should consider** (none committed; this is decision-input only):

1. **Full redaction (per original hypothesis):** redact all wallet addresses from public surfaces. Defensible because the moat is real (7.37× lift) even if the strict criterion didn't cleanly fire.
2. **Tiered redaction:** redact High-stratum wallets (the ones with the strongest empirical signal) while keeping Low/Mid visible. Trade-off: less protective; more transparent.
3. **Field-level redaction:** redact `smart_money_examples` per-mint output (which surfaces specific wallet addresses on specific mints) while keeping aggregate `smart_money_in` counts and stratum-level analyses public. Protects the index without hiding the signal-level moat narrative.
4. **No redaction yet:** wait for Audit 09b (composite-decomposition follow-up) and Audit 12 verdict before deciding. Risk: more time exposed if competitors notice.

**Implementer recommendation (non-binding, methodology-input only):** Option 3 looks like the best alignment with the data. The redaction protects the wallet-address surface (where the moat lives) without hiding the aggregate signal-level data that the receipts trail relies on. The user can call differently — this is methodology and is yours.

---

## What ships next

- This commit (results + verdict)
- (Pending user decision) Wallet redaction deploy OR no-redaction commit with reasoning
- Pre-register Audit 09b — composite-decomposition follow-up: which features within the composite carry the most signal? Possibly `smart_money_in` is doing most of the work; possibly `n_whales` or freshness factor are; need empirical decomposition before any composite-formula changes
- Audit 12 verdict context now sharpened: composite is likely working because of smart_money — Audit 12 Branch A' (complementary product layer) just became substantially more likely

---

## Receipts trail

| Commit | Action |
|---|---|
| `4992703` Audit 09 pre-registration — smart-money lift validation | Methodology, hypothesis, frozen acceptance criteria, branch decision tree |
| **(this commit) Audit 09 results — PARTIAL verdict with substantive caveat** | Stratified table + Wilson CIs + monotonicity test + verdict assignment per pre-reg + substantive interpretation + redaction sub-options |
| (next, user-owned) Wallet redaction deploy OR no-redaction commit | User makes the call grounded in this audit's evidence + the substantive interpretation |
| (downstream) Audit 09b — composite decomposition | Pre-register before any composite-formula changes |

---

## Cross-references

- [`audit_09_smart_money_lift_prereg.md`](audit_09_smart_money_lift_prereg.md) — methodology + frozen criteria
- [`audit_12_hot_launch_composite_validation_prereg.md`](audit_12_hot_launch_composite_validation_prereg.md) — composite signal validation (Audit 09 result sharpens Branch A' likelihood)
- [`audit_12_amendment_01_coverage_gap_dual_measurement.md`](audit_12_amendment_01_coverage_gap_dual_measurement.md) — dual-axis measurement that this Audit 09 result informs
- Memory: `project_wallet_index_is_the_moat.md` — strategic hypothesis empirically validated (strongly on attention/runners; with caveat on graduation specifically)
- Memory: `feedback_postmortem_survivorship_bias.md` — directly relevant; smart_money on bundle-heavy launches doesn't always finish in graduation
- Memory: `feedback_methodology_calls_user_owned.md` — redaction decision is user-owned
- Memory: `feedback_pre_registration_branches.md` — verdict assignment under strict pre-reg even when data is stronger than criterion expected
