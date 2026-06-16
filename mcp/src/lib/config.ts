// Config — reads the API key from env first, then the goracle CLI's saved
// config at ~/.config/goracle/config.json. Same key, two ways in. Lets
// users who already ran `npx goracle signup` use the MCP server without
// re-entering anything.

import { homedir, platform } from "node:os";
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";

const DEFAULT_API_BASE = "https://graduateoracle.fun";

interface CliConfig {
  apiKey?: string | null;
  keyPrefix?: string | null;
  tier?: string | null;
  apiBase?: string | null;
}

function findCliConfigPath(): string | null {
  // `conf` (the lib the CLI uses) picks platform-specific paths.
  // macOS: ~/Library/Preferences/goracle-nodejs/config.json
  // Linux: ~/.config/goracle-nodejs/config.json
  // Windows: %APPDATA%\goracle-nodejs\Config\config.json
  const home = homedir();
  const p = platform();
  const candidates: string[] = [];
  if (p === "darwin") {
    candidates.push(join(home, "Library", "Preferences", "goracle-nodejs", "config.json"));
  } else if (p === "win32") {
    if (process.env.APPDATA) {
      candidates.push(join(process.env.APPDATA, "goracle-nodejs", "Config", "config.json"));
    }
  } else {
    // linux / freebsd / etc.
    const xdg = process.env.XDG_CONFIG_HOME || join(home, ".config");
    candidates.push(join(xdg, "goracle-nodejs", "config.json"));
  }
  // Fallback: also check standard ~/.config/goracle/ for users who set it manually
  candidates.push(join(home, ".config", "goracle", "config.json"));
  for (const c of candidates) {
    if (existsSync(c)) return c;
  }
  return null;
}

function loadCliConfig(): CliConfig {
  const path = findCliConfigPath();
  if (!path) return {};
  try {
    const raw = readFileSync(path, "utf-8");
    return JSON.parse(raw) as CliConfig;
  } catch {
    return {};
  }
}

export function getApiKey(): string | null {
  // Env var wins. Then CLI config.
  const fromEnv = process.env.GORACLE_API_KEY?.trim();
  if (fromEnv) return fromEnv;
  const cli = loadCliConfig();
  if (cli.apiKey) return cli.apiKey;
  return null;
}

export function getApiBase(): string {
  const fromEnv = process.env.GORACLE_API_BASE?.trim();
  if (fromEnv) return fromEnv.replace(/\/+$/, "");
  const cli = loadCliConfig();
  if (cli.apiBase) return cli.apiBase.replace(/\/+$/, "");
  return DEFAULT_API_BASE;
}
