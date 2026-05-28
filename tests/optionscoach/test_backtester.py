"""One end-to-end test per pre-built strategy on synthetic SPY data."""

from __future__ import annotations

from datetime import date

import pytest
from backtesting_engine import Result

from optionscoach.backtester import (
    CashSecuredPut,
    CoveredCall,
    IronCondor,
    ShortPut,
    ShortStraddle,
    ShortStrangle,
    StrategyConfig,
    run_strategy,
)

# A 9-month window is plenty to open, manage, expire and re-open positions while
# keeping the synthetic-data test suite fast.
START = date(2020, 1, 1)
END = date(2020, 9, 30)


@pytest.mark.parametrize(
    "strategy_cls",
    [ShortPut, CashSecuredPut, CoveredCall, ShortStraddle, ShortStrangle, IronCondor],
)
def test_strategy_runs_end_to_end(strategy_cls: type) -> None:
    config = StrategyConfig(symbol="SPY", target_delta=0.30, dte=30, time_exit_dte=7)
    result = run_strategy(strategy_cls, START, END, config=config)
    assert isinstance(result, Result)
    assert not result.equity_curve.empty
    # Every strategy should open at least one position over 18 months.
    assert len(result.trade_log) > 0
    # Metrics are always computed.
    assert "sharpe" in result.metrics
    # Equity stays finite and positive (no blow-ups in the synthetic world).
    assert result.final_equity > 0


def test_iron_condor_has_four_opening_legs() -> None:
    config = StrategyConfig(symbol="SPY", target_delta=0.25, dte=45, time_exit_dte=10)
    result = run_strategy(IronCondor, START, END, config=config)
    opens = result.trade_log[result.trade_log["tag"] == "IronCondor"]
    # The first entry opens four legs on the same day.
    first_day = opens["timestamp"].min()
    assert len(opens[opens["timestamp"] == first_day]) == 4
