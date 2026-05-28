"""Implied and historical volatility analytics.

Implied volatility reuses the engine's Newton-Raphson + bisection solver
(:func:`backtesting_engine.pricing.implied_volatility`) but is **vectorized over a
DataFrame** of contracts here. Historical volatility, IV rank and IV percentile
operate on price/IV history.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from backtesting_engine.pricing import as_option_type, implied_volatility

TRADING_DAYS = 252


def implied_vol_df(
    chain: pd.DataFrame,
    spot: float,
    *,
    asof: date | None = None,
    r: float = 0.0,
    q: float = 0.0,
    price_col: str = "mark",
    out_col: str = "iv_solved",
) -> pd.DataFrame:
    """Solve implied volatility for every row of an option ``chain``.

    The chain must have ``strike``, ``type`` and a price column (``mark`` by
    default). Time to expiry is taken from a ``t`` column (years) if present, else
    computed from an ``expiry`` column relative to ``asof``. Returns a copy of the
    chain with an added ``out_col`` of solved IVs (NaN where no real IV exists).
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

    def _solve(row: pd.Series, t: float) -> float:
        price = row[price_col]
        if pd.isna(price) or price <= 0:
            return float("nan")
        return implied_volatility(
            float(price),
            spot,
            float(row["strike"]),
            float(t),
            r,
            as_option_type(str(row["type"])),
            q,
        )

    df[out_col] = [_solve(row, t) for (_, row), t in zip(df.iterrows(), t_series, strict=True)]
    return df


def historical_volatility(
    closes: pd.Series, window: int = 20, *, trading_days: int = TRADING_DAYS
) -> pd.Series:
    """Annualized rolling historical volatility from a close-price series.

    Uses the rolling standard deviation of daily log returns, annualized by
    ``sqrt(trading_days)``. The returned series is aligned to ``closes`` (leading
    values are NaN until the window fills).
    """
    log_returns = np.log(closes / closes.shift(1))
    rolling_std = log_returns.rolling(window=window).std(ddof=1)
    return rolling_std * np.sqrt(trading_days)


def hv_windows(closes: pd.Series, windows: tuple[int, ...] = (10, 20, 60, 252)) -> pd.DataFrame:
    """Compute historical volatility for several windows at once.

    Returns a DataFrame with one column per window (``hv_10``, ``hv_20`` ...).
    """
    return pd.DataFrame({f"hv_{w}": historical_volatility(closes, w) for w in windows})


def iv_rank(iv_history: pd.Series, lookback: int = TRADING_DAYS) -> float:
    """IV rank: where the latest IV sits between its lookback min and max (0..100).

    ``iv_rank = 100 * (iv_now - iv_min) / (iv_max - iv_min)`` over the trailing
    ``lookback`` observations. Returns NaN with insufficient history.
    """
    series = iv_history.dropna().tail(lookback)
    if series.empty:
        return float("nan")
    iv_now = float(series.iloc[-1])
    iv_min, iv_max = float(series.min()), float(series.max())
    if iv_max == iv_min:
        return 0.0
    return 100.0 * (iv_now - iv_min) / (iv_max - iv_min)


def iv_percentile(iv_history: pd.Series, lookback: int = TRADING_DAYS) -> float:
    """IV percentile: % of trailing days with IV below the latest value (0..100)."""
    series = iv_history.dropna().tail(lookback)
    if series.empty:
        return float("nan")
    iv_now = float(series.iloc[-1])
    below = float((series < iv_now).sum())
    return 100.0 * below / len(series)
