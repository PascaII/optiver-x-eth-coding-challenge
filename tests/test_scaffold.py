"""Offline checks for the bot scaffold. Uses a fake exchange; never connects to Optibook."""

from types import SimpleNamespace

import pytest

import config
from intent import ASK, BID, IOC, OrderIntent
from market_data import MarketDataTracker
from orders import OrderManager
from rate_limiter import RateLimiter
from risk import clip_intents, max_buy_volume, max_sell_volume, round_to_tick

INSTRUMENT = SimpleNamespace(instrument_id="NVDA", tick_size=0.1)


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class FakeExchange:
    """Implements only the client methods the scaffold uses."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.orders: dict[int, SimpleNamespace] = {}
        self.next_id = 1
        self.reject_deletes = False
        self.book = None
        self.positions = {"NVDA": 0}

    def _ok(self, **kw):
        return SimpleNamespace(success=True, error_reason=None, **kw)

    def insert_order(self, iid, *, price, volume, side, order_type="limit"):
        self.calls.append(("insert", side, price, volume, order_type))
        self.positions.setdefault(iid, 0)
        oid = self.next_id
        self.next_id += 1
        if order_type == "limit":
            self.orders[oid] = SimpleNamespace(order_id=oid, instrument_id=iid, price=price, volume=volume, side=side)
        return self._ok(order_id=oid)

    def amend_order(self, iid, *, order_id, volume):
        self.calls.append(("amend", order_id, volume))
        self.orders[order_id].volume = volume
        return self._ok()

    def delete_order(self, iid, *, order_id):
        self.calls.append(("delete", order_id))
        if self.reject_deletes:
            return SimpleNamespace(success=False, error_reason="test reject")
        self.orders.pop(order_id, None)
        return self._ok()

    def get_outstanding_orders(self, iid):
        return {oid: o for oid, o in self.orders.items() if o.instrument_id == iid}

    def get_positions(self):
        return dict(self.positions)

    def get_last_price_book(self, iid):
        return self.book

    def poll_new_trade_ticks(self, iid):
        return []


def rest(ex: FakeExchange, side: str, price: float, volume: int) -> int:
    ex.insert_order("NVDA", price=price, volume=volume, side=side)
    ex.calls.clear()
    return ex.next_id - 1


def manager(ex: FakeExchange, budget: int = 100) -> OrderManager:
    return OrderManager(ex, RateLimiter(budget, clock=FakeClock()))


# --- risk ---------------------------------------------------------------------

def test_round_to_tick_never_more_aggressive():
    assert round_to_tick(101.37, 0.1, BID) == 101.3
    assert round_to_tick(101.37, 0.1, ASK) == 101.4
    # Already on the grid despite float noise (101.3 / 0.1 = 1012.9999...).
    assert round_to_tick(101.3, 0.1, BID) == 101.3
    assert round_to_tick(101.3, 0.1, ASK) == 101.3


def test_worst_case_volume_at_boundaries():
    assert max_buy_volume(position=75, resting_bid_volume=0) == 25
    assert max_buy_volume(position=75, resting_bid_volume=25) == 0
    assert max_buy_volume(position=100, resting_bid_volume=0) == 0
    assert max_sell_volume(position=-100, resting_ask_volume=0) == 0
    assert max_sell_volume(position=100, resting_ask_volume=0) == 200


def test_clip_caps_position_and_does_not_net_sides():
    intents = [OrderIntent(BID, 100.0, 50), OrderIntent(ASK, 100.2, 50)]
    clipped = clip_intents(intents, position=75, instrument=INSTRUMENT)
    assert clipped == [OrderIntent(BID, 100.0, 25), OrderIntent(ASK, 100.2, 50)]


def test_clip_caps_outstanding_volume_but_not_iocs():
    intents = [OrderIntent(ASK, 99.0, 10, IOC), OrderIntent(BID, 100.0, 100),
               OrderIntent(ASK, 100.2, 100), OrderIntent(ASK, 100.4, 100)]
    clipped = clip_intents(intents, position=100, instrument=INSTRUMENT)
    # Position +100: no bids; ask room 200 (to −100), IOC takes 10 first in list order.
    assert clipped == [OrderIntent(ASK, 99.0, 10, IOC), OrderIntent(ASK, 100.2, 100), OrderIntent(ASK, 100.4, 90)]
    assert sum(i.volume for i in clipped if i.order_type == "limit") <= config.MAX_OUTSTANDING_VOLUME


def test_clip_drops_price_outside_instrument_limit():
    inst = SimpleNamespace(tick_size=0.1, price_limit={"min": 90.0, "max": 110.0})
    clipped = clip_intents([OrderIntent(BID, 80.0, 1), OrderIntent(ASK, 100.0, 1)], 0, inst)
    assert clipped == [OrderIntent(ASK, 100.0, 1)]


# --- rate limiter ------------------------------------------------------------

def test_rate_limiter_window():
    clock = FakeClock()
    rl = RateLimiter(3, clock=clock)
    assert rl.try_acquire() and rl.try_acquire(2)
    assert not rl.try_acquire()
    clock.t = 0.999
    assert not rl.try_acquire()
    clock.t = 1.0
    assert rl.try_acquire(3)


# --- order manager -------------------------------------------------------------

def test_reprice_deletes_before_insert():
    ex = FakeExchange()
    oid = rest(ex, BID, 100.0, 10)
    manager(ex).execute("NVDA", [OrderIntent(BID, 100.1, 10)], ex.get_outstanding_orders("NVDA"), 0.1)
    assert ex.calls == [("delete", oid), ("insert", BID, 100.1, 10, "limit")]


def test_matching_order_is_kept_and_shrunk_by_amend():
    ex = FakeExchange()
    oid = rest(ex, ASK, 100.3, 10)
    om = manager(ex)
    om.execute("NVDA", [OrderIntent(ASK, 100.3, 10)], ex.get_outstanding_orders("NVDA"), 0.1)
    assert ex.calls == []
    om.execute("NVDA", [OrderIntent(ASK, 100.3, 4)], ex.get_outstanding_orders("NVDA"), 0.1)
    assert ex.calls == [("amend", oid, 4)]


def test_growing_order_keeps_priority_and_inserts_difference():
    ex = FakeExchange()
    rest(ex, BID, 100.0, 4)
    manager(ex).execute("NVDA", [OrderIntent(BID, 100.0, 10)], ex.get_outstanding_orders("NVDA"), 0.1)
    assert ex.calls == [("insert", BID, 100.0, 6, "limit")]


def test_failed_delete_still_counts_toward_exposure():
    ex = FakeExchange()
    rest(ex, BID, 100.0, 80)
    ex.reject_deletes = True
    manager(ex).execute("NVDA", [OrderIntent(BID, 100.5, 80)], ex.get_outstanding_orders("NVDA"), 0.1)
    # Old 80-lot bid may still fill, so only 20 more lots fit under +100.
    assert ex.calls[-1] == ("insert", BID, 100.5, 20, "limit")


def test_ioc_counts_as_filled_for_later_iocs():
    ex = FakeExchange()
    intents = [OrderIntent(BID, 101.0, 60, IOC), OrderIntent(BID, 101.0, 60, IOC)]
    manager(ex).execute("NVDA", intents, {}, 0.1)
    assert [c[3] for c in ex.calls] == [60, 40]


def test_budget_exhaustion_stops_after_deletes():
    ex = FakeExchange()
    oid = rest(ex, BID, 100.0, 10)
    manager(ex, budget=1).execute("NVDA", [OrderIntent(BID, 100.1, 10)], ex.get_outstanding_orders("NVDA"), 0.1)
    assert ex.calls == [("delete", oid)]


def test_exception_stops_instrument_without_retry():
    ex = FakeExchange()

    def boom(*a, **k):
        ex.calls.append(("insert-timeout",))
        raise TimeoutError

    ex.insert_order = boom
    manager(ex).execute("NVDA", [OrderIntent(BID, 100.0, 5), OrderIntent(ASK, 100.2, 5)], {}, 0.1)
    assert ex.calls == [("insert-timeout",)]


def test_grown_quote_stays_in_place_and_shrinks_across_multiple_orders():
    ex = FakeExchange()
    original = rest(ex, BID, 100.0, 4)
    om = manager(ex)
    desired = [OrderIntent(BID, 100.0, 10)]
    om.execute("NVDA", desired, ex.get_outstanding_orders("NVDA"), 0.1)
    supplement = ex.next_id - 1
    ex.calls.clear()
    for _ in range(3):
        om.execute("NVDA", desired, ex.get_outstanding_orders("NVDA"), 0.1)
    assert ex.calls == []
    om.execute("NVDA", [OrderIntent(BID, 100.0, 7)], ex.get_outstanding_orders("NVDA"), 0.1)
    assert ex.calls == [("amend", supplement, 3)]
    assert ex.orders[original].volume == 4


def test_same_price_intents_share_existing_volume():
    ex = FakeExchange()
    rest(ex, BID, 100.0, 10)
    manager(ex).execute("NVDA", [OrderIntent(BID, 100.0, 4), OrderIntent(BID, 100.0, 6)],
                        ex.get_outstanding_orders("NVDA"), 0.1)
    assert ex.calls == []


@pytest.mark.parametrize("side,position", [(BID, 80), (ASK, -80)])
def test_fill_during_cancel_reduces_replacement_room(side, position):
    ex = FakeExchange()
    oid = rest(ex, side, 100.0, 100)
    delete = ex.delete_order

    def fill_then_delete(iid, *, order_id):
        ex.positions[iid] = position
        ex.orders[order_id].volume -= 80
        return delete(iid, order_id=order_id)

    ex.delete_order = fill_then_delete
    manager(ex).execute("NVDA", [OrderIntent(side, 100.1, 100)],
                        ex.get_outstanding_orders("NVDA"), 0.1)
    assert ex.calls == [("delete", oid), ("insert", side, 100.1, 20, "limit")]


def test_fill_during_rejected_cancel_is_counted_in_position_and_remainder():
    ex = FakeExchange()
    rest(ex, BID, 100.0, 100)
    ex.reject_deletes = True
    delete = ex.delete_order

    def fill_then_reject(iid, *, order_id):
        ex.positions[iid] += 80
        ex.orders[order_id].volume -= 80
        return delete(iid, order_id=order_id)

    ex.delete_order = fill_then_reject
    manager(ex).execute("NVDA", [OrderIntent(BID, 100.1, 100)],
                        ex.get_outstanding_orders("NVDA"), 0.1)
    assert [c[0] for c in ex.calls] == ["delete"]


def test_fill_between_order_and_position_reads_cannot_create_room():
    ex = FakeExchange()
    oid = rest(ex, BID, 100.0, 60)

    def fill_before_position_read():
        ex.positions["NVDA"] = 60
        ex.orders.pop(oid)
        return dict(ex.positions)

    ex.get_positions = fill_before_position_read
    manager(ex).execute("NVDA", [OrderIntent(BID, 100.0, 60), OrderIntent(BID, 99.9, 40)],
                        ex.get_outstanding_orders("NVDA"), 0.1)
    assert ex.calls == []  # double counting the observed fill is deliberately conservative


@pytest.mark.parametrize("action", ["insert", "delete", "amend"])
def test_ambiguous_request_blocks_instrument_across_loops(action):
    ex = FakeExchange()
    if action != "insert":
        rest(ex, BID, 100.0, 10)

    def timeout(*args, **kwargs):
        ex.calls.append((action + "-timeout",))
        raise TimeoutError

    setattr(ex, action + "_order", timeout)
    desired = [OrderIntent(BID, 100.0 if action == "amend" else 100.1, 5)]
    om = manager(ex)
    om.execute("NVDA", desired, ex.get_outstanding_orders("NVDA"), 0.1)
    for _ in range(3):
        om.execute("NVDA", desired, ex.get_outstanding_orders("NVDA"), 0.1)
    assert om.blocked == {"NVDA"}
    assert ex.calls == [(action + "-timeout",)]


def test_failed_exposure_read_blocks_insertions():
    ex = FakeExchange()

    def fail():
        raise RuntimeError("cache unavailable")

    ex.get_positions = fail
    om = manager(ex)
    om.execute("NVDA", [OrderIntent(BID, 100.0, 5)], {}, 0.1)
    assert om.blocked == {"NVDA"} and ex.calls == []


def test_unknown_instrument_does_not_block_other_instruments():
    ex = FakeExchange()
    ex.positions.update(A=0, B=0)
    insert = ex.insert_order

    def timeout_on_a(iid, **kwargs):
        if iid == "A":
            raise TimeoutError
        return insert(iid, **kwargs)

    ex.insert_order = timeout_on_a
    om = manager(ex)
    instruments = {"A": INSTRUMENT, "B": INSTRUMENT}
    states = {iid: SimpleNamespace(orders={}) for iid in instruments}
    wanted = {iid: [OrderIntent(BID, 100.0, 10)] for iid in instruments}
    om.execute_all(wanted, states, instruments)
    assert om.blocked == {"A"}
    assert ex.get_outstanding_orders("B")


def test_definitive_rejection_does_not_block_instrument():
    ex = FakeExchange()
    insert = ex.insert_order

    def reject_bid(iid, **kwargs):
        if kwargs["side"] == BID:
            return SimpleNamespace(success=False, error_reason="price limit")
        return insert(iid, **kwargs)

    ex.insert_order = reject_bid
    om = manager(ex)
    om.execute("NVDA", [OrderIntent(BID, 100.0, 5), OrderIntent(ASK, 101.0, 5)], {}, 0.1)
    assert not om.blocked
    assert ex.calls == [("insert", ASK, 101.0, 5, "limit")]


def test_global_cancellations_precede_replacements():
    ex = FakeExchange()
    instruments = {iid: INSTRUMENT for iid in ["A", "B", "C", "D", "E", "VRT"]}
    for iid in instruments:
        ex.insert_order(iid, price=100.0, volume=10, side=BID)
        ex.insert_order(iid, price=101.0, volume=10, side=ASK)
    ex.calls.clear()
    states = {iid: SimpleNamespace(orders=ex.get_outstanding_orders(iid)) for iid in instruments}
    wanted = {iid: [OrderIntent(BID, 100.1, 10), OrderIntent(ASK, 101.1, 10)]
              for iid in instruments}
    wanted["VRT"] = []  # stale book: withdraw both quotes
    manager(ex, budget=20).execute_all(wanted, states, instruments)
    actions = [c[0] for c in ex.calls]
    assert actions[:12] == ["delete"] * 12
    assert actions[12:] == ["insert"] * 8
    assert not ex.get_outstanding_orders("VRT")


def test_budget_exhaustion_resumes_unfinished_cancels_before_busy_instrument():
    ex = FakeExchange()
    clock = FakeClock()
    om = OrderManager(ex, RateLimiter(2, clock=clock))
    instruments = {"A": INSTRUMENT, "B": INSTRUMENT}

    def add_quotes(iid):
        ex.insert_order(iid, price=100.0, volume=10, side=BID)
        ex.insert_order(iid, price=101.0, volume=10, side=ASK)

    def execute():
        states = {iid: SimpleNamespace(orders=ex.get_outstanding_orders(iid)) for iid in instruments}
        om.execute_all({"A": [], "B": []}, states, instruments)

    add_quotes("A")
    add_quotes("B")
    execute()  # budget used cancelling A
    assert ex.get_outstanding_orders("B")
    add_quotes("A")  # continuing activity must not starve B
    execute()  # still no budget: preserve B as the first unfinished instrument
    clock.t = 1.0
    execute()
    assert not ex.get_outstanding_orders("B")
    assert ex.get_outstanding_orders("A")


def test_budget_exhaustion_resumes_insertions_at_unfinished_instrument():
    ex = FakeExchange()
    clock = FakeClock()
    om = OrderManager(ex, RateLimiter(1, clock=clock))
    instruments = {"A": INSTRUMENT, "B": INSTRUMENT}
    ex.positions.update(A=0, B=0)
    states = {iid: SimpleNamespace(orders={}) for iid in instruments}
    wanted = {iid: [OrderIntent(BID, 100.0, 10)] for iid in instruments}
    om.execute_all(wanted, states, instruments)
    assert ex.get_outstanding_orders("A") and not ex.get_outstanding_orders("B")
    # Simulate A filling before the next loop, so it wants another insertion too.
    ex.orders.clear()
    ex.positions["A"] = 10
    om.execute_all(wanted, states, instruments)  # preserve cursor with no budget
    clock.t = 1.0
    om.execute_all(wanted, states, instruments)
    assert ex.get_outstanding_orders("B") and not ex.get_outstanding_orders("A")


# --- market data ---------------------------------------------------------------

def test_book_age_and_missing_book():
    ex = FakeExchange()
    md = MarketDataTracker()
    snap = md.update(ex, ["NVDA"], now=0.0)["NVDA"]
    assert snap.is_stale(config.BOOK_STALE_AFTER_S) and not snap.is_two_sided and snap.mid is None

    lvl = SimpleNamespace
    ex.book = SimpleNamespace(timestamp=1, bids=[lvl(price=100.0, volume=5)], asks=[lvl(price=100.2, volume=3)])
    snap = md.update(ex, ["NVDA"], now=10.0)["NVDA"]
    assert snap.book_age_s == 0.0 and snap.mid == pytest.approx(100.1)
    snap = md.update(ex, ["NVDA"], now=16.0)["NVDA"]  # same book timestamp
    assert snap.is_stale(config.BOOK_STALE_AFTER_S)
