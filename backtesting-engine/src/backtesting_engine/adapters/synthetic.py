"""Deterministic synthetic data source used by all unit tests.

Generates a reproducible geometric-Brownian-motion price path per symbol and
Black-Scholes option chains with a mild volatility **skew** (lower strikes carry
higher IV) and **term structure** (longer-dated IV is slightly higher). Because
it is seeded, the same ``(seed, symbol)`` always yields the same data, so CI runs
with no network access or credentials.
"""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd
from scipy.stats import norm

from backtesting_engine.core.types import OptionRight
from backtesting_engine.pricing import as_option_type, bs_price


class SyntheticOptionsAdapter:
    """A :class:`DataSource` producing deterministic equity bars and option chains.

    Parameters
    ----------
    seed:
        Master RNG seed; combined with the symbol for per-symbol determinism.
    start_price:
        Starting underlying price for generated paths.
    annual_drift, annual_vol:
        Drift and volatility of the GBM price process.
    base_iv, skew, term_slope:
        Volatility-surface shape parameters (see module docstring).
    n_expiries:
        Number of monthly (third-Friday) expiries listed in each chain. Expiries
        are **fixed calendar dates**, so an option listed today is still listed on
        later days — which is what makes position management / early exits work.
    strike_pct_range, n_strikes:
        Strikes span ``start*(1 ± range)`` with ``n_strikes`` per side.
    """

    name = "synthetic"

    def __init__(
        self,
        *,
        seed: int = 42,
        start_price: float = 100.0,
        annual_drift: float = 0.05,
        annual_vol: float = 0.20,
        base_iv: float = 0.20,
        skew: float = 0.15,
        term_slope: float = 0.03,
        n_expiries: int = 8,
        strike_pct_range: float = 0.30,
        n_strikes: int = 12,
        r: float = 0.0,
        q: float = 0.0,
    ) -> None:
        self.seed = seed
        self.start_price = start_price
        self.annual_drift = annual_drift
        self.annual_vol = annual_vol
        self.base_iv = base_iv
        self.skew = skew
        self.term_slope = term_slope
        self.n_expiries = n_expiries
        self.strike_pct_range = strike_pct_range
        self.n_strikes = n_strikes
        self.r = r
        self.q = q
        # A FIXED strike ladder (multiples of this step) so a listed strike stays
        # listed as spot drifts — required for closing/marking open positions.
        self.strike_step = max(1.0, round(start_price * strike_pct_range / n_strikes))
        self._paths: dict[str, pd.DataFrame] = {}

    # -- price path -------------------------------------------------------
    def _symbol_seed(self, symbol: str) -> int:
        return (self.seed * 1_000_003 + sum(ord(c) for c in symbol)) % (2**32)

    def _full_path(self, symbol: str) -> pd.DataFrame:
        """Generate (and memoize) a long daily GBM path for ``symbol``.

        The path is generated once over a fixed anchor window so that the price on
        any given calendar date is stable regardless of the requested slice.
        """
        if symbol in self._paths:
            return self._paths[symbol]
        anchor = pd.Timestamp("2015-01-01")
        days = pd.bdate_range(anchor, anchor + pd.Timedelta(days=365 * 12))
        rng = np.random.default_rng(self._symbol_seed(symbol))
        dt = 1.0 / 252.0
        shocks = rng.normal(0.0, 1.0, size=len(days))
        log_returns = (
            self.annual_drift - 0.5 * self.annual_vol**2
        ) * dt + self.annual_vol * math.sqrt(dt) * shocks
        closes = self.start_price * np.exp(np.cumsum(log_returns))
        intraday = rng.uniform(0.002, 0.012, size=len(days))
        highs = closes * (1.0 + intraday)
        lows = closes * (1.0 - intraday)
        opens = np.concatenate([[self.start_price], closes[:-1]])
        volume = rng.integers(1_000_000, 5_000_000, size=len(days)).astype("float64")
        df = pd.DataFrame(
            {
                "open": opens,
                "high": np.maximum.reduce([opens, highs, closes]),
                "low": np.minimum.reduce([opens, lows, closes]),
                "close": closes,
                "volume": volume,
            },
            index=days,
        )
        df.index.name = "date"
        self._paths[symbol] = df
        return df

    def get_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """Return synthetic daily OHLCV bars for ``symbol`` in ``[start, end]``."""
        path = self._full_path(symbol)
        mask = (path.index >= pd.Timestamp(start)) & (path.index <= pd.Timestamp(end))
        return path.loc[mask].copy()

    def _price_on(self, symbol: str, asof: date) -> float:
        path = self._full_path(symbol)
        upto = path.loc[path.index <= pd.Timestamp(asof)]
        if upto.empty:
            return float(path["close"].iloc[0])
        return float(upto["close"].iloc[-1])

    # -- option chain -----------------------------------------------------
    @staticmethod
    def _third_friday(year: int, month: int) -> date:
        """Return the third Friday of ``year``/``month`` (the standard expiry)."""
        d = date(year, month, 1)
        # weekday(): Monday=0 .. Friday=4. Days to the first Friday, then +14.
        first_friday = 1 + ((4 - d.weekday()) % 7)
        return date(year, month, first_friday + 14)

    def _expiries(self, asof: date) -> list[date]:
        """The next ``n_expiries`` monthly third-Friday expiries strictly after ``asof``."""
        expiries: list[date] = []
        year, month = asof.year, asof.month
        while len(expiries) < self.n_expiries:
            exp = self._third_friday(year, month)
            if exp > asof:
                expiries.append(exp)
            month += 1
            if month > 12:
                month, year = 1, year + 1
        return expiries

    def _expiry_frame(self, spot: float, expiry: date, t: float) -> pd.DataFrame:
        """Vectorized Black-Scholes chain for a single expiry (calls + puts).

        Uses numpy + vectorized ``scipy.stats.norm`` so a full chain is generated
        with a handful of array ops instead of thousands of scalar greek calls.
        Greek conventions match :func:`backtesting_engine.pricing.greeks`
        (theta per day, vega per vol point, rho per rate point).
        """
        # Fixed ladder: multiples of ``strike_step`` within ±range of spot.
        step = self.strike_step
        lo = math.ceil(spot * (1.0 - self.strike_pct_range) / step) * step
        hi = math.floor(spot * (1.0 + self.strike_pct_range) / step) * step
        k = np.arange(lo, hi + step, step, dtype="float64")
        k = k[k > 0]

        log_m = np.log(k / spot)
        iv = np.clip(
            self.base_iv
            + self.skew * (-log_m)
            + self.term_slope * (math.sqrt(t) - math.sqrt(30 / 365)),
            0.05,
            2.0,
        )
        r, q = self.r, self.q
        sqrt_t = math.sqrt(t)
        vol_sqrt_t = iv * sqrt_t
        d1 = (np.log(spot / k) + (r - q + 0.5 * iv**2) * t) / vol_sqrt_t
        d2 = d1 - vol_sqrt_t
        disc_q, disc_r = math.exp(-q * t), math.exp(-r * t)
        pdf_d1 = norm.pdf(d1)

        gamma = disc_q * pdf_d1 / (spot * vol_sqrt_t)
        vega = spot * disc_q * pdf_d1 * sqrt_t / 100.0
        frames = []
        for right, n_d1, n_d2 in (
            (OptionRight.CALL, norm.cdf(d1), norm.cdf(d2)),
            (OptionRight.PUT, norm.cdf(-d1), norm.cdf(-d2)),
        ):
            if right is OptionRight.CALL:
                price = disc_q * spot * n_d1 - disc_r * k * n_d2
                delta = disc_q * n_d1
                theta = (
                    -(spot * disc_q * pdf_d1 * iv) / (2 * sqrt_t)
                    - r * k * disc_r * norm.cdf(d2)
                    + q * spot * disc_q * norm.cdf(d1)
                ) / 365.0
                rho = k * t * disc_r * norm.cdf(d2) / 100.0
            else:
                price = disc_r * k * n_d2 - disc_q * spot * n_d1
                delta = -disc_q * n_d1
                theta = (
                    -(spot * disc_q * pdf_d1 * iv) / (2 * sqrt_t)
                    + r * k * disc_r * norm.cdf(-d2)
                    - q * spot * disc_q * norm.cdf(-d1)
                ) / 365.0
                rho = -k * t * disc_r * norm.cdf(-d2) / 100.0
            mark = np.maximum(price, 0.0)
            half = np.maximum(0.02, np.round(mark * 0.02, 2))
            liquidity = np.maximum(1, (2000 * np.exp(-12 * np.abs(k - spot) / spot)).astype(int))
            frames.append(
                pd.DataFrame(
                    {
                        "expiry": expiry,
                        "strike": k,
                        "type": right.value,
                        "bid": np.round(np.maximum(mark - half, 0.0), 2),
                        "ask": np.round(mark + half, 2),
                        "last": np.round(mark, 2),
                        "mark": np.round(mark, 2),
                        "volume": liquidity.astype("float64"),
                        "oi": (liquidity * 5).astype("float64"),
                        "iv": np.round(iv, 4),
                        "delta": delta,
                        "gamma": gamma,
                        "theta": theta,
                        "vega": vega,
                        "rho": rho,
                        "underlying_price": round(spot, 4),
                    }
                )
            )
        return pd.concat(frames, ignore_index=True)

    def get_chain(self, symbol: str, asof: date) -> pd.DataFrame:
        """Return a synthetic Black-Scholes option chain for ``symbol`` at ``asof``."""
        spot = self._price_on(symbol, asof)
        frames = [
            self._expiry_frame(spot, expiry, (expiry - asof).days / 365.0)
            for expiry in self._expiries(asof)
        ]
        return pd.concat(frames, ignore_index=True)

    # -- convenience for strategies --------------------------------------
    @staticmethod
    def bs_mark(
        spot: float, strike: float, t: float, iv: float, right: str, r: float = 0.0
    ) -> float:
        """Expose a Black-Scholes mark for callers that want a quick price."""
        return bs_price(spot, strike, t, r, iv, as_option_type(right))
