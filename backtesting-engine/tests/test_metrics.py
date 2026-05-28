"""Metrics tests against hand-computed cases."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from backtesting_engine.metrics import (
    avg_loss,
    avg_win,
    cagr,
    calmar,
    compute_metrics,
    cvar,
    max_drawdown,
    profit_factor,
    sharpe,
    sortino,
    total_return,
    win_rate,
)


def _curve(values: list[float]) -> pd.Series:
    idx = pd.bdate_range("2020-01-01", periods=len(values))
    return pd.Series(values, index=idx, dtype="float64")


def test_total_return() -> None:
    assert total_return(_curve([100, 110])) == pytest.approx(0.10)
    assert total_return(_curve([100, 50])) == pytest.approx(-0.50)


def test_total_return_empty() -> None:
    assert total_return(_curve([100])) == 0.0


def test_cagr_one_year() -> None:
    # 252 steps doubling -> CAGR ~ 100% over exactly one year.
    values = list(np.linspace(100, 200, 253))
    assert cagr(_curve(values)) == pytest.approx(1.0, abs=0.02)


def test_max_drawdown() -> None:
    # peak 120 then trough 90 -> dd = 25%
    assert max_drawdown(_curve([100, 120, 90, 110])) == pytest.approx(0.25)


def test_max_drawdown_monotonic_zero() -> None:
    assert max_drawdown(_curve([100, 101, 102, 103])) == 0.0


def test_sharpe_constant_returns() -> None:
    # Constant positive daily return -> zero std -> Sharpe defined as 0 here.
    assert sharpe(_curve([100, 101, 102.01, 103.0301])) == 0.0


def test_sharpe_known() -> None:
    # Returns alternating but with positive mean; compare to manual computation.
    rets = pd.Series([0.01, -0.005, 0.02, -0.01, 0.015])
    curve_vals = [100.0]
    for r in rets:
        curve_vals.append(curve_vals[-1] * (1 + r))
    expected = rets.mean() / rets.std(ddof=1) * math.sqrt(252)
    assert sharpe(_curve(curve_vals)) == pytest.approx(expected, rel=1e-6)


def test_sortino_no_downside() -> None:
    assert sortino(_curve([100, 101, 102, 103])) == 0.0


def test_calmar() -> None:
    values = list(np.linspace(100, 200, 253))
    c = _curve(values)
    assert calmar(c) == 0.0  # strictly increasing -> zero drawdown -> 0


def test_cvar_tail() -> None:
    curve = _curve([100, 99, 101, 98, 102, 97])
    val = cvar(curve, alpha=0.5)
    assert val > 0  # there are losing days


def test_trade_metrics() -> None:
    log = pd.DataFrame(
        {
            "realized_pnl": [0.0, 100.0, -50.0, 200.0, -25.0, 0.0],
        }
    )
    assert win_rate(log) == pytest.approx(2 / 4)
    assert avg_win(log) == pytest.approx(150.0)
    assert avg_loss(log) == pytest.approx(-37.5)
    assert profit_factor(log) == pytest.approx(300.0 / 75.0)


def test_profit_factor_no_losses_inf() -> None:
    log = pd.DataFrame({"realized_pnl": [10.0, 20.0]})
    assert math.isinf(profit_factor(log))


def test_empty_trade_log() -> None:
    log = pd.DataFrame({"realized_pnl": []})
    assert win_rate(log) == 0.0
    assert avg_win(log) == 0.0
    assert avg_loss(log) == 0.0
    assert profit_factor(log) == 0.0


def test_compute_metrics_keys() -> None:
    m = compute_metrics(_curve([100, 110, 105, 120]), pd.DataFrame({"realized_pnl": [50.0, -10.0]}))
    for key in [
        "total_return",
        "cagr",
        "sharpe",
        "sortino",
        "max_drawdown",
        "calmar",
        "cvar_5pct",
        "win_rate",
        "avg_win",
        "avg_loss",
        "profit_factor",
        "num_trades",
    ]:
        assert key in m
