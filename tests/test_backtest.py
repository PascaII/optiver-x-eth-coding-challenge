"""Offline checks for the backtester's simulated exchange and replay loop."""

import json
from types import SimpleNamespace

import pytest

import runner
from backtest.data import BookEvent, Level, TickEvent, load_events, parse_ts
from backtest.report import write_report
from backtest.sim_exchange import SimExchange
from intent import ASK, BID, IOC, OrderIntent

T0 = 1_800_000_000.0  # arbitrary epoch seconds


def book(t, bids, asks, iid="NVDA"):
    return BookEvent(T0 + t, iid, [Level(p, v) for p, v in bids], [Level(p, v) for p, v in asks])


def tick(t, price, volume, iid="NVDA"):
    return TickEvent(T0 + t, iid, price, volume, "bid", "a", "b", "1")


def sim_with(*events):
    return SimExchange(sorted(events, key=lambda e: e.ts))


# --- data loading ---------------------------------------------------------------

def test_parse_ts_accepts_recorder_and_loader_formats():
    assert parse_ts("2026-11-06 10:00:00.500000+00:00") == parse_ts("2026-11-06T10:00:00.5")
    assert parse_ts(1_800_000_000_500_000_000) == pytest.approx(1_800_000_000.5)
    assert parse_ts("1800000000.25") == 1_800_000_000.25
    with pytest.raises(ValueError):
        parse_ts("yesterday")


def test_load_events_reads_recorder_csvs_in_time_order(tmp_path):
    (tmp_path / "books.csv").write_text(
        "local_ts,exchange_ts,instrument_id,bid_px_1,bid_px_2,bid_vol_1,bid_vol_2,ask_px_1,ask_px_2,ask_vol_1,ask_vol_2\n"
        "x,2026-11-06 10:00:01+00:00,NVDA,100.0,99.9,5,7,100.2,,3,\n"
        "x,2026-11-06 10:00:00+00:00,NVDA,,,,,100.3,,1,\n")
    (tmp_path / "trade_ticks.csv").write_text(
        "local_ts,exchange_ts,instrument_id,trade_id,price,volume,aggressor_side,buyer,seller\n"
        "x,2026-11-06 10:00:01+00:00,NVDA,9,100.2,2,bid,x,y\n")
    events = load_events(tmp_path)
    assert [type(e).__name__ for e in events] == ["BookEvent", "BookEvent", "TickEvent"]
    assert events[0].bids == [] and events[0].asks == [Level(100.3, 1)]
    assert events[1].bids == [Level(100.0, 5), Level(99.9, 7)] and events[1].asks == [Level(100.2, 3)]


# --- aggressive orders ---------------------------------------------------------

def test_ioc_walks_book_up_to_limit_and_does_not_reuse_liquidity():
    sim = sim_with(book(0, [(99.9, 10)], [(100.0, 3), (100.1, 4), (100.3, 50)]))
    r = sim.insert_order("NVDA", price=100.1, volume=10, side=BID, order_type=IOC)
    assert r.success
    assert [(f["price"], f["volume"]) for f in sim.fill_rows] == [(100.0, 3), (100.1, 4)]
    assert sim.positions["NVDA"] == 7 and sim.cash == pytest.approx(-(300.0 + 400.4))
    assert sim.get_outstanding_orders("NVDA") == {}  # IOC remainder cancelled
    sim.insert_order("NVDA", price=100.1, volume=10, side=BID, order_type=IOC)
    assert sim.positions["NVDA"] == 7  # same snapshot: liquidity already taken


def test_crossing_limit_order_fills_then_rests_remainder():
    sim = sim_with(book(0, [(99.9, 10)], [(100.0, 3)]))
    r = sim.insert_order("NVDA", price=100.0, volume=5, side=BID)
    assert sim.positions["NVDA"] == 3
    assert sim.get_outstanding_orders("NVDA")[r.order_id].volume == 2


# --- resting orders ------------------------------------------------------------

def test_resting_bid_needs_trade_through():
    sim = sim_with(book(0, [(99.9, 10)], [(100.1, 10)]), tick(0.1, 99.9, 4), tick(0.3, 99.8, 4))
    sim.insert_order("NVDA", price=99.9, volume=6, side=BID)
    sim.sleep(0.2)  # trade AT our price: we are last in the queue, no fill
    assert sim.positions["NVDA"] == 0
    sim.sleep(0.2)  # trade below our price: fills up to the trade's volume, at our price
    assert sim.positions["NVDA"] == 4
    assert sim.fill_rows[-1]["price"] == 99.9 and sim.fill_rows[-1]["reason"] == "trade-through"
    assert [t.volume for t in sim.poll_new_trades("NVDA")] == [4]


def test_resting_ask_fills_when_book_crosses_it():
    sim = sim_with(book(0, [(99.9, 10)], [(100.5, 10)]), book(0.1, [(100.4, 2), (100.2, 5)], [(100.6, 1)]))
    sim.insert_order("NVDA", price=100.3, volume=10, side=ASK)
    sim.sleep(0.2)
    assert sim.positions["NVDA"] == -2  # only the 100.4 level crosses 100.3
    assert sim.fill_rows[-1]["price"] == 100.3


# --- limits and requests -------------------------------------------------------

def test_rejections_mirror_exchange_limits():
    sim = sim_with(book(0, [(99.0, 10)], [(101.0, 10)]))
    sim.positions["NVDA"] = 75
    assert sim.insert_order("NVDA", price=100.0, volume=50, side=BID).error_reason == "position limit (worst case)"
    assert sim.insert_order("NVDA", price=100.05, volume=1, side=BID).error_reason == "price not on tick grid"
    # With ±100 the position limit binds first; widen it to reach the 200-lot resting cap.
    sim = SimExchange([book(0, [(99.0, 10)], [(101.0, 10)])], position_limit=1000)
    assert sim.insert_order("NVDA", price=100.0, volume=150, side=BID).success
    assert sim.insert_order("NVDA", price=100.5, volume=50, side=ASK).success
    assert sim.insert_order("NVDA", price=100.6, volume=1, side=ASK).error_reason == "outstanding volume limit"


def test_rate_violations_counted_per_one_second_window():
    sim = sim_with(book(0, [(99.0, 10)], [(101.0, 10)]), book(5, [(99.0, 10)], [(101.0, 10)]))
    for _ in range(26):
        sim.delete_order("NVDA", order_id=999)
    assert sim.rate_violations == 1
    sim.sleep(1.0)
    sim.delete_order("NVDA", order_id=999)
    assert sim.rate_violations == 1


# --- end to end through the real bot loop ---------------------------------------

def _buy_once_strategy():
    """Buys 5 by IOC once, and rests a far ask that is never hit."""
    def decide(snapshots, states, now):
        st, snap = states["NVDA"], snapshots["NVDA"]
        intents = [OrderIntent(ASK, 105.0, 1)]
        if st.position == 0 and st.fills.count == 0 and snap.best_ask is not None:
            intents.insert(0, OrderIntent(BID, snap.best_ask, 5, IOC))
        return {"NVDA": intents}
    return SimpleNamespace(decide=decide)


def test_runner_replays_to_end_and_cancels_on_shutdown(monkeypatch, tmp_path):
    events = [book(t, [(99.9, 10)], [(100.1, 10)]) for t in range(0, 4)]
    events.append(book(4, [(100.9, 10)], [(101.1, 10)]))
    sim = SimExchange(events)
    monkeypatch.setattr(runner, "strategy", _buy_once_strategy())
    assert runner.run(sim, live=True, run_dir=None, clock=sim.clock, sleep=sim.sleep) == 0
    assert sim.finished
    assert sim.positions["NVDA"] == 5
    assert sim.get_outstanding_orders("NVDA") == {}  # cancel_all ran on the Ctrl-C path
    assert sim.order_rows[-1]["action"] == "cancel_all"
    # Bought 5 @ 100.1; final mid 101.0 -> P&L = -500.5 + 5 * 101.0
    assert sim.equity_rows[-1]["pnl_mid"] == pytest.approx(4.5)
    assert sim.rate_violations == 0

    # The report generator accepts the run's outputs.
    import backtest.run as bt
    summary = bt.summarize(sim, tmp_path / "s.py", tmp_path, 0, 0.0)
    bt._write_csv(tmp_path / "equity.csv", sim.equity_rows, list(sim.equity_rows[0]))
    bt._write_csv(tmp_path / "fills.csv", sim.fill_rows,
                  ["sim_ts", "instrument_id", "order_id", "side", "price", "volume", "position", "reason"])
    (tmp_path / "summary.json").write_text(json.dumps(summary))
    write_report([tmp_path], tmp_path / "report.html")
    assert summary["per_instrument"]["NVDA"]["bought"] == 5
    assert "const RUNS = [" in (tmp_path / "report.html").read_text()


def test_max_drawdown():
    import backtest.run as bt
    assert bt.max_drawdown([0, 5, 2, 7, 1, 3]) == 6
    assert bt.max_drawdown([1, 2, 3]) == 0
