"""Data adapters implementing the :class:`DataSource` protocol.

* :class:`SyntheticOptionsAdapter` — deterministic, offline, used by all unit tests.
* :class:`FMPEquityAdapter` — live equity OHLCV (Financial Modeling Prep).
* :class:`AlphaVantageOptionsAdapter` — live option chains (Alpha Vantage).
* :class:`CachedDataSource` — wraps any source with a Parquet cache.
"""

from backtesting_engine.adapters.alphavantage import AlphaVantageOptionsAdapter
from backtesting_engine.adapters.cache import CachedDataSource, DiskCache
from backtesting_engine.adapters.fmp import FMPEquityAdapter
from backtesting_engine.adapters.rate_limit import RateLimiter, TokenBucket
from backtesting_engine.adapters.synthetic import SyntheticOptionsAdapter

__all__ = [
    "AlphaVantageOptionsAdapter",
    "CachedDataSource",
    "DiskCache",
    "FMPEquityAdapter",
    "RateLimiter",
    "SyntheticOptionsAdapter",
    "TokenBucket",
]
