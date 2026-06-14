// probe — single mint score lookup. Requires API key.

import chalk from "chalk";
import { api } from "../lib/api.js";
import { config } from "../lib/config.js";
import { fmt } from "../lib/format.js";

export async function probe(mint, opts) {
  if (!config.get("apiKey")) {
    console.error(chalk.yellow("⚠  No API key configured."));
    console.error(chalk.gray("   Run: ") + chalk.bold("goracle signup builder"));
    process.exit(1);
  }
  if (!mint || mint.length < 32) {
    console.error(chalk.red("✗ mint looks invalid (expected 44-char base58, got " + (mint?.length || 0) + " chars)"));
    process.exit(1);
  }

  let result;
  try {
    result = await api.get(`/api/v1/probe/${encodeURIComponent(mint)}`);
  } catch (err) {
    if (err.status === 401) {
      console.error(chalk.red("✗ unauthorized — your key may have expired."));
      console.error(chalk.gray("   Run: ") + chalk.bold("goracle me") + chalk.gray(" to check tier."));
    } else if (err.status === 404) {
      console.error(chalk.yellow("⚠ mint not currently tracked (past 60s window or never seen)."));
    } else {
      console.error(chalk.red("✗ ") + err.message);
    }
    process.exit(1);
  }

  if (opts.json) {
    console.log(JSON.stringify(result, null, 2));
    return;
  }

  const m = result.mint || result;
  console.log();
  console.log(chalk.bold(`${fmt.shortMint(mint)}  `) + chalk.gray(mint));
  console.log();
  console.log(`  grad_prob:    ${fmt.probColor(m.grad_prob)} ${chalk.gray(`(age ${fmt.age(m.age_s)})`)}`);
  if (m.grad_prob_bucket) {
    console.log(`  bucket:       ${m.grad_prob_bucket}`);
  }
  console.log(`  current mult: ${chalk.bold(m.current_mult?.toFixed?.(2) ?? "—")}×`);
  console.log(`  vSOL:         ${fmt.sol(m.virtual_sol_reserves / 1e9, 2)}`);
  console.log(`  unique buys:  ${fmt.num(m.unique_buyers)}`);
  if (m.bundle_detected) console.log(chalk.yellow("  bundled:      yes"));
  if (m.manufactured_pump) console.log(chalk.yellow("  manufactured: yes"));
  if (m.dex_paid) console.log(chalk.cyan("  dex paid:     yes"));
  if (m.fee_delegated) console.log(chalk.cyan("  fee delegated: yes"));
  console.log();
}
