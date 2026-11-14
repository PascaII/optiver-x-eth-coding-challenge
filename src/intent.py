"""The order intent: what the strategy asks for, before risk clipping."""

from dataclasses import dataclass

BID = "bid"  # buy
ASK = "ask"  # sell
LIMIT = "limit"  # rests in the book until cancelled
IOC = "ioc"  # fills immediately against the book, remainder cancelled


@dataclass(slots=True, frozen=True)
class OrderIntent:
    side: str  # BID or ASK
    price: float  # limit price; aligned to the tick grid by risk.clip_intents
    volume: int  # lots, > 0
    order_type: str = LIMIT  # LIMIT or IOC
