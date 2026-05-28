"""Deterministic synthetic data source used by all unit tests.

Generates a reproducible geometric-Brownian-motion price path per symbol and
Black-Scholes option chains with a mild volatility **skew** (lower strikes carry
higher IV) and **term structure** (longer-dated IV is slightly higher). Because
it is seeded, the same ``(seed, symbol)`` always yields the same data, so CI runs
with no network access or credentials.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pandas as pd

from backtesting_engine.core.types import OptionRight
from backtesting_engine.pricing import as_option_type, bs_price, greeks


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
    dtes:
        Days-to-expiry offsets that define the available expiries in a chain.
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
        dtes: tuple[int, ...] = (30, 60, 90, 120, 150, 180),
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
        self.dtes = dtes
        self.strike_pct_range = strike_pct_range
        self.n_strikes = n_strikes
        self.r = r
        self.q = q
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
    def _iv(self, spot: float, strike: float, t: float) -> float:
        log_m = math.log(strike / spot)
        iv = (
            self.base_iv
            + self.skew * (-log_m)
            + self.term_slope * (math.sqrt(max(t, 0.0)) - math.sqrt(30 / 365))
        )
        return float(min(max(iv, 0.05), 2.0))

    def get_chain(self, symbol: str, asof: date) -> pd.DataFrame:
        """Return a synthetic Black-Scholes option chain for ``symbol`` at ``asof``."""
        spot = self._price_on(symbol, asof)
        step = max(1.0, round(spot * self.strike_pct_range / self.n_strikes))
        atm = round(spot / step) * step
        strikes = [atm + step * i for i in range(-self.n_strikes, self.n_strikes + 1)]
        strikes = [s for s in strikes if s > 0]

        rows: list[dict[str, object]] = []
        for dte in self.dtes:
            expiry = asof + timedelta(days=dte)
            t = dte / 365.0
            for strike in strikes:
                iv = self._iv(spot, strike, t)
                for right in (OptionRight.CALL, OptionRight.PUT):
                    g = greeks(spot, strike, t, self.r, iv, as_option_type(right.value), self.q)
                    mark = max(g.price, 0.0)
                    half = max(0.02, round(mark * 0.02, 2))
                    moneyness = abs(strike - spot) / spot
                    liquidity = max(1, int(2000 * math.exp(-12 * moneyness)))
                    rows.append(
                        {
                            "expiry": expiry,
                            "strike": float(strike),
                            "type": right.value,
                            "bid": round(max(mark - half, 0.0), 2),
                            "ask": round(mark + half, 2),
                            "last": round(mark, 2),
                            "mark": round(mark, 2),
                            "volume": float(liquidity),
                            "oi": float(liquidity * 5),
                            "iv": round(iv, 4),
                            "delta": g.delta,
                            "gamma": g.gamma,
                            "theta": g.theta,
                            "vega": g.vega,
                            "rho": g.rho,
                            "underlying_price": round(spot, 4),
                        }
                    )
        return pd.DataFrame(rows)

    # -- convenience for strategies --------------------------------------
    @staticmethod
    def bs_mark(
        spot: float, strike: float, t: float, iv: float, right: str, r: float = 0.0
    ) -> float:
        """Expose a Black-Scholes mark for callers that want a quick price."""
        return bs_price(spot, strike, t, r, iv, as_option_type(right))
