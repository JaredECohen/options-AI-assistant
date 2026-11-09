"""P4 analytics validated against independent references, not against itself."""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pytest

from app.analytics import bsm, vol


def test_hull_example_prices():
    # Hull, Options Futures & Other Derivatives: S=42 K=40 r=10% sigma=20% T=0.5 -> c=4.76, p=0.81
    c = bsm.price(42, 40, 0.5, 0.10, 0.0, 0.20, True)
    p = bsm.price(42, 40, 0.5, 0.10, 0.0, 0.20, False)
    assert float(c) == pytest.approx(4.76, abs=0.005)
    assert float(p) == pytest.approx(0.81, abs=0.005)


def test_hull_greeks_example():
    # Hull greek examples: S=49 K=50 r=5% sigma=20% T=20/52 (non-dividend call)
    # delta 0.522, gamma 0.066, theta -4.31/yr, vega 12.1 per 1.00, rho 8.91 per 1.00
    g = bsm.greeks(49, 50, 20 / 52, 0.05, 0.0, 0.20, True)
    assert float(g["delta"]) == pytest.approx(0.522, abs=0.001)
    assert float(g["gamma"]) == pytest.approx(0.066, abs=0.001)
    assert float(g["theta"]) * 365 == pytest.approx(-4.31, abs=0.01)
    assert float(g["vega"]) * 100 == pytest.approx(12.1, abs=0.05)
    assert float(g["rho"]) * 100 == pytest.approx(8.91, abs=0.01)
    assert float(bsm.price(49, 50, 20 / 52, 0.05, 0.0, 0.20, True)) == pytest.approx(2.40, abs=0.005)


def test_put_call_parity_with_dividends_vectorized():
    S = np.linspace(50, 150, 41)
    K, T, r, q, s = 100.0, 0.75, 0.03, 0.02, 0.35
    c = bsm.price(S, K, T, r, q, s, True)
    p = bsm.price(S, K, T, r, q, s, False)
    np.testing.assert_allclose(c - p, S * math.exp(-q * T) - K * math.exp(-r * T), atol=1e-10)


def test_implied_vol_round_trip_over_a_grid():
    K = np.array([60, 80, 100, 120, 140] * 2, dtype=float)
    is_call = np.array([True] * 5 + [False] * 5)
    sig = np.array([0.15, 0.25, 0.35, 0.55, 0.9] * 2)
    px = bsm.price(100, K, 0.4, 0.04, 0.01, sig, is_call)
    iv, reason = bsm.implied_vol(px, 100, K, 0.4, 0.04, 0.01, is_call)
    # K=60 at 15% vol (deep ITM call / far OTM put) has ~2e-8 of time value: not identifiable.
    assert list(reason) == ["insufficient_time_value"] + [""] * 4 + ["insufficient_time_value"] + [""] * 4
    ok = reason == ""
    np.testing.assert_allclose(iv[ok], sig[ok], atol=1e-6)
    assert np.isnan(iv[~ok]).all()


def test_implied_vol_rejects_prices_outside_bounds():
    iv, reason = bsm.implied_vol([0.5, 150.0, -1.0, 5.0], 100, [80, 100, 100, 100], [0.5, 0.5, 0.5, 0.0], 0.0, 0.0,
                                 [True, True, True, True])
    assert list(reason) == ["below_intrinsic", "above_upper_bound", "invalid_input", "invalid_input"]
    assert np.isnan(iv).all()


def test_invalid_inputs_are_nan_not_numbers():
    assert np.isnan(bsm.price(100, 100, 0.0, 0.0, 0.0, 0.2, True))
    assert np.isnan(bsm.greeks(100, 100, 0.5, 0.0, 0.0, 0.0, True)["delta"])


def test_historical_vol_known_series():
    # constant 1% daily log return -> zero dispersion; alternating +/-r -> known std
    closes = [100 * math.exp(0.01 * i) for i in range(30)]
    assert vol.historical_vol(closes, 20).value == pytest.approx(0.0, abs=1e-12)
    r = 0.01
    alt = [100.0]
    for i in range(25):
        alt.append(alt[-1] * math.exp(r if i % 2 == 0 else -r))
    hv = vol.historical_vol(alt, 20).value
    rets = np.diff(np.log(alt[-21:]))
    assert hv == pytest.approx(float(np.std(rets, ddof=1)) * math.sqrt(252))


def test_historical_vol_insufficient_and_invalid():
    assert vol.historical_vol([100.0] * 10, 10).reason == "insufficient_history"
    assert vol.historical_vol([100.0] * 10 + [0.0], 10).reason == "invalid_prices"
    assert set(vol.hv_set([100.0] * 300)) == {"hv10", "hv20", "hv60", "hv252"}


def test_iv_rank_and_percentile_definitions():
    hist = [0.20] * 100 + [0.10] * 100 + [0.40] * 51 + [0.25]
    assert vol.iv_rank(hist).value == pytest.approx(50.0)  # (0.25-0.10)/(0.40-0.10)
    # prior 251 obs: 100 at .20 and 100 at .10 are below .25 -> 200/251
    assert vol.iv_percentile(hist).value == pytest.approx(100 * 200 / 251)
    assert vol.iv_rank([0.2] * 100).reason == "insufficient_history"
    assert vol.iv_rank([0.2] * 252).reason == "flat_history"


def test_no_lookahead_slicing():
    dates = [dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(10)]
    values = list(range(10))
    d, v = vol.upto(dates, values, dt.date(2026, 1, 5))
    assert d[-1] == dt.date(2026, 1, 5) and v == [0, 1, 2, 3, 4]


def test_constant_maturity_total_variance_interpolation():
    m = vol.constant_maturity([(20, 0.30), (40, 0.20)], 30)
    t1, t2, t = 20 / 365, 40 / 365, 30 / 365
    expected = math.sqrt((0.09 * t1 + (0.04 * t2 - 0.09 * t1) * (t - t1) / (t2 - t1)) / t)
    assert m.value == pytest.approx(expected)
    assert vol.constant_maturity([(20, 0.3), (40, 0.2)], 60).reason == "target_outside_expiries"
    assert vol.constant_maturity([(30, 0.25)], 30).value == 0.25
