# goracle-mcp

> MCP server for **graduate-oracle** — real-time pump.fun graduation signal as a native tool in Claude, Cursor, Continue, and any MCP-aware AI agent.

Ship the LLM-native pump.fun signal layer into your trading agent in one config line.

## What it is

A [Model Context Protocol](https://modelcontextprotocol.io) server that exposes graduate-oracle's real-time pump.fun graduation signal as 7 tools your AI agent can call:

| Tool | What it does | Auth |
|---|---|---|
| `product_overview` | Introspect what graduate-oracle is + tool index | — |
| `accuracy_receipts` | Live calibration: runway, hit rate, tier breakdown | — |
| `live_mints` | Currently-tracked mints, filtered + sorted | API key |
| `probe_mint` | Full feature breakdown for one mint address | API key |
| `recent_graduations` | What graduated in the last N hours | API key |
| `verify_prediction` | Look up the SHA256 commitment for a past prediction | — |
| `check_account` | Tier, quota, expiry | API key |

**Receipts on top.** Every graduate-oracle prediction is hashed with a SHA256 commitment *before* the outcome is observable. Your agent can call `verify_prediction` to prove a claim before acting on it.

## Quick start

### 1. Install + run via npx

```bash
npx -y goracle-mcp
```

That's the server. It speaks stdio MCP. To wire it into an AI client:

### 2. Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "graduate-oracle": {
      "command": "npx",
      "args": ["-y", "goracle-mcp"],
      "env": {
        "GORACLE_API_KEY": "grad_xxxxxxxxxxxxxx"
      }
    }
  }
}
```

Restart Claude Desktop. The graduate-oracle tools appear in the tool list automatically.

### 3. Cursor

In `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "graduate-oracle": {
      "command": "npx",
      "args": ["-y", "goracle-mcp"],
      "env": {
        "GORACLE_API_KEY": "grad_xxxxxxxxxxxxxx"
      }
    }
  }
}
```

### 4. Continue

In `~/.continue/config.json`, add to `experimental.modelContextProtocolServers`:

```json
{
  "transport": {
    "type": "stdio",
    "command": "npx",
    "args": ["-y", "goracle-mcp"]
  },
  "env": {
    "GORACLE_API_KEY": "grad_xxxxxxxxxxxxxx"
  }
}
```

### 5. Don't have an API key yet?

Two options:

```bash
# Pay in SOL via terminal QR:
npx goracle signup builder

# Or link a $GO-holding wallet:
npx goracle signup builder --wallet <YOUR_SOLANA_ADDRESS>
```

The CLI saves the key to `~/.config/goracle/config.json` (or platform equivalent), which this MCP server reads automatically. Or set `GORACLE_API_KEY` in the env.

**The `accuracy_receipts` and `product_overview` tools work without any key** — try those first to validate the install.

## Why this matters for agents

Most pump.fun signal services give you:

- 🔔 "BUY NOW" alerts — an LLM can't reason over that
- ❌ Vague "high conviction" labels — no probability bands
- ❌ No way to verify claims before acting

What graduate-oracle gives your agent:

- ✅ **Structured JSON.** 20+ fields per mint — `grad_prob`, `smart_money_in`, `bundle_detected`, `creator_runner`, `runner_prob_5x_from_now`, etc.
- ✅ **Calibrated probabilities.** When the model says `0.70`, **91%** actually graduate. When it says `0.90`, **99%** do.
- ✅ **Hashed-before-outcome receipts.** Every prediction has a SHA256 commitment locked in *before* the outcome is observable. Your agent calls `verify_prediction` to validate before acting.
- ✅ **950,000+ historical corpus.** The largest verified pump.fun mint index on Solana.

## Example agent loop (psuedocode)

```
LOOP:
  mints = live_mints(min_grad_prob=0.85, exclude_bundled=true)
  FOR each mint IN mints:
    history = probe_mint(mint.address)
    IF history.creator_5x_rate > 0.20 AND history.smart_money_in >= 2:
      receipt = verify_prediction(mint=mint.address)
      IF receipt.committed_before_outcome:
        place_trade(mint.address, size=position_size(history.grad_prob))
  SLEEP 5s
```

That's an LLM trading agent in 10 lines. The agent never has to trust the signal — it verifies before acting.

## Configuration

| Env var | Purpose | Default |
|---|---|---|
| `GORACLE_API_KEY` | Bearer token for authed tools | reads from CLI config |
| `GORACLE_API_BASE` | Override base URL (for staging / self-host) | `https://graduateoracle.fun` |

## Tiers + pricing

| Tier | SOL price | $GO holder path | Calls/day | Tools available |
|---|---|---|---|---|
| Free / unauth | — | — | — | `accuracy_receipts`, `product_overview`, `verify_prediction` |
| `tg_paid` | 0.2 SOL/mo | hold 500,000 $GO | TG bot only | (TG signal access) |
| `builder` | 0.4 SOL/mo | hold 2,500,000 $GO | 5,000 | all 7 tools |
| `pro` | 1 SOL/mo | — | 50,000 | all 7 tools + webhooks |

Founding rate locks forever — your price never goes up.

## Links

- **Site:** https://graduateoracle.fun
- **Docs:** https://graduateoracle.fun/docs
- **Live receipts:** https://graduateoracle.fun/accuracy
- **Pre-launch audit / verdict:** https://graduateoracle.fun/verdict
- **CLI:** https://www.npmjs.com/package/goracle
- **GitHub:** https://github.com/Based-LTD/graduate-oracle
- **TG bot:** https://t.me/graduate_oracle_bot
- **X:** https://x.com/GraduateOracle

## License

MIT
