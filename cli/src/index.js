#!/usr/bin/env node
// goracle — graduate-oracle CLI. Sign up, pay, and use the pump.fun
// graduation prediction API from your terminal.
//
// $ npx goracle signup builder
// $ goracle probe <mint>
// $ goracle live --threshold 0.70
// $ goracle accuracy

import { Command } from "commander";
import chalk from "chalk";

import { signup }   from "./commands/signup.js";
import { probe }    from "./commands/probe.js";
import { live }     from "./commands/live.js";
import { accuracy } from "./commands/accuracy.js";
import { me }       from "./commands/me.js";
import { resume }   from "./commands/resume.js";
import { config }   from "./lib/config.js";

const program = new Command();

program
  .name("goracle")
  .description(chalk.bold("graduate-oracle") + chalk.gray(" — pump.fun graduation prediction API, from your terminal."))
  .version("0.2.0");

program
  .command("signup")
  .description("Sign up for a tier. Pay in SOL (default) OR link a $GO-holding wallet with --wallet.")
  .argument("[tier]", "tg_paid | builder | pro", "builder")
  .option("--plan <plan>", "monthly | yearly (SOL pay only)", "monthly")
  .option("--email <email>", "optional, for renewal reminders (SOL pay only)")
  .option("--wallet <addr>",
          "skip SOL pay; link a Solana wallet that holds $GO (hold 500k for tg_paid, 2.5M for builder)")
  .action(signup);

program
  .command("resume")
  .description("Resume an interrupted signup poll.")
  .action(resume);

program
  .command("probe <mint>")
  .description("Score a single mint. Requires API key.")
  .option("--json", "raw JSON output")
  .action(probe);

program
  .command("live")
  .description("Stream all currently-tracked mints, filtered + sorted. Requires API key.")
  .option("-t, --threshold <number>", "min grad_prob (0.0–1.0)", "0")
  .option("-m, --max-mult <number>",  "max current_mult (entry quality)", "999")
  .option("-n, --limit <number>",     "max rows", "25")
  .option("--once",  "fetch once and exit (no streaming)")
  .option("--json",  "raw JSON output (single fetch)")
  .action(live);

program
  .command("accuracy")
  .description("Show the live calibration receipt (no key required).")
  .option("--json", "raw JSON output")
  .action(accuracy);

program
  .command("me")
  .description("Show your key, tier, and expiry.")
  .option("--json", "raw JSON output")
  .action(me);

program
  .command("config")
  .description("Show config file path and current settings.")
  .option("--clear", "wipe all saved settings (including your API key)")
  .option("--set <key=value>", "override a config field (e.g. --set apiBase=https://staging.graduateoracle.fun)")
  .action((opts) => {
    if (opts.clear) {
      config.clear();
      console.log(chalk.yellow("⚠  config wiped. Run `goracle signup builder` to start over."));
      return;
    }
    if (opts.set) {
      const idx = opts.set.indexOf("=");
      if (idx === -1) {
        console.error(chalk.red("✗ expected key=value"));
        process.exit(1);
      }
      const k = opts.set.slice(0, idx);
      const v = opts.set.slice(idx + 1);
      config.set(k, v);
      console.log(chalk.green(`✓ set ${k} = ${v}`));
      return;
    }
    console.log();
    console.log(chalk.bold("config file:"));
    console.log("  " + chalk.gray(config.path()));
    console.log();
    for (const key of ["apiBase", "tier", "keyPrefix", "expiresAt"]) {
      console.log(`  ${key}: ${config.get(key) ?? chalk.gray("(unset)")}`);
    }
    if (config.get("apiKey")) {
      console.log(`  apiKey: ${chalk.gray("(saved, " + config.get("keyPrefix") + "…)")}`);
    } else {
      console.log("  apiKey: " + chalk.gray("(unset — run `goracle signup`)"));
    }
    console.log();
  });

// Global error handler so unhandled rejections don't dump stack traces.
process.on("unhandledRejection", (err) => {
  console.error(chalk.red("\n✗ unhandled error: ") + (err?.message || err));
  process.exit(1);
});

program.parseAsync().catch((err) => {
  console.error(chalk.red("✗ ") + (err?.message || err));
  process.exit(1);
});
