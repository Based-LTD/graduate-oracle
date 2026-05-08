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

---

## Finding 7e — root cause located in 30 minutes; one-line snapshot-source bug

**Captured:** 2026-05-07, ~30 minutes after Path E sunset shipped. Per pre-registered architecture-review scoping discipline, before committing to a multi-day review, spent 30 min investigating Finding 7d's root cause. **The simple-bug hypothesis won decisively.**

### Root cause (located in 5 min of code reading + 5 min of cross-table sanity-check)

`web/post_grad_tracker.py:434` reads the snapshot file directly:

```python
with open(snapshot_path) as f:
    snap = json.load(f)
new_grads = _detect_new_graduates(snap)
for grad_mint in new_grads:
    _record_graduation(grad_mint)
```

`snapshot_path` is `observer-active.json` (set at `web/main.py:67` via `SNAPSHOT_PATH = ROOT / "observer-active.json"`).

That file is the **raw observer output**, written by the Rust observer daemon. Inspected on prod (`fly ssh console -a graduate-oracle -C "head -c 4000 /data/observer-active.json"`), the schema is confined to:

```
mint, age_s, n_trades, n_buys, unique_buyers, current_vsol_sol,
first_vsol_sol, vsol_growth_sol, current_mult, max_mult,
is_mayhem_mode, last_trade_age_s, vsol_velocity_30s
```

It does **NOT** contain:
- `smart_money_in` (computed by `_enrich_mint` in `web/main.py:745` — `m_out["smart_money_in"] = n_smart_in` where `n_smart_in` is derived from the live wallet leaderboard)
- `wallet_balance.n_whale_wallets` (Python enrichment, joined from a separate table)
- `fee_delegation.total_bps` (Python enrichment, joined from `fee_delegation` table)

When `_extract_features(m)` runs against the raw snapshot mint dict, those `m.get(...)` lookups all return `None`, fall through to `int(... or 0) = 0`, and write zero to the SQL row. **Always. For every graduation. Since the post-grad tracker was deployed.**

### Sister-module contrast (the smoking gun)

Two other modules track features at lifecycle-event time and use the **correct** pattern:

`web/early_grad_tracker.py:307`:
```python
import urllib.request
req = urllib.request.Request("http://127.0.0.1:8765/api/live?limit=200")
with urllib.request.urlopen(req, timeout=5) as r:
    live = json.loads(r.read())
for m in (live or {}).get("mints", []):
    record_observation(m)
```

`web/mint_checkpoints.py:62`:
```python
LIVE_URL = "http://127.0.0.1:8765/api/live?limit=300"
```

Both make an HTTP self-call to the locally-running web service, which serves the **enriched** payload. Both have **clean** feature data on prod:

```
early_grad_outcomes feature_smart_money:  [(0, 31896), (1, 1331), (2, 1278), (3, 1514), (4, 1476), ...]
early_grad_outcomes feature_n_whales:     [(0, 30854), (1, 521),  (2, 575),  (3, 871),  (4, 995), ...]
early_grad_outcomes feature_fee_delegated: [(0, 40676), (1, 4997)]

mint_checkpoints   feature_smart_money:  [(0, 90642), (1, 6169), (2, 5851), (3, 6279), (4, 6041), ...]
mint_checkpoints   feature_n_whales:     [(0, 84888), (1, 1285), (2, 1878), (3, 3095), (4, 3894), ...]
mint_checkpoints   feature_fee_delegated: [(0, 125067), (1, 22804)]
```

Only `post_grad_outcomes` is corrupted. **Blast radius confined to one table.** No cascading impact — every other consumer of these feature names operates on its own correctly-populated table, not on the corrupted post-grad rows.

### Why this is good news

- The "fix the data plumbing" question has a **one-block answer** (~10 lines of code).
- The architecture review **is cancellable or rescoped sharply.** What remains is contingent on Path D2's re-validation against clean data. If clean data produces in-range distance distributions, k-NN works fine; review fully cancelled. If clean data still produces broken distances, the architecture review re-opens with a sharper question: "model choice given clean inputs," not "diagnose the entire pipeline."
- The discipline pattern produced its **cleanest demonstration yet**: pre-registered escalation prevented metric-tweak thrashing, **then** root-cause investigation prevented over-scoping the response. Two halves of "knowing when to stop iterating" working in sequence.

### Pre-registered fix decisions (frozen here, BACKLOG also captures this)

1. **Data source:** swap raw snapshot file → HTTP self-call to `/api/live?limit=300`. Mirrors sister modules. No new abstractions.

2. **Corrupted-row handling: filter, not wipe.** `_refresh_training_set` adds `WHERE graduated_at >= FIX_DEPLOY_TS`. The 6,357 zero-feature rows stay in the table for forensic value (they document the bug); never enter training. Reversible.

3. **`MIN_SAMPLES_FOR_PREDICTION = 30`** per existing original spec.

4. **Auto-lift (operator-gated final flip):**
   - corpus < 30 clean rows → `status='warming_clean_corpus_accumulating'`
   - corpus ≥ 30 AND `LIFT_ENABLED=False` → `status='sunset_pending_validation_rerun'`
   - corpus ≥ 30 AND `LIFT_ENABLED=True` → run `_predict_d2`, return live results
   - `LIFT_ENABLED` flips True only after operator runs `scripts/validate_path_d2.py` against clean corpus and acceptance criteria pass. If validation fails on clean data, sunset stays — that becomes a different finding (architecture, not data plumbing).

### Receipts trail (Finding 7 chain, complete)

| Diagnosis | Action |
|---|---|
| `5296351` Finding 7 (layers 7a/7b) | (Path C pre-registered) |
| `2d95a5a` Path C pre-registration | (Path C deployed; validation FAILED) |
| `c553d7f` Finding 7c — Path C failed; pre-register Path D2 + Path E | (Path D2 deployed; validation FAILED) |
| `707c169` Finding 7d + Path E execution receipt | (sunset shipped to prod) |
| **(this commit) Finding 7e — root cause located; fix pre-registered** | (HTTP self-call swap + filter + auto-lift commit ships next) |

### Discipline-pattern observation worth naming explicitly

The iteration-limit pre-registration rule (Path D2/Path E commit) didn't just stop endless metric-tweaking. It **also forced** the "investigate root cause for 30 min before committing to multi-day review" check that prevented over-scoping the response.

**Iteration-limit pre-registration works at the scope level, not just the iteration count level.** Two halves of "knowing when to stop iterating":
1. Stop the failed-fix retry loop (Path E vs Path D3/D4)
2. Stop committing to a bigger investigation than the situation requires (30-min code check vs multi-day architecture review)

Both halves fired correctly. The framework caught a pre-existing pathology AND avoided over-scoping the response. **The discipline isn't just about catching issues — it's about right-sizing the response.**

---

## Finding 7f — Finding 7e fix was mechanically wrong; lifecycle-window mismatch

**Captured:** 2026-05-07, ~15 minutes after Finding 7e fix deployed. Verification-by-content (per the rule added to `feedback_pre_registration_branches.md` after the sixth finding) caught it. **The pattern caught its own fix being insufficient. Owning the mistake publicly because the receipts moat depends on it.**

### What broke (post-deploy verification)

Pulled the recent post-fix rows from prod sqlite. The HTTP self-call code path was deployed and verified live (read deployed source via `inspect.getsource` over fly ssh). But:

```
recent rows graduated_at >= FIX_DEPLOY_TS (~12 within first ~6 min):
  All 12 rows have feature_smart_money=0, feature_n_whales=0, feature_fee_delegated=0
  (same corruption pattern as pre-fix)

new rows landing in last 5 minutes: 0
```

Two phenomena in sequence:
1. **First 6 minutes after deploy:** 12 rows landed with the same corruption (zero on the 3 sparse fields). These were written by the OLD code path that was still running until supervisord killed and restarted the web/daemon process.
2. **After supervisord restart (~6 min after deploy):** ZERO new rows. The new code's HTTP self-call returns 0 candidates because `/api/live` doesn't include mints with vsol≥115 — they've left the prediction lane (≤60s) by the time they cross the graduation threshold.

### Why the sister-module pattern doesn't transfer

`early_grad_tracker._loop` and `mint_checkpoints` work because they capture features at **age-checkpoints** (15s, 30s, 60s) when mints are still well in lane and well below the graduation threshold. They use `/api/live` as the source because, **at those ages, the mints are present in the response.**

`post_grad_tracker._record_graduation` is supposed to capture features at the **graduation moment** (vsol≥115). By the time a mint reaches 115, it's typically aged out of the ≤60s prediction lane — so it's NOT in `/api/live`'s response.

I assumed the sister-module pattern would transfer mechanically. **It doesn't, because the lifecycle windows are different.**

### The deeper diagnosis (Finding 7f)

**Three things need to be separated:**
1. **Graduation detection** (vsol≥115) — happens in the raw observer feed (`observer-active.json`), which DOES include all mints regardless of age/lane.
2. **Feature enrichment** at graduation moment — the enrichment fields (smart_money_in, wallet_balance.n_whale_wallets, fee_delegation.total_bps) are computed during /api/live request handling. They're NOT in observer-active.json.
3. **Feature persistence across the lifecycle window gap** — the gap between "last in-lane observation" (≤60s) and "graduation moment" (typically minutes later) means features computed during in-lane observations are the freshest available when graduation happens.

**The corrected fix:** keep `_loop` reading `observer-active.json` (graduation detection works there); when graduation is detected, `_record_graduation` looks up the latest enriched feature snapshot for that mint from **`mint_checkpoints`** (sister table that already captures these features correctly, at age-checkpoints, with the correct enriched data path).

### Why the Finding 7e fix was incomplete (verification-by-content gap)

The Finding 7e investigation correctly identified that observer-active.json lacks Python-layer enrichment, and correctly identified that sister modules use HTTP self-call to /api/live. **It did NOT verify that graduating mints actually appear in /api/live.** I should have:

1. Curl'd `/api/live` for a few minutes and grep'd for vsol≥115 mints. **Would have shown zero immediately.** 
2. OR: traced through `_detect_new_graduates(api_live_response)` manually and confirmed it returns non-empty results before deploying.

Skipping that one verification step is exactly the failure mode that the **verification-by-content rule** (added after the sixth finding) was designed to catch. I applied it to alert content but not to data-source assumptions during the Finding 7e investigation.

**Meta-rule extension worth adding to `feedback_pre_registration_branches.md`:** verification-by-content applies at deploy time, not just at fix-claim time. Before declaring a data-plumbing fix correct, manually verify that the new data source CONTAINS the data the fix expects. "The new code path has the right shape" ≠ "the new data source has the right values."

This is the same class of error the sixth finding caught (counting alerts ≠ verifying alert content). **The third instance of the same meta-pattern: confirming structure isn't confirming substance.** Strengthens the rule by showing it generalizes beyond alert evaluation.

### Pre-registered fix decisions (Finding 7f, frozen here)

1. **Revert `_loop` data source:** back to `observer-active.json` (the snapshot file). It DOES include graduating mints; that's where graduation detection has to live.

2. **Replace `_record_graduation` feature extraction:** instead of `_extract_features(m)` against the (raw, unenriched) graduation snapshot mint, JOIN `mint_checkpoints` for the mint's latest checkpoint:
```python
def _record_graduation(m: dict):
    feats = _features_from_checkpoints(m["mint"])
    if feats is None:
        # fallback: at-graduation snapshot (3 of 5 fields will be zero;
        # better than refusing to record a graduation we observed)
        feats = _extract_features(m)
    ...
```

3. **`_features_from_checkpoints(mint)` queries `mint_checkpoints`** and returns the latest checkpoint row's features. mint_checkpoints already captures these features cleanly at age-checkpoints (verified clean on prod: smart_money distribution spans 0-4+, n_whales same, fee_delegated 0/1).

4. **FIX_DEPLOY_TS bump:** new constant for the Finding 7f deploy. Existing pre-7f rows (including the 12 zero-feature rows from this morning's failed fix attempt) get filtered out. Training corpus rebuilds from clean Finding 7f rows onward.

5. **Auto-lift gate retained.** `LIFT_ENABLED=False` until operator runs validation script against clean Finding 7f corpus and acceptance criteria pass.

6. **Edge case noted:** mints that graduate so fast they have no mint_checkpoints entry (e.g., manufactured pumps that hit vsol≥115 before age 15s) will fall back to the at-graduation snapshot, which has 3 zero-fields. These edge-case rows will degrade k-NN slightly but they're a small minority and surfacing them lets us measure their fraction. Future architecture work can decide whether to drop them from training or model them separately.

### Receipts trail (Finding 7 chain, complete through 7f)

| Diagnosis | Action |
|---|---|
| `5296351` Finding 7 (layers 7a/7b) | (Path C pre-registered) |
| `2d95a5a` Path C pre-registration | (Path C deployed; validation FAILED) |
| `c553d7f` Finding 7c — Path C failed; pre-register Path D2 + Path E | (Path D2 deployed; validation FAILED) |
| `707c169` Finding 7d + Path E execution receipt | (sunset shipped to prod) |
| `45fb3b9` Finding 7e — root cause located; HTTP self-call fix pre-reg | (deployed; verification surfaced fix is mechanically wrong) |
| **(this commit) Finding 7f — Finding 7e fix wrong; lifecycle-window mismatch; mint_checkpoints JOIN pre-registered** | (corrected fix lands next) |

**Nine iterations of pre-fix-then-fix in 72 hours. Two of the nine were corrections of prior fixes that turned out to be insufficient.** The pattern works partly because it surfaces these. Each correction is more credible than the prior because each one explicitly owns "the previous attempt was wrong, here's specifically why."

The receipts moat strengthens, not weakens, when a fix is publicly retracted with a sharper diagnosis. **A receipts trail with corrections is more trustworthy than one without** — it demonstrates the discipline holds even when the discipline catches its own work being insufficient.

### Pre-deploy verification (per the new deploy-time content rule)

Per the verification-by-content rule extension, applied the deploy-time check on the corrected fix BEFORE shipping: confirmed `mint_checkpoints` actually contains entries for graduating mints, with the right shape and non-zero values.

```
Recent post_grad_outcomes graduates with mint_checkpoints coverage:
   3/12  = 25.0%  for the most recent 12 graduates (last 30 min, includes 7e churn)
   7/44  = 15.9%  for graduates in last 1h
  27/233 = 11.6%  for graduates in last 6h
 128/846 = 15.1%  for graduates in last 24h
 603/6782 = 8.9%  for graduates in last 7d

For mints WITH a checkpoint, features are clean and meaningful:
  smart_money={7, 4, 7}  n_whales={10, 8, 8}  fee_delegated={1, 0, 0}
```

**~10-15% coverage is systemic, not transient.** Most pump.fun graduations are bot-driven manufactured pumps that hit vsol≥115 before age 15s — too fast for any age-checkpoint to capture. The mints that DO have checkpoints are the slower-graduating subset.

### Implications (publicly owned; ships with the fix)

1. **The Finding 7f fix is a strict improvement (10-15% > 0%) but PARTIAL.** ~85-90% of new graduates will fall back to snapshot-only extraction, which produces zero on the 3 sparse fields (smart_money, n_whales, fee_delegated). Those rows still capture unique_buyers and vsol_velocity correctly.

2. **The 10-15% covered subset is biased toward slow-graduating mints.** k-NN trained on this subset learns primarily from non-bot graduations. May or may not generalize to the full graduation population.

3. **Auto-lift gate is the next decision boundary.** If the partial corpus produces a sane Path D2 distance distribution (median ∈ [0.5, 3.0]) when validation re-runs, the fix is sufficient and sustain restores. If validation fails on the partial corpus, **Finding 7g pre-registers** at that point with a sharper question: "how to enrich features for fast-graduating mints (≤15s)?" Possible answers include: capturing features at vsol-thresholds rather than age-thresholds; an in-memory cache populated from /api/live request handling; lazy-import-and-call `_enrich_mint` from within `_record_graduation`.

4. **Not pre-registering Finding 7g now.** Per the iteration-limit rule, escalation is pre-registered as "if validation fails, escalate to architecture review of how to capture features for fast graduators" — not a specific implementation. Specific implementation gets pre-registered THEN, with its own verification criteria.

### What this morning has demonstrated about the discipline pattern

- **Eight findings in 72 hours, two of them retractions** (Path D2 failed → Path E sunset; Finding 7e mechanical wrong → Finding 7f corrected approach).
- **Every retraction is more credible than the prior fix**, because each explicitly owns the previous attempt's specific failure.
- **The discipline doesn't claim to ship perfect fixes.** It claims to publicly timestamp every diagnosis BEFORE the corresponding action, retract publicly when post-deploy verification surfaces a flaw, and pre-register the next escalation gate before the next action ships.
- **Coverage of 10-15% is publicly stated.** A trustless reader can run the same SQL against prod and verify the number. That's the receipts moat in operation: every claim is checkable.

---

## Finding 7f validation deferred (2026-05-07, ~1h after deploy)

The post-7f corpus accumulated faster than expected — 35 raw post-7f rows in 1h 9min after deploy, of which 19 had resolved 30m sustain outcomes. Ran the validation script to see what the metric produces on clean data.

**This commit captures the result honestly: validation deferred, not failed.** The metric works on clean data; corpus is still too small for two of the three frozen acceptance criteria to be meaningful. **No criterion relaxation. Sample-size adequacy is the rule update.**

### Validation result against the three frozen Finding 7c criteria

```
training rows: 19 (resolved post-7f, < MIN_SAMPLES=30)
sample size:   21 in-lane live mints (target 50)

distance distribution (squared-Euclidean on log-z-scored 2D):
  min:    1.6507    median: 2.2782    max: 3.6179

CRIT 1 — median NN distance ∈ [0.5, 3.0]:        2.2782  →  PASS ✓
CRIT 2 — post-filter coverage ≥ 70%:               61.9% (13/21)  →  FAIL
CRIT 3 — ≥5 distinct probabilities (n=20):          1 distinct: {0.714}  →  FAIL
```

### Strict-vs-qualitative divergence (per discipline rule)

- **Strict verdict:** any criterion fails → don't flip `LIFT_ENABLED`. Sunset stays.
- **Qualitative verdict:** CRIT 1 (the load-bearing criterion) passed; CRITs 2+3 are structurally caused by the small corpus, not by metric brokenness:
  - CRIT 2: 8 of 21 live mints have signatures (1,1,1), (1,1,0), (0,0,1) etc. that don't match the dominant (0,0,0) signature in the 19-row corpus. Post-filter drops them. With more signature variety in training, this fails less.
  - CRIT 3: all 7 "live" outputs got `prob=0.714` because they all matched the same 7 (0,0,0)-signature training rows. With ~60+ training rows, K=8 nearest would vary across live mints → distinct probs.
  - Sample sizes are below spec: 19 training vs MIN_SAMPLES=30; 21 in-lane vs target 50.

Per `feedback_pre_registration_branches.md` divergence-handling: **flag publicly, pick action covering both verdicts, update rule before next pre-registration. Don't decide privately and document later.**

### Action covering both verdicts

**Hold sunset** (covers strict verdict — `LIFT_ENABLED=False` stays). **Schedule re-validation when corpus is adequate** (covers qualitative verdict — gives the small-sample artifacts a chance to resolve naturally).

**No criterion relaxation. The criteria stay frozen at the original spec.** What's being updated is the precondition: *sample-size adequacy* before running the validation.

### CRIT 1 passing is the load-bearing finding

Three independent reasons it matters:

1. **Path D2 metric design works.** The Path C/D2/E chain wasn't failing because the math was wrong — it was failing because training data was uniformly zero on 3 of 5 features.
2. **Finding 7d (snapshot-source bug) was the actual root cause.** Confirmed end-to-end: clean data → sane distances. The bug fix worked.
3. **Architecture review is no longer needed by default.** The clean-data hypothesis is provisionally confirmed; only a CRIT 1 failure at higher n would reopen the architecture question.

### Re-validation trigger (frozen pre-registration)

Re-validation runs when EITHER:

- `post_grad_outcomes` has **≥60 clean resolved rows** (graduated_at ≥ FIX_DEPLOY_TS=1778169865, sustained_30m IS NOT NULL) **AND ≥3 distinct binary signatures represented** with each signature having ≥3 rows, OR
- **72h post-Finding-7f-deploy** elapses (deadline: 2026-05-10T16:04:25Z), forcing a checkpoint even if corpus growth is slower than expected

The OR-cap matters because it forces a publishable verdict on a known timeline. Whichever fires first triggers re-validation.

### Pre-registered escalation paths if re-validation also fails

Two-pronged, frozen here so the next round doesn't require fresh deliberation:

| Re-validation outcome at n≥60 | Action |
|---|---|
| All three criteria pass | Flip `LIFT_ENABLED=True`. Sustain restores. Update `/api/scope`. |
| CRIT 1 still passes; CRITs 2+3 still fail | **Finding 7h pre-registration:** small-corpus k-NN tuning (K reduction 8→5 OR signature-clustering relaxation). Specific change must be pre-registered before implementation. |
| **CRIT 1 fails at n≥60** | **Finding 7g pre-registration:** architecture review reopens. The clean-data hypothesis is rejected; metric needs deeper rethink (different model entirely, not just tuning). |

This pre-registers two distinct escalation paths so the next decision doesn't require fresh deliberation. The discipline pattern's iteration-limit rule applied at the lookahead, not just at the next-step.

### Why this commit is more durable than a "sustain restored" commit would have been

A "fix shipped + sustain restored" narrative reads cleanly but obscures the actual epistemic work. **This commit demonstrates the discipline pattern doing its hardest job:**

- Resisting post-hoc criterion relaxation when "the criteria are too strict" sounds reasonable in isolation
- Naming the strict-vs-qualitative divergence publicly rather than quietly picking one
- Separating "the test wasn't valid" from "the criteria were too strict" — first is a precondition issue, second would be discipline failure
- Pre-registering the two-pronged escalation **before** seeing the next result, so the next decision doesn't need fresh deliberation

When the data is decisive, the framework accepts the verdict. When the data is ambiguous, the framework names the ambiguity and defers. This commit is the second case.

### Receipts trail (Finding 7 chain, complete through validation deferral)

| Diagnosis | Action |
|---|---|
| `5296351` Finding 7 (layers 7a/7b) | Path C pre-registered |
| `2d95a5a` Path C pre-registration | Path C deployed; validation FAILED |
| `c553d7f` Finding 7c — Path C failed; Path D2 + Path E pre-registered | Path D2 deployed; validation FAILED |
| `707c169` Finding 7d + Path E execution receipt | Sunset shipped |
| `45fb3b9` Finding 7e — HTTP self-call fix pre-reg | Deployed; verification surfaced fix wrong |
| `2e615f4` Finding 7f deploy-time verification | 10-15% coverage acknowledged |
| `c3a83ef` Finding 7f — corrected fix pre-reg + retraction | Deployed |
| `ea6d5f5` Finding 7f validation deferred — CRIT 1 PASS, CRITs 2+3 small-sample-deferred | Re-validation at n≥60 + 3 sigs OR 72h cap |
| **(this commit) Finding 7g — re-validation at n=901 FAILS CRIT 1; pre-registered architecture review reopens** | Sustain stays sunset; clean-data hypothesis rejected |

---

## Finding 7g — re-validation at n=901; CRIT 1 fails by density collapse; architecture review reopens

**Captured:** 2026-05-08T18:01Z, ~26h after the Finding 7f deploy clock started. Re-validation triggered when corpus reached the pre-registered threshold (n=901 resolved post-7f rows ≥ 60; 5 distinct binary signatures each with ≥3 rows ≥ 3). Pre-registered Finding 7g escalation **executes** per the frozen branch at `c553d7f`.

### Re-validation result

```
training rows: 901 resolved (post-7f, sustained_30m present, FIX_DEPLOY_TS=1778169865)
sample size:   11 in-lane live mints (live traffic was thin at run-time)

distance distribution (squared-Euclidean on log-z-scored 2D continuous):
  min:    0.0000
  p25:    0.0000
  median: 0.0000
  p75:    0.0000
  max:    0.0663

CRIT 1 — median NN distance ∈ [0.5, 3.0]:    0.0000  →  FAIL
CRIT 2 — post-filter coverage ≥ 70%:           72.7% (8/11)  →  PASS
CRIT 3 — ≥5 distinct probabilities (n=20):     2 ({0.875, 1.0})  →  FAIL
```

### The verdict flip from yesterday is the load-bearing finding

| | Yesterday (n=19) | Today (n=901) |
|---|---|---|
| CRIT 1 — median NN distance | **2.2782 (PASS)** | **0.0000 (FAIL)** |
| CRIT 2 — post-filter coverage | 61.9% (FAIL) | 72.7% (PASS) |
| CRIT 3 — ≥5 distinct probs | 1 distinct (FAIL) | 2 distinct (FAIL) |

Yesterday's qualitative read was: *"CRITs 2+3 are small-sample artifacts that resolve naturally as corpus grows."* The corpus grew. CRIT 2 did pass at scale (the qualitative read held there). CRIT 3 partially improved (1→2 distinct probs) but still fails. **CRIT 1 — the load-bearing criterion — REVERSED, going from PASS at small corpus to FAIL at large corpus.**

This is a non-monotonic failure shape. The metric was claimed to "work on clean data" based on the small-corpus result. At larger corpus, the metric fails for a *different* reason than Path C/D2 attempts failed.

### Failure mechanism: density collapse on dense (0,0,0)-signature corpus

The training corpus distribution at n=901:

```
  (0,0,0): 766  (85.0%)   ← dominant signature
  (1,1,0): 107  (11.9%)
  (1,1,1):  18   (2.0%)
  (0,1,0):   7   (0.8%)
  (0,0,1):   3   (0.3%)
```

The 766 (0,0,0)-signature training rows occupy the same 2D continuous-feature region (log-z-scored unique_buyers + vsol_velocity). With that many rows in a constrained space, **any live (0,0,0)-signature mint finds 8 nearest neighbors that are essentially co-located — distance to each of the K=8 nearest is ≤0.0663, median 0.0000.**

The metric is mathematically functional (it computes distances). But the distances are not informative — the corpus is too dense in the dominant signature region for K-nearest-neighbor distances to discriminate.

**Yesterday at n=19:** training rows were sparse; live mints found 8 neighbors at meaningfully-different distances; median was in the [0.5, 3.0] range as the pre-registration anticipated. The criterion was specced for sparse-corpus k-NN behavior, which is the regime we're never going to operate in once the corpus accumulates.

**Today at n=901:** training rows are dense in the (0,0,0) region; distances collapse; the criterion's frozen [0.5, 3.0] range no longer matches the regime k-NN actually produces under realistic corpus sizes.

### Why this rejects the clean-data hypothesis cleanly

The clean-data hypothesis (formalized at `ea6d5f5`): *"Path D2 metric design works on clean data; Finding 7d (snapshot-source bug) was the actual root cause; once data is clean, k-NN works."*

The hypothesis is now **rejected** by the n=901 data. With clean training data, the metric still fails CRIT 1 — but for a different reason than the dirty-data attempts (Path C, Path D2 at small clean-corpus). Density collapse is a property of K-nearest-neighbor on a corpus dominated by one signature, independent of feature-data cleanliness.

The hypothesis was checkable; it was checked; it failed. Per pre-registration, **architecture review reopens** with a sharper question.

### Architecture review questions (pre-registered scope)

The reopening narrows to two specific questions:

**Q1: Is K-nearest-neighbor viable as the model class** for sustain prediction on a corpus that's structurally dominated by one binary signature (~85% (0,0,0) at n=901, projected to stay roughly proportional as corpus grows)?

Sub-questions:
- Does increasing K (8 → 50 → 200) help at scale, or does the density problem persist at any K?
- Does shifting to weighted-distance kernels (e.g., RBF instead of vanilla Euclidean) recover discrimination?
- Does dimensional reduction or alternative scaling help?

If Q1 is "no, k-NN is unsuitable for this data shape": move to Q2.

**Q2: Does lane-60s sustain prediction need a different model shape entirely**, or should it be **accepted as not predictable at this lane**?

Sub-questions:
- Is there a model class (logistic regression, gradient boosting, calibrated neural network) that can produce calibrated probabilities on this corpus shape, where K-NN cannot?
- Is the prediction problem itself well-posed, given that 85% of resolved sustain outcomes share a (0,0,0) signature with only the 2D continuous features differentiating them?
- Should the "lane-60s post-graduation sustain" concept be retired from the prediction surface entirely, with the aggregate `/api/accuracy.post_graduation.sustain_rate_30m` (independent Jupiter measurement) becoming the only sustain claim graduate-oracle makes?

### What ships in this commit (per user direction)

- **Public commit marking the trigger.** This writeup. The architecture review questions are pre-registered above.
- **Sustain field stays sunset.** `predict_survival()` continues returning `warming_clean_corpus_accumulating` until corpus accumulates further, then transitions to `sunset_pending_validation_rerun` at the LIFT_ENABLED gate; `LIFT_ENABLED` stays False indefinitely until architecture review delivers a different model OR formally accepts non-predictability.
- **Downstream surfaces unchanged.** Dashboard, /api/scope, alert template, /status acceptance-gates panel — all already render the sunset state honestly. No edits needed.
- **Aggregate `post_graduation.sustain_rate_30m` continues to publish on /api/accuracy** (the independent Jupiter measurement). That number is unaffected by Finding 7g; the per-mint sunset is the only thing changed.

### Pre-registered iteration-limit (frozen at this commit)

The architecture review **does not pre-register specific model-class attempts** ahead of time. That would re-introduce the iteration thrashing the discipline pattern was designed to prevent. Instead:

- **The architecture review is pre-registered as a single thinking session**, scoped to answer Q1 + Q2 above. Output: ONE proposed approach (or a "not predictable at this lane" verdict) with its own pre-registered acceptance criterion.
- **That proposal commits publicly before any code changes.** Same publish-then-post discipline that's caught the prior 7c→7d→7e→7f chain issues.
- **If the proposed approach fails its pre-registered acceptance**: the field is permanently sunset (the "accept as not predictable at this lane" branch fires). No iteration to a Path-D3-on-the-new-architecture chain.

This is iteration-limit pre-registration applied at the model-class level: **at most one new model-class attempt; if it fails, the feature is retired.** Same shape as Path D2 pre-registering Path E as the stop-iterating escalation — applied one level higher.

### Receipts trail (Finding 7 chain, complete through 7g)

| Diagnosis | Action |
|---|---|
| `5296351` Finding 7 (layers 7a/7b) | Path C pre-registered |
| `2d95a5a` Path C pre-reg | Path C deployed; validation FAILED |
| `c553d7f` Finding 7c — Path C failed; Path D2 + Path E pre-reg | Path D2 deployed; validation FAILED |
| `707c169` Finding 7d + Path E execution receipt | Sunset shipped |
| `45fb3b9` Finding 7e — HTTP self-call fix pre-reg | Deployed; verification surfaced fix wrong |
| `2e615f4` Finding 7f deploy-time verification | 10-15% coverage acknowledged |
| `c3a83ef` Finding 7f — corrected fix pre-reg + retraction | Deployed |
| `ea6d5f5` Finding 7f validation deferred — CRIT 1 PASS small-corpus | Re-validation at n≥60 + 3 sigs OR 72h cap |
| **(this commit) Finding 7g — re-validation at n=901 FAILS CRIT 1; clean-data hypothesis rejected; architecture review reopens** | Sustain stays sunset indefinitely; architecture review scoped to Q1+Q2 above; iteration-limit applied at model-class level |

### A meta-observation worth naming (non-monotonic failure shape)

The Finding 7 chain demonstrates a failure shape that the discipline pattern hasn't surfaced before: **a metric that PASSES a pre-registered criterion at one corpus size and FAILS the same criterion at a larger corpus size of the same data quality.**

The implication for future pre-registrations: **specifying the corpus size explicitly in the criterion** matters more than just "validate at n≥X." A criterion like `median NN distance ∈ [0.5, 3.0] at n_train ∈ [60, ∞)` should ideally include the corpus-size sensitivity check — does the criterion hold across the corpus-size range, or only at one point?

Today's case: yesterday's CRIT 1 check at n=19 was a single-point measurement that didn't predict n=901 behavior. A more disciplined criterion would have included "validate at multiple corpus sizes during accumulation, not just first-trigger" — but that wasn't pre-registered, and we shouldn't post-hoc rationalize having checked it. The pre-registration was what it was; the verdict stands.

The lesson for next time: when validating a model on a growing corpus, the criterion should specify behavior across corpus-size ranges, not at a single point. **Adding to memory as a refinement of the verification-by-content rule.**

---

## Finding 7h — calibrated logistic regression with interaction terms (one-shot architecture-review attempt)

**Captured:** 2026-05-08, after the architecture review session that Finding 7g pre-registered. **Pre-registration commits publicly before the experiment runs.** Same publish-then-post discipline as Finding 8 amendment — the protocol commits before any data resolves it.

### What the architecture review concluded (from Q1 + Q2)

Q1 (k-NN viability on signature-dominated corpus): **rejected.** k-NN's distance metric collapses on dense (0,0,0)-signature corpus regardless of normalization scheme. Increasing K, RBF kernels, dimensional reduction were all considered; none address the underlying issue that 85% of the corpus shares one signature with only the 2D continuous features differentiating within it.

Q2 (different model shape OR accept non-predictability): **one-shot attempt before sunset.** A model class that explicitly handles signature × continuous interactions — calibrated logistic regression with explicit interaction terms — is the single permitted attempt. If it fails the frozen criteria below, lane-60s sustain prediction is **permanently sunset** and the structural-boundary verdict is documented.

**Iteration-limit at model-class level applies** (frozen at Finding 7g): at most ONE new model-class attempt; if it fails, the feature is permanently sunset. This is that one attempt.

### Strategic context (frozen)

Sustain is **upside, not required.** Bias toward strict criteria. Soft thresholds at the margin would be discipline-pattern violation; the criteria below stay strict regardless of how close the experiment lands.

### Model specification (frozen)

**Model class:** calibrated logistic regression with explicit interaction terms.

**Feature engineering:**

```
Continuous features (apply log(1+x) then z-score normalization across training fold):
  - unique_buyers
  - vsol_velocity

Binary indicators (use as-is):
  - smart_money     := (smart_money_count > 0)
  - n_whales        := (n_whale_wallets > 0)
  - fee_delegated   := (fee_delegation_total_bps > 0)

Interaction terms:
  Binary × binary (3 pairwise + 1 triple):
    - smart_money × n_whales
    - smart_money × fee_delegated
    - n_whales × fee_delegated
    - smart_money × n_whales × fee_delegated

  Binary × continuous (6 pairs):
    - smart_money × log_unique_buyers_z
    - smart_money × log_vsol_velocity_z
    - n_whales × log_unique_buyers_z
    - n_whales × log_vsol_velocity_z
    - fee_delegated × log_unique_buyers_z
    - fee_delegated × log_vsol_velocity_z

Total feature vector: 5 main effects + 4 binary-binary + 6 binary-continuous = 15 features.
```

**Logistic regression:** standard `LogisticRegression(penalty='l2', C=1.0)` from sklearn (or equivalent — the choice of L2 vs L1 vs none does not change the pre-registration; if the implementer prefers L1 or no penalty for interpretability, that's allowed; the criterion evaluates output predictions, not coefficients).

**Calibration:** isotonic regression fit on out-of-fold predictions, applied to held-out fold predictions before evaluation. This matches the existing calibrated-GBM pipeline pattern.

### Experimental protocol (frozen)

**Cross-validation:** stratified 5-fold cross-validation on the resolved post-7f corpus. Stratification key: binary signature (so each fold has proportional signature representation, modulo signatures with n<5 which can't be evenly distributed across 5 folds — those go to whichever fold the random seed picks).

**Random seed:** `42`. Frozen so the experiment is reproducible.

**Per fold:** fit LR + isotonic on training fold (4/5 of corpus), predict on held-out fold (1/5 of corpus). Aggregate held-out predictions across all folds.

**Corpus state at pre-registration time** (frozen here so the experiment uses the same data the criteria are anchored to):

```
post-7f resolved corpus (graduated_at >= FIX_DEPLOY_TS=1778169865, sustained_30m IS NOT NULL):

  signature   n     n_sustained   base_rate   qualifier
  (0,0,0)     766   378           0.4935      modal
  (1,1,0)     107    42           0.3925      ≥30 minority (CRIT 2 evaluable)
  (1,1,1)      18     6           0.3333      <30 (excluded from CRIT 2)
  (0,1,0)       7     5           0.7143      <30 (excluded from CRIT 2)
  (0,0,1)       3     1           0.3333      <30 (excluded from CRIT 2)
  TOTAL       901   432           0.4795      overall corpus base rate
```

The experiment runs on a frozen snapshot of this corpus. If the corpus has grown since this commit, the experiment uses the snapshot at commit-time, not the live corpus, to keep the criteria anchored.

### Three frozen acceptance criteria

**CRITERION 1 — (0,0,0) base rate convergence (sanity):**

Aggregate held-out predictions across all 5 folds for rows with binary signature (0,0,0). Compute mean prediction `p_000`. Compare against (0,0,0) base rate `r_000 = 0.4935`.

PASS if `|p_000 - r_000| ≤ 0.05` (5pp tolerance).

This is a sanity check. The model must be honest about the modal 85% case — predicting roughly the base rate on (0,0,0)-sig mints, not pretending to predict noise. A model that produces wildly varying predictions on (0,0,0) rows but happens to average near the base rate would *also* pass this criterion; that's by design — the criterion tests calibration on the modal case, not within-modal-signature discrimination.

**CRITERION 2 — Minority-signature Brier improvement:**

Identify minority signatures with n ≥ 30 in the corpus. **At commit-time, this is exactly one signature: (1,1,0) with n=107.** No other minority signature qualifies.

For all held-out predictions on rows with qualifying-minority signatures (i.e., (1,1,0) at this commit-time):

- `Brier_model = mean((p_model - actual)²)` — model's Brier score on these rows
- `Brier_baseline = mean((r_signature_baseline - actual)²)` — baseline Brier using **per-signature base rate** as the prediction. For (1,1,0), `r_signature_baseline = 0.3925`.

PASS if `Brier_baseline - Brier_model ≥ 0.10` on the aggregate.

**Per-signature** baseline (not overall corpus base rate): the model has to find structure WITHIN the signature, not just learn the signature average. A model that predicts the per-signature base rate on every (1,1,0) row would have `Brier_model = Brier_baseline` and fail this criterion — exactly the right failure shape.

**CRITERION 3 — Coverage gate:**

After the model is fitted, run it against a sample of in-lane live mints from `/api/live` (at least 50 mints, sampled within a 24h window starting from when the experiment runs). Count:

- `n_total` = total in-lane mints sampled (where in-lane = age 15-60s, current_vsol_sol > 0)
- `n_predicted` = mints that get a numeric prediction from the model (any non-null `p_model` in [0, 1])

PASS if `n_predicted / n_total ≥ 0.95`.

A mint fails to get a prediction only if a required feature is unavailable, or the model produces a NaN/Inf. Silent dropouts (try/except swallowing errors) count as failures.

### Pre-registered branches (frozen)

| Outcome | Action |
|---|---|
| **PASS all 3 criteria** | Ship as conditional sustain prediction. Detail spec below. |
| **FAIL any 1 criterion** | Permanent sunset. Detail spec below. |

**PASS branch — conditional sustain prediction:**

Sustain field returns calibrated predictions, with explicit signature-dependent behavior:

```
For (0,0,0)-signature live mints:
  predict_survival returns:
    {prob: <p_000_corpus_baseline>,           # the modal-signature base rate
     status: 'baseline_no_signature_signal',
     n_neighbors: null,
     fix_deploy_ts: <FIX_DEPLOY_TS>}

For non-(0,0,0)-signature live mints:
  predict_survival returns:
    {prob: <calibrated LR output>,
     status: 'live',
     signature: '(s,w,f)',
     n_neighbors: null,
     fix_deploy_ts: <FIX_DEPLOY_TS>}
```

Public framing: **"we predict sustain when signature signal exists; we don't pretend when it doesn't."** The (0,0,0) case explicitly returns the corpus baseline rather than a fake-precise per-mint prediction. Honest about what we know AND what we don't.

`LIFT_ENABLED` flips True. `/api/scope` updates: `calibrated: "directional only — calibrated logistic regression with signature-dependent precision"`. Caveat retains the Finding 7 chain receipts for transparency.

Dashboard alert template + bot follow-up resume rendering sustain on non-(0,0,0) mints; (0,0,0) mints render `sustain ~baseline (no signature signal)` or omit the line (display detail open for refinement post-PASS).

**FAIL branch — permanent sunset:**

`predict_survival` returns:

```
{prob: null,
 status: 'sunset_lane_60s_structural_limit',
 fix_deploy_ts: <FIX_DEPLOY_TS>}
```

Permanent. `LIFT_ENABLED` stays False. The status enum value `'sunset_lane_60s_structural_limit'` documents the verdict at the API surface.

`/api/scope` post_grad_survival_prob entry rewritten:

> "PERMANENTLY SUNSET 2026-05-XX after three model-class attempts (Path C max-scaling, Path D2 log-z-score + binary post-filter, calibrated LR with interaction terms) all failed pre-registered acceptance criteria. The structural finding: lane-60s sustain prediction is not viable from the available features given the signature distribution of resolved graduates. Aggregate post_graduation.sustain_rate_30m on /api/accuracy continues as the only sustain claim — that's the independent Jupiter measurement, unaffected by per-mint sunset."

Architecture review verdict gets its own writeup section "Finding 7i — sustain not predictable at lane-60s, structural boundary documented" added to this file at sunset time.

Dashboard sustain card removed entirely (was already conditionally hidden during sunset; this makes it permanent).

Aggregate `post_graduation.sustain_rate_30m` continues unchanged — independent Jupiter measurement, never touched the per-mint k-NN.

### What this commit does NOT do

- Does NOT run the experiment. Pre-registration ships first; experiment runs after this commit lands per publish-then-post.
- Does NOT introduce escape hatches or "wait for more data" branches. Iteration-limit at model-class level applies; one attempt, frozen criteria, ship-or-sunset.
- Does NOT relax thresholds. ±5pp on CRIT 1, ≥10pp Brier improvement on CRIT 2, ≥95% coverage on CRIT 3 are the bars. Sustain is upside, not required; soft thresholds would betray the bias-toward-strict instruction.
- Does NOT predict success or failure. The corpus signature distribution (85% (0,0,0)) makes CRIT 1 likely-passable (model can learn the modal base rate). CRIT 2 is harder: the LR has to find signal WITHIN (1,1,0) on continuous features, which density-collapse-on-k-NN suggests is structurally absent. CRIT 3 is mechanically simple. The actual outcome depends on what the data reveals.

### Implementation plan after this commit lands

1. Push this commit publicly. Wait for it to land on github (verifiable timestamp).
2. Implement the experiment script: `scripts/finding_7h_lr_experiment.py`. Frozen feature engineering, frozen 5-fold stratified CV, frozen random seed, frozen criteria evaluation.
3. Run the experiment. Capture output in `docs/research/post_grad_metric_broken_since_launch.md` as a "Finding 7h experiment results" section.
4. Apply CRIT 3 (coverage) by deploying a candidate prediction endpoint or running the fitted model against a sample of /api/live in-lane mints.
5. Per pre-registered branch: ship-or-sunset. Both paths are committed as part of the same discipline cycle — the experiment-results commit also includes the implementation that ships PASS or executes FAIL.

### Receipts trail (Finding 7 chain, complete through 7h pre-registration)

| Diagnosis | Action |
|---|---|
| `5296351` Finding 7 (layers 7a/7b) | Path C pre-registered |
| `2d95a5a` Path C pre-reg | Path C deployed; validation FAILED |
| `c553d7f` Finding 7c — Path C failed; Path D2 + Path E pre-reg | Path D2 deployed; validation FAILED |
| `707c169` Finding 7d + Path E execution receipt | Sunset shipped |
| `45fb3b9` Finding 7e — HTTP self-call fix pre-reg | Deployed; verification surfaced fix wrong |
| `2e615f4` Finding 7f deploy-time verification | 10-15% coverage acknowledged |
| `c3a83ef` Finding 7f — corrected fix + retraction | Deployed |
| `ea6d5f5` Finding 7f validation deferred — CRIT 1 PASS small-corpus | Re-validation pre-registered |
| `f3f1f3e` Finding 7g — re-validation FAILS CRIT 1 at n=901; clean-data hypothesis rejected | Architecture review reopens; iteration-limit at model-class level |
| **(this commit) Finding 7h — calibrated LR with interactions; one-shot model-class attempt; frozen criteria** | Experiment runs after commit lands; ship-or-sunset per branches |

The trail extends with the experiment-results commit as Finding 7h-result (PASS) or Finding 7i-sunset (FAIL).

---

## Finding 7h experiment result + Finding 7i — permanent sunset (FAIL branch executed)

**Captured:** 2026-05-08, ~hours after the Finding 7h pre-registration commit `354024f` landed publicly. Per the publish-then-post discipline, the experiment ran AFTER the pre-registration commit — never the other way around.

**Verdict: OVERALL FAIL.** Pre-registered FAIL branch executes: **permanent sunset of `post_grad_survival_prob`.**

### Experiment results (per frozen protocol)

```
corpus loaded: n=901 resolved post-7f rows

signature distribution:
  (0, 0, 0): n=766  base_rate=0.4935
  (1, 1, 0): n=107  base_rate=0.3925
  (1, 1, 1): n=18   base_rate=0.3333
  (0, 1, 0): n=7    base_rate=0.7143
  (0, 0, 1): n=3    base_rate=0.3333

stratified 5-fold CV (random_state=42):
  fold 1: n_train=720, n_test=181, mean_pred_test=0.4842
  fold 2: n_train=721, n_test=180, mean_pred_test=0.4724
  fold 3: n_train=721, n_test=180, mean_pred_test=0.4863
  fold 4: n_train=721, n_test=180, mean_pred_test=0.4911
  fold 5: n_train=721, n_test=180, mean_pred_test=0.4678
```

### CRITERION 1 — (0,0,0) base rate convergence: PASS ✓

```
n on (0,0,0) signature (held-out):  766
mean held-out prediction p_000:      0.4942
corpus base rate r_000:              0.4935
|p_000 - r_000|:                     0.0007  (tolerance ±0.05)
```

**Verdict: PASS.** The model is honest about the modal (0,0,0) case — predictions on (0,0,0)-signature held-out rows average 0.4942, within 0.07pp of the 0.4935 corpus base rate. Far inside the 5pp tolerance.

This was the easy criterion. CRIT 1 tests calibration on the modal signature; the LR can trivially learn the (0,0,0) base rate by setting its main-effect coefficients appropriately. PASS here doesn't say anything about within-signature signal — it says the model isn't pathologically miscalibrated on the modal case.

### CRITERION 2 — Minority-signature Brier improvement: FAIL ✗

```
qualifying minority signatures (n≥30): {(1, 1, 0)}
per-signature baseline rate:           0.3925 (n=107)

n on qualifying minority sigs (held-out):  107

Brier_baseline (per-signature base rate predicted on every (1,1,0) row):  0.2384
Brier_model    (calibrated LR predictions on (1,1,0) rows):                0.2507

improvement (baseline - model):                                            -0.0122
threshold required:                                                        ≥+0.10
```

**Verdict: FAIL — by 11.22pp.** The calibrated LR with interaction terms is **1.22pp WORSE** than just predicting the per-signature base rate on every (1,1,0) row.

The threshold required ≥10pp Brier improvement; the result is in the opposite direction by ~1pp. Even at sample noise, the model is not finding within-signature signal — it's adding noise to the per-signature base rate estimate.

**Mechanistic interpretation:** the LR's interaction terms (binary × continuous) had access to `unique_buyers × n_whales`, `vsol_velocity × n_whales`, etc. on the (1,1,0) subset (n=107). With 15 features and 107 rows, the model has degrees of freedom to fit noise — but no within-signature structure to fit signal. The continuous features (log-z-scored unique_buyers, vsol_velocity) carry no information beyond what the signature already encodes about sustain probability for (1,1,0)-signature mints.

This is the same shape as Path D2's density collapse — the *raw data* doesn't have within-signature structure that any model in this corpus shape can capture from these 5 features. k-NN couldn't find it; LR with explicit interaction terms can't find it either. The structural boundary is real, not an artifact of model class.

### CRITERION 3 — Coverage gate: PASS ✓ (sample below target)

```
n_total in-lane:        14   (target ≥50; live traffic was thin at run time)
n_predicted (numeric, in [0,1]):  14
coverage:                100.0%  (threshold ≥95%)
```

**Verdict: PASS, with sample-size caveat.** 14/14 in-lane mints get numeric predictions. Sample size below the pre-registration target of ≥50 (live in-lane traffic was thin at experiment run-time, only 14 in-lane mints visible in `/api/live` snapshot).

**Interpretive note (frozen at this writeup):** the verdict on CRIT 3 is unambiguous at 100% coverage — even at sample n=50 with a single dropout, the result would be 98%, still PASS. The coverage criterion is mechanically simple and the LR pipeline handles input shape correctly. The sub-target sample size is a methodology limitation, not an outcome ambiguity.

**Coverage criterion's role in the verdict:** moot — CRIT 2 fails decisively, so the OVERALL FAIL verdict stands regardless of CRIT 3's resolution. Even if CRIT 3 had failed (e.g., model produced NaN on some inputs), the verdict would be the same.

### Final tally

```
CRIT 1 — (0,0,0) baseline:            PASS ✓
CRIT 2 — minority Brier improvement:  FAIL ✗ (decisive: -0.0122 vs +0.10 required)
CRIT 3 — coverage:                    PASS ✓ (sample n=14 below target n≥50)

OVERALL: FAIL → execute pre-registered permanent sunset branch.
```

---

## Finding 7i — permanent sunset (executing pre-registered FAIL branch)

Per Finding 7h pre-registration (`354024f`) FAIL branch:

> Permanent sunset. `predict_survival` returns `{prob: null, status: 'sunset_lane_60s_structural_limit'}`. Permanent. `LIFT_ENABLED` stays False. The status enum value `'sunset_lane_60s_structural_limit'` documents the verdict at the API surface.

This commit ships that operationally:

1. **`web/post_grad_tracker.py` `predict_survival()`** rewritten to return only the sunset payload. All prior status branches removed (warming, sunset_pending_*, live). Single terminal state.
2. **`web/main.py` `/api/scope.predictions.post_grad_survival_prob`** description rewritten with the permanent-sunset framing + complete Finding 7 chain caveat.
3. **`bot/main.py` alert template** adds `'sunset_lane_60s_structural_limit'` to the no-render status list (alongside the existing sunset states).
4. **`web/static/app.js` dashboard sustain card** adds a new render branch for the permanent-sunset state — explicit messaging at the API-detail surface that the field is retired and pointing at this writeup.
5. **Aggregate `post_graduation.sustain_rate_30m`** on `/api/accuracy` is unchanged — that's the independent Jupiter measurement and was never affected by the per-mint k-NN at any point in the Finding 7 chain.

### The structural finding (frozen)

**Lane-60s sustain prediction is not viable from the available features given the signature distribution of resolved graduates.**

Three independent attempts demonstrated this:

| Attempt | Mechanism failure |
|---|---|
| Path C (z-score, 5 dims) | Sparse-dim 1e-6 floor → distances exploded to 10^14; metric mathematically broken |
| Path D2 (log-z-score, 2 continuous + binary post-filter) | Small-corpus passed CRIT 1; large-corpus density-collapsed on dense (0,0,0) sig |
| Path 7h (calibrated LR + 15 interaction terms) | Found no within-(1,1,0)-signature signal; model 1.22pp WORSE than per-signature baseline |

The pattern across all three: **k-NN couldn't find structure in the corpus shape; LR with explicit interaction terms also couldn't find it.** Two structurally different model classes both failed. The boundary isn't model-class-specific — it's data-specific. The features available at lane-60s (smart_money, n_whales, unique_buyers, vsol_velocity, fee_delegated) do not carry within-signature signal sufficient to produce calibrated per-mint sustain predictions at this corpus shape.

### What IS viable (frozen acknowledgment)

- **Aggregate `post_graduation.sustain_rate_30m`** on `/api/accuracy` — independent Jupiter price-poll measurement at the 5/15/30 min checkpoints. n>=6,800 resolved graduates; rate ≈47% sustain. **Unaffected by sunset; continues to publish.** This is the only sustain claim graduate-oracle makes.
- **Per-mint sustain prediction** — retired. Will not return without a fundamentally different feature set (e.g., DEX-side post-graduation features that aren't available in the lane-60s prediction window) AND a fundamentally different framing of what "sustain" means.

### What this commit is NOT

- NOT softening criteria. CRIT 2 failed by ~11.22pp from threshold; this is not a marginal failure.
- NOT iterating to a Path 7j with a different model class. The pre-registered iteration-limit at the model-class level fired correctly: ONE attempt; FAIL = permanent sunset.
- NOT promising a reopening. The structural-boundary verdict is durable; absent a new feature set or substantially different problem framing, the field stays sunset.
- NOT removing the receipts trail. The complete Finding 7 chain (7a→7i) lives in this writeup permanently. Any future reader inspecting the trail can verify the discipline pattern's full execution.

### Receipts trail (Finding 7 chain — COMPLETE)

| Diagnosis | Action |
|---|---|
| `5296351` Finding 7 (layers 7a/7b) | Path C pre-registered |
| `2d95a5a` Path C pre-reg | Path C deployed; validation FAILED |
| `c553d7f` Finding 7c — Path C failed; Path D2 + Path E pre-reg | Path D2 deployed; validation FAILED |
| `707c169` Finding 7d + Path E execution receipt | Sunset shipped |
| `45fb3b9` Finding 7e — HTTP self-call fix pre-reg | Deployed; verification surfaced fix wrong |
| `2e615f4` Finding 7f deploy-time verification | 10-15% coverage acknowledged |
| `c3a83ef` Finding 7f — corrected fix + retraction | Deployed |
| `ea6d5f5` Finding 7f validation deferred — CRIT 1 PASS small-corpus | Re-validation pre-registered |
| `f3f1f3e` Finding 7g — re-validation FAILS CRIT 1 at n=901; clean-data hypothesis rejected | Architecture review reopens; iteration-limit at model-class level |
| `354024f` Finding 7h — calibrated LR with interactions; one-shot architecture attempt; frozen criteria | Pre-registration commits; experiment runs after |
| **(this commit) Finding 7h experiment FAILED CRIT 2 by 11.22pp + Finding 7i permanent sunset executed** | predict_survival returns sunset_lane_60s_structural_limit; chain complete |

**Eleven public commits across 48 hours documenting the complete chain. Five model-class diagnoses, three formal model-class attempts, two retractions, one root-cause fix, one structural-boundary verdict.** The discipline pattern produced a clean negative result: a publicly-justified permanent sunset, with every step's pre-registration predating its corresponding verdict.

### Closing meta-observation

The Finding 7 chain is an example of the discipline pattern executing without compromise across an unfavorable outcome. **A team that believes in its discipline pattern only when it produces ship verdicts is doing performance discipline.** A team that executes the same pattern when it produces sunset verdicts — when the verdict is "the thing we wanted to ship doesn't work, and here's the receipts proving we tried" — is doing genuine epistemic discipline.

The receipts moat from Finding 7 is *stronger* with the sunset verdict than it would have been with a marginal-pass ship: the sunset is **harder to fake** because three independent model-class attempts on the same problem with the same frozen criteria produced consistent FAIL across mechanisms. Anyone replicating the experiment from the public spec gets the same answer.

Sustain is upside, not required. The bias-toward-strict-criteria instruction held under pressure. The pattern works.
