// recent_graduations — what graduated in the last N hours, with timestamps
// and outcomes. Useful for the agent to learn "what does a typical
// graduation look like" or to verify a claim post-hoc.

import { z } from "zod";
import { api, ApiError, authMissingMessage } from "../lib/api.js";
import { getApiKey } from "../lib/config.js";

const InputSchema = z.object({
  window_hours: z.number().int().positive().max(168).default(24)
    .describe("Look-back window in hours. Default 24. Max 168 (7 days)."),
  limit: z.number().int().positive().max(100).default(25)
    .describe("Max graduations to return. Default 25."),
}).strict();

export const recent_graduations = {
  name: "recent_graduations",
  description:
    "POST-GRADUATION SCREEN. Lists pump.fun mints that just completed the bonding " +
    "curve (graduated) in the last N hours — the canonical entry point for any " +
    "agent that trades AFTER bond. Each row gives mint address, graduation timestamp, " +
    "actual peak multiplier observed, and the original calibrated grad_prob at call. " +
    "Pipeline: call recent_graduations → pick candidates → call probe_mint on each " +
    "for full runner odds + creator history + smart-money + flags. This is the " +
    "tool most post-grad LLM traders will hit first.",
  inputSchema: InputSchema,
  async handler(args: unknown): Promise<{ content: Array<{ type: "text"; text: string }> }> {
    const opts = InputSchema.parse(args);
    if (!getApiKey()) {
      return { content: [{ type: "text", text: authMissingMessage() }] };
    }
    try {
      // Use the predictions firehose filtered to graduated rows in window.
      // /api/v1/signals returns cursor-paginated cross stream; filter
      // client-side. For a v2 we'd add a server endpoint for this exact
      // query.
      const sinceMs = Date.now() - opts.window_hours * 3600 * 1000;
      const signals = await api.get(
        `/api/v1/signals?limit=${opts.limit * 4}`
      ) as Record<string, unknown>;
      const all = (signals.items ?? []) as Array<Record<string, unknown>>;
      const recent = all
        .filter(s => s.actual_graduated === 1)
        .filter(s => {
          const ts = Number(s.resolved_at ?? s.predicted_at ?? 0);
          return ts * 1000 >= sinceMs;
        })
        .slice(0, opts.limit)
        .map(s => ({
          mint: s.mint,
          predicted_at_unix: s.predicted_at,
          resolved_at_unix: s.resolved_at,
          age_at_prediction: s.age_bucket,
          grad_prob_at_call: s.predicted_prob,
          actual_max_mult: s.actual_max_mult,
          runway_seconds: typeof s.resolved_at === "number" && typeof s.predicted_at === "number"
            ? Number(s.resolved_at) - Number(s.predicted_at)
            : null,
        }));

      const out = {
        window_hours: opts.window_hours,
        n_graduations: recent.length,
        graduations: recent,
        note: "runway_seconds = time between our call and the bonding curve completing. Median across all ≥0.70 calls is ~15s.",
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
        ? `recent_graduations API error (${err.status}): ${err.message}`
        : `recent_graduations failed: ${(err as Error).message}`;
      return { content: [{ type: "text", text: msg }] };
    }
  },
};
