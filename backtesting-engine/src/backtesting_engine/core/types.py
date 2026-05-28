"""Frozen, typed value objects shared across the engine.

These are deliberately small and immutable so they are safe to pass between the
strategy, the execution loop, the cost model, and the portfolio without anyone
mutating shared state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

import pandas as pd


class Side(StrEnum):
    """Direction of an order or fill."""

    BUY = "buy"
    SELL = "sell"

    @property
    def sign(self) -> int:
        """+1 for buys, -1 for sells (signed quantity convention)."""
        return 1 if self is Side.BUY else -1


class OrderType(StrEnum):
    """Supported order types for the EOD v0 engine."""

    MARKET = "market"
    LIMIT = "limit"


class OptionRight(StrEnum):
    """Call or put."""

    CALL = "call"
    PUT = "put"


class AssetType(StrEnum):
    """Tradable instrument family."""

    EQUITY = "equity"
    OPTION = "option"


EQUITY_MULTIPLIER = 1
OPTION_MULTIPLIER = 100


@dataclass(frozen=True)
class Instrument:
    """Identifies a single tradable instrument (an equity share or one option leg).

    For equities only ``symbol`` and ``asset_type`` are set. For options the
    ``expiry``, ``strike`` and ``right`` fields are populated and ``symbol`` is the
    underlying ticker.
    """

    symbol: str
    asset_type: AssetType = AssetType.EQUITY
    expiry: date | None = None
    strike: float | None = None
    right: OptionRight | None = None

    @property
    def multiplier(self) -> int:
        """Contract multiplier (100 for options, 1 for equities)."""
        return OPTION_MULTIPLIER if self.asset_type is AssetType.OPTION else EQUITY_MULTIPLIER

    @property
    def is_option(self) -> bool:
        return self.asset_type is AssetType.OPTION

    @staticmethod
    def equity(symbol: str) -> Instrument:
        """Convenience constructor for an equity instrument."""
        return Instrument(symbol=symbol, asset_type=AssetType.EQUITY)

    @staticmethod
    def option(symbol: str, expiry: date, strike: float, right: OptionRight) -> Instrument:
        """Convenience constructor for an option instrument."""
        return Instrument(
            symbol=symbol,
            asset_type=AssetType.OPTION,
            expiry=expiry,
            strike=strike,
            right=right,
        )

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        if self.is_option:
            letter = (self.right or OptionRight.CALL).value[0].upper()
            return f"{self.symbol} {self.expiry} {self.strike}{letter}"
        return self.symbol


@dataclass(frozen=True)
class Bar:
    """A single end-of-day OHLCV bar for one equity symbol."""

    timestamp: pd.Timestamp
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Order:
    """An instruction emitted by a strategy to trade one instrument.

    ``quantity`` is always a positive number of units (shares or contracts); the
    ``side`` carries direction. ``limit_price`` is required for limit orders and
    ignored for market orders.
    """

    instrument: Instrument
    quantity: float
    side: Side
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    tag: str = ""

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("Order.quantity must be positive; use `side` for direction")
        if self.order_type is OrderType.LIMIT and self.limit_price is None:
            raise ValueError("limit orders require a limit_price")


@dataclass(frozen=True)
class Fill:
    """The realized execution of an order."""

    instrument: Instrument
    quantity: float
    side: Side
    price: float
    commission: float
    timestamp: pd.Timestamp
    tag: str = ""

    @property
    def signed_quantity(self) -> float:
        return self.side.sign * self.quantity

    @property
    def cash_flow(self) -> float:
        """Change in cash from this fill (negative for buys), including commission."""
        notional = self.price * self.quantity * self.instrument.multiplier
        return -self.side.sign * notional - self.commission


@dataclass(frozen=True)
class MarketQuote:
    """A minimal quote handed to the cost model to price a single order.

    ``close`` is used for equities (which have no bid/ask in EOD OHLCV data);
    ``bid``/``ask`` are used for options. ``tick`` is the instrument's tick size,
    used for the conservative one-tick slippage.
    """

    close: float | None = None
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    tick: float = 0.01

    def mid(self) -> float | None:
        if self.bid is not None and self.ask is not None:
            return 0.5 * (self.bid + self.ask)
        return self.last if self.last is not None else self.close


@dataclass
class MarketSlice:
    """Everything a strategy can observe at a single timestamp.

    Passed to :meth:`Strategy.on_bar` as the ``bar`` argument. Exposes equity bars
    by symbol and option chains (a normalized DataFrame) by underlying.
    """

    timestamp: pd.Timestamp
    bars: dict[str, Bar] = field(default_factory=dict)
    chains: dict[str, pd.DataFrame] = field(default_factory=dict)

    def price(self, symbol: str) -> float | None:
        """Close price for ``symbol`` on this bar, or ``None`` if unavailable."""
        bar = self.bars.get(symbol)
        return bar.close if bar is not None else None

    def bar(self, symbol: str) -> Bar | None:
        return self.bars.get(symbol)

    def chain(self, symbol: str) -> pd.DataFrame | None:
        """Option chain for ``symbol`` as of this timestamp."""
        return self.chains.get(symbol)
