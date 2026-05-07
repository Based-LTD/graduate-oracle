# Alert-rule post-cutover gap — fifth pre-fix structural finding (post-cutover review)

**Discovery:** 2026-05-06 evening, ~80min post-cutover (commit `76a3712`)
**Status at capture:** cutover live; bucket logic working; bucket-aware alert template deployed; **but no user-facing alerts have fired since cutover landed and none will fire under current rule until the rule semantics are migrated.**
**Why this artifact exists:** timestamped receipts. The fix lands in the next commit; this commit names the issue first. Same pre-fix-then-fix discipline as `eaab3f5`, `3d9b451`, `7011ea0`.

---

## The observation

Active TG alert rules in production:
```
rule_id=8  kind='grad_prob'  threshold=0.7  active=1
```

This is the same rule that was active pre-cutover. It was tuned for the deployed k-NN era when alerts fired on `m_out["grad_prob"] >= 0.7`.

**Last 24h alert fires:** 8 total. **Last 6h (post-cutover):** 0. All 8 fires were pre-cutover.

## Mechanism — what's actually happening

The cutover (`76a3712`) made three relevant changes:

1. **`m_out["grad_prob"]` field unchanged.** This is still the deployed k-NN's calibrated output. The cutover ADDED `m_out["grad_prob_gbm_calibrated"]` and `m_out["grad_prob_bucket"]` alongside; it didn't replace `grad_prob`. Current /api/live data confirms: same mint shows `grad_prob=0.009` (k-NN) and `grad_prob_gbm_calibrated=0.1132` (GBM) as separate fields.
2. **`alert_push.py` for `kind='grad_prob'` rewritten** to read `m_out["grad_prob_bucket"]` (not the absolute value). This is the bucket-aware path that was in the cutover patch.
3. **The rule's threshold semantics weren't migrated.** New code interprets the existing `threshold=0.7` as: `bucket_gate = "HIGH" if (threshold or 0.70) >= 0.5 else "MED"` → bucket_gate = **"HIGH"**.

**Production state (last 70 min post-cutover):** HIGH bucket assignments: **0**. MED: 222. LOW: 60.

So: the alert path IS bucket-aware. The bucket logic IS computing assignments correctly. But the only existing rule maps to the HIGH-only gate, and HIGH is rare by design (~5/week per the bimodal-cliff spec). MED-bucket assignments — the daily-signal volume the cutover narrative implied — have nothing to fire them.

## The framing the cutover narrative implied vs what shipped

Cutover post material claimed: *"alerts switched from absolute thresholds to HIGH/MED/LOW buckets calibrated to live rates."*

What's true: alert evaluation code IS bucket-aware. Bucket assignments ARE landing in /api/live. Dashboard renders bucket badges. **The technical pipe is plumbed.**

What's not true (yet): users don't actually receive bucket-driven alerts because the only rule that exists routes to HIGH-only via the threshold→gate mapping, and HIGH is by-design rare.

The cutover's user-facing claim is currently false. The product update is technical-only until rules migrate.

## Same class as LOG_THRESHOLD (commit `7011ea0`)

This is the second post-cutover-review finding of the same shape: cutover changed the alert source, downstream gates weren't migrated to match. The framing-check from yesterday's discipline-memory update applies directly:

> "When product semantics change, every dependent semantic needs re-validation, not just preserved framing."

Specific dependent semantics on the alert path:
- ✅ Alert evaluation code (rewritten in cutover)
- ✅ Bucket assignment logic (new module shipped)
- ✅ Dashboard rendering (updated in cutover)
- ❌ Persistence threshold (LOG_THRESHOLD — caught in `7011ea0`, fixed in `2e7ca45`)
- ❌ **Alert rule semantics (this finding)**

The framing-check checkpoint from `7011ea0` should have included alert rules in the "every system tuned to the old alert source" list. It didn't. **Updating the framing-check checklist itself is part of this fix.**

## The fix

### Schema: add `kind='bucket'` as a rule type

The existing `tg_alert_rules.kind` column accepts string values. Pre-cutover kinds: `'grad_prob'`, `'runner_5x'`, etc. with `threshold` as a numeric probability. Adding `kind='bucket'` with `threshold` as a string label (`'HIGH'` or `'MED'`) makes the semantics explicit instead of overloading the threshold field.

### Code update in `alert_push.py`

Add a `kind='bucket'` branch alongside the existing `grad_prob` branch:

```python
if kind == "bucket":
    # Bucket-tier rules — direct evaluation against grad_prob_bucket.
    # Threshold is the minimum bucket tier to fire on (string: HIGH or MED).
    # Avoids the threshold→gate mapping ambiguity from the legacy grad_prob
    # branch by making the rule's intent explicit.
    bucket = m_out.get("grad_prob_bucket")
    bucket_rank = {"HIGH": 2, "MED": 1, "LOW": 0}
    target = (threshold or "MED").upper() if isinstance(threshold, str) else "MED"
    if bucket is None or bucket_rank.get(bucket, 0) < bucket_rank.get(target, 1):
        return False, ""
    # Alert template — same shape as the bucket-aware grad_prob branch
    # (HIGH/MED emoji + calibrated grad_prob + base rate + age + entry context).
    ...
```

### Rule migration

```sql
-- Deactivate the legacy grad_prob 0.7 rule (kept for audit, not deleted)
UPDATE tg_alert_rules SET active=0 WHERE id=8;

-- Add explicit bucket-tier rules
INSERT INTO tg_alert_rules (kind, threshold, telegram_id, active, ...)
VALUES ('bucket', 'HIGH', <existing_telegram_id>, 1, ...);

INSERT INTO tg_alert_rules (kind, threshold, telegram_id, active, ...)
VALUES ('bucket', 'MED', <existing_telegram_id>, 1, ...);
```

This requires the schema's `threshold` column to accept TEXT or be nullable for string storage. Need to verify schema before insert.

## Verification gate (after fix deploy)

1. Both new bucket rules present in `tg_alert_rules` with `active=1`.
2. Within 1-2 hours of fix deploy, at least one MED-tier alert lands in `pending_alerts` (~10-100/day expected rate, so within 1-2 hours we should see ≥1).
3. Within 1 day, alert volume reaches expected baseline (HIGH ~5/week, MED ~daily-signal rate based on actual production volume).
4. No regressions: existing `kind='grad_prob'` legacy rule paths still work (preserved for backward compat even though rule_id=8 is deactivated).

## Receipts trail (five pre-fix findings now)

1. 2026-05-05 morning — deployed kNN saturation (`eaab3f5`)
2. 2026-05-05 evening — GBM over-confidence Gate 5 (`2aeba1d`)
3. 2026-05-06 evening pre-cutover — GBM bimodal cliff (`3d9b451`)
4. 2026-05-06 evening post-cutover review — LOG_THRESHOLD validation gap (`7011ea0`)
5. **2026-05-06 evening post-cutover review — alert-rule mismatch (this commit)**

Pre-cutover analysis caught 3. Post-cutover review caught 2. Both review checkpoints are part of the same discipline pattern; both produce pre-fix-then-fix shape; both ship within hours of being named.

## Cross-references

- [`docs/research/cutover_2026_05_06.md`](cutover_2026_05_06.md) — cutover landing summary
- [`docs/research/log_threshold_validation_gap.md`](log_threshold_validation_gap.md) — first post-cutover-review finding (same class)
- [`docs/research/bucket_cutoffs_bimodal_finding.md`](bucket_cutoffs_bimodal_finding.md) — bucket logic that this fix lets users see
- Memory: `feedback_pre_registration_branches.md` — discipline rules; the framing-check checklist needs to expand to include alert rules
