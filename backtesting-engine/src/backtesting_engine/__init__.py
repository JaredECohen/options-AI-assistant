"""backtesting-engine: a clear, EOD, per-bar event-driven backtester.

Public API re-exports the most commonly used building blocks so consumers can do::

    from backtesting_engine import Backtest, Strategy, SyntheticOptionsAdapter
"""

from backtesting_engine.adapters import (
    AlphaVantageOptionsAdapter,
    CachedDataSource,
    FMPEquityAdapter,
    SyntheticOptionsAdapter,
)
from backtesting_engine.core import (
    AssetType,
    Backtest,
    Bar,
    Fill,
    Instrument,
    MarketQuote,
    MarketSlice,
    OptionRight,
    Order,
    OrderType,
    Portfolio,
    Position,
    Result,
    Side,
    Strategy,
)
from backtesting_engine.costs import StandardEquityCostModel, StandardOptionsCostModel

__version__ = "0.1.0"

__all__ = [
    "AlphaVantageOptionsAdapter",
    "AssetType",
    "Backtest",
    "Bar",
    "CachedDataSource",
    "FMPEquityAdapter",
    "Fill",
    "Instrument",
    "MarketQuote",
    "MarketSlice",
    "OptionRight",
    "Order",
    "OrderType",
    "Portfolio",
    "Position",
    "Result",
    "Side",
    "StandardEquityCostModel",
    "StandardOptionsCostModel",
    "Strategy",
    "SyntheticOptionsAdapter",
    "__version__",
]
