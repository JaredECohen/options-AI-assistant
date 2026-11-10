"""Backtest engine (P7): hand-checked trades on a Black-Scholes world, the pure
helpers, the data cache, the job path and the routes. No network."""
from __future__ import annotations

import datetime as dt
import uuid

import numpy as np
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.analytics import bsm
from app.analytics.chain import AnalyticsConfig, time_to_expiry
from app.backtest import engine as E
from app.backtest.data import CachedHistory
from app.backtest.spec import PRESETS, BacktestRequest, CostModel
from app.db.models import Base, Job
from app.db.session import Database
from app.jobs import queue
from app.jobs.iv_backfill import close_time
from app.providers.options.base import EodOption
from app.worker import Worker
from tests.helpers import auth, client

CFG = AnalyticsConfig()
JAN5 = dt.date(2026, 1, 5)  # a Monday


def weekdays(start: dt.date, end: dt.date, skip=()) -> list[dt.date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5 and d not in skip:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


class World:
    """Underlying path + Black-Scholes prices at a fixed vol, strikes every $1,
    Friday expirations, a 2% (+1c) bid/ask spread around the model price."""

    requests = 0
    cache_hits = 0
    data_source = "fake Black-Scholes world"

    def __init__(self, path: dict[dt.date, float], vol: float = 0.20, vendor_delta: bool = True,
                 weeklies_listed_days: int | None = None):
        self.path = path
        self.weeklies_listed_days = weeklies_listed_days
        self.vol = vol
        self.vendor_delta = vendor_delta
        self.chain_calls: list[tuple[str, dt.date]] = []

    def row(self, exp: str, k: float, right: str, day: dt.date) -> EodOption:
        s = self.path[day]
        T = time_to_expiry(exp, close_time(day))
        is_call = right == "call"
        p = float(bsm.price(s, k, T, CFG.risk_free_rate, 0.0, self.vol, is_call)) if T > 0 else (
            max(s - k, 0.0) if is_call else max(k - s, 0.0))
        bid, ask = round(max(p * 0.98 - 0.01, 0.0), 4), round(p * 1.02 + 0.01, 4)
        delta = float(bsm.greeks(s, k, T, CFG.risk_free_rate, 0.0, self.vol, is_call)["delta"]) if T > 0 else None
        return EodOption(date=day, expiration=exp, strike=k, option_type=right, bid=bid, ask=ask, underlying_price=s,
                         delta=delta if self.vendor_delta else None, implied_vol=self.vol if self.vendor_delta else None)

    async def bars(self, start, end):
        return [(d, self.path[d]) for d in sorted(self.path) if start <= d <= end]

    async def expirations(self):
        d = dt.date(2025, 12, 5)
        out = []
        while d < dt.date(2027, 1, 1):
            out.append(d.isoformat())
            d += dt.timedelta(days=7)
        return out

    async def chain(self, exp, day):
        self.chain_calls.append((exp, day))
        e = dt.date.fromisoformat(exp)
        if self.weeklies_listed_days is not None and (e - day).days > self.weeklies_listed_days \
                and not 15 <= e.day <= 21:
            return []  # not listed yet
        return [self.row(exp, float(k), r, day) for k in np.arange(60, 141) for r in ("call", "put")]

    async def contract(self, exp, k, right, start, end):
        e = dt.date.fromisoformat(exp)
        return [self.row(exp, k, right, d) for d in sorted(self.path) if start <= d <= end and d <= e]


def flat(price=100.0, start=JAN5, end=dt.date(2026, 3, 31), **moves) -> dict[dt.date, float]:
    path = {d: price for d in weekdays(start, end)}
    for d, p in moves.items():
        path[dt.date.fromisoformat(d)] = p
    return path


def req(**kw) -> BacktestRequest:
    base = {"underlying": "SPY", "start": "2026-01-05", "end": "2026-01-30", "strategy": "short_put",
            "entry": {"dte_target": 14, "dte_min": 10, "dte_max": 20, "frequency": "monthly", "max_open": 1},
            "exit": {"profit_target_pct": None, "stop_loss_pct": None, "exit_dte": None, "max_hold_days": None}}
    for k, v in kw.items():
        base[k] = {**base[k], **v} if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return BacktestRequest.model_validate(base)


# ---- hand-checked trades -------------------------------------------------------------

async def test_short_put_expires_worthless_keeps_the_bid_less_commission():
    world = World(flat())
    out = await E.run_backtest(req(), world)
    [t] = out["trades"]
    assert t["entry_date"] == "2026-01-05" and t["expiration"] == "2026-01-16"  # 11 DTE beats 18 for a 14 target
    leg = t["legs"][0]
    sold = world.row("2026-01-16", leg["strike"], "put", JAN5)
    assert leg["entry_price"] == pytest.approx(sold.bid)  # conservative: sell at the bid
    assert abs(abs(leg["entry_delta"]) - 0.30) <= 0.05
    assert t["exit_reason"] == "expired" and t["exit_value"] == 0
    assert t["pnl"] == pytest.approx(sold.bid * 100 - 0.65, abs=0.01)
    assert out["summary"]["final_equity"] == pytest.approx(100_000 + t["pnl"], abs=0.01)
    assert out["equity"][0]["benchmark"] == 100_000 and out["synthetic"] is False


async def test_assigned_put_settles_at_intrinsic_with_assignment_fee():
    path = flat()
    for d in weekdays(dt.date(2026, 1, 6), dt.date(2026, 3, 31)):
        path[d] = 90.0
    out = await E.run_backtest(req(costs={"assignment_fee": 5.0}), World(path))
    [t] = out["trades"]
    k, credit = t["legs"][0]["strike"], t["legs"][0]["entry_price"]
    assert t["exit_reason"] == "expired" and t["legs"][0]["exit_price"] == pytest.approx(k - 90)
    assert t["exit_value"] == pytest.approx(-(k - 90) * 100)
    assert t["pnl"] == pytest.approx(credit * 100 - (k - 90) * 100 - 0.65 - 5.0, abs=0.01)


async def test_profit_target_closes_at_the_ask_with_both_commissions():
    world = World(flat())
    out = await E.run_backtest(req(exit={"profit_target_pct": 50}), world)
    [t] = out["trades"]
    assert t["exit_reason"] == "profit_target" and t["exit_date"] < t["expiration"]
    leg = t["legs"][0]
    bought = world.row(t["expiration"], leg["strike"], "put", dt.date.fromisoformat(t["exit_date"]))
    assert leg["exit_price"] == pytest.approx(bought.ask)
    assert t["pnl"] == pytest.approx((leg["entry_price"] - bought.ask) * 100 - 2 * 0.65, abs=0.01)
    assert out["exit_reasons"] == {"profit_target": 1}


async def test_stop_loss_and_exit_dte():
    path = flat(**{"2026-01-07": 95.0, "2026-01-08": 95.0, "2026-01-09": 95.0})
    out = await E.run_backtest(req(exit={"stop_loss_pct": 100}), World(path))
    assert out["trades"][0]["exit_reason"] == "stop_loss" and out["trades"][0]["exit_date"] == "2026-01-07"
    out = await E.run_backtest(req(exit={"exit_dte": 7}), World(flat()))
    t = out["trades"][0]
    assert t["exit_reason"] == "exit_dte" and t["exit_date"] == "2026-01-09"  # 7 days before Jan 16


async def test_covered_call_called_away_earns_strike_minus_cost_plus_credit():
    path = flat()
    for d in weekdays(dt.date(2026, 1, 6), dt.date(2026, 3, 31)):
        path[d] = 110.0
    out = await E.run_backtest(req(strategy="covered_call"), World(path))
    [t] = out["trades"]
    k, credit = t["legs"][0]["strike"], t["legs"][0]["entry_price"]
    assert t["stock_pnl"] == pytest.approx((110 - 100) * 100)
    assert t["pnl"] == pytest.approx((k - 100) * 100 + credit * 100 - 0.65, abs=0.01)  # capped at the strike
    assert t["buying_power"] == pytest.approx(100 * 100)  # the shares


async def test_iron_condor_legs_follow_their_rules_and_risk_is_defined():
    out = await E.run_backtest(req(strategy="iron_condor"), World(flat()))
    t = out["trades"][0]
    sp, lp, sc, lc = (leg["strike"] for leg in t["legs"])
    assert lp == sp - 5 and lc == sc + 5 and sp < 100 < sc
    credit = t["entry_value"] / 100
    assert t["buying_power"] == pytest.approx((5 - credit) * 100, abs=0.01)


async def test_offset_wings_never_shrink_or_cross_when_strikes_are_missing():
    world = World(flat())
    real_chain = world.chain

    async def gappy(exp, day):  # no strikes 89-96: a $5 wing below the ~98 short is 4+ dollars off target
        return [r for r in await real_chain(exp, day) if not 89 <= r.strike <= 96]

    world.chain = gappy
    out = await E.run_backtest(req(strategy="bull_put_spread"), world)
    assert out["trades"] == [] and out["diagnostics"]["skipped_entries"]["strike_not_found"] == 1


async def test_risk_sizing_cash_secured_put_uses_strike_collateral():
    out = await E.run_backtest(req(strategy="cash_secured_put", sizing={"mode": "risk_pct", "risk_pct": 50}),
                               World(flat()))
    t = out["trades"][0]
    assert t["contracts"] == int(50_000 // (t["legs"][0]["strike"] * 100))
    assert t["buying_power"] == pytest.approx(t["legs"][0]["strike"] * 100 * t["contracts"])


async def test_filters_and_skips_are_counted_not_hidden():
    out = await E.run_backtest(req(entry={"min_iv": 0.5}), World(flat()))
    assert out["trades"] == [] and out["diagnostics"]["skipped_entries"]["iv_filter"] == 1
    out = await E.run_backtest(req(entry={"delta_tolerance": 0.0001}), World(flat()))
    assert out["diagnostics"]["skipped_entries"]["strike_not_found"] == 1
    out = await E.run_backtest(req(entry={"dte_min": 360, "dte_target": 360, "dte_max": 360}), World(flat()))  # a Thursday
    assert out["diagnostics"]["skipped_entries"]["no_expiration_in_window"] == 1
    out = await E.run_backtest(req(entry={"frequency": "daily", "max_open": 1}), World(flat()))
    assert out["diagnostics"]["skipped_entries"]["max_open"] > 0


async def test_black_scholes_delta_when_the_vendor_has_none():
    out = await E.run_backtest(req(), World(flat(), vendor_delta=False))
    assert abs(abs(out["trades"][0]["legs"][0]["entry_delta"]) - 0.30) <= 0.05


async def test_weekly_entries_prefetch_chains_and_overlap():
    world = World(flat())
    out = await E.run_backtest(req(end="2026-02-27", entry={"frequency": "weekly", "weekday": 0, "max_open": 3}),
                               world)
    assert len(out["trades"]) == 8 and {t["entry_date"][:7] for t in out["trades"]} == {"2026-01", "2026-02"}
    assert len(set(world.chain_calls)) == len(world.chain_calls)  # each chain fetched once
    assert 0 < out["summary"]["time_in_market"] <= 1 and out["monthly_returns"][0]["month"] == "2026-01"


async def test_unlisted_weeklies_fall_back_to_a_listed_monthly():
    world = World(flat(), weeklies_listed_days=30)
    out = await E.run_backtest(req(entry={"dte_target": 40, "dte_min": 25, "dte_max": 60}), world)
    assert out["trades"][0]["expiration"] == "2026-02-20"  # Feb 13 (39 DTE) unlisted; skip Feb 27, take the monthly
    assert world.chain_calls[:2] == [("2026-02-13", JAN5), ("2026-02-20", JAN5)]


async def test_cancellation_stops_the_run():
    with pytest.raises(E.Cancelled):
        await E.run_backtest(req(), World(flat()), should_continue=lambda: False)


async def test_unknown_underlying_has_no_history():
    with pytest.raises(ValueError, match="no price history"):
        await E.run_backtest(req(), World({}))


# ---- pure helpers -------------------------------------------------------------------

def test_fill_models():
    assert E.fill_price(2.0, 2.2, "sell", CostModel(fill="conservative")) == pytest.approx(2.0)
    assert E.fill_price(2.0, 2.2, "buy", CostModel(fill="conservative")) == pytest.approx(2.2)
    assert E.fill_price(2.0, 2.2, "buy", CostModel(fill="mid")) == pytest.approx(2.1)
    assert E.fill_price(2.0, 2.2, "buy", CostModel(fill="spread_fraction", spread_fraction=0.5)) == pytest.approx(2.15)
    assert E.fill_price(0.0, 0.02, "sell", CostModel(fill="conservative")) == 0.0
    assert not E.valid_quote(2.5, 2.0) and not E.valid_quote(0, 0) and E.valid_quote(0, 0.05)


def test_expiration_cycles_and_selection():
    good_friday_thursday = dt.date(2026, 4, 2)  # Good Friday 2026-04-03 is a holiday
    listed = {good_friday_thursday, dt.date(2026, 4, 17), dt.date(2026, 4, 16)}
    assert E.is_friday_cycle(good_friday_thursday, listed) and not E.is_friday_cycle(dt.date(2026, 4, 16), listed)
    assert E.is_monthly(dt.date(2026, 4, 17), listed) and not E.is_monthly(dt.date(2026, 4, 10), listed)
    dates = [dt.date(2026, 1, 9) + dt.timedelta(days=i) for i in range(0, 120)]  # daily expirations
    r = req(entry={"dte_target": 45, "dte_min": 30, "dte_max": 60, "expirations": "monthly"})
    got = E.candidate_expirations(dates, JAN5, r)
    assert got[0] == "2026-02-20" and all(dt.date.fromisoformat(x).weekday() == 4 for x in got)


def test_weekly_schedule_moves_past_a_holiday_monday():
    r = req(entry={"frequency": "weekly", "weekday": 0})
    state: dict = {}
    days = weekdays(dt.date(2026, 1, 12), dt.date(2026, 1, 30), skip={dt.date(2026, 1, 19)})  # MLK day
    hits = [d for i, d in enumerate(days) if E.scheduled(d, days[i - 1] if i else None, r, 0, state)]
    assert hits == [dt.date(2026, 1, 12), dt.date(2026, 1, 20), dt.date(2026, 1, 26)]


def test_risk_shapes():
    condor = [("put", "sell", 1, 95), ("put", "buy", 1, 90), ("call", "sell", 1, 105), ("call", "buy", 1, 110)]
    assert E.max_loss_per_unit(condor, 1.5, 0, 100) == pytest.approx(350)
    assert not E.naked_shorts(condor, 0)
    strangle = [("put", "sell", 1, 95), ("call", "sell", 1, 105)]
    assert E.naked_shorts(strangle, 0) and E.max_loss_per_unit(strangle, 2.0, 0, 100) is None
    # put side: 20% * 100 - 5 OTM = 15 (> 9.5 floor) + 1.2 premium; plus the call's 0.8 premium
    assert E.reg_t_requirement(strangle, [1.2, 0.8], 100) == pytest.approx((15 + 1.2 + 0.8) * 100)
    assert not E.naked_shorts([("call", "sell", 1, 105)], 1)  # covered by 100 shares


# ---- request validation -------------------------------------------------------------

def test_request_validation_and_presets():
    r = req(strategy="iron_condor")
    assert len(r.legs) == 4 and r.legs[1].strike.method == "offset" and r.underlying == "SPY"
    assert req(strategy="covered_call").stock.shares_per_contract == 100
    assert set(PRESETS) >= {"short_put", "covered_call", "iron_condor", "short_strangle", "long_straddle", "custom"}
    with pytest.raises(ValidationError, match="earlier leg"):
        req(strategy="custom", legs=[{"right": "put", "side": "buy", "strike": {"method": "offset", "value": -5,
                                                                              "from_leg": 0}}])
    with pytest.raises(ValidationError, match="needs at least one leg"):
        req(strategy="custom")
    with pytest.raises(ValidationError, match="end must be after start"):
        req(start="2026-02-01", end="2026-01-01")
    with pytest.raises(ValidationError, match="5 years"):
        req(start="2019-01-01", end="2026-01-01")
    with pytest.raises(ValidationError, match="before today"):
        req(end=dt.date.today().isoformat())
    with pytest.raises(ValidationError, match="dte_min"):
        req(entry={"dte_target": 5})
    with pytest.raises(ValidationError):
        req(extra_field=1)


# ---- data cache --------------------------------------------------------------------

class CountingProvider:
    name = "thetadata"

    def __init__(self, world: World):
        self.world = world
        self.calls = 0

    async def get_daily_bars(self, ticker, start, end):
        self.calls += 1
        return await self.world.bars(start, end)

    async def list_expirations_all(self, ticker):
        return await self.world.expirations()

    async def get_eod_chain(self, ticker, exp, day, strike_range=40):
        self.calls += 1
        return await self.world.chain(exp, day)

    async def get_contract_eod(self, ticker, exp, k, right, start, end):
        self.calls += 1
        return await self.world.contract(exp, k, right, start, end)


@pytest.fixture
def db(tmp_path):
    d = Database(f"sqlite:///{tmp_path}/bt-{uuid.uuid4().hex}.db")
    Base.metadata.create_all(d.engine)
    return d


async def test_history_cache_serves_reruns_and_never_stores_today(db):
    prov = CountingProvider(World(flat()))
    first = CachedHistory(prov, db, "spy", today=dt.date(2026, 11, 9))
    a = await first.chain("2026-01-16", JAN5)
    second = CachedHistory(prov, db, "SPY", today=dt.date(2026, 11, 9))
    b = await second.chain("2026-01-16", JAN5)
    assert a == b and prov.calls == 1 and second.cache_hits == 1 and second.requests == 0
    live = CachedHistory(prov, db, "SPY", today=JAN5)  # a range ending today may still change
    await live.chain("2026-01-23", JAN5)
    await live.chain("2026-01-23", JAN5)
    assert prov.calls == 3


async def test_worker_runs_a_backtest_job_end_to_end_and_reruns_from_cache(db):
    prov = CountingProvider(World(flat()))
    w = Worker(db, None, CFG, worker_id="w-bt", history_provider=prov)
    payload = req().model_dump(mode="json")
    queue.enqueue(db, "backtest", "bt-1", payload, user_id=None)
    assert await w.run_once()
    with db.session() as s:
        job = s.execute(select(Job).where(Job.idempotency_key == "bt-1")).scalar_one()
        assert job.status == "succeeded", job.error
        assert job.result["summary"]["trades"] == 1 and job.result["diagnostics"]["requests"] > 0
    calls = prov.calls
    queue.enqueue(db, "backtest", "bt-2", payload, user_id=None)
    await w.run_once()
    with db.session() as s:
        job = s.execute(select(Job).where(Job.idempotency_key == "bt-2")).scalar_one()
        assert job.result["diagnostics"]["cache_hits"] > 0
    assert prov.calls == calls  # the rerun made no provider calls


# ---- routes ----------------------------------------------------------------------

BODY = {"underlying": "spy", "start": "2026-01-05", "end": "2026-03-31", "strategy": "iron_condor"}


def test_routes_presets_estimate_and_blocked_without_history(tmp_path):
    c, keys = client(tmp_path)
    p = c.get("/api/backtests/presets", headers=auth(keys)).json()
    assert len(p["presets"]) == len(PRESETS) and p["available"] is False and "OPTIONS_HISTORY_PROVIDER" in p["blocked_by"][0]
    BacktestRequest.model_validate(p["defaults"])  # the page's defaults are a valid request
    est = c.post("/api/backtests/estimate", json=BODY, headers=auth(keys)).json()
    assert est["entry_days"] == 13 and est["est_requests"] == 13 * 5 + 2 and est["est_minutes"] > 0
    r = c.post("/api/backtests", json=BODY, headers=auth(keys))
    assert r.status_code == 503 and r.json()["error"] == "backtest_unavailable" and r.json()["blocked_by"]
    assert c.post("/api/backtests", json={**BODY, "strategy": "nope"}, headers=auth(keys)).status_code == 422


def test_routes_queue_list_and_dedupe(tmp_path):
    c, keys = client(tmp_path, settings_overrides={"options_history_provider": "thetadata"})
    first = c.post("/api/backtests", json=BODY, headers=auth(keys)).json()
    again = c.post("/api/backtests", json=BODY, headers=auth(keys)).json()
    assert first["status"] == "queued" and first["id"] == again["id"] and first["estimate"]["entry_days"] == 13
    listed = c.get("/api/backtests", headers=auth(keys)).json()["backtests"]
    assert [b["id"] for b in listed] == [first["id"]]
    assert listed[0]["underlying"] == "SPY" and listed[0]["strategy"] == "iron_condor" and listed[0]["summary"] is None
    assert c.get(f"/api/jobs/{first['id']}", headers=auth(keys)).json()["status"] == "queued"
