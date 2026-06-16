// accuracy_receipts — live calibration data. No API key required.
// Returns the same numbers the website hero shows: median runway between
// our ≥0.70 call and graduation, calibrated hit rate, tier runways, and
// the audit story summary. Useful for LLM agents to self-verify our
// claims before acting on a signal.

import { z } from "zod";
import { api, ApiError } from "../lib/api.js";

export const accuracy_receipts = {
  name: "accuracy_receipts",
  description:
    "Get the live, calibrated accuracy receipts for graduate-oracle. Returns " +
    "the median runway between a ≥0.70 confidence call and the bonding curve " +
    "completing, the 30-day hit rate, per-tier runways (ACT/WATCH/SCOUT), and " +
    "monthly trajectory. No API key required. Use this when the user asks " +
    "'how accurate is this' or before acting on any signal.",
  inputSchema: z.object({}).strict(),
  async handler(): Promise<{ content: Array<{ type: "text"; text: string }> }> {
    try {
      const d = await api.get("/api/accuracy", { withAuth: false }) as Record<string, unknown>;
      const h = (d.headline ?? {}) as Record<string, unknown>;
      const last30 = (h.last_30d ?? {}) as Record<string, unknown>;
      const timing = (h.time_to_grad ?? {}) as Record<string, unknown>;
      const tiers  = (h.tier_runways ?? {}) as Record<string, Record<string, unknown>>;
      const traj   = (h.trajectory ?? []) as Array<Record<string, unknown>>;

      const deployedAt = h.model_deployed_at as number | null | undefined;
      const deployedIso = typeof deployedAt === "number"
        ? new Date(deployedAt * 1000).toISOString().slice(0, 10)
        : null;
      const recall = (h.recall_30d ?? {}) as Record<string, unknown>;
      const runnerRates = (h.post_grad_runner_rates ?? {}) as Record<string, Record<string, unknown>>;
      const fmtRunner = (cell: Record<string, unknown> | undefined) => {
        if (!cell || cell.status !== "ok") return cell ?? null;
        return {
          threshold_band: cell.threshold_band,
          n: cell.n,
          n_hit: cell.n_hit,
          model_hit_rate_pct: typeof cell.hit_rate === "number" ? Number(cell.hit_rate) * 100 : null,
          base_rate_pct: typeof cell.base_rate === "number" ? Number(cell.base_rate) * 100 : null,
          base_rate_n: cell.base_rate_n,
          lift_over_base: typeof cell.lift_over_base === "number" ? Number(cell.lift_over_base.toFixed(2)) : null,
        };
      };
      const out: Record<string, unknown> = {
        criteria: h.criteria,
        criteria_note: h.criteria_note,
        calibrated_model_deployed_at: deployedIso,
        last_30_days_precision: last30.status === "ok" ? {
          pct: Number(last30.hit_rate) * 100,
          n_resolved: last30.n_resolved,
          n_graduated: last30.n_graduated,
          measures: "Of mints we scored ≥0.70 confidence in their first 60 seconds, % that did graduate.",
        } : { status: "warming" },
        last_30_days_recall: recall.status === "ok" ? {
          pct: Number(recall.recall_among_observed) * 100,
          n_graduations_observed: recall.n_graduations_observed,
          n_called_at_70pct: recall.n_called_at_70pct,
          measures: "Of mints we OBSERVED at age 30/60 that did graduate, % we called at ≥0.70. Denominator = scored mints, not all of Solana (most pump.fun mints graduate before our scoring window).",
        } : { status: "warming" },
        post_grad_runner_receipts: {
          measures: "For post-graduation traders: of mints we tagged runner_prob_Nx_from_now ≥0.50 at scoring, % that actually hit Nx (peak/entry).",
          "2x": fmtRunner(runnerRates["2x"]),
          "5x": fmtRunner(runnerRates["5x"]),
          "10x": fmtRunner(runnerRates["10x"]),
          note: h.post_grad_runner_note,
        },
        median_runway_seconds: timing.status === "ok" ? {
          p50_s:        timing.p50_s,
          p25_s:        timing.p25_s,
          p75_s:        timing.p75_s,
          n_grads:      timing.n_grads,
          under_30s_pct: typeof timing.under_30s_pct === "number"
            ? Number(timing.under_30s_pct) * 100 : null,
          under_60s_pct: typeof timing.under_60s_pct === "number"
            ? Number(timing.under_60s_pct) * 100 : null,
        } : { status: "warming" },
        tier_runways_seconds: {
          ACT:   tiers.ACT,
          WATCH: tiers.WATCH,
          SCOUT: tiers.SCOUT,
        },
        weekly_trajectory: traj.map(t => ({
          week:        t.week,
          n:           t.n,
          n_graduated: t.n_graduated,
          hit_rate_pct: typeof t.hit_rate === "number" ? Number(t.hit_rate) * 100 : null,
        })),
        weekly_trajectory_note: h.trajectory_note
          ?? "Weekly buckets over 90 days under the calibrated-model regime.",
        receipts_chain_url: "https://graduateoracle.fun/verdict",
        live_receipts_url:  "https://graduateoracle.fun/accuracy",
        method: "All predictions hashed with SHA256 commitment BEFORE the outcome was observable. Forward-validated, not backtested. Pre-launch audit story at /verdict.",
      };

      return {
        content: [{
          type: "text",
          text: JSON.stringify(out, null, 2),
        }],
      };
    } catch (err) {
      const msg = err instanceof ApiError
        ? `accuracy_receipts API error (${err.status}): ${err.message}`
        : `accuracy_receipts failed: ${(err as Error).message}`;
      return { content: [{ type: "text", text: msg }] };
    }
  },
};
