"""Black-Scholes-Merton pricing, greeks, and implied-volatility solver.

All functions assume European options on a single underlying with a continuous
dividend yield ``q`` and continuously-compounded risk-free rate ``r``. Prices are
per-share (multiply by the contract multiplier, typically 100, for dollar values).

Conventions
-----------
* ``T`` is time to expiry in **years** (e.g. 30/365 for a 30-day option).
* ``sigma`` is annualized volatility as a decimal (0.20 == 20% vol).
* ``theta`` is returned **per calendar day** (the annual theta divided by 365),
  which is how it is quoted on most trading screens.
* ``vega`` is returned **per 1 vol point** (i.e. per 1% change in IV), again to
  match how it is conventionally displayed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, cast

from scipy.stats import norm

OptionType = Literal["call", "put"]


def as_option_type(value: str) -> OptionType:
    """Narrow a free-form ``"call"``/``"put"`` string to the ``OptionType`` literal."""
    lowered = value.lower()
    if lowered not in ("call", "put"):
        raise ValueError(f"option type must be 'call' or 'put', got {value!r}")
    return cast(OptionType, lowered)


# Smallest time / vol we treat as "expired" / "degenerate" to avoid divide-by-zero.
_EPS = 1e-12


@dataclass(frozen=True)
class Greeks:
    """First-order (and gamma) option sensitivities.

    Attributes
    ----------
    price:
        Theoretical per-share option price.
    delta:
        d(price)/d(spot).
    gamma:
        d(delta)/d(spot).
    theta:
        Time decay **per calendar day** (negative for long options).
    vega:
        d(price)/d(vol), expressed **per 1 vol point** (per 1%).
    rho:
        d(price)/d(rate), expressed **per 1 rate point** (per 1%).
    """

    price: float
    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float


def _d1_d2(
    spot: float, strike: float, t: float, r: float, sigma: float, q: float
) -> tuple[float, float]:
    vol_sqrt_t = sigma * math.sqrt(t)
    d1 = (math.log(spot / strike) + (r - q + 0.5 * sigma * sigma) * t) / vol_sqrt_t
    d2 = d1 - vol_sqrt_t
    return d1, d2


def _intrinsic(
    spot: float, strike: float, option_type: OptionType, t: float, r: float, q: float
) -> float:
    """Discounted intrinsic value, used at/near expiry where the model degenerates."""
    fwd = spot * math.exp(-q * t)
    k = strike * math.exp(-r * t)
    if option_type == "call":
        return max(fwd - k, 0.0)
    return max(k - fwd, 0.0)


def bs_price(
    spot: float,
    strike: float,
    t: float,
    r: float,
    sigma: float,
    option_type: OptionType,
    q: float = 0.0,
) -> float:
    """Return the Black-Scholes price of a European option (per share).

    Degenerate inputs (``t <= 0`` or ``sigma <= 0``) collapse to discounted
    intrinsic value rather than raising, which keeps the per-bar loop robust at
    expiration.
    """
    if t <= _EPS or sigma <= _EPS or spot <= _EPS or strike <= _EPS:
        return _intrinsic(spot, strike, option_type, max(t, 0.0), r, q)
    d1, d2 = _d1_d2(spot, strike, t, r, sigma, q)
    disc_spot = spot * math.exp(-q * t)
    disc_strike = strike * math.exp(-r * t)
    if option_type == "call":
        return float(disc_spot * norm.cdf(d1) - disc_strike * norm.cdf(d2))
    return float(disc_strike * norm.cdf(-d2) - disc_spot * norm.cdf(-d1))


def greeks(
    spot: float,
    strike: float,
    t: float,
    r: float,
    sigma: float,
    option_type: OptionType,
    q: float = 0.0,
) -> Greeks:
    """Return price + greeks for a European option.

    See the module docstring for theta/vega/rho units (per-day, per-vol-point,
    per-rate-point respectively).
    """
    price = bs_price(spot, strike, t, r, sigma, option_type, q)
    if t <= _EPS or sigma <= _EPS or spot <= _EPS or strike <= _EPS:
        # At expiry delta is a step function; report the limiting values.
        itm = (option_type == "call" and spot > strike) or (option_type == "put" and spot < strike)
        delta = (1.0 if option_type == "call" else -1.0) if itm else 0.0
        return Greeks(price=price, delta=delta, gamma=0.0, theta=0.0, vega=0.0, rho=0.0)

    d1, d2 = _d1_d2(spot, strike, t, r, sigma, q)
    sqrt_t = math.sqrt(t)
    pdf_d1 = float(norm.pdf(d1))
    disc_q = math.exp(-q * t)
    disc_r = math.exp(-r * t)

    if option_type == "call":
        delta = disc_q * norm.cdf(d1)
        theta_annual = (
            -(spot * disc_q * pdf_d1 * sigma) / (2.0 * sqrt_t)
            - r * strike * disc_r * norm.cdf(d2)
            + q * spot * disc_q * norm.cdf(d1)
        )
        rho = strike * t * disc_r * norm.cdf(d2)
    else:
        delta = -disc_q * norm.cdf(-d1)
        theta_annual = (
            -(spot * disc_q * pdf_d1 * sigma) / (2.0 * sqrt_t)
            + r * strike * disc_r * norm.cdf(-d2)
            - q * spot * disc_q * norm.cdf(-d1)
        )
        rho = -strike * t * disc_r * norm.cdf(-d2)

    gamma = (disc_q * pdf_d1) / (spot * sigma * sqrt_t)
    vega = spot * disc_q * pdf_d1 * sqrt_t

    return Greeks(
        price=price,
        delta=delta,
        gamma=gamma,
        theta=theta_annual / 365.0,  # per calendar day
        vega=vega / 100.0,  # per 1 vol point (1%)
        rho=rho / 100.0,  # per 1 rate point (1%)
    )


def implied_volatility(
    target_price: float,
    spot: float,
    strike: float,
    t: float,
    r: float,
    option_type: OptionType,
    q: float = 0.0,
    *,
    tol: float = 1e-6,
    max_iter: int = 100,
) -> float:
    """Solve for implied volatility via Newton-Raphson with a bisection fallback.

    Returns ``nan`` if the target price is below intrinsic value (no real IV) or
    the solver fails to converge within ``max_iter``.
    """
    if t <= _EPS or target_price <= 0.0:
        return float("nan")

    intrinsic = _intrinsic(spot, strike, option_type, t, r, q)
    if target_price < intrinsic - tol:
        return float("nan")

    # Newton-Raphson seeded with a Brenner-Subrahmanyam style guess.
    sigma = max(math.sqrt(2.0 * math.pi / t) * target_price / spot, 0.05)
    for _ in range(max_iter):
        price = bs_price(spot, strike, t, r, sigma, option_type, q)
        # vega here is per unit sigma (not per vol point), so recompute raw.
        d1, _ = _d1_d2(spot, strike, t, r, sigma, q)
        raw_vega = spot * math.exp(-q * t) * float(norm.pdf(d1)) * math.sqrt(t)
        diff = price - target_price
        if abs(diff) < tol:
            return sigma
        if raw_vega < _EPS:
            break
        sigma -= diff / raw_vega
        if sigma <= 0.0 or sigma > 10.0:
            break  # diverged; hand off to bisection

    # Bisection fallback on a wide, well-behaved bracket.
    lo, hi = 1e-4, 10.0
    p_lo = bs_price(spot, strike, t, r, lo, option_type, q) - target_price
    p_hi = bs_price(spot, strike, t, r, hi, option_type, q) - target_price
    if p_lo * p_hi > 0.0:
        return float("nan")
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        p_mid = bs_price(spot, strike, t, r, mid, option_type, q) - target_price
        if abs(p_mid) < tol:
            return mid
        if p_lo * p_mid < 0.0:
            hi = mid
        else:
            lo, p_lo = mid, p_mid
    return 0.5 * (lo + hi)
