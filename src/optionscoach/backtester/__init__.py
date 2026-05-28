"""Backtester: pre-built option strategies wrapping the ``backtesting_engine``."""

from optionscoach.backtester.runner import StrategyConfig, run_strategy
from optionscoach.backtester.strategies import (
    CashSecuredPut,
    CoveredCall,
    IronCondor,
    ShortPut,
    ShortStraddle,
    ShortStrangle,
)

__all__ = [
    "CashSecuredPut",
    "CoveredCall",
    "IronCondor",
    "ShortPut",
    "ShortStraddle",
    "ShortStrangle",
    "StrategyConfig",
    "run_strategy",
]
