// live_mints — the firehose, filtered. Returns currently-tracked mints
// matching the agent's threshold + entry-quality filter, sorted by
// confidence. This is the "give me what's hot right now" tool.

import { z } from "zod";
import { api, ApiError, authMissingMessage } from "../lib/api.js";
import { getApiKey } from "../lib/config.js";

const InputSchema = z.object({
  min_grad_prob: z.number().min(0).max(1).default(0.70)
    .describe("Minimum calibrated graduation probability (0-1). Default 0.70 = 'high confidence' band."),
  max_entry_mult: z.number().positive().default(999)
    .describe("Maximum current price multiplier from launch. Lower = cleaner entry. 2.0 is a common filter."),
  exclude_bundled: z.boolean().default(true)
    .describe("Drop mints flagged bundle_detected (rug risk). Default true."),
  exclude_manufactured: z.boolean().default(true)
    .describe("Drop mints flagged manufactured_pump (bot-laddered launch). Default true."),
  require_smart_money: z.boolean().default(false)
    .describe("Only show mints with smart_money_in ≥ 1."),
  limit: z.number().int().positive().max(50).default(20)
    .describe("Max mints to return. Default 20."),
}).strict();

export const live_mints = {
  name: "live_mints",
  description:
    "Stream currently-tracked pump.fun mints in real time, filtered by " +
    "graduation confidence, entry quality, and risk flags. Sorted descending " +
    "by grad_prob. The agent's main 'what's hot right now' tool. Returns " +
    "structured rows ready to reason over for sizing/entry decisions.",
  inputSchema: InputSchema,
  async handler(args: unknown): Promise<{ content: Array<{ type: "text"; text: string }> }> {
    const opts = InputSchema.parse(args);
    if (!getApiKey()) {
      return { content: [{ type: "text", text: authMissingMessage() }] };
    }
    try {
      const d = await api.get("/api/v1/live") as Record<string, unknown>;
      const mints = ((d.mints ?? []) as Array<Record<string, unknown>>)
        .filter(m => (Number(m.grad_prob) || 0) >= opts.min_grad_prob)
        .filter(m => (Number(m.current_mult) || 0) <= opts.max_entry_mult)
        .filter(m => opts.exclude_bundled ? !m.bundle_detected : true)
        .filter(m => opts.exclude_manufactured ? !m.manufactured_pump : true)
        .filter(m => opts.require_smart_money ? (Number(m.smart_money_in) || 0) >= 1 : true)
        .sort((a, b) => (Number(b.grad_prob) || 0) - (Number(a.grad_prob) || 0))
        .slice(0, opts.limit)
        .map(m => ({
          mint: m.mint,
          grad_prob: m.grad_prob,
          grad_prob_bucket: m.grad_prob_bucket,
          current_mult: m.current_mult,
          vsol_sol: typeof m.virtual_sol_reserves === "number" ? Number(m.virtual_sol_reserves) / 1e9 : null,
          unique_buyers: m.unique_buyers,
          age_s: m.age_s,
          smart_money_in: m.smart_money_in,
          runner_prob_2x: m.runner_prob_2x_from_now,
          runner_prob_5x: m.runner_prob_5x_from_now,
          flags: {
            bundle_detected: m.bundle_detected,
            manufactured_pump: m.manufactured_pump,
            dex_paid: m.dex_paid,
            fee_delegated: m.fee_delegated,
          },
          creator_runner: m.creator_runner,
          creator_5x_rate: m.creator_5x_rate,
        }));

      const out = {
        filter: opts,
        n_mints: mints.length,
        snapshot_age_s: d.snapshot_age_s,
        indexed_total: d.n_indexed_curves,
        mints,
        note: "Sorted by grad_prob descending. For ≥0.70 calls, calibrated grad rate is 91%. For ≥0.90, 99%.",
      };

      return {
        content: [{
          type: "text",
          text: JSON.stringify(out, null, 2),
        }],
      };
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        return { content: [{ type: "text", text: "API key invalid or expired. " + authMissingMessage() }] };
      }
      const msg = err instanceof ApiError
        ? `live_mints API error (${err.status}): ${err.message}`
        : `live_mints failed: ${(err as Error).message}`;
      return { content: [{ type: "text", text: msg }] };
    }
  },
};
