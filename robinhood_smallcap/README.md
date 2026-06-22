# Robinhood SmallCap Trading Bot

Agentic small-cap momentum + mean reversion trading bot using the Robinhood MCP API.

**Capital:** $1,000 | **Universe:** US small-caps $300M–$2B market cap | **Account:** Robinhood Agentic (cash)

---

## Strategy Summary

Three signal types (see `smallcap/STRATEGY.md` for full rules):

| Signal | Name | Trigger | Confidence |
|--------|------|---------|------------|
| A | Volume Breakout Pullback | New 20-day high on 1.5× vol, then 2–4% pullback above 20 EMA | High |
| B | Oversold Bounce | 8–15% off 20-day high, RSI<35, MACD hist improving | Medium |
| C | Gap-and-Go | Pre-market gap >3%, 30% filled, price at VWAP | Medium |

Market filter (all three must pass before any entry):
- VIX < 30
- SPY day change > -1.5%
- IWM above 50-day SMA

---

## Project Structure

```
robinhood_smallcap/
├── smallcap/
│   ├── broker/robinhood_mcp.py   # Robinhood MCP HTTP client
│   ├── signals/scanner.py        # Signal A/B/C logic + market filter
│   ├── live/
│   │   ├── daily_loop.py         # Main trading loop (10 AM–3:45 PM ET)
│   │   ├── eod_report.py         # End-of-day P&L summary
│   │   ├── trade_log.py          # JSONL trade ledger with PnL tracking
│   │   └── notify.py             # Telegram alerts
│   ├── pdt_tracker.py            # PDT compliance (3 day trades / 5 days)
│   └── scheduler.py              # 24/7 weekday scheduler (the main entrypoint)
├── scripts/
│   ├── health_check.py           # Verify MCP connectivity
│   └── weekly_screener.py        # Sunday watchlist refresh
├── deploy/
│   ├── Dockerfile
│   ├── docker-compose.yml        # Preferred: restart:always for 24/7
│   └── smallcap.service          # Alternative: systemd
├── requirements.txt
└── .env.example
```

---

## Quick Start

```bash
# 1. Clone and set up
git clone <repo-url> && cd Robinhood_SmallCap
cp .env.example .env
# Edit .env — add ROBINHOOD_API_TOKEN and optionally TELEGRAM_*

pip install -r requirements.txt

# 2. Verify connection
python scripts/health_check.py

# 3. Run weekly screener (Sundays)
python scripts/weekly_screener.py

# 4a. Run with Docker (recommended for 24/7)
docker compose -f deploy/docker-compose.yml up -d

# 4b. Or run with systemd
sudo cp deploy/smallcap.service /etc/systemd/system/
sudo systemctl enable --now smallcap

# 4c. Or run manually (exits after market close)
python smallcap/scheduler.py
```

---

## Key Bug Fixes (vs original v1.0 code)

| # | File | Bug | Fix |
|---|------|-----|-----|
| 1 | `robinhood_mcp.py` | Tool name `"get_equity-historicals"` (hyphen) | Fixed to `"get_equity_historicals"` |
| 2 | `robinhood_mcp.py` | Historicals used `span=` (deprecated param) | Switched to `start_time=` RFC3339 |
| 3 | `robinhood_mcp.py` | `get_indexes_quotes` wrong name + symbol-based | Fixed to `get_index_quotes` with UUID via `get_indexes` |
| 4 | `robinhood_mcp.py` | `get_portfolio` missing required `account_number` | Auto-discovered from `get_accounts` agentic account |
| 5 | `robinhood_mcp.py` | `place_equity_order` missing `account_number` | Same fix |
| 6 | `daily_loop.py` | Stop-loss placed before entry fill (naked stop risk) | Stop placed only after fill confirmation |
| 7 | `daily_loop.py` | No T1/T2 exit logic implemented | T1 limit sell placed after fill; T2 tracked |
| 8 | `daily_loop.py` | No PDT compliance tracking | `pdt_tracker.py` counts day trades, blocks excess |
| 9 | `daily_loop.py` | No daily loss limit enforcement | Stops trading after -$100/day |
| 10 | `scanner.py` | Signal C missing | Fully implemented |
| 11 | `notify.py` | Hardcoded Telegram chat_id + `.openclaw` path dep | All credentials via env vars |
| 12 | `trade_log.py` | P&L fields never populated | `close_trade()` + `update_trade()` functions added |

---

## Go-Live Checklist (from STRATEGY.md)

- [ ] `python scripts/health_check.py` passes
- [ ] Paper-trade for 1 full week — review EOD reports
- [ ] Confirm stop-loss orders appearing in Robinhood app after fills
- [ ] Weekly screener producing reasonable candidates
- [ ] Telegram alerts arriving correctly
- [ ] Hossam approval after 1-week paper review

---

## Risk Warnings

- This bot places **real orders with real money** on your Robinhood account
- Small-cap stocks are highly volatile; losses can exceed stop estimates due to gaps
- PDT rule: <$25K account is limited to 3 day trades per rolling 5 business days
- Always monitor the first week manually
