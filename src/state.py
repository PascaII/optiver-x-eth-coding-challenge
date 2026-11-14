"""Our own per-instrument state: position, resting orders, and fills.

The exchange client is the source of truth. Positions and outstanding orders
are re-read on every loop instead of being tracked locally, because the exchange
can cancel orders itself (e.g. after a price-limit change). Fills are tracked
separately via `poll_new_trades`, because an order can fill partly or fully
while it is being cancelled.
"""

from dataclasses import dataclass, field

from intent import ASK, BID


@dataclass(slots=True)
class FillStats:
    """Cumulative fills since this bot process started."""
    count: int = 0
    bought_volume: int = 0  # lots
    sold_volume: int = 0  # lots
    cash: float = 0.0  # + sell proceeds − buy cost, in price units; no fees assumed

    def add(self, trade) -> None:
        self.count += 1
        if trade.side == BID:  # our bid filled: we bought
            self.bought_volume += trade.volume
            self.cash -= trade.price * trade.volume
        else:
            self.sold_volume += trade.volume
            self.cash += trade.price * trade.volume


@dataclass(slots=True)
class InstrumentState:
    instrument_id: str
    position: int  # lots; + long, − short
    orders: dict  # order_id -> OrderStatus; our resting limit orders
    new_fills: list = field(default_factory=list)  # our Trades since the last loop
    fills: FillStats = field(default_factory=FillStats)

    @property
    def resting_bid_volume(self) -> int:
        return sum(o.volume for o in self.orders.values() if o.side == BID)

    @property
    def resting_ask_volume(self) -> int:
        return sum(o.volume for o in self.orders.values() if o.side == ASK)


class StateTracker:
    def __init__(self) -> None:
        self._fills: dict[str, FillStats] = {}

    def update(self, exchange, instrument_ids: list[str]) -> dict[str, InstrumentState]:
        positions = exchange.get_positions()
        states = {}
        for iid in instrument_ids:
            new_fills = exchange.poll_new_trades(iid)
            stats = self._fills.setdefault(iid, FillStats())
            for trade in new_fills:
                stats.add(trade)
            states[iid] = InstrumentState(
                instrument_id=iid,
                position=positions.get(iid, 0),
                orders=dict(exchange.get_outstanding_orders(iid)),
                new_fills=new_fills,
                fills=stats,
            )
        return states
