from __future__ import annotations

import os

import httpx

from .base import LLMProvider


class AnthropicProvider:
    name = "claude"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
        if not self.api_key:
            raise RuntimeError("Missing Anthropic API key")

    async def generate(self, system: str, user: str, max_output_tokens: int, temperature: float) -> str:
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": self.model,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "max_tokens": max_output_tokens,
            "temperature": temperature,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        parts = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts).strip()


def build_anthropic_provider() -> LLMProvider:
    return AnthropicProvider()
