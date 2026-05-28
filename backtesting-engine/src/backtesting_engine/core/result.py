"""The :class:`Result` object returned by a completed backtest."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from backtesting_engine.metrics import compute_metrics


@dataclass
class Result:
    """Outcome of a backtest: equity curve, trade log, and summary metrics.

    ``metrics`` is computed lazily from the equity curve and trade log when the
    object is created, so callers can simply read :attr:`metrics`.
    """

    equity_curve: pd.Series
    trade_log: pd.DataFrame
    starting_cash: float
    metrics: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.metrics:
            self.metrics = compute_metrics(self.equity_curve, self.trade_log)

    @property
    def final_equity(self) -> float:
        if self.equity_curve.empty:
            return self.starting_cash
        return float(self.equity_curve.iloc[-1])

    def summary(self) -> str:
        """Human-readable one-metric-per-line summary."""
        lines = [f"Final equity: ${self.final_equity:,.2f}"]
        lines += [f"{k}: {v:,.4f}" for k, v in self.metrics.items()]
        return "\n".join(lines)
