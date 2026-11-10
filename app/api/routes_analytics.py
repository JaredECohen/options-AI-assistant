"""Analytics (P4/P5), comparison (T4), screener + saved screens (P6), jobs (P7;
backtests live in routes_backtest), stored research (D) and course context (T8)."""
from __future__ import annotations

import datetime as dt
import math
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field, StringConstraints

from app.analytics import screener
from app.analytics.chain import enrich_chain, forward_price, otm_iv_points, time_to_expiry
from app.analytics.compare import TickerSnapshot, compare
from app.analytics.surface import ExpirySlice, build_surface
from app.api.routes_market import _provenance, freshness
from app.auth.deps import Principal, require
from app.db import repo
from app.integrations import course_context
from app.integrations.marketmosaic import ResearchUnavailable
from app.jobs import queue

router = APIRouter()
TICKER_PATH = r"^[A-Za-z.]{1,10}$"
TickerPath = Path(..., pattern=TICKER_PATH)
MAX_LIVE_UNIVERSE = 10


def _err(status: int, code: str, detail: str, **extra) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": code, "detail": detail, **extra})


@router.get("/api/analytics/{ticker}")
async def ticker_analytics(request: Request, ticker: str = TickerPath, expiration: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
                           principal: Principal = Depends(require("analytics"))):
    state = request.app.state
    snap = await state.snapshots.get(ticker)
    body = {"summary": snap.as_json(), "conventions": state.analytics_cfg.conventions(), **_provenance(state.options_provider)}
    if expiration:
        quotes = await state.options_provider.get_chain(ticker, expiration)
        now = dt.datetime.now(dt.UTC)
        body["chain"] = {"expiration": expiration, "contracts": enrich_chain(quotes, snap.underlying, expiration, now, state.analytics_cfg),
                         "freshness": freshness([q.quote_time for q in quotes if q.quote_time], state.options_provider.data_status)}
    return body


@router.get("/api/analytics/{ticker}/surface")
async def surface(request: Request, ticker: str = TickerPath, max_expiries: int = Query(4, ge=1, le=6),
                  principal: Principal = Depends(require("analytics"))):
    state = request.app.state
    p, cfg = state.options_provider, state.analytics_cfg
    now = dt.datetime.now(dt.UTC)
    underlying = await p.get_underlying_price(ticker)
    exps = [e for e in await p.list_expirations(ticker) if 7 <= time_to_expiry(e, now) * 365 <= 365][:max_expiries]
    slices = []
    for exp in exps:
        T = time_to_expiry(exp, now)
        F = forward_price(underlying, T, cfg)
        enriched = enrich_chain(await p.get_chain(ticker, exp), underlying, exp, now, cfg)
        pts = [(math.log(k / F), iv, "put" if k < F else "call", k) for k, iv in otm_iv_points(enriched, F)]
        slices.append(ExpirySlice(exp, T * 365, pts))
    return {"ticker": ticker.upper(), "as_of": now.isoformat(), "underlying_price": underlying,
            "surface": build_surface(slices), "conventions": cfg.conventions(), **_provenance(p)}


class CompareIn(BaseModel):
    tickers: list[Annotated[str, StringConstraints(pattern=TICKER_PATH)]] = Field(min_length=2, max_length=MAX_LIVE_UNIVERSE)
    target_dte: int = Field(30, ge=7, le=180)
    sort_by: Literal["iv", "hv20", "iv_hv_ratio", "iv_rank", "skew_25d", "ticker"] = "iv"


@router.post("/api/compare")
async def compare_tickers(body: CompareIn, request: Request, principal: Principal = Depends(require("analytics"))):
    state = request.app.state
    snaps, errors = [], []
    for t in dict.fromkeys(x.upper() for x in body.tickers):
        try:
            s = await state.snapshots.get(t)
        except Exception as exc:  # noqa: BLE001 - one bad ticker must not sink the table
            errors.append({"ticker": t, "error": getattr(exc, "code", exc.__class__.__name__)})
            continue
        snaps.append(TickerSnapshot(s.ticker, s.as_of, "USD", s.data_status, s.underlying,
                                    [(x["dte"], x["atm_iv"]) for x in s.term if x["atm_iv"]], s.hv["hv20"], s.iv_rank, s.skew))
    return {**compare(snaps, body.target_dte, body.sort_by), "errors": errors}


class ScreenRunIn(BaseModel):
    definition: dict = Field(default_factory=dict)
    preset: str | None = None
    cursor: str | None = Field(None, max_length=400)


async def _metrics(state, universe) -> dict:
    out = {}
    for t in universe:
        try:
            out[t] = (await state.snapshots.get(t)).screener_row()
        except Exception as exc:  # noqa: BLE001
            out[t] = {"_reasons": {f: getattr(exc, "code", exc.__class__.__name__) for f in screener.FIELDS}}
    return out


@router.post("/api/screener/run")
async def run_screen(body: ScreenRunIn, request: Request, principal: Principal = Depends(require("screens"))):
    try:
        screen = screener.parse(body.definition, body.preset)
    except screener.ScreenError as exc:
        raise _err(422, "invalid_screen", str(exc)) from exc
    if len(screen.universe) > MAX_LIVE_UNIVERSE:
        raise _err(422, "universe_too_large", f"live screens are limited to {MAX_LIVE_UNIVERSE} tickers")
    try:
        result = screener.run(screen, await _metrics(request.app.state, screen.universe), body.cursor)
    except screener.ScreenError as exc:
        raise _err(422, "invalid_screen", str(exc)) from exc
    return {**result, **_provenance(request.app.state.options_provider)}


@router.get("/api/screener/presets")
async def presets(principal: Principal = Depends(require("screens"))):
    return {"presets": screener.PRESETS, "fields": {k: v[0] for k, v in screener.FIELDS.items()}}


class ScreenIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    definition: dict


def _validated(defn: dict) -> dict:
    try:
        return screener.parse(defn).canonical()
    except screener.ScreenError as exc:
        raise _err(422, "invalid_screen", str(exc)) from exc


@router.get("/api/screens")
def list_screens(request: Request, principal: Principal = Depends(require("screens"))):
    return {"items": repo.list_screens(request.app.state.db, principal.user_id)}


@router.post("/api/screens")
def create_screen(body: ScreenIn, request: Request, principal: Principal = Depends(require("screens"))):
    return repo.save_screen(request.app.state.db, principal.user_id, body.name, _validated(body.definition))


@router.put("/api/screens/{screen_id}")
def update_screen(screen_id: str, body: ScreenIn, request: Request, principal: Principal = Depends(require("screens"))):
    return repo.save_screen(request.app.state.db, principal.user_id, body.name, _validated(body.definition), screen_id)


@router.get("/api/screens/{screen_id}")
def get_screen(screen_id: str, request: Request, principal: Principal = Depends(require("screens"))):
    return repo.get_screen(request.app.state.db, principal.user_id, screen_id)


@router.delete("/api/screens/{screen_id}")
def delete_screen(screen_id: str, request: Request, principal: Principal = Depends(require("screens"))):
    repo.delete_screen(request.app.state.db, principal.user_id, screen_id)
    return {"status": "deleted"}


@router.get("/api/jobs/{job_id}")
def get_job(job_id: str, request: Request, principal: Principal = Depends(require("chat"))):
    return queue.get_owned(request.app.state.db, principal.user_id, job_id).__dict__


@router.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request, principal: Principal = Depends(require("chat"))):
    return queue.cancel_owned(request.app.state.db, principal.user_id, job_id).__dict__


@router.get("/api/research/{ticker}/memos")
async def research_versions(request: Request, ticker: str = TickerPath, principal: Principal = Depends(require("research"))):
    client = request.app.state.research
    if client is None:
        raise _err(503, "research_unavailable", "MarketMosaic research integration is not enabled.")
    try:
        return await client.list_versions(ticker, principal.bearer, principal.external_id)
    except ResearchUnavailable as exc:
        raise _err(_research_status(exc.code), "research_" + exc.code, exc.detail or exc.code) from exc


@router.get("/api/research/{ticker}/memos/{version}")
async def research_memo(request: Request, version: int, ticker: str = TickerPath, principal: Principal = Depends(require("research"))):
    client = request.app.state.research
    if client is None:
        raise _err(503, "research_unavailable", "MarketMosaic research integration is not enabled.")
    try:
        return await client.get_memo(ticker, version, principal.bearer, principal.external_id)
    except ResearchUnavailable as exc:
        raise _err(_research_status(exc.code), "research_" + exc.code, exc.detail or exc.code) from exc


def _research_status(code: str) -> int:
    return {"not_found": 404, "not_authorized": 403, "plan_required": 402, "bad_ticker": 422, "bad_version": 422,
            "unreadable": 422, "rate_limited": 429}.get(code, 503)


@router.get("/api/course-context")
def course_notes(request: Request, q: str = Query(..., min_length=3, max_length=200),
                 principal: Principal = Depends(require("chat"))):
    try:
        return course_context.retrieve(request.app.state.settings.course_context_dir, q)
    except course_context.CourseContextUnavailable as exc:
        raise _err(503, "course_context_unavailable", str(exc)) from exc
