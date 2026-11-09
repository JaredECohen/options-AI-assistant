"""Full-chain analytics (vectorized): model IV and greeks per contract, ATM IV,
25-delta skew, term structure and liquidity.

Conventions: see app/analytics/bsm.py. Expiry is the 16:00 America/New_York
close on the expiration date; T = calendar time / 365. The forward is
S*exp((r-q)T). Rates and dividend yields are inputs with a stated source;
defaults are configuration assumptions, labelled as such in every response.
Provider-supplied IV/greeks are kept separately with their source; model values
never overwrite them.
"""
from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import numpy as np

from app.analytics import bsm
from app.analytics.vol import Metric
from app.providers.options.base import OptionQuote

NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class AnalyticsConfig:
    risk_free_rate: float = 0.04
    dividend_yield: float = 0.0
    rate_source: str = "configured assumption (ANALYTICS_RISK_FREE_RATE), not a market curve"
    dividend_source: str = "configured assumption (0 unless supplied), not a dividend forecast"

    def conventions(self) -> dict:
        return {
            "model": bsm.MODEL,
            "exercise_note": "European approximation of American listed options; early exercise ignored.",
            "risk_free_rate": self.risk_free_rate,
            "risk_free_rate_source": self.rate_source,
            "dividend_yield": self.dividend_yield,
            "dividend_yield_source": self.dividend_source,
            "day_count": "ACT/365 calendar time to 16:00 America/New_York on expiration",
            "units": {"iv": "annualized decimal", "theta": "per calendar day per share",
                      "vega": "per 1 vol point per share", "rho": "per 1 percentage point per share"},
        }


def time_to_expiry(expiration: str, as_of: dt.datetime) -> float:
    exp_date = dt.date.fromisoformat(expiration)
    close = dt.datetime.combine(exp_date, dt.time(16, 0), tzinfo=NY)
    seconds = (close - as_of.astimezone(NY)).total_seconds()
    return max(seconds, 0.0) / (365.0 * 24 * 3600)


def enrich_chain(quotes: Sequence[OptionQuote], underlying: float, expiration: str, as_of: dt.datetime,
                 cfg: AnalyticsConfig) -> list[dict]:
    if not quotes:
        return []
    T = time_to_expiry(expiration, as_of)
    K = np.array([q.strike for q in quotes], dtype=float)
    is_call = np.array([q.option_type == "call" for q in quotes])
    bid = np.array([np.nan if q.bid is None else q.bid for q in quotes], dtype=float)
    ask = np.array([np.nan if q.ask is None else q.ask for q in quotes], dtype=float)
    two_sided = np.isfinite(bid) & np.isfinite(ask) & (ask > 0) & (bid >= 0)
    crossed = two_sided & (bid > ask)
    valid = two_sided & ~crossed
    mid = np.where(valid, (bid + ask) / 2.0, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        spread_pct = np.where(valid & (mid > 0), (ask - bid) / mid, np.nan)
    iv, reason = bsm.implied_vol(mid, underlying, K, T, cfg.risk_free_rate, cfg.dividend_yield, is_call)
    reason = np.where(crossed, "crossed_quote", np.where(~two_sided, "no_two_sided_quote", reason))
    g = bsm.greeks(underlying, K, T, cfg.risk_free_rate, cfg.dividend_yield, iv, is_call)
    out = []
    for i, q in enumerate(quotes):
        def num(a, digits=6, i=i):
            v = float(a[i])
            return None if not math.isfinite(v) else round(v, digits)

        out.append({
            "symbol": q.symbol, "option_type": q.option_type, "strike": q.strike, "expiration": expiration,
            "bid": q.bid, "ask": q.ask, "mid": num(mid, 4), "spread_pct": num(spread_pct, 4),
            "volume": q.volume, "open_interest": q.open_interest, "quote_time": q.quote_time,
            "multiplier": q.multiplier, "adjusted": q.adjusted,
            "iv_model": num(iv), "iv_model_unavailable_reason": reason[i] or None,
            "greeks_model": {k: num(v) for k, v in g.items()} if math.isfinite(float(iv[i])) else None,
            "iv_provider": q.implied_volatility, "iv_provider_source": q.iv_source,
            "greeks_provider": None if q.greeks is None else {
                "delta": q.greeks.delta, "gamma": q.greeks.gamma, "theta": q.greeks.theta, "vega": q.greeks.vega,
                "rho": q.greeks.rho, "source": q.greeks.source, "as_of": q.greeks.as_of},
        })
    return out


def forward_price(underlying: float, T: float, cfg: AnalyticsConfig) -> float:
    return underlying * math.exp((cfg.risk_free_rate - cfg.dividend_yield) * T)


def otm_iv_points(enriched: Sequence[dict], forward: float) -> list[tuple[float, float]]:
    """(strike, iv) using OTM options only: puts below the forward, calls at/above."""
    pts = []
    for c in enriched:
        if c["iv_model"] is None:
            continue
        if (c["option_type"] == "put" and c["strike"] < forward) or (c["option_type"] == "call" and c["strike"] >= forward):
            pts.append((c["strike"], c["iv_model"]))
    return sorted(pts)


def atm_iv(enriched: Sequence[dict], forward: float) -> Metric:
    pts = otm_iv_points(enriched, forward)
    if len(pts) < 2:
        return Metric(None, "insufficient_quotes")
    ks = np.log(np.array([p[0] for p in pts]) / forward)
    ivs = np.array([p[1] for p in pts])
    if ks.min() > 0 or ks.max() < 0:
        return Metric(None, "forward_outside_strikes")
    return Metric(float(np.interp(0.0, ks, ivs)))


def skew_25d(enriched: Sequence[dict]) -> Metric:
    """25-delta put IV minus 25-delta call IV (model delta, linear in delta)."""
    def interp_at(option_type: str, target: float) -> float | None:
        pts = sorted(
            (c["greeks_model"]["delta"], c["iv_model"]) for c in enriched
            if c["option_type"] == option_type and c["greeks_model"] and c["iv_model"] is not None
        )
        if len(pts) < 2:
            return None
        ds = np.array([p[0] for p in pts])
        if not (ds.min() <= target <= ds.max()):
            return None
        return float(np.interp(target, ds, np.array([p[1] for p in pts])))

    put, call = interp_at("put", -0.25), interp_at("call", 0.25)
    if put is None or call is None:
        return Metric(None, "delta_not_bracketed")
    return Metric(put - call)


def liquidity(enriched: Sequence[dict]) -> dict:
    spreads = [c["spread_pct"] for c in enriched if c["spread_pct"] is not None]
    vols = [c["volume"] for c in enriched if c["volume"] is not None]
    ois = [c["open_interest"] for c in enriched if c["open_interest"] is not None]
    return {
        "contracts": len(enriched),
        "two_sided": sum(1 for c in enriched if c["mid"] is not None),
        "median_spread_pct": round(float(np.median(spreads)), 4) if spreads else None,
        "total_volume": sum(vols) if vols else None,
        "total_open_interest": sum(ois) if ois else None,
        "volume_oi_available": bool(vols or ois),
    }
