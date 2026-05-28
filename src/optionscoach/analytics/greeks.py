"""Greeks analytics — thin, vectorized wrappers over the engine's Black-Scholes.

Conventions follow the engine: theta is per calendar day, vega per 1 vol point,
rho per 1 rate point. See :mod:`backtesting_engine.pricing`.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from backtesting_engine.pricing import Greeks, as_option_type
from backtesting_engine.pricing import greeks as bs_greeks

_GREEK_COLS = ("delta", "gamma", "theta", "vega", "rho")


def single_greeks(
    spot: float,
    strike: float,
    t: float,
    sigma: float,
    option_type: str,
    *,
    r: float = 0.0,
    q: float = 0.0,
) -> Greeks:
    """Greeks for one option. ``t`` is years to expiry; ``sigma`` is decimal vol."""
    return bs_greeks(spot, strike, t, r, sigma, as_option_type(option_type), q)


def compute_greeks_df(
    chain: pd.DataFrame,
    spot: float,
    *,
    asof: date | None = None,
    r: float = 0.0,
    q: float = 0.0,
    iv_col: str = "iv",
) -> pd.DataFrame:
    """Add ``delta, gamma, theta, vega, rho`` columns to an option ``chain``.

    Requires ``strike``, ``type``, an IV column (``iv``) and either a ``t``
    (years) column or an ``expiry`` column plus ``asof``.
    """
    df = chain.copy()
    if "t" in df.columns:
        t_series = df["t"].astype(float)
    else:
        if asof is None:
            raise ValueError("provide asof= or a 't' (years to expiry) column")
        expiry = pd.to_datetime(df["expiry"]).dt.date
        t_series = pd.Series(
            [(e - asof).days / 365.0 for e in expiry], index=df.index, dtype="float64"
        )

    records: list[dict[str, float]] = []
    for (_, row), t in zip(df.iterrows(), t_series, strict=True):
        g = bs_greeks(
            spot,
            float(row["strike"]),
            float(t),
            r,
            float(row[iv_col]),
            as_option_type(str(row["type"])),
            q,
        )
        records.append({c: getattr(g, c) for c in _GREEK_COLS})
    greeks_df = pd.DataFrame(records, index=df.index)
    for col in _GREEK_COLS:
        df[col] = greeks_df[col]
    return df
