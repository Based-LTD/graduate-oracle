"""
Personal Telegram alerter for $GRADUATE signals.

Polls /api/v1/live every 5 seconds. When a brand-new mint crosses your
configured probability threshold, sends a formatted message to your
Telegram chat with the contract, key metrics, and quick-buy links.

Setup
-----
1. Get a free $GRADUATE API key:           https://graduateoracle.fun/api
2. Talk to @BotFather to create a TG bot:  https://t.me/BotFather
   Save the bot token.
3. DM your bot any message, then visit:    https://api.telegram.org/bot<TOKEN>/getUpdates
   Read your chat_id from the response.
4. Export env vars:

    export GRADUATE_API_KEY=grad_xxx
    export TG_BOT_TOKEN=123:abc
    export TG_CHAT_ID=12345
    export THRESHOLD=0.80     # optional, default 0.80

5. Run:  python tg-alerter.py
"""
import os
import time
import requests

API_KEY     = os.environ.get("GRADUATE_API_KEY", "grad_REPLACE_ME")
TG_TOKEN    = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT     = os.environ.get("TG_CHAT_ID", "")
THRESHOLD   = float(os.environ.get("THRESHOLD", "0.80"))
POLL_S      = int(os.environ.get("POLL_S", "5"))

LIVE_URL = "https://graduateoracle.fun/api/v1/live"
HEADERS  = {"Authorization": f"Bearer {API_KEY}"}

alerted: set[str] = set()


def send(text: str):
    requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={"chat_id": TG_CHAT, "text": text, "parse_mode": "Markdown",
              "disable_web_page_preview": True},
        timeout=10,
    )


def fmt(m: dict) -> str:
    addr = m["mint"]
    flags = m.get("bot_flags") or []
    flag_line = f"\n⚠️ {' · '.join(flags)}" if flags else ""
    return (
        f"*{m['grad_prob']*100:.0f}% grad probability*\n"
        f"`{addr}`\n\n"
        f"vSOL: *{m['current_vsol_sol']:.0f}*  ·  "
        f"buyers: *{m['unique_buyers']}*  ·  "
        f"age: *{int(m['age_s'])}s*  ·  "
        f"mult: *{m['current_mult']:.2f}×*\n"
        f"sell ratio: {m.get('sell_ratio',0)*100:.0f}%  ·  "
        f"buys/wallet: {m.get('buys_per_buyer',0):.1f}"
        f"{flag_line}\n\n"
        f"[pump.fun](https://pump.fun/coin/{addr})  ·  "
        f"[axiom](https://axiom.trade/t/{addr})  ·  "
        f"[dexscreener](https://dexscreener.com/solana/{addr})"
    )


def main():
    if API_KEY == "grad_REPLACE_ME" or not TG_TOKEN or not TG_CHAT:
        print("Missing env vars. See script header for setup.")
        return
    print(f"tg-alerter · threshold={THRESHOLD}  poll={POLL_S}s  → chat {TG_CHAT}")
    while True:
        try:
            r = requests.get(LIVE_URL, headers=HEADERS,
                             params={"limit": 60, "min_prob": THRESHOLD}, timeout=10)
            r.raise_for_status()
            for m in r.json().get("mints", []):
                addr = m.get("mint")
                if not addr or addr in alerted: continue
                if m.get("is_suspect"):  # skip anything tagged for review
                    continue
                send(fmt(m))
                alerted.add(addr)
        except Exception as e:
            print(f"poll failed: {e}")
        time.sleep(POLL_S)


if __name__ == "__main__":
    main()
