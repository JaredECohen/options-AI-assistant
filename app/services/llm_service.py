from __future__ import annotations

import os
import time

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
        self.max_output_tokens = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "800"))
        self.temperature = float(os.getenv("LLM_TEMPERATURE", "0.2"))
        cache_ttl = int(os.getenv("CACHE_TTL_SECONDS", "900"))
        self.cache = TTLCache(maxsize=256, ttl=cache_ttl)
        self.rate_limiter = RateLimiter(int(os.getenv("RATE_LIMIT_PER_MIN", "30")))

        deterministic = os.getenv("EVAL_DETERMINISTIC", "0") == "1"
        self.provider = HeuristicProvider() if deterministic else build_llm_provider()

    async def generate(self, user_text: str) -> str:
        key = (SYSTEM_PROMPT, user_text, self.max_output_tokens, self.temperature)
        if key in self.cache:
            return self.cache[key]
        if not self.rate_limiter.allow():
            response = (
                "Summary: I can explain strategies and evaluate positions you specify.\n"
                "Setup: Please retry in a moment or provide legs to evaluate.\n"
                "Payoff at Expiration: I compute expiration payoff for specified legs.\n"
                "Max Profit / Max Loss: Computed from specified premiums.\n"
                "Breakeven(s): Computed from specified premiums.\n"
                "Key Sensitivities: I can summarize delta/vega/theta.\n"
                "Typical Use Case: Educational analysis of a known strategy.\n"
                "Main Risks: Options carry risk and time decay.\n"
                "Assumptions / What I need from you: Provide ticker, expiration, strikes, call/put, buy/sell, quantity, and optional premiums."
            )
            return response
        text = await self.provider.generate(
            system=SYSTEM_PROMPT, user=user_text, max_output_tokens=self.max_output_tokens, temperature=self.temperature
        )
        text = ensure_sections(text)
        self.cache[key] = text
        return text
