"""Builds a ChatService wired the way the characterization snapshots expect:
heuristic text generation, synthetic options data, isolated in-memory history."""
from __future__ import annotations


def build_service():
    from app.config import Settings
    from app.llm.gateway import LLMGateway
    from app.models import ChatRequest
    from app.providers.options.mock import MockOptionsProvider
    from app.services.chat_service import ChatService
    from app.services.memory_store import MemoryStore

    gateway = LLMGateway(Settings(app_env="test", llm_provider="heuristic"), db=None)
    service = ChatService(MockOptionsProvider(), gateway, MemoryStore())

    def make_request(turn: dict, session_id: str) -> ChatRequest:
        return ChatRequest(**turn, session_id=session_id)

    return service, make_request
