"""
Robinhood MCP Client — enhanced & bug-fixed.

Bugs fixed vs original:
  - Tool name typo: "get_equity-historicals" → "get_equity_historicals"
  - Historicals now uses `symbols` list + `start_time` (new API schema)
  - VIX/index quotes now uses get_indexes → get_index_quotes (UUID-based)
  - get_portfolio / place_equity_order now pass account_number (required)
  - account_number auto-discovered from get_accounts (agentic account)
  - stop_price passed correctly to review and place for stop_limit orders
"""
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

BASE = Path(__file__).parent.parent
load_dotenv(BASE.parent / ".env")

MCP_URL = os.getenv("ROBINHOOD_MCP_URL", "https://agent.robinhood.com/mcp/trading")
ROBINHOOD_TOKEN = os.getenv("ROBINHOOD_API_TOKEN", "")

logger = logging.getLogger(__name__)


def _iso(days_ago: int = 0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class RobinhoodMCP:
    def __init__(self, token: Optional[str] = None):
        self.url = MCP_URL
        self.token = token or ROBINHOOD_TOKEN
        self._req_id = 0
        self._account_number: Optional[str] = None
        self._vix_id: Optional[str] = None
        self._iwm_sma_cache: Optional[float] = None
        if not self.token:
            raise ValueError("ROBINHOOD_API_TOKEN not set. Add to .env")

    # ── Private transport ────────────────────────────────────────────────────

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }

    def _call(self, method: str, params: dict) -> dict:
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": self._next_id(),
        }
        resp = requests.post(self.url, headers=self._headers(), json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"MCP error {data['error']['code']}: {data['error']['message']}")
        return data.get("result", {})

    def _tool(self, name: str, arguments: dict) -> dict:
        return self._call("tools/call", {"name": name, "arguments": arguments})

    # ── Account discovery ────────────────────────────────────────────────────

    @property
    def account_number(self) -> str:
        if not self._account_number:
            accounts = self.get_accounts().get("data", {}).get("accounts", [])
            for a in accounts:
                if a.get("agentic_allowed"):
                    self._account_number = a["account_number"]
                    logger.info(f"Using agentic account ...{self._account_number[-4:]}")
                    return self._account_number
            raise RuntimeError("No agentic_allowed account found. Enable Agentic Trading in Robinhood app.")
        return self._account_number

    # ── Account & Portfolio ───────────────────────────────────────────────────

    def get_accounts(self) -> dict:
        return self._tool("get_accounts", {})

    def get_portfolio(self) -> dict:
        return self._tool("get_portfolio", {"account_number": self.account_number})

    def search(self, query: str) -> dict:
        return self._tool("search", {"query": query})

    # ── Market Data ───────────────────────────────────────────────────────────

    def get_equity_quotes(self, symbols: list[str]) -> dict:
        if len(symbols) > 20:
            raise ValueError("Max 20 symbols per quote call")
        return self._tool("get_equity_quotes", {"symbols": symbols})

    def get_equity_historicals(
        self,
        symbol: str,
        interval: str = "day",
        days_back: int = 365,
        bounds: str = "regular",
    ) -> dict:
        """Fetch OHLCV bars. days_back controls lookback window."""
        return self._tool(
            "get_equity_historicals",
            {
                "symbols": [symbol],
                "start_time": _iso(days_back),
                "interval": interval,
                "bounds": bounds,
            },
        )

    def get_equity_fundamentals(self, symbols: list[str]) -> dict:
        return self._tool("get_equity_fundamentals", {"symbols": symbols})

    def get_indexes(self, symbols: list[str]) -> dict:
        """Look up index metadata including instrument UUID."""
        return self._tool("get_indexes", {"symbols": symbols})

    def get_index_quotes(self, instrument_ids: list[str]) -> dict:
        """Get live index levels by UUID (obtain UUIDs via get_indexes)."""
        return self._tool("get_index_quotes", {"instrument_ids": instrument_ids})

    def get_vix(self) -> float:
        """Return current VIX level. Caches UUID across calls."""
        if not self._vix_id:
            indexes = self.get_indexes(["VIX"])
            results = indexes.get("data", {}).get("results", [])
            if not results:
                raise RuntimeError("VIX index not found via get_indexes")
            self._vix_id = results[0]["id"]
        quotes = self.get_index_quotes([self._vix_id])
        results = quotes.get("data", {}).get("results", [])
        return float(results[0]["value"])

    # ── Orders & Positions ────────────────────────────────────────────────────

    def get_equity_positions(self) -> dict:
        return self._tool("get_equity_positions", {"account_number": self.account_number})

    def get_equity_orders(self) -> dict:
        return self._tool("get_equity_orders", {"account_number": self.account_number})

    def get_equity_tradability(self, symbol: str) -> dict:
        return self._tool("get_equity_tradability", {"symbol": symbol})

    def review_equity_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "limit",
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        time_in_force: str = "day",
    ) -> dict:
        params: dict = {
            "account_number": self.account_number,
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "type": order_type,
            "time_in_force": time_in_force,
        }
        if limit_price is not None:
            params["limit_price"] = round(limit_price, 2)
        if stop_price is not None:
            params["stop_price"] = round(stop_price, 2)
        return self._tool("review_equity_order", params)

    def place_equity_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "limit",
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        time_in_force: str = "day",
        ref_id: Optional[str] = None,
    ) -> dict:
        params: dict = {
            "account_number": self.account_number,
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "type": order_type,
            "time_in_force": time_in_force,
        }
        if limit_price is not None:
            params["limit_price"] = round(limit_price, 2)
        if stop_price is not None:
            params["stop_price"] = round(stop_price, 2)
        if ref_id is not None:
            params["ref_id"] = ref_id
        return self._tool("place_equity_order", params)

    def cancel_equity_order(self, order_id: str) -> dict:
        return self._tool("cancel_equity_order", {"order_id": order_id})

    # ── Watchlists ────────────────────────────────────────────────────────────

    def get_watchlists(self) -> dict:
        return self._tool("get_watchlists", {})

    def get_watchlist_items(self, list_id: str) -> dict:
        return self._tool("get_watchlist_items", {"list_id": list_id})

    def create_watchlist(self, display_name: str) -> dict:
        return self._tool("create_watchlist", {"display_name": display_name})

    def add_to_watchlist(self, list_id: str, symbols: list[str]) -> dict:
        return self._tool("add_to_watchlist", {"list_id": list_id, "symbols": symbols})
