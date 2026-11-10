"""Per-ticker analytics snapshot from the configured provider (and stored IV
history), shared by the screener, comparison and analytics routes.

Bounded on purpose: at most MAX_EXPIRIES expiries between 7 and 180 days are
fetched per ticker, and results are cached for CACHE_SECONDS per (provider,
ticker). Market data is not user-private, so a shared cache does not cross
ownership boundaries. IV rank/percentile read ONLY stored iv_daily history (no
lookahead, no synthetic backfill); until ingestion has accumulated enough
observations they are unavailable with reason insufficient_history.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import math
import re
import time
from collections import OrderedDict
from dataclasses import dataclass

from sqlalchemy import select

from app.analytics import vol
from app.analytics.chain import (
    AnalyticsConfig,
    atm_iv,
    enrich_chain,
    forward_price,
    liquidity,
    skew_25d,
    time_to_expiry,
)
from app.analytics.vol import Metric
from app.db.models import IVDaily
from app.db.session import Database
from app.providers.options.base import ProviderUnavailable, QuoteUnavailable

MAX_EXPIRIES = 3
MIN_DTE, MAX_DTE = 7, 180
CACHE_SECONDS = 60
MAX_CACHED = 256  # bounded: an LRU over (provider, ticker)
TICKER_RE = re.compile(r"^[A-Z.]{1,10}$")
IV_METHOD = "atm_cm30_total_variance"


@dataclass
class Snapshot:
    ticker: str
    as_of: dt.datetime
    underlying: float
    data_status: str
    provider: str
    term: list[dict]
    nearest: dict | None
    hv: dict[str, Metric]
    iv_30d: Metric
    iv_rank: Metric
    iv_percentile: Metric
    skew: Metric
    liquidity: dict

    def screener_row(self) -> dict:
        def v(m: Metric):
            return m.value

        iv_hv = vol.ratio(self.iv_30d, self.hv["hv20"])
        reasons = {name: m.reason for name, m in {
            "iv_30d": self.iv_30d, "hv20": self.hv["hv20"], "hv60": self.hv["hv60"], "iv_hv_ratio": iv_hv,
            "iv_rank": self.iv_rank, "iv_percentile": self.iv_percentile, "skew_25d": self.skew}.items() if m.value is None}
        return {
            "iv_30d": v(self.iv_30d), "hv20": v(self.hv["hv20"]), "hv60": v(self.hv["hv60"]), "iv_hv_ratio": v(iv_hv),
            "iv_rank": v(self.iv_rank), "iv_percentile": v(self.iv_percentile), "skew_25d": v(self.skew),
            "median_spread_pct": self.liquidity.get("median_spread_pct"),
            "total_volume": self.liquidity.get("total_volume"),
            "total_open_interest": self.liquidity.get("total_open_interest"),
            "underlying_price": self.underlying,
            "_reasons": reasons,
            "_meta": {"as_of": self.as_of.isoformat(), "data_status": self.data_status, "provider": self.provider},
        }

    def as_json(self) -> dict:
        return {
            "ticker": self.ticker, "as_of": self.as_of.isoformat(), "underlying_price": self.underlying,
            "data_status": self.data_status, "provider": self.provider,
            "term_structure": self.term, "hv": {k: m.as_json() for k, m in self.hv.items()},
            "iv_30d": self.iv_30d.as_json(), "iv_rank": self.iv_rank.as_json(2),
            "iv_percentile": self.iv_percentile.as_json(2), "skew_25d": self.skew.as_json(),
            "iv_hv_ratio": vol.ratio(self.iv_30d, self.hv["hv20"]).as_json(), "liquidity": self.liquidity,
            "definitions": {
                "iv_rank": "(IV_t - min)/(max - min) over the last 252 stored daily 30-day ATM IV observations",
                "iv_percentile": "share of the prior 251 observations strictly below IV_t",
                "hv": "sample std of daily log returns x sqrt(252)",
                "skew_25d": "25-delta put IV minus 25-delta call IV, nearest expiry >= 20 days",
            },
        }


def select_expiries(eligible: list[tuple[str, float]], target: float = 30.0, limit: int = MAX_EXPIRIES) -> list[tuple[str, float]]:
    """Spend the per-ticker fetch budget where it matters: the two expiries that
    bracket the 30-day target (needed for constant-maturity IV without
    extrapolation), then the nearest expiry, then the next ones out."""
    eligible = sorted(eligible, key=lambda x: x[1])
    picks: list[tuple[str, float]] = []
    below = [x for x in eligible if x[1] <= target]
    above = [x for x in eligible if x[1] > target]
    for candidate in ([below[-1]] if below else []) + ([above[0]] if above else []) + eligible:
        if candidate not in picks:
            picks.append(candidate)
        if len(picks) >= limit:
            break
    return sorted(picks, key=lambda x: x[1])


def iv_history(db: Database | None, ticker: str, as_of: dt.date, n: int = 252) -> list[float]:
    if db is None:
        return []
    # One observation per date: the same day can be stored by more than one
    # provider (daily ingestion and a history backfill). The method is
    # identical (our own model IV), so the first row per date is kept,
    # ordered by provider name for determinism.
    with db.session() as s:
        rows = s.execute(
            select(IVDaily.date, IVDaily.iv).where(IVDaily.symbol == ticker, IVDaily.date <= as_of,
                                                   IVDaily.method == IV_METHOD)
            .order_by(IVDaily.date.desc(), IVDaily.provider).limit(n * 3)
        ).all()
    out: list[float] = []
    seen: set[dt.date] = set()
    for day, iv in rows:
        if day in seen:
            continue
        seen.add(day)
        out.append(iv)
        if len(out) >= n:
            break
    return list(reversed(out))


class SnapshotService:
    def __init__(self, provider, cfg: AnalyticsConfig, db: Database | None = None, clock=time.monotonic):
        self.provider = provider
        self.cfg = cfg
        self.db = db
        self.clock = clock
        self._cache: OrderedDict[tuple[str, str], tuple[float, Snapshot]] = OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}

    async def get(self, ticker: str, now: dt.datetime | None = None) -> Snapshot:
        ticker = ticker.upper()
        if not TICKER_RE.match(ticker):
            raise ValueError("invalid ticker")
        key = (self.provider.name, ticker)
        hit = self._cache.get(key)
        if hit and self.clock() - hit[0] < CACHE_SECONDS:
            return hit[1]
        lock = self._locks.setdefault(ticker, asyncio.Lock())
        async with lock:  # one fetch per ticker at a time: no duplicate provider calls
            hit = self._cache.get(key)
            if hit and self.clock() - hit[0] < CACHE_SECONDS:
                return hit[1]
            snap = await self._build(ticker, now or dt.datetime.now(dt.UTC))
            self._cache[key] = (self.clock(), snap)
            self._cache.move_to_end(key)
            while len(self._cache) > MAX_CACHED:
                old_key, _ = self._cache.popitem(last=False)
                self._locks.pop(old_key[1], None)
            return snap

    async def _build(self, ticker: str, now: dt.datetime) -> Snapshot:
        p = self.provider
        underlying = await p.get_underlying_price(ticker)
        expirations = await p.list_expirations(ticker)
        eligible = [(e, time_to_expiry(e, now) * 365) for e in expirations]
        eligible = [(e, d) for e, d in eligible if MIN_DTE <= d <= MAX_DTE]
        chosen = select_expiries(eligible)
        term, nearest, skew = [], None, Metric(None, "no_expiry_over_20_days")
        for exp, dte in chosen:
            try:
                quotes = await p.get_chain(ticker, exp)
            except (ProviderUnavailable, QuoteUnavailable):
                # One expiry the vendor will not price must not blank the whole page.
                term.append({"expiration": exp, "dte": round(dte, 2), "atm_iv": None,
                             "unavailable_reason": "chain_unavailable"})
                continue
            enriched = enrich_chain(quotes, underlying, exp, now, self.cfg)
            T = dte / 365
            atm = atm_iv(enriched, forward_price(underlying, T, self.cfg))
            term.append({"expiration": exp, "dte": round(dte, 2), "atm_iv": atm.value, "unavailable_reason": atm.reason or None})
            if nearest is None:
                nearest = {"expiration": exp, "liquidity": liquidity(enriched)}
            if skew.value is None and dte >= 20:
                skew = skew_25d(enriched)
        iv30 = vol.constant_maturity([(t["dte"], t["atm_iv"]) for t in term if t["atm_iv"]], 30)
        closes: list[float] = []
        if hasattr(p, "get_daily_closes"):
            try:
                closes = await p.get_daily_closes(ticker, now.date() - dt.timedelta(days=400), now.date())
            except Exception:  # noqa: BLE001 - HV becomes unavailable, with a reason
                closes = []
        hv = vol.hv_set(closes) if closes else {f"hv{w}": Metric(None, "no_price_history") for w in vol.HV_WINDOWS}
        history = iv_history(self.db, ticker, now.date())
        if iv30.value is not None and math.isfinite(iv30.value):
            history = history + [iv30.value]
        return Snapshot(
            ticker=ticker, as_of=now, underlying=underlying, data_status=p.data_status, provider=p.name, term=term,
            nearest=nearest, hv=hv, iv_30d=iv30, iv_rank=vol.iv_rank(history), iv_percentile=vol.iv_percentile(history),
            skew=skew, liquidity=(nearest or {}).get("liquidity", {}),
        )
