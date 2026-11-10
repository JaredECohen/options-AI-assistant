"""Backtests (P7): presets, estimate, submit, history. The engine runs in a job
(app.worker, or the embedded worker); contract in docs/BACKTESTING.md."""
from __future__ import annotations

import datetime as dt
import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from app.auth.deps import Principal, require
from app.backtest.engine import ENGINE_VERSION
from app.backtest.spec import PRESETS, BacktestRequest, default_request
from app.db.models import Job
from app.jobs import queue

router = APIRouter()
HISTORY_SECONDS_PER_CHAIN = 4.5  # live ThetaData, 2026-11-09: one closing chain-day, incl. the odd unlisted weekly
HISTORY_SECONDS_PER_CONTRACT = 0.6  # one contract's whole life
RECENT = 20


def blockers(request: Request) -> list[str]:
    s = request.app.state.settings
    out = []
    if s.options_history_provider != "thetadata":
        out.append("no historical options data source: set OPTIONS_HISTORY_PROVIDER=thetadata "
                   "(with THETADATA_EMAIL/THETADATA_PASSWORD or THETADATA_API_KEY)")
    return out


def estimate(req: BacktestRequest) -> dict:
    """Rough cost of a first run. Cached reruns need almost no requests."""
    days = [req.start + dt.timedelta(days=i) for i in range((req.end - req.start).days + 1)]
    trading = [d for d in days if d.weekday() < 5]
    f = req.entry.frequency
    if f == "daily":
        entries = len(trading)
    elif f == "weekly":
        entries = len({d.isocalendar()[:2] for d in trading})
    elif f == "monthly":
        entries = len({(d.year, d.month) for d in trading})
    else:  # when_flat: one entry per holding period
        hold = req.entry.dte_target - (req.exit.exit_dte or 0)
        if req.exit.max_hold_days:
            hold = min(hold, req.exit.max_hold_days)
        entries = max(1, round(len(trading) / max(3, hold * 0.6 * 5 / 7)))
    legs = len(req.legs)
    requests = entries * (1 + legs) + 2 + len(trading) // 366
    seconds = entries * HISTORY_SECONDS_PER_CHAIN
    if f in ("weekly", "monthly"):
        seconds /= 1.5  # chains for upcoming entry days are fetched in parallel (measured)
    seconds += entries * legs * HISTORY_SECONDS_PER_CONTRACT
    return {"entry_days": entries, "est_requests": requests, "est_minutes": round(seconds / 60 + 0.1, 1),
            "trading_days": len(trading)}


@router.get("/api/backtests/presets")
def presets(request: Request, principal: Principal = Depends(require("backtest"))):
    b = blockers(request)
    return {"presets": [{"key": k, **v} for k, v in PRESETS.items()], "defaults": default_request(),
            "available": not b, "blocked_by": b}


@router.post("/api/backtests/estimate")
def backtest_estimate(req: BacktestRequest, principal: Principal = Depends(require("backtest"))):
    return estimate(req)


@router.post("/api/backtests")
def create_backtest(req: BacktestRequest, request: Request, principal: Principal = Depends(require("backtest"))):
    b = blockers(request)
    if b:
        raise HTTPException(status_code=503, detail={"error": "backtest_unavailable",
                                                     "detail": "Backtesting is not available yet.",
                                                     "blocked_by": b})
    payload = req.model_dump(mode="json")
    raw = json.dumps([principal.user_id, ENGINE_VERSION, payload], sort_keys=True)
    key = "backtest:" + hashlib.sha256(raw.encode()).hexdigest()
    job = queue.enqueue(request.app.state.db, "backtest", key, payload, user_id=principal.user_id, max_attempts=2)
    return {**job.__dict__, "estimate": estimate(req)}


@router.get("/api/backtests")
def list_backtests(request: Request, principal: Principal = Depends(require("backtest"))):
    with request.app.state.db.session() as s:
        jobs = s.execute(select(Job).where(Job.user_id == principal.user_id, Job.kind == "backtest")
                         .order_by(Job.created_at.desc()).limit(RECENT)).scalars().all()
        out = []
        for j in jobs:
            p, r = j.payload or {}, j.result or {}
            summ = r.get("summary") if j.status == "succeeded" else None
            out.append({"id": j.id, "status": j.status, "created_at": j.created_at.isoformat(),
                        "underlying": p.get("underlying"), "strategy": p.get("strategy"),
                        "start": p.get("start"), "end": p.get("end"),
                        "summary": None if not summ else {k: summ.get(k) for k in
                                                          ("total_return", "sharpe", "max_drawdown", "trades")}})
    return {"backtests": out}
