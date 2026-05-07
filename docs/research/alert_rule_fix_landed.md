# Alert-rule fix landed

**Status:** kind='bucket' rule support deployed; rule_id=8 deactivated; new rules `id=9` (HIGH tier) and `id=10` (MED tier) active. Verified with 44 MED-tier alert fires within minutes of the fix landing.

The diagnosis at `f3b1c45` named the issue. This commit lands the fix:

1. `web/alert_push.py` — adds `kind='bucket'` evaluation branch reading the tier from `params` JSON. Avoids the legacy `threshold→bucket-gate` mapping ambiguity from the `kind='grad_prob'` branch by making rule intent explicit.
2. `PUSH_KINDS` extended to include `'bucket'`. SELECT updated to pull `params` column.
3. Database migration (atomic transaction):
   - `UPDATE tg_alert_rules SET active=0 WHERE id=8` (legacy `kind='grad_prob' threshold=0.7` rule deactivated; preserved for audit)
   - `INSERT` two new `kind='bucket'` rules with `params={"tier": "HIGH"}` and `params={"tier": "MED"}`

## Verification

Within minutes of deploy:
```
pending_alerts since cutover (~6h):
  rule_id=10 fires=44

most recent (5min ago):
  rule=10 mint=GvEznR... 🎯 ACT 🟡 *MED* — calibrated grad_prob 11.3% (live base rate ~5%) · age 54s · entry 1.0× launch · vsol +12
  rule=10 mint=J2bZ24... 🎯 ACT 🟡 *MED* — calibrated grad_prob 11.3% (live base rate ~5%) · age 48s · entry 1.5× launch · vsol +8
  ... (44 total fires)
```

The cutover's user-facing claim — alerts switched from absolute thresholds to bucket-driven — is now actually true. Pre-fix: 0 alerts since cutover landed. Post-fix: 44 in minutes, on the expected MED tier.

## Receipts trail (final state of the 2026-05-06 cutover sequence)

**Five pre-fix structural diagnoses, five fixes shipped, all publicly committed in order:**

| Diagnosis | Fix |
|---|---|
| `eaab3f5` deployed kNN saturation | `76a3712` cutover landing |
| `2aeba1d` GBM over-confidence (Gate 5) | `76a3712` cutover landing |
| `3d9b451` GBM bimodal cliff | `76a3712` cutover landing |
| `7011ea0` LOG_THRESHOLD validation gap | `2e7ca45` OR-clause expansion |
| `f3b1c45` alert-rule mismatch | (this commit) |

Three caught pre-cutover. Two caught in post-cutover review. Both classes valid; both publish pre-fix-then-fix; both ship within hours.

## Framing-check checklist update

The framing-check from `7011ea0` listed schema, write-path, and leaf-format as systems tuned to the old alert source needing migration at cutover. **It missed alert rules.** This finding adds them to the checklist.

For future cutovers that change the alert source, the framing-check now includes (at minimum):
- ✅ Schema (new columns)
- ✅ Write path (drain thread, persistence)
- ✅ Tamper-evident leaf format (V_n bump if new fields are user-claim-bearing)
- ✅ Persistence threshold / filter (LOG_THRESHOLD)
- ✅ **Alert rules (rule kinds + threshold semantics)** — added 2026-05-06 evening
- ✅ Alert evaluation code
- ✅ Display layer (dashboard, /api/scope, /api/live shape)

Updated in memory at `feedback_pre_registration_branches.md`.
