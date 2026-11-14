"""Per-loop market data snapshots built from the client's cached order books.

`get_last_price_book` returns the last book the client received; it does not
send a request. We measure staleness locally: the age is the time since we last
saw the book's timestamp change. This avoids comparing the exchange clock with
our clock.
"""

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class MarketSnapshot:
    instrument_id: str
    bids: list  # PriceVolume levels, best (highest) first; empty if none
    asks: list  # PriceVolume levels, best (lowest) first; empty if none
    book_age_s: float  # seconds since the book last changed; inf if no book seen yet
    trade_ticks: list = field(default_factory=list)  # public TradeTicks since the last loop
    book_changed: bool = False  # book timestamp differs from the previous loop's
    exchange_ts: Any = None  # the book's own timestamp; None if no book seen yet

    @property
    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None

    @property
    def is_two_sided(self) -> bool:
        return bool(self.bids) and bool(self.asks)

    @property
    def mid(self) -> float | None:
        """Valuation reference only, not an executable price."""
        if not self.is_two_sided:
            return None
        return 0.5 * (self.bids[0].price + self.asks[0].price)

    def is_stale(self, max_age_s: float) -> bool:
        return self.book_age_s > max_age_s


class MarketDataTracker:
    def __init__(self) -> None:
        self._last_book_ts: dict[str, Any] = {}  # exchange timestamp of the last book seen
        self._last_change_at: dict[str, float] = {}  # local monotonic time it changed

    def update(self, exchange, instrument_ids: list[str], now: float) -> dict[str, MarketSnapshot]:
        snapshots = {}
        for iid in instrument_ids:
            book = exchange.get_last_price_book(iid)
            changed = False
            if book is None:
                bids, asks, age = [], [], math.inf
            else:
                if self._last_book_ts.get(iid) != book.timestamp:
                    changed = True
                    self._last_book_ts[iid] = book.timestamp
                    self._last_change_at[iid] = now
                bids, asks = book.bids, book.asks
                age = now - self._last_change_at[iid]
            snapshots[iid] = MarketSnapshot(
                instrument_id=iid,
                bids=bids,
                asks=asks,
                book_age_s=age,
                trade_ticks=exchange.poll_new_trade_ticks(iid),
                book_changed=changed,
                exchange_ts=None if book is None else book.timestamp,
            )
        return snapshots
