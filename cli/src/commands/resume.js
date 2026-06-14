// resume — pick up a signup whose poll loop was interrupted (Ctrl-C, terminal
// closed, network drop). The intent is still in the server; we just need
// to start polling again.

import chalk from "chalk";
import { api } from "../lib/api.js";
import { config } from "../lib/config.js";

export async function resume() {
  const last = config.get("lastIntent");
  if (!last || !last.memo) {
    console.error(chalk.yellow("⚠  no in-flight intent to resume."));
    console.error(chalk.gray("   Run: ") + chalk.bold("goracle signup builder") + chalk.gray(" to start a fresh one."));
    process.exit(1);
  }

  console.log(chalk.bold(`Resuming intent for ${last.tier} (${last.plan})…`));
  console.log(chalk.gray(`  memo: ${last.memo}`));
  console.log();

  let st;
  try {
    st = await api.get(`/api/upgrade/${encodeURIComponent(last.memo)}`, { withAuth: false });
  } catch (err) {
    console.error(chalk.red("✗ ") + err.message);
    process.exit(1);
  }

  if (st.fulfilled || st.status === "fulfilled") {
    console.log(chalk.green.bold("✓ already fulfilled — tier active."));
    config.set("tier", st.tier || config.get("tier"));
    config.set("lastIntent", null);
    return;
  }
  if (st.status === "expired") {
    console.error(chalk.red("✗ intent expired."));
    config.set("lastIntent", null);
    process.exit(2);
  }

  // Delegate to the signup poll loop. Re-import to avoid a circular dep
  // at module load time.
  const { signup } = await import("./signup.js");
  // The intent endpoint above returned 'pending' — kick off polling by
  // re-running signup() with the same tier/plan. signup() will detect
  // the existing intent via config and resume polling without minting a
  // new key. (Today's behavior just re-creates an intent; the server is
  // idempotent on memo so worst case is one stale intent.)
  console.log(chalk.gray("Re-running signup poll…"));
  await signup(last.tier, { plan: last.plan });
}
