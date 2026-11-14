"""Pre-trade risk checks: tick alignment, price/volume ranges, worst-case position.

Worst case means every resting and proposed order on a side fills. Opposite-side
orders do not offset each other: both sides of a quote can fill.
"""

import math

import config
from intent import ASK, BID, LIMIT, OrderIntent

_EPS = 1e-9  # absorbs float noise such as 101.3 / 0.1 = 1012.9999999


def round_to_tick(price: float, tick: float, side: str) -> float:
    """Align a price to the tick grid, never more aggressively than asked.

    Bids round down and asks round up, so a clipped order never pays more or
    sells for less than the strategy intended. Idea adapted from mm-toolbox
    `rounding/` (MIT, references/beatzxbt/mm-toolbox).
    """
    ticks = price / tick
    n = math.floor(ticks + _EPS) if side == BID else math.ceil(ticks - _EPS)
    return round(n * tick, 10)


def max_buy_volume(position: int, resting_bid_volume: int, limit: int = config.POSITION_LIMIT) -> int:
    """Lots we may still bid for so that position + all bids filling stays ≤ +limit."""
    return max(0, limit - position - resting_bid_volume)


def max_sell_volume(position: int, resting_ask_volume: int, limit: int = config.POSITION_LIMIT) -> int:
    """Lots we may still offer so that position − all asks filling stays ≥ −limit."""
    return max(0, limit + position - resting_ask_volume)


def _price_bounds(instrument) -> tuple[float, float]:
    """Fixed order range, narrowed by the instrument's price_limit when present.

    The workspace guide says `instrument.price_limit` carries inclusive `min` and
    `max` (None = unlimited); it is missing from the API docs, so read it defensively.
    """
    lo, hi = config.MIN_ORDER_PRICE, config.MAX_ORDER_PRICE
    limit = getattr(instrument, "price_limit", None)
    if limit is not None:
        get = limit.get if isinstance(limit, dict) else lambda k: getattr(limit, k, None)
        if get("min") is not None:
            lo = max(lo, get("min"))
        if get("max") is not None:
            hi = min(hi, get("max"))
    return lo, hi


def clip_intents(intents: list[OrderIntent], position: int, instrument) -> list[OrderIntent]:
    """Return the intents that fit the limits, in the strategy's priority order.

    Treats the list as the full desired order set for this instrument. Total bid
    volume is capped at limit − position, total ask volume at limit + position,
    and total resting (LIMIT) volume at MAX_OUTSTANDING_VOLUME. Earlier intents get
    volume first. Invalid prices are dropped. The price_change_limit (distance from
    the last trade) is not checked here; the exchange rejects such orders and we
    log the rejection.

    OrderManager re-checks each insert against the orders actually resting, which
    covers deletes that failed or have not happened yet.
    """
    lo, hi = _price_bounds(instrument)
    buy_left = max_buy_volume(position, 0)
    sell_left = max_sell_volume(position, 0)
    resting_left = config.MAX_OUTSTANDING_VOLUME
    clipped = []
    for intent in intents:
        price = round_to_tick(intent.price, instrument.tick_size, intent.side)
        if not lo <= price <= hi:
            continue
        side_left = buy_left if intent.side == BID else sell_left
        volume = min(int(intent.volume), side_left, config.MAX_ORDER_VOLUME)
        if intent.order_type == LIMIT:
            volume = min(volume, resting_left)
        if volume < config.MIN_ORDER_VOLUME:
            continue
        if intent.side == BID:
            buy_left -= volume
        else:
            sell_left -= volume
        if intent.order_type == LIMIT:
            resting_left -= volume
        clipped.append(OrderIntent(intent.side, price, volume, intent.order_type))
    return clipped
