"""Download Optibook historical data into the backtester's CSV format.

Run in the event workspace (needs the optibook package and pandas):

    python src/backtest/fetch_history.py --start 2026-11-06T09:00 --end 2026-11-06T12:00

Only downloads data; it never connects an Exchange or sends orders. Times are
UTC. Writes runs/history/<start>_<end>/books.csv and trade_ticks.csv, which
`src/backtest/run.py` reads.

Historical pricebooks have full depth; we keep the top BOOK_DEPTH levels, the
same as our own recordings. Loads are capped at one day per call, so the range
is fetched in day-sized chunks.
"""

import argparse
import csv
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))

from recorder import BOOK_DEPTH  # noqa: E402

HISTORY_DIR = SRC.parent / "runs" / "history"


def _naive_utc(text: str) -> datetime:
    """The loader examples pass naive UTC datetimes (datetime.utcnow())."""
    dt = datetime.fromisoformat(text)
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _ns(value) -> int:
    return int(value.value) if hasattr(value, "value") else int(value)  # pandas Timestamp or int


def _side(levels_json: str) -> tuple[list, list]:
    """'[[price, volume], ...]' -> (prices, volumes), top BOOK_DEPTH, blank-padded."""
    levels = json.loads(levels_json) if levels_json else []
    prices, volumes = [], []
    for level in levels[:BOOK_DEPTH]:
        price, volume = (level["price"], level["volume"]) if isinstance(level, dict) else level
        prices.append(price)
        volumes.append(volume)
    pad = [""] * (BOOK_DEPTH - len(prices))
    return prices + pad, volumes + pad


def _chunks(start: datetime, end: datetime):
    while start < end:
        chunk_end = min(start + timedelta(days=1), end)
        yield start, chunk_end
        start = chunk_end


def main() -> int:
    parser = argparse.ArgumentParser(description="Download historical books and trades as CSV")
    parser.add_argument("--start", required=True, help="UTC start, ISO format, e.g. 2026-11-06T09:00")
    parser.add_argument("--end", required=True, help="UTC end, ISO format")
    parser.add_argument("--out", type=Path, help="output folder (default: runs/history/<start>_<end>)")
    args = parser.parse_args()
    start, end = _naive_utc(args.start), _naive_utc(args.end)
    out = args.out or HISTORY_DIR / f"{start:%Y%m%d-%H%M}_{end:%Y%m%d-%H%M}"
    out.mkdir(parents=True, exist_ok=True)

    from optibook.historical_data_loader import HistoricalDataLoader
    loader = HistoricalDataLoader()

    book_cols = [f"{side}_{kind}_{i}" for side in ("bid", "ask") for kind in ("px", "vol")
                 for i in range(1, BOOK_DEPTH + 1)]
    n_books = n_ticks = 0
    with open(out / "books.csv", "w", newline="") as fb, open(out / "trade_ticks.csv", "w", newline="") as ft:
        books, ticks = csv.writer(fb), csv.writer(ft)
        books.writerow(["exchange_ts", "instrument_id"] + book_cols)
        ticks.writerow(["exchange_ts", "instrument_id", "trade_id", "price", "volume",
                        "aggressor_side", "buyer", "seller"])
        for chunk_start, chunk_end in _chunks(start, end):
            print(f"fetching {chunk_start} -> {chunk_end}")
            for row in loader.get_pricebooks_df(chunk_start, chunk_end).itertuples(index=False):
                bid_px, bid_vol = _side(row.bids_json)
                ask_px, ask_vol = _side(row.asks_json)
                books.writerow([_ns(row.timestamp_ns), row.instrument_id] + bid_px + bid_vol + ask_px + ask_vol)
                n_books += 1
            for row in loader.get_trades_df(chunk_start, chunk_end).itertuples(index=False):
                ticks.writerow([_ns(row.timestamp_ns), row.instrument_id, row.trade_id, row.price, row.volume,
                                row.aggressor_side, row.buyer, row.seller])
                n_ticks += 1
    print(f"wrote {n_books} book rows and {n_ticks} trade rows to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
