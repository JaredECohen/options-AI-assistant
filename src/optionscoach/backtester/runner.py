"""A thin runner that wires an OptionsCoach strategy into the backtesting engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from backtesting_engine import (
    Backtest,
    Result,
    StandardEquityCostModel,
    StandardOptionsCostModel,
    Strategy,
    SyntheticOptionsAdapter,
)
from backtesting_engine.core.protocols import DataSource


@dataclass
class StrategyConfig:
    """Common configuration shared by the pre-built strategies."""

    symbol: str = "SPY"
    target_delta: float = 0.30
    dte: int = 30
    profit_target_pct: float = 0.50
    stop_loss_multiple: float = 2.0
    time_exit_dte: int = 7
    quantity: int = 1
    starting_cash: float = 100_000.0
    extra: dict[str, object] = field(default_factory=dict)

    def kwargs(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "target_delta": self.target_delta,
            "dte": self.dte,
            "profit_target_pct": self.profit_target_pct,
            "stop_loss_multiple": self.stop_loss_multiple,
            "time_exit_dte": self.time_exit_dte,
            "quantity": self.quantity,
            **self.extra,
        }


def run_strategy(
    strategy_cls: type[Strategy],
    start: date,
    end: date,
    *,
    config: StrategyConfig | None = None,
    data_source: DataSource | None = None,
) -> Result:
    """Build and run ``strategy_cls`` over ``[start, end]`` and return the Result.

    Defaults to the deterministic :class:`SyntheticOptionsAdapter` so the function
    is runnable offline (used by the per-strategy tests). Pass a ``data_source`` to
    run against live FMP/Alpha Vantage data instead.
    """
    cfg = config or StrategyConfig()
    source = data_source or SyntheticOptionsAdapter(start_price=400.0, seed=7)
    strategy = strategy_cls(**cfg.kwargs())  # type: ignore[arg-type]
    bt = Backtest(
        strategy,
        source,
        StandardEquityCostModel(),
        StandardOptionsCostModel(),
        starting_cash=cfg.starting_cash,
        equity_symbols=[cfg.symbol],
        option_symbols=[cfg.symbol],
    )
    return bt.run(start, end)
