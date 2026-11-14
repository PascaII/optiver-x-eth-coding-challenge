"""Order execution: move our resting orders towards the desired set, within limits.

`execute_all` applies steps 1–2 across all instruments before steps 3–4:
  1. delete resting orders that are not wanted,
  2. amend down resting orders that are too large (amend changes volume only),
  3. insert missing limit orders,
  4. send IOC orders.
Steps 1–2 only reduce exposure, so they go first. Before every insert or IOC, we
re-check worst-case exposure against what is still resting, including orders
whose delete failed. A price change is a delete + insert, which costs two requests.

The desired-vs-current diff follows the shape of smm's OMS
(MIT, references/beatzxbt/smm/src/strategy/oms.py), heavily simplified.

Failure handling: a rejected request is logged and nothing is retried. An
exception (e.g. timeout) means unknown state. That instrument is blocked for
the rest of this process: cache reads cannot prove an unresolved request failed.
Pascal must stop the process and reconcile before starting it again.
"""

import logging

import config
from intent import ASK, BID, IOC, LIMIT, OrderIntent
from rate_limiter import RateLimiter
from recorder import NullRecorder
from risk import max_buy_volume, max_sell_volume

log = logging.getLogger(__name__)


class _Stop(Exception):
    """Unknown order state: stop this instrument."""


class _BudgetExhausted(_Stop):
    """Resume at this instrument when request budget becomes available."""


class OrderManager:
    def __init__(self, exchange, limiter: RateLimiter, recorder=None) -> None:
        self.exchange = exchange
        self.limiter = limiter
        self.recorder = recorder or NullRecorder()
        self.blocked: set[str] = set()
        self._reduce_next: str | None = None
        self._insert_next: str | None = None

    def _send(self, iid: str, action: str, fn, **kwargs):
        """Send one counted request. Returns the response; raises _Stop if we must stop."""
        if not self.limiter.try_acquire():
            log.warning("%s: request budget exhausted, skipping %s %s until next loop", iid, action, kwargs)
            raise _BudgetExhausted
        try:
            response = fn(iid, **kwargs)
        except Exception as exc:  # noqa: BLE001 - any failure leaves the order state unknown
            self.blocked.add(iid)
            self.recorder.record_order(iid, action, kwargs, error=exc)
            log.error("%s: %s %s raised %r; order state UNKNOWN, instrument blocked until process exit",
                      iid, action, kwargs, exc)
            raise _Stop from exc
        self.recorder.record_order(iid, action, kwargs, response=response)
        if not response.success:
            log.warning("%s: %s %s rejected: %s", iid, action, kwargs, response.error_reason)
        return response

    def execute(self, iid: str, intents: list[OrderIntent], orders: dict, tick: float) -> None:
        """Single-instrument helper; the runner uses execute_all for global priority."""
        try:
            pending = self._reduce(iid, intents, orders, tick)
            self._place(iid, pending)
        except _Stop:
            return

    @staticmethod
    def _starting_at(instrument_ids: list[str], first: str | None) -> list[str]:
        index = instrument_ids.index(first) if first in instrument_ids else 0
        return instrument_ids[index:] + instrument_ids[:index]

    def execute_all(self, wanted: dict[str, list[OrderIntent]], states: dict, instruments: dict) -> None:
        """Reduce globally before inserting; resume budget-limited work fairly.

        If reductions exhaust the budget, send no insertions in this batch. Keep
        the first unfinished instrument at the front even across empty-budget loops.
        """
        instrument_ids = list(wanted)
        pending = {}
        for iid in self._starting_at(instrument_ids, self._reduce_next):
            try:
                pending[iid] = self._reduce(
                    iid, wanted[iid], states[iid].orders, instruments[iid].tick_size
                )
            except _BudgetExhausted:
                self._reduce_next = iid
                return
            except _Stop:
                continue
        self._reduce_next = None
        for iid in self._starting_at(instrument_ids, self._insert_next):
            if iid not in pending:
                continue
            try:
                self._place(iid, pending[iid])
            except _BudgetExhausted:
                self._insert_next = iid
                return
            except _Stop:
                continue
        self._insert_next = None

    def _reduce(self, iid: str, intents: list[OrderIntent], orders: dict, tick: float) -> list[OrderIntent]:
        """Keep matching volume, delete/amend excess, and return missing orders."""
        if iid in self.blocked:
            return []

        # --- diff desired limit orders against resting orders, by (side, price) ---
        desired = {}  # (side, price in ticks) -> [price, lots still needed]
        for intent in intents:
            if intent.order_type == LIMIT:
                key = (intent.side, round(intent.price / tick))
                if key not in desired:
                    desired[key] = [intent.price, 0]
                desired[key][1] += intent.volume
        keep: set[int] = set()
        amends: list[tuple[int, int]] = []  # (order_id, new smaller volume)
        inserts: list[OrderIntent] = []
        # Retain matching orders in snapshot order, including supplementary orders.
        for oid, order in orders.items():
            key = (order.side, round(order.price / tick))
            target = desired.get(key)
            if target is None or target[1] == 0:
                continue
            keep.add(oid)
            volume = min(order.volume, target[1])
            target[1] -= volume
            if order.volume > volume:
                amends.append((oid, volume))
        for (side, _), (price, volume) in desired.items():
            if volume:
                inserts.append(OrderIntent(side, price, volume))
        for oid in orders:
            if oid not in keep:
                self._send(iid, "delete", self.exchange.delete_order, order_id=oid)
        for oid, volume in amends:
            self._send(iid, "amend", self.exchange.amend_order, order_id=oid, volume=volume)
        return inserts + [intent for intent in intents if intent.order_type == IOC]

    def _place(self, iid: str, pending: list[OrderIntent]) -> None:
        if iid in self.blocked or not pending:
            return
        try:
            # Copy order quantities BEFORE reading position. A fill between these
            # reads is conservatively counted both in position and reserved volume.
            # This also includes fills during successful or rejected cancellations.
            resting = {oid: (o.side, o.volume)
                       for oid, o in self.exchange.get_outstanding_orders(iid).items()}
            position = self.exchange.get_positions()[iid]
        except Exception as exc:
            self.blocked.add(iid)
            log.error("%s: exposure read failed: %r; instrument blocked until process exit", iid, exc)
            raise _Stop from exc
        ioc_sent = {BID: 0, ASK: 0}  # assume full fills; never net opposite sides
        for intent in pending:
            self._insert(iid, intent, position, resting, ioc_sent)

    def _insert(self, iid: str, intent: OrderIntent, position: int, resting: dict, ioc_sent: dict) -> None:
        bids = sum(v for s, v in resting.values() if s == BID) + ioc_sent[BID]
        asks = sum(v for s, v in resting.values() if s == ASK) + ioc_sent[ASK]
        room = max_buy_volume(position, bids) if intent.side == BID else max_sell_volume(position, asks)
        if intent.order_type == LIMIT:
            room = min(room, config.MAX_OUTSTANDING_VOLUME - sum(v for _, v in resting.values()))
        volume = min(intent.volume, room)
        if volume < config.MIN_ORDER_VOLUME:
            log.info("%s: no room for %s (position %d, resting bids %d / asks %d)", iid, intent, position, bids, asks)
            return
        response = self._send(iid, "insert", self.exchange.insert_order, price=intent.price,
                              volume=volume, side=intent.side, order_type=intent.order_type)
        if not response.success:
            return
        if intent.order_type == LIMIT:
            resting[response.order_id] = (intent.side, volume)
        else:
            ioc_sent[intent.side] += volume

    def cancel_all(self, instrument_ids: list[str]) -> None:
        """Delete all our resting orders. Does not touch positions.

        Unverified: whether `delete_orders` counts as one request or one per order.
        We count it as one per order to be safe.
        """
        for iid in instrument_ids:
            try:
                n_orders = len(self.exchange.get_outstanding_orders(iid))
                if n_orders == 0:
                    continue
                self.limiter.acquire_blocking(n_orders)
                self.exchange.delete_orders(iid)
                self.recorder.record_order(iid, "cancel_all", {})
                log.info("%s: cancelled %d resting orders", iid, n_orders)
            except Exception as exc:  # noqa: BLE001 - keep cancelling the other instruments
                self.recorder.record_order(iid, "cancel_all", {}, error=exc)
                log.error("%s: cancel_all failed: %r", iid, exc)
