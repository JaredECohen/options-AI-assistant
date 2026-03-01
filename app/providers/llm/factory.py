import os

from .base import LLMProvider
from .heuristic import HeuristicProvider
from .vertex import VertexProvider


def build_llm_provider() -> LLMProvider:
    provider = os.getenv("LLM_PROVIDER", "vertex").lower()
    if provider == "vertex":
        try:
            return VertexProvider()
        except Exception:
            return HeuristicProvider()
    return HeuristicProvider()
