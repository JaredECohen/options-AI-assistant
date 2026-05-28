"""Tests for the IV surface module."""

from __future__ import annotations

from datetime import date

import numpy as np
import plotly.graph_objects as go
import pytest
from backtesting_engine import SyntheticOptionsAdapter

from optionscoach.surface import (
    Surface,
    fit_surface,
    plot_surface_3d,
    skew_curves,
    term_structure,
)


def _chain_and_spot():  # type: ignore[no-untyped-def]
    adapter = SyntheticOptionsAdapter(seed=8, start_price=400)
    asof = date(2020, 1, 15)
    chain = adapter.get_chain("SPY", asof)
    spot = float(chain["underlying_price"].iloc[0])
    return chain, spot, asof


def test_fit_and_query_surface() -> None:
    chain, spot, asof = _chain_and_spot()
    surface = fit_surface(chain, spot, asof)
    assert isinstance(surface, Surface)
    atm_short = surface.iv(0.0, 30 / 365)
    assert 0.05 < atm_short < 1.0


def test_surface_skew_is_downward_in_strike() -> None:
    chain, spot, asof = _chain_and_spot()
    surface = fit_surface(chain, spot, asof)
    t = 60 / 365
    # Put skew: lower strikes (negative log-moneyness) have higher IV.
    iv_low = surface.iv(-0.10, t)
    iv_high = surface.iv(0.10, t)
    assert iv_low > iv_high


def test_skew_curves_structure() -> None:
    chain, spot, asof = _chain_and_spot()
    curves = skew_curves(chain, spot, asof)
    assert len(curves) >= 2
    sample = next(iter(curves.values()))
    assert {"log_moneyness", "strike", "iv"}.issubset(sample.columns)
    # Each curve is sorted by log-moneyness.
    assert sample["log_moneyness"].is_monotonic_increasing


def test_term_structure_buckets() -> None:
    chain, spot, asof = _chain_and_spot()
    surface = fit_surface(chain, spot, asof)
    ts = term_structure(surface)
    assert set(np.round(ts["moneyness"].unique(), 2)) == {0.90, 1.00, 1.10}
    assert {"t", "moneyness", "iv"}.issubset(ts.columns)


def test_plot_surface_returns_figure() -> None:
    chain, spot, asof = _chain_and_spot()
    surface = fit_surface(chain, spot, asof)
    fig = plot_surface_3d(surface)
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 1


def test_fit_requires_enough_points() -> None:
    chain, spot, asof = _chain_and_spot()
    with pytest.raises(ValueError):
        fit_surface(chain.head(2), spot, asof)
