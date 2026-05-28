"""Implied-volatility surface fitting via bilinear interpolation.

The surface is parameterized over **log-moneyness** ``ln(K/S)`` and
**time-to-expiry** (years). Fitting builds a piecewise-linear interpolant over the
observed (log-moneyness, t, IV) points (scipy ``LinearNDInterpolator``), with a
nearest-point fallback outside the convex hull so queries never return NaN.

A :class:`Surface` Protocol is defined so richer parameterizations (SVI, SABR)
can be dropped in later without changing callers.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

import numpy as np
import pandas as pd
from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator

# Standard moneyness buckets for the term-structure view (K/S).
DEFAULT_MONEYNESS_BUCKETS = (0.90, 1.00, 1.10)


@runtime_checkable
class Surface(Protocol):
    """A fitted volatility surface queryable at any (log-moneyness, t)."""

    def iv(self, log_moneyness: float, t: float) -> float:
        """Interpolated implied volatility at the given coordinates."""
        ...


class BilinearIVSurface:
    """A bilinear (piecewise-linear) IV surface over (log-moneyness, t)."""

    def __init__(
        self,
        points: np.ndarray,
        values: np.ndarray,
        *,
        spot: float,
        asof: date,
    ) -> None:
        self.spot = spot
        self.asof = asof
        self._points = points
        self._values = values
        self._linear = LinearNDInterpolator(points, values)
        self._nearest = NearestNDInterpolator(points, values)

    def iv(self, log_moneyness: float, t: float) -> float:
        """IV at ``(log_moneyness, t)``; falls back to nearest outside the hull."""
        val = self._linear(log_moneyness, t)
        result = float(np.asarray(val).ravel()[0])
        if np.isnan(result):
            result = float(np.asarray(self._nearest(log_moneyness, t)).ravel()[0])
        return result

    def iv_by_strike(self, strike: float, t: float) -> float:
        """Convenience: IV for an absolute ``strike`` (converts to log-moneyness)."""
        return self.iv(float(np.log(strike / self.spot)), t)

    def grid(
        self, n_money: int = 40, n_time: int = 20
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Return a dense ``(log_moneyness, t, iv)`` mesh for plotting."""
        lm = np.linspace(self._points[:, 0].min(), self._points[:, 0].max(), n_money)
        tt = np.linspace(self._points[:, 1].min(), self._points[:, 1].max(), n_time)
        mesh_lm, mesh_t = np.meshgrid(lm, tt)
        iv = self._linear(mesh_lm, mesh_t)
        nearest = self._nearest(mesh_lm, mesh_t)
        iv = np.where(np.isnan(iv), nearest, iv)
        return mesh_lm, mesh_t, iv


def _prepare(chain: pd.DataFrame, spot: float, asof: date, iv_col: str) -> pd.DataFrame:
    df = chain.copy()
    df = df[df[iv_col].notna() & (df[iv_col] > 0)]
    df["log_moneyness"] = np.log(df["strike"].astype(float) / spot)
    if "t" not in df.columns:
        expiry = pd.to_datetime(df["expiry"]).dt.date
        df["t"] = [(e - asof).days / 365.0 for e in expiry]
    df = df[df["t"] > 0]
    # Average call/put IV at duplicate (log_moneyness, t) coordinates.
    return df.groupby(["log_moneyness", "t"], as_index=False)[iv_col].mean()


def fit_surface(
    chain: pd.DataFrame, spot: float, asof: date, *, iv_col: str = "iv"
) -> BilinearIVSurface:
    """Fit a :class:`BilinearIVSurface` from an option ``chain``.

    Requires ``strike``, ``iv`` and either ``t`` (years) or ``expiry`` columns.
    """
    prepared = _prepare(chain, spot, asof, iv_col)
    if len(prepared) < 3:
        raise ValueError("need at least 3 valid IV points to fit a surface")
    points = prepared[["log_moneyness", "t"]].to_numpy()
    values = prepared[iv_col].to_numpy()
    return BilinearIVSurface(points, values, spot=spot, asof=asof)


def skew_curves(
    chain: pd.DataFrame, spot: float, asof: date, *, iv_col: str = "iv"
) -> dict[float, pd.DataFrame]:
    """Per-expiry skew curves: ``{t: DataFrame[log_moneyness, strike, iv]}``."""
    prepared = chain.copy()
    prepared = prepared[prepared[iv_col].notna() & (prepared[iv_col] > 0)]
    prepared["log_moneyness"] = np.log(prepared["strike"].astype(float) / spot)
    if "t" not in prepared.columns:
        expiry = pd.to_datetime(prepared["expiry"]).dt.date
        prepared["t"] = [(e - asof).days / 365.0 for e in expiry]

    curves: dict[float, pd.DataFrame] = {}
    for t_val, group in prepared.groupby("t"):
        agg = (
            group.groupby(["log_moneyness", "strike"], as_index=False)[iv_col]
            .mean()
            .sort_values("log_moneyness")
        )
        curves[float(t_val)] = agg.reset_index(drop=True)
    return curves


def term_structure(
    surface: BilinearIVSurface,
    *,
    buckets: tuple[float, ...] = DEFAULT_MONEYNESS_BUCKETS,
    n_time: int = 20,
) -> pd.DataFrame:
    """ATM/OTM term structure: IV vs t for each moneyness bucket (K/S).

    Returns a tidy DataFrame with columns ``t``, ``moneyness`` and ``iv``.
    """
    t_min = float(surface._points[:, 1].min())
    t_max = float(surface._points[:, 1].max())
    times = np.linspace(t_min, t_max, n_time)
    rows: list[dict[str, float]] = []
    for m in buckets:
        lm = float(np.log(m))
        for t in times:
            rows.append({"t": float(t), "moneyness": float(m), "iv": surface.iv(lm, float(t))})
    return pd.DataFrame(rows)
