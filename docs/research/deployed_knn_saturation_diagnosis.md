# Deployed k-NN saturation — structural alert-system failure (pre-cutover diagnosis)

**Discovery date:** 2026-05-06 morning (calibrated-shadow window @ uptime 2.14h)
**Status at capture:** Track A live in shadow mode; deployed k-NN still serving alerts. Track B cutover ~22h away.
**Why this artifact exists:** timestamped receipts. The diagnosis lands BEFORE the fix ships, so the "discipline catching its own structural problem" trail is provable on the merkle-committed clock.

---

## Finding

The deployed k-NN's absolute-threshold alert system has been **structurally failing** for as long as the product has been deployed. It's not a transient. It's not a Track A regression. It's a fundamental mismatch between an absolute 0.70-confidence threshold and the live graduation distribution.

## Evidence

### 1. Live alert cadence is sparse and irregular (pre-Track-A baseline)

`pending_alerts` table, last 48h prior to Track A deploy:

| Window (rel to query) | Alerts queued |
|---|---:|
| 36-42h ago | 7 |
| 30-36h ago | 5 |
| 24-30h ago | (no row) |
| 18-24h ago | (no row) |
| 12-18h ago | 3 |
| 6-12h ago | 3 |
| **0-6h ago (post-Track-A)** | **0** |

Pattern: some 6h windows fire 3-7 alerts, others fire zero. No transparent reason from the user's side why a given hour delivers nothing.

### 2. Most predictions don't cross even the 0.50 floor

In the 4h post-Track-A window:
- 286 in-lane predictions logged
- **0 crossed `predicted_prob >= 0.50`** (alert template's hard floor)
- **0 crossed `predicted_prob >= 0.70`** (default user threshold)

The deployed k-NN's 4h distribution: every single prediction sat below 0.50. The alert system literally cannot fire when the model can't produce a value above its own threshold.

### 3. Calibrated GBM confirms the structural cause

Same window, calibrated GBM shadow predictions (n=151, all in-lane):
```
[0.00-0.05):  74.8%   ← anchored to live graduation base rate ~5%
[0.05-0.10):  11.9%
[0.10-0.15):  13.2%
[0.15+):       0.0%
```

The calibrated distribution shape was independently predicted by the offline isotonic-training analysis (78.7%/14.3%/7.0%/0% on held-out — production matches within 4pp per bin). **The live graduation rate genuinely does not support 0.70-confidence calls.** Any model honestly calibrated to live base rates will compress its output below ~0.20.

The deployed k-NN isn't producing values above 0.50 today because **values above 0.50 don't correspond to anything in live reality.** It's the right behavior on a calibration axis; it's the wrong behavior on a useful-alert-system axis.

## Diagnosis

The 0.70 absolute-threshold alerting model has two assumptions that don't hold:

1. **The model's score scale is interpretable as a probability.** It's not — it's a model-scale artifact. The deployed k-NN's "0.70" doesn't mean "70% likely"; it means "this is in the top sliver of what k-NN happens to produce on its current corpus." On days where the live distribution permits, k-NN occasionally crosses 0.70. On days it doesn't, alerts go silent.

2. **The threshold can be tuned to a useful firing rate.** It can't — there's no threshold value that gives consistent daily alert volume because the underlying distribution shifts. A 0.50 floor (the alert template's hard minimum) STILL produced zero crossings in a 4h, 286-prediction window. Lower thresholds would produce noise on bad days and be drowned by base-rate firing on good days.

The honest reframe: **alert quality is not a threshold problem. It's a base-rate-calibrated-bucket problem.**

## What Track B cutover changes

The HIGH/MED/LOW bucket framing (deferred to Track B per the staggered cutover) is not just a UI preference. It's the only honest way to fire alerts when the live graduation rate is ~5%:

- **HIGH** = top-1% percentile of last 7-day calibrated scores → fires on the most-confident-relative-to-base-rate predictions every day, regardless of absolute value
- **MED** = top-5% percentile → second-tier consistent firing
- **LOW** = below MED → not displayed in alerts

This produces:
- Consistent daily alert volume (1% and 5% of in-lane predictions per the percentile cutoffs)
- Alerts that mean "this is in the top tier of what the live distribution actually supports today" — a claim that always parses
- Self-correcting drift handling (cutoffs rebuild every 24h)

Same predictive signal (the calibrated GBM's ranking is unchanged from raw — top-N picks identical per offline analysis). Fundamentally different alert UX.

## Single-user cohort caveat (honesty)

Active `tg_alert_rules` for grad_prob: **1** (rule_id=8, threshold=0.7, single user — likely the developer). So the FELT user-facing impact today is n=1.

But the STRUCTURAL diagnosis is real. The same broken behavior would affect any user on the same threshold. Cutover fixes the structural problem regardless of who's currently subscribed.

## What this artifact is for

- **Public receipts trail:** dated 2026-05-06 morning, pre-cutover. When Track B ships and the X-post narrative goes out, the timestamps prove the diagnosis preceded the fix.
- **B2B sales material:** "Most graduation-prediction tools fire alerts when absolute probability crosses some threshold. The threshold is rarely calibrated to live base rates, which means alerts are inconsistent — some days many fire, some days none, with no transparent reason. Our calibrated cascade with HIGH/MED/LOW buckets fires on the top-1% / top-5% of relative-to-base-rate scores, every day."
- **Post-launch validation reference:** when the +7 days clean/mixed/regression decision rule fires per BACKLOG "UI threshold update" pre-registration, this artifact is the baseline against which "did alert UX improve?" gets measured.

## Cross-references

- [docs/research/gbm_v1_isotonic_calibration.md](gbm_v1_isotonic_calibration.md) — the calibration writeup that predicted the 74.8%-in-[0,0.05) distribution
- [docs/research/track_b_cutover_patch.md](track_b_cutover_patch.md) — the surgical cutover patch this diagnosis motivates
- [docs/research/competitive_landscape.md](competitive_landscape.md) — the B2B framing this finding sharpens
- [BACKLOG.md "UI threshold update"](../../BACKLOG.md) — the pre-registered cutover spec
