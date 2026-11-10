"""Point-in-time history for backtests, with a durable cache (contract: docs/BACKTESTING.md).

Live ThetaData costs: a chain-day is ~4 s whatever its size, and one contract's
whole life is ~0.5 s. So the engine fetches a chain only on entry days, plus one
request per leg. Every response for a range that ends before today is stored in
provider_cache, which makes reruns and parameter sweeps nearly free.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from zoneinfo import ZoneInfo

from app.db.models import ProviderCache
from app.db.session import Database
from app.providers.options.base import EodOption

NY = ZoneInfo("America/New_York")
BAR_CHUNK_DAYS = 366
# Strikes either side of the money, in LISTED strikes. Near-term SPY strikes are $1
# apart, so 40 covered only ~6% and cut off 16-delta shorts and their wings. Live,
# a wider range costs about the same per chain-day.
CHAIN_STRIKE_RANGE = 200


def ny_today() -> dt.date:
    return dt.datetime.now(NY).date()


def _pack(rows: list[EodOption]) -> list[list]:
    return [[r.date.isoformat(), r.expiration, r.strike, r.option_type, r.bid, r.ask, r.underlying_price,
             r.delta, r.implied_vol] for r in rows]


def _unpack(rows: list[list]) -> list[EodOption]:
    return [EodOption(date=dt.date.fromisoformat(d), expiration=e, strike=k, option_type=t, bid=b, ask=a,
                      underlying_price=u, delta=de, implied_vol=iv) for d, e, k, t, b, a, u, de, iv in rows]


class CachedHistory:
    """Adapts a history provider (ThetaData) to the engine's HistorySource."""

    def __init__(self, provider, db: Database | None, ticker: str, today: dt.date | None = None):
        self.provider = provider
        self.db = db
        self.ticker = ticker.upper()
        self.today = today or ny_today()
        self.requests = 0
        self.cache_hits = 0
        self.data_source = (f"{getattr(provider, 'name', 'provider')} end-of-day "
                            "(closing NBBO; vendor greeks when available, else Black-Scholes from the mid)")
        self._expirations: list[str] | None = None

    # -- cache ----------------------------------------------------------------------
    def _key(self, kind: str, params: dict) -> str:
        raw = json.dumps([getattr(self.provider, "name", ""), kind, self.ticker, params], sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    def _get(self, key: str):
        if self.db is None:
            return None
        with self.db.session() as s:
            row = s.get(ProviderCache, key)
            return None if row is None else row.payload

    def _put(self, key: str, kind: str, payload: list) -> None:
        if self.db is None:
            return
        with self.db.session() as s:
            if s.get(ProviderCache, key) is None:
                s.add(ProviderCache(key=key, provider=getattr(self.provider, "name", ""), kind=kind, payload=payload))

    async def _cached(self, kind: str, params: dict, end: dt.date, fetch, pack, unpack):
        key = self._key(kind, params)
        hit = self._get(key)
        if hit is not None:
            self.cache_hits += 1
            return unpack(hit)
        self.requests += 1
        value = await fetch()
        if end < self.today:  # only settled history is immutable
            try:
                self._put(key, kind, pack(value))
            except Exception:  # noqa: BLE001 - a concurrent writer stored it first; the data is identical
                pass
        return value

    # -- HistorySource ----------------------------------------------------------------
    async def bars(self, start: dt.date, end: dt.date) -> list[tuple[dt.date, float]]:
        out: dict[dt.date, float] = {}
        lo = start
        while lo <= end:
            hi = min(lo + dt.timedelta(days=BAR_CHUNK_DAYS - 1), end)
            rows = await self._cached(
                "bars", {"start": lo.isoformat(), "end": hi.isoformat()}, hi,
                lambda lo=lo, hi=hi: self.provider.get_daily_bars(self.ticker, lo, hi),
                lambda v: [[d.isoformat(), c] for d, c in v],
                lambda v: [(dt.date.fromisoformat(d), c) for d, c in v])
            out.update(rows)
            lo = hi + dt.timedelta(days=1)
        return sorted(out.items())

    async def expirations(self) -> list[str]:
        if self._expirations is None:
            self.requests += 1
            self._expirations = await self.provider.list_expirations_all(self.ticker)
        return self._expirations

    async def chain(self, expiration: str, day: dt.date) -> list[EodOption]:
        return await self._cached(
            "chain", {"expiration": expiration, "day": day.isoformat(), "strike_range": CHAIN_STRIKE_RANGE}, day,
            lambda: self.provider.get_eod_chain(self.ticker, expiration, day, CHAIN_STRIKE_RANGE), _pack, _unpack)

    async def contract(self, expiration: str, strike: float, right: str, start: dt.date,
                       end: dt.date) -> list[EodOption]:
        if end < start:
            return []
        return await self._cached(
            "contract", {"expiration": expiration, "strike": strike, "right": right,
                         "start": start.isoformat(), "end": end.isoformat()}, end,
            lambda: self.provider.get_contract_eod(self.ticker, expiration, strike, right, start, end),
            _pack, _unpack)
