"""Black-Scholes-Merton pricing, greeks and implied volatility (vectorized).

Model and conventions (stated so every number is interpretable):
- European exercise, lognormal underlying, continuous risk-free rate r and
  continuous dividend yield q, both flat to expiry. Listed US equity options
  are American; for calls on non-dividend payers BSM is exact, otherwise it
  is an approximation (early-exercise premium is ignored, mostly for deep ITM
  puts). Results are labelled model "bsm-european".
- Time to expiry T in years, ACT/365 calendar time.
- Units: price per share; delta per $1; gamma per $1 per $1; theta per
  CALENDAR DAY; vega per 1 volatility POINT (0.01); rho per 1 percentage point
  (0.01) of r.
- Invalid inputs (non-positive S/K/T/sigma, prices outside no-arbitrage
  bounds) produce NaN with a reason code, never a fabricated value.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.special import ndtr

MODEL = "bsm-european"
MIN_TIME_VALUE = 1e-4  # USD per share


def _ncdf(x):
    return ndtr(np.asarray(x, dtype=float))


def _npdf(x):
    x = np.asarray(x, dtype=float)
    return np.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _arr(*xs):
    return np.broadcast_arrays(*[np.asarray(x, dtype=float) for x in xs])


def _d1d2(S, K, T, r, q, sigma):
    with np.errstate(divide="ignore", invalid="ignore"):
        vol_t = sigma * np.sqrt(T)
        d1 = (np.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / vol_t
        d2 = d1 - vol_t
    return d1, d2


def price(S, K, T, r, q, sigma, is_call):
    S, K, T, r, q, sigma, is_call = _arr(S, K, T, r, q, sigma, is_call)
    is_call = is_call.astype(bool)
    d1, d2 = _d1d2(S, K, T, r, q, sigma)
    df_r, df_q = np.exp(-r * T), np.exp(-q * T)
    call = S * df_q * _ncdf(d1) - K * df_r * _ncdf(d2)
    put = K * df_r * _ncdf(-d2) - S * df_q * _ncdf(-d1)
    out = np.where(is_call, call, put)
    bad = (S <= 0) | (K <= 0) | (T <= 0) | (sigma <= 0) | ~np.isfinite(out)
    return np.where(bad, np.nan, out)


def greeks(S, K, T, r, q, sigma, is_call) -> dict[str, np.ndarray]:
    S, K, T, r, q, sigma, is_call = _arr(S, K, T, r, q, sigma, is_call)
    is_call = is_call.astype(bool)
    d1, d2 = _d1d2(S, K, T, r, q, sigma)
    df_r, df_q = np.exp(-r * T), np.exp(-q * T)
    pdf = _npdf(d1)
    with np.errstate(divide="ignore", invalid="ignore"):
        delta = np.where(is_call, df_q * _ncdf(d1), -df_q * _ncdf(-d1))
        gamma = df_q * pdf / (S * sigma * np.sqrt(T))
        vega = S * df_q * pdf * np.sqrt(T) / 100.0
        common = -(S * df_q * pdf * sigma) / (2.0 * np.sqrt(T))
        theta_call = common - r * K * df_r * _ncdf(d2) + q * S * df_q * _ncdf(d1)
        theta_put = common + r * K * df_r * _ncdf(-d2) - q * S * df_q * _ncdf(-d1)
        theta = np.where(is_call, theta_call, theta_put) / 365.0
        rho = np.where(is_call, K * T * df_r * _ncdf(d2), -K * T * df_r * _ncdf(-d2)) / 100.0
    bad = (S <= 0) | (K <= 0) | (T <= 0) | (sigma <= 0)
    return {name: np.where(bad, np.nan, val) for name, val in
            {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega, "rho": rho}.items()}


def no_arbitrage_bounds(S, K, T, r, q, is_call):
    S, K, T, r, q, is_call = _arr(S, K, T, r, q, is_call)
    df_r, df_q = np.exp(-r * T), np.exp(-q * T)
    lower = np.where(is_call.astype(bool), np.maximum(S * df_q - K * df_r, 0.0), np.maximum(K * df_r - S * df_q, 0.0))
    upper = np.where(is_call.astype(bool), S * df_q, K * df_r)
    return lower, upper


def implied_vol(option_price, S, K, T, r, q, is_call, tol=1e-8, max_iter=100) -> tuple[np.ndarray, np.ndarray]:
    """Return (iv, reason). reason is "" where iv is valid, else one of
    invalid_input | below_intrinsic | above_upper_bound | insufficient_time_value | no_convergence."""
    P, S, K, T, r, q, is_call = _arr(option_price, S, K, T, r, q, is_call)
    lower, upper = no_arbitrage_bounds(S, K, T, r, q, is_call)
    reason = np.full(P.shape, "", dtype=object)
    invalid = ~np.isfinite(P) | (P <= 0) | (S <= 0) | (K <= 0) | (T <= 0)
    reason[invalid] = "invalid_input"
    reason[(reason == "") & (P < lower - 1e-12)] = "below_intrinsic"
    reason[(reason == "") & (P >= upper)] = "above_upper_bound"
    # Time value below a hundredth of a cent cannot identify volatility (vega ~ 0):
    # any sigma in a wide range reprices it, so report it instead of guessing.
    reason[(reason == "") & (P - lower < MIN_TIME_VALUE)] = "insufficient_time_value"
    active = reason == ""

    lo = np.full(P.shape, 1e-6)
    hi = np.full(P.shape, 5.0)
    sigma = np.full(P.shape, 0.3)
    for _ in range(max_iter):
        if not active.any():
            break
        val = price(S, K, T, r, q, sigma, is_call)
        diff = val - P
        lo = np.where(active & (diff < 0), sigma, lo)
        hi = np.where(active & (diff > 0), sigma, hi)
        vega = greeks(S, K, T, r, q, sigma, is_call)["vega"] * 100.0
        with np.errstate(divide="ignore", invalid="ignore"):
            newton = sigma - diff / vega
        ok = np.isfinite(newton) & (newton > lo) & (newton < hi)
        sigma = np.where(active, np.where(ok, newton, 0.5 * (lo + hi)), sigma)
        done = np.abs(diff) < tol * np.maximum(1.0, P)
        active = active & ~done
    final = price(S, K, T, r, q, sigma, is_call)
    unconverged = (reason == "") & (np.abs(final - P) > 1e-5 * np.maximum(1.0, P))
    reason[unconverged] = "no_convergence"
    iv = np.where(reason == "", sigma, np.nan)
    return iv, reason
