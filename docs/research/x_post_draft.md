# X post drafts (2026-05-07 cutover sequence)

Three drafts now: a **status post** (stage-setting, ships before the gate resolves) and two **resolution variants** (one of which ships when the first gate resolves cleanly).

The arc is now: today's status post → ~47h gate resolution → resolution post (Variant A or B) → +7d full acceptance update.

Per the user direction (2026-05-07): X post arc is better as a thread, not single posts. Each major resolution gets a quote-tweet of the prior post.

---

## ⚠️ V0 staleness note (2026-05-08)

The Variant 0 status post (drafted 2026-05-07, commit `442e4b9`) references the sustain auto-lift gate as "corpus rebuilding on clean data; auto-lift gate accumulating samples." **As of 2026-05-08, that claim is stale** — the sustain feature has now permanently sunset (Finding 7i, see `post_grad_metric_broken_since_launch.md`).

If V0 ships as-is today, it would be inaccurate on the sustain reference. Two options for the user:

- **(a) Update V0 to drop the sustain reference**, leaving only the bucket-calibration gate as the topic. Cleanest if shipping V0 alone.
- **(b) Skip V0; ship Variant D (below) directly.** Variant D claims the sustain sunset and absorbs the "honest about what's broken" framing without needing V0's stage-setting first.

Recommend option (b). The Variant 0 framing was about explaining a temporary pause; sustain is now permanently retired, which is a different (stronger) post category. Variant D leads with the verdict; the bucket-calibration gate gets covered in Variant A/B/C when its verdict lands.

---

## Variant 0 — status post (drafted 2026-05-07; SEE STALENESS NOTE ABOVE)

**When this ships:** as soon as it's been greenlit. Doesn't wait for a gate. **Stage-setting, not resolution.**

**Why ship a stage-setting post:** public silence about a deliberate alert pause reads worse than honest disclosure of why. Without a status post today, the audience moves on by the time the resolution post lands ~47h from now. The arc benefits from a "here's why we paused, here's the gate, here's the timeline" → "gate resolved, here's what shipped" two-post structure rather than a single resolution post that has to spend half its content explaining the silence.

**Distinct from Variants A/B (below):** those are resolution posts. This is a status post. Different category; precedes them; doesn't replace them.

```
Status update on graduate-oracle:

TG alerts are silent right now. Not broken — paused.

Yesterday we shipped a major model cutover (calibrated GBM + isotonic
+ HIGH/MED/LOW buckets replacing absolute-threshold alerts). In post-
cutover review, the discipline pattern caught a bucket calibration
aliasing pattern — alerts firing 697 in one hour, then zero for the
next 22. Bursty isn't useful.

So we paused alerts. Pre-registered a 48h interim acceptance gate. The
fix (EMA smoothing on the cutoff daemon) is deployed. Gate verdict
~2026-05-09 16:45 UTC.

Eight structural findings caught and resolved across the cutover
sequence. All publicly committed on github with timestamps that
predate their corresponding fixes. The discipline pattern works at
every checkpoint.

We chose alert silence over alert noise. Will post the resolution
either way.

Receipts: github.com/Based-LTD/graduate-oracle
Status: graduateoracle.fun/status
```

**Length:** ~200 chars in lede + ~600 chars body = ~3 posts in a thread on X.

**Key sentences (the moat in plain language):**
- "TG alerts are silent right now. Not broken — paused."
- "Bursty isn't useful."
- "We chose alert silence over alert noise."

**Continuity callback:** the same "alert silence over alert noise" line should land in the eventual resolution post (Variant A or B) as a callback, not as a fresh introduction. Two posts, one moat.

---

## Variant D — RETIRED 2026-05-09 (superseded by Thread 1)

**Status: superseded.** Per user direction (option a), [`x_threads/thread_01_finding_7_sunset.md`](x_threads/thread_01_finding_7_sunset.md) does Variant D's job with more depth. Thread 1's Post 1 leads with the same line ("post_grad_survival_prob is permanently retired") and then expands the receipts narrative across 6 follow-up posts. Variant D as a single-post announcement is redundant once the thread exists.

**Variant D draft below kept for historical receipts-trail integrity** — anyone auditing the drafts repository can confirm Variant D existed before Thread 1 was written, and the user's option-(a) decision is documented above. The body of Variant D is preserved verbatim as it was committed at `7658639`.

---

### Variant D body (preserved; SHIP THREAD 1 INSTEAD)

**When this ships:** as soon as it's been greenlit, today. Doesn't replace V0; **replaces V0 if V0 hasn't shipped yet.** Different post category — claims a permanent retirement, not a temporary pause.

**Why this category is stronger than V0's stage-setting:** V0 was "alerts paused for calibration; verdict in 47h." Variant D is "we tried three model-class attempts at sustain prediction; none passed pre-registered acceptance; the feature is permanently retired; here's the receipts trail proving we tried." That's a closing-the-loop post, not a setting-expectations post. Closing-the-loop posts are inherently more credible — they prove the discipline pattern produces verdicts under pressure, including the verdicts we didn't want.

```
post_grad_survival_prob is permanently retired.

Three model-class attempts on lane-60s sustain prediction. All three
failed pre-registered acceptance criteria.

  Path C  — z-score scaling on 5-dim k-NN. Distances exploded to 10^14
            from a 1e-6 floor on sparse dimensions. FAIL.

  Path D2 — log+z-score on 2 continuous + binary post-filter. Passed
            at small corpus; density-collapsed at large corpus on a
            (0,0,0)-signature-dominated dataset. FAIL.

  Path 7h — calibrated logistic regression with 15 interaction-term
            features. Found no within-signature signal; model 1.22pp
            WORSE than per-signature baseline on the only minority sig
            with n>=30. FAIL by 11.22pp from threshold.

Pre-registered iteration-limit at the model-class level fired: ONE
attempt; FAIL = permanent sunset.

Structural finding: lane-60s sustain prediction is not viable from
the available features given the signature distribution of resolved
graduates. Aggregate post_graduation.sustain_rate_30m continues —
that's the independent Jupiter measurement. The per-mint k-NN is
done.

Eleven public commits across 48 hours documenting the chain. Every
diagnosis timestamped before its fix or escalation. The receipts
moat is stronger with a sunset verdict than it would have been with
a marginal-pass ship — a sunset is harder to fake.

Sustain was upside, not required. The discipline held under pressure.

Receipts: github.com/Based-LTD/graduate-oracle/blob/main/docs/research/post_grad_metric_broken_since_launch.md
```

**Length:** ~1100 chars (multi-post thread on X).

**Common framing with V0/A/B/C:** "we chose alert silence over alert noise" doesn't apply here — sustain isn't paused, it's retired. The closing-equivalent line is: **"Sustain was upside, not required. The discipline held under pressure."**

**Decision rule for Variant D:** ship after the Finding 7i sunset commit lands publicly. Optionally pair with V0 update or skip V0 entirely (per staleness note above). Variants A/B/C remain held until the Finding 8 interim gate verdict at 2026-05-09T16:45Z.

**Quote-tweet structure (after Variant D ships):** when the Finding 8 interim gate resolves, the resolution post (A/B/C) quote-tweets Variant D for continuity. The receipts arc becomes: V-D (sustain retired, today) → A/B/C (bucket calibration verdict, ~22h) → +7d update (full Finding 8 acceptance close).

---

## Variant A — interim TG gate passes first (alerts back on with bucket framework)

Triggers: 2026-05-09T16:45Z. Acceptance: max 1h MED ≤30, ≥1 daemon recompute without burst.

**Posts as a quote-tweet of Variant 0** ("Status update on graduate-oracle: TG alerts are silent right now..."). Variant 0 does the silence-explanation work; Variant A claims the resolution.

```
gate verdict — alerts back on.

(quote-tweets the status post from 2026-05-07)

The 48h interim acceptance gate just passed clean. No recompute
aliasing, max 1h MED ≤30, ≥1 daemon recompute observed without
burst — exactly the criteria pre-registered before the gate
started running.

HIGH/MED/LOW bucket alerts firing again on every pump.fun mint that
crosses the calibrated threshold. Full 7d acceptance window continues
through 2026-05-15.

The silence is over. Alert silence over alert noise was the right
call — and the data says it ships under live calibration too.

Sustain prediction (Finding 7 chain) still in clean-corpus auto-lift
gate. Separate gate; will post when it resolves.

8 findings, 13 public commits, every diagnosis timestamped before
its fix.

Receipts: github.com/Based-LTD/graduate-oracle/commit/[FIX_LANDED_COMMIT]
Status: graduateoracle.fun/status
```

**Length:** ~700 chars (fits ~2 posts in a thread; quote-tweet structure means the original V0 post is visible in-thread).

**What V0 absorbed (no longer in Variant A):** the silence-explanation, the Finding 7d snapshot-source bug summary, the "Bursty isn't useful" explainer. All of that lives in Variant 0 now and shows in-thread when V0 is quote-tweeted.

**Continuity callback:** "Alert silence over alert noise was the right call" → this is the moat sentence from V0 returning as callback, not as fresh introduction. Reader who saw V0 recognizes it; reader who didn't sees the framing in-thread.

---

## Variant B — sustain auto-lift validates first (sustain restored before TG)

Triggers: corpus reaches n≥60 + 3 sigs OR 72h cap (deadline 2026-05-10T16:04Z), AND validation passes the three frozen Path D2 criteria.

**Posts as a quote-tweet of Variant 0.**

```
follow-up — sustain prediction restored.

(quote-tweets the status post from 2026-05-07)

post_grad_survival_prob is back online. The Path D2 metric
(log-z-score on 2 continuous dims + binary signature post-filter)
validated on clean-corpus k-NN at n≥60 against the three frozen
Finding 7c acceptance criteria. The sunset that ran since
2026-05-07 is lifted; sustain predictions firing again at honest
probabilities.

The Finding 7 chain — broken since launch → root cause located
→ metric replacement failed twice → Path E sunset → corpus rebuild
→ auto-lift validates — is itself an 8-commit demonstration of
discipline catching pre-existing pathologies surfaced by new
instrumentation.

TG alerts remain paused pending the separate bucket calibration
gate (verdict ~2026-05-09 16:45 UTC). Alert silence over alert
noise still holds for that one.

Receipts: github.com/Based-LTD/graduate-oracle/commit/[LIFT_COMMIT]
```

**Length:** ~870 chars.

**What V0 absorbed (no longer in Variant B):** the cutover-context preamble, the bucket-calibration explanation. V0's quote-tweet structure makes those visible in-thread; Variant B can lead directly with the sustain news.

**Continuity callback:** "Alert silence over alert noise still holds for that one" — different framing of the V0 moat sentence, signals that the alert pause persists for the bucket gate while sustain is now resolved.

---

## Variant C — hybrid (both gates resolve cleanly in the same window)

Triggers: both gates resolve cleanly within ~6h of each other. Could happen if sustain corpus accumulates fast AND bucket interim verdict lands close in time.

**Posts as a quote-tweet of Variant 0.**

```
both gates resolved.

(quote-tweets the status post from 2026-05-07)

Bucket calibration interim 48h gate passed clean. HIGH/MED/LOW
alerts firing again. Full 7d acceptance window continues through
2026-05-15.

Sustain prediction also restored. Path D2 metric (log-z-score on
2 continuous dims + binary signature post-filter) validated on
clean-corpus k-NN at n≥60 against the three frozen Finding 7c
acceptance criteria. Sunset lifted; sustain firing at honest probs.

Two pre-registered gates, two clean verdicts, within ~[N] hours of
each other. Alert silence over alert noise was the right call —
and the data says both calibrations ship clean.

8 findings caught + resolved across the cutover sequence; 13 public
commits in 28 hours; every diagnosis timestamped before its fix.

Receipts: github.com/Based-LTD/graduate-oracle
Status: graduateoracle.fun/status
```

**Length:** ~830 chars.

**Use this when:** both gates resolve within ~6h. Reduces the post-arc from 4 posts (V0 → A → B-quote-tweet → +7d update) to 3 (V0 → C → +7d update). Cleaner narrative; harder to schedule (depends on gate timing alignment).

---

## Common framing across all resolution variants (A, B, C)

- **"8 findings, 13 public commits, every diagnosis timestamped before its fix"** — central credibility claim. Identical wording.
- **"Alert silence over alert noise"** — moat sentence from Variant 0 returning as callback in resolution variants.
- **Specific commit-link receipts** — `[FIX_LANDED_COMMIT]` or `[LIFT_COMMIT]` placeholders get filled at posting time so the receipts link points at the *exact commit* that triggered the gate verdict, not just at the repo root.
- **Quote-tweet structure** — all three resolution variants quote V0. The status post does the framing work; the resolution variants do the verdict work.

## What the resolution variants do NOT do (revised post-V0)

- Do NOT re-explain the silence. V0 covered that; the quote-tweet structure surfaces V0 in-thread for any reader who didn't see it the first time.
- Do NOT claim everything is fixed. Both A and B honestly state remaining open gates; only C claims both resolved.
- Do NOT relax pre-registered criteria post-hoc. Same discipline as the technical work.
- Do NOT mention "we" or "the team" — receipts trail speaks for itself; ego dilutes credibility.
- Do NOT promise timelines beyond what the gates publicly state. The remaining gate's deadline is the only forward-looking claim.

## Decision rule for which variant to ship (revised post-V0)

Ship the variant matching the first gate that lands cleanly:
- **Bucket calibration interim gate passes** (max 1h MED ≤30, ≥1 recompute without burst) AND sustain still in auto-lift gate → **Variant A**
- **Sustain auto-lift validates** (all three Path D2 criteria) AND bucket gate not yet resolved → **Variant B**
- **Both resolve cleanly within ~6h** → **Variant C** (hybrid)
- **Sequential resolution >6h apart** → **Variant A or B first, then a quote-tweet of the resolution variant** when the second gate lands. Quote-tweet template:

```
follow-up to ↑ — [sustain | TG alerts] also resolved.
[one-sentence specific verdict description]
[Finding chain receipts link]
```

If a gate ESCALATES (Path E for Finding 8, OR Finding 7g/7h for sustain) instead of passing, the variant becomes a **different post entirely** — those drafts will be written when the escalation triggers (per the iteration-limit pre-registration discipline applied at messaging too).

## The publish-then-post sub-pattern (worth naming explicitly)

Pre-drafting these variants and committing them to public github BEFORE they ship as posts is itself a discipline-pattern application — same shape as publish-the-diagnosis-before-publishing-the-fix. The arc is:

```
T-1: draft committed to docs/research/x_post_draft.md
T+0: post goes live on X
T+ε: anyone can verify draft existed at T-1, post matches at T+0,
     pre-registered framing held under live conditions
```

This compounds the receipts moat at a meta level: drafts predate posts, posts predate user reactions, all timestamped publicly. A reader inspecting the trail can see that the framing was committed to before the verdict was visible — the post wasn't written under post-verdict optimism or panic, it was written ahead of time and held to a frozen criterion.

Same structure as Finding 8 EMA smoothing fix predating its acceptance gate, applied to messaging.

This sub-pattern goes into `feedback_pre_registration_branches.md` as a memory extension: **publish-then-post as a generalization of pre-fix-then-fix to the messaging surface.**

## Holding state until first verdict

Variant 0 (status post) is shipping today. Variants A/B/C remain held until the relevant gate resolves. The receipts trail (this file) is already public; the resolution variants are the *announcement* that brings traffic to the trail at the moment of verdict.

Premature posting of A/B/C (before any gate resolves) would commit to a state that hasn't actually verified. **The discipline that makes these posts worth reading is the same discipline that prevents premature posting.**
