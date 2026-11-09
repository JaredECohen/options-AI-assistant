"""IV-history backfill: request planning, recovery of a known volatility, idempotency,
synthetic refusal, IV rank becoming available, and one observation per date."""
from __future__ import annotations

import datetime as dt
import uuid

import numpy as np
import pytest
from sqlalchemy import func, select

from app.analytics import bsm
from app.analytics.chain import AnalyticsConfig, time_to_expiry
from app.analytics.snapshot import IV_METHOD, iv_history
from app.analytics.vol import iv_rank
from app.db.models import Base, IngestBatch, IVDaily
from app.db.session import Database
from app.jobs.ingest import SyntheticDataRefused
from app.jobs.iv_backfill import (
    backfill_iv_history,
    bracketing_expiries,
    close_time,
    plan_requests,
    trading_days,
)
from app.providers.options.base import EodOption

CFG = AnalyticsConfig()


@pytest.fixture
def db(tmp_path):
    d = Database(f"sqlite:///{tmp_path}/ivb-{uuid.uuid4().hex}.db")
    Base.metadata.create_all(d.engine)
    return d


def fridays(start: dt.date, end: dt.date) -> list[str]:
    d = start + dt.timedelta(days=(4 - start.weekday()) % 7)
    out = []
    while d <= end:
        out.append(d.isoformat())
        d += dt.timedelta(days=7)
    return out


class FakeHistory:
    """Prices every contract with Black-Scholes at a volatility that is a known
    function of the date, so the backfill's answer can be checked exactly."""

    name = "thetadata"
    data_status = "eod"
    max_history_range_days = 30

    def __init__(self, vol_for_day, spot=100.0, holidays=(), underlying_in_rows=True, bars=True):
        self.vol_for_day = vol_for_day
        self.spot = spot
        self.holidays = set(holidays)
        self.underlying_in_rows = underlying_in_rows
        self.bars = bars
        self.requests: list[tuple[str, dt.date, dt.date]] = []

    async def list_expirations_all(self, ticker):
        return fridays(dt.date(2025, 1, 1), dt.date(2027, 1, 1))

    async def get_daily_bars(self, ticker, start, end):
        if not self.bars:
            return []
        return [(d, self.spot) for d in trading_days(start, end) if d not in self.holidays]

    async def get_eod_options(self, ticker, expiration, start, end):
        self.requests.append((expiration, start, end))
        rows = []
        for day in trading_days(start, end):
            if day in self.holidays:
                continue
            T = time_to_expiry(expiration, close_time(day))
            strikes = np.arange(60.0, 141.0, 5.0)
            for is_call in (True, False):
                px = bsm.price(self.spot, strikes, T, CFG.risk_free_rate, CFG.dividend_yield,
                               self.vol_for_day(day), is_call)
                for k, p in zip(strikes, np.atleast_1d(px), strict=True):
                    p = float(p)
                    rows.append(EodOption(day, expiration, float(k), "call" if is_call else "put",
                                          round(p * 0.995, 4), round(p * 1.005 + 0.01, 4),
                                          self.spot if self.underlying_in_rows else None))
        return rows


def test_bracketing_and_plan_cover_only_needed_days():
    exps = fridays(dt.date(2026, 1, 1), dt.date(2026, 12, 31))
    day = dt.date(2026, 3, 2)  # Monday
    pair = bracketing_expiries(exps, day)
    dtes = [(dt.date.fromisoformat(e) - day).days for e in pair]
    assert len(pair) == 2 and dtes[0] <= 30 < dtes[1] and dtes[1] - dtes[0] == 7
    days = trading_days(dt.date(2026, 3, 2), dt.date(2026, 6, 30))
    plan = plan_requests(exps, days, max_range_days=30)
    needed = {e for d in days for e in bracketing_expiries(exps, d)}
    assert set(plan) == needed  # nothing fetched that no day needs
    for ranges in plan.values():
        for lo, hi in ranges:
            assert (hi - lo).days < 30
    # each weekly expiry brackets 30 days for about two weeks, so roughly one request per expiry
    assert sum(len(r) for r in plan.values()) <= len(plan) + 2


async def test_backfill_recovers_known_vol_and_is_idempotent(db):
    vol = lambda d: 0.20 + 0.10 * ((d.toordinal() % 20) / 20)  # noqa: E731 - varies 0.20..0.295
    prov = FakeHistory(vol, holidays={dt.date(2026, 5, 25)})
    out = await backfill_iv_history(db, prov, {"ticker": "spy", "days": 60, "end": "2026-06-30"}, CFG)
    assert out["observations"] > 35 and out["failed"] == []
    assert out["skipped_days"].get("no_option_rows") == 1  # the holiday
    with db.session() as s:
        rows = s.execute(select(IVDaily.date, IVDaily.iv, IVDaily.provider, IVDaily.method)).all()
    assert all(p == "thetadata" and m == IV_METHOD for _d, _iv, p, m in rows)
    for d, iv, _p, _m in rows:
        assert iv == pytest.approx(vol(d), abs=0.005)  # mid of a +/-0.5% spread around the BSM price
    first_requests = len(prov.requests)
    again = await backfill_iv_history(db, prov, {"ticker": "SPY", "days": 60, "end": "2026-06-30"}, CFG)
    assert again["rows_written"] == 0 and len(prov.requests) == 2 * first_requests
    with db.session() as s:
        assert s.execute(select(func.count()).select_from(IngestBatch)).scalar() == 2


async def test_underlying_from_option_rows_when_no_stock_bars(db):
    prov = FakeHistory(lambda d: 0.3, bars=False, underlying_in_rows=True)
    out = await backfill_iv_history(db, prov, {"ticker": "QQQ", "days": 14, "end": "2026-06-30"}, CFG)
    assert out["observations"] >= 9


async def test_days_without_any_underlying_price_are_skipped_not_guessed(db):
    prov = FakeHistory(lambda d: 0.3, bars=False, underlying_in_rows=False)
    out = await backfill_iv_history(db, prov, {"ticker": "IWM", "days": 14, "end": "2026-06-30"}, CFG)
    assert out["observations"] == 0 and out["skipped_days"]["no_underlying_price"] >= 9


async def test_refuses_synthetic(db):
    prov = FakeHistory(lambda d: 0.3)
    prov.data_status = "synthetic"
    with pytest.raises(SyntheticDataRefused):
        await backfill_iv_history(db, prov, {"ticker": "SPY", "days": 10}, CFG)


async def test_iv_rank_available_after_backfill_and_one_row_per_date(db):
    prov = FakeHistory(lambda d: 0.15 + 0.2 * ((d.toordinal() % 50) / 50))
    await backfill_iv_history(db, prov, {"ticker": "SPY", "days": 380, "end": "2026-06-30"}, CFG)
    hist = iv_history(db, "SPY", dt.date(2026, 6, 30))
    assert len(hist) >= 252
    assert iv_rank(hist).value is not None
    # a second provider storing the same dates must not double-count them
    with db.session() as s:
        batch = s.execute(select(IngestBatch.id)).scalars().first()
        s.add_all([IVDaily(symbol="SPY", date=d, iv=0.99, method=IV_METHOD, provider="tradier", batch_id=batch)
                   for d in trading_days(dt.date(2026, 6, 1), dt.date(2026, 6, 30))])
    hist2 = iv_history(db, "SPY", dt.date(2026, 6, 30))
    assert len(hist2) == len(hist) and 0.99 not in hist2  # 'thetadata' sorts before 'tradier'
