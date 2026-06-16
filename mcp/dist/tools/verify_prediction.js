// verify_prediction — look up a specific prediction in the receipts chain.
// "Agents that verify > agents that trust." This is the tool that
// distinguishes graduate-oracle from "trust me bro" signal services.
import { z } from "zod";
import { api, ApiError } from "../lib/api.js";
const InputSchema = z.object({
    mint: z.string().optional()
        .describe("Mint address to look up predictions for. Returns all predictions logged for this mint with their hashes + outcomes."),
    prediction_id: z.union([z.string(), z.number()]).optional()
        .describe("Specific prediction_id to verify (from /api/ledger/commits)."),
}).strict()
    .refine(d => d.mint || d.prediction_id, {
    message: "Provide either `mint` or `prediction_id`.",
});
export const verify_prediction = {
    name: "verify_prediction",
    description: "Verify a graduate-oracle prediction against the on-chain receipts chain. " +
        "Returns the original prediction (probability, age, features at score " +
        "time) along with the SHA256 commitment and the resolved on-chain " +
        "outcome. The hash was locked in BEFORE the outcome was observable — " +
        "this proves the prediction wasn't fabricated after the fact. Use this " +
        "when the agent needs to validate our claims before acting on a signal.",
    inputSchema: InputSchema,
    async handler(args) {
        const opts = InputSchema.parse(args);
        try {
            if (opts.mint) {
                const d = await api.get(`/api/predictions/by_mint/${encodeURIComponent(opts.mint)}`, { withAuth: false });
                return {
                    content: [{
                            type: "text",
                            text: JSON.stringify({
                                mint: opts.mint,
                                predictions: d.predictions ?? d,
                                receipts_chain: "Each prediction's SHA256 leaf is anchored in the hourly merkle commits at /api/ledger/commits. The commit was published before any outcome was visible.",
                                verify_full_chain: "https://graduateoracle.fun/api/ledger/commits",
                            }, null, 2),
                        }],
                };
            }
            // prediction_id path — call the ledger proof endpoint
            const d = await api.get(`/api/ledger/proof/${encodeURIComponent(String(opts.prediction_id))}`, { withAuth: false });
            return {
                content: [{
                        type: "text",
                        text: JSON.stringify({
                            prediction_id: opts.prediction_id,
                            proof: d,
                            note: "This proof shows the prediction was committed to the hourly merkle root BEFORE the outcome was observable. Anyone can verify the merkle path independently.",
                        }, null, 2),
                    }],
            };
        }
        catch (err) {
            if (err instanceof ApiError && err.status === 404) {
                return {
                    content: [{
                            type: "text",
                            text: `No prediction found for ${opts.mint ? `mint ${opts.mint}` : `id ${opts.prediction_id}`}. Either the prediction was made outside our observation window or the identifier is incorrect.`,
                        }],
                };
            }
            const msg = err instanceof ApiError
                ? `verify_prediction API error (${err.status}): ${err.message}`
                : `verify_prediction failed: ${err.message}`;
            return { content: [{ type: "text", text: msg }] };
        }
    },
};
