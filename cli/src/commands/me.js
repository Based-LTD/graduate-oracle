// me — show key state, tier, expiry. Refreshes config from /api/me.

import chalk from "chalk";
import { api } from "../lib/api.js";
import { config } from "../lib/config.js";

export async function me(opts) {
  if (!config.get("apiKey")) {
    console.error(chalk.yellow("⚠  No API key configured."));
    console.error(chalk.gray("   Run: ") + chalk.bold("goracle signup builder"));
    process.exit(1);
  }

  let info;
  try {
    info = await api.get("/api/me");
  } catch (err) {
    if (err.status === 401) {
      console.error(chalk.red("✗ key invalid or expired."));
      console.error(chalk.gray("   Run: ") + chalk.bold("goracle signup builder") + chalk.gray(" to renew."));
      process.exit(1);
    }
    console.error(chalk.red("✗ ") + err.message);
    process.exit(1);
  }

  if (opts.json) {
    console.log(JSON.stringify(info, null, 2));
    return;
  }

  config.setAll({
    tier:      info.tier,
    expiresAt: info.expires_at,
  });

  console.log();
  console.log(chalk.bold("graduate-oracle account"));
  console.log("  key:        " + chalk.gray(config.get("keyPrefix") || info.prefix || "?"));
  console.log("  tier:       " + chalk.cyanBright(info.tier || "?"));
  if (info.expires_at) {
    const days = Math.max(0, Math.floor((info.expires_at - Date.now() / 1000) / 86400));
    const color = days > 7 ? chalk.green : days > 0 ? chalk.yellow : chalk.red;
    console.log("  expires in: " + color(`${days}d`));
  } else {
    console.log("  expires:    " + chalk.gray("— (token-holder / never)"));
  }
  if (info.calls_today !== undefined) {
    console.log("  calls today: " + chalk.bold(info.calls_today.toLocaleString()) + chalk.gray(` / ${info.daily_limit?.toLocaleString?.() ?? "?"}`));
  }
  console.log(chalk.gray("  config: " + config.path()));
  console.log();
}
