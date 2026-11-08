"""Builds a ChatService wired the way the characterization snapshots expect:
heuristic text generation, synthetic options data, isolated in-memory history."""
from __future__ import annotations

import os


def build_service():
    os.environ["EVAL_DETERMINISTIC"] = "1"
    from app.models import ChatRequest
    from app.providers.options.mock import MockOptionsProvider
    from app.services.chat_service import ChatService
    from app.services.llm_service import LLMService
    from app.services.memory_store import MemoryStore

    service = ChatService(MockOptionsProvider(), LLMService(), MemoryStore())

    def make_request(turn: dict, session_id: str) -> ChatRequest:
        return ChatRequest(**turn, session_id=session_id)

    return service, make_request
