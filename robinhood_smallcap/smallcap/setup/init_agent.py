"""
One-time agent setup.

Creates an Anthropic cloud environment, a persistent trading agent, and
(optionally) a vault for the Robinhood OAuth credential.

TWO MODES:
─────────────────────────────────────────────────────────────────────────────
Mode A — No vault (default, try first)
  The Robinhood MCP server URL is declared on the agent. Anthropic may inject
  the OAuth credentials automatically because your Anthropic account already
  has a Robinhood connection via Claude Code's MCP integration. This is the
  cleanest path: ANTHROPIC_API_KEY is the only credential needed everywhere.

  python smallcap/setup/init_agent.py

Mode B — With vault (fallback if Mode A sessions get auth errors)
  Explicitly stores the Robinhood Bearer token in an Anthropic vault.
  How to get the token when Mode A fails:
    • Check Robinhood's website (not just app) for API/developer settings
    • Contact Robinhood support for Agentic Trading programmatic access
    • Inspect ~/.claude/ or Claude Code desktop credential store if you
      use the desktop Claude Code app (Mac: ~/Library/Application Support/Claude/)

  ROBINHOOD_API_TOKEN=<token> python smallcap/setup/init_agent.py --with-vault
─────────────────────────────────────────────────────────────────────────────

Outputs: smallcap/agent_config.json  (IDs only — no secrets in this file)
"""
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE = Path(__file__).parent.parent
load_dotenv(BASE.parent / ".env")

CONFIG_FILE = BASE / "agent_config.json"

ROBINHOOD_MCP_URL = os.getenv("ROBINHOOD_MCP_URL", "https://agent.robinhood.com/mcp/trading")

# ── System prompt ──────────────────────────────────────────────────────────────
# Encodes the full trading strategy so the agent can operate autonomously.
# Signal computation is done via the bash tool using pure-Python code to avoid
# dependency concerns (numpy/pandas not guaranteed in the cloud container).

SYSTEM_PROMPT = """\
You are a disciplined small-cap momentum trading agent for a $1,000 Robinhood cash account.

## Robinhood Account
Auto-discover the account number via get_accounts — use the account with agentic_allowed=true.

## Market Filter (check before any scan)
Skip all trading if ANY fails:
- VIX < 30  (use get_indexes → get_index_quotes)
- SPY day change > -1.5%
- IWM price > IWM 50-day SMA (fetch 120 days of daily bars, compute SMA)

## Universe
Price $5–$50, average daily volume > 500K shares, bid-ask spread < 0.5%.

## Signal Logic
Compute indicators via the bash tool. Use this Python snippet as a template:

```python
import json, math, sys

def ema(data, span):
    k = 2 / (span + 1)
    r = [data[0]]
    for x in data[1:]: r.append(x * k + r[-1] * (1 - k))
    return r

def atr14(highs, lows, closes):
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
    return sum(trs[-14:]) / 14

def rsi14(closes):
    diffs = [closes[i]-closes[i-1] for i in range(1, len(closes))]
    g = [max(0, d) for d in diffs[-14:]]
    l = [max(0, -d) for d in diffs[-14:]]
    ag, al = sum(g)/14, sum(l)/14
    return 100 - 100/(1 + ag/al) if al > 0 else 100

# Load data passed via stdin: {"bars": [...], "symbol": "...", "open_price": ..., "prev_close": ...}
data = json.load(sys.stdin)
symbol = data["symbol"]
bars = data["bars"]
bars.sort(key=lambda b: b.get("begins_at", ""))
opens  = [float(b.get("open_price",  b.get("open",  0))) for b in bars]
highs  = [float(b.get("high_price",  b.get("high",  0))) for b in bars]
lows   = [float(b.get("low_price",   b.get("low",   0))) for b in bars]
closes = [float(b.get("close_price", b.get("close", 0))) for b in bars]
vols   = [float(b.get("volume", 0)) for b in bars]
n = len(closes)

ema20  = ema(closes, 20)[-1]
ema12v = ema(closes, 12)
ema26v = ema(closes, 26)
macd_line = [ema12v[i] - ema26v[i] for i in range(n)]
macd_sig  = ema(macd_line, 9)
macd_hist = macd_line[-1] - macd_sig[-1]
prev_macd = macd_line[-2] - macd_sig[-2]
rsi   = rsi14(closes)
atr   = atr14(highs, lows, closes)
high20 = max(highs[-20:])
vol20  = sum(vols[-20:]) / 20
vr     = vols[-1] / vol20 if vol20 > 0 else 0
c = closes[-1]

MAX_RISK, MAX_POS, ATR_STOP, ATR_T1, ATR_T2 = 150.0, 200.0, 1.5, 2.0, 4.0

def size(entry, stop):
    r = entry - stop
    if r <= 0: return 0, 0.0
    sh = max(1, math.floor(MAX_RISK / r))
    sh = min(sh, math.floor(MAX_POS / entry))
    return sh, round(sh * r, 2)

sig = None

# Signal A: Volume Breakout Pullback
recent = list(zip(closes[-4:-1], vols[-4:-1], highs[-4:-1]))
bo = any(cl >= high20 * 0.995 and vl / vol20 >= 1.5 for cl, vl, _ in recent)
if bo:
    bo_high = max(cl for cl, vl, _ in recent if cl >= high20 * 0.995 and vl / vol20 >= 1.5)
    pb = (bo_high - c) / bo_high * 100
    if 2.0 <= pb <= 4.0 and c >= ema20 and 45 <= rsi <= 65:
        entry = round(c * 1.001, 2)
        stop = round(entry - atr * ATR_STOP, 2)
        sh, risk = size(entry, stop)
        if sh > 0:
            sig = {"signal_type": "A", "signal_name": "Volume Breakout Pullback",
                   "entry": entry, "stop": stop,
                   "t1": round(entry + atr*ATR_T1, 2), "t2": round(entry + atr*ATR_T2, 2),
                   "shares": sh, "risk_amt": risk, "rsi": round(rsi, 1), "confidence": "high"}

# Signal B: Oversold Bounce
if sig is None:
    pb_h = (high20 - c) / high20 * 100
    if (8.0 <= pb_h <= 15.0 and rsi < 35 and macd_hist < 0 and macd_hist > prev_macd
            and abs(c - ema20) / ema20 <= 0.05 and vr >= 1.2):
        entry = round(c * 1.001, 2)
        stop = round(entry - atr * ATR_STOP, 2)
        sh, risk = size(entry, stop)
        if sh > 0:
            sig = {"signal_type": "B", "signal_name": "Oversold Bounce",
                   "entry": entry, "stop": stop,
                   "t1": round(entry + atr*ATR_T1, 2), "t2": round(entry + atr*ATR_T2, 2),
                   "shares": sh, "risk_amt": risk, "rsi": round(rsi, 1), "confidence": "medium"}

# Signal C: Gap-and-Go
if sig is None:
    op = data.get("open_price", c)
    pc = data.get("prev_close", 0)
    if pc > 0:
        gap_pct = (op - pc) / pc * 100
        if gap_pct >= 3.0:
            gap_filled = (op - c) / (op - pc) * 100 if op != pc else 0
            vwap = sum(closes[-5:][i]*vols[-5:][i] for i in range(5)) / sum(vols[-5:])
            if gap_filled >= 30 and abs(c - vwap) / vwap <= 0.01:
                entry = round(c * 1.002, 2)
                stop = round(entry - atr * ATR_STOP, 2)
                sh, risk = size(entry, stop)
                if sh > 0:
                    sig = {"signal_type": "C", "signal_name": "Gap-and-Go",
                           "entry": entry, "stop": stop,
                           "t1": round(entry + atr*ATR_T1, 2), "t2": round(entry + atr*ATR_T2, 2),
                           "shares": sh, "risk_amt": risk, "gap_pct": round(gap_pct, 1),
                           "confidence": "medium", "intraday_only": True}

if sig: sig["symbol"] = symbol
print(json.dumps(sig))
```

## Workflow for Scan Tasks
For each symbol:
1. get_equity_quotes([symbol]) — check price ($5–$50), spread (<0.5%)
2. get_equity_historicals(symbol, interval="day", start_time=<400 days ago>)
3. Write the scanner snippet above to /tmp/scanner.py
4. Run: echo '<json_data>' | python3 /tmp/scanner.py
5. Parse the signal output

## Order Placement Rules
After confirming a signal:
1. review_equity_order — check for warnings
2. place_equity_order (buy, limit, DAY) — entry order only
3. After fill confirmed (check get_equity_orders):
   - place stop-loss: stop-limit GTC at (fill_price - entry_stop_diff), limit 0.5% below stop
   - place T1 sell: limit GTC at (fill_price + entry_t1_diff) for 50% of shares
4. NEVER place stop before fill confirmed

## Risk Controls
- Max 2 positions; max $200 per position; max risk $150
- Daily loss limit: -$100 (stop trading if hit)
- Max deployed: 40% of account ($400)
- No new entries after 2:00 PM ET
- Force-close all Signal C (intraday_only=true) positions before 3:45 PM ET

## Output Format
Always end your response with a JSON block:
```json
{
  "action": "scan|monitor|force_close|report",
  "market_ok": true,
  "signals_found": [],
  "orders_placed": [],
  "fills_confirmed": [],
  "stops_placed": [],
  "positions_closed": [],
  "daily_pnl_estimate": 0.00,
  "status": "ok|no_signals|market_filter_failed|daily_loss_limit",
  "notes": ""
}
```
"""


def main() -> None:
    try:
        import anthropic
    except ImportError:
        print("ERROR: anthropic package not found. Run: pip install anthropic")
        sys.exit(1)

    use_vault = "--with-vault" in sys.argv

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set.")
        sys.exit(1)

    token = os.getenv("ROBINHOOD_API_TOKEN", "")
    if use_vault and not token:
        print("ERROR: --with-vault requires ROBINHOOD_API_TOKEN in .env")
        print("See the module docstring for how to obtain the token.")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    print("=== SmallCap Agent Setup ===")
    print(f"Mode: {'vault (explicit token)' if use_vault else 'no-vault (Anthropic-managed auth)'}\n")

    # ── 1. Create cloud environment ──────────────────────────────────────────
    print("1. Creating cloud environment (unrestricted networking)...")
    env = client.beta.environments.create(
        name="robinhood-trading",
        config={"type": "cloud", "networking": {"type": "unrestricted"}},
    )
    print(f"   Environment: {env.id}")

    # ── 2. Optionally create vault ────────────────────────────────────────────
    vault_id = None
    if use_vault:
        print("2. Creating credentials vault...")
        vault = client.beta.vaults.create(name="robinhood-trading-vault")
        print(f"   Vault: {vault.id}")

        print("   Storing Robinhood MCP credential (static bearer)...")
        cred = client.beta.vaults.credentials.create(
            vault_id=vault.id,
            display_name="Robinhood Agentic Trading Token",
            auth={
                "type": "static_bearer",
                "mcp_server_url": ROBINHOOD_MCP_URL,
                "token": token,
            },
        )
        print(f"   Credential: {cred.id}")
        vault_id = vault.id
    else:
        print("2. Skipping vault (relying on Anthropic-managed Robinhood OAuth)")

    # ── 3. Create the trading agent ───────────────────────────────────────────
    print("3. Creating SmallCap Trading Agent...")
    agent = client.beta.agents.create(
        name="SmallCap Trading Agent",
        model="claude-opus-4-8",
        system=SYSTEM_PROMPT,
        mcp_servers=[
            {"type": "url", "name": "robinhood", "url": ROBINHOOD_MCP_URL},
        ],
        tools=[
            {
                "type": "agent_toolset_20260401",
                "default_config": {"enabled": True},
                "configs": [{"name": "web_search", "enabled": False}],
            },
            {"type": "mcp_toolset", "mcp_server_name": "robinhood"},
        ],
    )
    print(f"   Agent: {agent.id}  (version {agent.version})")

    # ── 4. Save config ────────────────────────────────────────────────────────
    config = {
        "environment_id": env.id,
        "vault_id": vault_id,        # None if no-vault mode
        "agent_id": agent.id,
        "agent_version": agent.version,
        "robinhood_mcp_url": ROBINHOOD_MCP_URL,
        "auth_mode": "vault" if use_vault else "anthropic_managed",
    }
    CONFIG_FILE.write_text(json.dumps(config, indent=2))
    print(f"\n4. Config saved → {CONFIG_FILE}")

    print("\n=== Setup Complete ===")
    if use_vault:
        print("✓ You can now REMOVE ROBINHOOD_API_TOKEN from .env")
    else:
        print("✓ No Robinhood token needed — Anthropic manages the OAuth connection")
    print("✓ Only ANTHROPIC_API_KEY is needed for daily operation")
    print(f"✓ Config file: {CONFIG_FILE}")
    if not use_vault:
        print("\nNOTE: If sessions get Robinhood auth errors, re-run with --with-vault")
        print("      once you obtain the token (see module docstring for paths).")


if __name__ == "__main__":
    main()
