"""The main polling loop: read data -> strategy -> risk clip -> execute -> log -> sleep.

The exchange client keeps its own connection open and caches books, positions
and orders. One single-threaded loop is enough. If the connection drops, we
exit; the workspace guide recommends crash-and-restart over reconnect logic.
"""

import logging
import time
from pathlib import Path

import config, strategy
from market_data import MarketDataTracker, MarketSnapshot
from orders import OrderManager
from rate_limiter import RateLimiter
from recorder import NullRecorder, Recorder
from risk import clip_intents
from state import InstrumentState, StateTracker

log = logging.getLogger(__name__)


def _fmt_level(levels: list) -> str:
    return f"{levels[0].price:.2f}x{levels[0].volume}" if levels else "-"


def _read_pnl(exchange) -> tuple[float | None, str]:
    """(pnl, text for the status line); pnl is None when the client cannot value it."""
    try:
        pnl = exchange.get_pnl()
        return pnl, f"{pnl:.2f}"
    except Exception as exc:  # noqa: BLE001 - PnL is informational; e.g. no trade tick to value at
        return None, f"n/a ({exc!r})"


def _print_status(snapshots: dict[str, MarketSnapshot], states: dict[str, InstrumentState],
                  wanted: dict[str, list], pnl_text: str, limiter: RateLimiter) -> None:
    lines = []
    for iid, snap in snapshots.items():
        st = states[iid]
        want = " ".join(f"{i.side[0]}{i.volume}@{i.price:.2f}{'!' if i.order_type == 'ioc' else ''}"
                        for i in wanted.get(iid, [])) or "-"
        lines.append(
            f"  {iid:<5} pos={st.position:+4d} bid={_fmt_level(snap.bids):>12} ask={_fmt_level(snap.asks):>12}"
            f" age={snap.book_age_s:5.1f}s ours=b{st.resting_bid_volume}/a{st.resting_ask_volume}"
            f" fills={st.fills.count} want=[{want}]"
        )
    log.info("status pnl=%s requests_last_1s=%d\n%s", pnl_text, limiter.used(), "\n".join(lines))


def run(exchange, live: bool, run_dir: Path | None = None,
        clock=time.monotonic, sleep=time.sleep) -> int:
    """Run until Ctrl-C or disconnect. Returns a process exit code.

    live=False (dry run): computes and logs desired orders but never sends any request.
    run_dir: folder for recorded CSVs (see recorder.py); None = no recording.
    clock/sleep: replaced by simulated time in the backtester (src/backtest/).
    """
    if not exchange.is_connected():
        log.error("not connected at start")
        return 1
    instruments = exchange.get_tradable_instruments()
    instrument_ids = sorted(instruments)
    log.info("mode=%s instruments=%s", "LIVE" if live else "DRY-RUN",
             {iid: instruments[iid].tick_size for iid in instrument_ids})

    limiter = RateLimiter(config.REQUEST_BUDGET_PER_S, clock=clock, sleep=sleep)
    market = MarketDataTracker()
    tracker = StateTracker()
    recorder = NullRecorder()
    if run_dir is not None:
        try:
            recorder = Recorder(run_dir, instrument_ids)
        except Exception:  # noqa: BLE001 - trade without recording rather than not at all
            log.exception("could not open recording files in %s; running without recording", run_dir)
    orders = OrderManager(exchange, limiter, recorder)
    last_status = 0.0
    exit_code = 0
    try:
        while True:
            loop_start = clock()
            if not exchange.is_connected():
                # A second connection for our team kicks us silently.
                log.error("disconnected (another connection for this team?); exiting")
                exit_code = 1
                break

            snapshots = market.update(exchange, instrument_ids, loop_start)
            states = tracker.update(exchange, instrument_ids)
            recorder.record_market(snapshots)
            recorder.record_fills(states)
            for iid, st in states.items():
                for trade in st.new_fills:
                    log.info("FILL %s %s %d @ %.2f -> position %+d", iid, trade.side, trade.volume,
                             trade.price, st.position)

            decisions = strategy.decide(snapshots, states, loop_start)
            wanted = {}
            for iid in instrument_ids:
                snap, st = snapshots[iid], states[iid]
                intents = decisions.get(iid, [])
                # Conservative default: no orders on a stale or one-sided book.
                if snap.is_stale(config.BOOK_STALE_AFTER_S) or not snap.is_two_sided:
                    intents = []
                intents = clip_intents(intents, st.position, instruments[iid])
                wanted[iid] = intents

            if live:
                orders.execute_all(wanted, states, instruments)

            if loop_start - last_status >= config.STATUS_INTERVAL_S:
                pnl, pnl_text = _read_pnl(exchange)
                _print_status(snapshots, states, wanted, pnl_text, limiter)
                recorder.record_status(snapshots, states, pnl, limiter.used())
                last_status = loop_start
                if pnl is not None and pnl < config.MAX_LOSS:
                    log.error("PnL %.2f below MAX_LOSS %.2f; stopping (quotes cancelled, positions stay)",
                              pnl, config.MAX_LOSS)
                    exit_code = 1
                    break

            sleep(max(0.0, config.LOOP_INTERVAL_S - (clock() - loop_start)))
    except KeyboardInterrupt:
        log.info("stopping on Ctrl-C")
    finally:
        # Cancelling quotes does not flatten positions; positions stay after exit.
        if live and exchange.is_connected():
            orders.cancel_all(instrument_ids)
        recorder.close()
    return exit_code
