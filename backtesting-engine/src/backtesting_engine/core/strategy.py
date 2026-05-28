"""The :class:`Strategy` base class.

Subclass it and implement :meth:`on_bar` to emit orders. The lifecycle hooks
(:meth:`on_start`, :meth:`on_fill`, :meth:`on_end`) are optional and default to
no-ops.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from backtesting_engine.core.portfolio import Portfolio
from backtesting_engine.core.types import Fill, MarketSlice, Order


class Strategy(ABC):
    """Abstract trading strategy.

    The engine calls, in order:

    1. :meth:`on_start` once before the first bar.
    2. :meth:`on_bar` for every trading day; return the orders to submit.
    3. :meth:`on_fill` whenever one of those orders is executed.
    4. :meth:`on_end` once after the final bar.
    """

    def on_start(self, portfolio: Portfolio) -> None:
        """Called once before the backtest loop begins."""

    @abstractmethod
    def on_bar(self, bar: MarketSlice, portfolio: Portfolio) -> list[Order]:
        """Return the orders to submit given the current market slice.

        ``bar`` exposes equity OHLCV and option chains for this timestamp;
        ``portfolio`` is the live portfolio (read it, don't mutate it).
        """
        raise NotImplementedError

    def on_fill(self, fill: Fill, portfolio: Portfolio) -> None:
        """Called after each fill is applied to the portfolio."""

    def on_end(self, portfolio: Portfolio) -> None:
        """Called once after the final bar has been processed."""
