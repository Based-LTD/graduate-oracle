//! tg-trader — multi-tenant execution layer for the graduate-oracle TG bot.
//!
//! Architecture:
//!
//!   TG bot (Python) → composite_score fires
//!     → user taps [BUY 0.5] inline button
//!     → Python reads bonding curve state from Helius RPC
//!     → Python calls tg-trader `build-buy-tx` with the curve state + blockhash
//!     → Python signs the returned unsigned tx with the user's wallet
//!       (custody key NEVER leaves Python)
//!     → Python calls tg-trader `submit-bundle` (Day 4.4) with signed bytes
//!     → tg-trader submits via Jito multi-region bundle
//!     → Python writes a Position row into /data/trader.sqlite
//!     → Background monitor (separate process) handles TP/SL via monitor.rs
//!
//! The protocol is **JSON-over-stdin**. Each line is one command. We respond
//! on stdout with one JSON line. Errors are returned as JSON, never panic.
//!
//! Commands implemented:
//!   - `health`           — sanity check that the binary is alive
//!   - `version`          — print library version + dependencies it can use
//!   - `dry-run-buy`      — validation only, no tx assembly
//!   - `build-buy-tx`     — assemble pump.fun pre-grad buy tx (unsigned, base64)
//!   - `build-sell-tx`    — assemble pump.fun pre-grad sell tx (unsigned, base64)
//!   - `submit-bundle`    — submit signed tx to Jito multi-region bundle
//!
//! Commands explicitly NOT YET IMPLEMENTED:
//!   - `monitor`          — long-running TP/SL loop (Day 5)
//!   - `fee-skim`         — operator fee + $GO burn redirect (Day 6)
//!
//! SAFETY GATE — submit-bundle:
//!   submit-bundle WILL NOT hit Jito unless BOTH conditions hold:
//!     1. Input `live` field is true
//!     2. Process env var `TG_TRADER_LIVE` is set to "1"
//!   Either missing → dry-run; we log what would have happened and return
//!   `would_submit=false`. The env var is the kill switch operators flip
//!   AFTER they've verified the orchestrator is wired up correctly.
//!
//! Post-graduation (Jupiter) buys do NOT pass through `build-buy-tx`. Python
//! fetches the swap tx directly from Jupiter's API (it returns a ready-to-sign
//! versioned tx), signs it, and hands the bytes to `submit-bundle`. Routing
//! decision lives in Python — see web/tg_trader_runner.py and the orchestrator.
//!
//! Read DESIGN: web/conditions.py for regime-aware gating. The Python side
//! will check /api/v1/conditions BEFORE calling build-buy-tx; if RED, it
//! prompts the user. We do NOT gate execution at this layer — the bot
//! always fires; the dashboard tells humans whether to act.
//!
//! Usage:
//!     echo '{"cmd":"health"}' | tg-trader
//!     echo '{"cmd":"dry-run-buy","user_id":"42","mint":"...","sol":0.5}' | tg-trader
//!     echo '{"cmd":"build-buy-tx",...}' | tg-trader

use anyhow::{anyhow, Result};
use base64::Engine;
use pump_jito_sniper::{jito, pump};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use solana_sdk::{
    compute_budget::ComputeBudgetInstruction,
    hash::Hash,
    instruction::Instruction,
    message::Message,
    pubkey::Pubkey,
    signature::Signature,
    transaction::Transaction,
};
use spl_associated_token_account::instruction::create_associated_token_account_idempotent;
use std::io::{self, BufRead, Write};
use std::str::FromStr;

// ─── Wire format ──────────────────────────────────────────────────────────

/// On-chain bonding curve state, as read by Python from the RPC.
/// We accept it as input so the Rust binary stays pure (no network calls)
/// and is fully unit-testable. Python is the source of truth for what's
/// on-chain right now.
#[derive(Deserialize, Debug)]
struct BondingCurveInput {
    virtual_sol_reserves: u64,
    virtual_token_reserves: u64,
    // real_* reserves are accepted for forward compatibility (sell-tx + audit
    // commitments may consume them in Day 4.3+). Currently unused in build-buy-tx.
    #[serde(default)]
    #[allow(dead_code)]
    real_sol_reserves: u64,
    #[serde(default)]
    #[allow(dead_code)]
    real_token_reserves: u64,
    #[serde(default)]
    token_total_supply: u64,
    complete: bool,
    /// base58-encoded creator pubkey from the bonding curve account
    creator: String,
    #[serde(default)]
    is_cashback_coin: bool,
}

#[derive(Deserialize, Debug)]
#[serde(tag = "cmd", rename_all = "kebab-case")]
enum Command {
    Health,
    Version,
    DryRunBuy {
        user_id: String,
        mint: String,
        sol: f64,
        #[serde(default)]
        slippage_bps: Option<u64>,
    },
    BuildBuyTx {
        user_id: String,
        mint: String,
        /// base58 payer pubkey (signs and pays)
        payer: String,
        sol: f64,
        #[serde(default)]
        slippage_bps: Option<u64>,
        bonding_curve: BondingCurveInput,
        /// base58 recent blockhash
        recent_blockhash: String,
        /// priority fee in microlamports per CU (default 100_000)
        #[serde(default)]
        priority_fee_microlamports: Option<u64>,
        /// compute unit limit (default 200_000)
        #[serde(default)]
        compute_units: Option<u32>,
        /// Jito tip in lamports. When present (and > 0), appends a
        /// system-transfer ix to a randomly-chosen Jito tip account.
        /// Required for the bundle to be PRIORITIZED by Jito. Without
        /// it, the bundle is accepted but unlikely to land under any
        /// competition. Typical range: 10_000 (0.00001 SOL) to
        /// 1_000_000 (0.001 SOL). Tip is fixed-cost, not slippage-based.
        #[serde(default)]
        jito_tip_lamports: Option<u64>,
    },
    BuildSellTx {
        user_id: String,
        mint: String,
        /// base58 payer pubkey (signs and pays — must own the token ATA)
        payer: String,
        /// base58 creator from the original position. Required for the
        /// creator_vault PDA. Python pulls this from the position row, not
        /// the curve, because post-graduation the curve is gone.
        creator: String,
        /// Raw token units to sell (NOT whole tokens — pump.fun mints have
        /// 6 decimals, so 1 whole token = 1_000_000 raw units).
        token_amount: u64,
        /// Slippage floor. Tx will fail on-chain if SOL out < this.
        /// Python computes from `expected_sol_out × (1 - slippage_bps/10000)`.
        min_sol_output_lamports: u64,
        /// Whether this mint is a cashback coin (changes pump.fun sell
        /// account layout — adds user_volume_accumulator). Python pulls
        /// this from the saved position state.
        #[serde(default)]
        is_cashback_coin: bool,
        recent_blockhash: String,
        #[serde(default)]
        priority_fee_microlamports: Option<u64>,
        #[serde(default)]
        compute_units: Option<u32>,
    },
    SubmitBundle {
        user_id: String,
        /// base64-encoded SIGNED transaction (Python has already replaced
        /// the placeholder signature with a real one). We verify the primary
        /// signature is non-default before accepting.
        signed_tx_b64: String,
        /// Jito regions to fan out to. Each region is a separate POST. Default
        /// = all 5 mainnet regions.
        #[serde(default = "default_jito_regions")]
        regions: Vec<String>,
        /// Master live flag from Python. Even with live=true, the env var
        /// TG_TRADER_LIVE must equal "1" or we refuse to send.
        #[serde(default)]
        live: bool,
    },
}

fn default_jito_regions() -> Vec<String> {
    vec![
        "ny".to_string(),
        "amsterdam".to_string(),
        "frankfurt".to_string(),
        "tokyo".to_string(),
        "slc".to_string(),
    ]
}

#[derive(Serialize)]
struct Response {
    ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    data: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<String>,
}

impl Response {
    fn ok(data: Value) -> Self { Self { ok: true,  data: Some(data), error: None } }
    fn err<E: ToString>(e: E) -> Self { Self { ok: false, data: None, error: Some(e.to_string()) } }
}

// ─── Shared validation ───────────────────────────────────────────────────

const MAX_SOL_PER_TRADE: f64 = 50.0;
const DEFAULT_SLIPPAGE_BPS: u64 = 500;
const DEFAULT_COMPUTE_UNITS: u32 = 200_000;
const DEFAULT_PRIORITY_FEE_MICROLAMPORTS: u64 = 100_000;

fn validate_user_id(user_id: &str) -> Result<()> {
    if user_id.trim().is_empty() {
        return Err(anyhow!("user_id is required"));
    }
    Ok(())
}

fn validate_mint(mint: &str) -> Result<&str> {
    let mint = mint.trim();
    if mint.len() < 32 || mint.len() > 44 {
        return Err(anyhow!("mint looks malformed (len {}, expected 32–44)", mint.len()));
    }
    if !mint.chars().all(|c| {
        matches!(c,
            '1'..='9' | 'A'..='H' | 'J'..='N' | 'P'..='Z' | 'a'..='k' | 'm'..='z')
    }) {
        return Err(anyhow!("mint contains non-base58 characters"));
    }
    Ok(mint)
}

fn validate_sol(sol: f64) -> Result<()> {
    if !sol.is_finite() || sol <= 0.0 {
        return Err(anyhow!("sol must be a positive number"));
    }
    if sol > MAX_SOL_PER_TRADE {
        return Err(anyhow!("sol exceeds skeleton safety bound (max {})", MAX_SOL_PER_TRADE));
    }
    Ok(())
}

// ─── Command handlers ────────────────────────────────────────────────────

fn handle_health() -> Result<Value> {
    let env_live = std::env::var("TG_TRADER_LIVE").ok();
    Ok(json!({
        "status":     "alive",
        "binary":     "tg-trader",
        "day":        4,
        "phase":      "submit-bundle",
        "commands":   ["health", "version", "dry-run-buy",
                       "build-buy-tx", "build-sell-tx", "submit-bundle"],
        "not_yet":    ["monitor", "fee-skim"],
        // Surface the live gate's state so the orchestrator can crash loud
        // if it expected live mode but the env isn't set.
        "live_enabled": env_live.as_deref() == Some("1"),
    }))
}

fn handle_version() -> Result<Value> {
    Ok(json!({
        "binary":           "tg-trader",
        "library":          env!("CARGO_PKG_NAME"),
        "library_version":  env!("CARGO_PKG_VERSION"),
        "rust_edition":     "2021",
        "available_modules": ["sniper", "seller", "monitor", "jito", "jupiter", "pump", "types"],
    }))
}

fn handle_dry_run_buy(
    user_id: &str,
    mint: &str,
    sol: f64,
    slippage_bps: Option<u64>,
) -> Result<Value> {
    validate_user_id(user_id)?;
    let mint = validate_mint(mint)?;
    validate_sol(sol)?;

    let lamports = (sol * 1_000_000_000.0) as u64;
    let slippage_bps = slippage_bps.unwrap_or(DEFAULT_SLIPPAGE_BPS);
    let max_sol_cost = (sol * (1.0 + slippage_bps as f64 / 10_000.0)) * 1_000_000_000.0;

    Ok(json!({
        "user_id":          user_id,
        "mint":             mint,
        "sol":              sol,
        "buy_lamports":     lamports,
        "slippage_bps":     slippage_bps,
        "max_sol_cost_lamports": max_sol_cost as u64,
        "phase":            "dry-run",
        "would_submit":     false,
        "note":             "Wire format validated end-to-end. No tx built. Use build-buy-tx to assemble.",
    }))
}

/// Assemble an unsigned pump.fun pre-graduation buy transaction.
///
/// Inputs come from Python (which owns RPC + custody); we just build the tx
/// using the pump.rs primitives, serialize it, and hand back base64 bytes
/// for Python to sign. No network calls, no signing, no submission.
#[allow(clippy::too_many_arguments)]
fn handle_build_buy_tx(
    user_id: &str,
    mint_str: &str,
    payer_str: &str,
    sol: f64,
    slippage_bps: Option<u64>,
    curve: &BondingCurveInput,
    recent_blockhash_str: &str,
    priority_fee_microlamports: Option<u64>,
    compute_units: Option<u32>,
    jito_tip_lamports: Option<u64>,
) -> Result<Value> {
    // ── Validation (re-uses dry-run-buy guardrails) ───────────────────
    validate_user_id(user_id)?;
    let mint_str = validate_mint(mint_str)?;
    validate_sol(sol)?;

    if curve.complete {
        // Graduation already happened — pump.fun curve is closed. The buy
        // must go through Jupiter (Raydium/PumpSwap LP). Reject loudly so
        // the Python orchestrator routes correctly.
        return Err(anyhow!(
            "bonding curve complete=true (token has graduated) — use Jupiter route, not pump.fun"
        ));
    }

    if curve.virtual_sol_reserves == 0 || curve.virtual_token_reserves == 0 {
        return Err(anyhow!(
            "bonding curve has zero reserves (virtual_sol={}, virtual_token={}) — refusing to build tx",
            curve.virtual_sol_reserves, curve.virtual_token_reserves
        ));
    }

    // ── Parse pubkeys + blockhash ─────────────────────────────────────
    let mint = Pubkey::from_str(mint_str)
        .map_err(|e| anyhow!("mint is not a valid pubkey: {}", e))?;
    let payer = Pubkey::from_str(payer_str.trim())
        .map_err(|e| anyhow!("payer is not a valid pubkey: {}", e))?;
    let creator = Pubkey::from_str(curve.creator.trim())
        .map_err(|e| anyhow!("bonding_curve.creator is not a valid pubkey: {}", e))?;
    let recent_blockhash = Hash::from_str(recent_blockhash_str.trim())
        .map_err(|e| anyhow!("recent_blockhash is not a valid hash: {}", e))?;

    // ── Curve math (matches src/sniper.rs:176) ────────────────────────
    let buy_lamports = (sol * 1_000_000_000.0) as u64;
    let slippage_bps = slippage_bps.unwrap_or(DEFAULT_SLIPPAGE_BPS);
    let max_sol_cost = ((sol * (1.0 + slippage_bps as f64 / 10_000.0)) * 1_000_000_000.0) as u64;

    // entry_price = lamports per token (NOT per whole-token — raw smallest units)
    let entry_price_lamports_per_token =
        curve.virtual_sol_reserves as f64 / curve.virtual_token_reserves as f64;
    if entry_price_lamports_per_token <= 0.0 {
        return Err(anyhow!("computed invalid entry price: {}", entry_price_lamports_per_token));
    }
    let tokens_out = (buy_lamports as f64 / entry_price_lamports_per_token) as u64;
    if tokens_out == 0 {
        return Err(anyhow!(
            "computed 0 tokens for {} lamports at price {} lamports/token",
            buy_lamports, entry_price_lamports_per_token
        ));
    }

    // ── Derive accounts + assemble instructions ───────────────────────
    let token_program = spl_token::id();
    let pump_accounts = pump::derive_accounts(&mint, &payer, &creator, &token_program);

    let cu_limit = compute_units.unwrap_or(DEFAULT_COMPUTE_UNITS);
    let cu_price = priority_fee_microlamports.unwrap_or(DEFAULT_PRIORITY_FEE_MICROLAMPORTS);

    let mut instructions: Vec<Instruction> = vec![
        ComputeBudgetInstruction::set_compute_unit_limit(cu_limit),
        ComputeBudgetInstruction::set_compute_unit_price(cu_price),
        // Idempotent: no-op if the user's ATA already exists. Cheap insurance
        // for first-time buyers of a given mint.
        create_associated_token_account_idempotent(&payer, &payer, &mint, &token_program),
        pump::build_buy_instruction(
            &payer,
            &mint,
            &pump_accounts,
            tokens_out,
            max_sol_cost,
            &token_program,
        ),
    ];
    // Jito tip — appended LAST so the tx still parses cleanly without it.
    // When > 0, this is what gets the bundle prioritized by Jito. The tip
    // account is picked at random per call (src/jito.rs:30) for load
    // distribution across Jito's 8 tip recipients.
    let tip_lamports = jito_tip_lamports.unwrap_or(0);
    if tip_lamports > 0 {
        instructions.push(jito::build_tip_instruction(&payer, tip_lamports));
    }

    // ── Build legacy Transaction (single signer = payer) ──────────────
    let message = Message::new_with_blockhash(&instructions, Some(&payer), &recent_blockhash);
    let num_sigs = message.header.num_required_signatures as usize;
    // Placeholder signatures — Python will replace these with real ones.
    let tx = Transaction {
        signatures: vec![Signature::default(); num_sigs],
        message,
    };

    let tx_bytes = bincode::serialize(&tx)
        .map_err(|e| anyhow!("failed to serialize transaction: {}", e))?;
    let tx_b64 = base64::engine::general_purpose::STANDARD.encode(&tx_bytes);

    // Round entry price for the response (lamports/token can be very small)
    let entry_price_sol_per_token = entry_price_lamports_per_token / 1e9;
    let entry_mcap_sol = entry_price_lamports_per_token
        * curve.token_total_supply as f64
        / 1e9;

    Ok(json!({
        "route":                            "pumpfun-pregrad",
        "user_id":                          user_id,
        "mint":                             mint_str,
        "payer":                            payer.to_string(),
        "creator":                          creator.to_string(),
        "tx_b64":                           tx_b64,
        "tx_bytes":                         tx_bytes.len(),
        "num_required_signatures":          num_sigs,
        "recent_blockhash":                 recent_blockhash.to_string(),
        // Trade economics
        "sol":                              sol,
        "buy_lamports":                     buy_lamports,
        "slippage_bps":                     slippage_bps,
        "max_sol_cost_lamports":            max_sol_cost,
        "expected_tokens_out":              tokens_out,
        "entry_price_lamports_per_token":   entry_price_lamports_per_token,
        "entry_price_sol_per_token":        entry_price_sol_per_token,
        "entry_mcap_sol":                   entry_mcap_sol,
        // Compute budget + Jito tip
        "compute_units":                    cu_limit,
        "priority_fee_microlamports":       cu_price,
        "jito_tip_lamports":                tip_lamports,
        // Account list (for Python's audit trail + receipt commitment)
        "accounts": {
            "bonding_curve":             pump_accounts.bonding_curve.to_string(),
            "associated_bonding_curve":  pump_accounts.associated_bonding_curve.to_string(),
            "user_ata":                  pump_accounts.user_ata.to_string(),
            "creator_vault":             pump_accounts.creator_vault.to_string(),
            "event_authority":           pump_accounts.event_authority.to_string(),
            "global_volume_accumulator": pump_accounts.global_volume_accumulator.to_string(),
            "user_volume_accumulator":   pump_accounts.user_volume_accumulator.to_string(),
            "fee_config":                pump_accounts.fee_config.to_string(),
            "bonding_curve_v2":          pump_accounts.bonding_curve_v2.to_string(),
            "token_program":             token_program.to_string(),
        },
        "is_cashback_coin": curve.is_cashback_coin,
        "phase":            "build-only",
        "would_submit":     false,
        "note":             "Unsigned tx. Python must sign before submission. Use submit-bundle (Day 4.4) to send to Jito.",
    }))
}

/// Assemble an unsigned pump.fun pre-graduation SELL transaction.
///
/// Sells need fewer inputs than buys: no bonding curve state, no ATA
/// creation (the wallet already has the token account from the buy).
/// We just need: token_amount (raw units), min_sol_output (slippage floor),
/// creator (for the vault PDA), and is_cashback flag (for account layout).
///
/// Python computes `min_sol_output_lamports` BEFORE calling us using the
/// curve's compute_sell_output() formula × (1 - slippage_bps/10000). We
/// don't compute it here so the build path stays pure and inspectable.
#[allow(clippy::too_many_arguments)]
fn handle_build_sell_tx(
    user_id: &str,
    mint_str: &str,
    payer_str: &str,
    creator_str: &str,
    token_amount: u64,
    min_sol_output_lamports: u64,
    is_cashback_coin: bool,
    recent_blockhash_str: &str,
    priority_fee_microlamports: Option<u64>,
    compute_units: Option<u32>,
) -> Result<Value> {
    // ── Validation ────────────────────────────────────────────────────
    validate_user_id(user_id)?;
    let mint_str = validate_mint(mint_str)?;

    if token_amount == 0 {
        return Err(anyhow!("token_amount must be > 0"));
    }

    // ── Parse pubkeys + blockhash ─────────────────────────────────────
    let mint = Pubkey::from_str(mint_str)
        .map_err(|e| anyhow!("mint is not a valid pubkey: {}", e))?;
    let payer = Pubkey::from_str(payer_str.trim())
        .map_err(|e| anyhow!("payer is not a valid pubkey: {}", e))?;
    let creator = Pubkey::from_str(creator_str.trim())
        .map_err(|e| anyhow!("creator is not a valid pubkey: {}", e))?;
    let recent_blockhash = Hash::from_str(recent_blockhash_str.trim())
        .map_err(|e| anyhow!("recent_blockhash is not a valid hash: {}", e))?;

    // ── Derive accounts + assemble instructions ───────────────────────
    let token_program = spl_token::id();
    let pump_accounts = pump::derive_accounts(&mint, &payer, &creator, &token_program);

    let cu_limit = compute_units.unwrap_or(DEFAULT_COMPUTE_UNITS);
    let cu_price = priority_fee_microlamports.unwrap_or(DEFAULT_PRIORITY_FEE_MICROLAMPORTS);

    let instructions: Vec<Instruction> = vec![
        ComputeBudgetInstruction::set_compute_unit_limit(cu_limit),
        ComputeBudgetInstruction::set_compute_unit_price(cu_price),
        pump::build_sell_instruction(
            &payer,
            &mint,
            &pump_accounts,
            token_amount,
            min_sol_output_lamports,
            &token_program,
            is_cashback_coin,
        ),
    ];

    // ── Build legacy Transaction (single signer = payer) ──────────────
    let message = Message::new_with_blockhash(&instructions, Some(&payer), &recent_blockhash);
    let num_sigs = message.header.num_required_signatures as usize;
    let tx = Transaction {
        signatures: vec![Signature::default(); num_sigs],
        message,
    };

    let tx_bytes = bincode::serialize(&tx)
        .map_err(|e| anyhow!("failed to serialize transaction: {}", e))?;
    let tx_b64 = base64::engine::general_purpose::STANDARD.encode(&tx_bytes);

    Ok(json!({
        "route":                            "pumpfun-pregrad-sell",
        "user_id":                          user_id,
        "mint":                             mint_str,
        "payer":                            payer.to_string(),
        "creator":                          creator.to_string(),
        "tx_b64":                           tx_b64,
        "tx_bytes":                         tx_bytes.len(),
        "num_required_signatures":          num_sigs,
        "recent_blockhash":                 recent_blockhash.to_string(),
        // Trade economics
        "token_amount":                     token_amount,
        "min_sol_output_lamports":          min_sol_output_lamports,
        "is_cashback_coin":                 is_cashback_coin,
        // Compute budget
        "compute_units":                    cu_limit,
        "priority_fee_microlamports":       cu_price,
        // Account list (audit trail)
        "accounts": {
            "bonding_curve":             pump_accounts.bonding_curve.to_string(),
            "associated_bonding_curve":  pump_accounts.associated_bonding_curve.to_string(),
            "user_ata":                  pump_accounts.user_ata.to_string(),
            "creator_vault":             pump_accounts.creator_vault.to_string(),
            "event_authority":           pump_accounts.event_authority.to_string(),
            "user_volume_accumulator":   pump_accounts.user_volume_accumulator.to_string(),
            "fee_config":                pump_accounts.fee_config.to_string(),
            "bonding_curve_v2":          pump_accounts.bonding_curve_v2.to_string(),
            "token_program":             token_program.to_string(),
        },
        "phase":            "build-only",
        "would_submit":     false,
        "note":             "Unsigned sell tx. Python signs and submits via submit-bundle. ATA close (if 100% sell) is handled separately.",
    }))
}

// ─── submit-bundle ────────────────────────────────────────────────────────
//
// Splits cleanly into three pieces so tests can verify the safety logic
// WITHOUT touching the network:
//   1. `validate_submit_input`  — parse signed tx, check signature is real,
//                                 verify regions list is non-empty.
//   2. `should_submit_live`     — decide dry-run vs live (input flag AND env
//                                 var). Pure function, fully testable.
//   3. `submit_bundle_to_regions` — the actual HTTP fan-out. Only called
//                                   when the gate above says yes.

#[derive(Debug)]
struct SubmitValidated {
    tx_bytes: Vec<u8>,
    primary_signature: Signature,
    n_signatures: usize,
}

fn validate_submit_input(
    user_id: &str,
    signed_tx_b64: &str,
    regions: &[String],
) -> Result<SubmitValidated> {
    validate_user_id(user_id)?;
    if regions.is_empty() {
        return Err(anyhow!("regions list is empty — no Jito region to submit to"));
    }
    let tx_bytes = base64::engine::general_purpose::STANDARD
        .decode(signed_tx_b64.trim())
        .map_err(|e| anyhow!("signed_tx_b64 is not valid base64: {}", e))?;
    if tx_bytes.len() < 64 {
        return Err(anyhow!("decoded tx is too small ({} bytes) — not a real signed tx", tx_bytes.len()));
    }
    let tx: Transaction = bincode::deserialize(&tx_bytes)
        .map_err(|e| anyhow!("decoded bytes are not a valid Transaction: {}", e))?;
    if tx.signatures.is_empty() {
        return Err(anyhow!("transaction has zero signatures — Python must sign before submitting"));
    }
    let primary_signature = tx.signatures[0];
    if primary_signature == Signature::default() {
        return Err(anyhow!(
            "primary signature is the default placeholder — Python did not sign the tx returned by build-buy-tx / build-sell-tx"
        ));
    }
    Ok(SubmitValidated {
        tx_bytes,
        primary_signature,
        n_signatures: tx.signatures.len(),
    })
}

/// Decision: actually call Jito, or stay dry-run?
/// live=true AND env var TG_TRADER_LIVE == "1" → submit.
/// Anything else → dry-run.
///
/// Pure function so the test suite can drive every branch without env
/// manipulation between threads (which is racy in Rust tests).
fn should_submit_live(input_live: bool, env_live_value: Option<&str>) -> bool {
    input_live && env_live_value == Some("1")
}

async fn submit_bundle_to_regions(
    tx_b64: &str,
    regions: &[String],
) -> Vec<Value> {
    use reqwest::Client;
    use std::time::Duration;

    let client = Client::builder()
        .timeout(Duration::from_secs(5))
        .build()
        .expect("reqwest client should build");
    let bundle = vec![tx_b64.to_string()];

    let mut futures = Vec::with_capacity(regions.len());
    for region in regions {
        let client = client.clone();
        let bundle = bundle.clone();
        let region = region.clone();
        futures.push(tokio::spawn(async move {
            let url = format!(
                "https://{}.mainnet.block-engine.jito.wtf/api/v1/bundles",
                region
            );
            let resp = client
                .post(&url)
                .json(&json!({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "sendBundle",
                    // Second positional param tells Jito the encoding. Default
                    // would be base58 → bincode/base64 txs fail to decode with
                    // "transaction #0 could not be decoded". Must match the
                    // base64 encoding used in handle_build_buy_tx.
                    "params": [&bundle, {"encoding": "base64"}],
                }))
                .send()
                .await;

            match resp {
                Ok(r) => {
                    let status = r.status().as_u16();
                    let ok = r.status().is_success();
                    let body = r.text().await.unwrap_or_default();
                    json!({
                        "region": region,
                        "ok":     ok,
                        "status": status,
                        "body":   body.chars().take(500).collect::<String>(),
                    })
                }
                Err(e) => json!({
                    "region": region,
                    "ok":     false,
                    "error":  e.to_string(),
                }),
            }
        }));
    }

    let mut results = Vec::with_capacity(futures.len());
    for f in futures {
        match f.await {
            Ok(v)  => results.push(v),
            Err(e) => results.push(json!({"region": "?", "ok": false, "error": e.to_string()})),
        }
    }
    results
}

fn handle_submit_bundle(
    user_id: &str,
    signed_tx_b64: &str,
    regions: &[String],
    live: bool,
) -> Result<Value> {
    let validated = validate_submit_input(user_id, signed_tx_b64, regions)?;

    let env_live = std::env::var("TG_TRADER_LIVE").ok();
    let will_submit = should_submit_live(live, env_live.as_deref());

    if live && !will_submit {
        // The caller explicitly asked for live but the safety env var is
        // missing. Refuse — do NOT silently fall back to dry-run. The whole
        // point of the env var is that an operator must flip it.
        return Err(anyhow!(
            "submit-bundle called with live=true but TG_TRADER_LIVE env var != \"1\" — refusing to submit. \
             Set TG_TRADER_LIVE=1 to enable real submission, or pass live=false for dry-run."
        ));
    }

    if !will_submit {
        return Ok(json!({
            "phase":          "dry-run",
            "would_submit":   false,
            "user_id":        user_id,
            "signature":      validated.primary_signature.to_string(),
            "n_signatures":   validated.n_signatures,
            "tx_bytes":       validated.tx_bytes.len(),
            "regions":        regions,
            "n_regions":      regions.len(),
            "note":           "live=false — validation passed, no Jito submission",
        }));
    }

    // Real submission — block on a one-shot runtime
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .map_err(|e| anyhow!("failed to create tokio runtime: {}", e))?;
    let results = runtime.block_on(submit_bundle_to_regions(signed_tx_b64, regions));

    let n_accepted = results.iter()
        .filter(|r| r.get("ok").and_then(|v| v.as_bool()).unwrap_or(false))
        .count();

    Ok(json!({
        "phase":          "submitted",
        "would_submit":   true,
        "user_id":        user_id,
        "signature":      validated.primary_signature.to_string(),
        "n_signatures":   validated.n_signatures,
        "tx_bytes":       validated.tx_bytes.len(),
        "regions":        results,
        "n_regions":      regions.len(),
        "n_accepted":     n_accepted,
    }))
}

// ─── Entry point ─────────────────────────────────────────────────────────

fn main() -> Result<()> {
    let stdin  = io::stdin();
    let stdout = io::stdout();
    let mut out = stdout.lock();

    for line in stdin.lock().lines() {
        let line = match line {
            Ok(l) if !l.trim().is_empty() => l,
            Ok(_) => continue,
            Err(e) => {
                let resp = Response::err(format!("stdin read failed: {}", e));
                writeln!(out, "{}", serde_json::to_string(&resp)?)?;
                out.flush()?;
                continue;
            }
        };

        let resp = match serde_json::from_str::<Command>(&line) {
            Err(e) => Response::err(format!("malformed JSON command: {}", e)),
            Ok(Command::Health) => match handle_health() {
                Ok(v) => Response::ok(v), Err(e) => Response::err(e),
            },
            Ok(Command::Version) => match handle_version() {
                Ok(v) => Response::ok(v), Err(e) => Response::err(e),
            },
            Ok(Command::DryRunBuy { user_id, mint, sol, slippage_bps }) => {
                match handle_dry_run_buy(&user_id, &mint, sol, slippage_bps) {
                    Ok(v) => Response::ok(v), Err(e) => Response::err(e),
                }
            }
            Ok(Command::BuildBuyTx {
                user_id, mint, payer, sol, slippage_bps,
                bonding_curve, recent_blockhash,
                priority_fee_microlamports, compute_units, jito_tip_lamports,
            }) => {
                match handle_build_buy_tx(
                    &user_id, &mint, &payer, sol, slippage_bps,
                    &bonding_curve, &recent_blockhash,
                    priority_fee_microlamports, compute_units, jito_tip_lamports,
                ) {
                    Ok(v) => Response::ok(v), Err(e) => Response::err(e),
                }
            }
            Ok(Command::BuildSellTx {
                user_id, mint, payer, creator,
                token_amount, min_sol_output_lamports, is_cashback_coin,
                recent_blockhash, priority_fee_microlamports, compute_units,
            }) => {
                match handle_build_sell_tx(
                    &user_id, &mint, &payer, &creator,
                    token_amount, min_sol_output_lamports, is_cashback_coin,
                    &recent_blockhash,
                    priority_fee_microlamports, compute_units,
                ) {
                    Ok(v) => Response::ok(v), Err(e) => Response::err(e),
                }
            }
            Ok(Command::SubmitBundle { user_id, signed_tx_b64, regions, live }) => {
                match handle_submit_bundle(&user_id, &signed_tx_b64, &regions, live) {
                    Ok(v) => Response::ok(v), Err(e) => Response::err(e),
                }
            }
        };

        writeln!(out, "{}", serde_json::to_string(&resp)?)?;
        out.flush()?;
    }

    Ok(())
}


// ─── Tests ────────────────────────────────────────────────────────────────
//
// All handlers are pure functions returning Result<Value>, so we can test
// them directly without spawning processes. The tests below cover the
// validation logic, curve math, account derivation, and serialized tx
// envelope that the Python orchestrator depends on.

#[cfg(test)]
mod tests {
    use super::*;

    // Valid pump.fun-style mint we use across tests
    const VALID_MINT: &str = "7BDgvCTSCux7hCY8LE3njhx3VvpwRD52EwMmCzBXpump";
    // System program is a known-valid base58 pubkey — fine for tests as a
    // stand-in for payer/creator since we never sign or submit.
    const VALID_PAYER: &str = "11111111111111111111111111111112";
    const VALID_CREATOR: &str = "11111111111111111111111111111113";
    // 32-byte all-zero hash is a valid base58 hash (just won't be accepted on-chain).
    const VALID_BLOCKHASH: &str = "11111111111111111111111111111111";

    fn fresh_curve() -> BondingCurveInput {
        // Numbers approximate a pump.fun curve right at creation:
        //   ~30 SOL virtual / 1.073B virtual tokens
        //   total supply 1B (smallest units: 1e15 with 6 decimals)
        BondingCurveInput {
            virtual_sol_reserves:   30_000_000_000,           // 30 SOL
            virtual_token_reserves: 1_073_000_000_000_000,    // 1.073B with 6 decimals
            real_sol_reserves:      0,
            real_token_reserves:    793_100_000_000_000,
            token_total_supply:     1_000_000_000_000_000,
            complete:               false,
            creator:                VALID_CREATOR.to_string(),
            is_cashback_coin:       false,
        }
    }

    fn graduated_curve() -> BondingCurveInput {
        let mut c = fresh_curve();
        c.complete = true;
        c
    }

    // ── health ──────────────────────────────────────────────────────────
    #[test]
    fn health_reports_submit_bundle_phase() {
        let v = handle_health().unwrap();
        assert_eq!(v["status"], "alive");
        assert_eq!(v["phase"], "submit-bundle");
        assert_eq!(v["binary"], "tg-trader");
        let cmds = v["commands"].as_array().unwrap();
        assert!(cmds.iter().any(|c| c == "build-buy-tx"));
        assert!(cmds.iter().any(|c| c == "build-sell-tx"));
        assert!(cmds.iter().any(|c| c == "submit-bundle"));
        // live_enabled MUST be a bool so the orchestrator can branch on it
        assert!(v["live_enabled"].is_boolean());
    }

    #[test]
    fn health_advertises_remaining_not_yet_implemented() {
        let v = handle_health().unwrap();
        let not_yet = v["not_yet"].as_array().unwrap();
        for needed in &["monitor", "fee-skim"] {
            assert!(not_yet.iter().any(|c| c == needed),
                "health should list {} as not-yet-implemented", needed);
        }
        // submit-bundle has shipped — must not be in not_yet anymore
        assert!(!not_yet.iter().any(|c| c == "submit-bundle"),
            "submit-bundle should no longer be in not_yet");
    }

    // ── version ─────────────────────────────────────────────────────────
    #[test]
    fn version_exposes_library_modules() {
        let v = handle_version().unwrap();
        let modules = v["available_modules"].as_array().unwrap();
        for needed in &["sniper", "seller", "monitor", "jito", "jupiter", "pump", "types"] {
            assert!(modules.iter().any(|c| c == needed),
                "version should list {} as available", needed);
        }
    }

    // ── dry-run-buy: happy path ─────────────────────────────────────────
    #[test]
    fn dry_run_buy_computes_lamports_correctly() {
        let v = handle_dry_run_buy("42", VALID_MINT, 0.5, None).unwrap();
        assert_eq!(v["buy_lamports"].as_u64().unwrap(), 500_000_000);
        assert_eq!(v["sol"].as_f64().unwrap(), 0.5);
        assert_eq!(v["user_id"], "42");
        assert_eq!(v["would_submit"], false);
        assert_eq!(v["phase"], "dry-run");
    }

    #[test]
    fn dry_run_buy_default_slippage_is_500_bps() {
        let v = handle_dry_run_buy("42", VALID_MINT, 1.0, None).unwrap();
        assert_eq!(v["slippage_bps"].as_u64().unwrap(), 500);
        assert_eq!(v["max_sol_cost_lamports"].as_u64().unwrap(), 1_050_000_000);
    }

    #[test]
    fn dry_run_buy_respects_explicit_slippage() {
        let v = handle_dry_run_buy("42", VALID_MINT, 1.0, Some(200)).unwrap();
        assert_eq!(v["slippage_bps"].as_u64().unwrap(), 200);
        assert_eq!(v["max_sol_cost_lamports"].as_u64().unwrap(), 1_020_000_000);
    }

    // ── dry-run-buy: rejections (regression coverage from Day 3) ────────
    #[test]
    fn dry_run_buy_rejects_empty_user_id() {
        let err = handle_dry_run_buy("", VALID_MINT, 0.5, None).unwrap_err();
        assert!(err.to_string().contains("user_id"));
    }

    #[test]
    fn dry_run_buy_rejects_short_mint() {
        let err = handle_dry_run_buy("42", "tooshort", 0.5, None).unwrap_err();
        assert!(err.to_string().contains("malformed"));
    }

    #[test]
    fn dry_run_buy_rejects_long_mint() {
        let long_mint = "x".repeat(50);
        let err = handle_dry_run_buy("42", &long_mint, 0.5, None).unwrap_err();
        assert!(err.to_string().contains("malformed"));
    }

    #[test]
    fn dry_run_buy_rejects_non_base58_mint() {
        let bad = "0".repeat(40);
        let err = handle_dry_run_buy("42", &bad, 0.5, None).unwrap_err();
        assert!(err.to_string().contains("non-base58"));
    }

    #[test]
    fn dry_run_buy_rejects_zero_sol() {
        let err = handle_dry_run_buy("42", VALID_MINT, 0.0, None).unwrap_err();
        assert!(err.to_string().contains("positive"));
    }

    #[test]
    fn dry_run_buy_rejects_negative_sol() {
        let err = handle_dry_run_buy("42", VALID_MINT, -1.0, None).unwrap_err();
        assert!(err.to_string().contains("positive"));
    }

    #[test]
    fn dry_run_buy_rejects_nan_sol() {
        let err = handle_dry_run_buy("42", VALID_MINT, f64::NAN, None).unwrap_err();
        assert!(err.to_string().contains("positive"));
    }

    #[test]
    fn dry_run_buy_rejects_above_safety_bound() {
        let err = handle_dry_run_buy("42", VALID_MINT, 51.0, None).unwrap_err();
        assert!(err.to_string().contains("safety bound"));
    }

    #[test]
    fn dry_run_buy_accepts_exactly_at_bound() {
        let v = handle_dry_run_buy("42", VALID_MINT, 50.0, None).unwrap();
        assert_eq!(v["buy_lamports"].as_u64().unwrap(), 50_000_000_000);
    }

    // ── build-buy-tx: happy path ────────────────────────────────────────
    #[test]
    fn build_buy_tx_returns_envelope_with_unsigned_tx() {
        let curve = fresh_curve();
        let v = handle_build_buy_tx(
            "42", VALID_MINT, VALID_PAYER, 0.5, None,
            &curve, VALID_BLOCKHASH, None, None, None,
        ).unwrap();

        assert_eq!(v["route"], "pumpfun-pregrad");
        assert_eq!(v["user_id"], "42");
        assert_eq!(v["mint"], VALID_MINT);
        assert_eq!(v["buy_lamports"].as_u64().unwrap(), 500_000_000);
        assert_eq!(v["slippage_bps"].as_u64().unwrap(), DEFAULT_SLIPPAGE_BPS);
        assert_eq!(v["would_submit"], false);
        assert_eq!(v["phase"], "build-only");

        // Envelope MUST carry an unsigned tx
        let tx_b64 = v["tx_b64"].as_str().unwrap();
        assert!(!tx_b64.is_empty(), "tx_b64 must be present");
        let tx_bytes = base64::engine::general_purpose::STANDARD.decode(tx_b64).unwrap();
        assert!(tx_bytes.len() > 100, "tx should be non-trivial size");

        // num_required_signatures must be 1 (payer-only)
        assert_eq!(v["num_required_signatures"].as_u64().unwrap(), 1);
    }

    #[test]
    fn build_buy_tx_serializes_into_valid_legacy_transaction() {
        // Deserialize the returned bytes back into a Transaction and check
        // it's structurally sound. This locks in the wire format so the
        // Python signer doesn't fail to parse it.
        let curve = fresh_curve();
        let v = handle_build_buy_tx(
            "42", VALID_MINT, VALID_PAYER, 0.5, None,
            &curve, VALID_BLOCKHASH, None, None, None,
        ).unwrap();
        let tx_b64 = v["tx_b64"].as_str().unwrap();
        let tx_bytes = base64::engine::general_purpose::STANDARD.decode(tx_b64).unwrap();
        let tx: Transaction = bincode::deserialize(&tx_bytes)
            .expect("returned bytes must deserialize as legacy Transaction");

        // Signature placeholder
        assert_eq!(tx.signatures.len(), 1, "should have one signature slot");
        assert_eq!(tx.signatures[0], Signature::default(),
            "signature slot must be default (Python signs)");

        // Blockhash must round-trip
        assert_eq!(
            tx.message.recent_blockhash.to_string(),
            VALID_BLOCKHASH
        );

        // Payer must be the first account
        let payer = Pubkey::from_str(VALID_PAYER).unwrap();
        assert_eq!(tx.message.account_keys[0], payer,
            "payer must be account_keys[0]");

        // Instructions: compute_unit_limit + compute_unit_price + create_ata + buy = 4
        assert_eq!(tx.message.instructions.len(), 4,
            "expected 4 instructions (CU limit, CU price, create ATA, buy)");
    }

    #[test]
    fn build_buy_tx_expected_tokens_matches_curve_math() {
        // At 30 SOL virtual / 1.073e15 raw-unit virtual reserves (6-decimal token):
        //   price = 30e9 lamports / 1.073e15 raw_tokens = 2.796e-5 lamports/raw_token
        //   0.5 SOL = 5e8 lamports → 5e8 / 2.796e-5 ≈ 1.788e13 raw_tokens
        //   That's ~17.88 MILLION whole tokens (raw / 1e6).
        let curve = fresh_curve();
        let v = handle_build_buy_tx(
            "42", VALID_MINT, VALID_PAYER, 0.5, None,
            &curve, VALID_BLOCKHASH, None, None, None,
        ).unwrap();
        let tokens = v["expected_tokens_out"].as_u64().unwrap();
        assert!(tokens > 15_000_000_000_000, "tokens_out {} too small (expected ~17.88T raw)", tokens);
        assert!(tokens < 20_000_000_000_000, "tokens_out {} too large (expected ~17.88T raw)", tokens);

        // Sanity: in whole tokens, 0.5 SOL should buy roughly 17M tokens
        let whole_tokens = tokens / 1_000_000;
        assert!(whole_tokens > 15_000_000 && whole_tokens < 20_000_000,
            "{} whole tokens for 0.5 SOL is out of expected range", whole_tokens);
    }

    #[test]
    fn build_buy_tx_max_sol_cost_applies_slippage() {
        let curve = fresh_curve();
        let v = handle_build_buy_tx(
            "42", VALID_MINT, VALID_PAYER, 1.0, Some(300),
            &curve, VALID_BLOCKHASH, None, None, None,
        ).unwrap();
        // 1.0 SOL * 1.03 = 1.03 SOL
        assert_eq!(v["max_sol_cost_lamports"].as_u64().unwrap(), 1_030_000_000);
        assert_eq!(v["slippage_bps"].as_u64().unwrap(), 300);
    }

    #[test]
    fn build_buy_tx_uses_default_compute_budget_when_omitted() {
        let curve = fresh_curve();
        let v = handle_build_buy_tx(
            "42", VALID_MINT, VALID_PAYER, 0.5, None,
            &curve, VALID_BLOCKHASH, None, None, None,
        ).unwrap();
        assert_eq!(v["compute_units"].as_u64().unwrap(), DEFAULT_COMPUTE_UNITS as u64);
        assert_eq!(
            v["priority_fee_microlamports"].as_u64().unwrap(),
            DEFAULT_PRIORITY_FEE_MICROLAMPORTS,
        );
    }

    #[test]
    fn build_buy_tx_honors_custom_compute_budget() {
        let curve = fresh_curve();
        let v = handle_build_buy_tx(
            "42", VALID_MINT, VALID_PAYER, 0.5, None,
            &curve, VALID_BLOCKHASH,
            Some(500_000), Some(400_000), None,
        ).unwrap();
        assert_eq!(v["priority_fee_microlamports"].as_u64().unwrap(), 500_000);
        assert_eq!(v["compute_units"].as_u64().unwrap(), 400_000);
    }

    #[test]
    fn build_buy_tx_appends_jito_tip_instruction_when_set() {
        // With a Jito tip, the tx should have ONE more instruction than
        // without (the tip transfer). The envelope must also report the
        // tip lamports back so Python's audit trail captures the cost.
        let curve = fresh_curve();
        let with_tip = handle_build_buy_tx(
            "42", VALID_MINT, VALID_PAYER, 0.5, None,
            &curve, VALID_BLOCKHASH, None, None, Some(50_000),
        ).unwrap();
        let no_tip = handle_build_buy_tx(
            "42", VALID_MINT, VALID_PAYER, 0.5, None,
            &curve, VALID_BLOCKHASH, None, None, None,
        ).unwrap();

        // Envelope correctness
        assert_eq!(with_tip["jito_tip_lamports"].as_u64().unwrap(), 50_000);
        assert_eq!(no_tip["jito_tip_lamports"].as_u64().unwrap(), 0);

        // Deserialize both and count instructions
        let decode = |v: &Value| -> Transaction {
            let bytes = base64::engine::general_purpose::STANDARD
                .decode(v["tx_b64"].as_str().unwrap()).unwrap();
            bincode::deserialize(&bytes).unwrap()
        };
        let tipped = decode(&with_tip);
        let plain  = decode(&no_tip);
        assert_eq!(
            tipped.message.instructions.len(),
            plain.message.instructions.len() + 1,
            "tipped tx must have exactly 1 more instruction (the tip transfer)",
        );
    }

    #[test]
    fn build_buy_tx_zero_tip_is_same_as_no_tip() {
        // jito_tip_lamports = Some(0) MUST behave like None — don't emit
        // a useless zero-lamport transfer ix.
        let curve = fresh_curve();
        let zero = handle_build_buy_tx(
            "42", VALID_MINT, VALID_PAYER, 0.5, None,
            &curve, VALID_BLOCKHASH, None, None, Some(0),
        ).unwrap();
        let none = handle_build_buy_tx(
            "42", VALID_MINT, VALID_PAYER, 0.5, None,
            &curve, VALID_BLOCKHASH, None, None, None,
        ).unwrap();
        assert_eq!(zero["jito_tip_lamports"].as_u64().unwrap(),
                   none["jito_tip_lamports"].as_u64().unwrap());
        assert_eq!(zero["tx_bytes"].as_u64().unwrap(),
                   none["tx_bytes"].as_u64().unwrap());
    }

    #[test]
    fn build_buy_tx_exposes_derived_accounts() {
        let curve = fresh_curve();
        let v = handle_build_buy_tx(
            "42", VALID_MINT, VALID_PAYER, 0.5, None,
            &curve, VALID_BLOCKHASH, None, None, None,
        ).unwrap();
        let accounts = v["accounts"].as_object().unwrap();
        for k in &[
            "bonding_curve", "associated_bonding_curve", "user_ata",
            "creator_vault", "event_authority", "global_volume_accumulator",
            "user_volume_accumulator", "fee_config", "bonding_curve_v2",
            "token_program",
        ] {
            assert!(accounts.contains_key(*k), "accounts missing field {}", k);
            let v = accounts[*k].as_str().unwrap();
            // All values must be parseable as pubkeys
            Pubkey::from_str(v).unwrap_or_else(|_| panic!("account {} = {} is not a valid pubkey", k, v));
        }
    }

    #[test]
    fn build_buy_tx_derived_bonding_curve_matches_library() {
        // The PDA we expose MUST match what pump::derive_bonding_curve returns
        // directly. Otherwise Python's audit trail diverges from on-chain truth.
        let curve = fresh_curve();
        let v = handle_build_buy_tx(
            "42", VALID_MINT, VALID_PAYER, 0.5, None,
            &curve, VALID_BLOCKHASH, None, None, None,
        ).unwrap();
        let mint = Pubkey::from_str(VALID_MINT).unwrap();
        let expected_bc = pump::derive_bonding_curve(&mint);
        let returned_bc = v["accounts"]["bonding_curve"].as_str().unwrap();
        assert_eq!(returned_bc, expected_bc.to_string());
    }

    // ── build-buy-tx: rejections ────────────────────────────────────────
    #[test]
    fn build_buy_tx_rejects_graduated_curve() {
        let curve = graduated_curve();
        let err = handle_build_buy_tx(
            "42", VALID_MINT, VALID_PAYER, 0.5, None,
            &curve, VALID_BLOCKHASH, None, None, None,
        ).unwrap_err();
        let msg = err.to_string();
        assert!(msg.contains("graduated") || msg.contains("Jupiter"),
            "expected message to mention graduation/Jupiter, got: {}", msg);
    }

    #[test]
    fn build_buy_tx_rejects_zero_reserves() {
        let mut curve = fresh_curve();
        curve.virtual_sol_reserves = 0;
        let err = handle_build_buy_tx(
            "42", VALID_MINT, VALID_PAYER, 0.5, None,
            &curve, VALID_BLOCKHASH, None, None, None,
        ).unwrap_err();
        assert!(err.to_string().contains("zero reserves"));
    }

    #[test]
    fn build_buy_tx_rejects_bad_payer_pubkey() {
        let curve = fresh_curve();
        let err = handle_build_buy_tx(
            "42", VALID_MINT, "not-a-pubkey", 0.5, None,
            &curve, VALID_BLOCKHASH, None, None, None,
        ).unwrap_err();
        assert!(err.to_string().contains("payer"));
    }

    #[test]
    fn build_buy_tx_rejects_bad_blockhash() {
        let curve = fresh_curve();
        let err = handle_build_buy_tx(
            "42", VALID_MINT, VALID_PAYER, 0.5, None,
            &curve, "not-a-hash", None, None, None,
        ).unwrap_err();
        assert!(err.to_string().contains("blockhash"));
    }

    #[test]
    fn build_buy_tx_rejects_bad_creator() {
        let mut curve = fresh_curve();
        curve.creator = "not-a-pubkey".to_string();
        let err = handle_build_buy_tx(
            "42", VALID_MINT, VALID_PAYER, 0.5, None,
            &curve, VALID_BLOCKHASH, None, None, None,
        ).unwrap_err();
        assert!(err.to_string().contains("creator"));
    }

    #[test]
    fn build_buy_tx_inherits_dry_run_validation() {
        // Empty user_id, bad mint, bad sol — all must be rejected here too,
        // because Python may skip dry-run-buy and go straight to build.
        let curve = fresh_curve();
        assert!(handle_build_buy_tx(
            "", VALID_MINT, VALID_PAYER, 0.5, None,
            &curve, VALID_BLOCKHASH, None, None, None,
        ).is_err());
        assert!(handle_build_buy_tx(
            "42", "tooshort", VALID_PAYER, 0.5, None,
            &curve, VALID_BLOCKHASH, None, None, None,
        ).is_err());
        assert!(handle_build_buy_tx(
            "42", VALID_MINT, VALID_PAYER, 0.0, None,
            &curve, VALID_BLOCKHASH, None, None, None,
        ).is_err());
        assert!(handle_build_buy_tx(
            "42", VALID_MINT, VALID_PAYER, 51.0, None,
            &curve, VALID_BLOCKHASH, None, None, None,
        ).is_err());
    }

    // ── build-sell-tx: happy path ───────────────────────────────────────
    fn call_sell(
        token_amount: u64,
        min_sol_output: u64,
        is_cashback: bool,
    ) -> Result<Value> {
        handle_build_sell_tx(
            "42", VALID_MINT, VALID_PAYER, VALID_CREATOR,
            token_amount, min_sol_output, is_cashback,
            VALID_BLOCKHASH, None, None,
        )
    }

    #[test]
    fn build_sell_tx_returns_envelope_with_unsigned_tx() {
        let v = call_sell(17_000_000_000_000, 450_000_000, false).unwrap();

        assert_eq!(v["route"], "pumpfun-pregrad-sell");
        assert_eq!(v["user_id"], "42");
        assert_eq!(v["mint"], VALID_MINT);
        assert_eq!(v["token_amount"].as_u64().unwrap(), 17_000_000_000_000);
        assert_eq!(v["min_sol_output_lamports"].as_u64().unwrap(), 450_000_000);
        assert_eq!(v["is_cashback_coin"], false);
        assert_eq!(v["would_submit"], false);
        assert_eq!(v["phase"], "build-only");

        let tx_b64 = v["tx_b64"].as_str().unwrap();
        assert!(!tx_b64.is_empty(), "tx_b64 must be present");
        let tx_bytes = base64::engine::general_purpose::STANDARD.decode(tx_b64).unwrap();
        assert!(tx_bytes.len() > 100, "tx should be non-trivial size");
        assert_eq!(v["num_required_signatures"].as_u64().unwrap(), 1);
    }

    #[test]
    fn build_sell_tx_serializes_into_valid_legacy_transaction() {
        let v = call_sell(17_000_000_000_000, 450_000_000, false).unwrap();
        let tx_b64 = v["tx_b64"].as_str().unwrap();
        let tx_bytes = base64::engine::general_purpose::STANDARD.decode(tx_b64).unwrap();
        let tx: Transaction = bincode::deserialize(&tx_bytes)
            .expect("returned bytes must deserialize as legacy Transaction");

        // Signature placeholder (Python will replace)
        assert_eq!(tx.signatures.len(), 1);
        assert_eq!(tx.signatures[0], Signature::default());

        // Blockhash round-trips
        assert_eq!(tx.message.recent_blockhash.to_string(), VALID_BLOCKHASH);

        // Payer is account_keys[0]
        let payer = Pubkey::from_str(VALID_PAYER).unwrap();
        assert_eq!(tx.message.account_keys[0], payer);

        // 3 instructions: CU limit + CU price + sell (NO create-ATA — wallet
        // already has the ATA from the buy that opened the position)
        assert_eq!(tx.message.instructions.len(), 3,
            "expected 3 instructions (CU limit, CU price, sell) — no ATA creation");
    }

    #[test]
    fn build_sell_tx_cashback_changes_account_layout() {
        // The pump.fun sell ix for cashback coins inserts
        // user_volume_accumulator before bonding_curve_v2. The non-cashback
        // version omits it. Both txs should still serialize cleanly.
        let cashback = call_sell(17_000_000_000_000, 450_000_000, true).unwrap();
        let normal = call_sell(17_000_000_000_000, 450_000_000, false).unwrap();

        assert_eq!(cashback["is_cashback_coin"], true);
        assert_eq!(normal["is_cashback_coin"], false);

        // Deserialize both and compare account_keys count — cashback should
        // have at least one extra account in the message (the user_volume
        // accumulator is added as a writable account).
        let decode = |v: &Value| -> Transaction {
            let bytes = base64::engine::general_purpose::STANDARD
                .decode(v["tx_b64"].as_str().unwrap()).unwrap();
            bincode::deserialize(&bytes).unwrap()
        };
        let cb_tx = decode(&cashback);
        let nm_tx = decode(&normal);
        assert!(cb_tx.message.account_keys.len() >= nm_tx.message.account_keys.len(),
            "cashback variant should not have FEWER accounts than normal");
    }

    #[test]
    fn build_sell_tx_uses_default_compute_budget_when_omitted() {
        let v = call_sell(17_000_000_000_000, 450_000_000, false).unwrap();
        assert_eq!(v["compute_units"].as_u64().unwrap(), DEFAULT_COMPUTE_UNITS as u64);
        assert_eq!(
            v["priority_fee_microlamports"].as_u64().unwrap(),
            DEFAULT_PRIORITY_FEE_MICROLAMPORTS,
        );
    }

    #[test]
    fn build_sell_tx_honors_custom_compute_budget() {
        let v = handle_build_sell_tx(
            "42", VALID_MINT, VALID_PAYER, VALID_CREATOR,
            17_000_000_000_000, 450_000_000, false,
            VALID_BLOCKHASH, Some(750_000), Some(350_000),
        ).unwrap();
        assert_eq!(v["priority_fee_microlamports"].as_u64().unwrap(), 750_000);
        assert_eq!(v["compute_units"].as_u64().unwrap(), 350_000);
    }

    #[test]
    fn build_sell_tx_accepts_zero_min_sol_output() {
        // 0 min_sol_output = "accept any non-zero output." Useful for
        // forced exits where Python knows the curve will drop fast.
        // Should NOT be rejected — slippage tolerance is the caller's call.
        let v = call_sell(17_000_000_000_000, 0, false).unwrap();
        assert_eq!(v["min_sol_output_lamports"].as_u64().unwrap(), 0);
    }

    #[test]
    fn build_sell_tx_derived_bonding_curve_matches_library() {
        let v = call_sell(17_000_000_000_000, 450_000_000, false).unwrap();
        let mint = Pubkey::from_str(VALID_MINT).unwrap();
        let expected_bc = pump::derive_bonding_curve(&mint);
        assert_eq!(
            v["accounts"]["bonding_curve"].as_str().unwrap(),
            expected_bc.to_string(),
        );
    }

    // ── build-sell-tx: rejections ───────────────────────────────────────
    #[test]
    fn build_sell_tx_rejects_zero_token_amount() {
        let err = call_sell(0, 450_000_000, false).unwrap_err();
        assert!(err.to_string().contains("token_amount"));
    }

    #[test]
    fn build_sell_tx_rejects_empty_user_id() {
        let err = handle_build_sell_tx(
            "", VALID_MINT, VALID_PAYER, VALID_CREATOR,
            17_000_000_000_000, 450_000_000, false,
            VALID_BLOCKHASH, None, None,
        ).unwrap_err();
        assert!(err.to_string().contains("user_id"));
    }

    #[test]
    fn build_sell_tx_rejects_bad_mint() {
        let err = handle_build_sell_tx(
            "42", "tooshort", VALID_PAYER, VALID_CREATOR,
            17_000_000_000_000, 450_000_000, false,
            VALID_BLOCKHASH, None, None,
        ).unwrap_err();
        assert!(err.to_string().contains("malformed"));
    }

    #[test]
    fn build_sell_tx_rejects_bad_payer() {
        let err = handle_build_sell_tx(
            "42", VALID_MINT, "not-a-pubkey", VALID_CREATOR,
            17_000_000_000_000, 450_000_000, false,
            VALID_BLOCKHASH, None, None,
        ).unwrap_err();
        assert!(err.to_string().contains("payer"));
    }

    #[test]
    fn build_sell_tx_rejects_bad_creator() {
        let err = handle_build_sell_tx(
            "42", VALID_MINT, VALID_PAYER, "not-a-pubkey",
            17_000_000_000_000, 450_000_000, false,
            VALID_BLOCKHASH, None, None,
        ).unwrap_err();
        assert!(err.to_string().contains("creator"));
    }

    #[test]
    fn build_sell_tx_rejects_bad_blockhash() {
        let err = handle_build_sell_tx(
            "42", VALID_MINT, VALID_PAYER, VALID_CREATOR,
            17_000_000_000_000, 450_000_000, false,
            "not-a-hash", None, None,
        ).unwrap_err();
        assert!(err.to_string().contains("blockhash"));
    }

    #[test]
    fn build_sell_tx_command_parses_from_full_json() {
        let json = format!(r#"{{
            "cmd": "build-sell-tx",
            "user_id": "42",
            "mint": "{}",
            "payer": "{}",
            "creator": "{}",
            "token_amount": 17000000000000,
            "min_sol_output_lamports": 450000000,
            "is_cashback_coin": true,
            "recent_blockhash": "{}",
            "priority_fee_microlamports": 200000,
            "compute_units": 300000
        }}"#, VALID_MINT, VALID_PAYER, VALID_CREATOR, VALID_BLOCKHASH);

        let cmd: Command = serde_json::from_str(&json).unwrap();
        match cmd {
            Command::BuildSellTx {
                user_id, mint, token_amount, min_sol_output_lamports,
                is_cashback_coin, ..
            } => {
                assert_eq!(user_id, "42");
                assert_eq!(mint, VALID_MINT);
                assert_eq!(token_amount, 17_000_000_000_000);
                assert_eq!(min_sol_output_lamports, 450_000_000);
                assert_eq!(is_cashback_coin, true);
            }
            _ => panic!("expected BuildSellTx variant"),
        }
    }

    #[test]
    fn build_sell_tx_command_omits_cashback_default() {
        // Optional `is_cashback_coin` should default to false when missing
        let json = format!(r#"{{
            "cmd": "build-sell-tx",
            "user_id": "42",
            "mint": "{}",
            "payer": "{}",
            "creator": "{}",
            "token_amount": 100,
            "min_sol_output_lamports": 0,
            "recent_blockhash": "{}"
        }}"#, VALID_MINT, VALID_PAYER, VALID_CREATOR, VALID_BLOCKHASH);

        let cmd: Command = serde_json::from_str(&json).unwrap();
        if let Command::BuildSellTx { is_cashback_coin, .. } = cmd {
            assert_eq!(is_cashback_coin, false);
        } else {
            panic!("expected BuildSellTx variant");
        }
    }

    // ── health (updated) ───────────────────────────────────────────────
    #[test]
    fn health_lists_build_sell_tx() {
        let v = handle_health().unwrap();
        let cmds = v["commands"].as_array().unwrap();
        assert!(cmds.iter().any(|c| c == "build-sell-tx"));
        // build-sell-tx must NO LONGER appear in not_yet
        let not_yet = v["not_yet"].as_array().unwrap();
        assert!(!not_yet.iter().any(|c| c == "build-sell-tx"));
    }

    // ── submit-bundle ───────────────────────────────────────────────────
    //
    // Tests focus on the SAFETY GATE — env var + live flag decision logic
    // and signature validation. We do NOT actually hit Jito in tests; the
    // network call is exercised only when both gates open, and the test
    // suite never opens them.

    use solana_sdk::signature::{Keypair, Signer};

    /// Build a real signed tx and return base64.
    fn build_signed_tx_b64() -> String {
        let curve = fresh_curve();
        let v = handle_build_buy_tx(
            "42", VALID_MINT, VALID_PAYER, 0.5, None,
            &curve, VALID_BLOCKHASH, None, None, None,
        ).unwrap();
        let unsigned_b64 = v["tx_b64"].as_str().unwrap();
        let tx_bytes = base64::engine::general_purpose::STANDARD.decode(unsigned_b64).unwrap();
        let mut tx: Transaction = bincode::deserialize(&tx_bytes).unwrap();
        // Stuff a non-default signature in slot 0 — the validation logic
        // only cares that it's not the placeholder.
        tx.signatures[0] = Keypair::new().sign_message(b"fake");
        let resigned = bincode::serialize(&tx).unwrap();
        base64::engine::general_purpose::STANDARD.encode(&resigned)
    }

    fn default_regions_for_test() -> Vec<String> {
        vec!["ny".to_string(), "amsterdam".to_string()]
    }

    // ── should_submit_live: the pure decision function ──────────────────
    #[test]
    fn should_submit_live_requires_both_gates() {
        // Default OFF: any of (live=false, env unset, env != "1") → dry-run
        assert!(!should_submit_live(false, None));
        assert!(!should_submit_live(false, Some("1")));
        assert!(!should_submit_live(true, None));
        assert!(!should_submit_live(true, Some("0")));
        assert!(!should_submit_live(true, Some("true")));   // exact "1" required
        assert!(!should_submit_live(true, Some("")));
        // ONLY true when BOTH gates open
        assert!(should_submit_live(true, Some("1")));
    }

    // ── validate_submit_input: signature and shape checks ───────────────
    #[test]
    fn submit_validates_real_signed_tx() {
        let signed = build_signed_tx_b64();
        let v = validate_submit_input("42", &signed, &default_regions_for_test()).unwrap();
        assert_ne!(v.primary_signature, Signature::default());
        assert_eq!(v.n_signatures, 1);
        assert!(v.tx_bytes.len() > 100);
    }

    #[test]
    fn submit_rejects_empty_user_id() {
        let signed = build_signed_tx_b64();
        let err = validate_submit_input("", &signed, &default_regions_for_test()).unwrap_err();
        assert!(err.to_string().contains("user_id"));
    }

    #[test]
    fn submit_rejects_empty_regions() {
        let signed = build_signed_tx_b64();
        let err = validate_submit_input("42", &signed, &[]).unwrap_err();
        assert!(err.to_string().contains("regions"));
    }

    #[test]
    fn submit_rejects_invalid_base64() {
        let err = validate_submit_input("42", "not-base64!@#", &default_regions_for_test()).unwrap_err();
        assert!(err.to_string().contains("base64"));
    }

    #[test]
    fn submit_rejects_too_small() {
        let tiny = base64::engine::general_purpose::STANDARD.encode(b"too small");
        let err = validate_submit_input("42", &tiny, &default_regions_for_test()).unwrap_err();
        assert!(err.to_string().contains("too small"));
    }

    #[test]
    fn submit_rejects_garbage_bytes() {
        // 200 bytes of zeros — passes the size check. Depending on the
        // bincode implementation, this might (a) fail Transaction
        // deserialization OR (b) decode to a Transaction with zero
        // signatures. Either path is a legitimate rejection — the safety
        // property is "garbage bytes never make it to Jito."
        let garbage = base64::engine::general_purpose::STANDARD.encode(&vec![0u8; 200]);
        let err = validate_submit_input("42", &garbage, &default_regions_for_test()).unwrap_err();
        let msg = err.to_string();
        assert!(
            msg.contains("Transaction") || msg.contains("deserialize")
                || msg.contains("zero signatures") || msg.contains("signatures"),
            "expected rejection for garbage bytes, got: {}", msg,
        );
    }

    #[test]
    fn submit_rejects_tx_with_zero_signatures() {
        // Explicitly test the zero-sigs path: a Transaction whose
        // signatures Vec is empty must be rejected.
        let tx = Transaction {
            signatures: vec![],
            message: Message::new_with_blockhash(
                &[], Some(&Pubkey::from_str(VALID_PAYER).unwrap()),
                &Hash::from_str(VALID_BLOCKHASH).unwrap(),
            ),
        };
        let bytes = bincode::serialize(&tx).unwrap();
        let b64 = base64::engine::general_purpose::STANDARD.encode(&bytes);
        let err = validate_submit_input("42", &b64, &default_regions_for_test()).unwrap_err();
        assert!(err.to_string().contains("zero signatures") || err.to_string().contains("signatures"));
    }

    #[test]
    fn submit_rejects_unsigned_tx_with_placeholder_signature() {
        // The build-buy-tx output has a default signature placeholder. We
        // must REJECT that — Python is supposed to sign before submitting.
        let curve = fresh_curve();
        let v = handle_build_buy_tx(
            "42", VALID_MINT, VALID_PAYER, 0.5, None,
            &curve, VALID_BLOCKHASH, None, None, None,
        ).unwrap();
        let unsigned = v["tx_b64"].as_str().unwrap();
        let err = validate_submit_input("42", unsigned, &default_regions_for_test()).unwrap_err();
        assert!(err.to_string().contains("placeholder")
             || err.to_string().contains("did not sign"));
    }

    // ── handle_submit_bundle: end-to-end envelope ───────────────────────
    #[test]
    fn submit_dry_run_returns_envelope_without_network() {
        // live=false → dry-run regardless of env var
        let signed = build_signed_tx_b64();
        let v = handle_submit_bundle("42", &signed, &default_regions_for_test(), false).unwrap();
        assert_eq!(v["phase"], "dry-run");
        assert_eq!(v["would_submit"], false);
        assert_eq!(v["user_id"], "42");
        assert_eq!(v["n_signatures"].as_u64().unwrap(), 1);
        assert_eq!(v["n_regions"].as_u64().unwrap(), 2);
        // signature must be a real string, not the default
        let sig = v["signature"].as_str().unwrap();
        assert!(!sig.is_empty());
        assert_ne!(sig, Signature::default().to_string());
    }

    #[test]
    fn submit_live_without_env_var_rejects_loudly() {
        // SAFETY: live=true but TG_TRADER_LIVE not set → reject with a
        // clear error. We assume the test environment does NOT set it.
        // (If it does, this test will fail loudly — that's the right
        // behavior because nobody should run cargo test with that env set.)
        let prior = std::env::var("TG_TRADER_LIVE").ok();
        assert!(
            prior.as_deref() != Some("1"),
            "TG_TRADER_LIVE=1 must NOT be set during cargo test — found {:?}",
            prior,
        );
        let signed = build_signed_tx_b64();
        let err = handle_submit_bundle("42", &signed, &default_regions_for_test(), true).unwrap_err();
        let msg = err.to_string();
        assert!(msg.contains("TG_TRADER_LIVE"), "expected env var in error, got: {}", msg);
        assert!(msg.contains("refusing"), "expected 'refusing' in error, got: {}", msg);
    }

    // ── Command parsing ─────────────────────────────────────────────────
    #[test]
    fn submit_bundle_command_parses_from_json() {
        let signed = build_signed_tx_b64();
        let json = format!(r#"{{
            "cmd": "submit-bundle",
            "user_id": "42",
            "signed_tx_b64": "{}",
            "regions": ["ny", "amsterdam"],
            "live": false
        }}"#, signed);
        let cmd: Command = serde_json::from_str(&json).unwrap();
        if let Command::SubmitBundle { user_id, regions, live, .. } = cmd {
            assert_eq!(user_id, "42");
            assert_eq!(regions, vec!["ny".to_string(), "amsterdam".to_string()]);
            assert_eq!(live, false);
        } else {
            panic!("expected SubmitBundle variant");
        }
    }

    #[test]
    fn submit_bundle_regions_default_to_all_five_when_omitted() {
        // Default region list is 5 Jito mainnet regions
        let signed = build_signed_tx_b64();
        let json = format!(r#"{{
            "cmd": "submit-bundle",
            "user_id": "42",
            "signed_tx_b64": "{}"
        }}"#, signed);
        let cmd: Command = serde_json::from_str(&json).unwrap();
        if let Command::SubmitBundle { regions, live, .. } = cmd {
            assert_eq!(regions.len(), 5, "expected default 5 regions, got {:?}", regions);
            assert!(regions.iter().any(|r| r == "ny"));
            assert!(regions.iter().any(|r| r == "amsterdam"));
            assert!(regions.iter().any(|r| r == "frankfurt"));
            assert!(regions.iter().any(|r| r == "tokyo"));
            assert!(regions.iter().any(|r| r == "slc"));
            assert_eq!(live, false, "live should default to false (safe)");
        } else {
            panic!("expected SubmitBundle variant");
        }
    }

    // ── Wire-format contract ────────────────────────────────────────────
    #[test]
    fn malformed_command_json_round_trips_as_error() {
        let parsed: Result<Command, _> = serde_json::from_str(r#"{"cmd":"not-a-real-command"}"#);
        assert!(parsed.is_err(), "unknown cmd variants must fail to deserialize");
    }

    #[test]
    fn response_serialization_shape_is_stable() {
        let ok_resp = Response::ok(json!({"x": 1}));
        let serialized = serde_json::to_string(&ok_resp).unwrap();
        assert!(serialized.contains(r#""ok":true"#));
        assert!(serialized.contains(r#""data":{"x":1}"#));
        assert!(!serialized.contains("error"));

        let err_resp = Response::err("boom");
        let serialized = serde_json::to_string(&err_resp).unwrap();
        assert!(serialized.contains(r#""ok":false"#));
        assert!(serialized.contains(r#""error":"boom""#));
        assert!(!serialized.contains(r#""data":"#));
    }

    #[test]
    fn build_buy_tx_command_parses_from_full_json() {
        // Lock in the wire format Python uses. If any field name drifts,
        // this test fails immediately.
        let json = format!(r#"{{
            "cmd": "build-buy-tx",
            "user_id": "42",
            "mint": "{}",
            "payer": "{}",
            "sol": 0.5,
            "slippage_bps": 250,
            "bonding_curve": {{
                "virtual_sol_reserves": 30000000000,
                "virtual_token_reserves": 1073000000000000,
                "real_sol_reserves": 0,
                "real_token_reserves": 793100000000000,
                "token_total_supply": 1000000000000000,
                "complete": false,
                "creator": "{}",
                "is_cashback_coin": false
            }},
            "recent_blockhash": "{}",
            "priority_fee_microlamports": 200000,
            "compute_units": 250000
        }}"#, VALID_MINT, VALID_PAYER, VALID_CREATOR, VALID_BLOCKHASH);

        let cmd: Command = serde_json::from_str(&json).unwrap();
        match cmd {
            Command::BuildBuyTx { user_id, mint, sol, slippage_bps, .. } => {
                assert_eq!(user_id, "42");
                assert_eq!(mint, VALID_MINT);
                assert_eq!(sol, 0.5);
                assert_eq!(slippage_bps, Some(250));
            }
            _ => panic!("expected BuildBuyTx variant"),
        }
    }
}
