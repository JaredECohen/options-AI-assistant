"""Shared helpers for the OptionsCoach Streamlit pages.

Handles ``src/`` path setup, default tickers/date ranges, and cached data loading.
By default everything uses the deterministic ``SyntheticOptionsAdapter`` so the UI
renders for SPY with no credentials. Set ``OPTIONSCOACH_LIVE=1`` (plus
``FMP_API_KEY`` / ``ALPHA_VANTAGE_API_KEY``) to pull live data instead.
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

# Make ``src/optionscoach`` and the vendored engine importable when run via
# ``streamlit run apps/streamlit/Home.py`` from the repo root.
_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "src", _ROOT / "backtesting-engine" / "src", _ROOT):
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402
from backtesting_engine import (  # noqa: E402
    CachedDataSource,
    FMPEquityAdapter,
    SyntheticOptionsAdapter,
)
from backtesting_engine.core.protocols import DataSource  # noqa: E402

DEFAULT_TICKERS = ["SPY", "QQQ", "IWM"]
TRADING_DAYS = 252
# Synthetic start prices keep the demo realistic per ticker.
_SYNTH_START = {"SPY": 400.0, "QQQ": 350.0, "IWM": 190.0}


def default_date_range() -> tuple[date, date]:
    """Last ~252 trading days (≈ 365 calendar days) ending today."""
    end = date.today()
    start = end - timedelta(days=int(TRADING_DAYS * 365 / 252))
    return start, end


def is_live() -> bool:
    return os.environ.get("OPTIONSCOACH_LIVE", "0") == "1"


@st.cache_resource
def get_equity_source(ticker: str) -> DataSource:
    """Cached equity bar source (live FMP if enabled, else synthetic)."""
    if is_live():
        return CachedDataSource(FMPEquityAdapter(), namespace="fmp")
    return SyntheticOptionsAdapter(start_price=_SYNTH_START.get(ticker, 300.0), seed=7)


@st.cache_resource
def get_options_source(ticker: str) -> DataSource:
    """Cached options chain source (synthetic by default).

    Live Alpha Vantage data is opt-in via OPTIONSCOACH_LIVE=1; the synthetic
    adapter is the default so the page always renders.
    """
    return SyntheticOptionsAdapter(start_price=_SYNTH_START.get(ticker, 300.0), seed=7)


@st.cache_data
def load_bars(ticker: str, start: date, end: date) -> pd.DataFrame:
    """Cached daily OHLCV bars for ``ticker``."""
    return get_equity_source(ticker).get_bars(ticker, start, end)


@st.cache_data
def load_chain(ticker: str, asof: date) -> pd.DataFrame:
    """Cached option chain for ``ticker`` as of ``asof``."""
    return get_options_source(ticker).get_chain(ticker, asof)


def sidebar_controls() -> tuple[str, date, date]:
    """Render the shared sidebar (ticker + date range) and return the selection."""
    st.sidebar.header("Controls")
    ticker = st.sidebar.selectbox("Ticker", DEFAULT_TICKERS, index=0)
    start_default, end_default = default_date_range()
    start = st.sidebar.date_input("Start", start_default)
    end = st.sidebar.date_input("End", end_default)
    if is_live():
        st.sidebar.caption("Live data mode (OPTIONSCOACH_LIVE=1)")
    else:
        st.sidebar.caption("Synthetic data (deterministic demo)")
    return ticker, start, end
