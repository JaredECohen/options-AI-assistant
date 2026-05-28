"""Liquidity helpers: bid/ask spread (absolute and relative), volume, OI."""

from __future__ import annotations

import numpy as np
import pandas as pd


def spread(bid: float, ask: float) -> float:
    """Absolute bid/ask spread."""
    return float(ask - bid)


def relative_spread(bid: float, ask: float) -> float:
    """Spread as a fraction of the mid price (NaN if mid <= 0)."""
    mid = 0.5 * (bid + ask)
    if mid <= 0:
        return float("nan")
    return float((ask - bid) / mid)


def add_liquidity_metrics(chain: pd.DataFrame) -> pd.DataFrame:
    """Add ``mid``, ``spread`` and ``rel_spread`` columns to an option chain.

    Expects ``bid`` and ``ask`` columns; ``volume`` and ``oi`` are passed through
    if present. ``rel_spread`` is the spread divided by the mid (NaN where mid<=0).
    """
    df = chain.copy()
    df["mid"] = 0.5 * (df["bid"] + df["ask"])
    df["spread"] = df["ask"] - df["bid"]
    df["rel_spread"] = np.where(df["mid"] > 0, df["spread"] / df["mid"], np.nan)
    return df
