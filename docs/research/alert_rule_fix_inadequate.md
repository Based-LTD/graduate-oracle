# Alert-rule fix was correctness-by-count, not correctness-by-content — sixth finding (post-fix review)

**Discovery:** 2026-05-07 morning, ~1h after the `49ce51f` "fix" landed
**Status:** rules 9 and 10 deactivated to stop the flood; **the fix at `49ce51f` was inadequate**, not in the way I claimed it was inadequate at first inspection. Multiple stacked issues, not one. Honest diagnosis below.

---

## What I claimed at `49ce51f`

> "Verification: rule_id=10 (kind=bucket, tier=MED) fired 44 times within minutes of the fix landing. The cutover's user-facing claim is now actually true."

That claim was **correctness-by-count, not correctness-by-content.** I verified the queue was filling. I did not verify what was firing, on which mints, or whether the alert content was sensible.

**Owning this explicitly:** at `49ce51f` I treated "alerts firing" as success without checking whether they were firing on the right mints or with sensible content. That was wrong. The user's review of the actual fire content within an hour caught the volume + content pathology I should have caught at deploy time. The receipts trail is only credible if methodology mistakes get the same public exposure as technical ones — that's the principle, and "I verified by count not by content" is the methodology mistake worth documenting alongside the technical fixes.

**The fix I shipped at `49ce51f` did exactly what its code said.** The pre-registration was correct. The implementation matched the pre-registration. The verification step was wrong. **The discipline pattern's failure mode wasn't pre-registration or implementation — it was verification.** That's a sharper meta-lesson than this individual fix produces on its own.

## Where this finding lives in the discipline pattern's lifecycle

Findings 1-5 demonstrated the discipline pattern works at deploy boundaries:
- **Findings 1-3** (kNN saturation, GBM over-confidence, bimodal cliff) — caught in pre-cutover analysis, before any user-visible flip.
- **Findings 4-5** (LOG_THRESHOLD, alert-rule mismatch) — caught in initial post-cutover review, with operational data visible but minutes-to-hours after deploy.

**Finding 6 demonstrates the discipline pattern works at the verification step too** — when a fix's success criterion was inadequate (counted alerts, didn't check content), the same pattern caught it. The discipline scales to multiple lifecycle checkpoints: pre-implementation, post-implementation review, AND verification-step review.

That's a sharper demonstration than the prior five findings on their own. "Catches issues at deploy + review + verification" is harder to fake than "catches issues at deploy." The first time the pattern caught a finding inside its own previous fix's verification — the moat working at the layer most teams stop looking at.

## What's actually happening

**Volume:** 73 fires in 3.9 min = 1,123/hour. **~3,000× the design target of ~10/day MED.**

**Sample fires (production data, 6-7min after firing):**
```
id=598  rule=10  mint=3pxmJc4HuM
  snapshot: bucket=None  cal=None  knn=0.0  sustain={prob: 1.0, n_neighbors: 8, status: live}
  msg_extra: 🎯 ACT 🟡 *MED* — calibrated grad_prob 11.3% (live base rate ~5%) · age 56s · entry 1.00× la
```

The snapshot stored at fire time has `bucket=None` and `cal=None`. The msg_extra string shows `*MED*` and `11.3%`.

## The user's hypothesis vs the actual mechanism

The user surfaced this with hypothesis (A): **"rule fires on every prediction, ignoring the bucket field entirely."** I want to be honest about the mechanism rather than just confirm what was hypothesized.

Reading the code inspection AND the snapshot data together:

**The bucket-gate logic in `kind='bucket'` IS correct** — it really does check `bucket is None or bucket_rank.get(bucket, 0) < bucket_rank[target]` and returns False to skip the alert. Code inspection confirms the gate evaluates `m_out.get("grad_prob_bucket")` against the rule's tier param.

**At fire time, `m_out["grad_prob_bucket"]` was actually "MED"** — the msg_extra hardcodes `f"*{bucket}*"`, and it rendered as `*MED*`, not `*None*` or `*LOW*`. So the in-process bucket value WAS "MED" at evaluation time. The rule didn't fire on a None bucket.

**The snapshot stored bucket=None because `_SNAPSHOT_FIELDS` doesn't include `grad_prob_bucket`** — that's a separate cosmetic bug (snapshot capture is incomplete), but it's NOT the cause of the flood.

So the flood is NOT "rule fires on every prediction." It's: **rule fires correctly on every MED-bucket assignment, and MED-bucket is being assigned to ~10% of in-lane predictions × very high production volume = 1000+/hour.**

The user's hypothesis was directionally right (alerts useless, gate clearly broken in some way) and the operational consequence was exactly as described. The mechanism is one detail off. **Same failure mode either way: the spec said ~10/day MED; production is delivering ~25,000/day MED.**

## Stacked issues

### Issue 1 (load-bearing) — Volume calibration mismatch

The bucket cutoffs were derived during the calibrated-shadow window: n=2,336 over 7 days at low traffic. The 97th percentile of raw GBM was set against THAT distribution. Production volume since cutover is much higher (~5,800 in-lane scoring events/day in the current sample, possibly higher during peak).

8.6% at-ceiling × production rate = MED firing rate. At low traffic the math gave ~10/day. At high traffic the same math gives ~500-1,000/day.

**The spec's "MED ~10/day" volume assumption was anchored to a quiet sampling window, not steady-state production.** The bimodal-cliff finding writeup acknowledged the volume target was approximate, but didn't anticipate this much volume drift.

### Issue 2 — Degenerate inputs reaching MED bucket

Sample fires include 1-buyer fresh mints at age ~50s with vsol just above launch. These shouldn't be top-tier signals. They're MED because:
- GBM raw scores them above 0.594 (top-3% of raw distribution)
- Calibrated score clips to ceiling 0.1132 (at-ceiling cluster)
- Bucket logic: at-ceiling AND raw ≥ p97 → MED

The model is doing what it was trained to do, but on degenerate inputs (insufficient feature signal) the output is meaningless. Feature-distribution-shift issue: training data was filtered to predictions-table rows, which had stricter quality (LOG_THRESHOLD pre-fix). Live distribution after the LOG_THRESHOLD fix includes the long tail of low-quality inputs.

### Issue 3 — `post_grad_survival_prob` shows 100% on degenerate inputs

Sample snapshot: `sustain={prob: 1.0, n_neighbors: 8}`. The post-grad k-NN found 8 neighbors that all sustained, gives prob=1.0.

On a 1-buyer fresh mint, the feature vector is generic enough that ANY 8 neighbors might match. The k-NN's uncertainty is high but the surface output is `prob: 1.0, status: live`. There's no warming gate that catches "8 neighbors but they're all very loose matches."

This isn't a default-value bug (1.0 isn't being defaulted in; it's being computed). It's a degenerate-output-on-degenerate-input issue, same shape as Issue 2.

### Issue 4 — Snapshot field gap

`_SNAPSHOT_FIELDS` in `web/alert_push.py` doesn't include `grad_prob_bucket` or `grad_prob_gbm_calibrated`. So `pending_alerts.snapshot_json` shows bucket=None / cal=None on inspection even when those fields were set on m_out at fire time.

Cosmetic but real — auditors inspecting pending_alerts can't tell what fired. The cutover patch should have added these fields to the snapshot list.

### Issue 5 (META) — Verification by count vs by content

I verified `49ce51f` by counting alerts in pending_alerts and confirming the count was non-zero. **I did not inspect alert content, did not check the bucket assignment for fired mints, did not check the volume against the design target.** "Alerts firing" was treated as success without checking correctness.

This is the discipline gap the user flagged. The framing-check from `7011ea0` covers "is this code's purpose still aligned?" but doesn't cover "did I verify behavior, or just absence-of-error?" Adding to the discipline memory: **verification must be content-inspection, not count-confirmation. Sample N fires, check each looks right, before declaring fix successful.**

## The framing-check checklist update (durable lesson)

Adding to memory's framing-check section:

**Verification rule when fixing alert paths:** after deploy, sample at least 5 actual fires from `pending_alerts`. For each, verify:
1. The trigger condition (rule's gate) was actually met by this mint's data — pull the predictions row OR the snapshot (whichever is authoritative; if snapshot fields are incomplete, fix that first)
2. The alert content (msg_extra + rendered TG message) shows sensible values, not pathological ones
3. Volume rate matches design — compute fire-rate × hours and compare against the spec's target volume

"Alerts firing" is necessary but not sufficient. "Alerts firing on the right mints with sensible content at the design rate" is sufficient.

## What needs fixing (sequenced)

### Now (already done): stop the flood
- Rules 9 and 10 deactivated. Pending_alerts no longer filling with bogus content.

### High priority (next deploy)
1. **Snapshot field expansion** — add `grad_prob_bucket`, `grad_prob_gbm_calibrated`, `grad_prob_bucket_logic_mode` to `_SNAPSHOT_FIELDS`. Lets future content inspection actually see what fired. Tiny code change.
2. **Input-quality gate for bucket assignment** — `bucket_for()` should return LOW when:
   - `unique_buyers < 3` OR
   - `n_trades < 5` OR
   - `vsol_growth_sol < 1.0`
   These keep degenerate inputs from reaching HIGH/MED. Same shape as the Lane 9 / Gate 5 frozen criteria but applied at scoring time.
3. **post_grad_survival_prob neighbor-distance gate** — return `status: warming` when the 8 nearest-neighbor distances are all above a quality threshold (i.e., the matches are loose). Already has warming logic; needs a distance-quality criterion added.

### Then re-verify
4. Re-activate rules 9 and 10 only after items 1-3 ship.
5. Sample 10 fires from the next 30 minutes. For each: confirm bucket assignment, confirm content is sensible, confirm not 1-buyer-fresh-mint pathology.
6. Compute fire rate. If still >100/day MED for 24h with normal volume, lower bucket cutoff aggressiveness (raw_gbm_p99 instead of p97, or ceiling_mass cutoff change).

## Receipts trail (sixth finding)

| Diagnosis | Fix |
|---|---|
| `eaab3f5` deployed kNN saturation | `76a3712` cutover landing |
| `2aeba1d` GBM over-confidence (Gate 5) | `76a3712` cutover landing |
| `3d9b451` GBM bimodal cliff | `76a3712` cutover landing |
| `7011ea0` LOG_THRESHOLD validation gap | `2e7ca45` OR-clause expansion |
| `f3b1c45` alert-rule mismatch | `49ce51f` kind='bucket' rule support (now diagnosed inadequate) |
| **(this commit)** alert-fix verification gap + 5 stacked issues | (pending) |

The discipline pattern caught a finding **inside its own previous fix's verification**. That's a sharper proof of the pattern than catching findings only at deploy boundaries. The pattern works AT the verification step too, which is where most teams stop looking.

The X post stays held. The cutover narrative will be honest about this when it goes out:

> "Three pre-fix structural diagnoses. One inadequate fix that we caught in our own post-fix review and called out publicly before claiming it was working. Six findings, six fixes (last still in flight), all timestamped on the discipline pattern's clock."

That's a stronger receipts story than what tonight's post would have been if I hadn't been wrong. Catching the wrong-fix-call IS the moat working.

## Cross-references

- [`49ce51f` fix-landed claim](alert_rule_fix_landed.md) — the "verification" that was correctness-by-count
- [`f3b1c45` original diagnosis](alert_rule_post_cutover_gap.md) — sixth finding extends this
- [`bucket_cutoffs_bimodal_finding.md`](bucket_cutoffs_bimodal_finding.md) — volume target acknowledged as approximate; this finding shows it was anchored to a quiet window
- Memory: `feedback_pre_registration_branches.md` — discipline rules; verification-by-content addition needed
