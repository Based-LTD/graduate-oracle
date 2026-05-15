# X Thread 01 — Finding 7 chain sunset (closing-the-loop)

**Status:** drafted; **NOT YET POSTED.** Ships via @GraduateOracle when user is ready. Same publish-then-post discipline as case-study branch templates: this draft commits publicly BEFORE the post goes live so the timestamps are auditable.

**Subsumes:** Variant D (single-post sustain-sunset announcement at `docs/research/x_post_draft.md`). Per user direction: Thread 1's hook does Variant D's job with more depth; Variant D retires as redundant.

**Source writeups:** [`post_grad_metric_broken_since_launch.md`](../post_grad_metric_broken_since_launch.md) — full Finding 7 chain (7a → 7i).

**Receipts trail closing this thread documents:** 11 public commits across ~48 hours.

---

## Thread structure (7 posts)

### Post 1 — hook

```
post_grad_survival_prob is permanently retired.

3 model-class attempts on lane-60s sustain prediction over the
past 48 hours. All three failed pre-registered acceptance criteria.

Per pre-registered iteration-limit at the model-class level: ONE
attempt; FAIL = permanent sunset.

11 public commits documenting the chain. Closing-the-loop thread.
```

### Post 2 — Path C (max-scaling distortion → z-score → dimension collapse)

Tweak applied 2026-05-09 per user direction: removed "broken since the field deployed" framing from this post — it conflated Path C's max-scaling distortion with Path D2's snapshot-source bug (Finding 7d). Post 3 carries the broken-since-launch finding unambiguously.

```
1/ Path C — z-score scaling on 5-dim k-NN.

Max-scaling caused smart_money to dominate distance computation
while compressing other dimensions to near-zero. We replaced it
with z-score.

New problem: 3 of 5 features had near-zero variance, hit the
1e-6 divide-by-zero floor, distances exploded to 10^14.

FAIL.
```

### Post 3 — Path D2 (small-corpus pass → large-corpus density collapse)

```
2/ Path D2 — log-z-score on 2 continuous + binary post-filter.

Acknowledged that 3 of 5 features carry near-zero distance signal.
Used them as side-channel filters instead of distance contributors.

First validation at small corpus PASSED (median NN 2.27 in [0.5, 3.0]).

Then validation surfaced something deeper: 3/5 features had been
WRITING ZERO at graduation-time since launch. A snapshot-source bug.

Fixed the bug. Re-validated at clean corpus n=901.

FAIL — density collapse on dense (0,0,0)-signature corpus.
```

### Post 4 — Path 7h (calibrated LR, decisive fail)

```
3/ Path 7h — calibrated logistic regression with 15 interaction terms.

If k-NN couldn't find within-signature signal at scale, maybe an
explicit-interaction model could.

Stratified 5-fold CV. Frozen criteria. Pre-registered before the
experiment ran (commit 354024f predates the experiment).

Result: model 1.22pp WORSE than per-signature baseline on the only
minority signature with n>=30.

FAIL by 11.22pp from threshold.
```

### Post 5 — verdict (iteration-limit fires)

```
4/ Three structurally different model classes on the same data.

All three failed pre-registered acceptance with the same shape:
the lane-60s features don't carry within-signature signal sufficient
to produce calibrated per-mint sustain predictions at this corpus
shape.

Per pre-registered iteration-limit (frozen 2026-05-07): ONE
model-class attempt after 7g; FAIL = permanent sunset.

The feature is permanently retired.
```

### Post 6 — discipline takeaway

```
5/ The receipts moat is STRONGER with this sunset verdict than it
would have been with a marginal-pass ship.

Three independent attempts on the same problem with the same frozen
criteria producing consistent FAIL across mechanisms is harder to
fake than a single-attempt PASS would have been.

A team that publishes negative findings with the same discipline
as positive findings demonstrates the discipline is real, not theater.

Sustain was upside, not required. The bias-toward-strict-criteria
instruction held under pressure.
```

### Post 7 — verify yourself (commit-hash receipts)

```
6/ Verify yourself.

11 public commits across the Finding 7 chain (5296351 → 7658639):

  5296351 → diagnosis 7a/7b
  2d95a5a → Path C pre-reg
  c553d7f → 7c failed; D2 + Path E pre-reg
  707c169 → 7d sunset shipped
  45fb3b9 → 7e fix attempt
  2e615f4 → 7f deploy verification
  c3a83ef → 7f corrected fix
  ea6d5f5 → 7f validation deferred
  f3f1f3e → 7g re-validation FAIL
  354024f → 7h pre-reg (predates experiment)
  7658639 → 7h FAIL + 7i sunset

Every diagnosis timestamped before its fix or escalation. The
audit trail is the receipts.

github.com/Based-LTD/graduate-oracle
```

---

## Total length

~6 posts of moderate length (~300-500 chars each in the actual posts) + closing receipts post. Reads in ~2 minutes for the engaged reader.

## Posting cadence (recommended)

- **All 7 posts shipped in one sitting**, ~30s apart, as a single thread on @GraduateOracle.
- Don't space across days — the closing-the-loop arc loses momentum if interrupted.
- Best time slot: weekday US business hours UTC for max engagement on technical-crypto-Twitter audience.

## Why this subsumes Variant D

Variant D was a single-post announcement: "post_grad_survival_prob is permanently retired." Thread 1's Post 1 leads with the same line but the thread expands it into the full receipts narrative. Per user direction (option a): ship Thread 1; retire Variant D as redundant.

`docs/research/x_post_draft.md` updated in the same commit cycle to mark Variant D as superseded.

## Risk + mitigation

**Risk:** thread is long for a non-technical reader. They drop off before Post 5's discipline takeaway.

**Mitigation:** Post 1's hook + Post 7's verify-yourself bookends do most of the work. A reader who only sees those two posts gets: "they retired a feature, here are the commits proving it." The middle 5 posts are for the engaged technical reader who wants the mechanism detail.

**Risk:** competitive surface — Trojan/Axiom/etc. read this and learn that graduate-oracle's lane-60s sustain prediction doesn't work, ammunition for "your competitor admits their feature failed."

**Mitigation:** the discipline framing inverts this. "We tried, we measured, we published" is a credibility asset against teams that ship features without disclosing failures. Plus: the aggregate `post_graduation.sustain_rate_30m` (independent Jupiter measurement) continues unaffected, so the broader sustain claim is unaffected.

**Risk:** thread reads as defeatist or apologetic.

**Mitigation:** Post 6 explicitly frames the sunset as receipts-positive, not negative. The bias-toward-strict-criteria language reinforces "we chose to publish the negative finding rather than soften the threshold to a pass."

## Cross-references

- Source writeup: [`post_grad_metric_broken_since_launch.md`](../post_grad_metric_broken_since_launch.md)
- Memory rule (iteration-limit at model-class level): user's local `feedback_pre_registration_branches.md`
- Variant D (superseded): [`x_post_draft.md`](../x_post_draft.md)
