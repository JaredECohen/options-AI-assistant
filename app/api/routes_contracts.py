"""Versioned, read-only contracts for other services (MarketMosaic first).

Auth is two-part and both parts are required:
1. X-Service-Token: identifies the calling service. Compared in constant time
   against SHA-256 hashes in OC_SERVICE_TOKEN_HASHES_JSON ({"marketmosaic": "<hex>"}),
   so the raw token is never stored here.
2. Authorization: Bearer <the end user's Clerk token>, delegated by the caller.
   OptionsCoach verifies it and applies its own entitlements, so a downstream
   service cannot widen what a user may see.
These endpoints never call a model, never trigger ingestion, and are bounded
(<= MAX_STRIKES strikes per page, one expiration per request).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.analytics.chain import enrich_chain
from app.api.routes_market import STALE_AFTER, _provenance, freshness
from app.auth.deps import Principal, require
from app.core.payoff import compute_payoff, resolve_premiums
from app.models import Leg

router = APIRouter(prefix="/api/v1/contracts")
CONTRACT = "oc.options_context.v1"
PAYOFF_CONTRACT = "oc.payoff.v1"
MAX_STRIKES = 60


def service_caller(request: Request) -> str:
    token = request.headers.get("x-service-token", "")
    hashes = request.app.state.settings.service_token_hashes or {}
    if not token or not hashes:
        raise HTTPException(401, {"error": "service_auth_required", "detail": "service token required"})
    digest = hashlib.sha256(token.encode()).hexdigest()
    for name, expected in hashes.items():
        if isinstance(expected, str) and hmac.compare_digest(digest, expected.lower()):
            return name
    raise HTTPException(401, {"error": "service_auth_invalid", "detail": "unknown service token"})


@router.get("/options-context")
async def options_context(
    request: Request,
    ticker: str = Query(..., pattern=r"^[A-Za-z.]{1,10}$"),
    expiration: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    page: int = Query(1, ge=1, le=100),
    page_size: int = Query(40, ge=1, le=MAX_STRIKES),
    caller: str = Depends(service_caller),
    principal: Principal = Depends(require("market_data")),
):
    state = request.app.state
    provider, cfg = state.options_provider, state.analytics_cfg
    now = dt.datetime.now(dt.UTC)
    quotes = await provider.get_chain(ticker, expiration)
    underlying = await provider.get_underlying_price(ticker)
    strikes = sorted({q.strike for q in quotes})
    window = set(strikes[(page - 1) * page_size : page * page_size])
    selected = [q for q in quotes if q.strike in window]
    enriched = enrich_chain(selected, underlying, expiration, now, cfg)
    contracts = []
    for q, e in zip(selected, enriched, strict=True):
        contracts.append({
            "contract_id": q.symbol, "option_type": q.option_type, "strike": q.strike, "expiration": expiration,
            "bid": q.bid, "ask": q.ask, "mid": e["mid"], "last": q.last, "volume": q.volume,
            "open_interest": q.open_interest, "quote_time": q.quote_time, "multiplier": q.multiplier,
            "adjusted_deliverable": q.adjusted,
            "iv": {"provider": {"value": q.implied_volatility, "source": q.iv_source} if q.implied_volatility is not None else None,
                   "model": {"value": e["iv_model"], "model": "bsm-european"} if e["iv_model"] is not None else None,
                   "unavailable_reason": e["iv_model_unavailable_reason"]},
            "greeks": {"provider": e["greeks_provider"], "model": e["greeks_model"]},
        })
    return {
        "contract": CONTRACT,
        "generated_at": now.isoformat(),
        "caller": caller,
        "ticker": ticker.upper(),
        "underlying": {"price": underlying, "currency": "USD"},
        "expiration": expiration,
        **_provenance(provider),
        "freshness": freshness([q.quote_time for q in quotes if q.quote_time], provider.data_status),
        "freshness_policy": {"stale_after_seconds": STALE_AFTER},
        "conventions": {**cfg.conventions(), "timezone": "quote_time is ISO-8601 with UTC offset",
                        "multiplier": "shares per contract; adjusted_deliverable=true means non-standard"},
        "page": page, "page_size": page_size, "total_strikes": len(strikes),
        "has_more": page * page_size < len(strikes),
        "contracts": contracts,
    }


class PayoffContractIn(BaseModel):
    ticker: str = Field(pattern=r"^[A-Za-z.]{1,10}$")
    legs: list[Leg] = Field(min_length=1, max_length=8)


@router.post("/payoff")
async def payoff_context(body: PayoffContractIn, request: Request, caller: str = Depends(service_caller),
                         principal: Principal = Depends(require("payoff"))):
    provider = request.app.state.options_provider
    legs = list(body.legs)
    resolved, used, underlying = await resolve_premiums(body.ticker.upper(), legs, provider)
    out = compute_payoff(body.ticker.upper(), resolved, used, underlying, provider.name, provider.data_status)
    out["computed"].pop("payoff_curve", None)  # bounded payload: bounds/breakevens only
    return {"contract": PAYOFF_CONTRACT, "caller": caller, **out["computed"]}
