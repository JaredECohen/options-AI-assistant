from __future__ import annotations

from typing import Protocol


class LLMProvider(Protocol):
    name: str

    async def generate(self, system: str, user: str, max_output_tokens: int, temperature: float) -> str:
        ...
