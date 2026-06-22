"""
Small-Cap Daily Trading Loop — Managed Agents edition.

Architecture change: all Robinhood API calls now go through an Anthropic Managed
Agent session (vault holds the Robinhood OAuth token). Python handles only:
  - Scheduling and loop timing
  - Trade logging (parse JSON from agent)
  - Force-close timing
  - Daily loss limit gate
  - Telegram alerts

No ROBINHOOD_API_TOKEN needed here — only ANTHROPIC_API_KEY.
Run smallcap/setup/init_agent.py once to bootstrap the vault/agent.
"""
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

from broker.trading_session import TradingSession
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
FORCE_CLOSE_TIME = (15, 45)         # 3:45 PM ET
SCAN_INTERVAL_SECS = 300            # new-signal scan every 5 min
MONITOR_INTERVAL_SECS = 60          # fill/position check every 60s
MAX_DAILY_LOSS = -100.0
TASK_TIMEOUT_SECS = 240

WATCHLIST = [
    # Tech / Crypto miners
    "WOLF", "MARA", "RIOT", "CLSK", "CIFR", "IREN",
    # AI / Cloud
    "APLD", "BTBT",
    # eVTOL / Biotech
    "ACHR", "JOBY",
    # Consumer / Healthcare
    "HIMS", "PRCT", "ARRY",
    # Industrials / Clean Energy
    "SWIM", "CLOV",
    # Screening placeholders — update weekly
    "OPEN", "BIRD", "SKIN",
]


def _now() -> datetime:
    return datetime.now(ET)


def _in_entry_window() -> bool:
    now = _now()
    (sh, sm), (eh, em) = ENTRY_WINDOW
    start = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
    end   = now.replace(hour=eh, minute=em, second=0, microsecond=0)
    return start <= now <= end


def _past_force_close() -> bool:
    now = _now()
    fc = now.replace(hour=FORCE_CLOSE_TIME[0], minute=FORCE_CLOSE_TIME[1], second=0, microsecond=0)
    return now >= fc


def _check_daily_loss() -> bool:
    """Return True (ok to trade) if daily P&L hasn't hit -$100."""
    stats = today_stats()
    if stats["total_pnl"] <= MAX_DAILY_LOSS:
        msg = f"Daily loss limit hit: ${stats['total_pnl']:.2f} — stopping"
        logger.warning(msg)
        send_telegram(msg)
        return False
    return True


def _log_orders_from_result(result: dict, entered_today: set) -> None:
    """Persist any orders_placed reported by the agent to trade_log.py."""
    for order in result.get("orders_placed", []):
        symbol = order.get("symbol", "")
        entered_today.add(symbol)
        log_trade({
            "symbol": symbol,
            "signal_type": order.get("signal_type", "?"),
            "entry": order.get("price", 0),
            "stop": order.get("stop", 0),
            "t1": order.get("t1", 0),
            "t2": order.get("t2", 0),
            "shares": order.get("quantity", 0),
            "risk_amt": order.get("risk_amt", 0),
            "order_id": order.get("order_id", ""),
            "timestamp": _now().isoformat(),
            "status": "pending_fill",
        })
        send_telegram(
            f"New trade: {symbol} [{order.get('signal_name', '')}]\n"
            f"Entry: ${order.get('price', 0)} | Stop: ${order.get('stop', 0)} | "
            f"T1: ${order.get('t1', 0)}\n"
            f"Shares: {order.get('quantity', 0)} | Risk: ${order.get('risk_amt', 0)}"
        )
        logger.info(f"Logged order: {symbol} id={order.get('order_id')}")


def _log_fills_from_result(result: dict) -> None:
    """Update trade log when agent reports fills."""
    for fill in result.get("fills_confirmed", []):
        order_id = fill.get("order_id", "")
        fill_price = fill.get("fill_price", 0)
        if order_id and fill_price:
            update_trade(order_id, {
                "fill_price": fill_price,
                "stop_adjusted": fill.get("stop_placed", 0),
                "t1_adjusted": fill.get("t1_placed", 0),
                "status": "open",
            })
            send_telegram(f"FILLED: {fill.get('symbol', '?')} @ ${fill_price}")


def _log_closes_from_result(result: dict) -> None:
    """Update trade log when agent reports position closes."""
    for pos in result.get("positions_closed", []):
        order_id = pos.get("order_id", "")
        symbol = pos.get("symbol", "?")
        exit_price = pos.get("exit_price", 0)
        exit_qty = pos.get("exit_qty", 0)
        reason = pos.get("reason", "agent_exit")
        if exit_price:
            pnl = close_trade(order_id, exit_price, exit_qty, reason)
            proceeds = exit_price * exit_qty
            record_sale(symbol, proceeds)
            send_telegram(f"CLOSED: {symbol} @ ${exit_price} | PnL: ${pnl:+.2f}")


def _build_scan_task(symbols: list[str], entered_today: set, n_positions: int, n_pending: int) -> str:
    slots = MAX_POSITIONS - n_positions - n_pending
    time_str = _now().strftime("%H:%M ET %Y-%m-%d")
    stats = today_stats()
    return (
        f"Time: {time_str}  Action: SCAN\n\n"
        f"Scan these symbols for entry signals: {', '.join(symbols)}\n\n"
        f"Current state:\n"
        f"  Open positions: {n_positions}  Pending orders: {n_pending}  "
        f"Available slots: {slots}\n"
        f"  Daily P&L so far: ${stats['total_pnl']:+.2f}  "
        f"Daily loss limit: ${MAX_DAILY_LOSS:.0f}\n\n"
        f"If signals found and slots available:\n"
        f"  1. Review each entry order (review_equity_order)\n"
        f"  2. Place entry limit order (DAY)\n"
        f"  Stop and T1 orders are placed ONLY after fill confirmed.\n\n"
        f"Also check any pending orders for fills. If filled, place stops/targets.\n\n"
        f"Return JSON as specified in your system prompt."
    )


def _build_monitor_task(n_positions: int, n_pending: int) -> str:
    time_str = _now().strftime("%H:%M ET")
    stats = today_stats()
    return (
        f"Time: {time_str}  Action: MONITOR\n\n"
        f"Open positions: {n_positions}  Pending orders: {n_pending}\n"
        f"Daily P&L: ${stats['total_pnl']:+.2f}\n\n"
        f"Check if any pending entry orders have filled. "
        f"If filled, place stop-loss and T1 orders.\n"
        f"Check if any open positions have hit stops or targets. "
        f"Report position status.\n\n"
        f"Return JSON as specified in your system prompt."
    )


def _build_force_close_task() -> str:
    return (
        f"Time: {_now().strftime('%H:%M ET')}  Action: FORCE_CLOSE\n\n"
        "It is approaching 3:45 PM ET. Force-close ALL positions flagged as "
        "intraday_only (Signal C). Swing positions (Signals A/B) may be held overnight.\n\n"
        "For each intraday position:\n"
        "  1. Get current quote\n"
        "  2. Place limit sell at bid * 0.998 (DAY order)\n"
        "  3. Cancel any open stop or target orders for that symbol\n\n"
        "Return JSON with positions_closed list."
    )


def main() -> None:
    logger.info("=== Small-Cap Daily Loop Starting (Managed Agents) ===")
    logger.info(f"Time: {_now().strftime('%Y-%m-%d %H:%M:%S ET')}")

    settle_msg = log_settlement_status()
    logger.info(settle_msg)

    entered_today: set[str] = set()
    last_scan = 0.0

    with TradingSession(title=f"Trading {_now().strftime('%Y-%m-%d')}") as session:

        # ── Market open startup ──────────────────────────────────────────────
        send_telegram(f"Market open — agent session started.\n{settle_msg}")

        while True:
            # Daily loss gate
            if not _check_daily_loss():
                break

            # Force-close check (3:45 PM)
            if _past_force_close():
                logger.info("3:45 PM: running force-close task")
                result = session.run_task(_build_force_close_task(), timeout_secs=TASK_TIMEOUT_SECS)
                _log_closes_from_result(result)
                logger.info(f"Force-close result: {result.get('status')} | {result.get('notes', '')}")
                break

            # Determine current positions / pending orders from last result (approximate)
            # The agent tracks exact state; Python only needs counts for slot calculation
            n_positions = 0  # agent reports exact positions in its result
            n_pending = 0

            now_mono = time.monotonic()
            if _in_entry_window() and now_mono - last_scan >= SCAN_INTERVAL_SECS:
                scan_syms = [s for s in WATCHLIST if s not in entered_today]
                if scan_syms:
                    task = _build_scan_task(
                        scan_syms, entered_today, n_positions, n_pending
                    )
                    logger.info(f"Sending scan task: {len(scan_syms)} symbols")
                    try:
                        result = session.run_task(task, timeout_secs=TASK_TIMEOUT_SECS)
                        status = result.get("status", "?")
                        logger.info(f"Scan result: {status} | {result.get('notes', '')}")
                        log_event("scan", {"result": result})

                        if result.get("market_ok") is False:
                            send_telegram(f"Market filter FAILED: {result.get('notes', '')}")
                        else:
                            _log_orders_from_result(result, entered_today)
                            _log_fills_from_result(result)
                            _log_closes_from_result(result)

                        if status == "daily_loss_limit":
                            break
                    except Exception as exc:
                        logger.error(f"Scan task error: {exc}")
                last_scan = now_mono

            else:
                # Between scans: just monitor fills/positions
                try:
                    result = session.run_task(
                        _build_monitor_task(n_positions, n_pending),
                        timeout_secs=TASK_TIMEOUT_SECS,
                    )
                    _log_fills_from_result(result)
                    _log_closes_from_result(result)
                except Exception as exc:
                    logger.error(f"Monitor task error: {exc}")

            time.sleep(MONITOR_INTERVAL_SECS)

    logger.info("=== Daily Loop Complete ===")


if __name__ == "__main__":
    main()
