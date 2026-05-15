# X Thread 05 — Finding 8 EMA fix verdict (branch variants)

**Status:** drafted; **NOT YET POSTED.** Ships via @GraduateOracle AFTER Finding 8 verdict resolves at 2026-05-09T16:45Z under the amended interim criteria. Same publish-then-post discipline as Threads 1, 2, 3, 4.

**Source writeups:** [`bucket_calibration_aliasing.md`](../bucket_calibration_aliasing.md) (full Finding 8 chain).

**Story shape:** closing-the-loop on the Finding 8 chain. Same shape as Thread 1 (Finding 7 sunset) — diagnosis → fix → frozen criterion → verdict → discipline takeaway → receipts.

**Three pre-registered verdict outcomes per the amended criterion (commit `f3f1f3e`):**

| Variant | Trigger | Sequence note |
|---|---|---|
| **5A** | EMA-fix-verified PASS + alert-volume PASS | Thread is "alerts back on; fix verified" — closing loop |
| **5B** | EMA-fix-verified PASS + alert-volume FAIL | Thread is "EMA fix worked; criterion-design conflation surfaced; what we learned about pre-registered criteria" — meta-discipline angle |
| **5C** | EMA-fix-verified FAIL with burst | Thread is "Path E executed; fixed-percentile cutoffs replace volume-target self-stabilization" — escalation-fired-cleanly angle |

**Currently most likely (per status snapshot 2026-05-08T18:01Z):** Variant 5B. Cumulative MED=0 in 25.9h post-deploy; trending toward "EMA fix prevents bursts" + "alert-volume gate fails." The amendment itself is the durable-receipts narrative if 5B fires.

**Recommended ship timing:** within 1-2 hours after the verdict commit lands publicly. Same shape as the Finding 7i sunset commit → Variant D (now Thread 1) cadence.

---

## Variant 5A — EMA-fix-verified PASS + alert-volume PASS

Triggers if the 48h interim window produces ≥1 daemon recompute without burst AND ≥1 MED prediction lands.

### Post 1 — hook

```
Bucket calibration EMA smoothing fix passed its pre-registered
48h interim acceptance gate.

No recompute aliasing bursts. Alert volume in the design range.
HIGH/MED/LOW alerts firing again on every pump.fun mint that
crosses the calibrated threshold.

Closing-the-loop thread on the Finding 8 chain.
```

### Post 2 — the original problem

```
1/ Two days ago: bucket alerts firing 697 in one hour, then zero
for the next 22.

Diagnosis: the volume-target cutoff daemon recomputes on a 24h
cadence over a rolling 7d window. When the cutoff dropped during
recompute, mints in the at-ceiling cluster that were just below the
old threshold suddenly qualified. Aliasing burst at every recompute
event.

Pre-registered acceptance criterion frozen pre-fix: max 1h MED count
≤30, ≥1 daemon recompute fires without burst within 48h, no
rebuild_failures.
```

### Post 3 — the fix + amendment

```
2/ Fix: EMA smoothing on raw_gbm_p_high cutoff transitions.

  smoothed = 0.2 * computed + 0.8 * previous

Persisted via JSON sidecar so smoothing survives daemon restart
(otherwise cold-start defeats it).

After 25h of post-deploy data, the original criterion was on track
to pass trivially because cumulative MED=0 — no bursts because no
MEDs at all. We surfaced the conflation and AMENDED the criterion
PRE-VERDICT (commit f3f1f3e):

  → EMA-fix-verified gate (no bursts)
  → Alert-volume gate (≥1 MED in 48h)

Strictly higher bar. Both must pass.
```

### Post 4 — the verdict

```
3/ Both gates passed. Amended interim criterion satisfied.

  Max 1h MED count: [N]/30 ✓
  Daemon recomputes without burst: [N] observed ✓
  rebuild_failures: 0 ✓
  ≥1 MED prediction in 48h: [N] observed ✓

Rules 9+10 re-enabled with content-inspection sample (10 alerts;
sensible content confirmed before declaring done).

Full 7d acceptance window continues through 2026-05-15.
```

### Post 5 — discipline takeaway (amendment as the durable artifact)

```
4/ The amendment IS the durable receipts moat from this chain.

The original criterion verified "no aliasing bursts." That was the
intended test. But it didn't verify "alert volume worth re-enabling
rules" — the criterion conflated two distinct concerns.

We caught the conflation BEFORE the verdict resolved the original
criterion. Amendment commit predates the verdict by ~22h. Strictly
higher bar; not a relaxation.

Pre-verdict amendment of frozen criteria is itself a discipline
pattern. Verification-by-content applies to the criteria themselves,
not just the system being tested.
```

### Post 6 — verify yourself

```
5/ Verify yourself.

Finding 8 chain (5 commits, 53be35f → 4d13430):

  53be35f → pre-registration + diagnostic plan
  790c8dd → diagnostic confirmed H1 (recompute aliasing)
  4d13430 → EMA fix landed
  70b4baf → interim 48h gate pre-registered
  f3f1f3e → interim criterion AMENDED pre-verdict
  [VERDICT_COMMIT] → verdict landed; criterion satisfied; rules
                     9+10 re-enabled

Amendment timestamp predates verdict by ~22h. Pre-registration of
both halves predates the data that resolved them.

github.com/Based-LTD/graduate-oracle
```

---

## Variant 5B — EMA-fix-verified PASS + alert-volume FAIL  ✅ **SHIPPED — this is the chosen variant**

**FIRED.** Verdict resolved at 2026-05-09T16:45:54Z. Numbers verified from `/data/data.sqlite`: 48h-window MED count = 0, HIGH = 0, LOW = 4305, max 1h MED = 0, rebuild_failures = 0. The verdict commit + writeup ship in the same commit as the [Finding 8 verdict resolution section in `bucket_calibration_aliasing.md`](../bucket_calibration_aliasing.md#interim-verdict-variant-5b-fired--2026-05-09t164554z). Below: ready-to-post copy with all `[N]` placeholders filled in. Posts 7h late (vs the pre-reg "within minutes of verdict" cadence) — the late ship is documented in the writeup so the receipts trail is honest about the discipline-pattern erosion.

### Post 1 — hook

```
The bucket calibration EMA smoothing fix did its job. Zero aliasing
bursts in the 48h verdict window.

But the volume-target calibration produced 0 MED predictions in the
same window — well below the rate that makes re-enabling alerts
worth it.

Per the amended interim criterion (pre-registered before the verdict
resolved), the fix is verified but rules 9+10 stay disabled. The
amendment itself is the load-bearing finding.
```

### Post 2 — the original problem + fix

```
1/ Two days ago: bucket alerts firing 697 in one hour, then zero
for the next 22. Diagnosis: cutoff-recompute aliasing.

Fix: EMA smoothing on raw_gbm_p_high transitions across recomputes,
with persistence sidecar so smoothing survives daemon restart.

Pre-registered interim criterion (frozen): max 1h MED ≤30, ≥1
recompute without burst, rebuild_failures=0.

By T+25h post-deploy: cumulative MED count = 0. No bursts because
no MEDs at all. Original criterion would PASS trivially.
```

### Post 3 — the conflation + amendment

```
2/ The original criterion verified "no aliasing bursts." It didn't
verify "alert volume worth re-enabling rules."

If we re-enabled rules 9+10 against the 0-MED distribution, users
would see ~6 days of silence until the full 7d gate failed for the
under-firing reason and Path E would execute. Worse than current
disabled state.

We surfaced the conflation BEFORE the verdict resolved. AMENDED
the criterion pre-verdict (commit f3f1f3e):

  → EMA-fix-verified gate (verifies the fix did its job)
  → Alert-volume gate (≥1 MED in 48h; verifies re-enable is worth it)

Both must pass to re-enable. Strictly higher bar; not a relaxation.
```

### Post 4 — the verdict

```
3/ Verdict at amended criteria:

  EMA-fix-verified gate: PASS
    → No bursts. Daemon recompute fired 2× over the 48h window
      without aliasing. rebuild_failures=0. Max 1h MED count = 0.

  Alert-volume gate: FAIL
    → 0 MED predictions in 48h. 0 HIGH. 4305 LOW. Volume-target
      calibration is producing zero non-LOW assignments —
      bimodal_cliff mode engaged because raw GBM scores saturate
      at the top sample (raw_p_high=0.98, ceiling_mass=15.5%).

Rules 9+10 stay disabled. Pre-registered next step: investigate WHY
the cutoff is producing zero MED, separate from the EMA-fix
verification. OR execute Path E (fixed-percentile cutoffs) directly.
Decision lands within 24h.
```

### Post 5 — what's actually durable

```
4/ The amendment IS the durable receipts artifact from this chain.

A team that pre-commits to a criterion, then notices the criterion
itself is design-flawed before the verdict resolves, then amends
publicly with strictly higher bar — that's the discipline pattern
operating recursively. Verification-by-content applied to the
criteria themselves, not just the system being tested.

Same shape as the Finding 7 sunset two days ago — when the data
says the simple framing of "did the fix work" doesn't fit, we
publish the more honest framing with receipts proving we caught
it pre-verdict.
```

### Post 6 — verify yourself

```
5/ Verify yourself.

Finding 8 chain timeline:

  53be35f → pre-reg + diagnostic plan
  790c8dd → diagnostic confirmed H1 (recompute aliasing)
  4d13430 → EMA fix landed
  70b4baf → interim 48h gate pre-registered
  f3f1f3e → CRITERION AMENDED pre-verdict (~22h before verdict)
  [VERDICT_COMMIT] → EMA-fix PASS + alert-volume FAIL; rules stay
                     disabled; cutoff-recalibration vs Path E
                     decision lands within 24h

Amendment commit predates verdict commit. The strict-vs-qualitative
divergence handling rule (added to memory after Lane 13) covered
the divergence properly.

github.com/Based-LTD/graduate-oracle
```

---

## Variant 5C — EMA-fix-verified FAIL with burst

**Triggers if a recompute aliasing burst (max 1h MED >30) fires within the 48h window.** Lowest probability based on current data (zero bursts observed at T+25h), but a real possibility if a delayed recompute event produces a discontinuity.

### Post 1 — hook

```
The bucket calibration EMA smoothing fix did NOT pass its
pre-registered 48h interim acceptance gate.

A recompute aliasing burst fired within the window — [N] MED
predictions in 1h, exceeding the 30/hour cap.

Per pre-registered Path E escalation (frozen 2026-05-07): revert
to fixed-percentile cutoffs without volume-targeting. Loses
self-stabilization; ships consistently. The escalation fired
cleanly without re-iterating.
```

### Post 2 — the diagnosis

```
1/ Two days ago: 697 MED predictions in one hour, then zero for
the next 22. Diagnosis: cutoff-recompute aliasing.

Fix: EMA smoothing on raw_gbm_p_high transitions across recomputes.

Result at T+[N]h: a [N]-MED burst fired anyway. The smoothing
dampened the cutoff transitions but didn't prevent the underlying
discontinuity at recompute time. The volume-target self-stabilization
is structurally fragile.
```

### Post 3 — pre-registered Path E fires

```
2/ Pre-registered Path E escalation (frozen at commit 53be35f,
two days before the verdict):

  If interim acceptance fails with burst: revert to fixed-percentile
  cutoffs (97th percentile of raw GBM scores in rolling 7d window).
  Loses volume-target self-stabilization. Ships consistently.

That fix shipped today at [PATH_E_COMMIT]. Bucket cutoffs now use
fixed percentile instead of volume-target derivation. Alerts fire
less often than the 10 MED/day target, but consistently — no
22-hour silences, no 2-hour bursts.

Iteration-limit at the cutoff-design level held: ONE EMA fix
attempt, FAIL = Path E. No fix-N+1 iteration on smoothing logic.
```

### Post 4 — discipline takeaway

```
3/ Pre-registered escalation that fires cleanly is the credibility
artifact.

The original Finding 8 EMA fix was specced + implemented + tested
in good faith. It failed its frozen acceptance criterion. We didn't
soften the threshold, didn't add a "well, it almost worked" hedge,
didn't loop into a Path D2 → D3 → D4 chain on the smoothing logic.

Path E was pre-registered as the stop-iterating escalation BEFORE
the EMA fix was even built. When the EMA fix failed, Path E executed
without fresh deliberation. That's the iteration-limit rule working
as designed.

The discipline pattern's most durable output isn't the fixes that
ship. It's the escalation rules that stop the iteration loop.
```

### Post 5 — verify yourself

```
4/ Verify yourself.

Finding 8 chain (escalation execution):

  53be35f → pre-reg with PATH E ESCALATION FROZEN
  790c8dd → diagnostic confirmed H1 (recompute aliasing)
  4d13430 → EMA fix landed (the attempt before Path E)
  70b4baf → interim 48h gate pre-registered
  f3f1f3e → criterion amended pre-verdict (split into two gates)
  [VERDICT_COMMIT] → EMA fix FAILED with burst at T+[N]h
  [PATH_E_COMMIT] → fixed-percentile cutoffs shipped per Path E

Path E was pre-registered TWO DAYS before its fire. The escalation
was designed to make today's commit mechanical, not deliberated.

github.com/Based-LTD/graduate-oracle
```

---

## Common shape across 5A / 5B / 5C

All three variants share:

- **Hook** opens with the verdict, no preamble
- **Posts 2-3** establish the original problem + pre-registered fix + criterion shape
- **Post 4** delivers the verdict
- **Post 5** is the discipline takeaway (which discipline-pattern element fired)
- **Post 6** is verify-yourself with the commit-hash receipts trail

This shape is reusable. Future verdict-resolution threads can use the same template (problem → fix → criterion → verdict → discipline → receipts) with branch-specific content filling each section.

## Posting cadence (across all three)

- All 6 posts shipped in one sitting on @GraduateOracle, ~30s apart.
- Best slot: within 1-2h after the verdict commit lands publicly. Don't space across days — the closing-the-loop arc loses momentum if interrupted.
- Sequence: Thread 5 ships AFTER Thread 1 + Thread 2 have landed. Thread 1 establishes the closing-the-loop precedent; Thread 2 establishes the pre-registered-discipline precedent for active studies; Thread 5 demonstrates the same pattern at the gate-resolution layer.

## Discipline note (frozen across all variants)

This thread's drafts commit publicly BEFORE the verdict resolves the criterion. Anyone auditing later can verify:
- Three branch variants existed at draft time
- Whichever variant fires matches the data and the pre-registered criterion
- Terminal numbers (`[N]` placeholders) fill in only AFTER the verdict, never retroactively edit the framing

Same shape as Case Study 01's three branch templates, applied to a different chain. The pattern is reusable.

## Cross-references

- Source writeup: [`bucket_calibration_aliasing.md`](../bucket_calibration_aliasing.md)
- Original interim criterion pre-reg: commit `70b4baf`
- Pre-verdict amendment: commit `f3f1f3e`
- Memory rule (pre-verdict amendment of frozen criteria): user's local `feedback_pre_registration_branches.md`
- Case Study 01 branch templates (analogous shape): [`case_study_01_gmgn_results_branch_a_template.md`](../case_study_01_gmgn_results_branch_a_template.md), [`branch_b_template.md`](../case_study_01_gmgn_results_branch_b_template.md), [`branch_c_template.md`](../case_study_01_gmgn_results_branch_c_template.md)
