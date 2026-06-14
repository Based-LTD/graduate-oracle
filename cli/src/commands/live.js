// live — streaming-ish view of currently-tracked mints. Polls /api/v1/live
// at the same cadence the dashboard does (~2s) and re-renders the table.

import chalk from "chalk";
import { api } from "../lib/api.js";
import { config } from "../lib/config.js";
import { fmt, table } from "../lib/format.js";

const POLL_INTERVAL_MS = 2_000;

export async function live(opts) {
  if (!config.get("apiKey")) {
    console.error(chalk.yellow("⚠  No API key configured."));
    console.error(chalk.gray("   Run: ") + chalk.bold("goracle signup builder"));
    process.exit(1);
  }

  const threshold = parseFloat(opts.threshold ?? "0");
  const maxMult   = parseFloat(opts.maxMult ?? "Infinity");
  const limit     = parseInt(opts.limit ?? "25", 10);
  const json      = !!opts.json;
  const once      = !!opts.once;

  if (json) {
    // Single fetch, dump JSON, exit. For piping to jq.
    try {
      const d = await api.get("/api/v1/live");
      console.log(JSON.stringify(d, null, 2));
    } catch (err) {
      console.error(chalk.red("✗ ") + err.message);
      process.exit(1);
    }
    return;
  }

  const handleInterrupt = () => {
    process.stdout.write("\n" + chalk.gray("(exited)\n"));
    process.exit(0);
  };
  process.on("SIGINT", handleInterrupt);

  async function tick() {
    let d;
    try {
      d = await api.get("/api/v1/live");
    } catch (err) {
      if (err.status === 401) {
        console.error(chalk.red("\n✗ unauthorized — key may have expired. Run `goracle me`."));
        process.exit(1);
      }
      console.error(chalk.red("\n✗ ") + err.message);
      return;
    }

    const mints = (d.mints || [])
      .filter(m => (m.grad_prob ?? 0) >= threshold)
      .filter(m => (m.current_mult ?? 0) <= maxMult)
      .sort((a, b) => (b.grad_prob ?? 0) - (a.grad_prob ?? 0))
      .slice(0, limit);

    // Clear screen + redraw. Plain ANSI; no full TUI lib.
    process.stdout.write("\x1B[2J\x1B[H");
    console.log(chalk.bold("graduate-oracle · live") +
      chalk.gray(`  tracked=${d.n_tracked_total ?? "?"}  indexed=${fmt.num(d.n_indexed_curves)}  snapshot=${fmt.age(d.snapshot_age_s)}`));
    console.log(chalk.gray(`filter: grad_prob ≥ ${threshold}, mult ≤ ${maxMult}, top ${limit}`));
    console.log();

    if (mints.length === 0) {
      console.log(chalk.gray("  no mints match the filter right now"));
    } else {
      const headers = ["grad", "mult", "vSOL", "buys", "age", "mint", "flags"];
      const rows = mints.map(m => {
        const flags = [];
        if (m.bundle_detected) flags.push(chalk.yellow("BNDL"));
        if (m.manufactured_pump) flags.push(chalk.yellow("MFG"));
        if (m.dex_paid) flags.push(chalk.cyan("DEX"));
        if (m.fee_delegated) flags.push(chalk.cyan("FEE"));
        return [
          fmt.probColor(m.grad_prob),
          (m.current_mult ?? 0).toFixed(2) + "×",
          ((m.virtual_sol_reserves ?? 0) / 1e9).toFixed(1),
          fmt.num(m.unique_buyers),
          fmt.age(m.age_s),
          fmt.shortMint(m.mint),
          flags.join(" "),
        ];
      });
      console.log(table(headers, rows));
    }
    console.log();
    console.log(chalk.gray(`refreshing every ${POLL_INTERVAL_MS / 1000}s · Ctrl-C to exit`));
  }

  await tick();
  if (once) return;
  const timer = setInterval(tick, POLL_INTERVAL_MS);
  // The interval keeps the process alive until SIGINT.
  await new Promise(() => {});  // never resolves; SIGINT handler exits.
}
