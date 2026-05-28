"""Structural protocols that let providers and cost models slot in without
touching the engine core. Any object implementing the right methods satisfies
these — no inheritance required.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

import pandas as pd

from backtesting_engine.core.types import MarketQuote, Order


@runtime_checkable
class DataSource(Protocol):
    """A source of historical market data.

    Implementations return tidy pandas objects so the engine never has to know
    where the data came from (FMP, Alpha Vantage, a synthetic generator, ...).
    """

    def get_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """Return daily OHLCV bars for ``symbol`` between ``start`` and ``end``.

        The returned DataFrame is indexed by a ``DatetimeIndex`` (one row per
        trading day) with columns ``open, high, low, close, volume``.
        """
        ...

    def get_chain(self, symbol: str, asof: date) -> pd.DataFrame:
        """Return the option chain for ``symbol`` as of ``asof``.

        Columns: ``expiry, strike, type, bid, ask, last, mark, volume, oi, iv,
        delta, gamma, theta, vega, rho`` (greeks/iv may be NaN if unavailable).
        Equity-only data sources may raise ``NotImplementedError``.
        """
        ...


@runtime_checkable
class CostModel(Protocol):
    """Computes fill prices and commissions for orders."""

    def compute_fill_price(self, order: Order, market: MarketQuote) -> float:
        """Return the per-unit fill price for ``order`` given the current quote."""
        ...

    def compute_commission(self, order: Order) -> float:
        """Return the total commission charged for ``order``."""
        ...
