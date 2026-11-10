"""Multi-leg options backtester on end-of-day history (contract: docs/BACKTESTING.md).

One code path for every strategy: a position is 1-4 option legs on one
expiration, plus optional long stock (covered calls). The loop walks trading
days (days with an underlying close). Each day it:

1. marks open legs at the closing mid (a missing quote carries the last mark);
2. checks exits in a fixed order: expiration, stop loss, profit target, exit DTE,
   max hold. Triggers use the mid, and fills use the cost model;
3. opens a position if the day is scheduled and fewer than max_open are open;
4. records equity = cash + open positions at mid + shares at the close.

Sign convention: per share, a leg's sign is +1 when sold and -1 when bought, so
a structure's net = sum(sign * price * ratio) is the credit received (> 0) or
the debit paid (< 0). In USD, entry_value = net_entry * 100 * contracts, and
closing it costs exit_value = -net_exit * 100 * contracts. A trade's P&L is
entry_value + exit_value + stock P&L - commissions - fees.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import math
import statistics
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from app.analytics import bsm
from app.analytics.chain import AnalyticsConfig, time_to_expiry
from app.backtest import metrics
from app.backtest.spec import PRESETS, BacktestRequest, CostModel, LegSpec
from app.jobs.iv_backfill import close_time
from app.providers.options.base import EodOption

MULT = 100
# Bump when results for the same request would change, so identical requests
# re-run instead of returning the previous job.
ENGINE_VERSION = 1
CHAIN_LOOKAHEAD = 4  # scheduled entry days whose chains are fetched ahead of the loop
CAVEATS = [
    "End-of-day data: exits trigger on closing marks, so intraday breaches of a stop or target are not seen.",
    "Early assignment is not modelled; in-the-money options settle at intrinsic value at expiration "
    "(cash-settled approximation; covered-call shares are sold at the strike).",
    "Dividends are not modelled, in the strategy or the buy-and-hold benchmark.",
    "Buying power for undefined-risk shorts is a Reg-T style estimate (20% of the underlying less the "
    "out-of-the-money amount, at least 10%, plus the premium); brokers differ.",
    "Closing quotes are the vendor's end-of-day NBBO; real fills at the close can be worse, especially in "
    "wide or illiquid markets.",
    "Results describe history under these assumptions. They are not a forecast or a recommendation.",
]


class Cancelled(Exception):
    """The job's lease was lost or the user cancelled."""


class HistorySource(Protocol):
    requests: int
    cache_hits: int
    data_source: str

    async def bars(self, start: dt.date, end: dt.date) -> list[tuple[dt.date, float]]: ...
    async def expirations(self) -> list[str]: ...
    async def chain(self, expiration: str, day: dt.date) -> list[EodOption]: ...
    async def contract(self, expiration: str, strike: float, right: str, start: dt.date,
                       end: dt.date) -> list[EodOption]: ...


# ---- small pure helpers (tested directly) ---------------------------------------

def sign(side: str) -> int:
    return 1 if side == "sell" else -1


def valid_quote(bid: float | None, ask: float | None) -> bool:
    return bid is not None and ask is not None and bid >= 0 and ask > 0 and ask >= bid


def mid(bid: float, ask: float) -> float:
    return (bid + ask) / 2


def fill_price(bid: float, ask: float, action: str, costs: CostModel) -> float:
    """Price for buying (action='buy') or selling ('sell') at the close.
    conservative: buy at the ask, sell at the bid. mid: the midpoint.
    spread_fraction: the mid moved against you by fraction x half the spread
    (0 = mid, 1 = the bid or ask)."""
    m = mid(bid, ask)
    if costs.fill == "mid":
        return m
    half = (ask - bid) / 2
    f = 1.0 if costs.fill == "conservative" else costs.spread_fraction
    return m + f * half if action == "buy" else max(m - f * half, 0.0)


def is_monthly(exp: dt.date, listed: set[dt.date]) -> bool:
    """Third Friday, or the Thursday before it when that Friday is a holiday."""
    if exp.weekday() == 4:
        return 15 <= exp.day <= 21
    if exp.weekday() == 3:
        return 14 <= exp.day <= 20 and (exp + dt.timedelta(days=1)) not in listed
    return False


def is_friday_cycle(exp: dt.date, listed: set[dt.date]) -> bool:
    """Weekly (Friday) expirations, including Thursdays that stand in for a holiday Friday."""
    return exp.weekday() == 4 or (exp.weekday() == 3 and (exp + dt.timedelta(days=1)) not in listed)


def parse_expirations(expirations: list[str]) -> list[dt.date]:
    return sorted({dt.date.fromisoformat(x) for x in expirations})


def candidate_expirations(dates: list[dt.date], day: dt.date, req: BacktestRequest, limit: int = 3,
                          listed: set[dt.date] | None = None) -> list[str]:
    """Expirations within [dte_min, dte_max] matching the filter, nearest the target first."""
    e = req.entry
    listed = listed if listed is not None else set(dates)
    out = []
    for exp in dates:
        dte = (exp - day).days
        if exp <= day or not e.dte_min <= dte <= e.dte_max:
            continue
        if e.expirations == "monthly" and not is_monthly(exp, listed):
            continue
        if e.expirations == "fridays" and not is_friday_cycle(exp, listed):
            continue
        out.append((abs(dte - e.dte_target), dte, exp.isoformat()))
    return [x for _a, _b, x in sorted(out)[:limit]]


def scheduled(day: dt.date, prev: dt.date | None, req: BacktestRequest, open_count: int,
              state: dict) -> bool:
    """Whether `day` is an entry day. Weekly entries fall on the first trading
    day on or after the chosen weekday in each week (so a holiday moves it later)."""
    f = req.entry.frequency
    if f == "daily":
        return True
    if f == "when_flat":
        return open_count == 0
    if f == "monthly":
        return prev is None or (prev.year, prev.month) != (day.year, day.month)
    week = day.isocalendar()[:2]
    if day.weekday() >= req.entry.weekday and state.get("week") != week:
        state["week"] = week
        return True
    return False


def option_delta(row: EodOption, spot: float, day: dt.date, cfg: AnalyticsConfig) -> float | None:
    """Vendor delta when present, else Black-Scholes delta from the mid's implied vol."""
    if row.delta is not None and math.isfinite(row.delta) and row.delta != 0:
        return row.delta
    iv = option_iv(row, spot, day, cfg)
    if iv is None:
        return None
    T = time_to_expiry(row.expiration, close_time(day))
    g = bsm.greeks(spot, row.strike, T, cfg.risk_free_rate, cfg.dividend_yield, iv, row.option_type == "call")
    d = float(g["delta"])
    return d if math.isfinite(d) else None


def option_iv(row: EodOption, spot: float, day: dt.date, cfg: AnalyticsConfig) -> float | None:
    if row.implied_vol is not None and math.isfinite(row.implied_vol) and row.implied_vol > 0:
        return row.implied_vol
    if not valid_quote(row.bid, row.ask) or spot <= 0:
        return None
    T = time_to_expiry(row.expiration, close_time(day))
    iv, reason = bsm.implied_vol(mid(row.bid, row.ask), spot, row.strike, T, cfg.risk_free_rate,
                                 cfg.dividend_yield, row.option_type == "call")
    v = float(iv)
    return v if reason == "" and math.isfinite(v) and v > 0 else None


def payoff_at_expiry(legs: list[tuple[str, str, int, float]], net: float, stock_shares_per_unit: float,
                     spot0: float, s: float) -> float:
    """P&L per share of one unit at expiry price s. legs = (right, side, ratio, strike)."""
    value = net
    for right, side, ratio, strike in legs:
        intrinsic = max(s - strike, 0.0) if right == "call" else max(strike - s, 0.0)
        value -= sign(side) * intrinsic * ratio
    return value + (s - spot0) * stock_shares_per_unit


def max_loss_per_unit(legs: list[tuple[str, str, int, float]], net: float, stock_shares_per_unit: float,
                      spot0: float) -> float | None:
    """Worst expiry loss in USD for one unit, or None when unbounded (net short calls)."""
    call_slope = stock_shares_per_unit + sum(-sign(side) * ratio for r, side, ratio, _k in legs if r == "call")
    if call_slope < 0:
        return None
    points = [0.0, *sorted({k for *_x, k in legs})]
    worst = min(payoff_at_expiry(legs, net, stock_shares_per_unit, spot0, s) for s in points)
    return max(-worst, 0.0) * MULT


def naked_shorts(legs: list[tuple[str, str, int, float]], has_stock_shares: float) -> bool:
    for right in ("call", "put"):
        short = sum(ratio for r, side, ratio, _k in legs if r == right and side == "sell")
        long_ = sum(ratio for r, side, ratio, _k in legs if r == right and side == "buy")
        if right == "call":
            long_ += has_stock_shares
        if short > long_:
            return True
    return False


def reg_t_requirement(legs: list[tuple[str, str, int, float]], prices: list[float], spot: float) -> float:
    """Reg-T style margin for naked shorts, USD per unit: the largest single short
    requirement (20% of spot less OTM amount, at least 10%, plus premium) plus the
    premium of the other shorts."""
    reqs, premiums = [], []
    for (right, side, ratio, strike), price in zip(legs, prices, strict=True):
        if side != "sell":
            continue
        otm = max(strike - spot, 0.0) if right == "call" else max(spot - strike, 0.0)
        floor = 0.10 * (spot if right == "call" else strike)
        reqs.append((max(0.20 * spot - otm, floor) + price) * ratio)
        premiums.append(price * ratio)
    if not reqs:
        return 0.0
    i = max(range(len(reqs)), key=reqs.__getitem__)
    return (reqs[i] + sum(p for j, p in enumerate(premiums) if j != i)) * MULT


# ---- state -------------------------------------------------------------------------

@dataclass
class OpenLeg:
    spec: LegSpec
    strike: float
    entry_price: float
    entry_delta: float | None
    marks: dict[dt.date, EodOption] = field(default_factory=dict)
    last_mid: float = 0.0
    exit_price: float | None = None


@dataclass
class Position:
    id: int
    entry_date: dt.date
    expiration: dt.date
    contracts: int
    legs: list[OpenLeg]
    net_entry: float
    shares: int
    stock_entry: float
    underlying_entry: float
    buying_power: float
    commissions: float = 0.0

    @property
    def credit(self) -> bool:
        return self.net_entry > 0

    def net_now(self) -> float:
        return sum(sign(leg.spec.side) * leg.last_mid * leg.spec.ratio for leg in self.legs)

    def liability(self) -> float:
        return -self.net_now() * MULT * self.contracts


@dataclass
class Run:
    req: BacktestRequest
    data: HistorySource
    cfg: AnalyticsConfig
    progress: Callable[[dict], None] | None = None
    should_continue: Callable[[], bool] | None = None


async def run_backtest(req: BacktestRequest, data: HistorySource, cfg: AnalyticsConfig | None = None,
                       progress: Callable[[dict], None] | None = None,
                       should_continue: Callable[[], bool] | None = None) -> dict:
    return await _Engine(Run(req, data, cfg or AnalyticsConfig(), progress, should_continue)).run()


class _Engine:
    def __init__(self, run: Run):
        self.r = run
        self.req = run.req
        self.costs = run.req.costs
        self.cash = run.req.sizing.capital
        self.open: list[Position] = []
        self.trades: list[dict] = []
        self.equity: list[dict] = []
        self.skips: Counter = Counter({k: 0 for k in ("no_expiration_in_window", "no_chain", "strike_not_found",
                                                      "iv_filter", "min_credit", "max_open",
                                                      "insufficient_capital")})
        self.stale = 0
        self.next_id = 1
        self.closes: dict[dt.date, float] = {}
        self.invested_days = 0
        self.max_bp = 0.0
        self.commissions = 0.0
        self._chain_tasks: dict[tuple[str, dt.date], asyncio.Task] = {}

    # -- data ---------------------------------------------------------------------
    def _chain(self, exp: str, day: dt.date) -> Awaitable[list[EodOption]]:
        key = (exp, day)
        if key not in self._chain_tasks:
            self._chain_tasks[key] = asyncio.ensure_future(self.r.data.chain(exp, day))
        return self._chain_tasks[key]

    def _prefetch(self, days: list[dt.date], i: int, expirations: list[dt.date]) -> None:
        """Start the first-choice chain for the next few scheduled days, so the
        ~4 s chain requests overlap instead of running one after another."""
        if self.req.entry.frequency in ("daily", "when_flat"):
            return
        state, prev, n = {"week": self._sched_state.get("week")}, days[i - 1] if i else None, 0
        for day in days[i:]:
            if n >= CHAIN_LOOKAHEAD:
                break
            if scheduled(day, prev, self.req, 0, state):
                n += 1
                cands = candidate_expirations(expirations, day, self.req, limit=1, listed=self._listed)
                if cands:
                    self._chain(cands[0], day)
            prev = day

    # -- main loop ----------------------------------------------------------------
    async def run(self) -> dict:
        started = time.monotonic()
        req = self.req
        bars = await self.r.data.bars(req.start, req.end)
        if len(bars) < 2:
            raise ValueError(f"no price history for {req.underlying} between {req.start} and {req.end}")
        self.closes = dict(bars)
        days = [d for d, _c in bars]
        expirations = parse_expirations(await self.r.data.expirations())
        self._listed = set(expirations)
        self._sched_state: dict = {}
        bench0 = bars[0][1]
        prev = None
        try:
            for i, day in enumerate(days):
                if self.r.should_continue is not None and i % 5 == 0 and not self.r.should_continue():
                    raise Cancelled()
                self._prefetch(days, i, expirations)
                close = self.closes[day]
                for pos in list(self.open):
                    self._mark(pos, day)
                    reason = self._exit_reason(pos, day)
                    if reason:
                        self._close(pos, day, reason)
                if scheduled(day, prev, req, len(self.open), self._sched_state):
                    if len(self.open) >= req.entry.max_open:
                        self.skips["max_open"] += 1
                    else:
                        await self._try_open(day, close, expirations)
                if self.open:
                    self.invested_days += 1
                self.max_bp = max(self.max_bp, sum(p.buying_power for p in self.open))
                eq = self._equity(close)
                self.equity.append({"date": day.isoformat(), "equity": round(eq, 2),
                                    "benchmark": round(req.sizing.capital * close / bench0, 2)})
                if self.r.progress is not None and (i % 10 == 0 or i == len(days) - 1):
                    self.r.progress({"pct": round(100 * (i + 1) / len(days), 1), "day": day.isoformat(),
                                     "trades": len(self.trades), "open": len(self.open),
                                     "requests": self.r.data.requests})
                prev = day
            last = days[-1]
            for pos in list(self.open):
                self._close(pos, last, "end_of_test", force=True)
            if self.equity:
                self.equity[-1]["equity"] = round(self._equity(self.closes[last]), 2)
        finally:
            for task in self._chain_tasks.values():
                if not task.done():
                    task.cancel()
        return self._result(time.monotonic() - started)

    def _equity(self, close: float) -> float:
        return self.cash + sum(p.liability() + p.shares * close for p in self.open)

    # -- marks and exits ------------------------------------------------------------
    def _mark(self, pos: Position, day: dt.date) -> None:
        for leg in pos.legs:
            row = leg.marks.get(day)
            if row is not None and valid_quote(row.bid, row.ask):
                leg.last_mid = mid(row.bid, row.ask)
            elif day < pos.expiration:
                self.stale += 1

    def _exit_reason(self, pos: Position, day: dt.date) -> str | None:
        if day >= pos.expiration:
            return "expired"
        x = self.req.exit
        pnl = pos.net_entry - pos.net_now()  # per share, before costs
        basis = abs(pos.net_entry)
        if basis > 0:
            if x.stop_loss_pct is not None and -pnl >= x.stop_loss_pct / 100 * basis:
                return "stop_loss"
            if x.profit_target_pct is not None and pnl >= x.profit_target_pct / 100 * basis:
                return "profit_target"
        if x.exit_dte is not None and (pos.expiration - day).days <= x.exit_dte:
            return "exit_dte"
        if x.max_hold_days is not None and (day - pos.entry_date).days >= x.max_hold_days:
            return "max_hold"
        return None

    def _close(self, pos: Position, day: dt.date, reason: str, force: bool = False) -> None:
        c = pos.contracts
        fees = 0.0
        if reason == "expired":
            settle_day = max((d for d in self.closes if d <= pos.expiration), default=day)
            s = self.closes[settle_day]
            net_exit = 0.0
            for leg in pos.legs:
                intrinsic = max(s - leg.strike, 0.0) if leg.spec.right == "call" else max(leg.strike - s, 0.0)
                leg.exit_price = intrinsic
                net_exit += sign(leg.spec.side) * intrinsic * leg.spec.ratio
                if intrinsic > 0:
                    fees += self.costs.assignment_fee * leg.spec.ratio * c
            stock_exit = s
        else:
            rows = [leg.marks.get(day) for leg in pos.legs]
            if not all(r is not None and valid_quote(r.bid, r.ask) for r in rows):
                if not force:
                    return  # wait for a day with quotes on every leg
                rows = [None] * len(pos.legs)
            net_exit = 0.0
            for leg, row in zip(pos.legs, rows, strict=True):
                if row is None:
                    price = leg.last_mid
                else:
                    price = fill_price(row.bid, row.ask, "buy" if leg.spec.side == "sell" else "sell", self.costs)
                leg.exit_price = price
                net_exit += sign(leg.spec.side) * price * leg.spec.ratio
                fees += self.costs.commission_per_contract * leg.spec.ratio * c
            stock_exit = self.closes[day]
        exit_value = -net_exit * MULT * c
        entry_value = pos.net_entry * MULT * c
        stock_pnl = (stock_exit - pos.stock_entry) * pos.shares
        self.cash += exit_value + pos.shares * stock_exit - fees
        self.commissions += fees
        pnl = entry_value + exit_value + stock_pnl - pos.commissions - fees
        self.open.remove(pos)
        self.trades.append({
            "id": pos.id, "entry_date": pos.entry_date.isoformat(), "exit_date": day.isoformat(),
            "expiration": pos.expiration.isoformat(), "contracts": c,
            "entry_value": round(entry_value, 2), "exit_value": round(exit_value, 2),
            "stock_pnl": round(stock_pnl, 2) if pos.shares else None,
            "commissions": round(pos.commissions + fees, 2), "pnl": round(pnl, 2), "exit_reason": reason,
            "days_held": (day - pos.entry_date).days, "underlying_entry": pos.underlying_entry,
            "underlying_exit": stock_exit, "buying_power": round(pos.buying_power, 2),
            "legs": [{"right": leg.spec.right, "side": leg.spec.side, "strike": leg.strike, "ratio": leg.spec.ratio,
                      "entry_price": round(leg.entry_price, 4),
                      "exit_price": None if leg.exit_price is None else round(leg.exit_price, 4),
                      "entry_delta": None if leg.entry_delta is None else round(leg.entry_delta, 4)}
                     for leg in pos.legs],
        })

    # -- entries --------------------------------------------------------------------
    async def _try_open(self, day: dt.date, close: float, expirations: list[dt.date]) -> None:
        cands = candidate_expirations(expirations, day, self.req, limit=12, listed=self._listed)
        if not cands:
            self.skips["no_expiration_in_window"] += 1
            return
        chain, exp = [], None
        # Weeklies and dailies are listed only a few weeks ahead; monthlies months
        # ahead. After the first empty chain, monthlies go to the front of the queue,
        # and non-monthlies at least that far out are skipped.
        queue_, unlisted_from, tries = list(cands), None, 0
        while queue_ and tries < 6:
            cand = queue_.pop(0)
            cand_date = dt.date.fromisoformat(cand)
            dte = (cand_date - day).days
            if unlisted_from is not None and dte >= unlisted_from and not is_monthly(cand_date, self._listed):
                continue
            tries += 1
            rows = [r for r in await self._chain(cand, day) if r.date == day]
            if rows:
                chain, exp = rows, cand
                break
            unlisted_from = dte if unlisted_from is None else min(unlisted_from, dte)
            queue_.sort(key=lambda c: not is_monthly(dt.date.fromisoformat(c), self._listed))  # stable
        if not chain:
            self.skips["no_chain"] += 1
            return
        spot = next((r.underlying_price for r in chain if r.underlying_price), None) or close
        picked = self._select(chain, spot, day)
        if picked is None:
            self.skips["strike_not_found"] += 1
            return
        if not self._iv_ok(chain, spot, day):
            self.skips["iv_filter"] += 1
            return
        prices = [fill_price(row.bid, row.ask, leg.side, self.costs) for leg, row in picked]
        net = sum(sign(leg.side) * p * leg.ratio for (leg, _row), p in zip(picked, prices, strict=True))
        e = self.req.entry
        if e.min_credit is not None and net < e.min_credit:
            self.skips["min_credit"] += 1
            return
        shares_unit = self.req.stock.shares_per_contract if self.req.stock else 0
        shape = [(leg.right, leg.side, leg.ratio, row.strike) for leg, row in picked]
        bp_unit = self._buying_power(shape, prices, net, spot, shares_unit)
        contracts = self._contracts(bp_unit)
        if contracts < 1:
            self.skips["insufficient_capital"] += 1
            return
        exp_date = dt.date.fromisoformat(exp)
        end = min(exp_date, self.req.end)
        legs = []
        histories = await asyncio.gather(*(
            self.r.data.contract(exp, row.strike, leg.right, day + dt.timedelta(days=1), end)
            for leg, row in picked)) if day < end else [[] for _ in picked]
        for (leg, row), price, hist in zip(picked, prices, histories, strict=True):
            legs.append(OpenLeg(spec=leg, strike=row.strike, entry_price=price,
                                entry_delta=option_delta(row, spot, day, self.r.cfg),
                                marks={h.date: h for h in hist}, last_mid=mid(row.bid, row.ask)))
        commissions = sum(self.costs.commission_per_contract * leg.ratio for leg, _r in picked) * contracts
        shares = shares_unit * contracts
        pos = Position(id=self.next_id, entry_date=day, expiration=exp_date, contracts=contracts, legs=legs,
                       net_entry=net, shares=shares, stock_entry=close, underlying_entry=spot,
                       buying_power=bp_unit * contracts, commissions=commissions)
        self.next_id += 1
        self.cash += net * MULT * contracts - commissions - shares * close
        self.commissions += commissions
        self.open.append(pos)

    def _select(self, chain: list[EodOption], spot: float, day: dt.date) -> list[tuple[LegSpec, EodOption]] | None:
        by_right: dict[str, list[EodOption]] = {"call": [], "put": []}
        for r in chain:
            if valid_quote(r.bid, r.ask):
                by_right[r.option_type].append(r)
        for rows in by_right.values():
            rows.sort(key=lambda r: r.strike)
        picked: list[tuple[LegSpec, EodOption]] = []
        for leg in self.req.legs:
            rows = by_right[leg.right]
            if not rows:
                return None
            rule = leg.strike
            if rule.method == "delta":
                scored = [(abs(abs(d) - rule.value), r) for r in rows
                          if (d := option_delta(r, spot, day, self.r.cfg)) is not None]
                if not scored:
                    return None
                diff, row = min(scored, key=lambda t: (t[0], t[1].strike))
                if diff > self.req.entry.delta_tolerance:
                    return None
            elif rule.method == "atm":
                row = min(rows, key=lambda r: (abs(r.strike - spot), r.strike))
            elif rule.method == "pct_otm":
                target = spot * (1 - rule.value / 100) if leg.right == "put" else spot * (1 + rule.value / 100)
                row = min(rows, key=lambda r: (abs(r.strike - target), r.strike))
            else:
                ref = picked[rule.from_leg][1].strike
                target = ref + rule.value
                if rule.value == 0:
                    side = [r for r in rows if r.strike == ref]
                else:  # never on the wrong side of (or equal to) the reference strike
                    side = [r for r in rows if (r.strike > ref if rule.value > 0 else r.strike < ref)]
                if not side:
                    return None
                row = min(side, key=lambda r: (abs(r.strike - target), r.strike))
                if abs(row.strike - target) > max(abs(rule.value) * 0.5, 0.5):
                    return None  # the listed strikes cannot express this width; skip rather than distort it
            picked.append((leg, row))
        return picked

    def _iv_ok(self, chain: list[EodOption], spot: float, day: dt.date) -> bool:
        e = self.req.entry
        if e.min_iv is None and e.max_iv is None:
            return True
        strikes = sorted({r.strike for r in chain})
        if not strikes:
            return False
        atm = min(strikes, key=lambda k: abs(k - spot))
        ivs = [v for r in chain if r.strike == atm and (v := option_iv(r, spot, day, self.r.cfg)) is not None]
        if not ivs:
            return False
        iv = sum(ivs) / len(ivs)
        return (e.min_iv is None or iv >= e.min_iv) and (e.max_iv is None or iv <= e.max_iv)

    def _buying_power(self, shape, prices, net, spot, shares_unit) -> float:
        if self.req.strategy == "cash_secured_put":
            return sum(k * ratio for r, side, ratio, k in shape if r == "put" and side == "sell") * MULT
        stock_cost = spot * shares_unit
        if naked_shorts(shape, shares_unit / MULT):
            return reg_t_requirement(shape, prices, spot) + stock_cost
        if shares_unit:  # stock-backed (covered call, collar): the cash is the shares; their risk is inside it
            return stock_cost
        loss = max_loss_per_unit(shape, net, 0, spot)
        return loss if loss is not None else reg_t_requirement(shape, prices, spot)

    def _contracts(self, bp_unit: float) -> int:
        s = self.req.sizing
        if s.mode == "fixed":
            return s.contracts
        if bp_unit <= 0:
            return 0
        equity = self.equity[-1]["equity"] if self.equity else s.capital  # yesterday's close
        return min(int(equity * s.risk_pct / 100 // bp_unit), 100)

    # -- results ----------------------------------------------------------------------
    def _result(self, runtime: float) -> dict:
        req = self.req
        eq = [p["equity"] for p in self.equity]
        bench = [p["benchmark"] for p in self.equity]
        peak = 0.0
        for p in self.equity:
            peak = max(peak, p["equity"])
            p["drawdown"] = round((peak - p["equity"]) / peak, 6) if peak > 0 else 0.0
        pnls = [t["pnl"] for t in self.trades]
        years = max((req.end - req.start).days / 365.25, 1 / 365.25)
        gains, losses = sum(p for p in pnls if p > 0), -sum(p for p in pnls if p < 0)
        rets = metrics.daily_returns(eq)
        summary = {
            "total_return": _ratio(eq[-1], eq[0]), "cagr": _cagr(eq[0], eq[-1], years),
            "sharpe": metrics.sharpe(rets), "sortino": metrics.sortino(rets),
            "max_drawdown": metrics.max_drawdown(eq), "win_rate": metrics.win_rate(pnls),
            "cvar_95": metrics.cvar(rets, 0.95), "trades": len(pnls),
            "avg_pnl": round(statistics.fmean(pnls), 2) if pnls else None,
            "median_pnl": round(statistics.median(pnls), 2) if pnls else None,
            "best_trade": max(pnls) if pnls else None, "worst_trade": min(pnls) if pnls else None,
            "profit_factor": round(gains / losses, 4) if losses > 0 else None,
            "avg_days_held": round(statistics.fmean(t["days_held"] for t in self.trades), 2) if pnls else None,
            "final_equity": eq[-1], "time_in_market": round(self.invested_days / len(eq), 4),
            "max_buying_power": round(self.max_bp, 2), "total_commissions": round(self.commissions, 2),
        }
        brets = metrics.daily_returns(bench)
        benchmark = {"name": f"Buy and hold {req.underlying}", "total_return": _ratio(bench[-1], bench[0]),
                     "cagr": _cagr(bench[0], bench[-1], years), "sharpe": metrics.sharpe(brets),
                     "max_drawdown": metrics.max_drawdown(bench)}
        config = req.model_dump(mode="json")
        return {
            "config": config, "summary": summary, "benchmark": benchmark, "equity": self.equity,
            "monthly_returns": _monthly(self.equity), "trades": self.trades,
            "exit_reasons": dict(Counter(t["exit_reason"] for t in self.trades)),
            "diagnostics": {"skipped_entries": dict(self.skips), "stale_marks": self.stale,
                            "requests": self.r.data.requests, "cache_hits": self.r.data.cache_hits,
                            "data_source": self.r.data.data_source, "runtime_seconds": round(runtime, 1),
                            "strategy_label": PRESETS[req.strategy]["label"]},
            "caveats": CAVEATS, "synthetic": False,
        }


def _ratio(end: float, start: float) -> float | None:
    return round(end / start - 1, 6) if start else None


def _cagr(start: float, end: float, years: float) -> float | None:
    if start <= 0 or end <= 0:
        return None
    return round((end / start) ** (1 / years) - 1, 6)


def _monthly(equity: list[dict]) -> list[dict]:
    last: dict[str, float] = {}
    first_value = equity[0]["equity"] if equity else None
    for p in equity:
        last[p["date"][:7]] = p["equity"]
    out, prev = [], first_value
    for month, value in last.items():
        out.append({"month": month, "return": round(value / prev - 1, 6) if prev else None})
        prev = value
    return out
