"""Trade logging — enhanced with close/PnL tracking."""
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
BASE = Path(__file__).parent.parent
LOG_FILE = BASE / "logs" / "trades.jsonl"
EVENT_FILE = BASE / "logs" / "events.jsonl"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)


def _now() -> str:
    return datetime.now(ET).isoformat()


def log_trade(trade: dict) -> None:
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(trade) + "\n")


def log_event(event_type: str, data: dict) -> None:
    entry = {"type": event_type, "ts": _now(), **data}
    with open(EVENT_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def update_trade(order_id: str, updates: dict) -> None:
    """Rewrite the trade log, patching the entry matching order_id."""
    if not LOG_FILE.exists():
        return
    trades = load_trades()
    with open(LOG_FILE, "w") as f:
        for t in trades:
            if t.get("order_id") == order_id:
                t.update(updates)
            f.write(json.dumps(t) + "\n")


def close_trade(order_id: str, exit_price: float, exit_qty: int, exit_reason: str) -> float:
    """
    Mark shares as closed, compute PnL, return realized PnL.
    Partial closes (T1 partial sell) call this with exit_qty < full position.
    """
    if not LOG_FILE.exists():
        return 0.0
    trades = load_trades()
    pnl = 0.0
    with open(LOG_FILE, "w") as f:
        for t in trades:
            if t.get("order_id") == order_id:
                entry_price = float(t.get("entry", exit_price))
                pnl = (exit_price - entry_price) * exit_qty
                t["exit_price"] = exit_price
                t["exit_qty"] = exit_qty
                t["exit_reason"] = exit_reason
                t["pnl"] = round(pnl, 2)
                t["result"] = "win" if pnl >= 0 else "loss"
                t["closed_at"] = _now()
                t["status"] = "closed"
            f.write(json.dumps(t) + "\n")
    return pnl


def load_trades() -> list[dict]:
    if not LOG_FILE.exists():
        return []
    with open(LOG_FILE) as f:
        return [json.loads(line) for line in f if line.strip()]


def today_stats() -> dict:
    today = datetime.now(ET).strftime("%Y-%m-%d")
    trades = [t for t in load_trades() if t.get("timestamp", "").startswith(today)]
    closed = [t for t in trades if t.get("status") == "closed"]
    wins = [t for t in closed if t.get("result") == "win"]
    losses = [t for t in closed if t.get("result") == "loss"]
    total_pnl = sum(t.get("pnl", 0) for t in closed)
    return {
        "total": len(trades),
        "closed": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "total_pnl": round(total_pnl, 2),
    }
