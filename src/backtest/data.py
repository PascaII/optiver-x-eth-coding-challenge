"""Load market data for a backtest from a folder in our recorder's CSV format.

Expected files (see README.md, "Run data"; fetch_history.py writes the same format):
  books.csv        exchange_ts, instrument_id, bid_px_1.., bid_vol_1.., ask_px_1.., ask_vol_1..
  trade_ticks.csv  exchange_ts, instrument_id, price, volume, aggressor_side, buyer, seller, trade_id

Other columns (e.g. local_ts) are ignored. Blank level cells mean "no level".
The number of book levels is read from the header, so any depth works.
"""

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(slots=True, frozen=True)
class Level:
    """Same attributes as optibook's PriceVolume."""
    price: float
    volume: int


@dataclass(slots=True)
class BookEvent:
    ts: float  # seconds since the Unix epoch, UTC
    instrument_id: str
    bids: list[Level]  # best (highest) first
    asks: list[Level]  # best (lowest) first


@dataclass(slots=True)
class TickEvent:
    """One public trade, as recorded."""
    ts: float
    instrument_id: str
    price: float
    volume: int
    aggressor_side: str
    buyer: str
    seller: str
    trade_id: str


def parse_ts(value) -> float:
    """Timestamp cell -> epoch seconds (UTC).

    Accepts an integer nanosecond count (historical loader's timestamp_ns),
    epoch seconds, or an ISO datetime string such as str(datetime) written by
    our recorder. Naive datetimes are taken as UTC. Anything else raises.
    """
    text = str(value).strip()
    try:
        number = float(text)
    except ValueError:
        pass
    else:
        return number / 1e9 if number > 1e14 else number  # > 1e14 cannot be seconds
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _levels(row: dict, side: str, depth: int) -> list[Level]:
    levels = []
    for i in range(1, depth + 1):
        px, vol = row.get(f"{side}_px_{i}", ""), row.get(f"{side}_vol_{i}", "")
        if px in ("", None) or vol in ("", None):
            break  # levels are written best-first, padded with blanks
        levels.append(Level(float(px), int(float(vol))))
    return levels


def load_books(path: Path) -> list[BookEvent]:
    events = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        depth = sum(1 for name in reader.fieldnames or [] if name.startswith("bid_px_"))
        for row in reader:
            events.append(BookEvent(parse_ts(row["exchange_ts"]), row["instrument_id"],
                                    _levels(row, "bid", depth), _levels(row, "ask", depth)))
    return events


def load_ticks(path: Path) -> list[TickEvent]:
    events = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            events.append(TickEvent(parse_ts(row["exchange_ts"]), row["instrument_id"], float(row["price"]),
                                    int(float(row["volume"])), row.get("aggressor_side", ""),
                                    row.get("buyer", ""), row.get("seller", ""), row.get("trade_id", "")))
    return events


def load_events(data_dir: Path) -> list[BookEvent | TickEvent]:
    """All events in time order. At equal timestamps, books come before ticks."""
    data_dir = Path(data_dir)
    books_path, ticks_path = data_dir / "books.csv", data_dir / "trade_ticks.csv"
    if not books_path.exists():
        raise FileNotFoundError(f"{books_path} not found; a backtest needs at least order books")
    books = load_books(books_path)
    ticks = load_ticks(ticks_path) if ticks_path.exists() else []
    events = books + ticks  # sort is stable, so books stay ahead of ticks at equal ts
    events.sort(key=lambda e: e.ts)
    if not events:
        raise ValueError(f"no market data rows in {data_dir}")
    return events
