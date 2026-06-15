// $GRADUATE · client app

const ROW_LIMIT = 60;
const REFRESH_MS = 2000;
const WATCH_KEY = 'graduate.watchlist.v1';
const SEEN_KEY = 'graduate.seen-high-prob.v1';
const HISTORY_LEN = 30;          // 30 samples × 2s = ~60s of history per mint
const FLASH_THRESHOLD = 0.10;    // flash if odds shifted >= 10pp in window

// in-memory: mint -> [{ t: ms, p: 0..1 }, ...]
const probHistory = new Map();

// ─── boot sequence ──────────────────────────────────────────────────────
const BOOT_LINES = [
  '> initializing $GRADUATE oracle',
  '> connecting to pump.fun firehose',
  '> loading historical curves... ok',
  '> mayhem filter engaged',
  '> graduation k-NN ready',
  '> wallet intelligence: standby',
  '> ready.',
];
function runBoot() {
  const el = document.getElementById('boot-text');
  if (!el) return;
  let i = 0;
  function next() {
    if (i >= BOOT_LINES.length) {
      setTimeout(() => {
        const b = document.getElementById('boot');
        if (b) b.classList.add('done');
        setTimeout(() => b && b.remove(), 500);
      }, 220);
      return;
    }
    el.textContent = BOOT_LINES.slice(0, i + 1).join('\n');
    i++;
    setTimeout(next, 110 + Math.random() * 90);
  }
  next();
}

// ─── formatters ─────────────────────────────────────────────────────────
function fmtAge(s) {
  if (s == null) return '—';
  s = Math.round(s);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s/60)}m${String(s%60).padStart(2,'0')}s`;
}
function fmtSol(v)   { return v == null ? '—' : v.toFixed(1); }
function fmtMC(mc) {
  if (!mc) return '—';
  if (mc.usd != null) {
    if (mc.usd >= 1000) return '$' + (mc.usd/1000).toFixed(1) + 'k';
    return '$' + mc.usd.toLocaleString();
  }
  if (mc.sol != null) return mc.sol.toFixed(0) + ' SOL';
  return '—';
}
function fmtMult(v)  { return v == null ? '—' : `${v.toFixed(2)}x`; }
function fmtPct(v)   { return v == null ? '—' : `${(v * 100).toFixed(0)}%`; }
function shortMint(m){ return m ? m.slice(0, 4) + '…' + m.slice(-4) : '—'; }
function fmtSnapAge(s) { return s == null ? '—' : `snapshot ${s}s ago`; }

function probClass(p) {
  if (p == null) return 'prob-na';
  if (p >= 0.5) return 'prob-high';
  if (p >= 0.2) return 'prob-mid';
  return 'prob-low';
}

function probHTML(p) {
  const cls = probClass(p);
  const txt = fmtPct(p);
  const w = p == null ? 0 : Math.max(2, p * 70);
  return `<span class="prob ${cls}">
    <span class="prob-num">${txt}</span>
    <span class="prob-bar"><span class="prob-bar-fill" style="width:${w}px"></span></span>
  </span>`;
}

// ─── Big predictions cell — THE product, dominant in the main row. ─────
// Three side-by-side predictions per row: GRAD (will it survive), X (will
// it pump from here), RUG (will it devil-candle). Each is a chip with its
// probability, lift-vs-base-rate, and (when present) calibration receipt.
// Replaces the old compact `oddsHTML` for the main board; oddsHTML is kept
// for the secondary tables (smart-money active, etc.) where space is tight.
function predictionsCellHTML(m) {
  const ageS = m.age_s ?? 0;
  const inLane = ageS <= 90;

  // Pretty-print helpers.
  const pct = v => v == null ? '—' : (v * 100).toFixed(0) + '%';
  const liftCls = lx => lx == null ? 'lift-na'
                      : lx >= 3 ? 'lift-strong' : lx >= 1.5 ? 'lift-mid' : 'lift-weak';

  // Tooltip helper — pushes lift, base rate, and receipt info into the
  // hover title instead of cluttering the chip.
  const tip = (parts) => parts.filter(Boolean).join(' · ');

  // ── GRAD chip ────────────────────────────────────────────────────────
  const grad = inLane ? m.grad_prob : null;
  const gLift = inLane ? m.grad_prob_lift_x : null;
  const gBR   = inLane ? m.grad_prob_base_rate : null;
  const gCal  = inLane ? (m.grad_prob_calibration || null) : null;
  let gradChip;
  if (grad == null) {
    const t = inLane ? 'no signal yet' : `out of prediction window (mint is ${ageS.toFixed(0)}s old, lane is ≤60s)`;
    gradChip = `<div class="pred-chip pred-chip-na" title="${t}"><div class="pred-label">GRAD</div><div class="pred-value pred-value-na">—</div></div>`;
  } else {
    const t = tip([
      'GRAD = probability the mint graduates (vSOL ≥115)',
      gBR != null ? `base rate ${(gBR*100).toFixed(1)}%` : null,
      gLift != null ? `${gLift.toFixed(2)}× lift vs base` : null,
      (gCal && gCal.historical_n >= 10)
        ? `historical: of ${gCal.historical_n} mints we said ≥${gCal.threshold_band}, ${(gCal.historical_actual_rate*100).toFixed(0)}% actually graduated`
        : null,
    ]);
    // Tier badge: ≥70% gets a "HIGH" tier — historically graduates 60% of
    // the time per 7d backtest. This is THE signal worth acting on. ≥50%
    // gets a "MID" tier (24-30% graduation, decent). Below 50% the model
    // is mostly noise (15-17% graduation, below predicted) — alerts are
    // suppressed there but the dashboard still shows them so traders can
    // see the long tail.
    const tierClass = grad >= 0.70 ? 'tier-high'
                    : grad >= 0.50 ? 'tier-mid'
                    : 'tier-low';
    gradChip = `<div class="pred-chip pred-chip-grad ${liftCls(gLift)} ${tierClass}" title="${t}">
      <div class="pred-label">GRAD</div>
      <div class="pred-value">${pct(grad)}</div>
    </div>`;
  }

  // ── X-FACTOR chip ────────────────────────────────────────────────────
  const xf = inLane ? m.x_factor : null;
  let xChip;
  if (!xf || xf.lift_x == null) {
    const t = inLane ? 'no upside signal yet' : 'out of prediction window';
    xChip = `<div class="pred-chip pred-chip-na" title="${t}"><div class="pred-label">X</div><div class="pred-value pred-value-na">—</div></div>`;
  } else {
    const tierLabel = `${xf.best_tier}×`;
    const t = tip([
      `X = strongest upside lift across 2×/5×/10× tiers (best: ${tierLabel})`,
      `${(xf.prob*100).toFixed(0)}% chance of ≥${tierLabel} from current price`,
      `base rate ${(xf.base_rate*100).toFixed(1)}%`,
      `${xf.lift_x.toFixed(2)}× lift vs base`,
      'Labels observer-derived (full on-chain backfill in progress)',
    ]);
    xChip = `<div class="pred-chip pred-chip-x ${liftCls(xf.lift_x)}" title="${t}">
      <div class="pred-label">X &nbsp;@${tierLabel}</div>
      <div class="pred-value">${pct(xf.prob)}</div>
    </div>`;
  }

  // ── RUG chip ─────────────────────────────────────────────────────────
  const rp = inLane ? (m.rug_prob || null) : null;
  let rugChip;
  if (!rp) {
    const t = inLane ? 'rug predictor not yet sampled at this checkpoint' : 'out of prediction window';
    rugChip = `<div class="pred-chip pred-chip-na" title="${t}"><div class="pred-label">RUG</div><div class="pred-value pred-value-na">—</div></div>`;
  } else if (rp.status === 'warming') {
    const t = `Rug predictor warming up: ${rp.n_total_resolved} resolved (need 30) · ${rp.n_rugged_resolved} rugged (need 15)`;
    rugChip = `<div class="pred-chip pred-chip-rug pred-chip-warming" title="${t}"><div class="pred-label">RUG</div><div class="pred-value pred-value-warming">warming</div></div>`;
  } else if (rp.status === 'too_young' || rp.prob == null) {
    const t = `Mint hasn't been sampled at the 60s rug-prob checkpoint yet (current age ${ageS.toFixed(0)}s)`;
    rugChip = `<div class="pred-chip pred-chip-na" title="${t}"><div class="pred-label">RUG</div><div class="pred-value pred-value-na">${ageS < 60 ? 'too young' : '—'}</div></div>`;
  } else {
    // Higher rug_prob = WORSE, so visual cls reverses lift logic.
    const rugCls = rp.lift_x == null ? 'lift-na'
                 : rp.lift_x >= 3 ? 'rug-elevated'
                 : rp.lift_x >= 1.5 ? 'rug-mid' : 'rug-low';
    const t = tip([
      'RUG = probability of a single ≥40% drop in first 5 min (devil candle)',
      `base rate ${(rp.base_rate*100).toFixed(2)}%`,
      rp.lift_x != null ? `${rp.lift_x.toFixed(2)}× lift vs base` : null,
    ]);
    rugChip = `<div class="pred-chip pred-chip-rug ${rugCls}" title="${t}">
      <div class="pred-label">RUG</div>
      <div class="pred-value">${pct(rp.prob)}</div>
    </div>`;
  }

  return `<div class="predictions-cell">${gradChip}${xChip}${rugChip}</div>`;
}


// Compact stacked odds — grad / 2× / 5× from current entry price.
// We default to *_from_now semantics because that's what a trader looking at
// this row is actually asking: "if I buy now, does it Nx from here?". The
// from-launch numbers are still on the API for analysts/ranking but are
// shown in the expanded view, not the row.
function oddsHTML(m) {
  const fmt = (v) => v == null ? '<span class="muted">—</span>' : (v * 100).toFixed(0) + '%';
  const cls = (v) => v == null ? 'muted'
                   : v >= 0.7 ? 'up' : v >= 0.4 ? '' : 'muted';
  // LANE DISCIPLINE: predictions are valid at age ≤ 60s only. Past a small
  // grace window (90s) we don't claim a live prediction — show "—". The
  // mint stays in the table for context, but the prob column is silent.
  const ageS = m.age_s ?? 0;
  const inLane = ageS <= 90;
  const grad = inLane ? m.grad_prob : null;
  const r2  = inLane ? m.runner_prob_2x_from_now : null;
  const r5  = inLane ? m.runner_prob_5x_from_now : null;

  // Inline calibration receipt — historical accuracy at THIS exact (age,
  // confidence) cell. The receipt is the moat: every prediction comes with
  // its own audit trail. None for out-of-lane or untrained cells.
  const cal = m.grad_prob_calibration;
  let receipt = '';
  if (inLane && cal && cal.historical_n >= 10) {
    const histPct = (cal.historical_actual_rate * 100).toFixed(0);
    receipt = `<div class="odds-receipt" title="Historical accuracy: of ${cal.historical_n} mints we predicted at the ${cal.threshold_band} confidence band at age ${cal.age_bucket}s, ${histPct}% actually graduated. Calibrated against on-chain truth.">
      ✓ ${histPct}% acc · n=${cal.historical_n}
    </div>`;
  }
  // Per-tier runner receipts (2x/5x). Smaller than the grad receipt and
  // marked with an asterisk because labels are observer-derived (biased)
  // until the Tier 3 max_mult backfill ships.
  const r2cal = m.runner_prob_2x_from_now_calibration;
  const r5cal = m.runner_prob_5x_from_now_calibration;
  const renderRunnerReceipt = (c) => {
    if (!inLane || !c || c.historical_n < 10) return '';
    const pct = (c.historical_actual_rate * 100).toFixed(0);
    return `<span class="odds-receipt-mini" title="Of ${c.historical_n} mints we predicted at ${c.threshold_band} for ${c.tier} from current price at age ${c.age_bucket}s, ${pct}% actually hit. *Observer-derived labels (biased, on-chain backfill pending).">${pct}%·n${c.historical_n}*</span>`;
  };

  const baseTip = inLane
    ? 'Calibrated graduation prediction at age ≤60s. Hover the receipt for historical accuracy at this confidence level.'
    : `Out of prediction window — we predict at age 30s/60s only. This mint is ${ageS.toFixed(0)}s old. Lane is bounded on purpose: graduateoracle.fun/scope`;

  // Lift badge: how much above the bucket-wide base graduation rate this
  // prediction is. Without lift, "30%" reads weak; "30× / 6× base" reads
  // like a signal. Hidden out-of-lane and when base_rate is missing.
  const liftBadge = (() => {
    if (!inLane) return '';
    const lift = m.grad_prob_lift_x;
    const br   = m.grad_prob_base_rate;
    if (lift == null || br == null) return '';
    const klass = lift >= 3 ? 'lift-strong' : lift >= 1.5 ? 'lift-mid' : 'lift-weak';
    return `<span class="lift-badge ${klass}" title="Base graduation rate at this age: ${(br*100).toFixed(1)}%. This prediction is ${lift.toFixed(2)}× that base — ${lift >= 1.5 ? 'meaningfully above' : lift >= 1 ? 'on par with' : 'below'} random.">${lift.toFixed(1)}× base</span>`;
  })();

  // Per-runner-tier lift badges. Same framing — "30% at 3% base = 10× lift"
  // — applied to the upside tiers. Lets a trader see at a glance which tier
  // is the strongest signal and why.
  const tierLiftBadge = (lift, br) => {
    if (lift == null || br == null) return '';
    const klass = lift >= 3 ? 'lift-strong' : lift >= 1.5 ? 'lift-mid' : 'lift-weak';
    return `<span class="lift-badge ${klass}" title="Base rate at this age: ${(br*100).toFixed(1)}% of corpus mints hit this tier from here. This prediction = ${lift.toFixed(2)}× that base.">${lift.toFixed(1)}×</span>`;
  };
  const r2lift = inLane ? tierLiftBadge(m.runner_prob_2x_from_now_lift_x, m.runner_prob_2x_from_now_base_rate) : '';
  const r5lift = inLane ? tierLiftBadge(m.runner_prob_5x_from_now_lift_x, m.runner_prob_5x_from_now_base_rate) : '';

  // x-factor headline: the highest-lift upside tier. Reframes "this might
  // not graduate" mints that are still trader-actionable. A coin with grad=5%
  // but x_factor 10× lift on the 5× tier is real money — neighbors with these
  // features tended to pump from this exact age + price ratio.
  const xf = inLane ? m.x_factor : null;
  let xfLine = '';
  if (xf && xf.lift_x != null) {
    const xklass = xf.lift_x >= 3 ? 'lift-strong' : xf.lift_x >= 1.5 ? 'lift-mid' : 'lift-weak';
    const tierLabel = `${xf.best_tier}×`;
    xfLine = `<div class="odds-row odds-xfactor" title="X-FACTOR — strongest from-now upside tier. Of corpus neighbors with similar features at this exact age and price ratio, ${(xf.prob*100).toFixed(0)}% peaked at ≥${tierLabel} from here. The base rate at this bucket is ${(xf.base_rate*100).toFixed(1)}% — this prediction is ${xf.lift_x.toFixed(2)}× that base. Catches trade-able mints even when grad_prob is mid (rug-pumps, fast runners that don't graduate).">
      <span class="odds-k odds-k-xfactor">x</span><span class="odds-v ${cls(xf.prob)}">${(xf.prob*100).toFixed(0)}% @ ${tierLabel}</span>
      <span class="lift-badge ${xklass}">${xf.lift_x.toFixed(1)}× base</span>
    </div>`;
  }

  // Bucket badge — post-2026-05-06 cutover headline. Bimodal-aware bucket
  // assignment per docs/research/bucket_cutoffs_bimodal_finding.md.
  // Tooltip copy reads accurately whether the daemon is in bimodal_cliff
  // mode or has fallen back to standard_percentile mode post-retrain.
  const bucket = inLane ? m.grad_prob_bucket : null;
  const bucketBadge = (() => {
    if (!bucket || bucket === 'LOW') return '';
    const klass = bucket === 'HIGH' ? 'bucket-high' : 'bucket-med';
    const emoji = bucket === 'HIGH' ? '🟢' : '🟡';
    const calProb = m.grad_prob_gbm_calibrated;
    const calStr = (calProb != null) ? `${(calProb*100).toFixed(1)}%` : '—';
    return `<span class="bucket-badge ${klass}" title="Calibrated probability ${calStr} (live base rate ~5%). HIGH = model unusually confident beyond what training showed (rare event, ~5/week). MED = strongest signal among the at-ceiling cluster, ranked by raw GBM. Bucket label is locked into the V3 merkle leaf at log time. /api/status shows current cutoffs + bucket_logic_mode.">${emoji} ${bucket}</span>`;
  })();

  return `<div class="odds-stack" title="${baseTip}">
    <div class="odds-row"><span class="odds-k">grad</span><span class="odds-v ${cls(grad)}">${fmt(grad)}</span> ${bucketBadge} ${liftBadge}</div>
    ${xfLine}
    <div class="odds-row"><span class="odds-k">2×</span><span class="odds-v ${cls(r2)}">${fmt(r2)}</span> ${r2lift} ${renderRunnerReceipt(r2cal)}</div>
    <div class="odds-row"><span class="odds-k">5×</span><span class="odds-v ${cls(r5)}">${fmt(r5)}</span> ${r5lift} ${renderRunnerReceipt(r5cal)}</div>
    ${receipt}
  </div>`;
}

// Inline badges for creator quality + manufactured-pump flag + graduation
// lifecycle state. Empty when none apply, so the contract cell stays clean
// for the long tail of one-off launchers.
function badgesHTML(m) {
  const out = [];
  // Live smart-money signal — leaderboard wallets currently in this mint's
  // top buyers. This is real-time alpha: it tells the trader "scoring wallets
  // are positioned in this RIGHT NOW," not just historical creator track record.
  if ((m.smart_money_in || 0) >= 1) {
    const ex = (m.smart_money_examples || []).map(w => w.length > 14 ? w.slice(0,4)+'…'+w.slice(-4) : w).join(', ');
    out.push(`<span class="sig-badge sig-smart-in" title="${m.smart_money_in} smart-money leaderboard wallet(s) currently in top buyers: ${ex}">▲ smart money in (${m.smart_money_in})</span>`);
  }

  // Cluster pile-in — multiple smart wallets currently in the mint who
  // have a history of moving together. Stronger signal than just count.
  const cl = m.cluster || {};
  if ((cl.n_clustered_pairs || 0) >= 1) {
    out.push(`<span class="sig-badge sig-cluster" title="${cl.n_clustered_pairs} clustered pair(s) in top buyers · max co-buys: ${cl.max_pair_count}">◇ cluster pile-in (${cl.n_clustered_pairs})</span>`);
  }

  // Whale-heavy — top buyers carry real money. n_whale_wallets is from
  // the wallet_balance daemon (≥1 SOL). Threshold of 5 keeps the badge
  // selective; not every mint with one whale gets flagged.
  const wb = m.wallet_balance || {};
  if ((wb.n_whale_wallets || 0) >= 5) {
    const max = wb.max_buyer_sol;
    out.push(`<span class="sig-badge sig-whale" title="${wb.n_whale_wallets} top buyers hold ≥1 SOL · avg ${wb.avg_buyer_sol} SOL · max ${max} SOL">$ whale-heavy (${wb.n_whale_wallets})</span>`);
  }

  // Accelerating — vSOL growth is speeding up. >5 SOL of acceleration
  // means the recent 30s window outpaced the prior 30s by 5+ SOL,
  // which is a strong climax-phase signal.
  if ((m.vsol_acceleration || 0) >= 5) {
    out.push(`<span class="sig-badge sig-accel" title="vSOL acceleration: +${m.vsol_acceleration.toFixed(1)} SOL · last 30s outpaced prior 30s">⚡ accelerating</span>`);
  }
  // The graduation_state field still drives the stale-hide skip on the
  // server side (so near-grad mints don't flicker); we just don't render
  // a badge for it — vsol is already on the row, no need for redundant UI.

  const c = m.creator_history;
  if (c) {
    if (c.runner_creator) {
      out.push(`<span class="sig-badge sig-runner" title="creator has launched ${c.n_launches} mints — historical 5× rate ${(c.rate_5x*100).toFixed(0)}%">▲ runner dev</span>`);
    } else if (c.good_creator) {
      out.push(`<span class="sig-badge sig-good" title="creator has launched ${c.n_launches} mints — historical graduation rate ${(c.grad_rate*100).toFixed(0)}%">✓ verified dev</span>`);
    } else if (c.n_launches >= 10 && c.rate_5x < 0.05 && c.grad_rate < 0.10) {
      // Spam factory: dozens of launches, almost none ever ran or graduated.
      out.push(`<span class="sig-badge sig-spam" title="${c.n_launches} prior launches · ${(c.grad_rate*100).toFixed(1)}% graduated · ${(c.rate_5x*100).toFixed(1)}% ran 5×">⚠ ${c.n_launches}× spam dev</span>`);
    }
  }
  if (m.manufactured_pump) {
    const sol = m.sol_spent_first_2s != null ? `${m.sol_spent_first_2s.toFixed(1)} SOL` : '';
    out.push(`<span class="sig-badge sig-runner" title="${sol} bought in first 2 seconds — concentrated early entry. Forward-validation shows these mints rug at 50% vs 81% baseline (lift 0.61) — they're actually safer than the average mint.">🔥 hot launch</span>`);
  }

  // Bundle detection — Axiom-style "Bundlers: X%". A bundle is ≥4 distinct
  // wallets buying within a 500ms window in the first 5 seconds — Solana
  // slots are 400ms so this is a Jito bundle by definition. The percentage
  // is the share of currently-circulating tokens those wallets still hold,
  // which moves over time. Pink (risk) badge; the bigger the %, the worse.
  const bun = m.bundle;
  if (bun && bun.detected) {
    const pct = bun.pct.toFixed(0);
    const cls = bun.pct >= 30 ? 'sig-spam'
              : bun.pct >= 10 ? 'sig-pump'
              : 'sig-good';
    out.push(`<span class="sig-badge ${cls}" title="${bun.size} wallets bundled at t=${(bun.at_t_s||0).toFixed(2)}s · they currently hold ${pct}% of circulating supply">⚠ bundlers ${pct}%</span>`);
  }

  // DexScreener paid-info badge — small green chip. Soft signal that the
  // creator paid $99-$300 to enable Enhanced Token Info on dexscreener.com
  // (logo, websites, socials). Filters against one-shot rug profile.
  const dx = m.dex_paid;
  if (dx && dx.is_paid) {
    const sites = dx.n_websites ? `${dx.n_websites} site` + (dx.n_websites>1?'s':'') : '';
    const socs  = dx.n_socials  ? `${dx.n_socials} social` + (dx.n_socials>1?'s':'')  : '';
    const tip   = [sites, socs].filter(Boolean).join(' · ') + ' on DexScreener (creator paid for Enhanced Token Info)';
    out.push(`<span class="sig-badge sig-dex" title="${tip}">DEX</span>`);
  }

  // Fee-delegation badge — pump.fun's on-chain creator-fee splitter.
  // Surfaces only when the mint actually has a delegation set.
  // 100% delegated = creator gave up their entire fee cut (often to a
  // launchpad partner like RapidLaunch). Split = N delegates share fees.
  const fd = m.fee_delegation;
  if (fd && fd.total_bps > 0) {
    const pct = (fd.total_bps / 100).toFixed(0);
    const dele = fd.primary_delegate
      ? fd.primary_delegate.slice(0,4) + '…' + fd.primary_delegate.slice(-4)
      : '?';
    if (fd.fully_delegated) {
      out.push(`<span class="sig-badge sig-good" title="100% of creator fees delegated to ${fd.primary_delegate} — creator has no direct fee incentive (often a launchpad partner contract)">🤝 fees 100% delegated → ${dele}</span>`);
    } else if (fd.n_delegates >= 2) {
      out.push(`<span class="sig-badge sig-good" title="${fd.n_delegates} delegates share ${pct}% of creator fees · primary: ${fd.primary_delegate}">🤝 fees split ${fd.n_delegates}× (${pct}% delegated)</span>`);
    } else {
      out.push(`<span class="sig-badge sig-good" title="${pct}% of creator fees delegated to ${fd.primary_delegate}">🤝 fees ${pct}% → ${dele}</span>`);
    }
  }

  // Post-graduation survival — only meaningful for mints close to or
  // already past the graduation threshold. Below that, the features the
  // predictor uses (smart_money_in, whales, velocity) haven't crystallized
  // yet. Show on near-grad and graduated mints; hide otherwise.
  const pgs = m.post_grad_survival;
  const nearGrad = (m.current_vsol_sol || 0) >= 80 || (m.grad_prob || 0) >= 0.5;
  if (pgs && nearGrad) {
    if (pgs.status === "sunset_pending_architecture_review"
        || pgs.status === "sunset_pending_validation_rerun"
        || pgs.status === "metric_recalibration_in_progress"
        || pgs.status === "sunset_lane_60s_structural_limit") {
      // Finding 7i permanent sunset (2026-05-08): no sustain badge ever again.
    } else if (pgs.status === "warming" || pgs.status === "warming_clean_corpus_accumulating") {
      out.push(`<span class="sig-badge sig-warming" title="post-grad survival predictor warming up · ${pgs.n_total_resolved}/30 resolved samples">🎓 sustain warming</span>`);
    } else if (pgs.prob != null) {
      const pct = Math.round(pgs.prob * 100);
      const cls = pgs.prob >= 0.65 ? 'sig-runner' : pgs.prob >= 0.4 ? 'sig-good' : 'sig-warming';
      out.push(`<span class="sig-badge ${cls}" title="if this mint graduates, k-NN predicts ${pct}% chance of holding ≥80% of grad price for 30 min · ${pgs.n_neighbors} nearest neighbors among ${pgs.n_total_resolved} resolved historical graduates">🎓 sustain ${pct}%</span>`);
    }
  }
  return out.length ? `<span class="sig-row">${out.join(' ')}</span>` : '';
}

// Creator-history column — full pubkey + counts. Distinct from badges
// (which are inline next to the contract) because this is the full track
// record. Returns muted dash when first_buyer is unknown / unseen in index.
function creatorCellHTML(m) {
  const c = m.creator_history;
  // c.creator can be null post-wallet-redaction (d8af9ec) even when c
  // itself is populated; fall through to first_buyer / muted dash path.
  if (!c || !c.creator) {
    if (m.first_buyer) {
      const short = m.first_buyer.length > 14
                  ? m.first_buyer.slice(0, 4) + '…' + m.first_buyer.slice(-4)
                  : m.first_buyer;
      return `<span class="muted" title="first buyer ${m.first_buyer} — no prior history in our index">${short}<br><span class="muted" style="font-size:0.7rem">new dev</span></span>`;
    }
    if (c && c.n_launches) {
      return `<span class="muted" title="creator wallet redacted; ${c.n_launches} prior launches · ${(c.grad_rate*100).toFixed(0)}% grad">${c.n_launches}× launches</span>`;
    }
    return '<span class="muted">—</span>';
  }
  const short = c.creator.length > 14
              ? c.creator.slice(0, 4) + '…' + c.creator.slice(-4)
              : c.creator;
  const cls = c.runner_creator ? 'up' : c.good_creator ? '' : 'muted';
  return `<span class="creator-cell ${cls}" title="${c.creator}">
    <span class="creator-addr">${short}</span>
    <span class="creator-stats">${c.n_launches}× · ${(c.grad_rate*100).toFixed(0)}% grad · ${(c.rate_5x*100).toFixed(0)}% 5×</span>
  </span>`;
}

// Track per-mint probability history. Returns the appended history array.
function recordProb(mint, prob) {
  if (prob == null) return null;
  const arr = probHistory.get(mint) || [];
  arr.push({ t: Date.now(), p: prob });
  while (arr.length > HISTORY_LEN) arr.shift();
  probHistory.set(mint, arr);
  return arr;
}

// Compute delta + stability over the recorded window. Returns
// {delta, dir, history, stddev, stability} or null.
function trendInfo(mint, currentProb) {
  const arr = probHistory.get(mint);
  if (!arr || arr.length < 2 || currentProb == null) return null;
  const oldest = arr[0].p;
  const delta = currentProb - oldest;
  const dir = delta > 0.02 ? 'up' : delta < -0.02 ? 'down' : 'flat';
  // Rolling stddev — how jittery has this score been?
  const ps = arr.map(x => x.p);
  const mean = ps.reduce((a, b) => a + b, 0) / ps.length;
  const variance = ps.reduce((a, b) => a + (b - mean) * (b - mean), 0) / ps.length;
  const stddev = Math.sqrt(variance);
  // Stability tier: 0 = volatile, 1 = mid, 2 = stable
  const stability = stddev < 0.03 ? 2 : stddev < 0.07 ? 1 : 0;
  return { delta, dir, history: arr, stddev, stability };
}

function sparklineSVG(history) {
  if (!history || history.length < 2) return '';
  const w = 56, h = 16;
  const ps = history.map(x => x.p);
  const lo = Math.min(...ps, 0);
  const hi = Math.max(...ps, 1);
  const range = Math.max(hi - lo, 0.01);
  const pts = history.map((x, i) => {
    const px = (i / (history.length - 1)) * (w - 2) + 1;
    const py = h - 1 - ((x.p - lo) / range) * (h - 2);
    return `${px.toFixed(1)},${py.toFixed(1)}`;
  }).join(' ');
  return `<svg class="sparkline" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <polyline points="${pts}" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>
  </svg>`;
}

function trendHTML(mint, prob) {
  const info = trendInfo(mint, prob);
  if (!info) return '<span class="muted" title="building history…">—</span>';
  const sign = info.delta >= 0 ? '+' : '';
  const arrow = info.dir === 'up' ? '▲' : info.dir === 'down' ? '▼' : '·';
  const cls = `trend-${info.dir}`;
  const pct = `${sign}${(info.delta * 100).toFixed(1)}pp`;
  const spark = sparklineSVG(info.history);
  // Stability dots: ●●● = solid signal, ●●○ = mid, ●○○ = jittery
  const dots = info.stability === 2 ? '●●●'
              : info.stability === 1 ? '●●○'
              : '●○○';
  const stabCls = info.stability === 2 ? 'stab-high'
                 : info.stability === 1 ? 'stab-mid'
                 : 'stab-low';
  const titleStab = `score variance over last ${info.history.length * 5}s: ±${(info.stddev * 100).toFixed(1)}pp`;
  return `<span class="trend ${cls}">${arrow}&nbsp;${pct}</span>${spark}<span class="stab ${stabCls}" title="${titleStab}">${dots}</span>`;
}

// ─── action buttons (links to trading sites) ────────────────────────────
function actionsHTML(mint) {
  return `<span class="actions">
    <a class="btn btn-pump"   href="https://pump.fun/coin/${mint}"        target="_blank" rel="noreferrer">PUMP</a>
    <a class="btn btn-axiom"  href="https://axiom.trade/t/${mint}"        target="_blank" rel="noreferrer">AXM</a>
    <a class="btn btn-photon" href="https://photon-sol.tinyastro.io/en/lp/${mint}" target="_blank" rel="noreferrer">PHO</a>
    <a class="btn"            href="https://dexscreener.com/solana/${mint}" target="_blank" rel="noreferrer">DEX</a>
    <a class="btn"            href="https://solscan.io/token/${mint}"     target="_blank" rel="noreferrer">SOL</a>
  </span>`;
}

// ─── toast / clipboard ──────────────────────────────────────────────────
function showToast(msg) {
  const t = document.getElementById('toast');
  if (!t) return;
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove('show'), 1400);
}
async function copyToClipboard(text, el) {
  try { await navigator.clipboard.writeText(text); }
  catch {
    const ta = document.createElement('textarea');
    ta.value = text; ta.style.position='fixed'; ta.style.opacity='0';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); } catch {}
    document.body.removeChild(ta);
  }
  if (el) {
    el.classList.add('copied');
    setTimeout(() => el.classList.remove('copied'), 900);
  }
  showToast('contract copied');
}

// ─── watchlist (localStorage) ───────────────────────────────────────────
function loadWatch() {
  try { return new Set(JSON.parse(localStorage.getItem(WATCH_KEY) || '[]')); }
  catch { return new Set(); }
}
function saveWatch(set) {
  localStorage.setItem(WATCH_KEY, JSON.stringify([...set]));
}
let watchSet = loadWatch();
function toggleWatch(mint) {
  if (watchSet.has(mint)) watchSet.delete(mint);
  else watchSet.add(mint);
  saveWatch(watchSet);
  refreshWatchCount();
  renderLastData();   // re-render to update star states
  renderWatchlist();
}
function refreshWatchCount() {
  const el = document.getElementById('watch-count');
  if (el) el.textContent = watchSet.size;
}

// ─── tabs ───────────────────────────────────────────────────────────────
function activateTab(name) {
  document.querySelectorAll('.tab').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
  document.querySelectorAll('.panel').forEach(p => p.style.display = (p.dataset.panel === name) ? '' : 'none');
  if (name === 'smart')   refreshSmart();
  if (name === 'snipers') refreshSnipers();
  if (name === 'watch')   renderWatchlist();
  if (name === 'paper')   refreshPaper();
}

// ─── high-prob alert pulse — mints crossing 70% trigger an alert flash ─
let seenHighProb = new Set();
try { seenHighProb = new Set(JSON.parse(sessionStorage.getItem(SEEN_KEY) || '[]')); } catch {}
function persistSeen() { sessionStorage.setItem(SEEN_KEY, JSON.stringify([...seenHighProb])); }

// ─── live grad-prob board ───────────────────────────────────────────────
let lastData = null;
let expandedMint = null;  // which mint's "why this score" popup is open

// ─── detail popup · floats above the live data ─────────────────────────
// The popup is positioned independently of the table so the underlying rows
// can keep flowing/sorting/refreshing without "chasing" the popup around the
// screen. Live data still flows: each refresh tick re-fills the popup's body
// with fresh content from the latest snapshot.
function _refreshDetailPopup(m) {
  const body = document.getElementById('detail-popup-body');
  const mintLabel = document.getElementById('detail-popup-mint');
  if (!body || !mintLabel) return;
  body.innerHTML = expandedRowHTML(m);
  mintLabel.textContent = shortMint(m.mint);
}

function _openDetailPopup(mint) {
  const m = (lastData?.mints || []).find(x => x.mint === mint);
  if (!m) return;
  expandedMint = mint;
  // Update the chevron state on every visible row — the open one shows ▾,
  // any previously-open one resets to ▸. Other than that the table is
  // untouched, so it keeps live-updating in the background.
  document.querySelectorAll('tr.is-expanded').forEach(r => {
    r.classList.remove('is-expanded');
    const c = r.querySelector('.expand-chev');
    if (c) { c.textContent = '▸'; c.setAttribute('aria-label', 'show details'); }
  });
  const row = document.querySelector(`tr[data-mint="${mint}"]`);
  if (row) {
    row.classList.add('is-expanded');
    const c = row.querySelector('.expand-chev');
    if (c) { c.textContent = '▾'; c.setAttribute('aria-label', 'hide details'); }
  }
  _refreshDetailPopup(m);
  document.getElementById('detail-popup').hidden = false;
  document.getElementById('detail-popup-backdrop').hidden = false;
}

function _closeDetailPopup() {
  expandedMint = null;
  document.getElementById('detail-popup').hidden = true;
  document.getElementById('detail-popup-backdrop').hidden = true;
  document.querySelectorAll('tr.is-expanded').forEach(r => {
    r.classList.remove('is-expanded');
    const c = r.querySelector('.expand-chev');
    if (c) { c.textContent = '▸'; c.setAttribute('aria-label', 'show details'); }
  });
}

// Wire up popup dismiss interactions — close button, backdrop click, Escape key.
(function _bindDetailPopup() {
  document.getElementById('detail-popup-backdrop')?.addEventListener('click', _closeDetailPopup);
  document.querySelector('.detail-popup-close')?.addEventListener('click', _closeDetailPopup);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && expandedMint) _closeDetailPopup();
  });
})();

// Show-all toggle removed 2026-05-04. The dashboard now shows the full
// firehose by default — bands are color-tiered (low/mid/high) so the
// user can scan and the ≥70% mints get featured at the top of the page
// in the HITS hero. No filter, no toggle, just the data.
try { localStorage.removeItem("showAllConfidence"); } catch (e) {}
function renderLastData() { if (lastData) render(lastData); }

// ─── sort + filter preferences ──────────────────────────────────────────
// Client-side. State persists in localStorage. Lane-60s prediction
// discipline holds — these sort modes affect DISPLAY only, not the model's
// per-mint commitments. Out-of-lane mints with smart-money entries get
// surfaced under "hot" / "smart" / "movers" sorts even though they have
// grad_prob = 0 (the bucket label remains '?' / out-of-lane).
const SORT_KEY = 'sort_mode_v1';
const FILTER_DEAD_KEY = 'filter_hide_dead_v1';
const FILTER_LANE_KEY = 'filter_in_lane_v1';
const FILTER_MC_FLOOR_KEY = 'filter_mc_floor_v1';

// Pro defaults (2026-05-16): a first-time visitor should land on the
// strong view — hot-launch composite sort + $5k MC floor + dead curves
// hidden — not a wall of post-backfill 2-6% grad_probs (honest but
// unimpressive cold). Per project_hot_launch_composite_signal.md this
// combination was empirically "actually incredible". RETURNING users
// keep whatever they explicitly set (localStorage wins); only the
// never-set / first-visit case gets the new default.
function _getSortMode() {
  try { return localStorage.getItem(SORT_KEY) || 'hot'; } catch (e) { return 'hot'; }
}
function _getHideDead() {
  try {
    const v = localStorage.getItem(FILTER_DEAD_KEY);
    return v === null ? true : v === '1';   // default ON
  } catch (e) { return true; }
}
function _getInLaneOnly() {
  try { return localStorage.getItem(FILTER_LANE_KEY) === '1'; } catch (e) { return false; }
}
function _getMcFloor() {
  try {
    const raw = localStorage.getItem(FILTER_MC_FLOOR_KEY);
    const v = parseInt(raw === null ? '5000' : raw, 10);   // default $5k
    return isNaN(v) ? 0 : v;
  } catch (e) { return 5000; }
}

function _applySort(mints, mode) {
  const arr = [...mints];
  switch (mode) {
    case 'time':
      // Newest first. age_s ascending.
      return arr.sort((a, b) => (a.age_s || 0) - (b.age_s || 0));
    case 'hot': {
      // Composite signal for catching post-60s rallies the model can't
      // surface. smart_money_in × max_mult × freshness. Freshness decays
      // smoothly so a 5-min-old 9-smart 3x mint outranks a stale one.
      const score = (m) => {
        const sm = (m.smart_money_in || 0);
        const mult = (m.max_mult || 1);
        const age = (m.age_s || 1);
        const freshness = 1 / (1 + age / 600); // half-life ~10 min
        return sm * mult * freshness;
      };
      return arr.sort((a, b) => score(b) - score(a));
    }
    case 'smart':
      return arr.sort((a, b) => (b.smart_money_in || 0) - (a.smart_money_in || 0));
    case 'progress':
      return arr.sort((a, b) => (b.current_vsol_sol || 0) - (a.current_vsol_sol || 0));
    case 'movers':
      return arr.sort((a, b) => (b.current_mult || 0) - (a.current_mult || 0));
    case 'prob':
    default:
      // Probability descending. Calibrated when present, raw grad_prob fallback.
      return arr.sort((a, b) => {
        const ap = a.grad_prob_gbm_calibrated ?? a.grad_prob ?? 0;
        const bp = b.grad_prob_gbm_calibrated ?? b.grad_prob ?? 0;
        return bp - ap;
      });
  }
}

function _applyFilters(mints, hideDead, inLaneOnly, mcFloor) {
  let arr = mints;
  if (hideDead) {
    // Dead = no movement after the lane window. Keeps fresh quiet mints
    // visible; only hides the stale, stagnant ones at the top of prob-sort.
    arr = arr.filter(m => !((m.max_mult || 1) <= 1.0 && (m.age_s || 0) > 60));
  }
  if (inLaneOnly) {
    arr = arr.filter(m => m.grad_prob_bucket && m.grad_prob_bucket !== '?');
  }
  if (mcFloor > 0) {
    // Pairs well with Hot-launch sort: surfaces real activity vs dead curves.
    // Per project_hot_launch_composite_signal.md (2026-05-10) — composite +
    // MC floor was empirically reported as "actually incredible" signal.
    arr = arr.filter(m => {
      const mc = m.market_cap;
      const usd = mc && typeof mc === 'object' ? mc.usd : null;
      return usd != null && usd >= mcFloor;
    });
  }
  return arr;
}

(function _bindBoardControls() {
  const sortSel = document.getElementById('sort-select');
  const hideDead = document.getElementById('filter-hide-dead');
  const inLane = document.getElementById('filter-in-lane');
  const mcFloor = document.getElementById('filter-mc-floor');
  if (sortSel) {
    sortSel.value = _getSortMode();
    sortSel.addEventListener('change', () => {
      try { localStorage.setItem(SORT_KEY, sortSel.value); } catch (e) {}
      renderLastData();
    });
  }
  if (hideDead) {
    hideDead.checked = _getHideDead();
    hideDead.addEventListener('change', () => {
      try { localStorage.setItem(FILTER_DEAD_KEY, hideDead.checked ? '1' : '0'); } catch (e) {}
      renderLastData();
    });
  }
  if (inLane) {
    inLane.checked = _getInLaneOnly();
    inLane.addEventListener('change', () => {
      try { localStorage.setItem(FILTER_LANE_KEY, inLane.checked ? '1' : '0'); } catch (e) {}
      renderLastData();
    });
  }
  if (mcFloor) {
    mcFloor.value = String(_getMcFloor());
    mcFloor.addEventListener('change', () => {
      try { localStorage.setItem(FILTER_MC_FLOOR_KEY, mcFloor.value); } catch (e) {}
      renderLastData();
    });
  }
})();

function render(data) {
  lastData = data;

  const snapAge = data.snapshot_age_s;
  document.getElementById('snap-age').textContent = fmtSnapAge(snapAge);
  document.getElementById('n-tracked').textContent = data.n_tracked_total;
  document.getElementById('n-indexed').textContent = data.n_indexed_curves.toLocaleString();
  const _nFmt = data.n_indexed_curves.toLocaleString();
  ['footer-curves', 'hero-curves'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.textContent = _nFmt + '+';
  });
  const feat = document.getElementById('feat-curves');
  if (feat) feat.textContent = Math.floor(data.n_indexed_curves / 1000).toLocaleString() + 'k+';

  // Apply user-chosen sort + filter preferences before slicing to ROW_LIMIT.
  // Default (sort=prob, no filters) preserves the pre-feature behavior; any
  // other selection re-orders/filters the live feed client-side. Hero +
  // counter logic below operates on `mints`, so they reflect the filtered
  // set — intentional: if the user filters out non-in-lane mints, the
  // counter shows what they're actually viewing.
  const _allMints = (data.mints || []);
  const _sorted = _applySort(_allMints, _getSortMode());
  const _filtered = _applyFilters(_sorted, _getHideDead(), _getInLaneOnly(), _getMcFloor());
  const mints = _filtered.slice(0, ROW_LIMIT);
  // HITS — every in-lane mint in the HIGH bucket right now (post-cutover
  // bucket framing per 2026-05-06 calibrated-GBM cutover). Falls back to
  // legacy ≥0.70 filter for old-format /api/live responses where
  // grad_prob_bucket is missing. Sorted by calibrated probability desc,
  // legacy grad_prob desc as tiebreaker.
  const hasBucketField = mints.some(m => m.grad_prob_bucket != null);
  const hits = hasBucketField
    ? mints.filter(m => m.grad_prob_bucket === 'HIGH')
    : mints.filter(m => (m.grad_prob || 0) >= 0.70);
  hits.sort((a, b) => {
    const ac = a.grad_prob_gbm_calibrated ?? a.grad_prob ?? 0;
    const bc = b.grad_prob_gbm_calibrated ?? b.grad_prob ?? 0;
    return bc - ac;
  });
  renderHero(hits);
  // Counter — counts HIGH and MED buckets when available, legacy ≥0.70 /
  // 0.50-0.70 thresholds otherwise. Shows what the model is calling right
  // now without forcing the absolute-percentage framing the calibrated
  // cutover retired.
  const highBand = hasBucketField
    ? mints.filter(m => m.grad_prob_bucket === 'HIGH').length
    : mints.filter(m => (m.grad_prob || 0) >= 0.70).length;
  const midBand = hasBucketField
    ? mints.filter(m => m.grad_prob_bucket === 'MED').length
    : mints.filter(m => { const g = m.grad_prob || 0; return g >= 0.50 && g < 0.70; }).length;
  const hcEl = document.getElementById('high-confidence-counter');
  if (hcEl) {
    if (hasBucketField) {
      // Post-cutover copy. Bimodal-aware buckets per
      // docs/research/bucket_cutoffs_bimodal_finding.md.
      if (highBand > 0) {
        hcEl.innerHTML = `<span class="hc-badge hc-active" title="Mints currently in the HIGH bucket — model unusually confident beyond what training showed (rare event signal). Cutoffs rebuilt every 24h. /api/status → bucket_cutoffs.">🟢 <strong>${highBand}</strong> HIGH · ${midBand} MED</span>`;
      } else if (midBand > 0) {
        hcEl.innerHTML = `<span class="hc-badge hc-mid" title="No HIGH-bucket calls right now. ${midBand} in the MED bucket (strongest signal among the at-ceiling cluster).">🟡 ${midBand} MED · 0 HIGH</span>`;
      } else {
        hcEl.innerHTML = `<span class="hc-badge hc-quiet" title="No mints in MED or HIGH buckets. Model is calling everything below the bucket cutoffs of the current 7-day calibrated distribution.">⚪ no MED/HIGH calls right now</span>`;
      }
    } else {
      if (highBand > 0) {
        hcEl.innerHTML = `<span class="hc-badge hc-active" title="Mints currently scoring ≥70% graduation probability (legacy framing).">🟢 <strong>${highBand}</strong> at ≥70% · ${midBand} at 50-70%</span>`;
      } else if (midBand > 0) {
        hcEl.innerHTML = `<span class="hc-badge hc-mid" title="No mints in the high-confidence (≥70%) band right now.">🟡 ${midBand} at 50-70% · 0 at ≥70%</span>`;
      } else {
        hcEl.innerHTML = `<span class="hc-badge hc-quiet" title="No high-confidence calls right now.">⚪ no calls ≥50% right now</span>`;
      }
    }
  }

  const rowsEl = document.getElementById('rows');
  const empty = document.getElementById('empty');
  if (mints.length === 0) {
    rowsEl.innerHTML = '';
    empty.style.display = '';
    return;
  }
  empty.style.display = 'none';

  // Compute per-mint flash state BEFORE rendering, so the new HTML can carry the class
  const flashes = new Map();    // mint -> 'up' | 'down'
  mints.forEach(m => {
    if (m.grad_prob == null) return;
    const prevHistory = probHistory.get(m.mint);
    const prevProb = prevHistory && prevHistory.length ? prevHistory[prevHistory.length - 1].p : null;
    if (prevProb != null && Math.abs(m.grad_prob - prevProb) >= FLASH_THRESHOLD) {
      flashes.set(m.mint, m.grad_prob > prevProb ? 'up' : 'down');
    }
    recordProb(m.mint, m.grad_prob);
  });

  // Show the full firehose. No filter. Visual tiers (band-low/mid/high)
  // communicate noise vs real signal vs act-band, and the ≥70% HITS get
  // featured at the top of the page in the hero card. The dashboard's
  // job is to show the model thinking; the trader can scan visually.
  const visibleMints = mints;
  rowsEl.innerHTML = visibleMints.map(m => rowHTML(m, flashes.get(m.mint))).join('');

  // Live-update the detail popup if it's open and the mint is still in the
  // snapshot. The popup floats above the table — it survives row shuffling
  // and gets fresh data on every tick.
  if (expandedMint) {
    const m = mints.find(x => x.mint === expandedMint);
    if (m) {
      _refreshDetailPopup(m);
    } else {
      _closeDetailPopup();
    }
  }

  // Pulse alert for newly-observed high-prob mints (one-time per session)
  let newAlerts = 0;
  mints.forEach(m => {
    if (m.grad_prob != null && m.grad_prob >= 0.7 && !seenHighProb.has(m.mint)) {
      seenHighProb.add(m.mint);
      newAlerts++;
      const tr = document.querySelector(`tr[data-mint="${m.mint}"]`);
      if (tr) tr.classList.add('alert-pulse');
    }
  });
  if (newAlerts > 0) persistSeen();

  // Refresh watchlist tab if it's also visible (live data updated)
  if (document.querySelector('.tab.active')?.dataset.tab === 'watch') renderWatchlist();
  if (document.querySelector('.tab.active')?.dataset.tab === 'paper') refreshPaper();
}

function rowHTML(m, flashDir) {
  const p = m.grad_prob;
  const growth = m.vsol_growth_sol;
  const growthCls = growth == null ? '' : growth > 0 ? 'up' : growth < 0 ? 'down' : 'muted';
  const starred = watchSet.has(m.mint);
  const flashCls = flashDir ? `flash-${flashDir}` : '';
  const isOpen = expandedMint === m.mint;
  const badges = badgesHTML(m);
  const meta = m.metadata || {};
  const thumbHTML = meta.image_url
    ? `<img class="row-thumb" src="${meta.image_url}" alt="" loading="lazy" onerror="this.style.display='none'">`
    : `<span class="row-thumb row-thumb-blank" aria-hidden="true">●</span>`;
  const nameHTML = meta.name || meta.symbol
    ? `<span class="row-name">${meta.name || meta.symbol}${meta.symbol && meta.name ? ` <span class="row-symbol">$${meta.symbol}</span>` : ''}</span>`
    : '';
  return `<tr data-mint="${m.mint}" class="${flashCls} ${isOpen ? 'is-expanded' : ''}">
    <td><span class="star ${starred ? 'on' : ''}" data-toggle-watch="${m.mint}" title="${starred ? 'unwatch' : 'watch'}">★</span></td>
    <td>
      <div class="contract-stack">
        <div class="contract-meta">
          ${thumbHTML}
          ${nameHTML}
        </div>
        <div class="contract-line">
          <span class="mint-cell" data-mint="${m.mint}" title="click to copy ${m.mint}">
            <span class="mint-text">${shortMint(m.mint)}</span>
            <span class="copy-icon">⎘</span>
          </span>
          <button type="button" class="expand-chev" data-expand-mint="${m.mint}" aria-label="${isOpen ? 'hide details' : 'show details'}" title="${isOpen ? 'hide details' : 'click for full breakdown'}">
            <span class="expand-chev-text">${isOpen ? 'HIDE' : 'EXPAND'}</span>
            <span class="expand-chev-arrow">${isOpen ? '▾' : '▸'}</span>
          </button>
        </div>
        ${badges}
      </div>
    </td>
    <td class="predictions-td">${predictionsCellHTML(m)}</td>
    <td class="r muted">${fmtAge(m.age_s)}</td>
    <td class="r">
      <div class="stacked-cell">
        <span class="stacked-top">${fmtMC(m.market_cap)}</span>
        <span class="stacked-bot muted">${m.unique_buyers}&nbsp;buy</span>
      </div>
    </td>
    <td class="r">
      <div class="stacked-cell">
        <span class="stacked-top">${fmtSol(m.current_vsol_sol)}</span>
        <span class="stacked-bot ${growthCls}">${growth != null ? (growth >= 0 ? '+' : '') + fmtSol(growth) : '—'}</span>
      </div>
    </td>
    <td class="r">
      <div class="stacked-cell">
        <span class="stacked-top">${fmtMult(m.current_mult)}</span>
        <span class="stacked-bot muted">pk&nbsp;${fmtMult(m.max_mult)}</span>
      </div>
    </td>
    <td class="r trend-cell">${trendHTML(m.mint, p)}</td>
    <td class="r">${creatorCellHTML(m)}</td>
    <td>${actionsHTML(m.mint)}</td>
  </tr>`;
}

// ─── expand row · "why this score" diagnostic panel ────────────────────
function expandedRowHTML(m) {
  const pct = (v, d = 0) => v == null ? '—' : (v * 100).toFixed(d) + '%';
  const num = (v, d = 2) => v == null ? '—' : (+v).toFixed(d);

  // Token metadata header — image + name + symbol pulled to the top of
  // the popup. Renders only when at least name/symbol resolved (so we
  // don't show an empty bar on cold-start).
  const meta = m.metadata || {};
  let metaBlock = '';
  if (meta.name || meta.symbol || meta.image_url) {
    const img = meta.image_url
      ? `<img class="why-thumb" src="${meta.image_url}" alt="" loading="lazy" onerror="this.style.display='none'">`
      : '';
    const name = meta.name ? `<span class="why-name">${meta.name}</span>` : '';
    const sym  = meta.symbol ? `<span class="why-symbol">$${meta.symbol}</span>` : '';
    const desc = meta.description ? `<div class="why-description">${meta.description}</div>` : '';
    metaBlock = `<div class="why-meta">
        ${img}
        <div class="why-meta-text">
          <div class="why-meta-name">${name}${sym}</div>
          ${desc}
        </div>
      </div>`;
  }

  // Badges row at the top of the popup — same set the table row carries,
  // but pulled into the detail view so a user reading the popup sees the
  // headline signals without having to look back at the table behind it.
  const headerBadges = badgesHTML(m);
  const badgeBlock = headerBadges
    ? `<div class="why-badges">${headerBadges}</div>`
    : '';

  // k-NN context
  const knn = (m.grad_neighbors != null && m.grad_neighbors > 0)
    ? `<b>${m.grad_n_graduated}</b> of ${m.grad_neighbors} mints with similar features at age <b>${m.age_bucket}s</b> ended up graduating.`
    : 'No comparable historical match yet.';

  // Combined-probability breakdown — explains how the headline odds
  // blend curve-shape and at-launch predictors.
  const cp = m.combined_prob || {};
  let combinedBlock = '';
  if (cp.early_warming) {
    combinedBlock = `<div class="why-row"><span class="why-k">combined headline</span><span class="why-v">${cp.prob != null ? (cp.prob*100).toFixed(0)+'%' : '—'} <span class="muted">(curve only · early signal warming)</span></span></div>`;
  } else if (cp.early_prob != null) {
    combinedBlock = `<div class="why-row"><span class="why-k">combined headline</span><span class="why-v"><b>${(cp.prob*100).toFixed(0)}%</b></span></div>
      <div class="why-row"><span class="why-k">curve-shape signal</span><span class="why-v">${(cp.curve_prob*100).toFixed(0)}% <span class="muted">(weight ${(cp.weight_curve*100).toFixed(0)}%)</span></span></div>
      <div class="why-row"><span class="why-k">at-launch signal</span><span class="why-v">${(cp.early_prob*100).toFixed(0)}% <span class="muted">(weight ${(cp.weight_early*100).toFixed(0)}%)</span></span></div>
      <div class="why-body muted" style="margin-top:6px">Age-weighted blend — early signal dominates at young ages, curve dominates as the mint matures. Divergence between the two = alpha.</div>`;
  }

  // Activity health (with organic baseline annotations)
  const sellRatio = m.sell_ratio;
  const bpb       = m.buys_per_buyer;
  const sellLabel = sellRatio == null ? '—'
                  : sellRatio < 0.05 ? `<span class="bad">${pct(sellRatio)}</span> <span class="muted">(organic ~30-40%)</span>`
                  : sellRatio < 0.15 ? `<span class="warn">${pct(sellRatio)}</span> <span class="muted">(organic ~30-40%)</span>`
                  : `<span class="ok">${pct(sellRatio)}</span>`;
  const bpbLabel  = bpb == null ? '—'
                  : bpb >= 2.5 ? `<span class="bad">${num(bpb,2)}</span> <span class="muted">(organic ~1)</span>`
                  : bpb >= 2.0 ? `<span class="warn">${num(bpb,2)}</span> <span class="muted">(organic ~1)</span>`
                  : `<span class="ok">${num(bpb,2)}</span>`;

  // Buyer composition
  const top1 = pct(m.top_buyer_pct, 1);
  const top3 = pct(m.top3_buyer_pct, 1);
  const unk  = pct(m.unknown_buyer_pct, 0);
  const lowH = pct(m.low_history_pct, 0);
  const snip = pct(m.sniper_pct, 0);
  const smart = m.avg_top_buyer_smart != null ? (m.avg_top_buyer_smart >= 0 ? '+' : '') + num(m.avg_top_buyer_smart, 2) : '—';

  // Top buyers list — full addresses, click to copy
  const buyers = (m.top_buyers || []).slice(0, 6).map(w => {
    const short = w.length > 14 ? w.slice(0, 6) + '…' + w.slice(-4) : w;
    return `<span class="mint-cell" data-mint="${w}" title="copy ${w}"><span class="mint-text">${short}</span><span class="copy-icon">⎘</span></span>`;
  }).join(' · ') || '<span class="muted">—</span>';

  // Bot flags
  const flags = (m.bot_flags || []);
  const flagsHTML = flags.length
    ? flags.map(f => `<span class="flag-badge">${f.replace(/_/g, ' ')}</span>`).join(' ')
    : '<span class="muted">none</span>';

  // Velocity + acceleration — pace of vSOL growth over recent windows.
  // Positive accel = mint is speeding up (climax phase). Negative = cooling.
  const v30 = m.vsol_velocity_30s;
  const v60 = m.vsol_velocity_60s;
  const accel = m.vsol_acceleration;
  const velRow = v30 != null
    ? `<div class="why-row"><span class="why-k">last 30s</span><span class="why-v ${v30 > 5 ? 'up' : v30 > 1 ? '' : 'muted'}">+${num(v30, 1)} SOL</span></div>
       <div class="why-row"><span class="why-k">last 60s</span><span class="why-v">+${num(v60 ?? 0, 1)} SOL</span></div>
       <div class="why-row"><span class="why-k">acceleration</span><span class="why-v ${accel > 0 ? 'up' : accel < 0 ? 'down' : 'muted'}">${accel > 0 ? '+' : ''}${num(accel ?? 0, 2)} SOL <span class="muted">${accel > 0 ? '(speeding up)' : accel < 0 ? '(cooling off)' : '(steady)'}</span></span></div>`
    : '<div class="why-body muted">no recent activity</div>';

  // Cluster signal — multiple smart-money wallets currently in this mint
  // who have a history of moving together. Stronger than just count.
  const cl = m.cluster || {};
  const clusterRow = cl.n_clustered_pairs > 0
    ? `<div class="why-row"><span class="why-k">clustered pairs</span><span class="why-v up"><b>${cl.n_clustered_pairs}</b> · density ${(cl.cluster_density * 100).toFixed(0)}%</span></div>
       <div class="why-row"><span class="why-k">strongest pair</span><span class="why-v">${cl.max_pair_count} prior co-buys</span></div>
       <div class="why-row"><span class="why-k">in cluster</span><span class="why-v">${(cl.clustered_wallets || []).slice(0,4).map(w => `<span class="mint-cell" data-mint="${w}" title="copy ${w}"><span class="mint-text">${w.slice(0,4)}…${w.slice(-4)}</span><span class="copy-icon">⎘</span></span>`).join(' · ') || '—'}</span></div>`
    : `<div class="why-body muted">No clustered pile-in detected. Either no smart-money wallets in top buyers, or those that are present have no history of moving together.</div>`;

  // Full runner-tier breakdown — shows BOTH semantics side by side so the
  // user can see "is this a runner overall?" (from-launch) AND "if I buy
  // now, do I N×?" (from-now). The from-now numbers are what the headline
  // odds column on the row uses.
  const tierRow = (label, fromLaunch, fromNow) => {
    const clsL = fromLaunch == null ? 'muted' : fromLaunch >= 0.5 ? 'up' : fromLaunch >= 0.2 ? '' : 'muted';
    const clsN = fromNow == null ? 'muted' : fromNow >= 0.5 ? 'up' : fromNow >= 0.2 ? '' : 'muted';
    return `<div class="why-row"><span class="why-k">${label}</span><span class="why-v">
      <span class="${clsN}" title="from current entry price">${pct(fromNow, 0)}</span>
      <span class="muted" style="font-size:0.7rem"> from-now</span>
      <span class="muted" style="font-size:0.7rem; margin-left:10px">·</span>
      <span class="${clsL}" title="from launch price">${pct(fromLaunch, 0)}</span>
      <span class="muted" style="font-size:0.7rem"> from-launch</span>
    </span></div>`;
  };
  const runnerRows = [
    ['≥2×',  m.runner_prob_2x,  m.runner_prob_2x_from_now],
    ['≥3×',  m.runner_prob_3x,  m.runner_prob_3x_from_now],
    ['≥5×',  m.runner_prob_5x,  m.runner_prob_5x_from_now],
    ['≥10×', m.runner_prob_10x, m.runner_prob_10x_from_now],
    ['≥20×', m.runner_prob_20x, m.runner_prob_20x_from_now],
  ].map(([label, fL, fN]) => tierRow(label, fL, fN)).join('');
  const peakLine = m.expected_peak_mult != null
    ? `<div class="why-row"><span class="why-k">expected peak</span><span class="why-v">${num(m.expected_upside_from_now ?? 0, 2)}× <span class="muted">from now</span> · ${num(m.expected_peak_mult, 2)}× <span class="muted">from launch</span></span></div>`
    : '';

  // Creator history block. c.creator can be null post-wallet-redaction
  // (d8af9ec) even when c is populated; render wallet row only if creator
  // address is present, otherwise skip the wallet line.
  const c = m.creator_history;
  const creatorBody = c
    ? `${c.creator ? `<div class="why-row"><span class="why-k">wallet</span><span class="why-v"><span class="mint-cell" data-mint="${c.creator}" title="copy ${c.creator}"><span class="mint-text">${c.creator.slice(0,4)}…${c.creator.slice(-4)}</span><span class="copy-icon">⎘</span></span></span></div>` : `<div class="why-row"><span class="why-k">wallet</span><span class="why-v muted">redacted</span></div>`}
       <div class="why-row"><span class="why-k">prior launches</span><span class="why-v">${c.n_launches}</span></div>
       <div class="why-row"><span class="why-k">graduation rate</span><span class="why-v">${(c.grad_rate*100).toFixed(1)}% <span class="muted">(${c.n_graduated}/${c.n_launches})</span></span></div>
       <div class="why-row"><span class="why-k">5× hit rate</span><span class="why-v">${(c.rate_5x*100).toFixed(1)}%</span></div>
       <div class="why-row"><span class="why-k">10× hit rate</span><span class="why-v">${(c.rate_10x*100).toFixed(1)}%</span></div>
       <div class="why-row"><span class="why-k">best ever</span><span class="why-v">${num(c.best_max_mult, 2)}×</span></div>`
    : `<div class="why-body muted">No prior launches indexed for this creator. Either a fresh wallet or a one-off launcher we haven't seen before.</div>`;

  // Hot-launch signals — ≥4 SOL bought in first 2s with concentrated
  // top1. Forward-validation showed these mints rug 49.6% vs 80.8%
  // baseline (lift 0.61) — they're actually *safer* than the average
  // mint. Reframed from the old "manufactured pump" label.
  const mfPump = m.manufactured_pump
    ? `<div class="why-row"><span class="why-k">first 2s</span><span class="why-v"><span class="ok">${num(m.sol_spent_first_2s, 1)}</span> SOL bought</span></div>
       <div class="why-row"><span class="why-k">first 5s</span><span class="why-v">${num(m.sol_spent_first_5s, 1)} SOL bought</span></div>
       <div class="why-body muted" style="margin-top:6px">🔥 Hot launch — heavy concentrated entry in the first 2s. Forward-validation: these mints rug at 50% vs 81% baseline (lift 0.61). Either insider/coordinated launch — historically <i>safer</i> than the average mint.</div>`
    : `<div class="why-body muted">Early-buy pattern looks organic. ${num(m.sol_spent_first_2s ?? 0, 1)} SOL in first 2s, ${num(m.sol_spent_first_5s ?? 0, 1)} SOL in first 5s.</div>`;

  // Note: returns the inner content (the .why-grid div), no <tr>/<td>
  // wrapper. The detail popup hosts this content directly. If we ever need
  // the in-table tr form again, wrap callsite-side.
  // Post-grad survival predictor block. Three states:
  //   - null   : daemon hasn't cached anything yet
  //   - warming: <20 resolved feature-having samples; show count
  //   - live   : real per-mint k-NN prediction with neighbor count
  const pgs = m.post_grad_survival;
  let postGradBody;
  if (!pgs) {
    postGradBody = `<div class="why-body muted">post-grad survival predictor warming up — calibrating against new resolved graduates.</div>`;
  } else if (pgs.status === 'sunset_lane_60s_structural_limit') {
    postGradBody = `<div class="why-body muted">post-grad survival predictor PERMANENTLY SUNSET (2026-05-08). Three model-class attempts (Path C max-scaling, Path D2 log-z-score + binary post-filter, Path 7h calibrated logistic regression with interaction terms) all failed pre-registered acceptance criteria. Structural finding: lane-60s sustain prediction is not viable from the available features given the signature distribution of resolved graduates. The aggregate <code>post_graduation.sustain_rate_30m</code> on <a href="/api/accuracy">/api/accuracy</a> continues unchanged — that's the independent Jupiter measurement. See <a href="https://github.com/Based-LTD/graduate-oracle/blob/main/docs/research/post_grad_metric_broken_since_launch.md">the full Finding 7 chain</a> for the complete receipts trail.</div>`;
  } else if (pgs.status === 'sunset_pending_architecture_review' || pgs.status === 'metric_recalibration_in_progress') {
    postGradBody = `<div class="why-body muted">post-grad survival predictor temporarily disabled (2026-05-07). Two metric replacements failed pre-registered acceptance criteria; validation surfaced that 3 of 5 feature columns have been writing zero at graduation-time since launch. Root cause was a snapshot-source bug — fix shipping; clean corpus rebuilding. See <a href="https://github.com/Based-LTD/graduate-oracle/blob/main/docs/research/post_grad_metric_broken_since_launch.md">post_grad_metric_broken_since_launch.md</a>.</div>`;
  } else if (pgs.status === 'sunset_pending_validation_rerun') {
    postGradBody = `<div class="why-body muted">post-grad survival predictor — clean corpus accumulated (<b>${pgs.n_total_resolved || 0}</b> rows). Awaiting Path D2 validation re-run on clean data before the auto-lift gate flips. See <a href="https://github.com/Based-LTD/graduate-oracle/blob/main/docs/research/post_grad_metric_broken_since_launch.md">post_grad_metric_broken_since_launch.md</a>.</div>`;
  } else if (pgs.status === 'warming_clean_corpus_accumulating') {
    postGradBody = `<div class="why-body muted">post-grad survival predictor — clean corpus rebuilding after the data-plumbing fix (Finding 7e, 2026-05-07). <b>${pgs.n_total_resolved || 0}</b> / 30 clean post-fix samples accumulated. Predictor lifts automatically when threshold is crossed and the Path D2 distance distribution validates.</div>`;
  } else if (pgs.status === 'warming' || pgs.prob == null) {
    postGradBody = `<div class="why-body muted">predictor warming · <b>${pgs.n_total_resolved || 0}</b> / 30 resolved historical graduates with feature data. Once we cross the threshold, this mint gets a calibrated 30-min sustain probability.</div>`;
  } else {
    const pp = (pgs.prob * 100).toFixed(0);
    const cls = pgs.prob >= 0.65 ? 'ok' : pgs.prob >= 0.40 ? 'warn' : 'bad';
    postGradBody = `<div class="why-row"><span class="why-k">if it graduates, sustain ≥80% of grad price for 30 min</span><span class="why-v"><span class="${cls}">${pp}%</span></span></div>
      <div class="why-row"><span class="why-k">k-NN neighbors</span><span class="why-v">${pgs.n_neighbors} closest of ${pgs.n_total_resolved} resolved</span></div>
      <div class="why-body muted" style="margin-top:6px">k-NN over historical graduates with similar smart-money / whale / velocity / fee-delegation features at the moment of graduation. Self-correcting — training set rebuilds every 5 min as new outcomes resolve.</div>`;
  }

  return `${metaBlock}${badgeBlock}<div class="why-grid">
        <div class="why-section">
          <div class="why-title">🎯 combined probability breakdown</div>
          ${combinedBlock || `<div class="why-body muted">Combined headline not available — check that the model has data for this mint.</div>`}
        </div>
        <div class="why-section">
          <div class="why-title">why this probability</div>
          <div class="why-body">${knn}</div>
        </div>
        <div class="why-section">
          <div class="why-title">runner odds (multi-tier)</div>
          ${runnerRows}
          ${peakLine}
        </div>
        <div class="why-section">
          <div class="why-title">creator track record</div>
          ${creatorBody}
        </div>
        <div class="why-section">
          <div class="why-title">velocity</div>
          ${velRow}
        </div>
        <div class="why-section">
          <div class="why-title">smart-money cluster</div>
          ${clusterRow}
        </div>
        <div class="why-section">
          <div class="why-title">🔥 hot launch check</div>
          ${mfPump}
        </div>
        <div class="why-section">
          <div class="why-title">🎓 post-graduation survival</div>
          ${postGradBody}
        </div>
        <div class="why-section">
          <div class="why-title">activity health</div>
          <div class="why-row"><span class="why-k">sell ratio</span><span class="why-v">${sellLabel}</span></div>
          <div class="why-row"><span class="why-k">buys / wallet</span><span class="why-v">${bpbLabel}</span></div>
          <div class="why-row"><span class="why-k">last trade</span><span class="why-v">${num(m.last_trade_age_s, 1)}s ago</span></div>
          <div class="why-row"><span class="why-k">growth</span><span class="why-v">${m.first_vsol_sol?.toFixed(0) ?? '—'} → <b>${m.current_vsol_sol?.toFixed(0) ?? '—'}</b> vSOL · max ${num(m.max_mult, 2)}×</span></div>
        </div>
        <div class="why-section">
          <div class="why-title">buyer composition</div>
          <div class="why-row"><span class="why-k">top buyer</span><span class="why-v">${top1} of buy volume</span></div>
          <div class="why-row"><span class="why-k">top 3 combined</span><span class="why-v">${top3}</span></div>
          <div class="why-row"><span class="why-k">unknown wallets</span><span class="why-v">${unk}</span></div>
          <div class="why-row"><span class="why-k">low-history wallets</span><span class="why-v">${lowH}</span></div>
          <div class="why-row"><span class="why-k">known snipers</span><span class="why-v">${snip}</span></div>
          <div class="why-row"><span class="why-k">avg buyer reputation</span><span class="why-v">${smart}</span></div>
        </div>
        <div class="why-section">
          <div class="why-title">top buyers</div>
          <div class="why-buyers">${buyers}</div>
        </div>
        <div class="why-section">
          <div class="why-title">bot signals</div>
          <div class="why-flags">${flagsHTML}</div>
        </div>
      </div>`;
}

// ─── hero card ──────────────────────────────────────────────────────────
// Now takes an array of HITS (mints scoring ≥70% grad_prob). When 0 hits,
// hidden. When 1, shows the standard featured layout. When 2+, shows the
// strongest in the main hero block + secondary hits as compact strips
// below it. The label changed from "HOTTEST RIGHT NOW" → "🎯 HITS — ≥70%
// to graduate" to match the simplified product framing.
function renderHero(hits) {
  const hero = document.getElementById('hero');
  if (!hits || hits.length === 0 || !hits[0] || hits[0].grad_prob == null) {
    hero.style.display = 'none';
    return;
  }
  const top = hits[0];
  hero.style.display = '';
  document.getElementById('hero-mint').dataset.mint = top.mint;
  document.getElementById('hero-mint-text').textContent = shortMint(top.mint);
  // Token metadata — image + name headline. Renders under the
  // "HOTTEST RIGHT NOW" label, before the stats row.
  const metaEl = document.getElementById('hero-meta');
  if (metaEl) {
    const meta = top.metadata || {};
    const img = meta.image_url
      ? `<img class="hero-thumb" src="${meta.image_url}" alt="" loading="lazy" onerror="this.style.display='none'">`
      : '';
    const name = meta.name ? `<span class="hero-name">${meta.name}</span>` : '';
    const sym  = meta.symbol ? `<span class="hero-symbol">$${meta.symbol}</span>` : '';
    metaEl.innerHTML = (img || name || sym) ? `${img}<span class="hero-meta-text">${name}${sym}</span>` : '';
  }
  document.getElementById('hero-vsol').textContent = fmtSol(top.current_vsol_sol);
  document.getElementById('hero-buyers').textContent = top.unique_buyers;
  document.getElementById('hero-age').textContent = fmtAge(top.age_s);
  document.getElementById('hero-prob').textContent = fmtPct(top.grad_prob);
  document.getElementById('hero-prob-5x').textContent  = top.runner_prob_5x  != null ? fmtPct(top.runner_prob_5x)  : '—';
  document.getElementById('hero-prob-10x').textContent = top.runner_prob_10x != null ? fmtPct(top.runner_prob_10x) : '—';
  document.getElementById('hero-peak').textContent     = top.expected_peak_mult != null
    ? `${top.expected_peak_mult.toFixed(2)}×`
    : '—';
  document.getElementById('hero-badges').innerHTML  = badgesHTML(top);
  // Creator inline summary on the hero — shorter than the table cell version.
  const heroCreatorEl = document.getElementById('hero-creator');
  const c = top.creator_history;
  if (c) {
    const cls = c.runner_creator ? 'up' : c.good_creator ? '' : 'muted';
    heroCreatorEl.innerHTML = `<span class="hero-creator-line ${cls}">creator: <b>${c.n_launches}</b> prior launches · <b>${(c.grad_rate*100).toFixed(0)}%</b> grad · <b>${(c.rate_5x*100).toFixed(0)}%</b> 5×</span>`;
  } else if (top.first_buyer) {
    heroCreatorEl.innerHTML = `<span class="hero-creator-line muted">creator: new (no prior history)</span>`;
  } else {
    heroCreatorEl.innerHTML = '';
  }
  document.getElementById('hero-actions').innerHTML = actionsHTML(top.mint);
  // Stash the mint on the EXPAND button so the existing
  // `[data-expand-mint]` click handler picks it up and opens the same
  // detail popup the table rows use — no separate code path.
  const expandBtn = document.getElementById('hero-expand');
  if (expandBtn) expandBtn.setAttribute('data-expand-mint', top.mint);
  // Hero trend pill
  const trendEl = document.getElementById('hero-trend');
  if (trendEl) {
    const info = trendInfo(top.mint, top.grad_prob);
    if (!info || Math.abs(info.delta) < 0.005) {
      trendEl.className = 'hero-trend trend-flat';
      trendEl.textContent = '· stable';
    } else {
      const sign = info.delta >= 0 ? '+' : '';
      const arrow = info.dir === 'up' ? '▲' : '▼';
      trendEl.className = `hero-trend trend-${info.dir}`;
      trendEl.textContent = `${arrow} ${sign}${(info.delta * 100).toFixed(1)}pp / 60s`;
    }
  }
  // Secondary hits — when more than one mint is at ≥70% right now,
  // surface the others as compact strips under the featured one. Each
  // strip is a clickable row that opens the same detail popup the table
  // uses. Limited to 4 extras to keep the hero from sprawling.
  const extrasEl = document.getElementById('hero-extras');
  if (extrasEl) {
    const extras = (hits || []).slice(1, 5);
    if (extras.length === 0) {
      extrasEl.innerHTML = '';
    } else {
      extrasEl.innerHTML =
        `<div class="hero-extras-label">Other hits right now</div>` +
        extras.map(m => {
          const meta = m.metadata || {};
          const name = meta.name || meta.symbol || shortMint(m.mint);
          const cm = m.current_mult || 0;
          return `<button type="button" class="hero-extra" data-expand-mint="${m.mint}" title="click for breakdown">
            <span class="hero-extra-prob">${fmtPct(m.grad_prob)}</span>
            <span class="hero-extra-name">${name}</span>
            <span class="hero-extra-meta">${cm.toFixed(2)}× · ${fmtAge(m.age_s)} · ${fmtSol(m.current_vsol_sol)} vSOL</span>
          </button>`;
        }).join('');
    }
  }
}

// ─── smart money leaderboard — DOM removed 2026-05-11 per
// project_wallet_index_is_the_moat.md. Function retained as no-op
// for any leftover tab-switcher callers; bails early if DOM is gone.
async function refreshSmart() {
  const empty = document.getElementById('smart-empty');
  const tbody = document.getElementById('smart-rows');
  if (!empty || !tbody) return;  // panel removed; nothing to render
  empty.style.display = '';
  empty.textContent = 'computing wallet stats…';
  try {
    const r = await fetch('/api/wallets?kind=smart&limit=100&min_total=8');
    const data = await r.json();
    const wallets = data.wallets || [];
    if (wallets.length === 0) {
      empty.textContent = 'still computing — wallet index builds at startup. retry in a few seconds.';
      tbody.innerHTML = '';
      return;
    }
    empty.style.display = 'none';
    tbody.innerHTML = wallets.map((w, i) => `<tr>
      <td class="muted">${i + 1}</td>
      <td><span class="mint-cell" data-mint="${w.wallet}" title="click to copy"><span class="mint-text">${w.wallet}</span><span class="copy-icon">⎘</span></span></td>
      <td class="r">${w.total}</td>
      <td class="r"><span class="up">${w.graduated}</span></td>
      <td class="r"><span class="up">${w.runner}</span></td>
      <td class="r"><span class="down">${w.rug}</span></td>
      <td class="r ${w.grad_rate >= 0.15 ? 'up' : 'muted'}">${fmtPct(w.grad_rate)}</td>
      <td class="r ${w.good_rate >= 0.4 ? 'up' : 'muted'}">${fmtPct(w.good_rate)}</td>
      <td class="r"><span class="prob prob-high"><span class="prob-num">${(w.smart_score * 100).toFixed(1)}</span></span></td>
      <td><a class="btn" href="https://solscan.io/account/${w.wallet}" target="_blank" rel="noreferrer">SOL</a></td>
    </tr>`).join('');
  } catch {
    empty.textContent = 'failed to load — retry shortly.';
  }
}

async function refreshSnipers() {
  const empty = document.getElementById('sniper-empty');
  const tbody = document.getElementById('sniper-rows');
  empty.style.display = '';
  empty.textContent = 'computing sniper stats…';
  try {
    const r = await fetch('/api/wallets?kind=sniper&limit=80&min_total=8');
    const data = await r.json();
    const wallets = data.wallets || [];
    if (wallets.length === 0) {
      empty.textContent = 'no sniper wallets identified yet (or index still building).';
      tbody.innerHTML = '';
      return;
    }
    empty.style.display = 'none';
    tbody.innerHTML = wallets.map((w, i) => `<tr>
      <td class="muted">${i + 1}</td>
      <td><span class="mint-cell" data-mint="${w.wallet}"><span class="mint-text">${w.wallet}</span><span class="copy-icon">⎘</span></span></td>
      <td class="r">${w.total}</td>
      <td class="r"><span class="down">${w.fast_snipes}</span></td>
      <td class="r down">${fmtPct(w.rug_rate)}</td>
      <td class="r muted">${fmtPct(w.good_rate)}</td>
      <td><a class="btn" href="https://solscan.io/account/${w.wallet}" target="_blank" rel="noreferrer">SOL</a></td>
    </tr>`).join('');
  } catch {
    empty.textContent = 'failed to load — retry shortly.';
  }
}

// ─── paper P&L panel ────────────────────────────────────────────────────
async function refreshPaper() {
  const cards = document.getElementById('paper-cards');
  const tbody = document.getElementById('paper-rows');
  const empty = document.getElementById('paper-empty');
  try {
    const r = await fetch('/api/paper');
    const data = await r.json();
    const buckets = data.strategies || data.thresholds || {};
    // Preserve declared strategy order from config
    const declared = (data.config?.strategies || []).map(s => s.id);
    const keys = declared.length
      ? declared.filter(k => k in buckets)
      : Object.keys(buckets).sort();

    // ── summary cards ─────────────────────────────────────────────────────
    cards.innerHTML = keys.map(k => {
      const b = buckets[k];
      const wr = b.win_rate != null ? (b.win_rate * 100).toFixed(0) + '%' : '—';
      const avg = b.avg_pnl_pct != null ? (b.avg_pnl_pct >= 0 ? '+' : '') + (b.avg_pnl_pct * 100).toFixed(1) + '%' : '—';
      const avgCls = (b.avg_pnl_pct || 0) > 0 ? 'up' : (b.avg_pnl_pct || 0) < 0 ? 'down' : 'muted';
      const bestPct = (kk => kk != null ? (kk >= 0 ? '+' : '') + (kk * 100).toFixed(0) + '%' : '—')(b.best_pnl_pct);
      const worstPct = (kk => kk != null ? (kk >= 0 ? '+' : '') + (kk * 100).toFixed(0) + '%' : '—')(b.worst_pnl_pct);

      const bankrollStart = b.bankroll_start_sol ?? 1;
      const bankroll = b.bankroll_sol ?? bankrollStart;
      const profit = b.profit_sol ?? 0;
      const unrealized = b.unrealized_sol ?? 0;
      const posSize = b.position_size_sol ?? 0.1;
      const bankrollCls = profit > 0 ? 'up' : profit < 0 ? 'down' : 'muted';
      const profitTxt = (profit >= 0 ? '+' : '') + profit.toFixed(3) + ' SOL';
      const roiPct = ((bankroll / bankrollStart - 1) * 100).toFixed(1);
      const unrlCls = unrealized > 0 ? 'up' : unrealized < 0 ? 'down' : 'muted';
      const unrlTxt = (unrealized >= 0 ? '+' : '') + unrealized.toFixed(3) + ' SOL';

      // Strategy label: prefer server-supplied; fallback to legacy threshold key
      const label = b.label
        || (b.threshold != null ? `≥ ${(b.threshold * 100).toFixed(0)}% prob` : k);
      const slBadge = b.stop_loss_pct
        ? `<span class="strat-sl-badge sl-on">−${(b.stop_loss_pct*100).toFixed(0)}% SL</span>`
        : `<span class="strat-sl-badge sl-off">no SL</span>`;
      const slLine = b.n_stop_loss > 0
        ? `<div class="paper-card-row"><span class="paper-k">stop-loss hits</span><span class="paper-v down">${b.n_stop_loss}</span></div>`
        : '';

      return `<div class="paper-card">
        <div class="paper-card-head">${label} ${slBadge}</div>
        <div class="paper-bankroll">
          <div class="paper-bankroll-line"><span class="muted">${bankrollStart.toFixed(2)} SOL →</span> <span class="paper-bankroll-now ${bankrollCls}">${bankroll.toFixed(3)} SOL</span></div>
          <div class="paper-bankroll-sub ${bankrollCls}">realized ${profitTxt} <span class="muted">(${roiPct >= 0 ? '+' : ''}${roiPct}% ROI)</span></div>
          ${b.n_open > 0 ? `<div class="paper-bankroll-sub ${unrlCls}">unrealized ${unrlTxt} <span class="muted">across ${b.n_open} open</span></div>` : ''}
        </div>
        <div class="paper-card-row"><span class="paper-k">position size</span><span class="paper-v">${posSize.toFixed(2)} SOL <span class="muted">(${(posSize/bankrollStart*100).toFixed(0)}% of starting)</span></span></div>
        <div class="paper-card-row"><span class="paper-k">trades</span><span class="paper-v">${b.n_total} <span class="muted">(${b.n_open} open · ${b.n_closed} closed)</span></span></div>
        <div class="paper-card-row"><span class="paper-k">win rate</span><span class="paper-v">${wr}</span></div>
        <div class="paper-card-row"><span class="paper-k">avg P&amp;L / trade</span><span class="paper-v ${avgCls}">${avg}</span></div>
        ${slLine}
        <div class="paper-card-row"><span class="paper-k">best / worst</span><span class="paper-v"><span class="up">${bestPct}</span> / <span class="down">${worstPct}</span></span></div>
      </div>`;
    }).join('');

    // ── recent trades (merged across all strategies, newest first) ───────
    const allRecent = [];
    for (const k of keys) {
      const strat = buckets[k];
      for (const t of (strat.recent || [])) {
        allRecent.push({
          ...t,
          strategy_id: strat.id || k,
          strategy_label: strat.label || k,
          threshold: strat.threshold,
        });
      }
    }
    allRecent.sort((a, b) => (b.exit_at || b.entry_at) - (a.exit_at || a.entry_at));
    const trades = allRecent.slice(0, 30);

    if (trades.length === 0) {
      empty.style.display = '';
      tbody.innerHTML = '';
      return;
    }
    empty.style.display = 'none';

    const reasonClass = {
      target_20:   'up',
      take_profit: 'up',
      graduated:   'up',
      stop_loss:   'down',
      stale:       'muted',
      timeout:     'muted',
      dead:        'down',
    };
    tbody.innerHTML = trades.map(t => {
      const isOpen = !t.exit_at;
      const pnl = t.pnl_pct != null ? (t.pnl_pct >= 0 ? '+' : '') + (t.pnl_pct * 100).toFixed(1) + '%' : '—';
      const pnlCls = (t.pnl_pct || 0) > 0 ? 'up' : (t.pnl_pct || 0) < 0 ? 'down' : 'muted';
      const hold = t.hold_secs != null ? fmtAge(t.hold_secs) : (t.entry_at ? fmtAge(Math.max(0, Math.floor(Date.now()/1000) - t.entry_at)) : '—');
      const reason = t.exit_reason || (isOpen ? 'open' : '—');
      const reasonHtml = isOpen
        ? '<span class="paper-state open">● live</span>'
        : `<span class="paper-state ${reasonClass[reason] || ''}">${reason.replace(/_/g,' ')}</span>`;
      return `<tr>
        <td class="r muted" title="${t.strategy_label || ''}">${t.strategy_label || ((t.threshold||0)*100).toFixed(0)+'%'}</td>
        <td><span class="mint-cell" data-mint="${t.mint}"><span class="mint-text">${shortMint(t.mint)}</span><span class="copy-icon">⎘</span></span></td>
        <td class="r">${(t.entry_grad_prob * 100).toFixed(0)}%</td>
        <td class="r">${fmtMult(t.entry_mult)}</td>
        <td class="r">${t.exit_mult != null ? fmtMult(t.exit_mult) : '—'}</td>
        <td class="r ${pnlCls}">${pnl}</td>
        <td class="r muted">${hold}</td>
        <td>${isOpen ? '—' : (t.exit_reason || '—').replace(/_/g,' ')}</td>
        <td>${reasonHtml}</td>
      </tr>`;
    }).join('');
  } catch (e) {
    empty.style.display = '';
    empty.textContent = 'failed to load paper P&L — retry shortly.';
  }
}

// ─── watchlist panel ────────────────────────────────────────────────────
function renderWatchlist() {
  const tbody = document.getElementById('watch-rows');
  const empty = document.getElementById('watch-empty');
  if (!tbody) return;
  if (watchSet.size === 0) {
    tbody.innerHTML = '';
    empty.style.display = '';
    return;
  }
  empty.style.display = 'none';
  // Cross-reference with live data when available
  const liveByMint = {};
  if (lastData && lastData.mints) {
    for (const m of lastData.mints) liveByMint[m.mint] = m;
  }
  tbody.innerHTML = [...watchSet].map(mint => {
    const live = liveByMint[mint];
    const liveBadge = live ? `<span class="up">● live</span>` : `<span class="muted">○ idle</span>`;
    const vsol = live ? fmtSol(live.current_vsol_sol) : '—';
    const buyers = live ? live.unique_buyers : '—';
    const oddsCell = live ? oddsHTML(live) : '<span class="muted">—</span>';
    const creatorCell = live ? creatorCellHTML(live) : '<span class="muted">—</span>';
    const badges = live ? badgesHTML(live) : '';
    return `<tr data-mint="${mint}">
      <td><span class="star on" data-toggle-watch="${mint}" title="unwatch">★</span></td>
      <td>
        <div class="contract-stack">
          <div class="contract-line">
            <span class="mint-cell" data-mint="${mint}">
              <span class="mint-text">${shortMint(mint)}</span>
              <span class="copy-icon">⎘</span>
            </span>
          </div>
          ${badges}
        </div>
      </td>
      <td class="r" style="font-size:0.78rem">${liveBadge}</td>
      <td class="r">${vsol}</td>
      <td class="r">${buyers}</td>
      <td class="r">${oddsCell}</td>
      <td class="r">${creatorCell}</td>
      <td>${actionsHTML(mint)}</td>
    </tr>`;
  }).join('');
}

// ─── tick ───────────────────────────────────────────────────────────────
async function tick() {
  try {
    const r = await fetch('/api/live');
    if (!r.ok) {
      document.getElementById('snap-age').textContent = `error ${r.status}`;
      return;
    }
    const data = await r.json();
    render(data);
  } catch {
    document.getElementById('snap-age').textContent = `connection lost`;
  }
}

// ─── acceptance-gates banner (Finding 7/8 chain) ────────────────────────
// Polls /api/status every 30s. Shows the banner when any gate is active;
// hides it cleanly when all gates close. Banner text is the highest-
// priority gate's summary; full list lives at /status.
async function refreshAcceptanceBanner() {
  try {
    const r = await fetch('/api/status');
    if (!r.ok) return;
    const s = await r.json();
    const gates = s.acceptance_gates || [];
    const banner = document.getElementById('acceptance-gates-banner');
    if (!banner) return;
    if (gates.length === 0) {
      banner.style.display = 'none';
      return;
    }
    // Prefer the bucket-calibration gate as the headline (most user-visible:
    // alerts paused). Sustain auto-lift is technical and shows on /status.
    const headline = gates.find(g => g.id === 'finding_8_bucket_calibration') || gates[0];
    const txt = document.getElementById('acceptance-banner-text');
    if (txt && headline) {
      txt.textContent = headline.summary || headline.name;
    }
    banner.style.display = 'block';
  } catch {
    // Silently — the banner is optional UI; never break /api/live ticks.
  }
}
refreshAcceptanceBanner();
setInterval(refreshAcceptanceBanner, 30000);

// ─── event handlers ─────────────────────────────────────────────────────
document.addEventListener('click', (e) => {
  // copy mint
  const mintCell = e.target.closest('.mint-cell');
  if (mintCell) {
    e.stopPropagation();
    const m = mintCell.getAttribute('data-mint');
    if (m) copyToClipboard(m, mintCell);
    return;
  }
  // toggle watch
  const star = e.target.closest('[data-toggle-watch]');
  if (star) {
    e.stopPropagation();
    toggleWatch(star.getAttribute('data-toggle-watch'));
    return;
  }
  // expand/collapse "why this score" — ONLY the chevron button triggers this.
  // Clicking the row body, action links (PUMP/AXM/PHO/DEX/SOL), star, or
  // mint-cell does NOT toggle, so external links work on first click and we
  // don't accidentally expand-on-blur when users come back from another tab.
  const chevBtn = e.target.closest('[data-expand-mint]');
  if (chevBtn) {
    e.preventDefault();
    e.stopPropagation();
    const mint = chevBtn.dataset.expandMint;
    const row = chevBtn.closest('tr');
    if (!row) return;
    if (expandedMint === mint) {
      _closeDetailPopup();
    } else {
      _openDetailPopup(mint);
    }
    return;
  }
  // tab buttons
  const tab = e.target.closest('.tab');
  if (tab) {
    activateTab(tab.dataset.tab);
    return;
  }
  // empty-state link "go to grads"
  const goto = e.target.closest('[data-go-tab]');
  if (goto) {
    e.preventDefault();
    activateTab(goto.getAttribute('data-go-tab'));
  }
  // details toggles (panel-sub "how it works ▸" buttons)
  const toggle = e.target.closest('[data-toggle-details]');
  if (toggle) {
    const targetId = toggle.getAttribute('data-toggle-details');
    const target = document.getElementById(targetId);
    if (target) {
      const open = target.hasAttribute('hidden') ? false : true;
      if (open) {
        target.setAttribute('hidden', '');
        toggle.classList.remove('expanded');
        toggle.textContent = 'how it works ▸';
      } else {
        target.removeAttribute('hidden');
        toggle.classList.add('expanded');
        toggle.textContent = 'hide details ▾';
      }
    }
  }
});

// When the page is restored from the browser's back-forward cache (e.g. user
// clicked an external Axiom/PUMP link in a new tab and came back), the JS
// state is preserved but the user expects a fresh view. Collapse any open
// detail panel and trigger a poll so the dashboard reflects current data.
window.addEventListener('pageshow', (e) => {
  if (e.persisted) {
    _closeDetailPopup();
    tick();
  }
});

// ─── proven-accuracy ticker · pulled from /api/accuracy ─────────────────
let calData = null;
let calRotateIdx = 0;
let calRotateTimer = null;

async function refreshCalibration() {
  try {
    const r = await fetch('/api/accuracy');
    const d = await r.json();
    // Hero hydration — runway (median time-to-grad after our ≥0.70 call)
    // is the new headline stat. The 30-day hit rate now plays the
    // "accuracy on top of that runway" support role.
    const hl = d.headline || null;
    const timing = hl?.time_to_grad;
    const heroRunway = document.getElementById('hero-runway');
    const heroRunwayN = document.getElementById('hero-runway-n');
    if (heroRunway && timing?.status === 'ok' && timing.p50_s > 0) {
      const s = timing.p50_s;
      heroRunway.textContent = s < 60 ? `${s}s` : `${Math.round(s/60)}m`;
      if (heroRunwayN && timing.n_grads) {
        heroRunwayN.textContent = ` (n=${timing.n_grads.toLocaleString()})`;
      }
    }
    const last30 = hl?.last_30d;
    const heroNum = document.getElementById('hero-last30-rate');
    const heroN   = document.getElementById('hero-last30-n');
    if (heroNum && last30?.status === 'ok' && last30.n_resolved > 0) {
      heroNum.textContent = `${(last30.hit_rate * 100).toFixed(1)}%`;
      if (heroN) heroN.textContent = ` (n=${last30.n_resolved.toLocaleString()})`;
    }
    // New shape: { lifetime: {...}, forward: {...} }
    // Old shape: { thresholds: {...}, total_samples: ... }  (legacy fallback)
    const lifetime = d.lifetime ?? (d.status === 'ok' ? d : null);
    const forward = d.forward ?? null;
    const forwardCal = d.forward_calibrated ?? null;
    const runner   = d.runner   ?? null;
    // Show "warming" ONLY if every source is empty. The forward track
    // (on-chain-resolved receipts, ~17k resolved) is the better number
    // to lead with anyway — lifetime is LOO cross-val and frequently
    // warming/None. Old bug: bailed on lifetime warming and never tried
    // forward, so the ticker showed "warming" while sitting on a
    // mountain of resolved forward receipts. pickGrad() already prefers
    // forward → forwardCal → lifetime; just don't gate before it runs.
    const lifetimeOk = lifetime && lifetime.status !== 'warming' && lifetime.thresholds;
    const forwardOk  = (forward && forward.thresholds) ||
                       (forwardCal && forwardCal.thresholds) ||
                       (runner && runner.tiers);
    if (!lifetimeOk && !forwardOk) {
      const t = document.getElementById('cal-ticker');
      const c = document.getElementById('cal-ticker-content');
      if (t && c) { t.dataset.state = 'warming'; c.textContent = 'computing model calibration… (first run)'; }
      return;
    }
    calData = { lifetime, forward, forwardCal, runner };
    document.getElementById('cal-ticker').dataset.state = 'live';
    rotateCalibration();
  } catch {}
}

function rotateCalibration() {
  // Any source with data is enough — not lifetime-gated (it's usually
  // the warming/empty one; forward carries the receipts).
  if (!calData || (!calData.lifetime?.thresholds &&
                   !calData.forward?.thresholds &&
                   !calData.forwardCal?.thresholds &&
                   !calData.runner?.tiers)) return;
  // ── Ticker philosophy: lead with the win rate, not the gap. ──
  // We pick the most flattering ACCURATE bucket per source rather than
  // showing the highest-threshold one (which can have a wide gap and
  // invites the "but you said 90, only got 79" misread). Specifically:
  //   1. Prefer over-delivery buckets (actual_rate >= threshold).
  //   2. Among those, take the HIGHEST threshold (most impressive band
  //      that still over-delivered).
  //   3. If nothing over-delivers, fall back to smallest-gap bucket.
  // Visual format puts the win rate first and confidence-band second.
  const items = [];

  const pickBest = (buckets, rateKey) => {
    const ok = buckets.filter(b => b[rateKey] != null);
    if (!ok.length) return null;
    const overDeliver = ok.filter(b => b[rateKey] >= b.threshold_pct / 100);
    if (overDeliver.length) {
      return overDeliver.sort((a, b) => b.threshold_pct - a.threshold_pct)[0];
    }
    // No bucket over-delivers — pick smallest-gap one.
    return ok.sort((a, b) => {
      const ga = (a.threshold_pct / 100) - a[rateKey];
      const gb = (b.threshold_pct / 100) - b[rateKey];
      return ga - gb;
    })[0];
  };

  // GRAD line — pick the best source and the best bucket within.
  const pickGrad = () => {
    if (calData.forwardCal?.thresholds) {
      const ok = Object.values(calData.forwardCal.thresholds)
                       .filter(b => b.actual_grad_rate != null && b.n_resolved >= 30);
      if (ok.length) {
        const best = pickBest(ok, 'actual_grad_rate');
        if (best) return { ...best, _rate: best.actual_grad_rate, _n: best.n_resolved };
      }
    }
    if (calData.forward?.thresholds) {
      const ok = Object.values(calData.forward.thresholds)
                       .filter(b => b.actual_grad_rate != null && b.n_resolved >= 30);
      if (ok.length) {
        const best = pickBest(ok, 'actual_grad_rate');
        if (best) return { ...best, _rate: best.actual_grad_rate, _n: best.n_resolved };
      }
    }
    if (calData.lifetime?.thresholds) {
      const ok = Object.values(calData.lifetime.thresholds)
                       .filter(b => b.actual_grad_rate != null && b.n > 0);
      if (ok.length) {
        const best = pickBest(ok, 'actual_grad_rate');
        if (best) return { ...best, _rate: best.actual_grad_rate, _n: best.n };
      }
    }
    return null;
  };

  const grad = pickGrad();
  if (grad) {
    items.push({
      type: 'grad',
      threshold_pct: grad.threshold_pct,
      rate: grad._rate,
      n:    grad._n,
    });
  }

  // Runner-tier accuracy — one line per tier with ≥30 resolved.
  // Use the same "best bucket" picker so the visible numbers are also
  // the most flattering accurate ones per tier.
  if (calData.runner?.tiers) {
    for (const [tierLabel, tier] of Object.entries(calData.runner.tiers)) {
      const ok = Object.values(tier.thresholds || {})
                       .filter(x => x.actual_hit_rate != null && x.n_resolved >= 30);
      if (!ok.length) continue;
      const best = pickBest(ok.map(b => ({...b, actual_grad_rate: b.actual_hit_rate})),
                            'actual_grad_rate');
      if (best) {
        items.push({
          type: 'runner',
          tag:  tierLabel.toUpperCase(),
          threshold_pct: best.threshold_pct,
          rate: best.actual_hit_rate,
          n:    best.n_resolved,
        });
      }
    }
  }

  // Reorder: runner tiers first (they unambiguously over-deliver and
  // look great), GRAD line second (still solid but the hit rate is the
  // story, not the threshold gap).
  items.sort((a, b) => {
    const order = { runner: -1, grad: 1 };
    return (order[a.type] || 0) - (order[b.type] || 0);
  });

  if (!items.length) return;
  calRotateIdx = (calRotateIdx + 1) % items.length;
  const it = items[calRotateIdx];
  const c = document.getElementById('cal-ticker-content');
  if (!c) return;
  const pct = (it.rate * 100).toFixed(0);
  const n   = it.n.toLocaleString();
  // Format puts the win rate first ("80% hit"), confidence band as
  // secondary muted text. No "predicted ≥90% → 79% graduated" shape — that
  // framing invited gap-comparisons; this one reads as a clean win rate.
  if (it.type === 'grad') {
    c.innerHTML =
      `<span class="cal-chip">GRAD</span> ` +
      `<b class="cal-rate">${pct}%</b> hit rate ` +
      `<span class="cal-meta">on ≥${it.threshold_pct}% conviction · ${n} verified</span>`;
  } else {
    c.innerHTML =
      `<span class="cal-chip cal-chip-runner">${it.tag} runner</span> ` +
      `<b class="cal-rate">${pct}%</b> hit rate ` +
      `<span class="cal-meta">on ≥${it.threshold_pct}% conviction · ${n} verified</span>`;
  }
}

// Start: fetch once on boot, refresh every 5 min, rotate display every 4s
refreshCalibration();
setInterval(refreshCalibration, 5 * 60 * 1000);
calRotateTimer = setInterval(rotateCalibration, 4000);

// ─── boot ───────────────────────────────────────────────────────────────
runBoot();
refreshWatchCount();
tick();
setInterval(tick, REFRESH_MS);
