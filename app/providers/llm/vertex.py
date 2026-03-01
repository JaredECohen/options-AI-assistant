from __future__ import annotations

import asyncio
import os

from .base import LLMProvider


class VertexProvider:
    name = "vertex"

    def __init__(self, project_id: str | None = None, location: str | None = None, model: str | None = None):
        self.project_id = project_id or os.getenv("VERTEX_PROJECT_ID")
        self.location = location or os.getenv("VERTEX_LOCATION")
        self.model = model or os.getenv("VERTEX_MODEL", "gemini-2.5-flash-lite")
        if not self.project_id or not self.location:
            raise RuntimeError("Missing Vertex configuration")
        try:
            import vertexai  # noqa: F401
        except Exception as exc:
            raise RuntimeError("Vertex SDK not available") from exc

    def _generate_sync(self, system: str, user: str, max_output_tokens: int, temperature: float) -> str:
        import vertexai
        from vertexai.generative_models import GenerationConfig, GenerativeModel

        vertexai.init(project=self.project_id, location=self.location)
        model = GenerativeModel(self.model)
        prompt = f"{system}\n\nUser: {user}"
        contents = [
            {"role": "user", "parts": [{"text": prompt}]},
        ]
        response = model.generate_content(
            contents=contents,
            generation_config=GenerationConfig(
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                top_p=0.8,
            ),
        )
        return response.text or ""

    async def generate(self, system: str, user: str, max_output_tokens: int, temperature: float) -> str:
        return await asyncio.to_thread(self._generate_sync, system, user, max_output_tokens, temperature)


def build_vertex_provider() -> LLMProvider:
    return VertexProvider()
