"""
Minimal agent wrapper · drop-in for LangChain / CrewAI / your-own-LLM-loop.

Wraps the $GRADUATE API in three plain Python functions that return
LLM-friendly summaries:

    get_top_graduating(n=5)   → "ranked list of mints near graduation"
    probe_mint(address)       → "explain this mint's score + signals"
    smart_money_top(n=10)     → "best wallets to follow on pump.fun"

Drop these into any agent framework as tools.
"""
import os
import requests

API_KEY = os.environ.get("GRADUATE_API_KEY", "grad_REPLACE_ME")
BASE    = "https://graduateoracle.fun/api/v1"
HEADERS = {"Authorization": f"Bearer {API_KEY}"}


def _short(addr: str) -> str:
    return addr[:6] + "…" + addr[-4:] if len(addr) > 14 else addr


def get_top_graduating(n: int = 5) -> str:
    """Top live mints by predicted graduation probability."""
    r = requests.get(f"{BASE}/live", headers=HEADERS,
                     params={"limit": n, "min_prob": 0.5}, timeout=10).json()
    mints = r.get("mints", [])[:n]
    if not mints:
        return "No live mints currently scoring above 50% probability."
    lines = [f"Top {len(mints)} mints by graduation probability:"]
    for m in mints:
        flags = ", ".join(m.get("bot_flags") or []) or "clean"
        lines.append(
            f"  {_short(m['mint'])}  "
            f"prob={m['grad_prob']*100:.0f}%  "
            f"vsol={m['current_vsol_sol']:.0f}  "
            f"age={int(m['age_s'])}s  "
            f"flags={flags}"
        )
    return "\n".join(lines)


def probe_mint(mint_address: str) -> str:
    """Detailed breakdown of any pump.fun mint."""
    r = requests.get(f"{BASE}/probe/{mint_address}", headers=HEADERS, timeout=10).json()
    if not r.get("found_in_live"):
        return f"{_short(mint_address)} is not currently being tracked (graduated, died, or never seen)."
    parts = [
        f"{_short(mint_address)}:",
        f"  graduation prob:  {r.get('grad_prob', 0)*100:.0f}% "
        f"({r.get('grad_n_graduated')}/{r.get('grad_neighbors')} comparable mints graduated)",
        f"  age:              {int(r['age_s'])}s",
        f"  vSOL / mult:      {r['current_vsol_sol']:.0f} / {r['current_mult']:.2f}×  "
        f"(peak {r['max_mult']:.2f}×)",
        f"  buyers:           {r['unique_buyers']} unique  ·  "
        f"sell ratio {r.get('sell_ratio',0)*100:.0f}%  ·  "
        f"buys/wallet {r.get('buys_per_buyer',0):.1f}",
        f"  buyer quality:    top1={r.get('top_buyer_pct',0)*100:.0f}%  "
        f"unknown={r.get('unknown_buyer_pct',0)*100:.0f}%  "
        f"sniper={r.get('sniper_pct',0)*100:.0f}%",
    ]
    if r.get("bot_flags"):
        parts.append(f"  ⚠️ flags:          {', '.join(r['bot_flags'])}")
    if r.get("hide_reason"):
        parts.append(f"  hidden because:   {r['hide_reason']} (dashboard would not surface this)")
    return "\n".join(parts)


def smart_money_top(n: int = 10) -> str:
    """Best wallets ranked by (graduation rate − rug rate), sample-weighted."""
    r = requests.get(f"{BASE}/wallets/leaderboard", headers=HEADERS,
                     params={"kind": "smart", "limit": n, "min_total": 8}, timeout=10).json()
    wallets = r.get("wallets", [])[:n]
    if not wallets:
        return "No wallets currently meet the minimum sample threshold."
    lines = [f"Top {len(wallets)} smart-money wallets:"]
    for w in wallets:
        lines.append(
            f"  {_short(w['wallet'])}  "
            f"smart={w['smart_score']:+.2f}  "
            f"grads={w['graduated']}/{w['total']}  "
            f"good_rate={w['good_rate']*100:.0f}%"
        )
    return "\n".join(lines)


# Example usage (works as a script too):
if __name__ == "__main__":
    if API_KEY == "grad_REPLACE_ME":
        print("Set GRADUATE_API_KEY. Free key: https://graduateoracle.fun/api")
        raise SystemExit(1)
    print("=" * 50, "\nTOP GRADUATING\n", "=" * 50)
    print(get_top_graduating(5))
    print("\n", "=" * 50, "\nSMART MONEY\n", "=" * 50)
    print(smart_money_top(10))
