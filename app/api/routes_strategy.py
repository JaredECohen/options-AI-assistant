"""Strategy finder (describe a view, get candidate structures). See app/strategy/finder.py."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.auth.deps import Principal, require
from app.providers.options.base import ProviderUnavailable, QuoteUnavailable
from app.strategy.finder import TEMPLATES, find_strategies
from app.strategy.view import View, interpret

router = APIRouter()


class SuggestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticker: str = Field(pattern=r"^[A-Za-z.]{1,10}$")
    text: str = Field(default="", max_length=600)
    view: View | None = None  # edited fields; when given they win over the text


@router.post("/api/strategies/suggest")
async def suggest(body: SuggestIn, request: Request, principal: Principal = Depends(require("payoff"))):
    state = request.app.state
    ticker = body.ticker.upper()
    try:
        spot = await state.options_provider.get_underlying_price(ticker)
    except (ProviderUnavailable, QuoteUnavailable) as exc:
        raise HTTPException(503, {"error": "market_data_unavailable", "detail": str(exc)}) from exc
    if body.view is not None:
        view, parsed_by = body.view, "edited"
    else:
        view, parsed_by = await interpret(body.text, spot, state.llm)
    base = {"interpreted": view.model_dump(), "parsed_by": parsed_by, "ticker": ticker, "spot": round(spot, 2)}
    if view.direction is None:
        return {**base, "needs_direction": True, "candidates": [], "excluded": [],
                "detail": "Say whether you expect the price to rise, fall, stay in a range, or move a lot, "
                          "or pick a direction below."}
    try:
        result = await find_strategies(view, ticker, state.options_provider, state.analytics_cfg, spot=spot)
    except (ProviderUnavailable, QuoteUnavailable) as exc:
        raise HTTPException(503, {"error": "market_data_unavailable", "detail": str(exc)}) from exc
    except LookupError as exc:
        raise HTTPException(422, {"error": "no_structures", "detail": str(exc)}) from exc
    return {**base, **result, "needs_direction": False}


@router.get("/api/strategies/templates")
def templates(principal: Principal = Depends(require("payoff"))):
    return {"templates": [{"key": t.key, "name": t.name, "directions": list(t.directions), "how": t.how,
                           "undefined_risk": t.undefined_risk} for t in TEMPLATES]}
