"""Instrument-specific execution helpers used by the backtest loop.

The :mod:`equity` module builds quotes from OHLCV bars; the :mod:`options` module
handles option quoting, greeks-at-fill, expiration settlement, and a simplified
Reg-T margin check.
"""

from backtesting_engine.engines import equity, options

__all__ = ["equity", "options"]
