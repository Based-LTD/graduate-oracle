use crate::types::NewCoinEvent;
use log::{error, info, warn};
use solana_sdk::pubkey::Pubkey;
use std::collections::HashSet;
use std::str::FromStr;
use tokio::sync::mpsc;

const PUMP_PROGRAM: &str = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P";
// Bumped 2026-06-17 from 5_000ms → 30_000ms. The WSS observer is the
// primary source of CREATE events; this poller is a backup that catches
// what the WS drops. At 5s we were burning ~17k Helius credits/day for
// duplicates; 30s keeps the safety net without the bleed.
//
// Day 4.81 (2026-06-28) — coverage fix. Daniel surfaced two pump.fun
// mints we missed entirely (9cRCn9rGT8…pump, HEU5mPDm5j…). Diagnosis:
// during busy launch windows pump.fun does 50-100+ creates/min, but
// 25-events-per-30s capped us at 50/min. We were structurally guaranteed
// to drop launches during bursts. Bumping interval down + fetch limit up
// to expand the safety net. Expected credit burn ~50k/day (vs current
// ~17k) — well within DEV tier.
const POLL_INTERVAL_MS: u64 = 10_000;
const FETCH_LIMIT: u32 = 100;
const MAX_SEEN_SIGS: usize = 4000;

/// Polls the Helius parsed transactions API for pump.fun CREATE events.
/// Runs alongside the websocket listener to catch events the websocket drops.
pub async fn run_helius_poller(api_key: String, tx: mpsc::Sender<NewCoinEvent>) {
    info!("[HELIUS-POLL] Starting — polling every {}ms for CREATE events", POLL_INTERVAL_MS);

    let client = reqwest::Client::new();
    let url = format!(
        "https://api.helius.xyz/v0/addresses/{}/transactions?api-key={}&type=CREATE&limit={}",
        PUMP_PROGRAM, api_key, FETCH_LIMIT
    );

    let mut seen_sigs: HashSet<String> = HashSet::new();
    let mut interval = tokio::time::interval(std::time::Duration::from_millis(POLL_INTERVAL_MS));
    let mut total_detected: u64 = 0;
    let mut consecutive_errors: u32 = 0;

    loop {
        interval.tick().await;

        match fetch_creates(&client, &url).await {
            Ok(events) => {
                consecutive_errors = 0;
                let mut new_count = 0u32;

                for event in events {
                    if seen_sigs.contains(&event.signature) {
                        continue;
                    }
                    seen_sigs.insert(event.signature.clone());

                    // Extract mint from first token transfer
                    let mint_str = match event.token_transfers.first() {
                        Some(tt) => &tt.mint,
                        None => continue,
                    };

                    let mint = match Pubkey::from_str(mint_str) {
                        Ok(pk) => pk,
                        Err(_) => {
                            warn!("[HELIUS-POLL] Invalid mint pubkey: {}", mint_str);
                            continue;
                        }
                    };

                    let creator = match Pubkey::from_str(&event.fee_payer) {
                        Ok(pk) => pk,
                        Err(_) => Pubkey::default(),
                    };

                    let coin_event = NewCoinEvent {
                        mint,
                        creator,
                        slot: event.slot,
                        signal_source: "HELIUS-POLL".to_string(),
                        ai_confidence: None,
                        volume_sol_at_signal: 0.0,
                        tx_count_at_signal: 0,
                        smart_wallet_count: 0,
                    };

                    if tx.send(coin_event).await.is_err() {
                        error!("[HELIUS-POLL] Channel closed — exiting");
                        return;
                    }
                    new_count += 1;
                    total_detected += 1;
                }

                if new_count > 0 {
                    info!("[HELIUS-POLL] Detected {} new tokens (total: {})", new_count, total_detected);
                }

                // Prune seen set to prevent unbounded growth
                if seen_sigs.len() > MAX_SEEN_SIGS {
                    seen_sigs.clear();
                    info!("[HELIUS-POLL] Cleared seen signatures cache");
                }
            }
            Err(e) => {
                consecutive_errors += 1;
                if consecutive_errors <= 3 {
                    warn!("[HELIUS-POLL] Fetch error ({}): {}", consecutive_errors, e);
                } else {
                    error!("[HELIUS-POLL] Fetch error ({}): {} — backing off", consecutive_errors, e);
                    tokio::time::sleep(std::time::Duration::from_secs(5)).await;
                }
            }
        }
    }
}

// ── API response types ──────────────────────────────────────────────────────

#[derive(serde::Deserialize)]
struct HeliusTx {
    signature: String,
    slot: u64,
    #[serde(rename = "feePayer")]
    fee_payer: String,
    #[serde(rename = "tokenTransfers", default)]
    token_transfers: Vec<TokenTransfer>,
}

#[derive(serde::Deserialize)]
struct TokenTransfer {
    mint: String,
}

async fn fetch_creates(client: &reqwest::Client, url: &str) -> anyhow::Result<Vec<HeliusTx>> {
    let resp = client
        .get(url)
        .timeout(std::time::Duration::from_secs(10))
        .send()
        .await?;

    if !resp.status().is_success() {
        anyhow::bail!("HTTP {}", resp.status());
    }

    let txs: Vec<HeliusTx> = resp.json().await?;
    Ok(txs)
}
