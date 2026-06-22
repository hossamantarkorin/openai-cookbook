"""
Cash Account Settlement Guard.

PDT rules do NOT apply here — account ••••4423 is a CASH account.
Cash accounts have a different constraint: Good Faith Violations (GFV).

A GFV occurs when you:
  1. Sell stock X → proceeds unsettled (T+2)
  2. Use those unsettled proceeds to buy stock Y
  3. Sell stock Y before the original proceeds from X settle

Three GFVs in 12 months → 90-day cash-only restriction.

Our defence: always check Robinhood's buying_power before entry.
Robinhood deducts unsettled funds from buying_power for cash accounts,
so a simple buying_power >= order_value check is sufficient.
We also log settlements so the EOD report can flag tight windows.
"""
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SETTLE_FILE = Path(__file__).parent / "logs" / "settlements.jsonl"
SETTLE_FILE.parent.mkdir(parents=True, exist_ok=True)
SETTLEMENT_DAYS = 2  # T+2

logger = logging.getLogger(__name__)


def _business_days_ahead(n: int, from_date: date | None = None) -> date:
    d = from_date or datetime.now(ET).date()
    added = 0
    while added < n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            added += 1
    return d


def record_sale(symbol: str, proceeds: float) -> date:
    """Log a sale; return settlement date (T+2 business days)."""
    settle_date = _business_days_ahead(SETTLEMENT_DAYS)
    entry = {
        "symbol": symbol,
        "proceeds": proceeds,
        "sale_date": datetime.now(ET).strftime("%Y-%m-%d"),
        "settle_date": settle_date.isoformat(),
    }
    with open(SETTLE_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
    logger.info(f"Sale recorded: {symbol} ${proceeds:.2f} settles {settle_date}")
    return settle_date


def unsettled_proceeds() -> float:
    """Return total proceeds from sales that haven't settled yet."""
    if not SETTLE_FILE.exists():
        return 0.0
    today = datetime.now(ET).date()
    total = 0.0
    with open(SETTLE_FILE) as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            settle = date.fromisoformat(rec["settle_date"])
            if settle > today:
                total += float(rec.get("proceeds", 0))
    return total


def log_settlement_status() -> str:
    pending = unsettled_proceeds()
    if pending > 0:
        return f"Unsettled proceeds: ${pending:.2f} (T+2 — avoid buying with these until settled)"
    return "All proceeds settled"
