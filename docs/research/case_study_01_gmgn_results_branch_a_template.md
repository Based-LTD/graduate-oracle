# Case Study 01 — Results (Branch A: thesis supported)

**TEMPLATE — committed pre-data-collection alongside `case_study_01_gmgn_comparison_prereg.md` per the publish-then-post discipline.** This file's terminal numbers fill in at outcome time; the structure, framing, and acceptance-criterion verdict are frozen pre-collection.

**Branch A fires when ALL of:**
1. Sample size: ≥30 mints in graduate-oracle MED+HIGH bucket on overlap
2. Precision: graduate-oracle MED+HIGH precision ≥ GMGN strict-preset precision + 10pp
3. Lift holds when restricted to mints where BOTH products made a positive call

**Action on Branch A fire:** rename this file to `case_study_01_gmgn_results.md` (drop the `_template_branch_a` suffix), fill the bracketed `[NUMBER]` fields with terminal data, commit + push, then ship X thread + TG message.

---

## Headline

**graduate-oracle's calibrated lane-60s bucket outperforms GMGN's `--filter-preset strict --type new_creation` by `[X]`pp on the same mint set, n=`[N]`.**

Methodology pre-registered at [`case_study_01_gmgn_comparison_prereg.md`](case_study_01_gmgn_comparison_prereg.md), commit `[PREREG_COMMIT_HASH]`. Data collection ran `2026-05-09T[HH]:00Z` to `2026-05-11T[HH]:00Z` (48h window) plus 24h outcome-resolution grace. This writeup commits at `[RESULTS_COMMIT_HASH]`, `[RESULTS_COMMIT_TIMESTAMP]`.

---

## The numbers

### Sample state at analysis

```
Total mints captured during 48h window:   [TOTAL_CAPTURED]
Mints in graduate-oracle's lane-60s scope: [GO_LANE]
Mints in GMGN strict-preset:               [GMGN_STRICT]
Mints in BOTH (overlap):                   [OVERLAP_N]
Mints excluded per pre-reg rules:          [N_EXCLUDED]
  - unresolved at 24h grace:               [N_UNRESOLVED]
  - outside lane-60s (age >75s):           [N_OUT_OF_LANE]
  - GMGN snapshot >120s offset:            [N_TIMING_FAIL]
  - already graduated at first observation: [N_ALREADY_GRAD]
```

### Precision comparison

```
                         | precision (graduated/total) | n
─────────────────────────┼─────────────────────────────┼──────
graduate-oracle MED+HIGH | [GO_PRECISION]%             | [N_GO]
GMGN strict-preset       | [GMGN_PRECISION]%           | [N_GMGN]
both products positive   | [BOTH_PRECISION]%           | [N_BOTH]
```

**Precision lift on overlap:** graduate-oracle outperforms GMGN by **`[LIFT_PP]`pp** (`[GO_PRECISION]`% − `[GMGN_PRECISION]`% = `[LIFT_PP]`pp). Threshold for Branch A was ≥10pp; observed lift is `[LIFT_PP]`pp.

**Both-products-positive subset:** when both graduate-oracle's MED+HIGH bucket AND GMGN's strict-preset called the mint a winner, `[BOTH_PRECISION]`% actually graduated (n=`[N_BOTH]`). graduate-oracle's lift is consistent on this subset (`[BOTH_LIFT_PP]`pp over GMGN-only-positive subset), satisfying acceptance criterion 3.

### Resolution rate

```
Total mints in overlap:       [OVERLAP_N]
Resolved within 24h grace:    [N_RESOLVED]  ([RESOLUTION_RATE]%)
```

Above the 70% resolution-rate floor; the comparison is statistically interpretable.

---

## What the result means

graduate-oracle's calibrated bucket isn't just a labeled output — it's a **measurably better predictor** than GMGN's component-composition strict-preset on the same mints, by a lift large enough to matter for downstream alert quality.

**For B2B integration partners:** integrating graduate-oracle's `should_we_alert(mint) → bool` saves the consumer from building their own composition-and-calibration pipeline AND produces a `[LIFT_PP]`pp precision lift over the closest off-the-shelf alternative. That's the empirical foundation for the B2B integration thesis the cutover sequence was building toward.

**For the receipts moat:** the methodology was pre-registered before data collection (commit `[PREREG_COMMIT_HASH]`), the result was branch-executed per frozen acceptance criteria (no post-hoc threshold relaxation), and the writeup commits with terminal numbers filled in. Anyone replicating the experiment from the public spec gets the same data shape; anyone auditing the timestamps confirms the pre-reg predates the data.

---

## What this DOES NOT claim

- This study tests **lane-60s graduation precision**, NOT post-graduation outcome quality (sustain, peak multiplier). Sustain prediction was permanently sunset per Finding 7i; this study doesn't reopen that.
- This study tests graduate-oracle's MED+HIGH bucket **vs GMGN's specific strict-preset configuration**. It does NOT generalize to "graduate-oracle is the best Solana memecoin signal" or any broader claim. Other competitors (Phantom, Birdeye, Pump.fun analytics) require their own pre-registered comparisons (Studies 02-04 scoped in the harness pre-reg).
- The 48h window is a sample, not a permanent claim. Longitudinal stability is the scope of Study 04 (90+ day rolling HIGH/MED bucket precision).
- Precision (graduation rate among called positives) is one quality metric; recall (how many of the actual graduates were called) is not measured in this study. Pre-registered; both products' "did they call this winner" booleans are what's compared, not their full ROC curves.

---

## Reusable harness performance

The instrumentation built for this study (case_study_harness/) ran cleanly over the 48h window. Adapter pattern (sources/grad_oracle.py, sources/gmgn.py) handled both feeds without intervention. **Study 02 can configure the same harness** by swapping the GMGN adapter for the next comparison source — one TOML config file, one pre-reg writeup, one scheduled run.

That's the systematic-receipts cadence the user named in the strategic context: every study extends the moat without artisanal rebuilding.

---

## Receipts trail

| Commit | Action |
|---|---|
| `[PREREG_COMMIT_HASH]` Case Study 01 pre-registration + 3 branch templates | Frozen methodology, criteria, branches, harness scope |
| `[HARNESS_COMMIT_HASH]` Phase 2 — reusable harness scaffold | Built after pre-reg landed; idle until Finding 8 verdict |
| `[COLLECTION_START_HASH]` Collection daemon start | After 2026-05-09T16:45Z |
| **(this commit) Case Study 01 results — Branch A: thesis supported** | Frozen acceptance criteria met; B2B integration thesis empirically grounded |

---

## Cross-channel artifacts (ship after this commit)

**X thread variant** (also pre-drafted at `docs/research/case_study_01_x_thread_branch_a_template.md` — TODO at template-completion time):

```
case study 01 — calibrated bucket vs component composition.

graduate-oracle's lane-60s MED+HIGH bucket has [LIFT_PP]pp higher
graduation precision than GMGN's --filter-preset strict on the same
mint set (n=[OVERLAP_N], 48h window).

methodology pre-registered before data collection. acceptance criteria
frozen: ≥10pp lift on n≥30 + lift consistency on both-products-positive
subset. branches A/B/C all pre-drafted; the result fired branch A.

receipts: github.com/Dspro-fart/graduate-oracle/blob/main/docs/research/case_study_01_gmgn_results.md
prereg commit: [PREREG_COMMIT_HASH]
results commit:  [RESULTS_COMMIT_HASH]
```

**TG channel pinned variant:**

```
📊 Case Study 01 — graduate-oracle outperforms GMGN strict-preset by [LIFT_PP]pp on lane-60s graduation prediction.

Pre-registered methodology, frozen acceptance criteria, branch A executed cleanly. Sample size n=[OVERLAP_N] over 48h window (2026-05-09 → 2026-05-11).

Full writeup: github.com/Dspro-fart/graduate-oracle/blob/main/docs/research/case_study_01_gmgn_results.md
Methodology pre-reg: github.com/Dspro-fart/graduate-oracle/blob/main/docs/research/case_study_01_gmgn_comparison_prereg.md
```

---

## Discipline note (frozen template language)

This template was committed alongside the pre-registration BEFORE any data collection ran. The fact that this writeup exists at all — pre-drafted in skeleton form for an outcome that hadn't yet been observed — is the publish-then-post pattern operating at the case-study level. A reader can verify:

- Pre-reg commit timestamp predates collection start
- Branch templates committed in the same commit as the pre-reg
- Final writeup (this file) commits with numbers filled in only AFTER data resolves the criteria
- Branch executed matches the frozen acceptance criteria (no post-hoc selection of branch)

**Same shape as Finding 7h's PASS/FAIL operational pre-drafts and Finding 8's pre-drafted TG follow-up templates.** The discipline pattern's most durable form: not just pre-registering criteria, but pre-drafting the full publication for every pre-registered branch.
