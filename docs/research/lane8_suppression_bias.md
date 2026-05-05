# Lane 8 — Suppression matrix bias test

**Run date:** 2026-05-05 evening
**Pre-registration:** [BACKLOG.md → "Lane 8 — suppression-matrix bias"](../../BACKLOG.md)
**Decision criteria (user-specified evening):**
- >50% of non-bundled ≥0.7 candidates filtered by max_mult/current_mult ratio → matrix biased, needs population-aware redesign
- <20% → matrix is fine, diagnosis complete
- 20-50% → ambiguous

---

## Headline (decision applied fresh, post-writeup)

**Non-bundled post-peak (max_mult/cur ≥ 2.0) filter rate: 11.8%** — well below the 20% "matrix is fine" threshold.

**Decision per pre-registered rule: matrix is FINE. Diagnosis complete.**

The 3-layer selection-bias hypothesis becomes a 2-layer one:
- ✅ Layer 1 (collection leak, ~40%) — confirmed in selection-bias investigation
- ✅ Layer 2 (feature-vector bias, +14.16pp closeable) — confirmed in Lane 9
- ❌ Layer 3 (suppression matrix bias) — **REJECTED**. Matrix is doing its job.

This is good news for the retrain path: fewer things to fix.

## Numbers

### ≥0.7 candidates (predictions where the model would have fired)

| Population | n | R1 post-peak filter | R2 entry-quality (>2.0) | Pass both → ACT |
|---|---:|---:|---:|---:|
| Full | 1,182 | 11.2% | 88.2% | 7.4% |
| Bundled | 63 | **0.0%** | **100.0%** | 0.0% |
| Non-bundled | 1,119 | **11.8%** | 87.5% | 7.9% |

### Bundled vs non-bundled filter-rate comparison

- **Post-peak (R1):** bundled=0% vs non-bundled=11.8%. Bundled mints rarely "post-peak" because they stay near their peak through bonding (the bundled-pump dynamic). Non-bundled occasionally post-peak before grad_prob crosses threshold. **Population-aware tendency exists**, but the magnitude (12pp) is below the threshold for "biased."
- **Entry-quality (R2):** bundled=100% vs non-bundled=87.5%. Both populations are heavily routed to WATCH because by the time the model crosses 0.7, the mint is past 2× launch. This is the lateness problem, not the matrix problem.

## The deeper finding (not pre-registered, but the more important read)

**Of the 88 non-bundled ≥0.7 candidates that pass BOTH filters (would fire as ACT), 95.5% sustained 30m post-bond.**
**Of the 1,031 non-bundled ≥0.7 candidates that get filtered, 64.0% sustained.**

The matrix isn't filtering randomly — **it's separating high-quality fires (95.5% sustain) from medium-quality ones (64% sustain).** That's exactly what a suppression matrix should do. The narrow ACT path is identifying the highest-quality entries; the WATCH path catches the rest with appropriate framing.

So the matrix is doing more than "not biased" — it's actively discriminating outcome quality even when the model can't.

## What the entry-quality filter is really telling us

87.5% of non-bundled ≥0.7 candidates have `current_mult > 2.0`. These are mints that have already pumped past 2× launch by the time the model is confident.

This isn't a matrix bias — it's the **lateness problem we've been chasing.** The model doesn't get confident until the price has confirmed graduation. The matrix correctly routes these to WATCH (the user gets an alert, just with "catching late" framing).

The fix isn't loosening the matrix. The fix is **earlier confidence in the model**, which is exactly what Lane 9 validated. With Lane 9's curve-replay features + retrained model, more non-bundled mints should reach grad_prob ≥0.7 BEFORE they pump past 2× launch. That's a Layer 2 fix, not a Layer 3 fix.

## Sub-finding: 100% bundled entry-quality trip

Every bundled ≥0.7 candidate has `cur_mult > 2.0`. Bundled pumps explode the price before the model's confidence crosses threshold — bundled pump dynamics literally guarantee the entry-quality filter trips. This is consistent with Lane 1's finding that bundled mints are the worse-sustaining 13% slice — by the time you can call them, you can't enter cleanly.

This is its own data point about the bundled-pump product framing: even when the model fires correctly on bundled, the trader can't enter cleanly. The narrow bundled product isn't just narrow in coverage — it's narrow in tradability per fire.

## What this means for tomorrow's retrain scoping

Update [BACKLOG.md → "Retrain scoping draft"](../../BACKLOG.md):

1. **Drop the matrix-redesign workstream from the retrain plan.** Layer 3 isn't broken. The retrain doesn't need to ship matrix changes alongside model changes.
2. **Single-track retrain.** Two-implementation-track planning ("retrain + matrix redesign in parallel") is now single-track ("retrain alone").
3. **The narrow ACT path is high-quality.** 88 ACT-eligible non-bundled fires, 95.5% sustain. That's a real product, even pre-retrain. Worth highlighting in the post-retrain framing: "of the model's highest-confidence + cleanest-entry calls, 95% sustain."
4. **The lateness problem is the bottleneck.** 87.5% of non-bundled ≥0.7 fires already pumped past 2×. The retrain should be evaluated not just on AUC but on **how much earlier the new model crosses 0.7**. Pre-register that secondary metric in the retrain spec.

## Caveats

- The "would fire as ACT" estimate uses `predictions.entry_mult` and `lane9_features.max_mult_at_age`, both at the row's age_bucket (30 or 60). Live fire happens at a snapshot tick within that bucket window. The stored values are FIRST-tick values; cur_mult and max_mult at the actual fire moment may differ slightly (mostly higher). This makes the analysis CONSERVATIVE — actual cur_mult is more likely to be > 2.0 at fire time, so the 87.5% entry-quality trip rate is a floor.
- Sample is post-Lane-1 join: 1,182 ≥0.7 candidates. Bundled subset n=63 is small; bundled filter-rate confidence intervals are wide. The 100%/0% bundled rates are likely directionally correct but exact numbers shouldn't be over-interpreted.
- Suppression matrix has more rules than R1/R2 (bundle.detected ≥30 hard suppression, rug_heuristic.severity high, relisted-old-mint). This analysis only covers the two rules pre-registered as the bias hypothesis. Other rules may behave differently per population, but they're not what the user's hypothesis was about.

## Numerical summary saved to `/tmp/lane2/summary_lane8.json`
