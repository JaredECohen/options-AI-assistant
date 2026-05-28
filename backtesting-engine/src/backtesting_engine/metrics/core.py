"""Implementations of the performance metrics.

Notes on conventions
--------------------
* Returns are **daily simple returns** derived from the equity curve.
* Annualization uses ``TRADING_DAYS = 252``.
* ``max_drawdown`` is reported as a **positive** fraction (0.20 == a 20% drawdown).
* ``cvar`` (Expected Shortfall) is the mean of the worst ``alpha`` tail of daily
  returns, reported as a **positive** loss fraction.
* Trade-based metrics (win rate, profit factor, ...) use the ``realized_pnl``
  column of the trade log and only count rows where P&L was actually realized
  (i.e. closing fills).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def _returns(equity_curve: pd.Series) -> pd.Series:
    """Daily simple returns from an equity curve, NaNs dropped."""
    if equity_curve is None or len(equity_curve) < 2:
        return pd.Series(dtype="float64")
    return equity_curve.pct_change().dropna()


def total_return(equity_curve: pd.Series) -> float:
    """Cumulative return over the whole curve (0.10 == +10%)."""
    if equity_curve is None or len(equity_curve) < 2:
        return 0.0
    first = float(equity_curve.iloc[0])
    if first == 0:
        return 0.0
    return float(equity_curve.iloc[-1]) / first - 1.0


def cagr(equity_curve: pd.Series, periods_per_year: int = TRADING_DAYS) -> float:
    """Compound annual growth rate, annualized by the number of bars elapsed."""
    if equity_curve is None or len(equity_curve) < 2:
        return 0.0
    first = float(equity_curve.iloc[0])
    last = float(equity_curve.iloc[-1])
    if first <= 0 or last <= 0:
        return 0.0
    n_periods = len(equity_curve) - 1
    years = n_periods / periods_per_year
    if years <= 0:
        return 0.0
    return float((last / first) ** (1.0 / years) - 1.0)


def sharpe(
    equity_curve: pd.Series,
    risk_free: float = 0.0,
    periods_per_year: int = TRADING_DAYS,
) -> float:
    """Annualized Sharpe ratio of daily returns.

    ``risk_free`` is the annual risk-free rate; it is converted to a per-period
    rate before subtracting from returns.
    """
    rets = _returns(equity_curve)
    if rets.empty:
        return 0.0
    rf_per_period = risk_free / periods_per_year
    excess = rets - rf_per_period
    std = excess.std(ddof=1)
    if std == 0 or np.isnan(std):
        return 0.0
    return float(excess.mean() / std * np.sqrt(periods_per_year))


def sortino(
    equity_curve: pd.Series,
    risk_free: float = 0.0,
    periods_per_year: int = TRADING_DAYS,
) -> float:
    """Annualized Sortino ratio (downside-deviation-adjusted Sharpe)."""
    rets = _returns(equity_curve)
    if rets.empty:
        return 0.0
    rf_per_period = risk_free / periods_per_year
    excess = rets - rf_per_period
    downside = excess[excess < 0]
    if downside.empty:
        return 0.0
    downside_dev = np.sqrt((downside**2).mean())
    if downside_dev == 0 or np.isnan(downside_dev):
        return 0.0
    return float(excess.mean() / downside_dev * np.sqrt(periods_per_year))


def max_drawdown(equity_curve: pd.Series) -> float:
    """Largest peak-to-trough decline as a positive fraction."""
    if equity_curve is None or len(equity_curve) < 2:
        return 0.0
    running_max = equity_curve.cummax()
    drawdown = (equity_curve - running_max) / running_max
    mdd = drawdown.min()
    if np.isnan(mdd):
        return 0.0
    return float(abs(min(mdd, 0.0)))


def calmar(equity_curve: pd.Series, periods_per_year: int = TRADING_DAYS) -> float:
    """CAGR divided by max drawdown."""
    mdd = max_drawdown(equity_curve)
    if mdd == 0:
        return 0.0
    return cagr(equity_curve, periods_per_year) / mdd


def cvar(equity_curve: pd.Series, alpha: float = 0.05) -> float:
    """Conditional VaR (Expected Shortfall) of daily returns at level ``alpha``.

    Returns the average of the worst ``alpha`` fraction of daily returns as a
    positive loss (0.03 == an average 3% loss in the tail).
    """
    rets = _returns(equity_curve)
    if rets.empty:
        return 0.0
    cutoff = rets.quantile(alpha)
    tail = rets[rets <= cutoff]
    if tail.empty:
        tail = rets.nsmallest(max(1, int(len(rets) * alpha)))
    mean_tail = tail.mean()
    if np.isnan(mean_tail):
        return 0.0
    return float(abs(min(mean_tail, 0.0)))


# -- trade-based metrics --------------------------------------------------
def _closing_pnl(trade_log: pd.DataFrame) -> pd.Series:
    if trade_log is None or trade_log.empty or "realized_pnl" not in trade_log:
        return pd.Series(dtype="float64")
    pnl = trade_log["realized_pnl"].astype("float64")
    return pnl[pnl != 0.0]


def win_rate(trade_log: pd.DataFrame) -> float:
    """Fraction of closing trades with positive realized P&L."""
    pnl = _closing_pnl(trade_log)
    if pnl.empty:
        return 0.0
    return float((pnl > 0).sum() / len(pnl))


def avg_win(trade_log: pd.DataFrame) -> float:
    """Average realized P&L of winning trades."""
    pnl = _closing_pnl(trade_log)
    wins = pnl[pnl > 0]
    return float(wins.mean()) if not wins.empty else 0.0


def avg_loss(trade_log: pd.DataFrame) -> float:
    """Average realized P&L of losing trades (negative number)."""
    pnl = _closing_pnl(trade_log)
    losses = pnl[pnl < 0]
    return float(losses.mean()) if not losses.empty else 0.0


def profit_factor(trade_log: pd.DataFrame) -> float:
    """Gross profit divided by gross loss. ``inf`` if there are no losses."""
    pnl = _closing_pnl(trade_log)
    if pnl.empty:
        return 0.0
    gross_profit = pnl[pnl > 0].sum()
    gross_loss = abs(pnl[pnl < 0].sum())
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return float(gross_profit / gross_loss)


def trade_pnl_distribution(trade_log: pd.DataFrame) -> pd.Series:
    """Series of per-trade realized P&L (closing fills only)."""
    return _closing_pnl(trade_log).reset_index(drop=True)


def compute_metrics(equity_curve: pd.Series, trade_log: pd.DataFrame) -> dict[str, float]:
    """Compute the full metrics dictionary for a :class:`Result`."""
    return {
        "total_return": total_return(equity_curve),
        "cagr": cagr(equity_curve),
        "sharpe": sharpe(equity_curve),
        "sortino": sortino(equity_curve),
        "max_drawdown": max_drawdown(equity_curve),
        "calmar": calmar(equity_curve),
        "cvar_5pct": cvar(equity_curve, 0.05),
        "win_rate": win_rate(trade_log),
        "avg_win": avg_win(trade_log),
        "avg_loss": avg_loss(trade_log),
        "profit_factor": profit_factor(trade_log),
        "num_trades": float(len(_closing_pnl(trade_log))),
    }
