"""Integration tests that hit the live FMP / Alpha Vantage APIs.

These are skipped unless the relevant API key is present in the environment, so
the default `pytest` run (and CI) never needs network access or credentials.
"""

from __future__ import annotations

import os
from datetime import date

import pytest

from backtesting_engine.adapters import (
    AlphaVantageOptionsAdapter,
    CachedDataSource,
    FMPEquityAdapter,
)


@pytest.mark.skipif(not os.environ.get("FMP_API_KEY"), reason="FMP_API_KEY not set")
def test_fmp_returns_bars() -> None:
    source = CachedDataSource(FMPEquityAdapter(), namespace="fmp")
    df = source.get_bars("SPY", date(2023, 1, 3), date(2023, 1, 31))
    assert not df.empty
    assert {"open", "high", "low", "close", "volume"}.issubset(df.columns)


@pytest.mark.skipif(
    not os.environ.get("ALPHA_VANTAGE_API_KEY"), reason="ALPHA_VANTAGE_API_KEY not set"
)
def test_alphavantage_returns_chain() -> None:
    source = CachedDataSource(AlphaVantageOptionsAdapter(), namespace="alphavantage")
    chain = source.get_chain("SPY", date(2023, 1, 3))
    assert not chain.empty
    assert {"expiry", "strike", "type", "bid", "ask"}.issubset(chain.columns)


def test_fmp_missing_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="FMP_API_KEY"):
        _ = FMPEquityAdapter().api_key


def test_alphavantage_missing_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ALPHA_VANTAGE_API_KEY"):
        _ = AlphaVantageOptionsAdapter().api_key


def test_adapters_satisfy_protocol() -> None:
    from backtesting_engine.core.protocols import DataSource

    assert isinstance(FMPEquityAdapter(api_key="x"), DataSource)
    assert isinstance(AlphaVantageOptionsAdapter(api_key="x"), DataSource)
