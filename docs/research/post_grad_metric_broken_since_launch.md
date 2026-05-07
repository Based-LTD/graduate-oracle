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
