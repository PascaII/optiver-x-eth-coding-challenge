"""Offline checks for run recording. Uses a fake exchange; never connects to Optibook."""

import csv
from datetime import datetime
from types import SimpleNamespace

import runner
from intent import ASK, BID, OrderIntent
from market_data import MarketDataTracker
from orders import OrderManager
from rate_limiter import RateLimiter
from recorder import Recorder
from state import StateTracker
from test_scaffold import FakeClock, FakeExchange

lvl = SimpleNamespace


def rows(path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


class RecordingExchange(FakeExchange):
    """FakeExchange plus the reads the runner loop needs. Disconnects after `loops` is_connected calls."""

    def __init__(self, loops: int = 2) -> None:
        super().__init__()
        self.loops = loops
        self.ticks: list = []
        self.fills: list = []

    def is_connected(self):
        self.loops -= 1
        return self.loops >= 0

    def get_tradable_instruments(self):
        return {"NVDA": SimpleNamespace(instrument_id="NVDA", tick_size=0.1)}

    def get_pnl(self):
        return 12.5

    def poll_new_trade_ticks(self, iid):
        ticks, self.ticks = self.ticks, []
        return ticks

    def poll_new_trades(self, iid):
        fills, self.fills = self.fills, []
        return fills


def test_runner_writes_all_streams(tmp_path, monkeypatch):
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    ex = RecordingExchange(loops=2)  # start check + one loop
    ts = datetime(2026, 11, 6, 10, 0, 0)
    ex.book = SimpleNamespace(timestamp=ts, bids=[lvl(price=100.0, volume=5)],
                              asks=[lvl(price=100.2, volume=3), lvl(price=100.3, volume=7)])
    ex.ticks = [SimpleNamespace(timestamp=ts, trade_id=7, price=100.1, volume=2,
                                aggressor_side=BID, buyer="team_x", seller="team_y")]
    ex.positions["NVDA"] = 4
    ex.fills = [SimpleNamespace(timestamp=ts, trade_id=8, order_id=3, side=BID, price=100.0, volume=4)]

    assert runner.run(ex, live=False, run_dir=tmp_path) == 1  # exits on fake disconnect

    book = rows(tmp_path / "books.csv")
    assert len(book) == 1
    assert book[0]["bid_px_1"] == "100.0" and book[0]["ask_vol_2"] == "7" and book[0]["bid_px_2"] == ""
    tick = rows(tmp_path / "trade_ticks.csv")[0]
    assert (tick["buyer"], tick["seller"], tick["aggressor_side"]) == ("team_x", "team_y", BID)
    fill = rows(tmp_path / "our_fills.csv")[0]
    assert fill["side"] == BID and fill["loop_position"] == "4"
    metric = rows(tmp_path / "metrics.csv")[0]
    assert metric["pnl"] == "12.5" and metric["pos_NVDA"] == "4" and float(metric["mid_NVDA"]) == 100.1


def test_books_written_only_when_timestamp_changes(tmp_path):
    ex = RecordingExchange()
    md, rec = MarketDataTracker(), Recorder(tmp_path, ["NVDA"])
    ex.book = SimpleNamespace(timestamp=1, bids=[lvl(price=100.0, volume=5)], asks=[])
    for now in (0.0, 1.0):  # same timestamp twice
        rec.record_market(md.update(ex, ["NVDA"], now))
    ex.book = SimpleNamespace(timestamp=2, bids=[], asks=[])
    rec.record_market(md.update(ex, ["NVDA"], 2.0))
    rec.close()
    assert [r["exchange_ts"] for r in rows(tmp_path / "books.csv")] == ["1", "2"]


def test_order_outcomes_recorded(tmp_path):
    ex = FakeExchange()
    rec = Recorder(tmp_path, ["NVDA"])
    insert = ex.insert_order

    def insert_or_fail(iid, **kw):
        if kw["side"] == ASK:
            return SimpleNamespace(success=False, error_reason="price limit")
        if kw["price"] < 100:
            raise TimeoutError
        return insert(iid, **kw)

    ex.insert_order = insert_or_fail
    om = OrderManager(ex, RateLimiter(100, clock=FakeClock()), rec)
    om.execute("NVDA", [OrderIntent(BID, 100.0, 5), OrderIntent(ASK, 101.0, 5), OrderIntent(BID, 99.0, 1)],
               {}, 0.1)
    rec.close()
    got = [(r["action"], r["side"], r["success"], r["order_id"]) for r in rows(tmp_path / "our_orders.csv")]
    assert got == [("insert", BID, "true", "1"), ("insert", ASK, "false", ""), ("insert", BID, "unknown", "")]


def test_write_failure_disables_recording_without_raising(tmp_path):
    rec = Recorder(tmp_path, ["NVDA"])
    rec.close()  # writes to closed files raise ValueError
    st = StateTracker().update(RecordingExchange(), ["NVDA"])
    st["NVDA"].new_fills.append(SimpleNamespace(timestamp=0, trade_id=1, order_id=1, side=BID,
                                                price=1.0, volume=1))
    rec.record_fills(st)
    assert not rec.enabled
    rec.record_order("NVDA", "delete", {"order_id": 1}, response=SimpleNamespace(success=True, error_reason=None))

