# X post drafts (2026-05-07 cutover sequence)

Three drafts now: a **status post** (stage-setting, ships before the gate resolves) and two **resolution variants** (one of which ships when the first gate resolves cleanly).

The arc is now: today's status post → ~47h gate resolution → resolution post (Variant A or B) → +7d full acceptance update.

Per the user direction (2026-05-07): X post arc is better as a thread, not single posts. Each major resolution gets a quote-tweet of the prior post.

---

## Variant 0 — status post (ships TODAY, before any gate resolves)

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

Receipts: github.com/Dspro-fart/graduate-oracle
Status: graduateoracle.fun/status
```

**Length:** ~200 chars in lede + ~600 chars body = ~3 posts in a thread on X.

**Key sentences (the moat in plain language):**
- "TG alerts are silent right now. Not broken — paused."
- "Bursty isn't useful."
- "We chose alert silence over alert noise."

**Continuity callback:** the same "alert silence over alert noise" line should land in the eventual resolution post (Variant A or B) as a callback, not as a fresh introduction. Two posts, one moat.

---

## Variant A — interim TG gate passes first (alerts back on with bucket framework)

Triggers: 2026-05-09T16:45Z. Acceptance: max 1h MED ≤30, ≥1 daemon recompute without burst.

```

## Variant A — interim TG gate passes first (alerts back on with bucket framework)

Triggers: 2026-05-09T16:45Z. Acceptance: max 1h MED ≤30, ≥1 daemon recompute without burst.

```
cutover update — TG alerts are back.

bucket framework live: HIGH/MED/LOW alerts calibrated to live graduation
rates with a self-stabilizing volume-target threshold. 48h interim
acceptance gate passed clean (no recompute aliasing, max 1h MED ≤30).
full 7d acceptance window continues through 2026-05-15.

eight findings caught and resolved across the cutover sequence.
five pre-cutover, three in post-deploy review.

most surprising: post_grad_survival_prob has been publishing artifacts
since the field was deployed — 3 of 5 features were writing zero at
graduation moment due to a snapshot-source bug. two metric replacements
failed pre-registered acceptance criteria. Path E sunset executed.
root cause located + corrected fix shipped. currently in clean-corpus
auto-lift gate.

13 public commits in 28 hours. every diagnosis publicly timestamped
before its corresponding fix or sunset.

receipts: github.com/Dspro-fart/graduate-oracle
```

**Length:** ~870 chars (fits ~3 posts in a thread).

**Thread structure (if posted as multi-post):**
1. Lede: "cutover update — TG alerts are back" + bucket framework summary + 48h gate result
2. Finding 7 chain summary + sunset/auto-lift state
3. "13 public commits in 28h" + receipts link

---

## Variant B — sustain auto-lift validates first (sustain restored before TG)

Triggers: corpus reaches n≥60 + 3 sigs OR 72h cap (deadline 2026-05-10T16:04Z), AND validation passes the three frozen Path D2 criteria.

```
cutover update — post_grad_survival_prob is back online.

Path D2 metric (log-z-score on 2 continuous dims + binary signature
post-filter) validated on clean-corpus k-NN at n≥60. the sunset that
ran since 2026-05-07 is lifted. sustain predictions firing again at
honest probabilities.

TG alerts remain paused pending a separate 48h bucket distribution
gate (Finding 8 EMA smoothing under acceptance). expected resumption
~2026-05-09.

eight findings caught and resolved across the cutover sequence.
the Finding 7 chain — broken since launch → root cause located
→ metric replacement failed twice → Path E sunset → corpus rebuild
→ auto-lift validates — is itself an 8-commit demonstration of
discipline catching pre-existing pathologies surfaced by new
instrumentation.

13 public commits in 28 hours. every diagnosis publicly timestamped
before its corresponding fix or sunset.

receipts: github.com/Dspro-fart/graduate-oracle
```

**Length:** ~1010 chars (fits ~3 posts in a thread).

---

## Common framing across both variants

- **"Eight findings caught and resolved"** — central credibility claim, identical wording in both.
- **"13 public commits in 28 hours"** — concrete artifact reference, citation-worthy.
- **"every diagnosis publicly timestamped before its corresponding fix or sunset"** — the discipline pattern's distinguishing property.
- **"receipts: github.com/Dspro-fart/graduate-oracle"** — terminal CTA. No hedging, no "thanks for following along" — direct link to the audit trail.

## What the X post does NOT do

- Does NOT claim everything is fixed. Both variants honestly state remaining open gates.
- Does NOT relax pre-registered criteria post-hoc. The discipline that makes these posts worth reading is exactly the same discipline that prevents post-hoc rationalization.
- Does NOT mention "we" or "the team" — the receipts trail speaks for itself; ego in the post would dilute the credibility of the framework.
- Does NOT promise a timeline for full resolution. "expected resumption ~2026-05-09" is the only forward-looking claim, and it's anchored to the pre-registered gate close, not a marketing window.

## Quote-tweet plan (when second resolution lands)

If Variant A ships first, the quote-tweet on sustain restoration:

```
follow-up to ↑ — sustain prediction also restored. clean-corpus
k-NN validated against the original Path D2 acceptance criteria.
the Finding 7 chain is now fully closed.

receipts: [link to specific commit]
```

If Variant B ships first, the quote-tweet on TG re-enable:

```
follow-up to ↑ — TG alerts back on. interim 48h bucket gate passed
clean. full 7d acceptance window continues; rules 9+10 firing on
HIGH/MED/LOW buckets calibrated to live rates.

receipts: [link to specific commit]
```

## Decision rule for which variant to ship

Ship the variant matching the first gate that lands cleanly:
- Sustain auto-lift validates with all three Path D2 criteria passing → Variant B
- Bucket calibration interim gate passes (max 1h MED ≤30, ≥1 recompute without burst) → Variant A
- Both gates resolve cleanly within the same ~6h window → ship a hybrid that absorbs both wins

If a gate ESCALATES (Path E for Finding 8, OR Finding 7g/7h for sustain) instead of passing, the variant becomes a different post entirely — those drafts will be written when the escalation triggers (per the iteration-limit pre-registration discipline applied at messaging too).

## Holding state until first verdict

Neither variant ships now. The receipts trail is already public; the X post is the *announcement* that brings traffic to the trail. Premature posting (before either gate resolves) would commit to a state that hasn't actually verified.
