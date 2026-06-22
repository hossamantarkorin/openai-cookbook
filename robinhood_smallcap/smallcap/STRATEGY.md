# Small-Cap Momentum + Mean Reversion Strategy
**Version:** 1.0  
**Date:** 2026-06-21  
**Capital:** $1,000  
**Broker:** Robinhood (Agentic Trading via MCP)  
**Instrument:** US Small-Cap Equities (market cap $300M–$2B)

---

## 1. Universe Selection

### Definition
- **Market cap range:** $300M – $2B (true small-cap)
- **Price range:** $5 – $50 (avoid penny stocks, avoid expensive per-share)
- **Min avg daily volume:** 500,000 shares (liquidity floor — critical for small caps)
- **Exchange:** NYSE / NASDAQ listed (no OTC)
- **Sector focus:** Technology, Healthcare, Consumer Discretionary (highest momentum historically)
- **Exclude:** Recent IPOs < 6 months, stocks in bankruptcy proceedings

### Dynamic Watchlist
Maintain a watchlist of 20–30 candidates screened weekly. Refresh every Sunday.

**Screening criteria (weekly):**
1. 52-week range position > 40% (not in deep downtrend)
2. Relative strength vs Russell 2000 (IWM) > 0 over last 20 days
3. No earnings within next 5 trading days (avoid binary event risk)
4. Institutional ownership > 5% (some smart money awareness)

---

## 2. Core Strategy: Momentum with Pullback Entry

### Philosophy
Small caps trend strongly when they break out, but are prone to 3–8% intraday whipsaws. 
The edge is: **buy the first pullback after a confirmed breakout**, not the breakout itself.

### Signal Types

#### Signal A: Volume Breakout + Pullback (Primary)
**Setup:**
1. Stock makes a new 20-day high on volume ≥ 1.5× 20-day avg volume
2. Price then pulls back 2–4% from breakout high over 1–3 days
3. RSI(14) on daily: 45–65 (not overbought, not broken)
4. Price holds above the 20-day EMA during pullback

**Entry:** Buy when price reclaims 50% of pullback (e.g., broke out at $20, pulled to $19.20 → enter at ~$19.60)

#### Signal B: Oversold Bounce (Secondary)
**Setup:**
1. Stock down 8–15% from 20-day high (oversold but not broken)
2. RSI(14) daily < 35
3. MACD histogram turning from negative to less-negative (momentum shift)
4. Volume on down days declining (selling exhaustion)
5. Price near support (20-day EMA or recent swing low)

**Entry:** Buy on first green daily candle with volume ≥ 1.2× average

#### Signal C: Gap-and-Go (Opportunistic)
**Setup:**
1. Pre-market gap up > 3% on significant news (earnings beat, partnership, FDA approval)
2. Gap fills at least 30% intraday before noon
3. Price consolidates for ≥ 30 minutes near VWAP

**Entry:** Buy when price reclaims VWAP with volume surge

---

## 3. Technical Indicators

### Required Indicators
| Indicator | Parameters | Use |
|-----------|-----------|-----|
| EMA | 9, 20, 50 | Trend direction, dynamic support |
| RSI | 14 | Overbought/oversold; entry timing |
| MACD | 12/26/9 | Momentum confirmation |
| Volume | 20-day avg | Confirm breakouts and reversals |
| VWAP | Daily | Intraday fair value anchor |
| ATR | 14 | Position sizing, stop placement |
| Bollinger Bands | 20/2 | Volatility expansion confirmation |

### Indicator Logic for Entry
All 3 must align for Signal A/B entry:
1. **Trend:** Price above 20 EMA (daily)
2. **Momentum:** RSI(14) between 40–70 (not extreme)  
3. **Volume:** Entry bar volume ≥ 1.2× 20-day avg

---

## 4. Entry Rules

### Order Type
- **Limit orders only** — small caps have wide spreads; never market order
- Set limit 0.1–0.2% above current ask to ensure fill without chasing

### Timing
- **Primary entry window:** 10:00 AM – 2:00 PM ET
  - Avoid first 30 min (9:30–10:00): high manipulation, wide spreads
  - Avoid last 30 min (3:30–4:00): end-of-day volatility
- **No entries on Fridays** for swing positions (weekend gap risk)
- **No entries day before earnings** (T-1)

### Position Sizing
```
ATR_stop = ATR(14) × 1.5
max_risk = $150 per trade  (hard cap)
shares = floor($150 / ATR_stop)
position_value = shares × entry_price

# Hard cap: never exceed 20% of $1,000 = $200 per position
shares = min(shares, floor($200 / entry_price))
```

**Max concurrent positions:** 2 (PDT constraint + concentration risk)

---

## 5. Exit Rules

### Stop Loss
- **Initial stop:** Entry price − (ATR(14) × 1.5)
- **Hard max loss:** 8% below entry price (overrides ATR stop if ATR stop is tighter)
- Stops are **hard stops** — no averaging down

### Profit Targets
**Scale-out approach:**
- **Target 1 (T1):** Entry + ATR × 2 → Sell 50% of position
- **Target 2 (T2):** Entry + ATR × 4 → Sell remaining 50%
- **If T1 hit:** Move stop to breakeven on remaining shares

### Trailing Stop (after T1)
Once T1 is hit and stop moved to breakeven:
- Trail stop at 20 EMA (daily) for swing holds
- Or 3% trailing stop, whichever is tighter

### Time-Based Exit
- **Max hold:** 10 trading days (swing)
- **Intraday signals (Signal C):** Force close before 3:45 PM ET

### Forced Exits
- Earnings announcement within 2 days → exit before announcement
- Stock gaps down > 5% at open → exit at open (accept the loss, no hoping)

---

## 6. Risk Management

### Account-Level Rules
| Rule | Value |
|------|-------|
| Max capital deployed at once | 40% ($400 of $1,000) |
| Max positions | 2 concurrent |
| Max daily loss | $100 (stop trading for the day) |
| Max weekly loss | $200 (stop trading for the week) |
| Per-trade risk | $150 hard cap |

### Market Filter
Do NOT trade when:
- VIX > 30 (extreme fear — small caps get crushed)
- IWM (Russell 2000) is below its 50-day SMA (broad small-cap downtrend)
- SPY is down > 1.5% on the day (risk-off environment)

### Liquidity Check (every entry)
Before placing order, verify:
- Bid-ask spread < 0.5% of price
- Volume on current day already ≥ 100K shares by entry time

---

## 7. Execution via Robinhood MCP

### Pre-Trade Checklist (automated)
```
1. get_portfolio() → verify buying power ≥ position_value
2. get_equity_tradability(symbol) → confirm tradable
3. get_equity_quotes(symbol) → check bid-ask spread
4. review_equity_order() → get pre-trade warnings
5. place_equity_order() → limit order
```

### Order Parameters
```json
{
  "symbol": "TICKER",
  "side": "buy",
  "type": "limit",
  "quantity": <shares>,
  "limit_price": <entry_price>,
  "time_in_force": "day"
}
```

### Stop Loss Handling
Robinhood supports stop-limit orders. After entry fills:
```json
{
  "symbol": "TICKER", 
  "side": "sell",
  "type": "stop_limit",
  "quantity": <shares>,
  "stop_price": <stop_price>,
  "limit_price": <stop_price × 0.995>,
  "time_in_force": "gtc"
}
```

---

## 8. Monitoring & Reporting

### Daily Routine
- **9:00 AM:** Scan watchlist, check market filter (VIX, IWM vs 50 SMA, SPY)
- **9:30–10:00 AM:** Observe only — no entries
- **10:00 AM – 2:00 PM:** Active scan window, entries allowed
- **3:30 PM:** Review open positions, adjust stops
- **4:15 PM:** EOD report — P&L, open positions, tomorrow's watchlist

### Metrics to Track
- Win rate (target: > 45%)
- Average win / average loss ratio (target: > 2:1)
- Expectancy per trade = (WR × avg_win) - (LR × avg_loss)
- % signals filtered by market filter
- Avg hold time per trade

---

## 9. Edge & Rationale

**Why small caps?**
- Less analyst coverage → more pricing inefficiency
- Momentum persists longer (retail-driven, slower institutional response)
- Breakouts are cleaner (fewer algorithms fighting the move)

**Why pullback entries (not breakout chasing)?**
- Breakout entries have poor R:R (stop too close to recent high, or too far if placed below breakout)
- Pullback entries let us buy closer to actual support with defined risk
- Studies consistently show pullback entries have 5–10% better win rates than direct breakout entries

**Why $300M–$2B market cap floor?**
- Below $300M: manipulation risk, wide spreads, poor fills
- Above $2B: less momentum, more efficient pricing, closer to mid-cap behavior

---

## 10. Known Risks

- **Liquidity risk:** Small caps can gap through stops; ATR-based sizing accounts for this but doesn't eliminate it
- **Spread cost:** Wide spreads eat into returns; strict limit-only discipline is critical
- **News risk:** Small caps move violently on news; earnings blackout rule mitigates but doesn't eliminate
- **PDT rule:** With $1,000 account, limited to 3 day trades per rolling 5 days — this strategy is **swing-first** by design
- **Slippage:** Small caps have thinner books; limit orders may not fill at desired price

---

## 11. Go-Live Criteria

Before trading real capital:
- [ ] MCP connection authenticated and tested (paper trade 1 full week)
- [ ] All entry/exit logic validated against 1 week of live data
- [ ] EOD report generating correctly
- [ ] Stop loss orders confirmed placing correctly via Robinhood
- [ ] Hossam approval after 1-week paper review
