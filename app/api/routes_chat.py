"""Chat: authenticated, conversation-scoped, attributed."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from app.auth.deps import Principal, require
from app.db import repo
from app.llm.gateway import TurnContext, current_turn
from app.llm.types import Attribution, current_attribution
from app.models import ChatRequest, ChatResponse, ClearRequest
from app.services.chat_service import ChatService

router = APIRouter()


@router.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request, principal: Principal = Depends(require("chat"))):
    state = request.app.state
    db = state.db
    if req.conversation_id:
        await run_in_threadpool(repo.require_conversation, db, principal.user_id, req.conversation_id)
        conversation_id = req.conversation_id
    else:
        conversation_id = await run_in_threadpool(repo.create_conversation, db, principal.user_id)
    tools, validator = [], None
    if state.settings.llm_provider == "anthropic":
        from app.llm.tools import build_tools, numbers_validator

        tools = build_tools(principal, state)
        validator = numbers_validator
    turn_token = current_turn.set(TurnContext(user_id=principal.user_id, correlation_id=request.state.correlation_id,
                                              tools=tools, validator=validator))
    attribution = Attribution()
    attr_token = current_attribution.set(attribution)
    try:
        history = repo.DurableHistory(db, principal.user_id)
        service = ChatService(state.options_provider, state.llm, history)
        turn = req.model_copy(update={"session_id": conversation_id})
        response = await service.handle(turn)
    finally:
        current_turn.reset(turn_token)
        current_attribution.reset(attr_token)
    response.conversation_id = conversation_id
    response.served_by = attribution.public()
    response.correlation_id = request.state.correlation_id
    return response


@router.post("/api/clear")
def clear(req: ClearRequest, request: Request, principal: Principal = Depends(require("chat"))):
    repo.DurableHistory(request.app.state.db, principal.user_id).clear(req.conversation_id)
    return {"status": "ok"}
