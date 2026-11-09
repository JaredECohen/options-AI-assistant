"""Historical volatility, IV rank / percentile and constant-maturity ATM IV.

Definitions (explicit, because vendors disagree):
- HV_n: sample standard deviation (ddof=1) of the last n daily close-to-close
  log returns ending AT the as-of date, times sqrt(252). Requires n returns,
  i.e. n+1 closes. Windows: 10, 20, 60, 252.
- IV rank (lookback L observations, default 252): (IV_t - min) / (max - min)
  over the L observations ending at t, inclusive. 0..100.
- IV percentile: share of the L-1 PRIOR observations strictly below IV_t. 0..100.
- Both require at least MIN_HISTORY_FRACTION * L observations; otherwise the
  result is unavailable with reason "insufficient_history". If max == min the
  rank is unavailable ("flat_history").
- No lookahead: every function takes data up to and including the as-of point
  only; callers must not pass later rows (helpers slice by date to enforce it).
- Constant-maturity ATM IV at target days D: linear interpolation in total
  variance (sigma^2 * T) between the two expiries bracketing D; no
  extrapolation (unavailable if D is outside the observed expiries).
"""
from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

TRADING_DAYS = 252
HV_WINDOWS = (10, 20, 60, 252)
MIN_HISTORY_FRACTION = 0.8


@dataclass(frozen=True)
class Metric:
    value: float | None
    reason: str = ""  # empty when value is available

    def as_json(self, digits: int = 4) -> dict:
        return {"value": None if self.value is None else round(self.value, digits), "unavailable_reason": self.reason or None}


def upto(dates: Sequence[dt.date], values: Sequence[float], as_of: dt.date) -> tuple[list[dt.date], list[float]]:
    """Slice a date-sorted series to rows on or before as_of (lookahead guard)."""
    pairs = [(d, v) for d, v in zip(dates, values, strict=True) if d <= as_of]
    pairs.sort(key=lambda p: p[0])
    return [p[0] for p in pairs], [p[1] for p in pairs]


def historical_vol(closes: Sequence[float], window: int) -> Metric:
    arr = np.asarray(closes, dtype=float)
    if arr.size < window + 1:
        return Metric(None, "insufficient_history")
    tail = arr[-(window + 1):]
    if np.any(~np.isfinite(tail)) or np.any(tail <= 0):
        return Metric(None, "invalid_prices")
    rets = np.diff(np.log(tail))
    return Metric(float(np.std(rets, ddof=1) * math.sqrt(TRADING_DAYS)))


def hv_set(closes: Sequence[float]) -> dict[str, Metric]:
    return {f"hv{w}": historical_vol(closes, w) for w in HV_WINDOWS}


def iv_rank(iv_history: Sequence[float], lookback: int = TRADING_DAYS) -> Metric:
    arr = np.asarray([x for x in iv_history if x is not None and math.isfinite(x)], dtype=float)
    window = arr[-lookback:]
    if window.size < max(2, math.ceil(MIN_HISTORY_FRACTION * lookback)):
        return Metric(None, "insufficient_history")
    lo, hi, cur = float(window.min()), float(window.max()), float(window[-1])
    if hi - lo <= 1e-12:
        return Metric(None, "flat_history")
    return Metric(100.0 * (cur - lo) / (hi - lo))


def iv_percentile(iv_history: Sequence[float], lookback: int = TRADING_DAYS) -> Metric:
    arr = np.asarray([x for x in iv_history if x is not None and math.isfinite(x)], dtype=float)
    window = arr[-lookback:]
    if window.size < max(2, math.ceil(MIN_HISTORY_FRACTION * lookback)):
        return Metric(None, "insufficient_history")
    cur, prior = window[-1], window[:-1]
    return Metric(100.0 * float(np.mean(prior < cur)))


def ratio(numerator: Metric, denominator: Metric) -> Metric:
    if numerator.value is None:
        return Metric(None, numerator.reason)
    if denominator.value is None:
        return Metric(None, denominator.reason)
    if denominator.value <= 0:
        return Metric(None, "zero_denominator")
    return Metric(numerator.value / denominator.value)


def constant_maturity(points: Sequence[tuple[float, float]], target_days: float) -> Metric:
    """points: (days_to_expiry, iv). Linear in total variance; no extrapolation."""
    pts = sorted((d, v) for d, v in points if d > 0 and v is not None and math.isfinite(v) and v > 0)
    if not pts:
        return Metric(None, "no_expiries")
    for d, v in pts:
        if abs(d - target_days) < 1e-9:
            return Metric(v)
    below = [p for p in pts if p[0] < target_days]
    above = [p for p in pts if p[0] > target_days]
    if not below or not above:
        return Metric(None, "target_outside_expiries")
    (d1, v1), (d2, v2) = below[-1], above[0]
    t1, t2, t = d1 / 365.0, d2 / 365.0, target_days / 365.0
    w1, w2 = v1 * v1 * t1, v2 * v2 * t2
    wt = w1 + (w2 - w1) * (t - t1) / (t2 - t1)
    if wt <= 0:
        return Metric(None, "negative_total_variance")
    return Metric(math.sqrt(wt / t))
