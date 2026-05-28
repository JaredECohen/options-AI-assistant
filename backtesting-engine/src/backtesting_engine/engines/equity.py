"""Equity execution helpers: build quotes and mark positions from OHLCV bars."""

from __future__ import annotations

from backtesting_engine.core.types import Bar, MarketQuote


def quote_from_bar(bar: Bar, tick: float = 0.01) -> MarketQuote:
    """Build a :class:`MarketQuote` for an equity order from its EOD bar.

    EOD OHLCV has no bid/ask, so we fill against the close; the cost model applies
    the adverse slippage tick.
    """
    return MarketQuote(close=bar.close, last=bar.close, tick=tick)
