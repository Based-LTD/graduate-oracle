// accuracy — pretty-print the same headline the website hero shows.
// No auth required.

import chalk from "chalk";
import { api } from "../lib/api.js";
import { fmt } from "../lib/format.js";

export async function accuracy(opts) {
  let data;
  try {
    data = await api.get("/api/accuracy", { withAuth: false });
  } catch (err) {
    console.error(chalk.red("✗ ") + err.message);
    process.exit(1);
  }

  const h = data.headline || {};
  const last30 = h.last_30d || {};
  const lifetime = h.lifetime || {};
  const traj = h.trajectory || [];

  if (opts.json) {
    console.log(JSON.stringify(h, null, 2));
    return;
  }

  console.log();
  console.log(chalk.bold("graduate-oracle · live calibration"));
  console.log(chalk.gray("predictions at age 30s or 60s with grad_prob ≥ 0.70"));
  console.log();

  if (last30.status === "ok") {
    console.log(chalk.bold("Last 30 days:"));
    console.log("  hit rate: " + fmt.probColor(last30.hit_rate));
    console.log("  resolved: " + fmt.num(last30.n_resolved) + " calls");
    console.log("  graduated: " + fmt.num(last30.n_graduated));
  } else {
    console.log(chalk.yellow("  last 30d: warming (insufficient sample)"));
  }

  console.log();
  if (lifetime.status === "ok") {
    console.log(chalk.bold("Lifetime:"));
    console.log("  hit rate: " + fmt.probColor(lifetime.hit_rate));
    console.log("  resolved: " + fmt.num(lifetime.n_resolved) + " calls");
  }

  if (traj.length) {
    console.log();
    console.log(chalk.bold("Monthly trajectory:"));
    for (const t of traj) {
      console.log(`  ${t.month}: ${fmt.probColor(t.hit_rate)} ` + chalk.gray(`(n=${fmt.num(t.n)})`));
    }
  }

  console.log();
  console.log(chalk.gray("Receipts: https://graduateoracle.fun/accuracy"));
  console.log(chalk.gray("Pre-registered verdict: https://graduateoracle.fun/verdict"));
}
