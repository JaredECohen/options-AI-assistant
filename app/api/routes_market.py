"""Market data (T1) and the deterministic payoff calculator (T2/T3)."""
from __future__ import annotations

import datetime as dt
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app.auth.deps import Principal, require
from app.core.payoff import compute_payoff, resolve_premiums
from app.models import Leg
from app.providers.options.base import DATA_STATUS_LABELS

router = APIRouter()
TICKER = Query(..., pattern=r"^[A-Za-z.]{1,10}$")
EXPIRATION = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$")

# A quote older than this (seconds) is flagged stale for its declared status.
STALE_AFTER = {"live": 120, "delayed": 30 * 60, "eod": 4 * 24 * 3600, "synthetic": None}


NY = ZoneInfo("America/New_York")
SESSION_OPEN, SESSION_CLOSE = dt.time(9, 30), dt.time(16, 15)


def last_session_close(now: dt.datetime) -> tuple[bool, dt.datetime]:
    """(market_open, reference time). While the market is open the reference is
    now; while it is closed it is the most recent 16:15 ET weekday close, so the
    closing quotes are not called stale all evening and weekend. Exchange holidays
    are not modelled (a holiday reads as a normal closed weekday)."""
    ny = now.astimezone(NY)
    if ny.weekday() < 5 and SESSION_OPEN <= ny.time() < SESSION_CLOSE:
        return True, now
    day = ny.date()
    if ny.weekday() >= 5 or ny.time() < SESSION_OPEN:
        day -= dt.timedelta(days=1)
    while day.weekday() >= 5:
        day -= dt.timedelta(days=1)
    return False, dt.datetime.combine(day, SESSION_CLOSE, NY).astimezone(dt.UTC)


def freshness(quote_times: list[str], data_status: str, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.UTC)
    parsed = []
    for t in quote_times:
        try:
            parsed.append(dt.datetime.fromisoformat(t))
        except (TypeError, ValueError):
            continue
    as_of = max(parsed) if parsed else None
    limit = STALE_AFTER.get(data_status)
    market_open, reference = last_session_close(now)
    age = (now - as_of).total_seconds() if as_of else None
    lag = max((reference - as_of).total_seconds(), 0) if as_of else None  # behind the latest available data
    stale = None if limit is None or lag is None else lag > limit
    return {"as_of": as_of.isoformat() if as_of else None, "age_seconds": None if age is None else int(age),
            "stale": stale, "stale_after_seconds": limit, "market_open": market_open}


def _provenance(provider) -> dict:
    return {"provider": provider.name, "data_status": provider.data_status,
            "data_status_label": DATA_STATUS_LABELS.get(provider.data_status, provider.data_status),
            "currency": "USD"}


@router.get("/api/quote")
async def quote(request: Request, ticker: str = TICKER, principal: Principal = Depends(require("market_data"))):
    provider = request.app.state.options_provider
    price = await provider.get_underlying_price(ticker)
    return {"ticker": ticker.upper(), "price": price, "source": provider.name, **_provenance(provider)}


@router.get("/api/expirations")
async def expirations(request: Request, ticker: str = TICKER, principal: Principal = Depends(require("market_data"))):
    provider = request.app.state.options_provider
    return {"ticker": ticker.upper(), "expirations": await provider.list_expirations(ticker), **_provenance(provider)}


def _mid(q: dict | None) -> float | None:
    if not q or q.get("bid") is None or q.get("ask") is None:
        return None
    return (q["bid"] + q["ask"]) / 2


def atm_strike(by_strike: dict[float, dict], underlying: float | None) -> tuple[float | None, str | None]:
    """The at-the-money strike and how it was found. Nearest the underlying
    (ties to the lower strike); without an underlying, the strike where call and
    put mids are closest (put-call parity puts that near the forward)."""
    strikes = sorted(by_strike)
    if not strikes:
        return None, None
    if underlying is not None:
        return min(strikes, key=lambda k: (abs(k - underlying), k)), "underlying"
    gaps = []
    for k in strikes:
        c, p = _mid(by_strike[k].get("call")), _mid(by_strike[k].get("put"))
        if c is not None and p is not None:
            gaps.append((abs(c - p), k))
    if gaps:
        return min(gaps)[1], "put_call_parity"
    return None, None


def centered_window(strikes: list[float], atm: float | None, each_side: int) -> tuple[int, int, int | None]:
    """[lo, hi) slice of the ascending strikes list covering each_side strikes
    below and above the one nearest atm, plus that strike's index. Without an
    ATM the window starts at the lowest strike."""
    if not strikes:
        return 0, 0, None
    if atm is None:
        return 0, min(len(strikes), 2 * each_side + 1), None
    idx = min(range(len(strikes)), key=lambda i: (abs(strikes[i] - atm), strikes[i]))
    return max(0, idx - each_side), min(len(strikes), idx + each_side + 1), idx


@router.get("/api/chain")
async def chain(
    request: Request,
    ticker: str = TICKER,
    expiration: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    side: Literal["both", "call", "put"] = "both",
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(40, ge=5, le=200),
    strikes: int | None = Query(None, ge=1, le=500, description="strikes on EACH side of the money; omit to page"),
    principal: Principal = Depends(require("market_data")),
):
    """One expiration. With `strikes`, a window centred on the at-the-money
    strike; without it, paged by strike from the lowest up (legacy). Rows keep
    the {strike, call, put} shape with bid/ask/last/mark plus volume, OI, IV,
    greeks (with their source, or null), contract symbol, multiplier and quote time."""
    provider = request.app.state.options_provider
    if not expiration:  # legacy behaviour: list expirations
        return {"ticker": ticker.upper(), "expirations": await provider.list_expirations(ticker), **_provenance(provider)}
    quotes = await provider.get_chain(ticker, expiration)
    try:
        underlying = await provider.get_underlying_price(ticker)
    except Exception:  # noqa: BLE001 - the chain is still useful without it
        underlying = None
    by_strike: dict[float, dict] = {}
    for q in quotes:
        row = by_strike.setdefault(q.strike, {"strike": q.strike})
        row[q.option_type] = q.as_json()
    atm, atm_basis = atm_strike(by_strike, underlying)  # on both sides, whatever is shown
    if side != "both":
        other = "put" if side == "call" else "call"
        by_strike = {k: {key: v for key, v in row.items() if key != other}
                     for k, row in by_strike.items() if side in row}
    ordered = sorted(by_strike)
    total = len(ordered)
    window: dict = {}
    if strikes is not None:
        lo, hi, _idx = centered_window(ordered, atm, strikes)
        rows = [by_strike[k] for k in ordered[lo:hi]]
        window = {"strikes_each_side": strikes, "more_below": lo > 0, "more_above": hi < total}
        has_more = hi < total
        page, page_size = 1, len(rows)
    else:
        start = (page - 1) * page_size
        rows = [by_strike[k] for k in ordered[start : start + page_size]]
        has_more = start + page_size < total
        window = {"strikes_each_side": None, "more_below": start > 0, "more_above": has_more}
    return {
        "ticker": ticker.upper(),
        "expiration": expiration,
        "underlying_price": underlying,
        "atm_strike": atm,
        "atm_basis": atm_basis,
        "chain": rows,
        "page": page,
        "page_size": page_size,
        "total_strikes": total,
        "has_more": has_more,
        **window,
        "freshness": freshness([q.quote_time for q in quotes if q.quote_time], provider.data_status),
        **_provenance(provider),
        "units": {"prices": "USD per share", "multiplier": "shares per contract (see each contract)",
                  "implied_volatility": "annualized decimal", "theta": "per calendar day per share"},
    }


class PayoffIn(BaseModel):
    ticker: str = Field(pattern=r"^[A-Za-z.]{1,10}$")
    legs: list[Leg] = Field(min_length=1, max_length=8)
    fill_convention: Literal["mid", "conservative"] = "mid"


@router.post("/api/payoff")
async def payoff(body: PayoffIn, request: Request, principal: Principal = Depends(require("payoff"))):
    """Deterministic and authoritative. Missing premiums are priced from quotes
    with full provenance, or the request fails; nothing is assumed to be zero."""
    provider = request.app.state.options_provider
    legs = list(body.legs)
    if any(leg.premium is None for leg in legs):
        resolved, used, underlying = await resolve_premiums(body.ticker.upper(), legs, provider, body.fill_convention)
        source, status = provider.name, provider.data_status
    else:
        resolved = legs
        used = [{**leg.model_dump(), "premium_source": "user_entered"} for leg in legs]
        try:
            underlying = await provider.get_underlying_price(body.ticker)
            status = provider.data_status
        except Exception:  # noqa: BLE001
            underlying, status = None, None
        source = "user_provided"
    strikes = [getattr(leg, "strike", None) for leg in resolved if getattr(leg, "strike", None)]
    reference = underlying if underlying is not None else (max(strikes) if strikes else 1.0)
    out = compute_payoff(body.ticker.upper(), resolved, used, reference, source, status)
    if underlying is None:
        out["computed"]["underlying_price"] = None
    return out
