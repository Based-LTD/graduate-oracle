# Audit 12 — Branch C (insufficient sample / both fail baseline)

**Template — fill in terminal numbers at verdict time. Pre-drafted before
any data was collected, per pre-registration audit_12_hot_launch_composite_validation_prereg.md.**

---

## Verdict (fill at outcome)

```
Audit 12 collection window: [START_DATE] → [END_DATE]
Branch fired:                 C — insufficient sample / inconclusive
Sub-condition:                [n_below_100 | hit_rate_below_30pct]
Verdict ships:                [VERDICT_DATE]
```

---

## Result

The audit did not produce a verdict at the pre-registered confidence
threshold. One of the following sub-conditions fired:

```
Composite arm:
  n_total:    [COMPOSITE_N]   (target: ≥100)
  hit_rate:   [COMPOSITE_HIT_RATE]   (target: ≥30%)

grad_prob arm:
  n_total:    [GRADPROB_N]   (target: ≥100)
  hit_rate:   [GRADPROB_HIT_RATE]   (target: ≥30%)
```

**Sub-condition that fired:** [INSUFFICIENT_SAMPLE | BASE_RATE_FAILURE]

---

## Pre-registered decision tree (executed)

### If sub-condition was insufficient sample (n < 100 in either arm):

Extension: collection window extended by 14 days, ONE extension only.
New window: [EXTENSION_START] → [EXTENSION_END].

If extension still produces n < 100, audit is officially inconclusive
and the methodology design itself becomes the finding: "graduate-oracle's
in-lane prediction rate doesn't generate enough overlap with composite +
MC floor in 28 days for this comparison to be feasible."

That's a real finding about the addressable audience size — surfaces
that Audit 12's framing assumed more in-lane traffic than the system
actually produces.

### If sub-condition was base-rate failure (both arms hit rate < 30%):

Both filters underperform expectations against the naive base rate.
This surfaces the deeper question: what IS carrying signal in this
product?

Pre-register Audit 13 (broader feature exploration):
- Hypothesis-free: scan all available features for predictive lift
- 30-day forward window
- Pre-register acceptance criteria for what "found a signal" looks like

---

## What this means

A Branch C outcome doesn't mean the user's observation was wrong; it
means the structured audit didn't reach the pre-registered confidence
threshold. The discipline pattern requires that we publish the
inconclusive verdict and execute the pre-registered fallback path,
not iterate on the audit until we get the answer we wanted.

The original user observation (composite + MC floor "actually incredible")
remains a data point worth preserving in the project memory log even
without formal validation. Future audits or product iterations can
reference it.

---

## What ships next

- Public commit: this writeup + numbers + sub-condition filled in
- If sub-condition was insufficient sample: extension daemon configured
  + restart; same harness, same criteria, +14d
- If sub-condition was base-rate failure: Audit 13 pre-registration
  drafted within 7 days
- X post / TG announcement: pre-drafted variant in `x_post_audit_12_branch_c.md`
  (frames as "the audit didn't produce a clean verdict; here's what
  happens per pre-reg")

---

## Discipline note

Branch C is the most important branch to ship cleanly. A team that
publishes inconclusive results with the same discipline as wins or
losses demonstrates the receipts pattern is real. Audit programs that
quietly drop inconclusive results are doing performance discipline,
not epistemic discipline.

---

**Verify yourself:** every commit in the Audit 12 chain timestamped; the
pre-registration predates this verdict by 14+ days; the Branch C template
was committed publicly BEFORE any data was collected — proof that
inconclusive outcomes were a pre-registered possibility, not a
post-hoc face-saving framing.
