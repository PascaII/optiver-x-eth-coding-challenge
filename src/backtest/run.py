"""Backtest a strategy by running the real bot loop against replayed market data.

    uv run python src/backtest/run.py DATA_DIR [--strategy FILE.py] [--out DIR] [-v]

DATA_DIR holds books.csv (+ optional trade_ticks.csv) in our recorder's format:
a runs/<ts>_dry/ folder, or the output of fetch_history.py. The strategy file
must define `decide(snapshots, states, now)` like src/strategy.py (the default).

The bot runs exactly as `src/main.py --live` would (runner.run with live=True),
but against SimExchange, so no optibook connection is made and no real order is sent.
Results go to runs/backtests/<UTC time>_<strategy>/; report.html opens in a browser.
"""

import argparse
import csv
import importlib.util
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))  # bot modules (runner, strategy, ...) live in src/

import runner  # noqa: E402
from backtest.data import load_events  # noqa: E402
from backtest.report import write_report  # noqa: E402
from backtest.sim_exchange import SimExchange  # noqa: E402

BACKTESTS_DIR = SRC.parent / "runs" / "backtests"
LOG_FORMAT = "%(levelname)s %(name)s: %(message)s"

log = logging.getLogger("backtest")


def load_strategy(path: Path):
    """Import a strategy file as the module the runner calls."""
    spec = importlib.util.spec_from_file_location(f"strategy_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "decide", None)):
        raise SystemExit(f"{path} has no decide(snapshots, states, now) function")
    return module


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="milliseconds")


def _write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "sim_ts": _iso(row["sim_ts"])})


def max_drawdown(values: list[float]) -> float:
    """Largest peak-to-trough fall, as a positive number."""
    peak, worst = float("-inf"), 0.0
    for v in values:
        peak = max(peak, v)
        worst = max(worst, peak - v)
    return worst


def summarize(sim: SimExchange, strategy_path: Path, data_dir: Path, exit_code: int, wall_s: float) -> dict:
    pnl_series = [r["pnl_mid"] for r in sim.equity_rows if r["pnl_mid"] is not None]
    final = sim.equity_rows[-1]
    rejections: dict[str, int] = {}
    for row in sim.order_rows:
        if not row["success"]:
            rejections[row["error"]] = rejections.get(row["error"], 0) + 1
    per_instrument = {}
    for iid in sim.instrument_ids:
        fills = [f for f in sim.fill_rows if f["instrument_id"] == iid]
        positions = [r[f"pos_{iid}"] for r in sim.equity_rows] + [f["position"] for f in fills]
        per_instrument[iid] = {
            "fills": len(fills),
            "bought": sum(f["volume"] for f in fills if f["side"] == "bid"),
            "sold": sum(f["volume"] for f in fills if f["side"] == "ask"),
            "max_abs_position": max((abs(p) for p in positions), default=0),
            "end_position": sim.positions[iid],
        }
    return {
        "strategy": str(strategy_path),
        "data_dir": str(data_dir),
        "data_start": _iso(sim.start_ts),
        "data_end": _iso(sim.end_ts),
        "sim_duration_s": round(sim.end_ts - sim.start_ts, 3),
        "wall_time_s": round(wall_s, 2),
        "fill_model": "trade-through (resting), book walk (aggressive), zero latency",
        "exit_code": exit_code,
        "pnl_mid": None if final["pnl_mid"] is None else round(final["pnl_mid"], 4),
        "pnl_last": None if final["pnl_last"] is None else round(final["pnl_last"], 4),
        "cash": round(sim.cash, 4),
        "max_drawdown_mid": round(max_drawdown(pnl_series), 4) if pnl_series else None,
        "fills": len(sim.fill_rows),
        "traded_volume": sum(f["volume"] for f in sim.fill_rows),
        "requests": len(sim.order_rows),
        "rejections": rejections,
        "rate_violations": sim.rate_violations,
        "end_gross_position": sum(abs(p) for p in sim.positions.values()),
        "per_instrument": per_instrument,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest the bot on recorded market data")
    parser.add_argument("data_dir", type=Path, help="folder with books.csv and trade_ticks.csv")
    parser.add_argument("--strategy", type=Path, default=SRC / "strategy.py", help="strategy file (default: src/strategy.py)")
    parser.add_argument("--out", type=Path, help="output folder (default: runs/backtests/<time>_<strategy>)")
    parser.add_argument("-v", "--verbose", action="store_true", help="print the bot's INFO log to the console")
    args = parser.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = args.out or BACKTESTS_DIR / f"{stamp}_{args.strategy.stem}"
    out.mkdir(parents=True, exist_ok=False)
    console = logging.StreamHandler()
    console.setLevel(logging.INFO if args.verbose else logging.WARNING)
    file_handler = logging.FileHandler(out / "bot.log")
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, handlers=[console, file_handler])

    print(f"loading {args.data_dir} ...")
    events = load_events(args.data_dir)
    sim = SimExchange(events)
    print(f"{len(events)} events, {len(sim.instrument_ids)} instruments, "
          f"{_iso(sim.start_ts)} -> {_iso(sim.end_ts)}; strategy {args.strategy}")

    runner.strategy = load_strategy(args.strategy)  # the module runner.run calls decide() on
    wall_start = time.monotonic()
    exit_code = runner.run(sim, live=True, run_dir=None, clock=sim.clock, sleep=sim.sleep)
    wall_s = time.monotonic() - wall_start

    summary = summarize(sim, args.strategy, args.data_dir, exit_code, wall_s)
    _write_csv(out / "equity.csv", sim.equity_rows, list(sim.equity_rows[0]))
    _write_csv(out / "fills.csv", sim.fill_rows,
               ["sim_ts", "instrument_id", "order_id", "side", "price", "volume", "position", "reason"])
    _write_csv(out / "orders.csv", sim.order_rows,
               ["sim_ts", "instrument_id", "action", "side", "price", "volume", "order_type",
                "order_id", "success", "error"])
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    write_report([out], out / "report.html")

    print(f"pnl (mid) {summary['pnl_mid']}, pnl (last trade) {summary['pnl_last']}, "
          f"fills {summary['fills']}, rejections {sum(summary['rejections'].values())}, "
          f"rate violations {summary['rate_violations']}, end gross position {summary['end_gross_position']}")
    print(f"results: {out}\nreport:  {out / 'report.html'}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
