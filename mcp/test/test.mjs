// End-to-end test harness for goracle-mcp.
//
// Spawns dist/index.js as a subprocess, talks MCP JSON-RPC over stdio,
// calls every tool with realistic inputs, validates response shapes,
// tests error paths, prints a pass/fail report.
//
// Run with:  node test/test.mjs
//
// Reads API key from GORACLE_API_KEY env or from ~/.config/goracle/...
// (same lookup as the server itself). Most tools need a key; the script
// will skip auth-requiring tests with a warning if no key is configured.

import { spawn } from "node:child_process";
import { readFileSync, existsSync } from "node:fs";
import { homedir, platform } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const SERVER_PATH = join(__dirname, "..", "dist", "index.js");

// ── ANSI colors ────────────────────────────────────────────────────────
const GREEN = "\x1b[32m", RED = "\x1b[31m", YELLOW = "\x1b[33m";
const CYAN = "\x1b[36m", DIM = "\x1b[2m", RESET = "\x1b[0m", BOLD = "\x1b[1m";

let passed = 0, failed = 0, skipped = 0;
const failures = [];

function ok(name)        { passed++; console.log(`  ${GREEN}✓${RESET} ${name}`); }
function fail(name, why) { failed++; failures.push({ name, why }); console.log(`  ${RED}✗${RESET} ${name}\n    ${RED}${why}${RESET}`); }
function skip(name, why) { skipped++; console.log(`  ${YELLOW}-${RESET} ${name} ${DIM}(skipped: ${why})${RESET}`); }
function section(t)      { console.log(`\n${BOLD}${CYAN}━━━ ${t} ━━━${RESET}`); }

// ── Check for API key (some tools need it) ────────────────────────────
function findCliKey() {
  if (process.env.GORACLE_API_KEY) return { source: "env", key: process.env.GORACLE_API_KEY };
  const home = homedir();
  const p = platform();
  const candidates = [];
  if (p === "darwin") {
    candidates.push(join(home, "Library", "Preferences", "goracle-nodejs", "config.json"));
  } else if (p === "win32") {
    if (process.env.APPDATA) candidates.push(join(process.env.APPDATA, "goracle-nodejs", "Config", "config.json"));
  } else {
    const xdg = process.env.XDG_CONFIG_HOME || join(home, ".config");
    candidates.push(join(xdg, "goracle-nodejs", "config.json"));
  }
  candidates.push(join(home, ".config", "goracle", "config.json"));
  for (const c of candidates) {
    if (existsSync(c)) {
      try {
        const d = JSON.parse(readFileSync(c, "utf-8"));
        if (d.apiKey) return { source: c, key: d.apiKey };
      } catch {}
    }
  }
  return null;
}

const cliKey = findCliKey();
const HAS_AUTH = !!cliKey;

console.log(`${BOLD}goracle-mcp end-to-end test${RESET}`);
console.log(`server: ${SERVER_PATH}`);
console.log(`auth:   ${HAS_AUTH ? `${GREEN}configured (${cliKey.source.replace(homedir(), "~")})${RESET}` : `${YELLOW}NONE — auth tests will be skipped${RESET}`}`);

// ── MCP client over stdio ──────────────────────────────────────────────
class McpClient {
  constructor(serverPath) {
    this.proc = spawn("node", [serverPath], {
      stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env },
    });
    this.buf = "";
    this.pending = new Map();
    this.nextId = 1;
    this.stderrLog = "";
    this.proc.stdout.on("data", (chunk) => this._onData(chunk.toString()));
    this.proc.stderr.on("data", (chunk) => { this.stderrLog += chunk.toString(); });
    this.proc.on("error", (err) => { console.error("server spawn error:", err); });
  }
  _onData(chunk) {
    this.buf += chunk;
    let idx;
    while ((idx = this.buf.indexOf("\n")) !== -1) {
      const line = this.buf.slice(0, idx).trim();
      this.buf = this.buf.slice(idx + 1);
      if (!line) continue;
      try {
        const msg = JSON.parse(line);
        if (msg.id !== undefined && this.pending.has(msg.id)) {
          const { resolve } = this.pending.get(msg.id);
          this.pending.delete(msg.id);
          resolve(msg);
        }
      } catch (e) {
        // ignore non-JSON output
      }
    }
  }
  _send(msg) {
    this.proc.stdin.write(JSON.stringify(msg) + "\n");
  }
  request(method, params = {}) {
    const id = this.nextId++;
    this._send({ jsonrpc: "2.0", id, method, params });
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`timeout waiting for ${method} (id=${id})`));
      }, 30_000);
      this.pending.set(id, { resolve: (r) => { clearTimeout(timer); resolve(r); }, reject });
    });
  }
  notify(method, params = {}) {
    this._send({ jsonrpc: "2.0", method, params });
  }
  async init() {
    const r = await this.request("initialize", {
      protocolVersion: "2024-11-05",
      capabilities: {},
      clientInfo: { name: "goracle-mcp-test", version: "0.1.0" },
    });
    if (!r.result?.serverInfo?.name) throw new Error("bad initialize response");
    this.notify("notifications/initialized");
    return r.result;
  }
  async callTool(name, args = {}) {
    const r = await this.request("tools/call", { name, arguments: args });
    return r;
  }
  async listTools() {
    const r = await this.request("tools/list", {});
    return r.result?.tools ?? [];
  }
  close() {
    this.proc.kill("SIGTERM");
    setTimeout(() => { try { this.proc.kill("SIGKILL"); } catch {} }, 500);
  }
}

// ── Helpers to inspect tool responses ──────────────────────────────────
function getText(res) {
  return res?.result?.content?.[0]?.text ?? null;
}
function isError(res) {
  return res?.error !== undefined;
}
function parseJsonContent(res) {
  const text = getText(res);
  if (!text) return null;
  try { return JSON.parse(text); }
  catch { return null; }
}

// ── The tests ──────────────────────────────────────────────────────────
async function main() {
  const client = new McpClient(SERVER_PATH);

  section("Server initialize + handshake");
  try {
    const info = await client.init();
    if (info.serverInfo.name === "goracle-mcp") ok("initialize returns correct serverInfo");
    else fail("initialize", `unexpected serverInfo: ${JSON.stringify(info.serverInfo)}`);
    if (info.capabilities?.tools) ok("capabilities.tools advertised");
    else fail("capabilities", "tools capability missing");
  } catch (err) {
    fail("initialize", err.message);
    client.close();
    return finish();
  }

  section("tools/list");
  try {
    const tools = await client.listTools();
    const expected = ["product_overview", "accuracy_receipts", "live_mints", "probe_mint",
                      "recent_graduations", "verify_prediction", "check_account"];
    const got = tools.map(t => t.name);
    if (tools.length === 7) ok(`returns 7 tools (got: ${tools.length})`);
    else fail("tools/list count", `expected 7, got ${tools.length}: ${got.join(", ")}`);
    for (const name of expected) {
      if (got.includes(name)) ok(`includes ${name}`);
      else fail(`tool ${name}`, "missing from list");
    }
    // Each tool has valid input schema
    for (const t of tools) {
      if (t.inputSchema?.type === "object") ok(`${t.name}: inputSchema is object`);
      else fail(`${t.name} schema`, `not type=object`);
    }
  } catch (err) {
    fail("tools/list", err.message);
  }

  section("product_overview (no auth)");
  {
    const res = await client.callTool("product_overview", {});
    if (isError(res)) { fail("product_overview", JSON.stringify(res.error)); }
    else {
      const data = parseJsonContent(res);
      if (data && typeof data === "object") ok("returns JSON object");
      else fail("product_overview", "didn't return parseable JSON");
      if (data?.product) ok("has 'product' field");
      else fail("product_overview", "missing 'product' field");
      if (data?.tools_available) ok("has 'tools_available' field");
      else fail("product_overview", "missing 'tools_available' field");
      if (data?.pricing) ok("has 'pricing' field");
      else fail("product_overview", "missing 'pricing' field");
    }
  }

  section("accuracy_receipts (no auth — should hit prod)");
  {
    const res = await client.callTool("accuracy_receipts", {});
    if (isError(res)) { fail("accuracy_receipts", JSON.stringify(res.error)); }
    else {
      const data = parseJsonContent(res);
      if (!data) { fail("accuracy_receipts", "didn't return parseable JSON"); }
      else {
        if (data.last_30_days_hit_rate) ok("has last_30_days_hit_rate");
        else fail("accuracy_receipts", "missing last_30_days_hit_rate");
        if (data.median_runway_seconds) ok("has median_runway_seconds");
        else fail("accuracy_receipts", "missing median_runway_seconds");
        if (data.tier_runways_seconds) ok("has tier_runways_seconds");
        else fail("accuracy_receipts", "missing tier_runways_seconds");
        const pct = data.last_30_days_hit_rate?.pct;
        if (typeof pct === "number" && pct > 0 && pct <= 100) ok(`live hit_rate=${pct.toFixed(1)}% (sensible range)`);
        else fail("accuracy_receipts hit_rate", `got ${pct}`);
      }
    }
  }

  section("verify_prediction (no auth)");
  {
    // First with no input — should error gracefully via Zod refine
    const noArgs = await client.callTool("verify_prediction", {});
    if (isError(noArgs)) ok("rejects missing both mint and prediction_id");
    else fail("verify_prediction empty", "should have errored but returned content");

    // Probe a real recent mint via live_mints? Need auth for that. Try a known mint instead.
    // Use a fake-but-valid-format mint to test the 404 path
    const fakeMint = "FaKe11111111111111111111111111111111111pump";
    const r = await client.callTool("verify_prediction", { mint: fakeMint });
    if (isError(r)) {
      // Could legitimately error from API; that's also fine
      ok(`fake mint: server returned error (acceptable)`);
    } else {
      const text = getText(r);
      if (text && (text.includes("No prediction found") || text.includes("predictions"))) ok("fake mint returns sensible response");
      else fail("verify_prediction fake mint", `unexpected: ${text?.slice(0, 100)}`);
    }
  }

  if (!HAS_AUTH) {
    section("Auth-required tools — SKIPPED");
    skip("live_mints", "no API key");
    skip("probe_mint", "no API key");
    skip("recent_graduations", "no API key");
    skip("check_account", "no API key");
  } else {
    // Set the key as env var for the server subprocess to read
    // (already inherited from parent process.env)
    section("check_account (auth)");
    {
      const res = await client.callTool("check_account", {});
      if (isError(res)) {
        fail("check_account", JSON.stringify(res.error));
      } else {
        const text = getText(res);
        if (text?.includes("API key invalid") || text?.includes("API key not configured")) {
          fail("check_account", "server reports no key, but we have one configured locally — env may not have propagated");
        } else {
          const data = parseJsonContent(res);
          if (data?.tier !== undefined) ok(`returns tier: ${data.tier}`);
          else fail("check_account", "missing 'tier' field");
          if (data?.key_prefix) ok(`returns key_prefix: ${data.key_prefix}`);
          else fail("check_account", "missing 'key_prefix' field");
        }
      }
    }

    section("live_mints (auth)");
    let probedMint = null;
    {
      // default args
      const res = await client.callTool("live_mints", {});
      if (isError(res)) {
        fail("live_mints default", JSON.stringify(res.error));
      } else {
        const data = parseJsonContent(res);
        if (data?.mints !== undefined) ok(`returns 'mints' array (n=${data.mints.length})`);
        else fail("live_mints default", "missing 'mints' field");
        if (Array.isArray(data?.mints)) ok("mints is an array");
        if (data?.indexed_total) ok(`indexed_total surfaced: ${data.indexed_total.toLocaleString()}`);
        if (data?.filter) ok("returns the filter that was applied");
        // grab a mint for probe_mint test
        if (data?.mints?.[0]?.mint) probedMint = data.mints[0].mint;
      }
      // Tight filter (likely empty)
      const tight = await client.callTool("live_mints", {
        min_grad_prob: 0.99, limit: 5,
      });
      if (isError(tight)) fail("live_mints tight filter", JSON.stringify(tight.error));
      else { const d = parseJsonContent(tight); if (d) ok("tight filter call succeeded"); }
      // Invalid input (out-of-range threshold)
      const bad = await client.callTool("live_mints", { min_grad_prob: 2.0 });
      if (isError(bad)) ok("rejects min_grad_prob > 1");
      else fail("live_mints validation", "should have rejected min_grad_prob=2.0");
    }

    section("probe_mint (auth)");
    {
      // Invalid input: short
      const short = await client.callTool("probe_mint", { mint: "abc" });
      if (isError(short)) ok("rejects mint < 32 chars");
      else fail("probe_mint validation", "should have rejected short mint");
      // Try with a live mint from live_mints
      if (probedMint) {
        const r = await client.callTool("probe_mint", { mint: probedMint });
        if (isError(r)) fail("probe_mint real", JSON.stringify(r.error));
        else {
          const data = parseJsonContent(r);
          if (data?.mint) ok(`returns 'mint' field for ${probedMint.slice(0, 8)}…`);
          else fail("probe_mint real", "missing 'mint' field in response");
          if (data?.grad_prob !== undefined) ok(`returns 'grad_prob': ${data.grad_prob}`);
          else fail("probe_mint real", "missing 'grad_prob'");
          if (data?.runner_odds_from_now) ok("returns runner_odds_from_now block");
          else fail("probe_mint real", "missing runner_odds_from_now");
          if (data?.flags) ok("returns flags block");
          else fail("probe_mint real", "missing flags");
        }
      } else {
        skip("probe_mint real call", "no live mint available from live_mints");
      }
      // Test non-existent (well-formed) mint
      const fake = "FaKe11111111111111111111111111111111111pump";
      const r2 = await client.callTool("probe_mint", { mint: fake });
      if (isError(r2)) ok("non-existent mint: server returned error (acceptable)");
      else {
        const text = getText(r2);
        if (text && (text.includes("not currently tracked") || text.toLowerCase().includes("not found")))
          ok("non-existent mint: returned helpful 404 message");
        else
          fail("probe_mint fake mint", `unexpected response: ${text?.slice(0, 120)}`);
      }
    }

    section("recent_graduations (auth)");
    {
      const res = await client.callTool("recent_graduations", { window_hours: 24, limit: 10 });
      if (isError(res)) fail("recent_graduations", JSON.stringify(res.error));
      else {
        const data = parseJsonContent(res);
        if (data?.graduations !== undefined) ok(`returns 'graduations' array (n=${data.graduations.length})`);
        else fail("recent_graduations", "missing 'graduations' field");
        if (data?.window_hours === 24) ok("echoes window_hours");
        else fail("recent_graduations", "window_hours not echoed");
        // The data SHAPE — this is the risky one I wasn't sure about
        if (data?.graduations?.length > 0) {
          const first = data.graduations[0];
          if (first.mint) ok(`first row has 'mint' field`);
          else fail("recent_graduations shape", `first row missing 'mint': ${JSON.stringify(first).slice(0, 200)}`);
        } else {
          skip("recent_graduations shape check", "no graduations in window");
        }
      }
      // Edge: too-large window
      const tooBig = await client.callTool("recent_graduations", { window_hours: 999 });
      if (isError(tooBig)) ok("rejects window_hours > 168");
      else fail("recent_graduations validation", "should have rejected window_hours=999");
    }
  }

  section("Invalid tool name");
  {
    const r = await client.callTool("nonexistent_tool", {});
    if (isError(r)) ok("returns error for unknown tool name");
    else fail("nonexistent tool", "should have errored");
  }

  client.close();
  finish();
}

function finish() {
  console.log();
  console.log(`${BOLD}━━━ Summary ━━━${RESET}`);
  console.log(`  ${GREEN}passed:  ${passed}${RESET}`);
  console.log(`  ${RED}failed:  ${failed}${RESET}`);
  console.log(`  ${YELLOW}skipped: ${skipped}${RESET}`);
  if (failures.length) {
    console.log(`\n${BOLD}${RED}Failures:${RESET}`);
    for (const f of failures) console.log(`  • ${f.name}: ${f.why}`);
  }
  process.exit(failed > 0 ? 1 : 0);
}

main().catch((err) => {
  console.error(`\n${RED}fatal:${RESET}`, err);
  process.exit(1);
});
