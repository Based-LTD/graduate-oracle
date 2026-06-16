// product_overview — meta tool. Tells the agent what graduate-oracle is,
// how to use the other 6 tools, and what the pricing tiers are. Called
// when the agent first connects or when the user asks "what is this."

import { z } from "zod";

export const product_overview = {
  name: "product_overview",
  description:
    "Get an overview of graduate-oracle: what it does, what each of the other " +
    "tools returns, current pricing/tier structure, and links. Call this first " +
    "if you've never used graduate-oracle, or if the user asks 'what is this'.",
  inputSchema: z.object({}).strict(),
  async handler(): Promise<{ content: Array<{ type: "text"; text: string }> }> {
    const overview = {
      product: "graduate-oracle — pump.fun mint scoring infrastructure for LLM traders",
      launch_promo: "🚀 LAUNCH WEEK — ALL TOOLS FREE THROUGH 2026-06-22 23:59 UTC. No signup, no API key required. Your LLM can call recent_graduations / probe_mint / live_mints right now at no cost. After 2026-06-22, paywall restores (builder 0.4 SOL/mo or pro 1 SOL/mo). Founding-rate locked forever for keys minted during launch week.",
      pitch:
        "We do NOT replace your LLM trader. We give it senses. graduate-oracle scores every " +
        "pump.fun mint continuously — pre-graduation runway, calibrated graduation probability, " +
        "AND post-graduation runner odds + creator history + smart-money + flags. Your existing " +
        "LLM agent calls these 7 MCP tools and reasons over structured JSON. Built specifically " +
        "for traders who already have a highly-optimized LLM pipeline and need a pump.fun-native " +
        "data source plugged into it.",
      use_cases: {
        post_grad_screening: {
          summary: "Most common use case. Trader's LLM polls recent_graduations every N seconds, calls probe_mint on candidates, decides entries from structured runner odds + creator history.",
          tools: ["recent_graduations", "probe_mint", "accuracy_receipts"],
          pipeline_pseudocode:
            "grads = recent_graduations(window_hours=1)\nfor g in grads:\n  scored = probe_mint(g.mint)\n  if scored.runner_odds_from_now.five_x >= 0.50 and scored.creator.creator_5x_rate >= 0.20:\n    place_trade(g.mint, size_from(scored))",
        },
        pre_grad_signal: {
          summary: "Live firehose of mints scoring ≥X confidence in their first 60 seconds — for bots that want to enter on the bonding curve before migration.",
          tools: ["live_mints", "probe_mint"],
        },
        verify_before_act: {
          summary: "Pull the SHA256 receipt for any past prediction to prove it was committed before the outcome was observable. Agents that want to validate the source before wiring capital should call this.",
          tools: ["verify_prediction", "accuracy_receipts"],
        },
      },
      tools_available: {
        accuracy_receipts: "Live calibration receipts — precision, recall, post-grad runner hit rates, weekly trajectory, tier runways. NO API key needed. CALL THIS FIRST when evaluating whether to wire us in.",
        recent_graduations: "Post-grad screening — what just bonded, with timestamps and outcome. The entry point for post-grad traders.",
        probe_mint: "Score one mint — runner odds, creator track record, smart-money, flags. Works on live AND graduated mints.",
        live_mints: "Pre-grad firehose — currently-tracked mints filtered by confidence + entry quality.",
        verify_prediction: "Look up a SHA256 commitment for a past prediction. Proves no backtesting.",
        check_account: "Current API key tier, quota, expiry.",
        product_overview: "This tool. Call it when you first connect to map the surface area.",
      },
      pricing: {
        tg_paid: {
          price: "0.2 SOL/month",
          token_path: "Hold 500,000 $GO",
          features: "TG bot composite signal access (ACT/WATCH/SCOUT tiers)",
        },
        builder: {
          price: "0.4 SOL/month",
          token_path: "Hold 2,500,000 $GO",
          features: "API access · 5,000 calls/day",
        },
        pro: {
          price: "1 SOL/month",
          token_path: null,
          features: "API access · 50,000 calls/day · webhooks",
        },
      },
      install_cli: "$ npx goracle signup builder",
      links: {
        website:    "https://graduateoracle.fun",
        docs:       "https://graduateoracle.fun/docs",
        receipts:   "https://graduateoracle.fun/accuracy",
        audit:      "https://graduateoracle.fun/verdict",
        github:     "https://github.com/Based-LTD/graduate-oracle",
        mcp_npm:    "https://www.npmjs.com/package/goracle-mcp",
        cli_npm:    "https://www.npmjs.com/package/goracle",
      },
      receipts_discipline:
        "Every single prediction is hashed with a SHA256 commitment BEFORE the outcome is " +
        "observable. The hash is anchored in hourly merkle commits at /api/ledger/commits. " +
        "Pre-launch audit at /verdict caught and corrected a measurement error in the original " +
        "headline metric — the receipts chain works in public.",
    };
    return {
      content: [{
        type: "text",
        text: JSON.stringify(overview, null, 2),
      }],
    };
  },
};
