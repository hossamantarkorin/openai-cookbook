"""
24/7 Scheduler — the entry point for the persistent process.

Runs the daily trading loop on weekdays only, then the EOD report.
Sleeps until next market open (9:00 AM ET). Restarts gracefully on error.
Never exits unless killed — this is the PID-1 process in Docker.
"""
import logging
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
BASE = Path(__file__).parent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [scheduler] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(BASE / "logs" / "scheduler.log"),
    ],
)
logger = logging.getLogger(__name__)

MARKET_OPEN_HOUR = 9
MARKET_OPEN_MIN = 0
LOOP_SCRIPT = BASE / "live" / "daily_loop.py"
EOD_SCRIPT = BASE / "live" / "eod_report.py"


def _now() -> datetime:
    return datetime.now(ET)


def _is_weekday() -> bool:
    return _now().weekday() < 5  # Mon=0 … Fri=4


def _secs_until_next_market_open() -> float:
    """Seconds until 9:00 AM ET next weekday."""
    now = _now()
    candidate = now.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MIN, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return (candidate - now).total_seconds()


def _run(script: Path) -> int:
    logger.info(f"Running: {script.name}")
    result = subprocess.run([sys.executable, str(script)])
    logger.info(f"{script.name} exited with code {result.returncode}")
    return result.returncode


def main() -> None:
    logger.info("=== Scheduler starting (24/7 mode) ===")
    (BASE / "logs").mkdir(parents=True, exist_ok=True)

    while True:
        now = _now()

        if not _is_weekday():
            secs = _secs_until_next_market_open()
            logger.info(f"Weekend — sleeping {secs/3600:.1f}h until Mon 9:00 AM ET")
            time.sleep(secs)
            continue

        market_open_today = now.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MIN, second=0, microsecond=0)
        if now < market_open_today:
            secs = (market_open_today - now).total_seconds()
            logger.info(f"Pre-market — sleeping {secs/60:.0f}m until 9:00 AM ET")
            time.sleep(secs)
            continue

        # Market hours: run trading loop
        logger.info(f"=== Starting trading day {now.strftime('%Y-%m-%d')} ===")
        try:
            _run(LOOP_SCRIPT)
        except Exception as e:
            logger.error(f"Daily loop crashed: {e}")

        # EOD report
        try:
            _run(EOD_SCRIPT)
        except Exception as e:
            logger.error(f"EOD report crashed: {e}")

        # Sleep until next market open
        secs = _secs_until_next_market_open()
        logger.info(f"Day complete — sleeping {secs/3600:.1f}h until next session")
        time.sleep(secs)


if __name__ == "__main__":
    main()
