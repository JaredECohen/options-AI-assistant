from __future__ import annotations

import json
from typing import Optional

from app.core.payoff import compute_payoff, resolve_premiums
from app.core.safety import (
    apply_backstop,
    ensure_sections,
    guarantee_misconception_template,
    illegal_refusal_template,
    is_illegal_request,
    is_strike_request,
    needs_refusal,
    safe_refusal_template,
    strike_refusal_template,
)
from app.core.strategies import get_strategy, normalize_strategy_name
from app.models import ChatRequest, ChatResponse, OptionLeg, StockLeg
from app.providers.options.base import OptionsDataProvider
from app.services.llm_service import LLMService


class ChatService:
    def __init__(self, options_provider: OptionsDataProvider, llm_service: LLMService):
        self.options_provider = options_provider
        self.llm_service = llm_service

    async def handle(self, req: ChatRequest) -> ChatResponse:
        user_text = req.message or ""

        if req.legs:
            return await self._handle_payoff(req)

        if req.strategy and req.ticker and req.view:
            return await self._handle_strategy_builder(req)

        if "guaranteed profit" in user_text.lower():
            return ChatResponse(response_text=guarantee_misconception_template())

        if is_illegal_request(user_text):
            return ChatResponse(response_text=illegal_refusal_template())

        if is_strike_request(user_text):
            return ChatResponse(response_text=strike_refusal_template())

        if needs_refusal(user_text):
            return ChatResponse(response_text=safe_refusal_template())

        response_text = await self.llm_service.generate(user_text)
        response_text = apply_backstop(response_text)
        response_text = ensure_sections(response_text)
        return ChatResponse(response_text=response_text)

    async def _handle_payoff(self, req: ChatRequest) -> ChatResponse:
        ticker = (req.ticker or "").upper().strip()
        if not ticker:
            return ChatResponse(response_text=ensure_sections("Please provide a ticker to fetch premiums."))

        legs = req.legs or []
        resolved_legs, premiums_used, underlying = await resolve_premiums(
            ticker, legs, self.options_provider
        )
        computed = compute_payoff(
            ticker,
            resolved_legs,
            premiums_used,
            underlying,
            quote_source=self.options_provider.name,
        )

        response_text = render_payoff_response(computed)
        response_text = apply_backstop(response_text)
        return ChatResponse(response_text=response_text, computed=computed)

    async def _handle_strategy_builder(self, req: ChatRequest) -> ChatResponse:
        strategy_name = normalize_strategy_name(req.strategy or "")
        strat = get_strategy(strategy_name)
        if not strat:
            return ChatResponse(
                response_text=ensure_sections(
                    "I can explain listed equity options strategies and evaluate specified legs. Please choose a supported strategy."
                )
            )
        response_text = (
            f"Summary: {strat['summary']}\n"
            f"Setup: {strat['setup']}\n"
            f"Payoff at Expiration: {strat['payoff']}\n"
            f"Max Profit / Max Loss: {strat['max_profit']} / {strat['max_loss']}\n"
            f"Breakeven(s): {strat['breakevens']}\n"
            f"Key Sensitivities: {strat['key_sensitivities']}\n"
            f"Typical Use Case: {strat['typical_use_case']}\n"
            f"Main Risks: {strat['main_risks']}\n"
            "Assumptions / What I need from you: Use the chain picker to select an expiration and strikes for your chosen strategy, then add the legs to evaluate."
        )
        return ChatResponse(response_text=response_text)


def render_payoff_response(computed: dict) -> str:
    c = computed["computed"]
    net = c["net_debit"] if c["net_debit"] > 0 else c["net_credit"]
    net_label = "net debit" if c["net_debit"] > 0 else "net credit"
    max_profit = c["max_profit"]
    max_loss = c["max_loss"]
    breakevens = ", ".join([str(b) for b in c["breakevens"]]) or "N/A"

    summary = f"Computed payoff at expiration using {c['quote_source']} premiums."
    setup = f"Net {net_label}: {net}. Underlying price used: {c['underlying_price']}."
    payoff = "Payoff curve computed at expiration across a price grid."
    max_pl = f"Max profit: {max_profit}. Max loss: {max_loss}."
    be = f"Breakeven(s): {breakevens}."
    sensitivities = "Expiration payoff only; greek sensitivities are structural (delta/vega/theta)."
    use_case = "Use this to evaluate a user-specified position with live premiums."
    risks = "Results ignore early exercise, dividends, and transaction costs."
    assumptions = "Assumes listed equity options, contract multiplier of 100, and expiration payoff only."

    json_block = json.dumps(computed, indent=2)

    return (
        f"Summary: {summary}\n"
        f"Setup: {setup}\n"
        f"Payoff at Expiration: {payoff}\n"
        f"Max Profit / Max Loss: {max_pl}\n"
        f"Breakeven(s): {be}\n"
        f"Key Sensitivities: {sensitivities}\n"
        f"Typical Use Case: {use_case}\n"
        f"Main Risks: {risks}\n"
        f"Assumptions / What I need from you: {assumptions}\n\n"
        "Computed JSON:\n"
        f"```json\n{json_block}\n```"
    )
