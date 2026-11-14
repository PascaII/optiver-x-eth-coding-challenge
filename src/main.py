"""Bot entry point. Run from the repository root in the Optibook workspace:

    python src/main.py              # dry run: connects and reads data, sends no orders
    python src/main.py --live       # sends orders
    python src/main.py --no-record  # keep bot.log only, no CSVs

Every run writes runs/<UTC time>_<live|dry>/ with bot.log and, unless
--no-record, the recorded CSVs (see README.md, "Run data").
"""

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from runner import run

RUNS_DIR = Path(__file__).resolve().parent.parent / "runs"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

log = logging.getLogger("main")


def _make_run_dir(live: bool) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_dir = RUNS_DIR / f"{stamp}_{'live' if live else 'dry'}"
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Optibook trading bot")
    parser.add_argument("--live", action="store_true", help="send orders (default: dry run)")
    parser.add_argument("--no-record", action="store_true", help="do not write CSVs")
    args = parser.parse_args()

    run_dir = _make_run_dir(args.live)
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT,
                        handlers=[logging.StreamHandler(), logging.FileHandler(run_dir / "bot.log")])
    log.info("run folder: %s", run_dir)
    # Imported here so offline checks work without the optibook package installed.
    from optibook.synchronous_client import Exchange

    exchange = Exchange()  # credentials are picked up from the workspace
    exchange.connect()  # kicks any other connection for our team
    try:
        return run(exchange, live=args.live, run_dir=None if args.no_record else run_dir)
    except Exception:
        log.exception("bot crashed")  # puts the traceback into bot.log too
        raise
    finally:
        exchange.disconnect()


if __name__ == "__main__":
    sys.exit(main())
