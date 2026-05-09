# X Thread 04 — Lane 13 transition-zone framing (methodology depth)

**Status:** drafted; **NOT YET POSTED.** Ships via @GraduateOracle when user is ready.

**Source writeup:** [`lane13_calibration_stability.md`](../lane13_calibration_stability.md) (run 2026-05-05 evening).

**Story shape:** pure methodology piece. Appeals to quants and rigor-minded technical readers. The discipline-pattern improvement that emerged from this finding is more durable than the specific result.

**Recommended ship timing:** following week of cadence (week 2 or 3). Ships independently of other threads — pure methodology piece, no precedent dependencies.

---

## Thread structure (6 posts)

### Post 1 — hook (the design flaw, named directly)

```
We pre-registered a 4-branch decision rule for a calibration-
stability analysis. Real data didn't fit any branch cleanly.

Strict rule fired "unclear → bug hunt." Qualitative read said
"this is mechanism 2 with noise."

Both reads were correct. The branches were under-specified.

Methodology thread on what we changed about pre-registration
discipline as a result.
```

### Post 2 — the original 4 branches

```
1/ The original Lane 13 decision rule, frozen pre-data:

  1. Single flip, recent only → mechanism 1 (sample bias)
     → action: wait, monitor, sharpen caveat
  2. Monotonic drift over→under → mechanism 2 (curve overshoot)
     → action: slow rebuild cadence
  3. ≥3 direction flips → mechanism 3 (oscillating instability)
     → action: structural redesign
  4. No clear pattern → mechanism 4 → action: bug hunt

Standard pre-reg shape. Mutually exclusive verdicts. Discrete
actions per branch. Frozen before the data lands.
```

### Post 3 — what the data actually showed

```
2/ The data: signed delta calibration over 7 days, 4 prediction
tiers (2x/3x/5x/10x_from_now), stepped 12h windows.

Per tier, the strict rule fired:
  2x_from_now: 2 crossings, +5.7pp drift → single-flip
  3x_from_now: 3 crossings, +10.7pp drift → OSCILLATING
  5x_from_now: 0 crossings, -1.4pp drift → stable
  10x_from_now: 2 crossings, +20.8pp drift → single-flip

Three of four tiers don't agree on a mechanism. By the strict rule,
that's "no clear pattern → bug hunt."

But qualitatively all four tiers show the same shape: monotonic
drift toward zero with noise crossings. Mechanism 2 with high variance.

Strict says one thing. Qualitative says another.
```

### Post 4 — the divergence handling

```
3/ Pre-registered protocol (frozen 2026-05-05) for strict-vs-
qualitative divergence:

  Three obligations:
  → Flag the divergence publicly in the writeup. Don't hide it.
  → Pick an action covering BOTH verdicts. If incompatible,
    the more cautious one.
  → Update the pre-registration rule before the next analysis.

  One prohibition:
  → Don't decide "what's right" privately and document later.
    That's how p-hacking starts.

We did all three. Action: hybrid (slow rebuild cadence per
mechanism 2 + light bug-hunt per mechanism 4). Rule updated for
next pre-registration.
```

### Post 5 — the methodology rule that emerged

```
4/ The rule that emerged from Lane 13 (now in our memory file):

  Pre-registered branches are evidence-types not exclusive verdicts.
  When real data triggers multiple branches OR sits in a transition
  zone between them, the pre-registration was under-specified.

Three corrections for the next pre-reg:

  1. Crossings count needs a magnitude threshold. "≥3 sign changes
     with each crossing reaching ≥|3pp|" filters out noise crossings.
  2. Always include an explicit transition-zone branch — monotonic
     drift WITH noise crossings is its own evidence type.
  3. Divergence handling: flag → cover both verdicts → update rule
     BEFORE next pre-reg. The publicness is what distinguishes this
     from p-hacking.
```

### Post 6 — verify yourself

```
5/ Verify yourself.

  Lane 13 writeup (full divergence detail):
    github.com/Dspro-fart/graduate-oracle/blob/main/docs/research/lane13_calibration_stability.md

  Pre-registration of the original 4 branches:
    BACKLOG.md "Lane 13 — calibration stability analysis"

  The rule update predates the next pre-registration that uses it.
  That ordering is the discipline.

When pre-registered branches don't fit the data, the discipline
isn't to pick a branch and call it a day. It's to flag the gap
publicly + cover both verdicts in the action + update the rule
before next time. The pattern compounds.

github.com/Dspro-fart/graduate-oracle
```

---

## Total length

~6 posts. Reads in ~2 min. Audience: quants, methodology-rigor crowd, technical leads at integration-prospect companies.

## Posting cadence

- All 6 posts shipped in one sitting, ~30s apart on @GraduateOracle.
- No sequencing dependency on Threads 1, 2, 3, 5.
- Best slot: weekday US business hours UTC.

## Why this thread converts (different audience than Thread 3)

**Thread 3 is a surprising-result thread.** Audience: traders + product-strategy readers who want "huh, that's counterintuitive."

**Thread 4 is a methodology depth thread.** Audience: quants + technical leads + rigor-minded engineers who want "the discipline pattern itself, named directly."

Each thread converts a different segment of the same broad technical-crypto-Twitter audience. The portfolio reach matters more than any single thread's peak engagement.

## Why this matters strategically

The Lane 13 rule update is a compounding asset. Every future pre-registration that uses the magnitude-threshold + transition-zone + divergence-handling improvements is a downstream beneficiary. Thread 4 documents the meta-improvement publicly so future commits can reference it ("per Lane 13 rule update, we pre-registered branches with magnitude thresholds...") and an outside reader can verify the rule update predates the pre-reg.

This is the receipts moat operating at the methodology layer — same shape as Finding 7's Sunset audit trail, but applied to the rules themselves, not just the system being tested.

## Risk + mitigation

**Risk:** thread reads as inside-baseball methodology talk that doesn't connect to product value.

**Mitigation:** Post 5's rule extracts are practical and reusable. Anyone running their own pre-registered analyses (academia, ML teams, prediction markets) can adopt the magnitude-threshold + transition-zone + divergence-handling structure. The methodology is exportable; the thread documents it cleanly.

**Risk:** Post 3's tier-by-tier breakdown is dense. Reader drops off.

**Mitigation:** Post 4 + 5 carry the takeaway without requiring Post 3's detail. A reader who skims to Post 5 still gets "they had a divergence, they handled it publicly, here's the rule that emerged."

**Risk:** Post 4's "p-hacking" line gets quoted out of context.

**Mitigation:** the post explicitly names the prohibition (don't decide privately and document later) AND the antidote (publicness distinguishes the discipline from p-hacking). Quote-out-of-context risk is low because the framing is self-defending.

## Cross-references

- Source writeup: [`lane13_calibration_stability.md`](../lane13_calibration_stability.md)
- Companion lane (multi-branch fire case): [`lane14_bundled_regression.md`](../lane14_bundled_regression.md)
- Memory rule (full discipline pattern with Lane 13 origin): user's local `feedback_pre_registration_branches.md`
