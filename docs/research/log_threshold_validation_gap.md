# LOG_THRESHOLD validation-data gap — fourth pre-fix structural finding (post-cutover review)

**Discovery:** 2026-05-06 evening, ~1h post-cutover (commit `76a3712`)
**Status:** **FIX LANDED.** Diagnosis published at the prior commit; OR-clause expansion deployed at this commit. Validation data integrity restored within hours, not the +24h budget.
**Why this artifact exists:** timestamped receipts. Diagnosis lands first; fix lands separately, both publicly committed. Same pre-fix-then-fix discipline as `eaab3f5` and `3d9b451`. **Different from those:** this one was caught in post-cutover review, not pre-implementation analysis.

## Verification (post-fix, ~5 min after deploy)

Last 5 min in-lane predictions logged: **11.** HIGH/MED bucket rows: **9.** **All 9 have `predicted_prob < 0.50`** — exactly the population the previous LOG_THRESHOLD was silently filtering. Raw GBM scores on these: 0.86-0.96 (top of raw distribution), clipping to calibration ceiling 0.1132, correctly assigned MED bucket.

Pre-fix: zero of these would have persisted to the predictions table.
Post-fix: all 9 persisted.

The +7d validation rule now joins on a complete dataset.

---

## The observation

`predictions.log_prediction()` filters which scoring events get persisted to the predictions table. The current threshold is:

```python
prob >= LOG_THRESHOLD (0.50)
  OR
any runner_prob_*_from_now >= LOG_THRESHOLD
```

Pre-cutover: this matched the alert system. Alerts fired on `predicted_prob ≥ 0.70` (with a 0.50 floor); the LOG_THRESHOLD's 0.50 captured the same population the alert path could fire on, plus runner_prob alerts. Predictions table = "every mint that had a chance of generating a user-facing alert."

Post-cutover (after `76a3712`): the alert system fires on `grad_prob_bucket IN ('HIGH', 'MED')`. Bucket assignment is **independent** of k-NN's `predicted_prob`. A mint can be MED-bucket (model picked it as a top-rank-by-raw-GBM at the calibration ceiling) while having `predicted_prob < 0.50` — because the deployed k-NN saturates low and rarely produces ≥0.50 values.

**Result: MED-bucket mints that don't independently meet the old k-NN threshold never get persisted.** They appear in `/api/live`, fire alerts via the new bucket-based path, but leave no row in the predictions table.

## The consequence

The +7d post-launch validation decision rule (locked in BACKLOG "UI threshold update") needs to answer: did bucket assignments correspond to actual outcomes?

That check joins `predictions` to `post_grad_outcomes` on mint, computes per-bucket actual-graduation rates, and applies the clean / mixed / regression branches.

If only a subset of MED-bucket mints (those whose k-NN ALSO crossed 0.50) get persisted, the validation runs on a biased intersection — "MED bucket AND k-NN was already confident." That's not the population we shipped buckets for.

Concrete failure modes:

1. **Validation result skews toward "false clean"** if k-NN's confident-subset of MED happens to graduate at a higher rate than the unfiltered MED population.
2. **Validation result skews toward "false regression"** if the un-logged MED mints are the ones with the actually-strong-bucket-but-weak-k-NN signal, which is where the cutover's whole value lives.
3. **Public ledger / `/api/ledger/commits` audit can't be cross-checked** — anyone counting persisted MED rows gets a number lower than the live MED count, which looks like backdating even though nothing was rewritten.

The longer the fix is delayed, the more 7d-window data is biased and unrecoverable (predictions table is append-only).

## The fix

Single OR-clause expansion in [`web/predictions.py`](web/predictions.py) `log_prediction()`. Adds bucket assignment as a third trigger condition alongside the existing k-NN + runner_prob thresholds:

```python
# Existing (preserved):
if prob is None or prob < LOG_THRESHOLD or not mint:
    if score is None: return
    any_above = any(
        (score.get(k) or 0) >= LOG_THRESHOLD
        for k in ("runner_prob_2x", ..., "runner_prob_20x")
    )
    # NEW:
    bucket = score.get("grad_prob_bucket")
    if not any_above and bucket not in ("HIGH", "MED"):
        return
```

Backwards-compatible: existing rows untouched, schema unchanged, no migration. The k-NN thresholds remain the primary trigger; bucket assignment is an additional capture path.

## Framing miss — naming it explicitly

When this was first observed (cutover verification, ~10min post-deploy), I described it as "pre-existing semantic — not new with the cutover." That framing was **wrong**.

The semantic itself was pre-existing (LOG_THRESHOLD has been gating predictions logging since deploy). What changed at the cutover was the alert source: from k-NN absolute thresholds to bucket assignments. The LOG_THRESHOLD logic, **considered against the new alert source**, is no longer aligned with what it was designed to capture.

The fact that the SQL behavior is unchanged from pre-cutover does NOT mean its product implication is unchanged. The framing "pre-existing and benign" dismissed the issue because the SQL hadn't changed — but the system around it had.

**This is the second half of the discipline pattern:** name what you observe, then check whether your framing is right against current product semantics. The first half (pre-register hypothesis, run, decide fresh) is well-documented in this repo. The framing-check half is what catches issues like this — where a thing that was correct yesterday is incorrect today because the ground shifted around it.

## Meta-lesson (durable)

**When product semantics change, every dependent semantic needs re-validation, not just preserved framing.**

A change to "what triggers an alert" requires checking every system that was tuned to the old alert source — including persistence, audit, validation, monitoring, and downstream consumers. The check isn't "did this code change?" The check is "does this code's purpose still match its behavior given the new product semantics?"

Specific dependent semantics that needed re-validation at cutover (and that this finding surfaces by absence in the original cutover patch):

- ✅ `predictions` table SCHEMA (changed: new columns added — caught)
- ✅ `predictions` table WRITE PATH (changed: drain thread updated — caught)
- ✅ Tamper-evident leaf format (changed: V3 bump — caught)
- ❌ `predictions` table FILTERING THRESHOLD (unchanged — NOT caught until post-cutover review)
- ❌ Validation-rule join semantics (still references `predictions` as "all alert-eligible" — assumed but not verified)

The framing-check should be a checkpoint in the cutover discipline: when the alert source changes, every system tuned to the old alert source needs explicit "still aligned?" verification, even if its code didn't change.

## Receipts trail

Four pre-fix structural findings in 48 hours:

1. **2026-05-05 morning** — deployed kNN saturation (committed `eaab3f5`)
2. **2026-05-05 evening** — calibrated GBM over-confidence (Gate 5 over-confident branch fires, `2aeba1d`)
3. **2026-05-06 evening — pre-cutover** — calibrated GBM bimodal cliff (`3d9b451`)
4. **2026-05-06 evening — post-cutover review** — LOG_THRESHOLD validation gap (this commit)

The first three were caught in pre-cutover analysis. The fourth was caught in post-cutover review (~1h after the user-visible flip), in the validation window itself rather than before it. **The discipline pattern works at multiple checkpoints, not just one.**

This is a sharper claim than "we catch everything pre-deploy." It's: pre-deploy review catches some classes of issue; post-deploy review (with operational data visible) catches others. Both are part of the same pattern; both are publicly committed before the corresponding fix; both ship within hours of being named.

## Cross-references

- [BACKLOG.md "UI threshold update"](../../BACKLOG.md) — the +7d validation decision rule that this fix protects
- [docs/research/cutover_2026_05_06.md](cutover_2026_05_06.md) — cutover landing summary
- [docs/research/bucket_cutoffs_bimodal_finding.md](bucket_cutoffs_bimodal_finding.md) — the bimodal-aware bucket logic the validation rule evaluates
- Memory: `feedback_pre_registration_branches.md` — discipline rules; updated this same session to add the framing-check half
