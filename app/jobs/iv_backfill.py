"""IV-history backfill (IV rank / IV percentile need ~252 daily observations).

For each past trading day this rebuilds the SAME measure the daily ingestion
stores (`atm_cm30_total_variance`): OptionsCoach's own Black-Scholes IV from the
end-of-day bid/ask mid, ATM by forward, interpolated to 30 days in total
variance between the two expiries that bracket 30 days. Provider-supplied IVs
are never mixed in, so history written by different providers measures one
thing.

Cost bounds: an expiry is fetched only for the days on which it brackets the
30-day target, as one ranged request per expiry (split at the provider's
maximum range), with strikes limited to a band around spot. Idempotent through
iv_daily's natural key; a rerun writes only missing days. Synthetic data is
refused.

The provider must implement `HistoryProvider` (ThetaData does; Tradier has no
historical option quotes).
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Protocol
from zoneinfo import ZoneInfo

from app.analytics.chain import AnalyticsConfig, atm_iv, enrich_chain, forward_price, time_to_expiry
from app.analytics.snapshot import IV_METHOD
from app.analytics.vol import constant_maturity
from app.db.models import IVDaily
from app.db.session import Database
from app.jobs.ingest import SyntheticDataRefused, _batch, _finish, _insert_ignore
from app.providers.options.base import EodOption, OptionQuote

NY = ZoneInfo("America/New_York")
TARGET_DTE = 30.0
MIN_DTE, MAX_DTE = 7.0, 180.0
MAX_BACKFILL_DAYS = 400
STRIKE_BAND = 0.25  # strikes within +/-25% of the day's underlying close


class HistoryProvider(Protocol):
    name: str
    data_status: str
    max_history_range_days: int

    async def list_expirations_all(self, ticker: str) -> list[str]: ...

    async def get_eod_options(self, ticker: str, expiration: str, start: dt.date, end: dt.date) -> list[EodOption]: ...

    async def get_daily_bars(self, ticker: str, start: dt.date, end: dt.date) -> list[tuple[dt.date, float]]: ...


def close_time(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(16, 0), NY).astimezone(dt.UTC)


def bracketing_expiries(expirations: list[str], day: dt.date) -> list[str]:
    """The expiries just below and just above 30 days on `day` (the pair that
    constant-maturity interpolation needs), within the 7-180 day window."""
    now = close_time(day)
    eligible = sorted(((time_to_expiry(e, now) * 365, e) for e in expirations), key=lambda x: x[0])
    eligible = [(d, e) for d, e in eligible if MIN_DTE <= d <= MAX_DTE]
    below = [e for d, e in eligible if d <= TARGET_DTE]
    above = [e for d, e in eligible if d > TARGET_DTE]
    return ([below[-1]] if below else []) + ([above[0]] if above else [])


def trading_days(start: dt.date, end: dt.date) -> list[dt.date]:
    """Weekdays only; exchange holidays simply return no rows and are skipped."""
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def plan_requests(expirations: list[str], days: list[dt.date], max_range_days: int) -> dict[str, list[tuple[dt.date, dt.date]]]:
    """expiry -> list of (start, end) ranges covering exactly the days it is needed."""
    needed: dict[str, list[dt.date]] = defaultdict(list)
    for day in days:
        for exp in bracketing_expiries(expirations, day):
            needed[exp].append(day)
    plan: dict[str, list[tuple[dt.date, dt.date]]] = {}
    for exp, ds in needed.items():
        ds.sort()
        ranges, lo, prev = [], ds[0], ds[0]
        for d in ds[1:]:
            if (d - prev).days > 4 or (d - lo).days >= max_range_days:  # gap (holiday/weekend ok) or range cap
                ranges.append((lo, prev))
                lo = d
            prev = d
        ranges.append((lo, prev))
        plan[exp] = ranges
    return plan


def iv30_for_day(day: dt.date, rows_by_exp: dict[str, list[EodOption]], underlying: float, cfg: AnalyticsConfig):
    now = close_time(day)
    term = []
    for exp, rows in rows_by_exp.items():
        quotes = [OptionQuote(option_type=r.option_type, strike=r.strike, expiration=exp, bid=r.bid, ask=r.ask,
                              last=None, mark=None) for r in rows]
        enriched = enrich_chain(quotes, underlying, exp, now, cfg)
        T = time_to_expiry(exp, now)
        atm = atm_iv(enriched, forward_price(underlying, T, cfg))
        if atm.value is not None:
            term.append((T * 365, atm.value))
    return constant_maturity(term, TARGET_DTE)


async def backfill_iv_history(db: Database, provider: HistoryProvider, payload: dict, cfg: AnalyticsConfig,
                              should_continue=lambda: True) -> dict:
    if provider.data_status == "synthetic":
        raise SyntheticDataRefused("refusing to store synthetic IV history")
    ticker = str(payload["ticker"]).upper()
    end = dt.date.fromisoformat(payload.get("end") or (dt.datetime.now(NY).date() - dt.timedelta(days=1)).isoformat())
    span = min(int(payload.get("days", 365)), MAX_BACKFILL_DAYS)
    start = end - dt.timedelta(days=span)
    batch_id = _batch(db, provider.name, "iv_backfill", {"ticker": ticker, "start": start.isoformat(),
                                                         "end": end.isoformat()})
    bars = dict(await provider.get_daily_bars(ticker, start, end))  # underlying closes; may be empty
    expirations = await provider.list_expirations_all(ticker)
    days = trading_days(start, end)
    plan = plan_requests(expirations, days, max(1, provider.max_history_range_days))

    by_day: dict[dt.date, dict[str, list[EodOption]]] = defaultdict(lambda: defaultdict(list))
    spot_from_rows: dict[dt.date, float] = {}
    failed: list[str] = []
    for exp, ranges in sorted(plan.items()):
        for lo, hi in ranges:
            if not should_continue():
                failed.append("cancelled")
                break
            try:
                rows = await provider.get_eod_options(ticker, exp, lo, hi)
            except Exception as exc:  # noqa: BLE001 - recorded; other expiries continue
                failed.append(f"{exp}:{lo.isoformat()}:{exc.__class__.__name__}")
                continue
            for r in rows:
                by_day[r.date][exp].append(r)
                if r.underlying_price and r.underlying_price > 0:
                    spot_from_rows.setdefault(r.date, r.underlying_price)

    obs, skipped = [], defaultdict(int)
    for day in days:
        exps = by_day.get(day)
        if not exps:
            skipped["no_option_rows"] += 1
            continue
        spot = bars.get(day) or spot_from_rows.get(day)
        if not spot:
            skipped["no_underlying_price"] += 1
            continue
        lo_k, hi_k = spot * (1 - STRIKE_BAND), spot * (1 + STRIKE_BAND)
        band = {e: [r for r in rows if lo_k <= r.strike <= hi_k] for e, rows in exps.items()}
        iv30 = iv30_for_day(day, band, spot, cfg)
        if iv30.value is None:
            skipped[iv30.reason or "iv_unavailable"] += 1
            continue
        obs.append({"symbol": ticker, "date": day, "iv": iv30.value, "method": IV_METHOD,
                    "provider": provider.name, "batch_id": batch_id})
    with db.session() as s:
        written = _insert_ignore(s, IVDaily, obs, ["symbol", "date", "method", "provider"])
    status = "succeeded" if not failed else ("partial" if obs else "failed")
    _finish(db, batch_id, status, written, "; ".join(failed)[:2000])
    if status == "failed":
        raise RuntimeError("IV backfill produced no observations: " + "; ".join(failed[:5]))
    return {"batch_id": batch_id, "ticker": ticker, "start": start.isoformat(), "end": end.isoformat(),
            "observations": len(obs), "rows_written": written, "requests": sum(len(r) for r in plan.values()),
            "skipped_days": dict(skipped), "failed": failed}
