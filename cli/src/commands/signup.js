// signup — the magic command. Two paths:
//
//   1. SOL pay (default): POST /api/upgrade -> Solana Pay QR -> poll until
//      payment confirms -> save key.
//   2. Token-holder (--wallet <addr>): POST /api/signup/wallet/init -> open
//      browser to Phantom signMessage -> poll until signature verifies and
//      $GO balance crosses the tier threshold -> save key.

import chalk from "chalk";
import qrcode from "qrcode-terminal";
import { execSync } from "child_process";
import { api } from "../lib/api.js";
import { config } from "../lib/config.js";

const POLL_INTERVAL_MS = 5_000;
const POLL_MAX_MIN = 30;

export async function signup(tier, opts) {
  const plan = opts.plan || "monthly";
  tier = (tier || "builder").toLowerCase();

  // Token-holder path takes precedence over SOL pay if --wallet is set.
  if (opts.wallet) {
    return signupWithWallet(tier, opts.wallet.trim());
  }

  console.log();
  console.log(chalk.bold(`graduate-oracle signup — ${chalk.cyanBright(tier)} (${plan})`));
  console.log(chalk.gray("Creating payment intent…"));
  console.log();

  let intent;
  try {
    intent = await api.post("/api/upgrade", {
      tier, plan,
      email: opts.email || undefined,
    }, { withAuth: false });
  } catch (err) {
    console.error(chalk.red("✗ failed to create intent: ") + err.message);
    process.exit(1);
  }

  if (intent.status === "not_open") {
    console.error(chalk.yellow("⚠ purchasing is not open right now."));
    console.error(chalk.gray(intent.message));
    process.exit(2);
  }

  if (!intent.deeplink_solana_pay || !intent.memo) {
    console.error(chalk.red("✗ unexpected intent response — no Solana Pay deeplink."));
    console.error(JSON.stringify(intent, null, 2));
    process.exit(1);
  }

  // Stash the new key + intent BEFORE rendering the QR — if the user
  // closes the terminal mid-payment, `goracle resume` can pick it back up.
  if (intent.new_key) {
    config.setAll({
      apiKey: intent.new_key,
      keyPrefix: intent.new_key_prefix,
      tier: "free",
    });
  }
  config.set("lastIntent", { memo: intent.memo, tier, plan, createdAt: Date.now() });

  console.log(chalk.bold("Scan with Phantom (mobile)"));
  console.log(chalk.gray("Phantom → top-right QR icon → scan."));
  console.log();
  qrcode.generate(intent.deeplink_solana_pay, { small: true });
  console.log();
  console.log(chalk.bold("Or send manually:"));
  console.log(`  Amount:    ${chalk.cyanBright(intent.amount_sol + " SOL")}`);
  console.log(`  To wallet: ${chalk.cyan(intent.treasury_wallet)}`);
  console.log(`  Memo:      ${chalk.cyan(intent.memo)}`);
  console.log();

  if (intent.new_key) {
    console.log(chalk.yellow("⚠  Your API key (saved to ") + chalk.gray(config.path()) + chalk.yellow("):"));
    console.log("   " + chalk.bold(intent.new_key));
    console.log(chalk.gray("   Activates the moment payment confirms (~30s after broadcast)."));
    console.log();
  }

  console.log(chalk.gray(`Polling /api/upgrade/${intent.memo} every ${POLL_INTERVAL_MS / 1000}s…`));
  console.log(chalk.gray(`(Ctrl-C is safe — resume with: goracle resume)`));
  console.log();

  await pollUntilFulfilled(intent.memo);
}

async function pollUntilFulfilled(memo) {
  const deadline = Date.now() + POLL_MAX_MIN * 60_000;
  let lastStatus = null;
  while (Date.now() < deadline) {
    let st;
    try {
      st = await api.get(`/api/upgrade/${encodeURIComponent(memo)}`, { withAuth: false });
    } catch (err) {
      process.stdout.write(chalk.red(` poll error: ${err.message}\n`));
      await sleep(POLL_INTERVAL_MS);
      continue;
    }
    const status = st.status || (st.fulfilled ? "fulfilled" : "pending");
    if (status !== lastStatus) {
      process.stdout.write(chalk.gray(`  status → ${status}\n`));
      lastStatus = status;
    }
    if (status === "fulfilled" || st.fulfilled) {
      console.log();
      console.log(chalk.green.bold("✓ Payment confirmed."));
      console.log(`  Tier:   ${chalk.cyanBright(st.tier || "?")}`);
      if (st.tx_signature) {
        console.log(`  Tx:     ${chalk.gray(st.tx_signature)}`);
      }
      config.set("tier", st.tier || config.get("tier"));
      config.set("lastIntent", null);
      console.log();
      console.log(chalk.gray("Try it:"));
      console.log(`  $ goracle accuracy`);
      console.log(`  $ goracle probe <mint>`);
      console.log(`  $ goracle live --threshold 0.70`);
      return;
    }
    if (status === "expired") {
      console.error();
      console.error(chalk.red("✗ intent expired before payment landed."));
      console.error(chalk.gray("  Run `goracle signup " + (st.tier || "builder") + "` to try again."));
      process.exit(3);
    }
    await sleep(POLL_INTERVAL_MS);
  }
  console.error();
  console.error(chalk.yellow(`⚠ timed out after ${POLL_MAX_MIN} min. Run \`goracle resume\` once your payment lands.`));
  process.exit(4);
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }


// ── Token-holder signup ──────────────────────────────────────────────────
async function signupWithWallet(tier, wallet) {
  console.log();
  console.log(chalk.bold(`graduate-oracle signup — ${chalk.cyanBright("token holder")} (${tier})`));
  console.log(chalk.gray(`Linking wallet ${wallet.slice(0, 8)}…${wallet.slice(-4)}`));
  console.log();

  let init;
  try {
    init = await api.post("/api/signup/wallet/init",
                          { wallet, tier },
                          { withAuth: false });
  } catch (err) {
    console.error(chalk.red("✗ failed to start wallet link: ") + err.message);
    process.exit(1);
  }

  if (init.dormant) {
    console.log(chalk.yellow("⚠  $GO is not live yet."));
    console.log(chalk.gray("   " + init.dormant_note));
    console.log();
    console.log(chalk.gray("   Once the token launches, re-run:"));
    console.log(chalk.gray(`   $ goracle signup ${tier} --wallet ${wallet}`));
    process.exit(2);
  }

  console.log(chalk.bold("Opening your browser to sign with Phantom"));
  console.log("  " + chalk.cyanBright(init.browser_url));
  console.log();
  console.log(chalk.gray("If your browser didn't open, copy that URL manually."));
  console.log(chalk.gray(`Polling for signature every ${POLL_INTERVAL_MS / 1000}s… (Ctrl-C is safe)`));
  console.log();

  // Best-effort open. If it fails, the URL is already printed above.
  try { openBrowser(init.browser_url); } catch { /* user has the URL */ }

  await pollWalletLink(init.link_id, tier, wallet);
}


async function pollWalletLink(linkId, tier, wallet) {
  const deadline = Date.now() + POLL_MAX_MIN * 60_000;
  let lastStatus = null;
  while (Date.now() < deadline) {
    let st;
    try {
      st = await api.get(`/api/signup/wallet/${encodeURIComponent(linkId)}`,
                         { withAuth: false });
    } catch (err) {
      process.stdout.write(chalk.red(` poll error: ${err.message}\n`));
      await sleep(POLL_INTERVAL_MS);
      continue;
    }
    const status = st.status;
    if (status !== lastStatus) {
      process.stdout.write(chalk.gray(`  status → ${status}\n`));
      lastStatus = status;
    }
    if (status === "fulfilled") {
      console.log();
      console.log(chalk.green.bold("✓ Wallet linked."));
      console.log(`  Tier:    ${chalk.cyanBright(st.resolved_tier)} ${chalk.gray("(token-held)")}`);
      console.log(`  Balance: ${chalk.bold((st.balance_ui ?? 0).toLocaleString())} ${chalk.gray("$GO")}`);
      if (st.key) {
        config.setAll({
          apiKey:    st.key,
          keyPrefix: st.key_prefix,
          tier:      st.resolved_tier,
        });
        console.log(`  Key:     ${chalk.bold(st.key)}`);
        console.log(chalk.gray(`           saved to ${config.path()}`));
      } else if (st.key_expired_for_poll) {
        console.log(chalk.yellow("\n  ⚠ The plaintext key is no longer poll-visible (>5min since fulfillment)."));
        console.log(chalk.yellow("    Your key is still valid — but you'll need to recover it from the link's"));
        console.log(chalk.yellow("    fulfilled-at timestamp via support, or re-run signup."));
      }
      console.log();
      console.log(chalk.gray("Try it:"));
      console.log(`  $ goracle accuracy`);
      console.log(`  $ goracle probe <mint>`);
      console.log(`  $ goracle live --threshold 0.70`);
      return;
    }
    if (status === "insufficient_balance") {
      console.error();
      console.error(chalk.red("✗ Insufficient $GO balance to claim " + tier + "."));
      console.error(chalk.gray(`   Held:   ${(st.balance_ui ?? 0).toLocaleString()} $GO`));
      console.error(chalk.gray(`   Needed: ${(st.threshold_ui ?? 0).toLocaleString()} $GO`));
      console.error();
      console.error(chalk.gray("   Buy more $GO and re-run, or use SOL pay:"));
      console.error(chalk.gray(`   $ goracle signup ${tier}`));
      process.exit(3);
    }
    if (status === "invalid_signature") {
      console.error();
      console.error(chalk.red("✗ Signature verification failed."));
      console.error(chalk.gray("   This usually means the wallet that signed differs from the one"));
      console.error(chalk.gray("   you specified. Re-run with the correct address."));
      process.exit(3);
    }
    if (status === "expired") {
      console.error();
      console.error(chalk.red("✗ Link expired before signature submitted."));
      console.error(chalk.gray("   Re-run: goracle signup " + tier + " --wallet " + wallet));
      process.exit(3);
    }
    await sleep(POLL_INTERVAL_MS);
  }
  console.error(chalk.yellow(`\n⚠ timed out after ${POLL_MAX_MIN} min.`));
  process.exit(4);
}


function openBrowser(url) {
  const platform = process.platform;
  const cmd = platform === "darwin" ? `open` :
              platform === "win32"  ? `start ""` :
                                       `xdg-open`;
  try {
    execSync(`${cmd} ${JSON.stringify(url)}`, { stdio: "ignore" });
  } catch (_) {
    // Silent — caller already printed the URL.
  }
}
