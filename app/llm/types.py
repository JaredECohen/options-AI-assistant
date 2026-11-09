"""Shared LLM result/error types and per-turn attribution."""
from __future__ import annotations

import contextvars
from dataclasses import dataclass, field


class LLMUnavailable(Exception):
    """The model could not produce a usable answer. `code` is safe to show and log."""

    MESSAGES = {
        "not_configured": "The AI assistant is not configured on this deployment.",
        "disabled": "The AI assistant is turned off on this deployment.",
        "rate_limited": "The AI provider is rate limiting requests. Please try again shortly.",
        "timeout": "The AI provider timed out. Please try again.",
        "upstream_error": "The AI provider returned an error. Please try again.",
        "connection": "The AI provider could not be reached. Please try again.",
        "circuit_open": "The AI assistant is temporarily paused after repeated provider errors.",
        "budget_user": "You have reached today's AI usage limit. Calculators and data views still work.",
        "budget_global": "The AI assistant has reached today's usage limit. Calculators and data views still work.",
        "too_many_requests": "Too many messages in a short time. Please wait a moment.",
        "truncated": "The answer was cut off before it finished. Try a narrower question.",
        "refusal": "The AI model declined this request.",
        "invalid_output": "The AI answer failed validation and was not shown.",
        "bad_request": "The AI request was rejected; this has been logged.",
        "auth": "The AI provider rejected this deployment's credentials.",
    }

    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.detail = detail

    @property
    def user_message(self) -> str:
        return self.MESSAGES.get(self.code, "The AI assistant is unavailable.")

    @property
    def http_status(self) -> int:
        return {"budget_user": 429, "budget_global": 429, "too_many_requests": 429, "refusal": 422,
                "invalid_output": 502, "truncated": 502}.get(self.code, 503)


@dataclass
class Attribution:
    mode: str = "deterministic"  # deterministic | llm | heuristic_demo | heuristic_fallback
    provider: str = ""
    model_requested: str = ""
    model_served: str = ""
    finish_reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    retries: int = 0
    tool_calls: list[str] = field(default_factory=list)
    fallback_reason: str = ""
    escalated: bool = False
    latency_ms: int = 0

    def public(self) -> dict:
        return {
            "mode": self.mode,
            "provider": self.provider or None,
            "model": self.model_served or self.model_requested or None,
            "fallback_reason": self.fallback_reason or None,
            "tools_used": self.tool_calls,
            "escalated": self.escalated,
        }


current_attribution: contextvars.ContextVar[Attribution | None] = contextvars.ContextVar("oc_attribution", default=None)
