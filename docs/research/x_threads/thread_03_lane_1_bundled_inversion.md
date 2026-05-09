# X Thread 03 — Lane 1 bundled corpus selection-bias inversion

**Status:** drafted; **NOT YET POSTED.** Ships via @GraduateOracle when user is ready. Same publish-then-post discipline as Threads 1 + 2.

**Source writeup:** [`lane1_bundled_corpus.md`](../lane1_bundled_corpus.md) (run 2026-05-04).

**Story shape:** counterintuitive result with clean reframe. The model's "obvious" pattern was inverted by the data. Surprising-result threads convert well on technical-crypto-Twitter when the inversion is unambiguous and the receipts are auditable.

**Recommended ship timing:** day 3-5 of cadence (~one thread per week per outreach_plan.md). Ships independently of Threads 1, 2, 4, 5 — no sequencing constraints.

---

## Thread structure (6 posts)

### Post 1 — hook (the surprising-result framing)

```
A WATCH alert pattern: 7/7 of our recent bundled-mint fires were
graduating. Looked obvious — the model was learning that bundled
manufactured pumps reliably bond on pump.fun.

We pre-registered a hypothesis check: ≥90% of resolved graduations
should be bundled if the model is just reflecting pool composition.

We found the opposite. The hypothesis flipped.
```

### Post 2 — the data

```
1/ n=2,975 resolved post-graduation outcomes with both bundle flags
present. We measured the actual bundled share of graduations:

  Bundled:     398 / 2,975 = 13.4%
  Not bundled: 2,577 / 2,975 = 86.6%

Pump.fun's graduation pool is ~87% NON-bundled. Not 90%+ bundled.
The 7/7 fires we'd been seeing weren't pool composition — they were
something else.
```

### Post 3 — the inversion (selection bias, not label leak)

```
2/ The 7/7-bundled-fires pattern wasn't the market. It was MODEL
SELECTION BIAS.

The k-NN was finding the 13% slice that's separable on the 6-feature
vector at age 30s — extreme top_buyer_pct, fast vsol_growth, low
n_trades — and missing the 87% non-bundled majority that doesn't
share that signature.

The label-leak hypothesis ("the model learned bundled=graduates
because that's what graduates") is REJECTED. Bundled isn't what
graduates. Bundled is what the MODEL picks when it's confident.
```

### Post 4 — the asymmetry (sustain rates)

```
3/ The non-bundled majority isn't just bigger. It sustains BETTER.

  Bundled graduates:     31.7% sustain ≥80% of grad price for 30m
  Non-bundled graduates: 53.1% sustain ≥80% of grad price for 30m

Non-bundled mints are 1.7× more likely to hold price post-bond.
Runner-2x rates are roughly equal (15-17%), so upside potential is
similar — the asymmetry is in downside (bundled rugs harder).

The model has been catching the worse-sustaining slice and missing
the better-sustaining majority.
```

### Post 5 — what this means strategically

```
4/ Two reframes from this finding:

The product's potential CEILING went UP, not down.

  "Narrow product on a narrow population (bundled-only)" is a
  permanent ceiling — capped by pump.fun's bundled launch rate.

  "Selection-biased model missing the better half" is a fixable
  problem with a clear lever — retrain with features that capture
  the non-bundled cluster's structure.

The decision rule was pre-registered: 13.4% < 80% → unlock the
investigation lane + base-rate analysis. The data did the work; we
ran the rule fresh against the result.
```

### Post 6 — verify yourself

```
5/ Verify yourself.

Pre-reg + run + decision rule all public:

  Lane 1 writeup:
    github.com/Dspro-fart/graduate-oracle/blob/main/docs/research/lane1_bundled_corpus.md

  Pre-registration of the ≥90%/<80%/80-90% decision branches:
    BACKLOG.md "Lane 1 — bundled-pump corpus check"

  Resulting investigation lanes (8, 9, 11, 12, 14):
    docs/research/lane{8,9,11,12,14}*.md

The pre-reg predates the data. The decision rule was applied fresh.
The investigation that fired is the result of the rule, not a
post-hoc reframing.

github.com/Dspro-fart/graduate-oracle
```

---

## Total length

~6 posts. Reads in ~2 min for engaged reader.

## Posting cadence

- All 6 posts shipped in one sitting, ~30s apart on @GraduateOracle.
- Independent of Threads 1, 2 — no sequencing constraint.
- Best slot: weekday US business hours UTC for technical-crypto-Twitter audience.

## Why this thread converts

**Surprising-result framing.** The hook ("our 'obvious' pattern was inverted by the data") catches attention without overselling. Posts 2-3 deliver the inversion mechanically — n=2,975, exact percentages, no editorializing.

**Concrete technical depth.** Post 4's sustain-rate asymmetry (1.7× difference, runner-2x parity) is the kind of detail that crypto-Twitter quants and technical readers will quote. The numbers are auditable from the writeup.

**Clean strategic reframe.** Post 5 inverts a potential weakness ("our model has bias") into a product-ceiling-going-up framing without softening the finding. The discipline pattern (pre-registered decision rule, applied fresh) is the connective tissue.

**Verify-yourself receipts.** Post 6 closes with the audit trail — Lane 1 writeup, BACKLOG pre-registration, downstream lane investigations the decision rule unlocked. Same shape as Threads 1, 2.

## Risk + mitigation

**Risk:** crypto-Twitter reads this as "their model is biased and has been catching the wrong stuff" — competitive ammunition.

**Mitigation:** the discipline framing inverts this. "We measured a bias, published the measurement, and unlocked a fix path" is more credible than teams that ship features without audit. Post 5 explicitly names the strategic upside.

**Risk:** the 28% no-predictions-row coverage gap (1,256 mints not in the analysis) gets pulled out of context.

**Mitigation:** Post 2's "n=2,975 resolved outcomes with both bundle flags present" is precise about the sample. The Lane 1 writeup discusses the coverage caveats; if a reader pushes on it, point them at the writeup. Pre-registration also discussed sensitivity to that gap.

**Risk:** the thread reads as too technical for general crypto-Twitter.

**Mitigation:** Post 1 hook is non-technical. Post 5 is non-technical. Posts 2-4 carry the depth. A reader who only sees Posts 1 + 5 + 6 still gets: "hypothesis tested, inversion found, here's the trail."

## Cross-references

- Source writeup: [`lane1_bundled_corpus.md`](../lane1_bundled_corpus.md)
- Downstream investigation lanes (8/9/11/12/14): [`docs/research/`](.. /)
- Memory rule (postmortem-survivorship-bias): user's local `feedback_postmortem_survivorship_bias.md`
