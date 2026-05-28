"""Black-Scholes pricing, greeks, and IV solver tests against known values."""

from __future__ import annotations

import math

import pytest

from backtesting_engine.pricing import bs_price, greeks, implied_volatility


def test_atm_call_known_value() -> None:
    # S=K=100, T=1, r=0, sigma=0.2 -> price = S*(2*N(sigma/2)-1) ~ 7.9656
    price = bs_price(100, 100, 1.0, 0.0, 0.20, "call")
    assert price == pytest.approx(7.9656, abs=1e-3)


def test_put_call_parity() -> None:
    s, k, t, r, sigma = 100.0, 105.0, 0.5, 0.03, 0.25
    call = bs_price(s, k, t, r, sigma, "call")
    put = bs_price(s, k, t, r, sigma, "put")
    # C - P = S - K*e^{-rT}
    assert call - put == pytest.approx(s - k * math.exp(-r * t), abs=1e-6)


def test_atm_call_delta_near_half() -> None:
    g = greeks(100, 100, 30 / 365, 0.0, 0.20, "call")
    assert g.delta == pytest.approx(0.5, abs=0.02)
    assert g.gamma > 0
    assert g.theta < 0  # long option decays
    assert g.vega > 0


def test_put_delta_negative() -> None:
    g = greeks(100, 100, 30 / 365, 0.0, 0.20, "put")
    assert -1.0 < g.delta < 0.0


def test_gamma_equal_call_put() -> None:
    gc = greeks(100, 100, 0.5, 0.01, 0.2, "call")
    gp = greeks(100, 100, 0.5, 0.01, 0.2, "put")
    assert gc.gamma == pytest.approx(gp.gamma, abs=1e-9)
    assert gc.vega == pytest.approx(gp.vega, abs=1e-9)


def test_expired_option_is_intrinsic() -> None:
    assert bs_price(110, 100, 0.0, 0.0, 0.2, "call") == pytest.approx(10.0)
    assert bs_price(90, 100, 0.0, 0.0, 0.2, "call") == pytest.approx(0.0)
    g = greeks(110, 100, 0.0, 0.0, 0.2, "call")
    assert g.delta == 1.0
    assert g.gamma == 0.0


def test_implied_volatility_round_trip() -> None:
    true_sigma = 0.35
    price = bs_price(100, 95, 0.5, 0.02, true_sigma, "put")
    iv = implied_volatility(price, 100, 95, 0.5, 0.02, "put")
    assert iv == pytest.approx(true_sigma, abs=1e-4)


def test_implied_volatility_below_intrinsic_is_nan() -> None:
    iv = implied_volatility(0.01, 120, 100, 0.5, 0.0, "call")
    assert math.isnan(iv)


def test_implied_volatility_zero_time_nan() -> None:
    assert math.isnan(implied_volatility(5.0, 100, 100, 0.0, 0.0, "call"))


def test_high_vol_iv_recovered_by_bisection() -> None:
    # Very high vol can push Newton out of range; bisection should still recover it.
    price = bs_price(100, 100, 1.0, 0.0, 3.0, "call")
    iv = implied_volatility(price, 100, 100, 1.0, 0.0, "call")
    assert iv == pytest.approx(3.0, abs=1e-3)
