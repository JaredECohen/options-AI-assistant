"""Implied-volatility surface (P5): observed points, a simple fitted surface and
dislocations.

Method (deliberately simple, stated in every response):
- Observed: OTM model IVs per expiry against log-moneyness k = ln(K/F).
- Fit per expiry: least-squares quadratic in k when the expiry has >= 4 points
  (linear with 2-3 points; none with fewer). Evaluated only inside the
  expiry's observed k range; never extrapolated.
- Across expiries: linear interpolation in total variance w = iv^2 * T at fixed
  k between neighbouring fitted expiries; nothing outside the observed DTE range.
- Dislocation: |observed - fitted| > max(2 vol points, 3 * 1.4826 * MAD of that
  expiry's residuals). This flags quotes that deviate from the local smile; it
  is not an arbitrage or mispricing signal.
- The surface is NOT calibrated to be arbitrage-free (no calendar or butterfly
  constraints). SVI/SABR are out of scope until evidence justifies them.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

DISCLAIMER = ("Quadratic smile per expiry with total-variance interpolation across expiries. Not arbitrage-free; "
              "fitted values are smoothing, not prices. Dislocations flag deviations from the local fit, not mispricing.")
MIN_DISLOCATION = 0.02


@dataclass(frozen=True)
class ExpirySlice:
    expiration: str
    dte: float
    points: list[tuple[float, float, str, float]]  # (k, iv, option_type, strike)


def _fit(ks: np.ndarray, ivs: np.ndarray) -> np.ndarray | None:
    if ks.size >= 4:
        return np.polyfit(ks, ivs, 2)
    if ks.size >= 2:
        return np.concatenate([[0.0], np.polyfit(ks, ivs, 1)])
    return None


def build_surface(slices: Sequence[ExpirySlice], k_grid: Sequence[float] | None = None) -> dict:
    k_grid = list(k_grid or [round(x, 3) for x in np.linspace(-0.3, 0.3, 13)])
    observed, dislocations, fits = [], [], []
    for sl in sorted(slices, key=lambda s: s.dte):
        if not sl.points:
            continue
        ks = np.array([p[0] for p in sl.points])
        ivs = np.array([p[1] for p in sl.points])
        coeffs = _fit(ks, ivs)
        fitted = np.polyval(coeffs, ks) if coeffs is not None else np.full(ks.shape, np.nan)
        resid = ivs - fitted
        finite = resid[np.isfinite(resid)]
        mad = float(np.median(np.abs(finite - np.median(finite)))) if finite.size else 0.0
        threshold = max(MIN_DISLOCATION, 3 * 1.4826 * mad)
        for (k, iv, typ, strike), f, r in zip(sl.points, fitted, resid, strict=True):
            row = {"expiration": sl.expiration, "dte": round(sl.dte, 2), "strike": strike, "option_type": typ,
                   "k": round(k, 5), "iv_observed": round(iv, 5),
                   "iv_fitted": None if not math.isfinite(f) else round(float(f), 5),
                   "residual": None if not math.isfinite(r) else round(float(r), 5)}
            observed.append(row)
            if math.isfinite(r) and abs(r) > threshold and ks.size >= 4:
                dislocations.append({**row, "threshold": round(threshold, 5),
                                     "direction": "rich_vs_fit" if r > 0 else "cheap_vs_fit"})
        if coeffs is not None:
            fits.append((sl.dte, sl.expiration, coeffs, float(ks.min()), float(ks.max())))

    grid = []
    for dte, exp, coeffs, kmin, kmax in fits:
        grid.append({"expiration": exp, "dte": round(dte, 2), "kind": "fitted_at_expiry",
                     "iv": [round(float(np.polyval(coeffs, k)), 5) if kmin <= k <= kmax else None for k in k_grid]})
    interpolated = []
    for (d1, e1, c1, lo1, hi1), (d2, e2, c2, lo2, hi2) in zip(fits, fits[1:], strict=False):
        dm = (d1 + d2) / 2
        t1, t2, tm = d1 / 365, d2 / 365, dm / 365
        row = []
        for k in k_grid:
            if not (max(lo1, lo2) <= k <= min(hi1, hi2)):
                row.append(None)
                continue
            w1 = float(np.polyval(c1, k)) ** 2 * t1
            w2 = float(np.polyval(c2, k)) ** 2 * t2
            wm = w1 + (w2 - w1) * (tm - t1) / (t2 - t1)
            row.append(round(math.sqrt(wm / tm), 5) if wm > 0 else None)
        interpolated.append({"between": [e1, e2], "dte": round(dm, 2), "kind": "interpolated_total_variance", "iv": row})
    return {"k_grid": k_grid, "observed": observed, "fitted": grid, "interpolated": interpolated,
            "dislocations": dislocations, "method": DISCLAIMER}
