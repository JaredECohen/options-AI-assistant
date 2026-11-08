"""Market data (T1) and the deterministic payoff calculator (T2/T3)."""
from __future__ import annotations

import datetime as dt
from typing import Literal

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
    age = (now - as_of).total_seconds() if as_of else None
    stale = None if limit is None or age is None else age > limit
    return {"as_of": as_of.isoformat() if as_of else None, "age_seconds": None if age is None else int(age),
            "stale": stale, "stale_after_seconds": limit}


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


@router.get("/api/chain")
async def chain(
    request: Request,
    ticker: str = TICKER,
    expiration: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    side: Literal["both", "call", "put"] = "both",
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(40, ge=5, le=200),
    principal: Principal = Depends(require("market_data")),
):
    """One expiration, paged by strike (ascending). Rows keep the legacy
    {strike, call, put} shape with bid/ask/last/mark plus volume, OI, IV, greeks
    (with their source, or null), contract symbol, multiplier and quote time."""
    provider = request.app.state.options_provider
    if not expiration:  # legacy behaviour: list expirations
        return {"ticker": ticker.upper(), "expirations": await provider.list_expirations(ticker), **_provenance(provider)}
    quotes = await provider.get_chain(ticker, expiration)
    by_strike: dict[float, dict] = {}
    for q in quotes:
        if side != "both" and q.option_type != side:
            continue
        row = by_strike.setdefault(q.strike, {"strike": q.strike})
        row[q.option_type] = q.as_json()
    strikes = sorted(by_strike)
    total = len(strikes)
    start = (page - 1) * page_size
    rows = [by_strike[k] for k in strikes[start : start + page_size]]
    try:
        underlying = await provider.get_underlying_price(ticker)
    except Exception:  # noqa: BLE001 - the chain is still useful without it
        underlying = None
    return {
        "ticker": ticker.upper(),
        "expiration": expiration,
        "underlying_price": underlying,
        "chain": rows,
        "page": page,
        "page_size": page_size,
        "total_strikes": total,
        "has_more": start + page_size < total,
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
