# Wallet-index overlap with public KOL lists — empirical audit

> **INTERNAL-ONLY restricted-visibility note.** This writeup contains the
> empirical fingerprint of graduate-oracle's wallet reputation index against
> third-party public KOL/smart-money lists. The numeric outcome is publishable
> in positioning materials once internally reviewed; the methodology + raw
> verdict-shape is documented here for the receipts trail. **No wallet
> addresses appear in this writeup or in any committed file.** Per
> `project_wallet_index_is_the_moat.md`: specific wallet addresses are
> proprietary index data and stay redacted.

**Audit date:** 2026-05-12. **Verdict:** **STRUCTURALLY_UNIQUE** under the pre-registered acceptance criteria.

**Pre-reg:** methodology + frozen acceptance criteria established in the user's 2026-05-11 implementer ask + reaffirmed 2026-05-12; this commit fills in the numbers. Per the publish-then-post discipline, the verdict shape and its acceptance bands were frozen before the audit ran.

---

## Strategic context

`project_wallet_index_is_the_moat.md` (memory, 2026-05-10) hypothesized: the 200k+ wallet reputation index built by `wallet_intel.py` consists of **empirically-detected high-performance trader wallets, NOT typical KOL/influencer wallets that everyone tracks**. The moat hypothesis: graduate-oracle's wallet index is structurally distinct from publicly-curated KOL lists; competitors don't track these wallets because they don't know who they are.

This audit empirically verifies that hypothesis.

**Upstream evidence:** Audit 09 (commit `34ce847`) already validated that the wallet index has 7.37× graduation-rate lift at smart_money_in ≥ 7 stratum — the empirically-detected wallets predict outcomes. This audit asks the complementary question: are those wallets ALSO publicly known? If yes → the lift is replicable from public lists alone; not a moat. If no → the index IS structurally unique.

---

## Methodology (frozen pre-audit)

### Sampling

- Top 20 wallets by `smart_score` from `wallet_intel.INDEX.leaderboard(kind='smart', min_total=20, limit=20)` — internal canonical leaderboard
- Stratified by tier:
  - **Top tier:** ranks 1–5 (highest smart_score)
  - **Mid tier:** ranks 6–15
  - **Lower tier:** ranks 16–20

### Public-source verification

Per-wallet lookup via `gmgn-cli portfolio stats` against GMGN's wallet catalog. GMGN aggregates the canonical Solana-meme-trader public-tracking surfaces:

- **GMGN smart_degen leaderboard** — canonical Solana smart-money public list
- **Axiom / Padre / Photon / Birdeye "smart money" widget** memberships — surfaced as tags
- **Twitter / X identity bindings** — surfaced as `twitter_bind` + `twitter_username`
- **Community follow / remark counts** — surfaced as `follow_count` + `remark_count` (informal-tracking signal)
- **Verified KOL status** — surfaced as `is_blue_verified` or explicit KOL tags

Classification per wallet:

- **PUBLIC** — appears on at least one curated KOL list OR has Twitter identity bound OR has a public nickname
- **SEMI-PUBLIC** — has informal community attention (≥100 follows or ≥50 remarks on GMGN) OR has a non-KOL tag, but NOT on any curated public list
- **PRIVATE** — entirely unidentified externally (no tags, no Twitter, no nickname, low community attention)

### Frozen acceptance criteria

- **STRUCTURALLY_UNIQUE:** < 30% of the 20 wallets classified as PUBLIC → moat is empirically distinct
- **PARTIALLY_UNIQUE:** 30–60% PUBLIC → moat is partially commoditized; refine positioning to honest level
- **LARGELY_COMMODITIZED:** > 60% PUBLIC → moat hypothesis weak; rethink defensibility

---

## Results

### Headline

| Classification | Count of 20 | % |
|---|---:|---:|
| **PUBLIC** | **0** | **0.0%** |
| **SEMI-PUBLIC** | 13 | 65.0% |
| **PRIVATE** | 7 | 35.0% |
| LOOKUP_FAILED | 0 | 0.0% |

**Verdict: STRUCTURALLY_UNIQUE.** 0% public-list overlap is well below the 30% threshold.

### By tier

| Tier | n | % PUBLIC | % SEMI-PUBLIC | % PRIVATE |
|---|---:|---:|---:|---:|
| **Top (ranks 1–5)** | 5 | **0.0%** | 80.0% | 20.0% |
| **Mid (ranks 6–15)** | 10 | **0.0%** | 50.0% | 50.0% |
| **Lower (ranks 16–20)** | 5 | **0.0%** | 80.0% | 20.0% |
| **All 20** | 20 | **0.0%** | 65.0% | 35.0% |

**No tier has any wallet on a public curated KOL list.** This holds across the entire top-20 distribution, not just the head.

### Twitter / KOL-identity signals (per-wallet aggregates only)

- Wallets with `twitter_bind = true`: **0 of 20**
- Wallets with public nickname: **0 of 20**
- Wallets verified blue check: **0 of 20**
- Wallets carrying a curated-list tag (smart_degen / axiom_kol / padre_kol / photon / birdeye / etc.): **0 of 20**

### Community-attention distribution (the SEMI-PUBLIC signal)

The SEMI-PUBLIC bucket (13 of 20) captures wallets with informal GMGN-community attention. GMGN users have explicitly tracked them (`follow_count`) and remarked on their trades (`remark_count`), but no curator has elevated them to a public list.

| Range | Description |
|---|---|
| GMGN follow_count | spans 1 → 154 (rank-1 wallet had 154 follows) |
| GMGN remark_count | spans 1 → 121 |

**Interpretation:** sophisticated GMGN users have noticed some of these wallets and informally tracked them, but the wallets remain off the public-list infrastructure that's the standard input to competitor smart-money widgets. The fingerprint matches the user's hypothesis: "these are wallets some people have noticed; nobody has formally cataloged them."

---

## Substantive interpretation

### Three audits, one consistent story

| Audit | Question | Verdict |
|---|---|---|
| Audit 09 (`34ce847`) | Does smart_money_in predict outcomes? | **YES — 7.37× lift on graduation at sm≥7** |
| Audit 12-B Phase 1b (`e2aaf51`) | Does freshness factor predict outcomes at lane-60s? | **YES — 1.40× 2x_rate lift, clean CIs** |
| **This audit** | Are the top wallets in our index publicly tracked? | **NO — 0% public overlap** |

**The three audits stack into a coherent empirical foundation:**
1. The wallets predict outcomes (Audit 09)
2. The signal works at the lane-60s commit window (Audit 12-B Phase 1b)
3. Competitors cannot replicate it from public lists alone (this audit)

Audit 09 is the "moat works" finding. Audit 12-B is the "moat fires at the right moment" finding. This audit is the "moat is exclusive" finding.

### What "0% public" means commercially

A competitor running the same product hypothesis (calibrated graduation prediction on pump.fun mints) would need to either:

1. **Build their own observer + corpus + reputation index from scratch** — months of empirical observation against curve data with continuous re-ranking. The 200k+ wallet index is months of compounding evidence; the audit-grade structure compounds with time.
2. **License the wallet index from us** — the B2B integration path.
3. **Approximate from public KOL lists** — but those lists overlap with our top 20 at 0%. Whatever they'd build from public sources would predict a structurally different set of outcomes.

The 0% overlap means **option 3 doesn't produce the same signal**. The empirically-detected wallets are not on the lists competitors would use as their starting point.

### What "65% semi-public" means commercially

The 13 of 20 wallets in the SEMI-PUBLIC bucket have SOME informal GMGN-community attention (1–154 follows; 1–121 remarks). This is a softer signal than full KOL-list membership but matters for:

- **Defensive monitoring:** if competitors' community-driven tracking surfaces start aggregating these wallets, our exclusivity narrows. Worth monitoring follow_count distribution quarterly.
- **Positioning honesty:** the moat is "wallets some users have noticed but no curator has elevated" — not "wallets nobody has ever heard of." This is a sharper, more defensible framing than the strict version.

### What "35% fully PRIVATE" means

The 7 of 20 wallets with zero follows / zero remarks / zero tags are completely off public-tracking infrastructure. These are the strongest individual moat exemplars. If any single category of wallet defines the proprietary value, it's this.

---

## Acceptance verdict — pre-registered branch action

Per the pre-reg's STRUCTURALLY_UNIQUE branch action:

> "→ update outreach materials with sharper positioning (deferred; B2B-prep work, not urgent ship). Memory rule `project_wallet_index_is_the_moat.md` empirically validated."

The verdict empirically validates the moat-positioning memory rule that has been load-bearing since 2026-05-10. The wallet-index-as-moat hypothesis is now backed by three independent audits. Specific outreach updates are queued for B2B-prep work but not in the immediate ship list per the user's prior direction.

**No memory rule amendment is needed.** The hypothesis was correct; the empirical verification confirms.

---

## What ships next

Per the pre-reg's pre-decision-ready action list:

1. **Position-update materials (deferred):** when B2B outreach starts, the verified-moat narrative ("Audit 09 + Audit 12-B Phase 1b + this audit") becomes the receipts foundation for the 4-asset acquisition narrative.

2. **Quarterly re-check:** rerun this audit at the end of each quarter. If pct_public drifts from 0% → 10% → 20%, that's signal that the moat is narrowing because competitors are catching up to our wallet picks. At >30% drift, methodology amendment + strategic re-think triggers automatically per the pre-reg's branching.

3. **Audit-program design extension (filed for next session):** the "look at public follow/remark counts" axis surfaced a useful gradient (PUBLIC vs SEMI-PUBLIC vs PRIVATE). Future moat-positioning audits can use the same three-bucket classification across new entrant lists (Pump.fun analytics, Phantom intelligence, etc.).

---

## Public-discussion discipline (operationally binding)

- **No wallet addresses** anywhere — not in this writeup, not in commits, not in conversations, not in positioning materials, not in X threads.
- **Numeric outcome IS publishable:** "Across the top 20 wallets in our reputation index, 0 of 20 appear on any public KOL list (GMGN smart_degen, Axiom, Padre, Photon, Birdeye)." This sentence is shareable.
- **Per-wallet identification stays private** — the audit shows the AGGREGATE shape; no specific wallet is identified, tagged, or correlated to any public-known entity.
- **Methodology IS publishable:** the audit's design, frozen acceptance criteria, and verdict-shape can be referenced. The pre-reg discipline is part of the receipts moat.

---

## Receipts trail

| Commit | Action |
|---|---|
| `34ce847` Audit 09 results | First empirical validation of wallet index (7.37× lift) |
| `06480be` Wallet redaction Option A deploy | Smart_money_examples + clustered_wallets redacted publicly |
| `d8af9ec` Wallet redaction Option 5 deploy | Remaining 5 wallet-shaped surfaces redacted |
| `e2aaf51` Audit 12-B Phase 1b — freshness retroactive | Second empirical pillar (1.40× 2x_rate lift) |
| **(this commit) Wallet-overlap audit — STRUCTURALLY_UNIQUE verdict** | Third empirical pillar; moat-as-distinct hypothesis validated |
| (future) B2B outreach materials | Will cite this audit + Audit 09 + Audit 12-B as the empirical foundation |
| (future, quarterly) Re-run | Detect moat-narrowing over time; pre-registered drift threshold at 30% |

---

## Cross-references

- [`audit_09_smart_money_lift_results.md`](audit_09_smart_money_lift_results.md) — upstream empirical validation
- [`audit_12b_phase1b_freshness_results.md`](audit_12b_phase1b_freshness_results.md) — sister empirical pillar
- [`wallet_redaction_2026_05_11.md`](wallet_redaction_2026_05_11.md) + [`wallet_redaction_option_5_2026_05_11.md`](wallet_redaction_option_5_2026_05_11.md) — protection deploys
- Memory: `project_wallet_index_is_the_moat.md` — strategic hypothesis empirically validated
- Memory: `feedback_methodology_calls_user_owned.md` — the verdict-shape was user-frozen pre-audit
- Memory: `feedback_pre_registration_branches.md` — discipline this audit follows
