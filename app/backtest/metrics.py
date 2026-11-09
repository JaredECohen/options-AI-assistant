"""Backtest metrics with explicit definitions (P7).

- returns: simple daily returns of the equity curve.
- sharpe: mean(excess daily return)/std(ddof=1) * sqrt(252); rf is an annual rate
  converted to daily as rf/252. Unavailable with < 2 returns or zero dispersion.
- max_drawdown: largest peak-to-trough decline of equity, as a positive fraction.
- win_rate: share of closed trades with pnl > 0 (ties are not wins).
- cvar_95: mean of the worst 5% daily returns (historical, at least one obs),
  reported as a positive loss fraction.
"""
from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np


def daily_returns(equity: Sequence[float]) -> np.ndarray:
    e = np.asarray(equity, dtype=float)
    if e.size < 2:
        return np.array([])
    return e[1:] / e[:-1] - 1.0


def sharpe(returns: Sequence[float], rf_annual: float = 0.0) -> float | None:
    r = np.asarray(returns, dtype=float) - rf_annual / 252
    if r.size < 2:
        return None
    sd = float(np.std(r, ddof=1))
    if sd <= 1e-15:
        return None
    return float(np.mean(r) / sd * math.sqrt(252))


def max_drawdown(equity: Sequence[float]) -> float:
    e = np.asarray(equity, dtype=float)
    if e.size == 0:
        return 0.0
    peaks = np.maximum.accumulate(e)
    return float(np.max((peaks - e) / peaks))


def win_rate(pnls: Sequence[float]) -> float | None:
    if not pnls:
        return None
    return sum(1 for p in pnls if p > 0) / len(pnls)


def cvar(returns: Sequence[float], level: float = 0.95) -> float | None:
    r = np.sort(np.asarray(returns, dtype=float))
    if r.size == 0:
        return None
    k = max(1, int(math.floor((1 - level) * r.size)))
    return float(-np.mean(r[:k]))


def summarize(equity: Sequence[float], pnls: Sequence[float], rf_annual: float = 0.0) -> dict:
    rets = daily_returns(equity)
    return {
        "total_pnl": round(float(sum(pnls)), 2),
        "trades": len(pnls),
        "win_rate": win_rate(pnls),
        "sharpe": sharpe(rets, rf_annual),
        "max_drawdown": max_drawdown(equity),
        "cvar_95": cvar(rets, 0.95),
        "definitions": __doc__.strip().splitlines()[2:],
    }
