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

---

## Finding 7c — Path C validation FAILED; Path D2 + pre-registered Path E escalation

**Captured:** 2026-05-07, immediately after Path C (z-score scaling) validation against the frozen acceptance criterion above. Path C failed catastrophically. This section pre-registers the next attempt (D2) AND the stopping rule (E) — the iteration limit is itself part of the discipline.

### Path C validation result (frozen criterion)

```
training rows: 6278
scales (z-score):  (1e-06, 1e-06, 134.78, 141.03, 1e-06)
                    smart_money  n_whales  unique_buyers  velocity  fee_delegated

sample size: 40 in-lane live mints

distance distribution:
  min:     0.0000
  p25:     0.0000
  median:  0.0000
  p75:     5.0×10^13
  max:     1.8×10^14

ACCEPTANCE CRITERION (median ∈ [0.5, 3.0]): FAIL
```

### Diagnosis (sub-finding 7c)

Three of the five feature dimensions — **smart_money**, **n_whales**, **fee_delegated** — have near-zero variance in the training corpus and collapse to the `1e-6` divide-by-zero floor. Once a live mint has any non-zero value on those dimensions, dividing by `1e-6` blows the squared-distance term to ~10^12 per dimension; compounded across three sparse dimensions, distances reach 10^14.

Mints with all-zero values on those three dimensions (~half the live sample, the "blank" mints) compute distance 0 to every neighbor. That's the bimodal cliff: median=0 because half the population is featureless on the binary-ish dimensions, p75=5×10^13 because the other half explodes.

**Path C swapped one broken metric for another.** The pathology isn't max-vs-stdev — three of five features are sparse/binary-ish and resist any linear normalization scheme that treats them as continuous Euclidean inputs.

### Why D1 (IQR scaling) and D3 (mixed Hamming + Euclidean) are not the next attempt

Pre-registered NOT to attempt these, with reasoning frozen here:

- **D1 — IQR scaling.** Hits the same wall. With smart_money having ~80% zeros in training, the 25th and 75th percentiles are both 0, IQR=0, and we're back to the same divide-by-zero floor that produced 10^14 distances. Skip.
- **D3 — Mixed Hamming + Euclidean.** Theoretically cleanest but introduces an unprincipled tuning knob: how to weight Hamming distance against Euclidean distance? That weighting choice has no objective answer, becomes a pre-registration debate of its own, and adds significant code surface. Higher iteration cost with uncertain payoff.

### Path D2 — pre-registered (frozen here)

**Hypothesis:** the data shape reality is that 3 of 5 features carry near-zero distance signal, and treating them as side-channel filters rather than distance contributors will produce a metric that operates on the actual continuous information.

**Method:**

1. **Continuous dimensions kept in distance metric:** `unique_buyers`, `vsol_velocity`
   - Apply `log(1 + x)` transformation to handle heavy tails (the outliers that broke max-scaling)
   - Z-score normalize the log-transformed values across the training corpus
   - Distance metric: Euclidean on the 2 log-z-scored continuous dimensions

2. **Sparse dimensions dropped from distance metric:** `smart_money`, `n_whales`, `fee_delegated`
   - Become **post-filters** on nearest-neighbor results
   - After k-NN identifies the K nearest neighbors on the 2 continuous dimensions, filter to neighbors whose binary signature `(smart_money>0, n_whales>0, fee_delegated>0)` matches the live mint's signature
   - If post-filtering leaves <3 neighbors, return `status='warming_too_few_matches'`

**Pre-registered acceptance criteria (all three must pass; frozen here):**

1. **Median NN distance ∈ [0.5, 3.0]** on 50 live in-lane mints
2. **Post-filter coverage ≥ 70%** — at least 70% of live mints retain ≥3 neighbors after binary-signature post-filter (otherwise the filter is too restrictive)
3. **Probability output diversity** — across 20 sampled live mints, at least 5 distinct probability values (rules out "all 0.0" and "all 1.0" pathologies that would let the metric pass criterion 1 while still being broken)

**Decision rule (post-D2-fix):**
- All three pass → ship D2; re-render sustain in alerts; update /api/scope; exit `metric_recalibration_in_progress` state
- **Any criterion fails → execute Path E (pre-registered below). DO NOT iterate to Path D3, D4, etc.**

### Path E — pre-registered escalation (frozen here, before D2 outcome is known)

**Trigger:** any of the three D2 acceptance criteria fails.

**Action:** **temporarily sunset `post_grad_survival_prob` from public surfaces entirely** pending architecture review.

1. `/api/scope` documents the field as `"temporarily disabled pending architecture review — see post_grad_metric_broken_since_launch.md"`. Field still exists in response shape (returns `{prob: null, status: 'sunset_pending_architecture_review'}`) but is no longer claimed as a signal.
2. **Alert template** removes the sustain line entirely. Not "warming". Not "100%". Not rendered at all.
3. **Dashboard** removes the sustain card from the mint detail surface.
4. **Diagnosis writeup** updated with Path E execution receipt and the timestamp.
5. **Schedule a separate architecture-review work session** (probably weeks out, post-cutover-stabilization) to evaluate whether k-NN is the right model for this prediction task at all, given that 3-of-5 nominal features can't carry distance signal under any linear normalization scheme.

**Why pre-registering Path E now matters:**

This is iteration 8 of pre-fix-then-fix in 72 hours. Without a pre-registered escalation, "let me try one more metric" can continue indefinitely — that's not discipline, it's deferred decision-making. **Pre-registering the stopping rule frozen-locks the iteration cycle.** If D2 fails, Path E executes; we do not propose D3, D4, etc.

Sunsetting a broken feature is **forward-motion, not retreat**. The receipts trail is strengthened, not weakened, by stating publicly:

> Finding 7 chain: post_grad_survival_prob has been broken since launch (distance metric never carried real signal). Two metric replacements attempted (Path C z-score, Path D2 log+drop). Both failed pre-registered acceptance criteria. Feature temporarily sunset pending architecture review. Honesty about what's broken is what makes the receipts trustworthy.

That's a sharper claim than continued iteration would produce.

### Receipts trail (seventh finding, updated)

| Diagnosis | Fix |
|---|---|
| `5296351` Finding 7 (this doc, layer 7a/7b) | `2d95a5a` Path C pre-registration |
| `2d95a5a` Path C pre-registration | (Path C deployed in code; validation FAILED) |
| **(this commit) Finding 7c — Path C failed; Path D2 + Path E pre-registered** | (pending — Path D2 implementation, then either D2-success or Path-E-sunset) |

### Cross-references (updated)

- Memory: `feedback_pre_registration_branches.md` — being extended with **iteration-limit pre-registration rule** as part of this commit (a fix attempt that fails its acceptance criterion must pre-register either a refined retry OR a stop-iterating escalation; not "try fix N+1, N+2, ...")

---

## Path D2 validation FAILED + Finding 7d — training corpus has uniform zeros in 3/5 feature dimensions

**Captured:** 2026-05-07. Path D2 (log-transform on continuous + drop sparse to post-filter) executed against the three pre-registered acceptance criteria. Two of three criteria failed. **More importantly, the validation surfaced a deeper data-plumbing finding (7d) that explains why no normalization scheme could have rescued this metric: the training corpus has been writing zeros for 3 of 5 features since the post-grad tracker was deployed.**

### Path D2 validation result (frozen criteria)

```
training rows: 6286 (resolved with features)
scales (log-z-score on continuous dims):
  stdev_log_buyers   = 1.353080
  stdev_log_velocity = 1.522563

training signature distribution (binary smart_money / n_whales / fee_delegated):
  (0, 0, 0): 6286 (100.0%)   ← every single resolved row

live mints fetched: 24; in-lane (age 15-60s, vsol>0): 10
sample size: 10 (target was 50; in-lane traffic was thin at validation moment)

distance distribution (squared-Euclidean on log-z-scored 2D continuous):
  min:    0.0000
  p25:    0.0000
  median: 0.0000
  p75:    0.0000
  max:    0.1588

CRIT 1 — median NN distance ∈ [0.5, 3.0]:  0.0000  →  FAIL
CRIT 2 — post-filter coverage ≥ 70%:  8/10 = 80.0%  →  PASS (spurious — see below)
CRIT 3 — ≥5 distinct probabilities in first 20 samples: 2 distinct ({0.75, 1.0})  →  FAIL
```

Two failed, one passed spuriously. **Per pre-registered decision rule (any criterion fails → execute Path E), we execute Path E, do not iterate to D3/D4.**

### Finding 7d — training corpus has uniform zeros in 3/5 feature dimensions

The validation surfaced a more fundamental problem than any normalization scheme could fix:

```sql
SELECT feature_smart_money, COUNT(*) FROM post_grad_outcomes
 WHERE feature_smart_money IS NOT NULL
 GROUP BY feature_smart_money;
-- (0, 6357)  -- ZERO is the only value, across all 6357 rows

SELECT feature_n_whales, COUNT(*) FROM post_grad_outcomes
 WHERE feature_n_whales IS NOT NULL
 GROUP BY feature_n_whales;
-- (0, 6357)  -- same

SELECT feature_fee_delegated, COUNT(*) FROM post_grad_outcomes
 WHERE feature_fee_delegated IS NOT NULL
 GROUP BY feature_fee_delegated;
-- (0, 6324)  -- same
```

**Every resolved post-grad row has feature_smart_money=0, feature_n_whales=0, feature_fee_delegated=0.** Not "mostly" or "sparsely" — uniformly zero across the entire history.

Meanwhile, **live mints in the snapshot DO carry non-zero values** for these dimensions:

```
live mints: smart_money_in=[4, 4, 6, 8, 0, 0, ...]
            n_whale_wallets=[7, 10, 6, 4, 0, 0, ...]
            fee_delegation.total_bps=[0, 0, 0, 0, 0, 0, 0, 0, 0, 10000, ...]
```

So the values exist at the live snapshot layer. The bug is in `_record_graduation` (or the snapshot path the post-grad tracker reads) writing zeros at graduation moment despite the data being present elsewhere in the live API.

**This explains why Path C and Path D2 both failed.** No metric over a feature dimension that's uniformly zero can produce meaningful distance signal. Path C's smart_money 1e-6-floor explosion was a downstream symptom of upstream data loss; Path D2's 80% post-filter "pass" is spurious because the binary-signature space collapsed to a single bucket.

### Why CRIT 2 passing was spurious

D2 criterion 2 measured "post-filter coverage" — the fraction of live mints whose binary signature has ≥3 matched neighbors in the training corpus. With training collapsed to signature (0,0,0) only, the post-filter trivially matches any live mint with signature (0,0,0), which is the modal live signature too. So 80% of the sample was "matching" against a feature space with zero discriminative signal. Pass-by-degeneracy, not pass-by-merit.

### Why CRIT 1 failing the way it did is informative

Squared distances cluster at 0.0000 because most in-lane live mints sit near the corpus median on (log-buyers, log-velocity) — when you log-transform and z-score, near-median rows produce near-zero z-scores, and squared distances against the K=8 nearest neighbors all collapse toward zero. The metric IS technically functional (the max sample distance was 0.1588), but the typical magnitudes never reach the [0.5, 3.0] regime that the pre-registration treated as "matches sit at meaningful distances." With only 2 of 5 features carrying signal (and those two clustered tightly), the metric can't span that range.

### Why Path E is the right answer

Pre-registered. The discipline pattern fired correctly. Two metric attempts (C, D2) failed pre-registered acceptance criteria; per the frozen escalation rule, sunset and architecture-review.

But Path E is also independently the right call given Finding 7d. **The architecture review now has a much sharper question to answer:** before evaluating "is k-NN the right model?", we need to fix the data plumbing. Three of five features have been writing zero at graduation-time since launch — no model retrain or metric replacement helps until that's resolved.

### Path E execution plan (this commit)

1. **`predict_survival` returns `{prob: null, status: 'sunset_pending_architecture_review'}`** for ALL live mints. Field still exists in API response shape; no probability is published.
2. **`/api/scope`** description updated:
   - `calibrated:` from `"directional only — distance metric being recalibrated as of 2026-05-07"` → `"temporarily disabled pending architecture review (2026-05-07; see post_grad_metric_broken_since_launch.md)"`
   - Description rewritten to explicitly state the sunset and link to this writeup.
3. **Alert template** removes the sustain line entirely. Not "warming," not "100%," not rendered at all. Already not rendered during recalibration window (Finding 7), so this is a status-string update only.
4. **Dashboard sustain card** removed from the mint detail surface (or its rendering condition tightened to never display while sunset).
5. **Memory `project_watch_grad_vs_runner.md`** corrective note updated to reflect Path E sunset rather than "fix in flight."
6. **Memory `feedback_pre_registration_branches.md`** extended with the iteration-limit pre-registration rule (the meta-discipline this commit codifies).
7. **Architecture review work session scheduled** as a separate item: questions to answer include (a) why are 3 of 5 features writing zero at graduation-time? (b) once fixed, is k-NN the right model? (c) what's the right backfill / re-resolve strategy for the existing degenerate rows?

### Receipts trail (seventh finding, after Path E)

| Diagnosis | Fix |
|---|---|
| `5296351` Finding 7 (this doc, layers 7a/7b) | `2d95a5a` Path C pre-registration |
| `2d95a5a` Path C pre-registration | (Path C deployed; validation FAILED) |
| `c553d7f` Finding 7c — Path C failed; pre-register Path D2 + Path E | (Path D2 deployed; validation FAILED for 2 of 3 criteria) |
| **(this commit) Finding 7d — D2 failed; training corpus has uniform zeros in 3/5 features. Executing pre-registered Path E.** | (Path E execution lands in same commit: predictor returns sunset; /api/scope updated; alert/dashboard surfaces stripped) |

### Meta-pattern (this is the commit where it lands)

The pre-fix-then-fix discipline now has a stopping rule. **Iteration-limit pre-registration:** when a fix attempt fails its acceptance criterion, the next pre-registered branch must include either a refined retry OR a stop-iterating escalation. Don't pre-register "try fix N, then N+1, then N+2..." — that's not discipline, it's deferred decision-making. Pre-register a hard escalation point: after K failed attempts within a defined scope, escalate to a different framing entirely (sunset, architecture review, scope reduction).

Eight iterations of pre-fix-then-fix in 72 hours; eight commits; eight publicly timestamped diagnoses-before-fixes. Now with a frozen stopping rule, the receipts trail strengthens by stating publicly what's broken rather than continuing to iterate.

This is being added to `feedback_pre_registration_branches.md` in this commit cycle so future Claude (and future me) inherit the rule.
