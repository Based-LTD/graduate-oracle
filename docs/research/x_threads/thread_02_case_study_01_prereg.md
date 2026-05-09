# X Thread 02 — Case Study 01 pre-reg (pre-commit-to-negative-findings)

**Status:** drafted; **NOT YET POSTED.** Ships via @GraduateOracle when user is ready. Same publish-then-post discipline as Thread 1.

**Source writeup:** [`case_study_01_gmgn_comparison_prereg.md`](../case_study_01_gmgn_comparison_prereg.md) (commit `5bc8f33`).

**Strategic framing per user direction:** make explicit that "we're running a study where the data could undermine our own product thesis, and here's the pre-registered branch where that triggers a product reshape." Most teams don't do this. Crypto-Twitter notices when a team pre-commits publicly to publishing negative findings.

**Recommended ship timing:** AFTER Thread 1 lands (Finding 7 sunset is the precedent that makes Thread 2's commitment credible) and AFTER trigger fires (so "the study is running RIGHT NOW" is true at post time, not aspirational).

---

## Thread structure (7 posts)

### Post 1 — hook (the unusual thing)

```
We're running a study right now where the data could undermine
our own product thesis. The pre-registered branch where that
triggers — and the public commitment to publish the negative
finding — is committed publicly, BEFORE any data lands.

Most teams shipping a product don't do this. We pre-commit because
the receipts moat depends on it.
```

### Post 2 — the thesis under test

```
1/ The thesis: graduate-oracle's calibrated lane-60s HIGH+MED bucket
outperforms component-data composition (specifically GMGN's
--filter-preset strict --type new_creation) on graduation precision,
on the same overlapping mint set.

If true: the calibrated-bucket-alone product spec IS the
differentiator.

If false: the spec is wrong, and the product reshapes around what
the data shows actually IS adding value.
```

### Post 3 — methodology (frozen pre-data)

```
2/ Pre-registration commit 5bc8f33 (2026-05-08), frozen BEFORE
data collection:

  • 48h window starting 2026-05-09T16:05Z
  • 60s polls of both APIs
  • ±120s mint matching
  • 24h grace for outcome resolution
  • ≥10pp precision lift on n≥30 overlap to claim "thesis supported"
  • 3 pre-registered branches: PASS / FAIL / inconclusive

All 3 branch templates (the actual writeup language) are pre-drafted
and committed publicly. Terminal numbers fill in at outcome time.
```

### Post 4 — Branch B explicit

```
3/ Branch B (frozen, pre-drafted): if graduate-oracle does NOT
outperform GMGN strict-preset by ≥10pp, OR difference is within
±5pp of zero:

  → Public writeup ships with the negative finding
  → Product-reshape discussion opens with concrete data anchoring
    what does add value (maybe receipts trail itself, maybe hybrid
    bucket+component output, maybe different lane window)
  → Pre-registered iteration-limit applies: this triggers a
    product-spec REOPEN, not "let's run another comparison until we win"

The team has explicitly committed to reshape if the data demands it.
```

### Post 5 — precedent (Finding 7 sunset)

```
4/ This isn't a hypothetical commitment.

48 hours ago we permanently sunset post_grad_survival_prob after
three model-class attempts all failed pre-registered acceptance:
github.com/Dspro-fart/graduate-oracle/blob/main/docs/research/post_grad_metric_broken_since_launch.md

11 commits across the Finding 7 chain. Each diagnosis timestamped
before its fix or escalation. When the data said the feature didn't
work, we published that verdict with the receipts proving we tried.

Sustain was upside, not required. Calibrated bucket alone is also
upside. Same discipline either way.
```

### Post 6 — why this is unusual

```
5/ Most teams shipping a product don't pre-commit to publishing
results that undermine the product's positioning.

Most studies are designed retrospectively to find the framing
that supports the conclusion the team wants.

Pre-registration + pre-drafted branch templates + public commit
predating data collection makes the retrospective-framing path
mechanically unavailable.

The discipline IS the moat. A team can't quietly redefine what
"success" means after the data lands if the success criterion is
already public, timestamped, and auditable.
```

### Post 7 — verify yourself (commit-hash receipts)

```
6/ Verify yourself.

  Pre-registration:    5bc8f33  (frozen methodology + 3 branch templates)
  Phase 2 scaffold:    08fb96c  (case_study_harness/ source tree)
  Bug fixes (verification cycle): c96f394, 7cd88a6
  Trigger fires:       2026-05-09T16:05Z

Daemon is currently in trigger-wait state on graduate-oracle.fly.dev.
Branch verdict + writeup commits will land ~2026-05-11T18:00Z.

When that commit appears, you can verify:
  → It cites whichever pre-drafted branch (A/B/C) actually fired
  → Terminal numbers match the frozen criteria
  → Commit timestamp predates any retroactive framing

github.com/Dspro-fart/graduate-oracle
```

---

## Total length

~7 posts. Reads in ~2.5 min for engaged reader. Hook + verify-yourself bookends carry most of the load if reader skims.

## Posting cadence

- **Single thread, all 7 posts shipped in one sitting**, ~30s apart on @GraduateOracle.
- **Recommended ship time:** after Thread 1 (Finding 7 sunset) AND after Case Study 01 trigger fires (~2026-05-09T16:05Z). Both conditions need to be true so:
  - Thread 1 establishes the precedent of "we publish negative findings with discipline"
  - Trigger fired = Thread 2's "the study is running RIGHT NOW" is literal, not aspirational
- If Thread 1 ships late and trigger has already fired: ship Thread 2 anyway with a small framing tweak (Post 1 says "We're running a study" — change to past tense if data collection has started without Thread 2 having been posted yet).

## Why this thread is structurally important

Per user direction: "Most teams don't pre-commit to publishing negative findings. The thread should make that explicit, not implicit."

Posts 1 + 4 + 6 do that work explicitly:
- Post 1: hook frames the unusual commitment up front
- Post 4: Branch B's pre-registered "publish the negative finding + reshape product" action is named in detail
- Post 6: the "unusual thing" framing is named directly — "Most teams shipping a product don't pre-commit to publishing results that undermine the product's positioning."

This isn't a sales pitch or marketing copy. It's the discipline pattern operating at the social-media surface — same epistemic shape as Finding 7's audit trail, applied to public-facing content.

## Risk + mitigation

**Risk:** competitors (GMGN team specifically) read this as a public attack on their composition. They retaliate with their own benchmarks claiming graduate-oracle loses.

**Mitigation:** Thread 2 doesn't claim graduate-oracle wins. It explicitly names the possibility that graduate-oracle loses ("If false: the spec is wrong, and the product reshapes"). The frame is "we're running a clean comparison and committing to publishing whichever direction the data points." That's harder to attack than "we beat GMGN" claims.

If GMGN wants to publish their own pre-registered comparison, that's actually GOOD for the receipts moat — sets the standard at the ecosystem level.

**Risk:** if Branch B fires (graduate-oracle does NOT outperform), the eventual results post is in some sense an embarrassing one. Did we set ourselves up for that?

**Mitigation:** that's the point of Thread 2 — naming the possibility EXPLICITLY before the data lands inverts the embarrassment. A team that pre-commits to publish a negative result and then publishes it is more credible than one that quietly drops the comparison after seeing the data. Branch B's writeup template (`case_study_01_gmgn_results_branch_b_template.md`, also pre-drafted publicly) makes the eventual negative-result post a continuation of the discipline narrative, not a contradiction.

**Risk:** Thread 2 reads as performative — "look how disciplined we are."

**Mitigation:** the receipts are the antidote to performance. The thread closes with commit hashes anyone can verify. Posts 4 + 5 reference specific frozen criteria + the Finding 7 sunset precedent, both of which are independently auditable. The thread documents the discipline; readers verify whether the discipline is real.

## Cross-references

- Source pre-reg: [`case_study_01_gmgn_comparison_prereg.md`](../case_study_01_gmgn_comparison_prereg.md)
- Branch templates: [`case_study_01_gmgn_results_branch_a_template.md`](../case_study_01_gmgn_results_branch_a_template.md), [`branch_b_template.md`](../case_study_01_gmgn_results_branch_b_template.md), [`branch_c_template.md`](../case_study_01_gmgn_results_branch_c_template.md)
- Precedent (Thread 1): [`thread_01_finding_7_sunset.md`](thread_01_finding_7_sunset.md)
- Memory rule (publish-then-post applied to social media): user's local `feedback_pre_registration_branches.md`
