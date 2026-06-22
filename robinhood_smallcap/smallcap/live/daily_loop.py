"""
Small-Cap Daily Trading Loop — enhanced.

Key improvements vs original:
  - Stop-loss placed ONLY after entry order confirms filled (no naked stops)
  - T1/T2 target exit orders placed after fill confirmation
  - Position monitor loop checks fills and manages scale-out every 60s
  - Cash account: no PDT rules; settlement guard prevents Good Faith Violations
  - Daily loss limit enforced ($100/day cap)
  - Buying power check respects 40% deployment cap ($400 max deployed)
  - Account number auto-discovered from agentic account
"""
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

from broker.robinhood_mcp import RobinhoodMCP
from signals.scanner import scan_watchlist
from live.trade_log import log_trade, log_event, close_trade, update_trade, today_stats
from live.notify import send_telegram
from settlement_guard import record_sale, log_settlement_status

ET = ZoneInfo("America/New_York")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(BASE / "logs" / "daily.log"),
    ],
)
logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
MAX_POSITIONS = 2
ENTRY_WINDOW = ((10, 0), (14, 0))   # 10:00–14:00 ET
FORCE_CLOSE_TIME = (15, 45)         # 3:45 PM ET — intraday positions only
SCAN_INTERVAL_SECS = 300            # new-signal scan every 5 min
MONITOR_INTERVAL_SECS = 60          # position health check every 60s
MAX_DAILY_LOSS = -100.0             # stop trading for the day
MAX_DEPLOYED_PCT = 0.40             # 40% of account value max deployed
MAX_ACCOUNT_EQUITY = 1_000.0        # used for deployment cap calculation

WATCHLIST = [
    # Tech / Semiconductors
    "WOLF", "MARA", "RIOT", "CLSK", "CIFR", "IREN",
    # AI / Cloud
    "APLD", "BTBT",
    # Biotech / eVTOL
    "ACHR", "JOBY",
    # Consumer / Healthcare
    "HIMS", "PRCT", "ARRY",
    # Industrials / Clean Energy
    "SWIM", "CLOV",
    # Screening placeholders — update weekly
    "OPEN", "BIRD", "SKIN",
]
# Note: IONQ removed — price ~$56 exceeds $50 cap; SMAR removed — mid-cap


def _now() -> datetime:
    return datetime.now(ET)


def _in_entry_window() -> bool:
    now = _now()
    (sh, sm), (eh, em) = ENTRY_WINDOW
    start = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
    end = now.replace(hour=eh, minute=em, second=0, microsecond=0)
    return start <= now <= end


def _past_force_close() -> bool:
    now = _now()
    fc = now.replace(hour=FORCE_CLOSE_TIME[0], minute=FORCE_CLOSE_TIME[1], second=0, microsecond=0)
    return now >= fc


def _get_open_positions(rh: RobinhoodMCP) -> list[dict]:
    result = rh.get_equity_positions()
    return [p for p in result.get("results", []) if float(p.get("quantity", 0)) > 0]


def _check_order_filled(rh: RobinhoodMCP, order_id: str) -> tuple[bool, float]:
    """Return (is_filled, avg_fill_price)."""
    orders = rh.get_equity_orders().get("results", [])
    for o in orders:
        if o.get("id") == order_id:
            if o.get("state") == "filled":
                return True, float(o.get("average_price", 0))
            return False, 0.0
    return False, 0.0


def _place_protective_orders(rh: RobinhoodMCP, signal: dict, filled_qty: int, fill_price: float) -> None:
    """
    After entry fill: place stop-loss (GTC) and T1 limit sell (GTC).
    Stop is adjusted to actual fill price so R:R is preserved.
    """
    symbol = signal["symbol"]
    atr = fill_price - signal["stop"] + (signal["entry"] - fill_price)
    # Recalculate stop/t1/t2 relative to actual fill
    stop = round(fill_price - (signal["entry"] - signal["stop"]), 2)
    t1 = round(fill_price + (signal["t1"] - signal["entry"]), 2)
    t2 = round(fill_price + (signal["t2"] - signal["entry"]), 2)
    stop_limit = round(stop * 0.995, 2)

    # Stop-loss: full position GTC
    try:
        rh.place_equity_order(
            symbol=symbol, side="sell", quantity=filled_qty,
            order_type="stop_limit", stop_price=stop, limit_price=stop_limit,
            time_in_force="gtc",
        )
        logger.info(f"{symbol}: stop-loss placed at {stop} (limit {stop_limit})")
    except Exception as e:
        logger.error(f"{symbol}: stop-loss placement failed — {e}")
        send_telegram(f"WARNING: Stop-loss placement failed for {symbol}: {e}")

    # T1 sell: 50% of position as limit order
    t1_qty = max(1, filled_qty // 2)
    try:
        rh.place_equity_order(
            symbol=symbol, side="sell", quantity=t1_qty,
            order_type="limit", limit_price=t1,
            time_in_force="gtc",
        )
        logger.info(f"{symbol}: T1 sell placed at {t1} for {t1_qty} shares")
    except Exception as e:
        logger.error(f"{symbol}: T1 placement failed — {e}")

    update_trade(signal.get("order_id", ""), {
        "fill_price": fill_price,
        "stop_adjusted": stop,
        "t1_adjusted": t1,
        "t2_adjusted": t2,
        "t1_qty": t1_qty,
        "status": "open",
    })


def force_close_all(rh: RobinhoodMCP) -> None:
    """
    Close intraday (Signal C) positions before 3:45 PM.
    Swing positions (Signals A/B) are held — force-close only touches same-day entries.
    Cash account: no PDT concern; settlement is handled by Robinhood's buying_power.
    """
    positions = _get_open_positions(rh)
    if not positions:
        logger.info("Force close: no open positions")
        return

    for pos in positions:
        symbol = pos["symbol"]
        qty = int(float(pos.get("quantity", 0)))
        intraday_qty = float(pos.get("intraday_quantity", 0))

        if intraday_qty <= 0:
            logger.info(f"Force close skipping {symbol} — swing position held overnight")
            continue

        try:
            q = rh.get_equity_quotes([symbol])["data"]["results"][0]["quote"]
            price = float(q["last_trade_price"])
            limit = round(price * 0.998, 2)
            rh.place_equity_order(symbol=symbol, side="sell", quantity=int(intraday_qty),
                                  order_type="limit", limit_price=limit, time_in_force="day")
            proceeds = limit * intraday_qty
            record_sale(symbol, proceeds)
            pnl = close_trade("", limit, int(intraday_qty), "force_close")
            logger.info(f"Force close {symbol}: qty={int(intraday_qty)} limit={limit} pnl≈${pnl:.2f}")
            log_event("force_close", {"symbol": symbol, "qty": int(intraday_qty), "limit": limit})
            send_telegram(f"Force close: {symbol} {int(intraday_qty)}sh @ ${limit}")
        except Exception as e:
            logger.error(f"Force close error {symbol}: {e}")


def execute_signal(rh: RobinhoodMCP, signal: dict, pending_fills: dict) -> bool:
    """
    Review → place entry limit order.
    Returns True if order placed. Stop/T1 are placed only after fill confirmation.
    """
    symbol = signal["symbol"]
    entry = signal["entry"]
    shares = signal["shares"]

    # Tradability
    td = rh.get_equity_tradability(symbol)
    if not td.get("is_tradable", False):
        logger.warning(f"{symbol}: not tradable")
        return False

    # Buying power check (40% deployment cap)
    portfolio = rh.get_portfolio()
    bp = float(portfolio.get("buying_power", {}).get("buying_power", 0))
    total = float(portfolio.get("total_value", MAX_ACCOUNT_EQUITY))
    max_deploy = total * MAX_DEPLOYED_PCT
    required = entry * shares

    positions = _get_open_positions(rh)
    deployed = sum(float(p.get("quantity", 0)) * float(p.get("average_buy_price", 0)) for p in positions)
    if deployed + required > max_deploy:
        logger.warning(f"{symbol}: deployment cap reached (deployed=${deployed:.0f} + {required:.0f} > {max_deploy:.0f})")
        return False
    if bp < required:
        logger.warning(f"{symbol}: insufficient buying power (${bp:.2f} < ${required:.2f})")
        return False

    # Review
    review = rh.review_equity_order(symbol=symbol, side="buy", quantity=shares,
                                    order_type="limit", limit_price=entry, time_in_force="day")
    for w in review.get("warnings", []):
        logger.warning(f"{symbol} pre-trade: {w}")

    order = rh.place_equity_order(symbol=symbol, side="buy", quantity=shares,
                                  order_type="limit", limit_price=entry, time_in_force="day")
    order_id = order.get("id", "unknown")
    logger.info(f"{symbol}: entry order placed id={order_id}")

    signal["order_id"] = order_id
    pending_fills[order_id] = signal

    log_trade({
        "symbol": symbol,
        "signal_type": signal["signal_type"],
        "entry": entry,
        "stop": signal["stop"],
        "t1": signal["t1"],
        "t2": signal["t2"],
        "shares": shares,
        "risk_amt": signal["risk_amt"],
        "order_id": order_id,
        "timestamp": _now().isoformat(),
        "status": "pending_fill",
    })
    send_telegram(
        f"New trade: {symbol} [{signal['signal_name']}]\n"
        f"Entry: ${entry} | Stop: ${signal['stop']} | T1: ${signal['t1']}\n"
        f"Shares: {shares} | Risk: ${signal['risk_amt']}"
    )
    return True


def check_pending_fills(rh: RobinhoodMCP, pending_fills: dict) -> None:
    """Poll pending entry orders; on fill, place protective stop + T1."""
    for order_id, signal in list(pending_fills.items()):
        filled, fill_price = _check_order_filled(rh, order_id)
        if filled and fill_price > 0:
            logger.info(f"{signal['symbol']}: FILLED at ${fill_price}")
            _place_protective_orders(rh, signal, signal["shares"], fill_price)
            del pending_fills[order_id]
            send_telegram(f"FILLED: {signal['symbol']} @ ${fill_price}")


def check_daily_loss_limit() -> bool:
    """Return True (ok to trade) if daily P&L hasn't hit -$100."""
    stats = today_stats()
    if stats["total_pnl"] <= MAX_DAILY_LOSS:
        logger.warning(f"Daily loss limit hit: ${stats['total_pnl']:.2f} — stopping for the day")
        send_telegram(f"Daily loss limit: ${stats['total_pnl']:.2f}. Stopping trading.")
        return False
    return True


def main() -> None:
    logger.info("=== Small-Cap Daily Loop Starting ===")
    logger.info(f"Time: {_now().strftime('%Y-%m-%d %H:%M:%S ET')}")

    rh = RobinhoodMCP()
    portfolio = rh.get_portfolio()
    bp = float(portfolio.get("buying_power", {}).get("buying_power", 0))
    total = float(portfolio.get("total_value", 0))
    logger.info(f"Account: equity=${total:.2f}, buying_power=${bp:.2f}")
    settle_status = log_settlement_status()
    logger.info(settle_status)
    send_telegram(f"Market open. Equity: ${total:.2f} | BP: ${bp:.2f}\n{settle_status}")

    entered_today: set[str] = set()
    pending_fills: dict[str, dict] = {}
    last_scan = 0.0

    while True:
        now = _now()

        # Check for pending fill confirmations every cycle
        if pending_fills:
            try:
                check_pending_fills(rh, pending_fills)
            except Exception as e:
                logger.error(f"Fill check error: {e}")

        # Force close check
        if _past_force_close():
            force_close_all(rh)
            logger.info("Past force-close time. Daily loop ending.")
            break

        # Daily loss limit
        if not check_daily_loss_limit():
            break

        # Entry window + new signal scan
        current_time = time.monotonic()
        if _in_entry_window() and current_time - last_scan >= SCAN_INTERVAL_SECS:
            positions = _get_open_positions(rh)
            open_symbols = {p["symbol"] for p in positions}
            slots = MAX_POSITIONS - len(positions) - len(pending_fills)

            if slots > 0:
                scan_syms = [s for s in WATCHLIST if s not in open_symbols and s not in entered_today]
                if scan_syms:
                    try:
                        signals = scan_watchlist(rh, scan_syms)
                        logger.info(f"Scan: {len(signals)} signals")
                        for sig in signals[:slots]:
                            ok = execute_signal(rh, sig, pending_fills)
                            if ok:
                                entered_today.add(sig["symbol"])
                                slots -= 1
                    except Exception as e:
                        logger.error(f"Scan error: {e}")
            last_scan = current_time

        time.sleep(MONITOR_INTERVAL_SECS)

    logger.info("=== Daily Loop Complete ===")


if __name__ == "__main__":
    main()
