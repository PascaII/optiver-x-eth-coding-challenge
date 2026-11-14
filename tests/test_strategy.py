"""Offline checks for the public placeholder and generic loss-stop behavior."""

from types import SimpleNamespace

import config
import runner
import strategy
from backtest.data import BookEvent, Level
from backtest.sim_exchange import SimExchange
from intent import ASK, OrderIntent


def test_public_strategy_never_requests_orders():
    assert strategy.decide({}, {}, 0.0) == {}
    assert strategy.decide({"EXAMPLE": object()}, {"EXAMPLE": object()}, 1.0) == {}


def test_runner_stops_below_max_loss_and_cancels(monkeypatch):
    events = [BookEvent(float(t), "EXAMPLE", [Level(99.9, 10)], [Level(100.1, 10)]) for t in range(10)]
    sim = SimExchange(events)
    monkeypatch.setattr(config, "MAX_LOSS", 1.0)  # flat book PnL 0.0 is already below
    monkeypatch.setattr(runner, "strategy", SimpleNamespace(
        decide=lambda snapshots, states, now: {"EXAMPLE": [OrderIntent(ASK, 105.0, 1)]}))
    assert runner.run(sim, live=True, clock=sim.clock, sleep=sim.sleep) == 1
    assert not sim.finished  # stopped by the loss check, not end of data
    assert sim.order_rows[-1]["action"] == "cancel_all"
