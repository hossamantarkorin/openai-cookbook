"""
Small-Cap Scanner — enhanced.

Fixes vs original:
  - VIX check uses get_vix() helper (get_indexes + get_index_quotes, UUID-based)
  - IWM historicals use updated API (start_time, not span)
  - Signal C (Gap-and-Go) fully implemented
  - MACD check in Signal B corrected (was checking wrong direction)
  - Price filter rejects IONQ-style $50+ overflows before computing indicators
  - Spread check added before signalling
"""
import logging
import math
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

from broker.robinhood_mcp import RobinhoodMCP

logger = logging.getLogger(__name__)

# ── Market Filter ─────────────────────────────────────────────────────────────
VIX_MAX = 30.0
SPY_MAX_DOWN_PCT = -1.5
IWM_SMA_PERIOD = 50

# ── Universe Rules ────────────────────────────────────────────────────────────
MIN_PRICE = 5.0
MAX_PRICE = 50.0
MIN_AVG_VOLUME = 500_000
MAX_SPREAD_PCT = 0.005  # 0.5%

# ── Risk Parameters ───────────────────────────────────────────────────────────
MAX_RISK = 150.0
MAX_POSITION_VALUE = 200.0
ATR_STOP_MULT = 1.5
ATR_T1_MULT = 2.0
ATR_T2_MULT = 4.0


# ── Indicator Engine ──────────────────────────────────────────────────────────

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    c, h, lo, v = df["close"], df["high"], df["low"], df["volume"]

    df["ema_9"] = c.ewm(span=9, adjust=False).mean()
    df["ema_20"] = c.ewm(span=20, adjust=False).mean()
    df["ema_50"] = c.ewm(span=50, adjust=False).mean()

    delta = c.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    df["rsi_14"] = 100 - (100 / (1 + gain / loss.replace(0, np.nan)))

    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    df["vol_avg_20"] = v.rolling(20).mean()
    df["vol_ratio"] = v / df["vol_avg_20"]

    tr = pd.concat([h - lo, (h - c.shift()).abs(), (lo - c.shift()).abs()], axis=1).max(axis=1)
    df["atr_14"] = tr.rolling(14).mean()

    bb_mid = c.rolling(20).mean()
    bb_std = c.rolling(20).std()
    df["bb_upper"] = bb_mid + 2 * bb_std
    df["bb_lower"] = bb_mid - 2 * bb_std

    df["high_20d"] = h.rolling(20).max()
    df["low_20d"] = lo.rolling(20).min()

    # VWAP proxy (daily reset unavailable on EOD bars; use price/vol weighted avg of last 5 bars)
    df["vwap_5d"] = (c * v).rolling(5).sum() / v.rolling(5).sum()

    return df


def parse_bars(raw: dict) -> pd.DataFrame:
    results = raw.get("data", {}).get("results", [])
    if not results:
        return pd.DataFrame()
    # Multi-symbol response: take first symbol's historicals
    if isinstance(results[0], dict) and "historicals" in results[0]:
        rows = results[0]["historicals"]
    else:
        rows = results
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df.get("begins_at", df.get("date", pd.Series(dtype="object"))))
    df = df.sort_values("date").reset_index(drop=True)
    for col in ["open_price", "high_price", "low_price", "close_price", "volume"]:
        if col in df.columns:
            df[col.replace("_price", "")] = pd.to_numeric(df[col], errors="coerce")
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["close"])


# ── Market Filter ─────────────────────────────────────────────────────────────

def check_market_filter(rh: RobinhoodMCP) -> tuple[bool, str]:
    try:
        vix = rh.get_vix()
        if vix > VIX_MAX:
            return False, f"VIX {vix:.1f} > {VIX_MAX} — skip trading"
    except Exception as e:
        logger.warning(f"VIX check failed: {e} — proceeding")

    try:
        quotes = rh.get_equity_quotes(["SPY"])
        spy = quotes["data"]["results"][0]["quote"]
        spy_price = float(spy["last_trade_price"])
        spy_prev = float(spy["adjusted_previous_close"])
        spy_chg = (spy_price - spy_prev) / spy_prev * 100
        if spy_chg < SPY_MAX_DOWN_PCT:
            return False, f"SPY {spy_chg:+.1f}% — risk-off environment"
    except Exception as e:
        logger.warning(f"SPY check failed: {e} — proceeding")

    try:
        raw = rh.get_equity_historicals("IWM", interval="day", days_back=90)
        iwm_df = parse_bars(raw)
        if len(iwm_df) >= IWM_SMA_PERIOD:
            sma50 = iwm_df["close"].rolling(IWM_SMA_PERIOD).mean().iloc[-1]
            last = iwm_df["close"].iloc[-1]
            if last < sma50:
                return False, f"IWM below 50-SMA ({last:.2f} < {sma50:.2f}) — small-cap downtrend"
    except Exception as e:
        logger.warning(f"IWM check failed: {e} — proceeding")

    return True, "Market filter passed"


# ── Signal Scorers ────────────────────────────────────────────────────────────

def _size(entry: float, stop: float) -> tuple[int, float]:
    risk_per_share = entry - stop
    if risk_per_share <= 0:
        return 0, 0.0
    shares = max(1, math.floor(MAX_RISK / risk_per_share))
    shares = min(shares, math.floor(MAX_POSITION_VALUE / entry))
    return shares, round(shares * risk_per_share, 2)


def score_signal_a(df: pd.DataFrame, symbol: str) -> Optional[dict]:
    """Signal A: Volume Breakout + Pullback."""
    if len(df) < 25:
        return None
    row = df.iloc[-1]

    close = row["close"]
    ema_20 = row["ema_20"]
    rsi = row["rsi_14"]
    atr = row["atr_14"]

    # Recent breakout: one of last 3 bars hit a 20-day high on volume ≥ 1.5×
    recent = df.iloc[-4:-1]
    bo_days = recent[
        (recent["close"] >= recent["high_20d"].shift(1).fillna(recent["high_20d"])) &
        (recent["vol_ratio"] >= 1.5)
    ]
    if bo_days.empty:
        return None

    breakout_high = bo_days["close"].max()
    pullback_pct = (breakout_high - close) / breakout_high * 100

    if not (2.0 <= pullback_pct <= 4.0):
        return None
    if close < ema_20:
        return None
    if not (45 <= rsi <= 65):
        return None

    entry = round(close * 1.001, 2)
    stop = round(entry - atr * ATR_STOP_MULT, 2)
    shares, risk = _size(entry, stop)
    if shares == 0:
        return None

    return {
        "symbol": symbol,
        "signal_type": "A",
        "signal_name": "Volume Breakout Pullback",
        "entry": entry,
        "stop": stop,
        "t1": round(entry + atr * ATR_T1_MULT, 2),
        "t2": round(entry + atr * ATR_T2_MULT, 2),
        "shares": shares,
        "risk_amt": risk,
        "rsi": round(rsi, 1),
        "pullback_pct": round(pullback_pct, 1),
        "confidence": "high",
    }


def score_signal_b(df: pd.DataFrame, symbol: str) -> Optional[dict]:
    """Signal B: Oversold Bounce."""
    if len(df) < 25:
        return None
    row = df.iloc[-1]
    prev = df.iloc[-2]

    close = row["close"]
    high_20d = row["high_20d"]
    rsi = row["rsi_14"]
    macd_hist = row["macd_hist"]
    prev_macd_hist = prev["macd_hist"]
    ema_20 = row["ema_20"]
    atr = row["atr_14"]
    vol_ratio = row["vol_ratio"]

    pullback_pct = (high_20d - close) / high_20d * 100
    if not (8.0 <= pullback_pct <= 15.0):
        return None
    if rsi >= 35:
        return None
    # MACD hist must be negative AND improving (less negative than previous)
    if macd_hist >= 0 or macd_hist <= prev_macd_hist:
        return None
    if abs(close - ema_20) / ema_20 > 0.05:
        return None
    if close <= prev["close"] or vol_ratio < 1.2:
        return None

    entry = round(close * 1.001, 2)
    stop = round(entry - atr * ATR_STOP_MULT, 2)
    shares, risk = _size(entry, stop)
    if shares == 0:
        return None

    return {
        "symbol": symbol,
        "signal_type": "B",
        "signal_name": "Oversold Bounce",
        "entry": entry,
        "stop": stop,
        "t1": round(entry + atr * ATR_T1_MULT, 2),
        "t2": round(entry + atr * ATR_T2_MULT, 2),
        "shares": shares,
        "risk_amt": risk,
        "rsi": round(rsi, 1),
        "pullback_pct": round(pullback_pct, 1),
        "confidence": "medium",
    }


def score_signal_c(df: pd.DataFrame, symbol: str, open_price: float, prev_close: float) -> Optional[dict]:
    """
    Signal C: Gap-and-Go.
    Requires intraday context: today's open and current price.
    Checks: gap > 3% pre-market, gap has partially filled (≥30%), price near VWAP.
    """
    if len(df) < 20:
        return None
    row = df.iloc[-1]
    atr = row["atr_14"]
    vwap = row["vwap_5d"]
    close = row["close"]  # most recent daily close (used as intraday proxy)

    if prev_close <= 0:
        return None

    gap_pct = (open_price - prev_close) / prev_close * 100
    if gap_pct < 3.0:
        return None

    gap_size = open_price - prev_close
    gap_filled_pct = (open_price - close) / gap_size * 100 if gap_size > 0 else 0
    if gap_filled_pct < 30:
        return None

    # Price should be near VWAP (within 1%)
    if abs(close - vwap) / vwap > 0.01:
        return None

    entry = round(close * 1.002, 2)
    stop = round(entry - atr * ATR_STOP_MULT, 2)
    shares, risk = _size(entry, stop)
    if shares == 0:
        return None

    return {
        "symbol": symbol,
        "signal_type": "C",
        "signal_name": "Gap-and-Go",
        "entry": entry,
        "stop": stop,
        "t1": round(entry + atr * ATR_T1_MULT, 2),
        "t2": round(entry + atr * ATR_T2_MULT, 2),
        "shares": shares,
        "risk_amt": risk,
        "gap_pct": round(gap_pct, 1),
        "gap_filled_pct": round(gap_filled_pct, 1),
        "confidence": "medium",
        "intraday_only": True,
    }


# ── Main Scanner ──────────────────────────────────────────────────────────────

def scan_watchlist(rh: RobinhoodMCP, symbols: list[str]) -> list[dict]:
    ok, reason = check_market_filter(rh)
    if not ok:
        logger.warning(f"Market filter FAIL: {reason}")
        return []

    logger.info(f"Market OK. Scanning {len(symbols)} symbols…")
    signals: list[dict] = []

    for symbol in symbols:
        try:
            # Quick price / spread check first (cheap API call)
            q_raw = rh.get_equity_quotes([symbol])
            q = q_raw["data"]["results"][0]["quote"]
            bid = float(q.get("bid_price", 0))
            ask = float(q.get("ask_price", 0))
            last = float(q.get("last_trade_price", 0))
            prev = float(q.get("adjusted_previous_close", last))

            if not (MIN_PRICE <= last <= MAX_PRICE):
                logger.debug(f"{symbol}: price {last:.2f} out of range")
                continue
            if bid > 0 and ask > 0:
                spread_pct = (ask - bid) / last
                if spread_pct > MAX_SPREAD_PCT:
                    logger.debug(f"{symbol}: spread {spread_pct:.3%} too wide")
                    continue

            raw = rh.get_equity_historicals(symbol, interval="day", days_back=400)
            df = parse_bars(raw)

            if len(df) < 25:
                logger.debug(f"{symbol}: only {len(df)} bars")
                continue

            avg_vol = df["volume"].tail(20).mean()
            if avg_vol < MIN_AVG_VOLUME:
                logger.debug(f"{symbol}: low avg vol {avg_vol:.0f}")
                continue

            df = compute_indicators(df)

            sig = (
                score_signal_a(df, symbol)
                or score_signal_b(df, symbol)
                or score_signal_c(df, symbol, open_price=float(q.get("open", last)), prev_close=prev)
            )

            if sig:
                signals.append(sig)
                logger.info(
                    f"SIGNAL [{sig['signal_type']}] {symbol}: "
                    f"entry={sig['entry']} stop={sig['stop']} t1={sig['t1']} "
                    f"risk=${sig['risk_amt']}"
                )

        except Exception as e:
            logger.error(f"{symbol}: scan error — {e}")

    signals.sort(key=lambda s: ({"A": 0, "C": 1, "B": 2}.get(s["signal_type"], 9), s["risk_amt"]))
    return signals
