# Lane 14 — Bundled regression sub-investigation

**Run date:** 2026-05-05 late evening (after Lane 13 calibration stability)
**Pre-registration:** [BACKLOG.md → "Lane 14 — bundled regression sub-investigation"](../../BACKLOG.md)
**Discipline lessons applied:** [feedback_pre_registration_branches.md](../../../.claude/projects/-Users-danielsproul/memory/feedback_pre_registration_branches.md) — magnitude thresholds, transition zone, divergence handling.

---

## Headline (decisions applied fresh, post-writeup)

**Two branches fire simultaneously under the pre-registered 5-branch rule:**

- **Branch 1 (sample noise):** Bootstrap 95% CI on the -5.92pp bundled regression is **[-23.60pp, +10.87pp]** — CI overlaps zero. Per the pre-registered rule, this branch fires.
- **Branch 2 (single-feature dominance):** Ablating `sol_spent_first_2s` recovers **+3.91pp** on bundled (above the 3pp threshold). Per the rule, this branch ALSO fires.

**Per Lane 13 divergence-handling discipline:** flag publicly, pick action covering both, update the rule before the next analysis.

**Hybrid action:** ship single-track (both branches agree on this baseline). Don't ship asymmetric feature handling speculatively (Branch 2's nominal action) given small-sample evidence — but add post-ship monitoring on bundled AUC + `sol_spent_first_2s` SHAP attribution, with explicit re-investigation trigger at n≥150 bundled.

## Numbers

### Test-set baselines (n=616, bundled=65, non-bundled=551)

| Comparison | Bundled AUC | Non-bundled AUC |
|---|---:|---:|
| Lane 9 GBM (full features) | 0.5180 | 0.7413 |
| Run B GBM (pre-grad features only) | 0.5772 | 0.5997 |
| k-NN reference | 0.5011 | 0.5999 |
| **Lane 9 vs Run B** | **-5.92pp** ← regression | **+14.17pp** |
| Lane 9 vs k-NN | +1.69pp | +14.15pp |

The -5.92pp bundled regression vs Run B is the originally-flagged signal. Lane 9 still narrowly beats k-NN on bundled (+1.69pp) and crushes both baselines on non-bundled.

### Bootstrap (1,000 resamples on bundled n=65, Lane 9 vs Run B)

- Observed delta: **-5.92pp**
- Bootstrap mean: -6.17pp
- **Bootstrap 95% CI: [-23.60pp, +10.87pp]**
- CI overlaps zero: **YES**

The CI is wide enough that the -5.92pp point estimate is statistically indistinguishable from zero at this n. Branch 1 (sample noise) fires.

### Feature ablation (each Lane 6 feature removed, GBM retrained, AUC delta on bundled)

Sorted by bundled improvement when removed:

| Feature removed | Bundled Δ | Non-bundled Δ |
|---|---:|---:|
| **sol_spent_first_2s** | **+3.91pp** | -1.20pp |
| vsol_acceleration | +1.69pp | +0.50pp |
| max_mult_at_age | +1.48pp | -3.66pp |
| bundle_pct | +1.37pp | -0.58pp |
| sol_spent_first_5s | +0.21pp | -0.26pp |
| top3_buyer_pct | -0.42pp | -0.79pp |
| sell_ratio | -0.32pp | +0.05pp |
| vsol_velocity_30s | -0.53pp | -0.53pp |
| n_smart_in | -0.95pp | +0.06pp |
| buys_per_buyer | -1.06pp | -0.57pp |
| dust_buy_rate | -1.37pp | -0.67pp |
| vsol_velocity_60s | -1.90pp | -1.32pp |
| repeat_buyer_rate | -2.22pp | +0.16pp |

- Features whose removal recovers ≥3pp on bundled: **1** (`sol_spent_first_2s`)
- Features whose removal recovers ≥2pp on bundled: 1
- Max single-feature recovery: +3.91pp

The distribution is NOT distributed — Branch 3 (multiple small contributors) does not fire. The signal concentrates on `sol_spent_first_2s`.

## Decision rule application (5 branches, applied fresh)

Per the pre-registered rule:

1. **Sample noise** — CI overlaps zero (CI includes ≥-1pp). **TRIPPED** (CI is [-23.60, +10.87]).
2. **Single-feature dominance** — one feature ablation recovers ≥3pp. **TRIPPED** (`sol_spent_first_2s` = +3.91pp).
3. **Distributed regression** — no single feature recovers ≥2pp; multiple small contributors. **Not tripped** (one feature recovers ≥3pp; only one other crosses 1.5pp).
4. **Transition zone** — CI overlaps zero AND ablations show 0-2pp distributed effect. **Not tripped** (one feature exceeds 3pp threshold, not 0-2pp distributed).
5. **Other / unclear** — n/a.

**Two branches fire (1 and 2). This is exactly the divergence pattern from Lane 13.**

## Divergence handling (per Lane 13's discipline rule)

Three obligations:

1. **Flag the divergence publicly** — done above and in the headline. Strict rule produces two simultaneous branch fires; we are not picking one privately.
2. **Pick an action covering both verdicts** — see "Hybrid action" below.
3. **Update the pre-registration rule before next analysis** — see "Rule update" section.

### Hybrid action (covers both Branch 1 and Branch 2)

| Branch | Nominal action | Hybrid component |
|---|---|---|
| Branch 1 (noise) | Ship single-track confidently | Ship single-track ✓ |
| Branch 2 (single-feature) | Asymmetric feature handling for bundled | Don't implement asymmetric handling speculatively at n=65 — but instrument and re-investigate at n≥150 |

**Concrete hybrid plan:**
1. **Ship single-track retrain** as planned. Both branches agree on this baseline; no architectural fork needed.
2. **Add bundled-population monitoring** post-ship:
   - Log bundled AUC weekly alongside non-bundled AUC
   - Track per-prediction SHAP/importance contribution of `sol_spent_first_2s` on bundled vs non-bundled mints
   - Surface in retrain-validation dashboard
3. **Re-investigation trigger:** if bundled regression persists at n≥150 in production (≥2-3× current sample), re-run Lane 14 with the larger sample. At n=150, the bootstrap CI would narrow ~2.4×, distinguishing noise from signal.
4. **Don't implement asymmetric feature handling now.** Two reasons:
   - Single-feature evidence at n=65 is borderline (3.91pp barely above 3pp threshold; bootstrap CI on the recovery itself wasn't computed but is wide given sample)
   - The non-bundled cost of removing `sol_spent_first_2s` is -1.20pp — non-trivial against the 87% population
   - Better to validate the signal at higher n before splitting feature engineering

**Why this isn't p-hacking:** the hybrid action is "do A AND defer B with a public trigger condition," not "do B because B is what I believed all along." Both nominal actions are documented; the asymmetric handling is on the table with a frozen re-investigation rule, not abandoned silently.

### Rule update (apply to next pre-registration)

**Gap identified:** Branch 1 and Branch 2 can both fire simultaneously. The 5-branch rule treated them as disjoint — but a small-sample CI overlapping zero is fully compatible with a real single-feature signal that the sample size can't statistically pin down. The branches are evidence-types, not mutually exclusive verdicts.

**Update for next bundled / sub-population regression analysis:**
- Compute bootstrap CI on the regression itself AND bootstrap CI on each ablation recovery
- Define explicit precedence when multiple branches trip:
  - If Branch 1 trips AND Branch 2 trips: **hybrid (ship + instrument + re-investigation trigger at higher n)**, not "pick one"
  - If Branch 2 trips AND Branch 3 trips: distributed-with-leader → ship single-track, log all suspicious features, re-investigate at higher n
  - If Branch 1 trips alone: ship confidently, no monitoring
  - If Branch 2 or 3 trips alone (CI doesn't overlap zero): regression is real, action depends on which branch
- Add minimum bundled n threshold for "decisive" verdicts (e.g., n≥150). Below that, default to hybrid + monitor.

This update applies the Lane 13 rule about **transition zones** to this kind of analysis: the gap between "noise" and "real signal with one feature" is itself a zone, and the rule should handle it explicitly rather than forcing a binary choice.

## What this means for tomorrow's retrain implementation

**Ship single-track GBM with full Lane 6 + Lane 9 features.** The bundled regression is not severe enough to fork architecture, and the single-feature signal is not robust enough at n=65 to justify asymmetric feature handling.

**Add to retrain validation checklist:**
- Stratified AUC by bundle_detected, logged in retrain output
- Per-feature SHAP attribution by population (bundled vs non-bundled)
- Bundled AUC threshold for ship-replace: must beat current model on BOTH populations OR not regress on bundled by ≥3pp (the Lane 14 single-feature threshold)

**Re-investigation trigger:** scheduled Lane 14 rerun once bundled n≥150 in production. Add to BACKLOG as a future scheduled investigation, not blocked work.

## Caveats

- **Bundled n=65 in held-out test set is small.** All bundled-specific findings here have wide error bars. Re-investigation at n≥150 is the path to decisive verdicts.
- **`sol_spent_first_2s` may be the leader of a small group, not a unique signal.** Three other features (`vsol_acceleration`, `max_mult_at_age`, `bundle_pct`) recover 1.4-1.7pp on bundled. If we redefined "single-feature dominance" as ≥4pp, only `sol_spent_first_2s` would trigger; if redefined as ≥1pp, four features would trigger and Branch 3 (distributed) would fire instead. The 2-3pp threshold band is a judgment call frozen pre-run.
- **The `bundle_pct` feature itself shows up as a +1.37pp bundled-improver when removed.** This is plausible — the feature could be over-fit to non-bundled patterns and noise on bundled. Worth tracking in monitoring.
- **Bootstrap CI was computed on the regression delta, not on each ablation.** A more rigorous version would bootstrap each ablation separately to separate true single-feature signal from sample-shuffle artifacts. Future enhancement.

## Numerical artifacts

- `/tmp/lane2/lane14_bundled.py` — analysis script
- `/tmp/lane2/lane14_results.json` — bootstrap + ablation results

## Related

- [Lane 9 — full retrain with curve-replay features](lane9_full_retrain.md) — the source of the original -5.9pp regression flag
- [Lane 13 — calibration stability analysis](lane13_calibration_stability.md) — the case that produced the divergence-handling rule applied here
- [feedback_pre_registration_branches.md](../../../.claude/projects/-Users-danielsproul/memory/feedback_pre_registration_branches.md) — the discipline rules applied
