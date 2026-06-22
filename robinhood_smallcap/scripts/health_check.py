"""
Health check — verifies Robinhood MCP connectivity and account status.
Run manually or via cron to confirm the bot is operational.
"""
import sys
from pathlib import Path

BASE = Path(__file__).parent.parent / "smallcap"
sys.path.insert(0, str(BASE))

from broker.robinhood_mcp import RobinhoodMCP


def main() -> None:
    print("Running health check…")
    rh = RobinhoodMCP()

    # 1. Account discovery
    acct = rh.account_number
    print(f"  Agentic account: ...{acct[-4:]}")

    # 2. Portfolio
    portfolio = rh.get_portfolio()
    equity = float(portfolio.get("total_value", 0))
    bp = float(portfolio.get("buying_power", {}).get("buying_power", 0))
    print(f"  Portfolio: equity=${equity:.2f}, buying_power=${bp:.2f}")

    # 3. Live quote
    q = rh.get_equity_quotes(["SPY"])["data"]["results"][0]["quote"]
    print(f"  SPY quote: ${float(q['last_trade_price']):.2f}")

    # 4. VIX
    try:
        vix = rh.get_vix()
        print(f"  VIX: {vix:.1f}")
    except Exception as e:
        print(f"  VIX: FAILED — {e}")

    # 5. Historicals
    raw = rh.get_equity_historicals("IWM", interval="day", days_back=10)
    results = raw.get("data", {}).get("results", [])
    n_bars = len(results[0].get("historicals", results)) if results else 0
    print(f"  IWM historicals: {n_bars} bars returned")

    print("Health check PASSED")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Health check FAILED: {e}")
        sys.exit(1)
