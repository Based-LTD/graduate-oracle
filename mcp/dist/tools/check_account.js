// check_account — what tier am I, how many calls today, when does this
// expire. Lets the LLM (and the user observing) understand the API budget
// and surface upgrade prompts when appropriate.
import { z } from "zod";
import { api, ApiError, authMissingMessage } from "../lib/api.js";
import { getApiKey } from "../lib/config.js";
export const check_account = {
    name: "check_account",
    description: "Get the current API account state — tier (free/tg_paid/builder/pro), " +
        "expiration time, today's call count vs daily limit, and whether the key " +
        "is bound to a wallet (token-holder path). Useful for the agent to know " +
        "whether to ration calls or to surface upgrade prompts to the user.",
    inputSchema: z.object({}).strict(),
    async handler() {
        if (!getApiKey()) {
            return { content: [{ type: "text", text: authMissingMessage() }] };
        }
        try {
            const d = await api.get("/api/me");
            const expiresAt = d.expires_at;
            const daysLeft = (typeof expiresAt === "number")
                ? Math.max(0, Math.floor((expiresAt - Date.now() / 1000) / 86400))
                : null;
            const today = (d.today ?? {});
            const tierLimits = (d.tier_limits ?? {});
            const callsToday = typeof today.used_today === "number" ? today.used_today : null;
            const dailyLimit = typeof today.limit_per_day === "number" ? today.limit_per_day : null;
            const remaining = typeof today.remaining === "number"
                ? today.remaining
                : (callsToday !== null && dailyLimit !== null ? Math.max(0, dailyLimit - callsToday) : null);
            const out = {
                tier: d.tier,
                key_prefix: d.key_prefix,
                expires_at_unix: expiresAt,
                days_until_expiry: daysLeft,
                is_token_holder: d.wallet ? true : false,
                calls_today: callsToday,
                daily_limit: dailyLimit,
                calls_remaining: remaining,
                tier_label: tierLimits.label ?? null,
                realtime_access: tierLimits.realtime ?? null,
                websocket_access: tierLimits.websocket ?? null,
                watchlist_max: tierLimits.watchlist ?? null,
            };
            return {
                content: [{
                        type: "text",
                        text: JSON.stringify(out, null, 2),
                    }],
            };
        }
        catch (err) {
            if (err instanceof ApiError && err.status === 401) {
                return {
                    content: [{
                            type: "text",
                            text: "API key invalid or expired. " + authMissingMessage(),
                        }],
                };
            }
            const msg = err instanceof ApiError
                ? `check_account API error (${err.status}): ${err.message}`
                : `check_account failed: ${err.message}`;
            return { content: [{ type: "text", text: msg }] };
        }
    },
};
