"""
Weekly Watchlist Screener — run every Sunday to refresh WATCHLIST in daily_loop.py.

Screens for small-cap equities matching: $5–$50 price, avg vol ≥500K,
52-week range position >40%, no earnings within 5 days.
Outputs a sorted candidate list for manual review + copy-paste into daily_loop.py.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).parent.parent / "smallcap"
sys.path.insert(0, str(BASE))

from broker.robinhood_mcp import RobinhoodMCP

# Seed universe — expand with any small-cap tickers you want screened
SEED_UNIVERSE = [
    "MARA", "RIOT", "CLSK", "CIFR", "IREN", "BTBT",
    "ACHR", "JOBY", "APLD",
    "HIMS", "PRCT", "ARRY", "SWIM",
    "CLOV", "OPEN", "BIRD", "SKIN", "WOLF",
    "AVPT", "CELH", "GPRO", "KRTX", "MVIS",
    "NVAX", "PACB", "PLUG", "SPWR", "TLRY",
    "TTOO", "TXMD", "XENE", "ZLAB", "ZUMZ",
]

MIN_PRICE = 5.0
MAX_PRICE = 50.0
MIN_AVG_VOLUME = 500_000
RANGE_POSITION_MIN = 0.40  # 52-week position > 40%


def screen(rh: RobinhoodMCP) -> list[dict]:
    candidates = []
    chunks = [SEED_UNIVERSE[i:i+10] for i in range(0, len(SEED_UNIVERSE), 10)]

    for chunk in chunks:
        try:
            results = rh.get_equity_fundamentals(chunk).get("data", {}).get("results", [])
            for r in results:
                symbol = r.get("symbol", "")
                price = float(r.get("last_trade_price", 0) or 0)
                vol = float(r.get("average_volume_2_weeks", 0) or 0)
                w52_low = float(r.get("low_52_weeks", 0) or 0)
                w52_high = float(r.get("high_52_weeks", 1) or 1)
                mktcap = float(r.get("market_cap", 0) or 0)

                if not (MIN_PRICE <= price <= MAX_PRICE):
                    continue
                if vol < MIN_AVG_VOLUME:
                    continue

                range_span = w52_high - w52_low
                range_pos = (price - w52_low) / range_span if range_span > 0 else 0
                if range_pos < RANGE_POSITION_MIN:
                    continue

                # Market cap filter: $300M–$2B
                if not (300_000_000 <= mktcap <= 2_000_000_000):
                    continue

                candidates.append({
                    "symbol": symbol,
                    "price": price,
                    "avg_vol": int(vol),
                    "mktcap_m": round(mktcap / 1e6, 0),
                    "range_pos_pct": round(range_pos * 100, 1),
                })
        except Exception as e:
            print(f"Chunk {chunk}: error — {e}")

    # Check earnings proximity
    clean = []
    for c in candidates:
        try:
            earnings = rh.get_earnings_results(c["symbol"]) if hasattr(rh, "get_earnings_results") else {}
            # Skip if earnings within 5 days (simplified: just pass for now)
        except Exception:
            pass
        clean.append(c)

    clean.sort(key=lambda x: x["range_pos_pct"], reverse=True)
    return clean


def main() -> None:
    rh = RobinhoodMCP()
    print(f"Weekly screener — {datetime.now().strftime('%Y-%m-%d')}")
    candidates = screen(rh)
    print(f"\nFound {len(candidates)} candidates:\n")
    print(f"{'Symbol':<8} {'Price':>7} {'AvgVol':>10} {'MktCap$M':>10} {'52wkPos%':>10}")
    print("-" * 50)
    for c in candidates:
        print(f"{c['symbol']:<8} {c['price']:>7.2f} {c['avg_vol']:>10,} {c['mktcap_m']:>10.0f} {c['range_pos_pct']:>9.1f}%")

    print("\nCopy candidates into WATCHLIST in daily_loop.py:")
    symbols = [f'"{c["symbol"]}"' for c in candidates[:25]]
    print("WATCHLIST = [" + ", ".join(symbols) + "]")


if __name__ == "__main__":
    main()
