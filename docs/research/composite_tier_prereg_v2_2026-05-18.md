# Pre-Registration — 3-Tier Composite Signal Forward-Validation (clean redo)

**Status:** ACTIVE as of T0 = `1779168347` (2026-05-19 05:25:47 UTC) — the
moment this file was committed to git and its digest anchored in
`prereg_anchors` on the persistent `/data` volume. The clean evaluation
clock starts at T0; see §7 for the tamper-evidence guarantee and its
honest scope.

**Why this document exists:** the original 3-tier pre-registration
(`two_tier_retune_prereg.md`, AMENDMENT 01) was lost — it only ever existed
in conversation context, never on disk or in git (this repo had zero
commits). Discovered 2026-05-18 when the old sample gate was met and the
verdict needed adjudicating against the written criteria. Reconstructing
criteria after the outcomes are already visible is the exact bias
pre-registration exists to prevent. Therefore: **all composite predictions
resolved before T0 are demoted to EXPLORATORY** — honest motivating
evidence, never a passed gate — and this pre-registration is evaluated
**only on predictions whose `predicted_at > T0`**, i.e. fresh forward data
the acceptance criteria cannot have been fit to.

---

## 1. Object under test

The frozen 3-tier classifier in `web/composite_predictions.py`
(`TIER_LOGIC_VERSION = "v2"`), `bestgp = max(grad_prob_60, grad_prob_30)`:

- **ACT**   — `bestgp >= 0.15`
- **WATCH** — `0.05 <= bestgp < 0.15`
- **SCOUT** — `0.02 <= bestgp < 0.05`  OR  `composite_strong`
  (`smart_money_in >= 7 AND max_mult_at_cross >= 4.0`)
- **DISCARD** — otherwise (not evaluated; not pushed)

The tier logic is FROZEN as of this document. No tuning of thresholds or
the `composite_strong` arm during the evaluation window. Any change voids
the run and requires a new committed pre-registration.

## 2. Claim being tested

The product claim is that the three tiers **monotonically stratify the
tradeable outcome**, and that the top tier clears a benchmark-anchored lift
with statistical confidence — on fresh post-T0 data.

- **Primary metric:** `peak_mult_24h >= 5.0` rate per tier (the tradeable
  metric — a holder could realize a ≥5× exit within 24h of the call).
- **Secondary metrics:** graduation rate; `peak_mult_24h >= 2.0` rate.
  Reported, not gating, except via the divergence rule (§5).

## 3. Frozen benchmark (the null)

Computed once, from **historical pre-T0 pooled** composite-cross outcomes
(all tiers pooled, n = 2,735 resolved, as of 2026-05-18). Frozen here; does
not change regardless of post-T0 data:

| metric | pooled base rate |
|---|---|
| `peak_mult_24h >= 5x` | **26.6%** |
| graduation            | 12.2% |
| `peak_mult_24h >= 2x` | 69.7% |

Using the pooled cross rate as the null tests the *informative* question:
does knowing the tier beat knowing only that the mint crossed? Acceptance
thresholds below are derived from this frozen benchmark via stated
multipliers — they are **not** read off the exploratory per-tier numbers.

## 4. Acceptance criteria (set a priori; derivations explicit)

Evaluated only on `composite_predictions` with `predicted_at > T0` and
`outcome_resolved_at IS NOT NULL`.

- **C1 — Monotonic separation (primary).** Post-T0 ≥5× rate ordered
  `ACT > WATCH > SCOUT`, with **each adjacent gap ≥ 5 percentage points**.
  (Magnitude threshold on the separation, not mere ordering.)
- **C2 — Top-tier lift (primary).** ACT ≥5× rate ≥ **benchmark × 1.5**
  = 26.6% × 1.5 = **≥ 39.9%** (stated as ≥ 40%). Derived from the frozen
  benchmark, not from observed ACT.
- **C3 — Sample + duration gate.** Before evaluation: resolved post-T0
  **ACT ≥ 30, WATCH ≥ 60, SCOUT ≥ 60**, AND **≥ 14 calendar days** of
  post-T0 accumulation. (Deliberately more conservative than the lost
  pre-reg's ACT ≥ 25 — no rush; build the strongest possible evidence.)
- **C4 — Confidence.** The one-sided 95% binomial **lower** bound on the
  post-T0 ACT ≥5× rate must remain **above the frozen benchmark (26.6%)**.
  Prevents a small-n fluke from passing C2.

## 5. Branch structure (evidence-types, not exclusive verdicts)

- **Branch A — PASS.** C1 ∧ C2 ∧ C3 ∧ C4 all hold. The tier system is
  validated for the ≥5× claim on fresh, externally-verifiable data.
  Consequence: "Live composite" may publish the real post-T0 tier numbers;
  the methodology gate blocking the purchasing reopen is cleared (the
  reopen itself remains a separate, user-owned product decision).
- **Branch B — FAIL (no separation).** C1 fails. The tier structure does
  not add tradeable information. Composite demoted to a single un-tiered
  heads-up; all tier-based product copy removed. No retry of the same
  classifier without a materially different design.
- **Branch C — TRANSITION ZONE.** C1 holds but C2 or C4 fails (tiers
  separate, but ACT does not clear the benchmark lift with confidence).
  Signal is real but weak: publish honestly as "tiers order outcomes; top-
  tier lift marginal," no strong product claim. **One** refined retry
  permitted, under a NEW committed pre-registration with a larger n.
  **Iteration limit:** a second consecutive Branch C ⇒ stop iterating;
  accept as a weak signal and stop tuning. (Per pre-registration-branches
  rule: the retry must be EITHER refined-retry OR stop-escalation, decided
  now, not deferred.)
- **Divergence handling.** If primary (≥5×) and secondary (graduation)
  point in opposite directions (e.g. tiers separate on ≥5× but invert on
  graduation), this is flagged **publicly**, BOTH outcomes reported, and
  the verdict is governed by the primary (≥5×, the tradeable metric) with
  the divergence disclosed in the receipt. The pre-registration is updated
  before the next one.

## 6. Evaluation procedure

1. Wait until C3 (sample + duration) is satisfied on post-T0 data.
2. Compute per-tier post-T0 ≥5× rate, graduation rate, ≥2× rate, n.
3. Evaluate C1, C2, C4. Select the branch.
4. The **verdict call is user-owned** (methodology). The implementer
   surfaces the computed numbers and the branch the criteria select;
   the user ratifies the verdict. No unilateral verdict, no purchasing
   flag change, ahead of that.
5. Publish the result and this pre-registration's ledger anchor together
   (publish-then-post documents amendments but does not replace user
   buy-in).

## 7. Tamper-evidence

- This document is the **first commit in this repo**. The defect that lost
  the original pre-reg was that it had no durable home; git's content-
  addressed history is now that home. The commit's tree hash changes if
  this file is edited, making post-T0 edits detectable.
- `SHA-256(this file)` + T0 are recorded into a dedicated `prereg_anchors`
  row on the persistent `/data` volume — deliberately NOT in
  `composite_prediction_commits` (that table is an hourly merkle root over
  *predictions*; a non-prediction row would corrupt its proof semantics).
- **Self-verification recipe** (anyone can run it, zero ambiguity): take
  the committed file, delete the entire final line — the one beginning
  `**SHA-256 (frozen at commit):**` — and `sha256sum` what remains. The
  result must equal the digest recorded on that deleted line and in the
  `prereg_anchors` row. (The canonical hashed form deliberately excludes
  the digest line itself, so the recipe is not self-referential.) Any
  substantive edit to any other line breaks this equality.
- **Honest scope of the guarantee.** Git history + the `/data` anchor make
  edits detectable *within our system*. A true third-party timestamp
  (public-repo push to `Based-LTD/graduate-oracle`, or a Solana memo of
  the digest) is the receipts-grade external proof and is a deliberate,
  separately-authorized publish step — recommended, not silently done.
- Any substantive edit after T0 voids the run and requires a new
  committed pre-registration.

## 8. Explicitly out of scope

- All pre-T0 resolved composite outcomes (exploratory only; never cited
  as a passed gate).
- Mature-mint predictions (lane discipline: predictions ≤ 60s only).
- Cold-entry trading of the signal (empirically falsified, n=20).
- The calibrated `grad_prob` track — independently validated, not gated
  by this document.

---

**T0 (frozen at commit):** `1779168347` — 2026-05-19 05:25:47 UTC. Only
`composite_predictions` rows with `predicted_at > 1779168347` are in scope.

**SHA-256 (frozen at commit):** `475927cd868a574a1e0b2a4213c8824a438530cbfb195b81d3aefd7d696c6251`
