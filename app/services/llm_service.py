from __future__ import annotations

import logging
import os
import time
import asyncio
from threading import Lock

from cachetools import TTLCache

from app.core.prompts import SYSTEM_PROMPT
from app.core.safety import ensure_sections
from app.providers.llm.factory import build_llm_provider
from app.providers.llm.heuristic import HeuristicProvider


class RateLimiter:
    def __init__(self, max_per_minute: int):
        self.max_per_minute = max_per_minute
        self.calls: list[float] = []

    def allow(self) -> bool:
        now = time.time()
        window_start = now - 60
        self.calls = [t for t in self.calls if t >= window_start]
        if len(self.calls) >= self.max_per_minute:
            return False
        self.calls.append(now)
        return True


class LLMService:
    def __init__(self):
        self.logger = logging.getLogger("app.llm")
        self.max_output_tokens = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "800"))
        self.temperature = float(os.getenv("LLM_TEMPERATURE", "0.2"))
        self.timeout_seconds = float(os.getenv("LLM_TIMEOUT_SECONDS", "12"))
        cache_ttl = int(os.getenv("CACHE_TTL_SECONDS", "900"))
        self.cache = TTLCache(maxsize=256, ttl=cache_ttl)
        self.rate_limiter = RateLimiter(int(os.getenv("RATE_LIMIT_PER_MIN", "30")))

        self.deterministic = os.getenv("EVAL_DETERMINISTIC", "0") == "1"
        configured_provider = os.getenv("LLM_PROVIDER", "vertex").lower()
        self.provider = None
        self.provider_name = "heuristic" if self.deterministic else configured_provider
        self._provider_lock = Lock()
        self.logger.info("LLM provider configured | provider=%s | deterministic=%s", self.provider_name, self.deterministic)

    def _ensure_provider(self):
        if self.provider is not None:
            return self.provider
        with self._provider_lock:
            if self.provider is not None:
                return self.provider
            self.provider = HeuristicProvider() if self.deterministic else build_llm_provider()
            self.provider_name = getattr(self.provider, "name", self.provider_name)
            self.logger.info(
                "LLM provider initialized | provider=%s | deterministic=%s",
                self.provider_name,
                self.deterministic,
            )
            return self.provider

    async def generate(self, user_text: str, ensure_structured_response: bool = True) -> str:
        key = (SYSTEM_PROMPT, user_text, self.max_output_tokens, self.temperature)
        if key in self.cache:
            return self.cache[key]
        if not self.rate_limiter.allow():
            if ensure_structured_response:
                response = (
                    "Summary: I can explain strategies and evaluate positions you specify.\n"
                    "Setup: Please retry in a moment or provide legs to evaluate.\n"
                    "Payoff at Expiration: I compute expiration payoff for specified legs.\n"
                    "Max Profit: Computed from specified premiums when available.\n"
                    "Max Loss: Computed from specified premiums when available.\n"
                    "Breakeven(s): Computed from specified premiums.\n"
                    "Key Sensitivities: I can summarize delta/vega/theta.\n"
                    "Typical Use Case: Educational analysis of a known strategy.\n"
                    "Main Risks: Options carry risk and time decay.\n"
                    "Assumptions / What I need from you: Provide ticker, expiration, strikes, call/put, buy/sell, quantity, and optional premiums."
                )
            else:
                response = "I’m getting too many requests at once. Retry in a moment, or send the exact legs if you want a payoff calculation."
            return response
        provider = self._ensure_provider()
        try:
            text = await asyncio.wait_for(
                provider.generate(
                    system=SYSTEM_PROMPT,
                    user=user_text,
                    max_output_tokens=self.max_output_tokens,
                    temperature=self.temperature,
                ),
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            if getattr(provider, "name", "") != "heuristic":
                self.logger.warning(
                    "LLM provider failed at runtime; falling back to heuristic provider | provider=%s | error=%s",
                    getattr(provider, "name", "unknown"),
                    exc.__class__.__name__,
                )
                self.provider = HeuristicProvider()
                self.provider_name = "heuristic"
                text = await self.provider.generate(
                    system=SYSTEM_PROMPT,
                    user=user_text,
                    max_output_tokens=self.max_output_tokens,
                    temperature=self.temperature,
                )
            else:
                raise
        if ensure_structured_response:
            text = ensure_sections(text)
        self.cache[key] = text
        return text
