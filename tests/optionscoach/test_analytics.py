"""Tests for the analytics module (IV/HV, rank/percentile, greeks, liquidity)."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest
from backtesting_engine import SyntheticOptionsAdapter

from optionscoach.analytics import (
    add_liquidity_metrics,
    compute_greeks_df,
    historical_volatility,
    hv_windows,
    implied_vol_df,
    iv_percentile,
    iv_rank,
    relative_spread,
    single_greeks,
)


def test_single_greeks_atm_call_known() -> None:
    # ATM 30-day call at IV=20% -> delta ~0.5, positive gamma/vega, negative theta.
    g = single_greeks(100, 100, 30 / 365, 0.20, "call")
    assert g.delta == pytest.approx(0.5, abs=0.02)
    assert g.gamma > 0
    assert g.vega > 0
    assert g.theta < 0


def test_implied_vol_df_recovers_chain_iv() -> None:
    adapter = SyntheticOptionsAdapter(seed=4, start_price=400)
    asof = date(2020, 1, 15)
    chain = adapter.get_chain("SPY", asof)
    spot = float(chain["underlying_price"].iloc[0])
    # Solve IV from the chain's own marks; should match the generating IV closely.
    solved = implied_vol_df(chain.head(40), spot, asof=asof, price_col="mark")
    merged = solved.dropna(subset=["iv_solved"])
    assert not merged.empty
    err = (merged["iv_solved"] - merged["iv"]).abs()
    assert err.median() < 0.02


def test_compute_greeks_df_adds_columns() -> None:
    adapter = SyntheticOptionsAdapter(seed=4, start_price=400)
    asof = date(2020, 1, 15)
    chain = adapter.get_chain("SPY", asof).head(20)
    spot = float(chain["underlying_price"].iloc[0])
    out = compute_greeks_df(
        chain.drop(columns=["delta", "gamma", "theta", "vega", "rho"]), spot, asof=asof
    )
    for col in ["delta", "gamma", "theta", "vega", "rho"]:
        assert col in out.columns
    # Calls have positive delta, puts negative.
    calls = out[out["type"] == "call"]
    puts = out[out["type"] == "put"]
    assert (calls["delta"] >= 0).all()
    assert (puts["delta"] <= 0).all()


def test_historical_volatility_constant_growth_is_zero() -> None:
    # Exactly constant daily log-return -> zero rolling std -> zero HV.
    closes = pd.Series([100 * (1.001**i) for i in range(60)])
    hv = historical_volatility(closes, window=20)
    assert hv.dropna().iloc[-1] == pytest.approx(0.0, abs=1e-9)


def test_historical_volatility_matches_manual() -> None:
    rng = np.random.default_rng(0)
    closes = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 300))))
    hv = historical_volatility(closes, window=20)
    log_ret = np.log(closes / closes.shift(1))
    expected = log_ret.rolling(20).std(ddof=1).iloc[-1] * np.sqrt(252)
    assert hv.iloc[-1] == pytest.approx(expected, rel=1e-9)


def test_hv_windows_columns() -> None:
    closes = pd.Series(100 + np.arange(300) * 0.1)
    out = hv_windows(closes)
    assert list(out.columns) == ["hv_10", "hv_20", "hv_60", "hv_252"]


def test_iv_rank_and_percentile() -> None:
    # IV ramps 0.10 -> 0.30 then sits at 0.20: rank = 50, percentile ~ middle.
    series = pd.Series(list(np.linspace(0.10, 0.30, 100)) + [0.20])
    assert iv_rank(series) == pytest.approx(50.0, abs=1.0)
    pct = iv_percentile(series)
    assert 40 < pct < 60


def test_iv_rank_flat_series_zero() -> None:
    assert iv_rank(pd.Series([0.2] * 10)) == 0.0


def test_liquidity_metrics() -> None:
    chain = pd.DataFrame({"bid": [1.00, 2.00], "ask": [1.10, 2.20]})
    out = add_liquidity_metrics(chain)
    assert out["mid"].tolist() == pytest.approx([1.05, 2.10])
    assert out["spread"].tolist() == pytest.approx([0.10, 0.20])
    assert out["rel_spread"].iloc[0] == pytest.approx(0.10 / 1.05)
    assert relative_spread(1.00, 1.10) == pytest.approx(0.10 / 1.05)
