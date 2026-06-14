// Pretty printers used by every command. Single source of truth for
// number formatting, color choices, and table rendering.

import chalk from "chalk";

export const fmt = {
  pct(x, digits = 1) {
    if (x === null || x === undefined || Number.isNaN(x)) return "—";
    return (x * 100).toFixed(digits) + "%";
  },
  num(x) {
    if (x === null || x === undefined) return "—";
    return Number(x).toLocaleString();
  },
  sol(x, digits = 4) {
    if (x === null || x === undefined) return "—";
    return Number(x).toFixed(digits) + " SOL";
  },
  shortMint(m) {
    if (!m || m.length < 12) return m || "—";
    return `${m.slice(0, 6)}…${m.slice(-4)}`;
  },
  age(seconds) {
    if (seconds === null || seconds === undefined) return "—";
    if (seconds < 60) return Math.round(seconds) + "s";
    if (seconds < 3600) return Math.round(seconds / 60) + "m";
    if (seconds < 86400) return Math.round(seconds / 3600) + "h";
    return Math.round(seconds / 86400) + "d";
  },
  probColor(p) {
    if (p === null || p === undefined) return chalk.gray("—");
    const pct = (p * 100).toFixed(1) + "%";
    if (p >= 0.85) return chalk.greenBright.bold(pct);
    if (p >= 0.70) return chalk.green(pct);
    if (p >= 0.50) return chalk.yellow(pct);
    return chalk.gray(pct);
  },
};

// Tiny table renderer — column headers + rows. Avoids a dependency on
// cli-table3 (slow npm install, big footprint).
export function table(headers, rows) {
  const cols = headers.map(h => h.length);
  for (const row of rows) {
    row.forEach((cell, i) => {
      const len = stripAnsi(String(cell)).length;
      if (len > cols[i]) cols[i] = len;
    });
  }
  const sep = "  ";
  const header = headers.map((h, i) => chalk.bold(h.padEnd(cols[i]))).join(sep);
  const lines = [header];
  lines.push(headers.map((_, i) => "─".repeat(cols[i])).join(sep));
  for (const row of rows) {
    lines.push(row.map((cell, i) => {
      const s = String(cell);
      const visible = stripAnsi(s);
      return s + " ".repeat(Math.max(0, cols[i] - visible.length));
    }).join(sep));
  }
  return lines.join("\n");
}

// Strip ANSI escape codes for column-width calculation. Borrowed from the
// `ansi-regex` pattern but inlined to keep the dep tree small.
function stripAnsi(s) {
  return s.replace(/\x1B\[[0-9;]*[A-Za-z]/g, "");
}
