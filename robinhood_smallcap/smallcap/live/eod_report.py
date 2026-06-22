"""EOD Report — runs at 4:15 PM ET after daily loop exits."""
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

from broker.robinhood_mcp import RobinhoodMCP
from live.trade_log import load_trades, today_stats
from live.notify import send_telegram

ET = ZoneInfo("America/New_York")


def main() -> None:
    rh = RobinhoodMCP()
    today = datetime.now(ET).strftime("%Y-%m-%d")

    portfolio = rh.get_portfolio()
    equity = float(portfolio.get("total_value", 0))
    bp = float(portfolio.get("buying_power", {}).get("buying_power", 0))

    orders = rh.get_equity_orders().get("results", [])
    filled_today = [o for o in orders
                    if o.get("last_transaction_at", "").startswith(today)
                    and o.get("state") == "filled"]

    stats = today_stats()
    wr = (stats["wins"] / stats["closed"] * 100) if stats["closed"] > 0 else 0

    msg = (
        f"EOD Report — {today}\n"
        f"Equity: ${equity:.2f} | BP: ${bp:.2f}\n"
        f"Trades: {stats['total']} | Filled orders: {len(filled_today)}\n"
        f"W/L: {stats['wins']}/{stats['losses']} ({wr:.0f}%) | P&L: ${stats['total_pnl']:+.2f}"
    )
    print(msg)
    send_telegram(msg)


if __name__ == "__main__":
    main()
