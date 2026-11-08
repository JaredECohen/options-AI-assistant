"""User-owned state: conversations, positions, export and deletion."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app.auth.deps import Principal, authenticate, require
from app.db import repo
from app.models import Leg

router = APIRouter()


@router.get("/api/conversations")
def list_conversations(request: Request, limit: int = Query(20, ge=1, le=100), before: str | None = Query(None, max_length=80),
                       principal: Principal = Depends(require("chat"))):
    return repo.list_conversations(request.app.state.db, principal.user_id, limit, before)


@router.get("/api/conversations/{conversation_id}/messages")
def conversation_messages(conversation_id: str, request: Request, principal: Principal = Depends(require("chat"))):
    return {"conversation_id": conversation_id,
            "messages": repo.conversation_messages(request.app.state.db, principal.user_id, conversation_id)}


@router.delete("/api/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, request: Request, principal: Principal = Depends(require("chat"))):
    repo.delete_conversation(request.app.state.db, principal.user_id, conversation_id)
    return {"status": "deleted"}


@router.get("/api/me/export")
def export_me(request: Request, principal: Principal = Depends(authenticate)):
    """Everything OptionsCoach stores for the caller. Requires only a valid
    identity (not beta/plan), so a revoked user can still take their data."""
    return repo.export_user_data(request.app.state.db, principal.user_id)


@router.delete("/api/me/data")
def delete_me(request: Request, principal: Principal = Depends(authenticate)):
    repo.delete_user_data(request.app.state.db, principal.user_id)
    return {"status": "deleted"}


class PositionIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    ticker: str = Field(pattern=r"^[A-Za-z.]{1,10}$")
    legs: list[Leg] = Field(min_length=1, max_length=8)
    notes: str = Field(default="", max_length=2000)


@router.get("/api/positions")
def list_positions(request: Request, principal: Principal = Depends(require("payoff"))):
    return {"items": repo.list_positions(request.app.state.db, principal.user_id)}


@router.post("/api/positions")
def create_position(body: PositionIn, request: Request, principal: Principal = Depends(require("payoff"))):
    return repo.save_position(request.app.state.db, principal.user_id, body.name, body.ticker.upper(),
                              [leg.model_dump() for leg in body.legs], body.notes)


@router.put("/api/positions/{position_id}")
def update_position(position_id: str, body: PositionIn, request: Request, principal: Principal = Depends(require("payoff"))):
    return repo.save_position(request.app.state.db, principal.user_id, body.name, body.ticker.upper(),
                              [leg.model_dump() for leg in body.legs], body.notes, position_id=position_id)


@router.delete("/api/positions/{position_id}")
def delete_position(position_id: str, request: Request, principal: Principal = Depends(require("payoff"))):
    repo.delete_position(request.app.state.db, principal.user_id, position_id)
    return {"status": "deleted"}
