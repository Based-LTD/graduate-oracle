// probe_mint — full feature breakdown for a single pump.fun mint.
// Requires API key. Returns the structured per-mint snapshot the agent
// can reason over: grad_prob (calibrated), entry quality, smart-money
// presence, creator track record, rug-risk flags, runner probabilities.
import { z } from "zod";
import { api, ApiError, authMissingMessage } from "../lib/api.js";
import { getApiKey } from "../lib/config.js";
const InputSchema = z.object({
    mint: z.string()
        .min(32, "mint must be a Solana base58 address (32-44 chars)")
        .max(44, "mint must be a Solana base58 address (32-44 chars)")
        .describe("The pump.fun mint address (base58 Solana pubkey, typically ending in 'pump')."),
}).strict();
export const probe_mint = {
    name: "probe_mint",
    description: "Score a single pump.fun mint by address — works for BOTH live (pre-grad) AND " +
        "already-graduated mints. Returns 20+ fields the agent reasons over: calibrated " +
        "runner_prob at 2×/5×/10× from current price, expected peak multiplier, creator " +
        "track record (creator_5x_rate, creator_n_launches, creator_runner), smart-money " +
        "presence, post-grad survival probability, rug-risk flags (bundle_detected, " +
        "manufactured_pump, dex_paid, fee_delegated), and grad_prob if still pre-grad. " +
        "Use this when your trading agent wants to score a specific mint — most commonly " +
        "in a post-graduation screening loop (LLM picks recent_graduations → probe_mint " +
        "for each → decide entry).",
    inputSchema: InputSchema,
    async handler(args) {
        const { mint } = InputSchema.parse(args);
        if (!getApiKey()) {
            return { content: [{ type: "text", text: authMissingMessage() }] };
        }
        try {
            const d = await api.get(`/api/v1/probe/${encodeURIComponent(mint)}`);
            // API returns {mint, found_in_live: false, hint} for mints we don't currently track.
            if (d.found_in_live === false) {
                const hint = typeof d.hint === "string" ? d.hint : "mint not currently tracked";
                return {
                    content: [{
                            type: "text",
                            text: `Mint ${mint} is not currently tracked (${hint}). It may be past the 60s scoring window, never seen by our observer, or invalid. Use 'live_mints' to see what's in the current scoring window, or 'recent_graduations' for historical lookups.`,
                        }],
                };
            }
            // Flatten + annotate for the LLM — easier to reason over than raw JSON.
            const m = (typeof d.mint === "object" && d.mint !== null ? d.mint : d);
            const summary = {
                mint: m.mint ?? mint,
                scored_at_age_s: m.age_s,
                grad_prob: m.grad_prob,
                grad_prob_bucket: m.grad_prob_bucket,
                confidence_interpretation: typeof m.grad_prob === "number"
                    ? (m.grad_prob >= 0.90 ? "very high (~99% calibrated grad rate)"
                        : m.grad_prob >= 0.70 ? "high (~91% calibrated grad rate)"
                            : m.grad_prob >= 0.50 ? "moderate"
                                : "low")
                    : "unknown",
                market: {
                    current_mult: m.current_mult,
                    vsol_sol: typeof m.virtual_sol_reserves === "number"
                        ? Number(m.virtual_sol_reserves) / 1e9
                        : null,
                    unique_buyers: m.unique_buyers,
                    n_trades: m.n_trades,
                },
                momentum: {
                    vsol_velocity_30s: m.vsol_velocity_30s,
                    vsol_velocity_60s: m.vsol_velocity_60s,
                    vsol_acceleration: m.vsol_acceleration,
                },
                runner_odds_from_now: {
                    two_x: m.runner_prob_2x_from_now,
                    three_x: m.runner_prob_3x_from_now,
                    five_x: m.runner_prob_5x_from_now,
                    ten_x: m.runner_prob_10x_from_now,
                    expected_peak_mult: m.expected_peak_mult,
                },
                smart_money: {
                    smart_money_in: m.smart_money_in,
                    n_whales: m.n_whales,
                    n_clustered_pairs: m.n_clustered_pairs,
                },
                creator: {
                    creator_runner: m.creator_runner,
                    creator_5x_rate: m.creator_5x_rate,
                    creator_n_launches: m.creator_n_launches,
                },
                flags: {
                    bundle_detected: m.bundle_detected,
                    manufactured_pump: m.manufactured_pump,
                    dex_paid: m.dex_paid,
                    fee_delegated: m.fee_delegated,
                },
                post_grad_survival_prob: m.post_grad_survival_prob,
                receipt: {
                    method: "Hashed before outcome was observable. Verify at graduateoracle.fun/receipts.",
                    verify_url: `https://graduateoracle.fun/api/predictions/by_mint/${mint}`,
                },
            };
            return {
                content: [{
                        type: "text",
                        text: JSON.stringify(summary, null, 2),
                    }],
            };
        }
        catch (err) {
            if (err instanceof ApiError && err.status === 404) {
                return {
                    content: [{
                            type: "text",
                            text: `Mint ${mint} is not currently tracked. Either it's past the 60s scoring window or never seen by our observer. Use 'recent_graduations' or 'live_mints' to find mints in the current scoring window.`,
                        }],
                };
            }
            if (err instanceof ApiError && err.status === 401) {
                return { content: [{ type: "text", text: "API key invalid or expired. " + authMissingMessage() }] };
            }
            const msg = err instanceof ApiError
                ? `probe_mint API error (${err.status}): ${err.message}`
                : `probe_mint failed: ${err.message}`;
            return { content: [{ type: "text", text: msg }] };
        }
    },
};
