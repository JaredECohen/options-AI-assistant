"""Adapter tests: rate limiter, disk cache, synthetic determinism."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from backtesting_engine.adapters import (
    CachedDataSource,
    RateLimiter,
    SyntheticOptionsAdapter,
    TokenBucket,
)


class FakeClock:
    """A controllable monotonic clock; sleeping advances it."""

    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, dt: float) -> None:
        self.t += dt


# -- rate limiter ---------------------------------------------------------
def test_token_bucket_blocks_when_empty() -> None:
    clock = FakeClock()
    bucket = TokenBucket(capacity=2, refill_rate=1.0, monotonic=clock.now)
    assert bucket.acquire(sleeper=clock.sleep) == 0.0
    assert bucket.acquire(sleeper=clock.sleep) == 0.0
    # Third acquire must wait exactly 1s (refill_rate=1/s, deterministic clock).
    waited = bucket.acquire(sleeper=clock.sleep)
    assert waited == pytest.approx(1.0, abs=1e-9)
    assert clock.t == pytest.approx(1.0, abs=1e-9)


def test_rate_limiter_combines_buckets() -> None:
    limiter = RateLimiter.per_minute_and_day(per_minute=5, per_day=25)
    waited = limiter.acquire(sleeper=lambda _s: None)
    assert waited == 0.0  # first call within budget


def test_token_bucket_validates_args() -> None:
    with pytest.raises(ValueError):
        TokenBucket(capacity=0, refill_rate=1)


# -- disk cache -----------------------------------------------------------
def test_cached_data_source_hits_cache(tmp_path) -> None:  # type: ignore[no-untyped-def]
    calls = {"bars": 0}

    class CountingSource:
        def get_bars(self, symbol, start, end):  # type: ignore[no-untyped-def]
            calls["bars"] += 1
            return pd.DataFrame(
                {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1.0]}
            )

        def get_chain(self, symbol, asof):  # type: ignore[no-untyped-def]
            raise NotImplementedError

    cached = CachedDataSource(CountingSource(), namespace="test", cache_root=tmp_path)
    a = cached.get_bars("SPY", date(2020, 1, 1), date(2020, 2, 1))
    b = cached.get_bars("SPY", date(2020, 1, 1), date(2020, 2, 1))
    assert calls["bars"] == 1  # second call served from cache
    pd.testing.assert_frame_equal(a, b)


# -- synthetic adapter ----------------------------------------------------
def test_synthetic_bars_are_deterministic() -> None:
    a = SyntheticOptionsAdapter(seed=1).get_bars("SPY", date(2020, 1, 1), date(2020, 6, 1))
    b = SyntheticOptionsAdapter(seed=1).get_bars("SPY", date(2020, 1, 1), date(2020, 6, 1))
    pd.testing.assert_frame_equal(a, b)
    assert list(a.columns) == ["open", "high", "low", "close", "volume"]
    assert (a["high"] >= a["close"]).all()
    assert (a["low"] <= a["close"]).all()


def test_synthetic_different_symbols_differ() -> None:
    src = SyntheticOptionsAdapter(seed=1)
    spy = src.get_bars("SPY", date(2020, 1, 1), date(2020, 6, 1))
    qqq = src.get_bars("QQQ", date(2020, 1, 1), date(2020, 6, 1))
    assert not spy["close"].equals(qqq["close"])


def test_synthetic_chain_schema_and_skew() -> None:
    chain = SyntheticOptionsAdapter(seed=3, start_price=400).get_chain("SPY", date(2020, 1, 15))
    for col in [
        "expiry",
        "strike",
        "type",
        "bid",
        "ask",
        "last",
        "mark",
        "volume",
        "oi",
        "iv",
        "delta",
        "gamma",
        "theta",
        "vega",
        "rho",
    ]:
        assert col in chain.columns
    assert set(chain["type"].unique()) == {"call", "put"}
    assert (chain["ask"] >= chain["bid"]).all()
    # Put skew: lowest strike IV should exceed ATM IV for a given expiry.
    one_exp = chain[chain["expiry"] == chain["expiry"].iloc[0]]
    low = one_exp.loc[one_exp["strike"].idxmin(), "iv"]
    high_strike_iv = one_exp.loc[one_exp["strike"].idxmax(), "iv"]
    assert low > high_strike_iv
