// Thin wrapper around zod-to-json-schema to produce MCP-compliant tool
// inputSchema objects. MCP expects JSON Schema; we author tool inputs as
// Zod for type safety + validation.
import { zodToJsonSchema as _convert } from "zod-to-json-schema";
export function zodToJsonSchema(schema) {
    const out = _convert(schema, { target: "openApi3" });
    // MCP tool schemas should be type=object at the top level. Most Zod
    // schemas we author with z.object() already produce this; defensive
    // patch in case a non-object slips in.
    if (out.type !== "object") {
        return { type: "object", properties: {}, additionalProperties: false };
    }
    return out;
}
