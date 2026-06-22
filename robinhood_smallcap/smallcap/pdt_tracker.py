"""
PDT (Pattern Day Trader) compliance tracker.

With a <$25K account, FINRA limits you to 3 day trades per rolling 5-business-day window.
A day trade = buying and selling the same security on the same day.
Force-close at 3:45 PM also counts as a day trade if entered today.
"""
import json
import logging
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
logger = logging.getLogger(__name__)

PDT_FILE = Path(__file__).parent / "logs" / "pdt.jsonl"
PDT_FILE.parent.mkdir(parents=True, exist_ok=True)

MAX_DAY_TRADES = 3


def _today() -> str:
    return datetime.now(ET).strftime("%Y-%m-%d")


def _load() -> list[dict]:
    if not PDT_FILE.exists():
        return []
    with open(PDT_FILE) as f:
        return [json.loads(line) for line in f if line.strip()]


def _business_days_ago(n: int) -> set[str]:
    """Return the last n business day date strings (Mon–Fri) including today."""
    days: list[str] = []
    d = datetime.now(ET).date()
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d.isoformat())
        from datetime import timedelta
        d -= timedelta(days=1)
    return set(days)


def record_day_trade(symbol: str) -> None:
    window = _business_days_ago(5)
    trades = [t for t in _load() if t["date"] in window]
    if len(trades) >= MAX_DAY_TRADES:
        logger.warning(f"PDT LIMIT: already {len(trades)} day trades this week — NOT recording {symbol}")
        return
    with open(PDT_FILE, "a") as f:
        f.write(json.dumps({"date": _today(), "symbol": symbol}) + "\n")
    logger.info(f"PDT: recorded day trade for {symbol} ({len(trades)+1}/{MAX_DAY_TRADES})")


def day_trades_remaining() -> int:
    window = _business_days_ago(5)
    trades = [t for t in _load() if t["date"] in window]
    return max(0, MAX_DAY_TRADES - len(trades))


def can_day_trade() -> bool:
    return day_trades_remaining() > 0
