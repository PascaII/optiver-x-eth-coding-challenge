"""Persist market data, our orders/fills and run metrics to CSV files in a run folder.

One folder per process (see main.py). Files and columns are listed in README.md
under "Run data". Public trade ticks include buyer/seller names, so they are the
raw material for opponent analysis.

Recording must never stop trading: any exception while writing is logged once
and disables the recorder for the rest of the process. Rows are buffered by the
csv module and flushed once per status interval, so a hard crash can lose up to
about one second of data.
"""

import csv
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

BOOK_DEPTH = 5  # book levels per side stored in books.csv


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _levels(levels: list) -> tuple[list, list]:
    """Prices and volumes of the top BOOK_DEPTH levels, padded with blanks."""
    top = levels[:BOOK_DEPTH]
    pad = [""] * (BOOK_DEPTH - len(top))
    return [lvl.price for lvl in top] + pad, [lvl.volume for lvl in top] + pad


class NullRecorder:
    """Records nothing. Used with --no-record and in tests."""

    def record_market(self, snapshots: dict) -> None: pass
    def record_fills(self, states: dict) -> None: pass
    def record_order(self, iid: str, action: str, request: dict, response=None, error=None) -> None: pass
    def record_status(self, snapshots: dict, states: dict, pnl, requests_last_1s: int) -> None: pass
    def close(self) -> None: pass


class Recorder(NullRecorder):
    def __init__(self, run_dir: Path, instrument_ids: list[str]) -> None:
        self.run_dir = Path(run_dir)
        self.instrument_ids = list(instrument_ids)
        self.enabled = True
        self._files = []
        book_cols = [f"{side}_{kind}_{i}" for side in ("bid", "ask") for kind in ("px", "vol")
                     for i in range(1, BOOK_DEPTH + 1)]
        metric_cols = [f"{name}_{iid}" for iid in self.instrument_ids
                       for name in ("pos", "mid", "bid_rest", "ask_rest")]
        self._books = self._open("books.csv", ["local_ts", "exchange_ts", "instrument_id"] + book_cols)
        self._ticks = self._open("trade_ticks.csv", ["local_ts", "exchange_ts", "instrument_id", "trade_id",
                                                     "price", "volume", "aggressor_side", "buyer", "seller"])
        # loop_position: position read in the same loop; with several fills per
        # loop it is the position after all of them, not after each one.
        self._fills = self._open("our_fills.csv", ["local_ts", "exchange_ts", "instrument_id", "trade_id",
                                                   "order_id", "side", "price", "volume", "loop_position"])
        self._orders = self._open("our_orders.csv", ["local_ts", "instrument_id", "action", "side", "price",
                                                     "volume", "order_type", "order_id", "success", "error"])
        self._metrics = self._open("metrics.csv", ["local_ts", "pnl", "requests_last_1s"] + metric_cols)

    def _open(self, name: str, header: list[str]):
        f = open(self.run_dir / name, "w", newline="")
        self._files.append(f)
        writer = csv.writer(f)
        writer.writerow(header)
        return writer

    def _fail(self, what: str) -> None:
        log.exception("recorder: %s failed; recording disabled for the rest of this run", what)
        self.enabled = False

    def record_market(self, snapshots: dict) -> None:
        """Books whose timestamp changed this loop, and all new public trade ticks."""
        if not self.enabled:
            return
        try:
            ts = _now()
            for iid, snap in snapshots.items():
                if snap.book_changed:
                    bid_px, bid_vol = _levels(snap.bids)
                    ask_px, ask_vol = _levels(snap.asks)
                    self._books.writerow([ts, snap.exchange_ts, iid] + bid_px + bid_vol + ask_px + ask_vol)
                for t in snap.trade_ticks:
                    self._ticks.writerow([ts, t.timestamp, iid, t.trade_id, t.price, t.volume,
                                          t.aggressor_side, t.buyer, t.seller])
        except Exception:  # noqa: BLE001 - recording must not stop trading
            self._fail("record_market")

    def record_fills(self, states: dict) -> None:
        if not self.enabled:
            return
        try:
            ts = _now()
            for iid, st in states.items():
                for t in st.new_fills:
                    self._fills.writerow([ts, t.timestamp, iid, t.trade_id, t.order_id, t.side,
                                          t.price, t.volume, st.position])
        except Exception:  # noqa: BLE001
            self._fail("record_fills")

    def record_order(self, iid: str, action: str, request: dict, response=None, error=None) -> None:
        """One request we sent.

        success column: true/false from the exchange response; "unknown" when the
        call raised; "sent" when the call returns no response (delete_orders).
        """
        if not self.enabled:
            return
        try:
            ts = _now()
            if error is not None:
                success, reason = "unknown", repr(error)
            elif response is None:
                success, reason = "sent", ""
            else:
                success, reason = str(bool(response.success)).lower(), response.error_reason or ""
            order_id = request.get("order_id", getattr(response, "order_id", ""))
            self._orders.writerow([ts, iid, action, request.get("side", ""), request.get("price", ""),
                                   request.get("volume", ""), request.get("order_type", ""),
                                   "" if order_id is None else order_id, success, reason])
        except Exception:  # noqa: BLE001
            self._fail("record_order")

    def record_status(self, snapshots: dict, states: dict, pnl, requests_last_1s: int) -> None:
        """Append a metrics.csv row and flush all files."""
        if not self.enabled:
            return
        try:
            ts = _now()
            row = [ts, "" if pnl is None else round(pnl, 4), requests_last_1s]
            for iid in self.instrument_ids:
                st, mid = states[iid], snapshots[iid].mid
                row += [st.position, "" if mid is None else mid, st.resting_bid_volume, st.resting_ask_volume]
            self._metrics.writerow(row)
            for f in self._files:
                f.flush()
        except Exception:  # noqa: BLE001
            self._fail("record_status")

    def close(self) -> None:
        for f in self._files:
            try:
                f.close()
            except Exception:  # noqa: BLE001 - closing others still matters
                log.exception("recorder: closing %s failed", f.name)
