"""Performance metrics computed from an equity curve and trade log.

Every function is pure and unit-tested against hand-computed cases. ``Sharpe`` and
``Sortino`` assume 252 trading days/year and a risk-free rate of 0 unless told
otherwise. All return-based metrics operate on the daily simple returns of the
equity curve.
"""

from backtesting_engine.metrics.core import (
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

__all__ = [
    "avg_loss",
    "avg_win",
    "cagr",
    "calmar",
    "compute_metrics",
    "cvar",
    "max_drawdown",
    "profit_factor",
    "sharpe",
    "sortino",
    "total_return",
    "win_rate",
]
