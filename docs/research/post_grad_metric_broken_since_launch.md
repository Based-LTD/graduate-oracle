# post_grad_survival_prob — distance metric broken since launch (Finding 7, two-layer)

**Discovery:** 2026-05-07 morning, while implementing Fix 3 from the sixth-finding pre-registration (`b1e2406`). The loose-match warming gate functioned correctly and surfaced a deeper problem.
**Status at capture:** post_grad k-NN distance metric has been miscalibrated since the field was deployed. `post_grad_survival_prob` has been publishing artifacts disguised as predictions. **Acknowledging this honestly because the receipts moat depends on it.**

---

## The two layers

This finding has two distinct layers that need separate naming:

### Layer 7a — loose-match warming gate (Fix 3) is functioning correctly

After deploying Fix 3 (the gate that returns `status: warming_loose_match` when nearest-neighbor mean distance exceeds the 75th-percentile of training-set self-distances), every single live in-lane mint exceeds the threshold. Sample:

```
age=24s  pgs={'prob': None, 'mean_distance': 10.0, 'loose_threshold': 2e-6, 'status': 'warming_loose_match'}
age=27s  pgs={'prob': None, 'mean_distance': 54.0, 'loose_threshold': 2e-6, 'status': 'warming_loose_match'}
age=28s  pgs={'prob': None, 'mean_distance':  0.0, 'loose_threshold': 2e-6, 'status': 'warming_loose_match'}
age=28s  pgs={'prob': None, 'mean_distance': 80.0, 'loose_threshold': 2e-6, 'status': 'warming_loose_match'}
```

Threshold computed at ~2×10⁻⁶ (75th percentile of training self-distances). Live mints have mean-NN distances of 10-80 — **seven orders of magnitude above the threshold**. The gate fires on everything because everything genuinely is a loose match by this metric.

**The gate is working as designed.** Fix 3 was specced correctly and implemented correctly.

### Layer 7b — the underlying k-NN distance metric is fundamentally broken

The reason live distances are 10-80 while training self-distances are ~10⁻⁶ is that the post_grad k-NN's **distance metric was always miscalibrated.**

```python
# Current scale computation in post_grad_tracker._refresh_training_set:
scales = (
    max(max(smarts), 1),     # → 1     (smart_money is 0/1 in training)
    max(max(whales), 1),     # → 1     (n_whales clusters around 0)
    max(max(buyers), 1),     # → 1632  (max-scaled by outlier)
    max(max(velocity), 0.1), # → 1949  (max-scaled by outlier)
    1,                        # fee_delegated
)
```

**The pathology:** max-scaling normalizes by the largest observed value. Dimensions with extreme outliers (unique_buyers=1632, vsol_velocity=1949) get scaled down to near-zero for typical values — most training rows have buyers in the 10-100 range, normalized to 0.006-0.06. Dimensions WITHOUT large outliers (smart_money, fee_delegated — binary 0/1) keep their full magnitude.

**Result:** distance computation is dominated by smart_money. Every live mint with smart_money=0 is at distance ~0 from training mints with smart_money=0; every live mint with smart_money=1 is at distance ~1 from training mints with smart_money=0. The other dimensions contribute (raw_value/1632)² ≤ 10⁻³ — effectively zero.

Training self-distances cluster near zero because most training rows have similar values on the smart_money dimension and are near-zero on the other dimensions. Live mints with even one different smart_money bit jump to distance ~1.

**This means `post_grad_survival_prob` has been publishing meaningless outputs for the entire time the field has existed:**

- Pre-Fix-3: returned `prob: 1.0` on degenerate inputs because 8 random training points (clustered near origin in normalized space) all happened to sustain. The k-NN was finding "nearest neighbors" that were actually arbitrary.
- Post-Fix-3: the broken metric became visible because the gate measures distances against expected.

**This isn't a Fix 3 bug. This is a pre-existing pathology that Fix 3 surfaced.**

## What's been false in production (honest accounting)

System claims that have been incorrect since the field's deployment:

1. **`/api/scope`** declared `"calibrated": True` for `post_grad_survival_prob`. **False.** The field has not been calibrated against any meaningful distance metric.
2. **Alert template** rendered "post-grad sustain (30m): X%" alongside grad_prob. **The X% has been an artifact**, not a signal.
3. **Dashboard** displayed sustain probability for near-grad mints. Same as above.
4. **`docs/methodology.md`** described post_grad_survival_prob as "k-NN over historical resolved outcomes." **Mechanically true but functionally meaningless** — the k-NN's distance computation was incoherent.
5. **`project_watch_grad_vs_runner.md` memory** referenced post_grad_survival_prob as a real signal in the WATCH-vs-runner framing. The framing's overall reasoning still holds; the specific role of post_grad_survival_prob in that framing was overstated.

This is the most consequential finding of the seven. The receipts moat depends on owning it explicitly, not minimizing it.

## Why path A and path B don't fix this

The user's choice was between:

- **Path A — Accept current state, relax threshold to 90th percentile per pre-registered decision rule.** Doesn't help: even at the 100th percentile of training self-distances, threshold ≈ 10⁻⁵, still seven orders of magnitude below live distances. Threshold adjustment doesn't address the metric.
- **Path B — Roll back Fix 3, return to publishing artifacts as predictions.** Discipline violation. Knowingly continuing to publish false claims to users. Off the table.
- **Path C — Replace the metric.** The only honest option.

## Path C — pre-registered metric replacement (frozen here)

**Hypothesis:** replacing max-scaling with standard-deviation (z-score) scaling produces a distance metric where dimensions contribute proportionally to their information content, eliminating the smart_money-dominated pathology and producing live distances that span a sensible range.

**Method:**

```python
# Old (max-scaling):
scales = (max(smarts), max(whales), max(buyers), max(velocity), 1)

# New (z-score scaling):
def _stdev(values):
    if len(values) < 2: return 1.0
    mean = sum(values) / len(values)
    var = sum((v - mean)**2 for v in values) / (len(values) - 1)
    return max(var**0.5, 1e-6)  # floor to avoid division by zero on constant dimensions

scales = (_stdev(smarts), _stdev(whales), _stdev(buyers), _stdev(velocity), _stdev(fee_delegated))
```

**Rationale:** std-dev normalizes by spread, treating dimensions with similar information content equivalently. Each dimension's contribution to distance becomes (raw_difference / std-dev)². Dimensions with high information content (large spread) contribute proportionally; dimensions with low spread contribute less but are not ignored.

**Pre-registered acceptance criterion (frozen):** post-fix, sample 50 live mints, compute mean nearest-neighbor distance for each. Verify:

- **Distribution spans 0.5-3.0 typical range:** tight matches sit near 0.5-1.0 (genuinely similar mints in the corpus); genuinely loose matches sit at 2.0-3.0+. Mean across the 50 samples should fall in this range.
- **No collapse to zero or explosion to >10:** if samples cluster below 0.1 or above 5, the metric still has issues; flag and re-investigate.

**Decision rule (post-fix):**
- Distribution in 0.5-3.0 range → metric is sane; loose-match threshold (75th percentile) becomes meaningful; re-enable sustain rendering with proper warming gate.
- Distribution outside range → metric still broken; re-investigate (possibly different normalization scheme — robust statistics like IQR, log-transform on heavy-tailed dimensions).

**Frozen at this commit. No revising downward without re-pre-registration.**

## What ships during the fix window

While Path C is being implemented + verified, the surface area updates to honest framing:

1. **`post_grad_survival_prob` returns** `{prob: null, status: 'metric_recalibration_in_progress', n_total_resolved: <count>}` for ALL live mints. No prob is published until the new metric is verified.
2. **Dashboard alert template** gracefully omits sustain when status indicates recalibration. No "—" or "100%" — just absence.
3. **`/api/scope`** description updated:
   - `calibrated:` from `True` to `"directional only — distance metric being recalibrated as of 2026-05-07"`
   - Caveat added: "Distance metric was found to be miscalibrated since the field's deployment. Currently being replaced with std-dev-based normalization. Re-enable estimated within hours; absolute prob suppressed in the meantime."
4. **Memory file `project_watch_grad_vs_runner.md`** gets a corrective single-line update: "post_grad_survival_prob has been broken since launch (distance metric pathology); fix in flight 2026-05-07."

## Receipts trail (seventh finding)

| Diagnosis | Fix |
|---|---|
| `eaab3f5` deployed kNN saturation | `76a3712` cutover landing |
| `2aeba1d` GBM over-confidence (Gate 5) | `76a3712` cutover landing |
| `3d9b451` GBM bimodal cliff | `76a3712` cutover landing |
| `7011ea0` LOG_THRESHOLD validation gap | `2e7ca45` OR-clause expansion |
| `f3b1c45` alert-rule mismatch | `49ce51f` kind='bucket' rule support (later diagnosed inadequate) |
| `597b5ab` (sharpened from `ce7a38b`) sixth-finding meta + multi-issue | `b1e2406` four-fix pre-registration → next deploy |
| **(this commit) Finding 7** post_grad metric broken since launch | (pending — Path C metric replacement) |

The discipline pattern caught a finding that **predates the cutover**. This is the strongest demonstration yet — the pattern works at deploy boundaries (findings 1-3), at post-deploy review (4-5), at the verification step (6), and now at uncovering pre-existing pathologies via newly-deployed instrumentation (7).

Five lifecycle checkpoints, one pattern. **Single events are dismissible; consecutive findings at every checkpoint demonstrate the methodology is real.**

## Cross-references

- [`alert_rule_fix_inadequate.md`](alert_rule_fix_inadequate.md) — Finding 6, including the verification-by-content rule that this finding extends
- [`bucket_cutoffs_bimodal_finding.md`](bucket_cutoffs_bimodal_finding.md) — Finding 3, similar shape (model-output pathology surfaced via newly-deployed gate)
- BACKLOG.md "Sixth-finding fixes" — pre-registration of Fix 3 (loose-match warming gate); Path C extends this with metric replacement
- Memory: `feedback_pre_registration_branches.md` — discipline rules; lifecycle-checkpoint framing extended to include "pre-existing pathology surfaced by new instrumentation"
