"""LLM gateway: the only path from OptionsCoach to a model.

Responsibilities, in order, for every model call:
1. Configuration: LLM_PROVIDER decides anthropic / heuristic / disabled. There is
   no silent downgrade. A heuristic answer is produced only when it is the
   configured provider (dev/test/DEMO_MODE, labelled heuristic_demo) or the
   explicitly configured LLM_FALLBACK while the circuit breaker is open
   (labelled heuristic_fallback). The breaker half-opens after the cooldown, so
   the downgrade is never permanent.
2. Admission: per-user request rate, per-user and global daily token budgets
   (summed from the llm_usage_events table, so they hold across processes).
3. The call: official Anthropic SDK, bounded timeout and retries, effort set
   explicitly, stable cached system prompt, strict tools with a bounded number
   of tool rounds.
4. Outcome handling: refusal, truncation (max_tokens), unexpected stop reasons
   and SDK errors become LLMUnavailable with a safe code; partial text is never
   returned as if complete.
5. Accounting: one usage row per call (tokens, cache tokens, model requested and
   served, outcome, estimated cost, pricing version) and a structured log line
   with correlation id and revision. Prompts, answers, positions and research
   text are never logged.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import anthropic

from app.config import Settings
from app.core.prompts import SYSTEM_PROMPT
from app.core.safety import ensure_sections
from app.db import repo
from app.db.session import Database
from app.llm.pricing import PRICING_VERSION, estimate_cost
from app.llm.types import Attribution, LLMUnavailable, current_attribution
from app.providers.llm.heuristic import HeuristicProvider

log = logging.getLogger("app.llm")

TRANSPORT_FAILURES = {"rate_limited", "timeout", "upstream_error", "connection"}


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    run: Callable[[dict], Awaitable[dict]]

    def api_shape(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema, "strict": True}


@dataclass
class TurnContext:
    """Per-request facts the gateway needs. Set by the route, read via contextvar."""

    user_id: int | None
    correlation_id: str
    tools: list[ToolSpec] = field(default_factory=list)
    validator: Callable[[str, list[dict]], str | None] | None = None


current_turn: contextvars.ContextVar[TurnContext | None] = contextvars.ContextVar("oc_turn", default=None)


class CircuitBreaker:
    """Process-local breaker. It protects this process's outbound calls only;
    budgets and rate limits (which must be global) live in the database."""

    def __init__(self, failures: int, cooldown: float, clock=time.monotonic):
        self.threshold = failures
        self.cooldown = cooldown
        self.clock = clock
        self.consecutive = 0
        self.opened_at: float | None = None

    def allow(self) -> bool:
        if self.opened_at is None:
            return True
        return self.clock() - self.opened_at >= self.cooldown  # half-open trial

    def success(self) -> None:
        self.consecutive = 0
        self.opened_at = None

    def failure(self) -> None:
        self.consecutive += 1
        if self.consecutive >= self.threshold:
            self.opened_at = self.clock()

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        return "half_open" if self.allow() else "open"


def classify_sdk_error(exc: Exception) -> str:
    if isinstance(exc, anthropic.RateLimitError):
        return "rate_limited"
    if isinstance(exc, anthropic.APITimeoutError):
        return "timeout"
    if isinstance(exc, anthropic.APIConnectionError):
        return "connection"
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return "auth"
    if isinstance(exc, anthropic.BadRequestError):
        return "bad_request"
    if isinstance(exc, anthropic.APIStatusError):
        return "upstream_error"
    return "upstream_error"


class LLMGateway:
    def __init__(self, settings: Settings, db: Database | None, client: anthropic.AsyncAnthropic | None = None):
        self.settings = settings
        self.db = db
        self.provider_name = settings.llm_provider
        self.breaker = CircuitBreaker(settings.llm_circuit_failures, settings.llm_circuit_cooldown_seconds)
        self.heuristic = HeuristicProvider()
        self._client = client
        # Exposed for /ready and the legacy llm_status endpoint.
        self.deterministic = settings.llm_provider == "heuristic"

    # ------------------------------------------------------------------ public

    def status(self) -> dict:
        return {
            "provider": self.provider_name,
            "model": self.settings.anthropic_model if self.provider_name == "anthropic" else None,
            "configured": self.provider_name != "anthropic" or bool(self.settings.anthropic_api_key),
            "fallback": self.settings.llm_fallback,
            "circuit": self.breaker.state,
            "pricing_version": PRICING_VERSION,
        }

    async def generate(self, user_text: str, ensure_structured_response: bool = True) -> str:
        attribution = current_attribution.get()
        if attribution is None:
            attribution = Attribution()
            current_attribution.set(attribution)
        turn = current_turn.get() or TurnContext(user_id=None, correlation_id="")

        if self.provider_name == "disabled":
            raise LLMUnavailable("disabled")
        if self.provider_name == "heuristic":
            return await self._heuristic(user_text, ensure_structured_response, attribution, "heuristic_demo")
        if not self.settings.anthropic_api_key:
            raise LLMUnavailable("not_configured")
        if not self.breaker.allow():
            if self.settings.llm_fallback == "heuristic":
                attribution.fallback_reason = "circuit_open"
                return await self._heuristic(user_text, ensure_structured_response, attribution, "heuristic_fallback")
            raise LLMUnavailable("circuit_open")
        reservation = await asyncio.to_thread(self._admit, turn)

        try:
            text = await self._anthropic_turn(user_text, turn, attribution)
        except LLMUnavailable as exc:
            if exc.code in TRANSPORT_FAILURES:
                self.breaker.failure()
                if self.settings.llm_fallback == "heuristic":
                    attribution.fallback_reason = exc.code
                    return await self._heuristic(user_text, ensure_structured_response, attribution, "heuristic_fallback")
            raise
        finally:
            if reservation is not None:
                await asyncio.to_thread(repo.finish_turn, self.db, reservation)
        self.breaker.success()
        return ensure_sections(text) if ensure_structured_response else text

    async def extract(self, instruction: str, user_text: str, tool_name: str, schema: dict,
                      turn: TurnContext | None = None, max_tokens: int = 600) -> dict | None:
        """One forced, strict tool call that returns structured fields (e.g. a trading
        view parsed from a sentence). Metered and budgeted like a chat turn. Returns
        None whenever no model can answer (heuristic/disabled provider, no key, open
        circuit, budget refusal, transport failure) so callers fall back to rules."""
        if self.provider_name != "anthropic" or not self.settings.anthropic_api_key or not self.breaker.allow():
            return None
        turn = turn or current_turn.get() or TurnContext(user_id=None, correlation_id="")
        try:
            reservation = await asyncio.to_thread(self._admit, turn)
        except LLMUnavailable:
            return None
        model = self.settings.anthropic_model
        try:
            resp = await self.client.messages.create(
                model=model, max_tokens=max_tokens, system=instruction,
                messages=[{"role": "user", "content": user_text}],
                tools=[{"name": tool_name, "description": "Record the parsed fields.", "input_schema": schema,
                        "strict": True}],
                tool_choice={"type": "tool", "name": tool_name},
            )
        except Exception as exc:  # noqa: BLE001 - mapped to a safe code; caller falls back
            code = classify_sdk_error(exc)
            self._record(turn, model, "", f"error:{code}", None)
            if code in TRANSPORT_FAILURES:
                self.breaker.failure()
            return None
        finally:
            if reservation is not None:
                await asyncio.to_thread(repo.finish_turn, self.db, reservation)
        self.breaker.success()
        self._record(turn, model, getattr(resp, "model", "") or model, "ok", resp.usage)
        for block in resp.content:
            if getattr(block, "type", "") == "tool_use" and block.name == tool_name:
                return dict(block.input or {})
        return None

    # ------------------------------------------------------------------ internals

    async def _heuristic(self, user_text: str, structured: bool, attribution: Attribution, mode: str) -> str:
        attribution.mode = mode
        attribution.provider = "heuristic"
        attribution.model_requested = ""  # never attribute canned text to the model that failed
        attribution.model_served = ""
        text = await self.heuristic.generate(system=SYSTEM_PROMPT, user=user_text, max_output_tokens=0, temperature=0.0)
        return ensure_sections(text) if structured else text

    def _admit(self, turn: TurnContext) -> int | None:
        """Reserve this turn atomically (see repo.reserve_turn). The estimate is
        the worst case for one call times the allowed tool rounds."""
        if self.db is None:
            return None
        s = self.settings
        estimate = s.llm_max_output_tokens * (s.llm_max_tool_rounds + 1)
        try:
            return repo.reserve_turn(self.db, turn.user_id, estimate_tokens=estimate, rate_per_min=s.rate_limit_per_min,
                                     user_budget=s.llm_user_daily_token_budget, global_budget=s.llm_global_daily_token_budget,
                                     correlation_id=turn.correlation_id)
        except repo.AdmissionDenied as exc:
            raise LLMUnavailable(exc.code) from exc

    @property
    def client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            self._client = anthropic.AsyncAnthropic(
                api_key=self.settings.anthropic_api_key,
                timeout=self.settings.llm_timeout_seconds,
                max_retries=self.settings.llm_max_retries,
            )
        return self._client

    def _record(self, turn: TurnContext, model: str, served: str, outcome: str, usage: Any) -> None:
        inp = int(getattr(usage, "input_tokens", 0) or 0) if usage else 0
        out = int(getattr(usage, "output_tokens", 0) or 0) if usage else 0
        cread = int(getattr(usage, "cache_read_input_tokens", 0) or 0) if usage else 0
        cwrite = int(getattr(usage, "cache_creation_input_tokens", 0) or 0) if usage else 0
        cost = estimate_cost(served or model, inp, out, cread, cwrite)
        if self.db is not None:
            repo.record_usage(
                self.db, user_id=turn.user_id, correlation_id=turn.correlation_id, provider="anthropic",
                model_requested=model, model_served=served, outcome=outcome, input_tokens=inp, output_tokens=out,
                cache_read_tokens=cread, cache_write_tokens=cwrite, est_cost_usd=cost, pricing_version=PRICING_VERSION,
            )
        log.info(
            "llm_call %s",
            json.dumps({
                "correlation_id": turn.correlation_id, "revision": self.settings.release_revision,
                "provider": "anthropic", "model_requested": model, "model_served": served, "outcome": outcome,
                "input_tokens": inp, "output_tokens": out, "cache_read_tokens": cread, "cache_write_tokens": cwrite,
                "est_cost_usd": cost, "pricing_version": PRICING_VERSION, "breaker": self.breaker.state,
            }),
        )

    async def _call(self, turn: TurnContext, model: str, messages: list, tools: list[ToolSpec]):
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": self.settings.llm_max_output_tokens,
            "system": [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            "messages": messages,
            "output_config": {"effort": self.settings.llm_effort},
        }
        if tools:
            params["tools"] = [t.api_shape() for t in tools]
        started = time.monotonic()
        try:
            resp = await self.client.messages.create(**params)
        except Exception as exc:  # noqa: BLE001 - mapped to a safe code below
            code = classify_sdk_error(exc)
            self._record(turn, model, "", f"error:{code}", None)
            raise LLMUnavailable(code) from exc
        attribution = current_attribution.get()
        if attribution is not None:
            attribution.latency_ms += int((time.monotonic() - started) * 1000)
        return resp

    async def _anthropic_turn(self, user_text: str, turn: TurnContext, attribution: Attribution) -> str:
        model = self.settings.anthropic_model
        attribution.mode = "llm"
        attribution.provider = "anthropic"
        attribution.model_requested = model
        text = await self._tool_loop(model, user_text, turn, attribution)
        problem = turn.validator(text, attribution.__dict__.get("_tool_results", [])) if turn.validator else None
        if problem and self.settings.llm_escalation_enabled and self.settings.anthropic_escalation_model != model:
            # "Difficult case" = the economical model's answer failed numerical validation.
            esc = self.settings.anthropic_escalation_model
            attribution.escalated = True
            attribution.model_requested = esc
            text = await self._tool_loop(esc, user_text, turn, attribution)
            problem = turn.validator(text, attribution.__dict__.get("_tool_results", [])) if turn.validator else None
        if problem:
            raise LLMUnavailable("invalid_output", problem)
        return text

    async def _tool_loop(self, model: str, user_text: str, turn: TurnContext, attribution: Attribution) -> str:
        messages: list[dict] = [{"role": "user", "content": user_text}]
        tools = turn.tools
        tool_results: list[dict] = []
        attribution.__dict__["_tool_results"] = tool_results
        by_name = {t.name: t for t in tools}
        for _round in range(self.settings.llm_max_tool_rounds + 1):
            resp = await self._call(turn, model, messages, tools)
            served = getattr(resp, "model", "") or model
            attribution.model_served = served
            attribution.finish_reason = resp.stop_reason or ""
            usage = resp.usage
            attribution.input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
            attribution.output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
            attribution.cache_read_tokens += int(getattr(usage, "cache_read_input_tokens", 0) or 0)
            stop = resp.stop_reason
            if stop == "refusal":
                self._record(turn, model, served, "refusal", usage)
                category = getattr(getattr(resp, "stop_details", None), "category", None)
                raise LLMUnavailable("refusal", str(category or ""))
            if stop == "max_tokens":
                self._record(turn, model, served, "truncated", usage)
                raise LLMUnavailable("truncated")
            if stop == "tool_use":
                self._record(turn, model, served, "tool_use", usage)
                messages.append({"role": "assistant", "content": resp.content})
                results = []
                for block in resp.content:
                    if getattr(block, "type", "") != "tool_use":
                        continue
                    tool = by_name.get(block.name)
                    attribution.tool_calls.append(block.name)
                    if tool is None:
                        results.append({"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                                        "content": "unknown tool"})
                        continue
                    try:
                        output = await asyncio.wait_for(tool.run(dict(block.input or {})), timeout=15)
                        tool_results.append({"tool": block.name, "output": output})
                        results.append({"type": "tool_result", "tool_use_id": block.id,
                                        "content": json.dumps(output, default=str)[:20_000]})
                    except Exception as exc:  # noqa: BLE001 - reported to the model as a tool error
                        results.append({"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                                        "content": f"tool failed: {getattr(exc, 'code', exc.__class__.__name__)}"})
                messages.append({"role": "user", "content": results})
                continue
            if stop not in ("end_turn", "stop_sequence"):
                self._record(turn, model, served, f"error:stop_{stop}", usage)
                raise LLMUnavailable("invalid_output", f"unexpected stop reason {stop}")
            self._record(turn, model, served, "ok", usage)
            text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text").strip()
            if not text:
                raise LLMUnavailable("invalid_output", "empty answer")
            return text
        self._record(turn, model, attribution.model_served, "error:tool_rounds", None)
        raise LLMUnavailable("invalid_output", "too many tool rounds")
