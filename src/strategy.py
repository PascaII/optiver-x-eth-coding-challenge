"""Public strategy plug-in point; competition trading logic is withheld.

`decide` receives market snapshots and instrument states, rebuilt each loop,
plus the loop clock in seconds (monotonic live, simulated in backtests).
Return {instrument_id: [OrderIntent, ...]} describing all desired resting limit
orders and any IOCs to send now. Intent order determines risk-clipping priority.
An omitted instrument or an empty list requests cancellation of resting orders;
it does not close positions. IOCs returned on successive loops can be sent again.
The risk layer rounds bids down and asks up to the tick and caps order volumes.

This placeholder returns no order intents. It contains no competition signals,
parameters, or models. Backtests can load a separate module with --strategy.
"""

from intent import OrderIntent
from market_data import MarketSnapshot
from state import InstrumentState


def decide(snapshots: dict[str, MarketSnapshot], states: dict[str, InstrumentState],
           now: float) -> dict[str, list[OrderIntent]]:
    """Request no orders; existing quotes are cancelled by a live runner."""
    return {}
