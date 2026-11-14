"""A simulated exchange that replays recorded market data for the real bot loop.

It implements only the client methods the bot calls (see runner.py, state.py,
market_data.py, orders.py) and returns objects with the same attributes as the
optibook types. Time is simulated: `clock()` is the replay time and `sleep(dt)`
advances it, applying every market event up to the new time.

Fill model (deliberately conservative where the data cannot tell us more):
- Aggressive orders (IOCs, and limit orders that cross the book on insert) take
  the recorded opposite side level by level, at the level's price, up to the
  displayed volume and our limit. Volume we take is remembered per price level
  until that instrument's next book snapshot, so we cannot take it twice.
- Resting limit orders fill only on a trade-through: a bid at P fills when a
  public trade prints strictly below P (an ask: strictly above), at P, for up to
  the trade's volume. A trade AT our price does not fill us, because we assume we
  are last in the queue. A resting order also fills, at its own price, when a new
  book snapshot shows the opposite side crossing it (up to the crossing volume).
- Zero latency: an order acts from the moment insert_order returns.
- No market impact: other participants never react to our orders. Replaying a
  live run's data also replays our own old quotes in the book.
- Self-trades are not modelled: our orders only trade against recorded data.

Exchange limits from https://2026-eth.optibook.net/challenge are enforced as
rejections, using the same worst-case rule as the bot
(position + all same-side resting + new volume).
Requests above 25 in any 1 s window are counted as rate violations; live, they
would disconnect us. Here the run continues so the whole period is evaluated.
"""

import logging
import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace

import config
from intent import ASK, BID, IOC, LIMIT

from backtest.data import BookEvent, Level, TickEvent

log = logging.getLogger(__name__)

TICK_SIZE = 0.1  # all eight stocks, https://2026-eth.optibook.net/challenge
EQUITY_SAMPLE_S = 1.0  # sim seconds between equity.csv rows
_EPS = 1e-9


def _dt(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, timezone.utc)


@dataclass(slots=True)
class Response:
    """Shaped like InsertOrderResponse / AmendOrderResponse / DeleteOrderResponse."""
    success: bool
    error_reason: str | None = None
    order_id: int | None = None


@dataclass(slots=True)
class Book:
    """Shaped like optibook's PriceBook."""
    timestamp: datetime
    instrument_id: str
    bids: list[Level]
    asks: list[Level]


@dataclass(slots=True)
class SimOrder:
    order_id: int
    instrument_id: str
    side: str
    price: float
    volume: int  # remaining resting volume
    inserted_at: float


@dataclass
class SimExchange:
    events: list
    tick_size: float = TICK_SIZE
    position_limit: int = config.POSITION_LIMIT
    max_outstanding: int = config.MAX_OUTSTANDING_VOLUME
    request_limit: int = config.EXCHANGE_REQUEST_LIMIT_PER_S

    # results, read by run.py after the backtest
    fill_rows: list = field(default_factory=list)
    order_rows: list = field(default_factory=list)
    equity_rows: list = field(default_factory=list)
    rate_violations: int = 0

    def __post_init__(self) -> None:
        if not self.events:
            raise ValueError("no market data events")
        self.instrument_ids = sorted({e.instrument_id for e in self.events})
        self.start_ts = self.events[0].ts
        self.end_ts = self.events[-1].ts
        self.now = self.start_ts
        self.finished = False  # data exhausted; set once sleep() has raised KeyboardInterrupt
        self._next_event = 0
        self._books: dict[str, Book] = {}
        self._taken: dict[str, dict] = {iid: {} for iid in self.instrument_ids}  # (side, ticks) -> lots we took
        self._last_trade: dict[str, float] = {}
        self._last_mid: dict[str, float] = {}
        self._new_ticks: dict[str, list] = {iid: [] for iid in self.instrument_ids}
        self._new_trades: dict[str, list] = {iid: [] for iid in self.instrument_ids}
        self.positions: dict[str, int] = {iid: 0 for iid in self.instrument_ids}
        self.cash = 0.0  # + sell proceeds − buy cost; no fees
        self._orders: dict[str, dict[int, SimOrder]] = {iid: {} for iid in self.instrument_ids}
        self._next_order_id = 1
        self._next_trade_id = 1
        self._request_times: deque[float] = deque()
        self._last_equity_ts = -math.inf
        self._apply_events_until(self.now)
        self._sample_equity(force=True)

    # --- simulated time -------------------------------------------------------------

    def clock(self) -> float:
        return self.now

    def sleep(self, dt: float) -> None:
        """Advance replay time. At end of data, raise KeyboardInterrupt once so the
        bot's normal Ctrl-C shutdown runs; later calls (shutdown waits) only move time."""
        if self.finished:
            self.now += dt
            return
        if self._next_event >= len(self.events):
            self.finished = True
            self._sample_equity(force=True)
            raise KeyboardInterrupt
        # max(): the loop never takes sim time, so dt is the full loop interval in practice.
        self.now += max(dt, 1e-3)
        self._apply_events_until(self.now)
        self._sample_equity()

    def _apply_events_until(self, t: float) -> None:
        while self._next_event < len(self.events) and self.events[self._next_event].ts <= t:
            event = self.events[self._next_event]
            self._next_event += 1
            if isinstance(event, BookEvent):
                self._on_book(event)
            else:
                self._on_tick(event)

    def _on_book(self, ev: BookEvent) -> None:
        iid = ev.instrument_id
        self._books[iid] = Book(_dt(ev.ts), iid, list(ev.bids), list(ev.asks))
        self._taken[iid] = {}  # a fresh snapshot already reflects what really traded
        if ev.bids and ev.asks:
            self._last_mid[iid] = 0.5 * (ev.bids[0].price + ev.asks[0].price)
        # Resting orders that the new book crosses fill at their own price.
        for order in self._by_priority(iid, BID):
            available = sum(l.volume for l in ev.asks if l.price <= order.price + _EPS)
            self._fill_resting(order, available, ev.ts, "cross")
        for order in self._by_priority(iid, ASK):
            available = sum(l.volume for l in ev.bids if l.price >= order.price - _EPS)
            self._fill_resting(order, available, ev.ts, "cross")

    def _on_tick(self, ev: TickEvent) -> None:
        iid = ev.instrument_id
        self._new_ticks[iid].append(SimpleNamespace(
            timestamp=_dt(ev.ts), instrument_id=iid, price=ev.price, volume=ev.volume,
            aggressor_side=ev.aggressor_side, buyer=ev.buyer, seller=ev.seller, trade_id=ev.trade_id))
        self._last_trade[iid] = ev.price
        # Trade-through: the printed volume is shared by our orders, best price first.
        remaining = ev.volume
        for order in self._by_priority(iid, BID):
            if remaining <= 0 or order.price <= ev.price + _EPS:
                break
            remaining -= self._fill_resting(order, remaining, ev.ts, "trade-through")
        remaining = ev.volume
        for order in self._by_priority(iid, ASK):
            if remaining <= 0 or order.price >= ev.price - _EPS:
                break
            remaining -= self._fill_resting(order, remaining, ev.ts, "trade-through")

    def _by_priority(self, iid: str, side: str) -> list[SimOrder]:
        orders = [o for o in self._orders[iid].values() if o.side == side]
        sign = -1 if side == BID else 1  # best bid = highest price
        return sorted(orders, key=lambda o: (sign * o.price, o.inserted_at, o.order_id))

    def _fill_resting(self, order: SimOrder, available: int, ts: float, reason: str) -> int:
        volume = min(order.volume, available)
        if volume <= 0:
            return 0
        self._book_fill(order.instrument_id, order.order_id, order.side, order.price, volume, ts, reason)
        order.volume -= volume
        if order.volume == 0:
            del self._orders[order.instrument_id][order.order_id]
        return volume

    def _book_fill(self, iid: str, order_id: int, side: str, price: float, volume: int,
                   ts: float, reason: str) -> None:
        signed = volume if side == BID else -volume
        self.positions[iid] += signed
        self.cash -= signed * price
        trade_id = self._next_trade_id
        self._next_trade_id += 1
        self._new_trades[iid].append(SimpleNamespace(
            timestamp=_dt(ts), order_id=order_id, trade_id=trade_id, instrument_id=iid,
            price=price, volume=volume, side=side))
        self.fill_rows.append({"sim_ts": ts, "instrument_id": iid, "order_id": order_id, "side": side,
                               "price": price, "volume": volume, "position": self.positions[iid],
                               "reason": reason})

    # --- valuation --------------------------------------------------------------------

    def mark_to_market(self, prices: dict[str, float]) -> float | None:
        """cash + Σ position × price; None if a non-zero position has no price."""
        value = self.cash
        for iid, pos in self.positions.items():
            if pos:
                if iid not in prices:
                    return None
                value += pos * prices[iid]
        return value

    def _sample_equity(self, force: bool = False) -> None:
        if not force and self.now - self._last_equity_ts < EQUITY_SAMPLE_S:
            return
        self._last_equity_ts = self.now
        row = {"sim_ts": self.now, "pnl_mid": self.mark_to_market(self._last_mid),
               "pnl_last": self.mark_to_market(self._last_trade), "cash": self.cash}
        for iid in self.instrument_ids:
            row[f"pos_{iid}"] = self.positions[iid]
            row[f"mid_{iid}"] = self._last_mid.get(iid)
        self.equity_rows.append(row)

    # --- request accounting -----------------------------------------------------------

    def _count_request(self) -> None:
        while self._request_times and self.now - self._request_times[0] >= 1.0:
            self._request_times.popleft()
        self._request_times.append(self.now)
        if len(self._request_times) > self.request_limit:
            self.rate_violations += 1
            log.warning("sim: %d requests in the last 1 s (limit %d); live this would disconnect us",
                        len(self._request_times), self.request_limit)

    def _log_order(self, iid, action, side="", price="", volume="", order_type="", order_id="",
                   response: Response | None = None) -> Response:
        response = response or Response(True)
        self.order_rows.append({"sim_ts": self.now, "instrument_id": iid, "action": action, "side": side,
                                "price": price, "volume": volume, "order_type": order_type,
                                "order_id": "" if order_id is None else order_id,
                                "success": response.success, "error": response.error_reason or ""})
        return response

    # --- client API used by the bot ---------------------------------------------------

    def is_connected(self) -> bool:
        return True

    def get_tradable_instruments(self) -> dict:
        return {iid: SimpleNamespace(instrument_id=iid, tick_size=self.tick_size, price_limit=None)
                for iid in self.instrument_ids}

    def get_last_price_book(self, iid: str):
        return self._books.get(iid)

    def poll_new_trade_ticks(self, iid: str) -> list:
        ticks, self._new_ticks[iid] = self._new_ticks[iid], []
        return ticks

    def poll_new_trades(self, iid: str) -> list:
        trades, self._new_trades[iid] = self._new_trades[iid], []
        return trades

    def get_positions(self) -> dict[str, int]:
        return dict(self.positions)

    def get_outstanding_orders(self, iid: str) -> dict:
        """Copies, like the client's snapshot: later fills do not change them."""
        return {oid: SimpleNamespace(order_id=oid, instrument_id=iid, price=o.price, volume=o.volume, side=o.side)
                for oid, o in self._orders[iid].items()}

    def get_pnl(self, valuations: dict | None = None) -> float:
        """Like the client: positions valued at the last public trade unless valuations are given."""
        value = self.mark_to_market({**self._last_trade, **(valuations or {})})
        if value is None:
            raise ValueError("no trade tick to value a non-zero position at")
        return value

    def _resting_volume(self, iid: str, side: str | None = None) -> int:
        return sum(o.volume for o in self._orders[iid].values() if side is None or o.side == side)

    def _reject_reason(self, iid: str, price: float, volume: int, side: str, order_type: str) -> str | None:
        if iid not in self._orders:
            return "unknown instrument"
        if side not in (BID, ASK) or order_type not in (LIMIT, IOC):
            return "invalid side or order type"
        if not (config.MIN_ORDER_VOLUME <= volume <= config.MAX_ORDER_VOLUME) or int(volume) != volume:
            return "volume out of range"
        if not (config.MIN_ORDER_PRICE <= price <= config.MAX_ORDER_PRICE):
            return "price out of range"
        if abs(price / self.tick_size - round(price / self.tick_size)) > 1e-6:
            return "price not on tick grid"
        position = self.positions[iid]
        same_side = self._resting_volume(iid, side)
        if side == BID and position + same_side + volume > self.position_limit:
            return "position limit (worst case)"
        if side == ASK and position - same_side - volume < -self.position_limit:
            return "position limit (worst case)"
        if order_type == LIMIT and self._resting_volume(iid) + volume > self.max_outstanding:
            return "outstanding volume limit"
        return None

    def _take_liquidity(self, iid: str, order_id: int, side: str, price: float, volume: int) -> int:
        """Aggress against the recorded book; returns the lots filled."""
        book = self._books.get(iid)
        if book is None:
            return 0
        levels = book.asks if side == BID else book.bids
        opposite = ASK if side == BID else BID
        taken = self._taken[iid]
        filled = 0
        for level in levels:
            if filled >= volume:
                break
            if (side == BID and level.price > price + _EPS) or (side == ASK and level.price < price - _EPS):
                break
            key = (opposite, round(level.price / self.tick_size))
            lots = min(volume - filled, level.volume - taken.get(key, 0))
            if lots <= 0:
                continue
            taken[key] = taken.get(key, 0) + lots
            filled += lots
            self._book_fill(iid, order_id, side, level.price, lots, self.now, "aggressive")
        return filled

    def insert_order(self, iid: str, *, price: float, volume: int, side: str, order_type: str = LIMIT):
        self._count_request()
        reason = self._reject_reason(iid, price, volume, side, order_type)
        if reason:
            return self._log_order(iid, "insert", side, price, volume, order_type, None,
                                   Response(False, reason))
        order_id = self._next_order_id
        self._next_order_id += 1
        filled = self._take_liquidity(iid, order_id, side, price, volume)
        if order_type == LIMIT and volume > filled:
            self._orders[iid][order_id] = SimOrder(order_id, iid, side, price, volume - filled, self.now)
        return self._log_order(iid, "insert", side, price, volume, order_type, order_id,
                               Response(True, order_id=order_id))

    def amend_order(self, iid: str, *, order_id: int, volume: int):
        """Volume-only amend. Only reductions are modelled (all the bot sends)."""
        self._count_request()
        order = self._orders.get(iid, {}).get(order_id)
        if order is None:
            response = Response(False, "order not found")
        elif not (config.MIN_ORDER_VOLUME <= volume <= order.volume):
            response = Response(False, "sim: only volume reductions are modelled")
        else:
            order.volume = volume
            response = Response(True)
        return self._log_order(iid, "amend", volume=volume, order_id=order_id, response=response)

    def delete_order(self, iid: str, *, order_id: int):
        self._count_request()
        removed = self._orders.get(iid, {}).pop(order_id, None)
        response = Response(True) if removed else Response(False, "order not found")
        return self._log_order(iid, "delete", order_id=order_id, response=response)

    def delete_orders(self, iid: str) -> None:
        """Counted as one request here; whether the exchange counts it per order is unverified."""
        self._count_request()
        self._orders[iid].clear()
        self._log_order(iid, "cancel_all")
