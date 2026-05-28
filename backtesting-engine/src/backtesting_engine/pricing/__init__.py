"""Black-Scholes pricing and greeks used across the engine.

This module is intentionally dependency-light (numpy + scipy only) and pure so it
can be reused by the synthetic data adapter, the options engine (greeks at fill),
and downstream consumers such as OptionsCoach.AI.
"""

from backtesting_engine.pricing.black_scholes import (
    Greeks,
    OptionType,
    as_option_type,
    bs_price,
    greeks,
    implied_volatility,
)

__all__ = [
    "Greeks",
    "OptionType",
    "as_option_type",
    "bs_price",
    "greeks",
    "implied_volatility",
]
