#!/usr/bin/env node
// goracle-mcp — MCP server for graduate-oracle. Runs as a stdio MCP
// server, ready to be installed into Claude Desktop, Cursor, Continue,
// or any MCP-aware AI agent.
//
// Install via Claude Desktop config:
//   "mcpServers": {
//     "graduate-oracle": {
//       "command": "npx",
//       "args": ["-y", "goracle-mcp"]
//     }
//   }

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
  ErrorCode,
  McpError,
} from "@modelcontextprotocol/sdk/types.js";
import { z } from "zod";
import { zodToJsonSchema } from "./lib/zod-to-json.js";

import { accuracy_receipts }   from "./tools/accuracy_receipts.js";
import { product_overview }    from "./tools/product_overview.js";
import { probe_mint }          from "./tools/probe_mint.js";
import { live_mints }          from "./tools/live_mints.js";
import { recent_graduations }  from "./tools/recent_graduations.js";
import { verify_prediction }   from "./tools/verify_prediction.js";
import { check_account }       from "./tools/check_account.js";

type ToolDef = {
  name: string;
  description: string;
  inputSchema: z.ZodTypeAny;
  handler: (args: unknown) => Promise<{ content: Array<{ type: "text"; text: string }> }>;
};

const TOOLS: ToolDef[] = [
  product_overview,
  accuracy_receipts,
  live_mints,
  probe_mint,
  recent_graduations,
  verify_prediction,
  check_account,
];

const server = new Server(
  {
    name: "goracle-mcp",
    version: "0.1.0",
  },
  {
    capabilities: {
      tools: {},
    },
  },
);

server.setRequestHandler(ListToolsRequestSchema, async () => {
  return {
    tools: TOOLS.map(t => ({
      name: t.name,
      description: t.description,
      inputSchema: zodToJsonSchema(t.inputSchema),
    })),
  };
});

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;
  const tool = TOOLS.find(t => t.name === name);
  if (!tool) {
    throw new McpError(ErrorCode.MethodNotFound, `Unknown tool: ${name}`);
  }
  try {
    return await tool.handler(args ?? {});
  } catch (err) {
    if (err instanceof z.ZodError) {
      throw new McpError(
        ErrorCode.InvalidParams,
        `Invalid arguments for ${name}: ${err.errors.map(e => `${e.path.join(".")} - ${e.message}`).join("; ")}`,
      );
    }
    throw new McpError(
      ErrorCode.InternalError,
      `${name} failed: ${(err as Error).message}`,
    );
  }
});

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  // Log to stderr so it doesn't pollute the MCP protocol on stdout
  console.error("[goracle-mcp] graduate-oracle MCP server running on stdio");
  console.error("[goracle-mcp] tools:", TOOLS.map(t => t.name).join(", "));
}

main().catch((err) => {
  console.error("[goracle-mcp] fatal:", err);
  process.exit(1);
});
