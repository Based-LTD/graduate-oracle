// accuracy — pretty-print the live receipts: median runway between our
// ≥0.70 call and the bonding curve completing, plus the 30-day hit rate.
// Mirrors the homepage hero. No auth required.

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
  const timing = h.time_to_grad || {};
  const traj = h.trajectory || [];

  if (opts.json) {
    console.log(JSON.stringify(h, null, 2));
    return;
  }

  console.log();
  console.log(chalk.bold("graduate-oracle · live receipts"));
  console.log(chalk.gray("predictions at age 30s or 60s, calibrated, ≥ 0.70 confidence"));
  console.log();

  console.log(chalk.bold("Last 30 days:"));
  if (timing.status === "ok" && timing.p50_s > 0) {
    const s = timing.p50_s;
    const sStr = s < 60 ? `${s}s` : `${Math.round(s/60)}m`;
    const under30 = (timing.under_30s_pct * 100).toFixed(0);
    console.log("  " + chalk.greenBright.bold("median runway: " + sStr) +
      chalk.gray("  (between our call and graduation completing)"));
    console.log("  " + chalk.gray(`${under30}% of graduations happen within 30s · n=${fmt.num(timing.n_grads)}`));
  }
  if (last30.status === "ok") {
    console.log("  " + chalk.bold("hit rate:      ") + fmt.probColor(last30.hit_rate) +
      chalk.gray(`  (of those calls graduate within 24h)`));
    console.log("  " + chalk.gray("resolved:      ") + fmt.num(last30.n_resolved) + " calls");
  } else {
    console.log(chalk.yellow("  hit rate: warming (insufficient sample)"));
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
  console.log(chalk.gray("Receipts:        https://graduateoracle.fun/accuracy"));
  console.log(chalk.gray("Pre-launch audit: https://graduateoracle.fun/verdict"));
}
