"""ChatService orchestrator: routes a turn to payoff, builder, deterministic answer, refusal or LLM."""
from __future__ import annotations

from app.core.payoff import compute_payoff
from app.core.safety import apply_backstop
from app.core.safety import ensure_sections
from app.core.safety import illegal_refusal_template
from app.core.safety import is_illegal_request
from app.core.safety import off_topic_template
from app.core.strategies import get_strategy
from app.core.strategies import normalize_strategy_name
from app.models import ChatRequest
from app.models import ChatResponse
from app.models import OptionLeg
from app.models import StockLeg
from app.providers.options.base import OptionsDataProvider
from app.services.llm_service import LLMService
from app.services.memory_store import MemoryStore
import json
import logging
from app.chat.answers import answer_known_question
from app.chat.context import _summarize_user_turn, _truncate, build_context_message, normalize_legs, strip_json_block
from app.chat.intents import is_options_related, is_payoff_intent
from app.chat.payoff_view import attach_ticker_context, render_payoff_response
from app.chat.render_freeform import illegal_refusal_freeform, off_topic_freeform, to_freeform
from app.chat.render_structured import ensure_sentence, format_view_menu


class ChatService:
    def __init__(self, options_provider: OptionsDataProvider, llm_service: LLMService, memory_store: MemoryStore):
        self.options_provider = options_provider
        self.llm_service = llm_service
        self.memory_store = memory_store
        self.logger = logging.getLogger("app.chat")

    def _log(self, msg: str, **fields) -> None:
        if fields:
            self.logger.info("%s | %s", msg, json.dumps(fields, default=str))
        else:
            self.logger.info(msg)

    async def handle(self, req: ChatRequest) -> ChatResponse:
        user_text = req.message or ""
        mode = req.mode or "structured"
        session_id = (req.session_id or "default").strip()
        history = self.memory_store.get(session_id)
        self._log(
            "Chat request",
            session_id=session_id,
            mode=mode,
            has_legs=bool(req.legs),
            view=req.view,
            strategy=req.strategy,
            user_message=_truncate(user_text),
        )

        if is_payoff_intent(user_text, has_legs=bool(req.legs)):
            self._log("Routing to payoff", session_id=session_id)
            response = await self._handle_payoff(req)
            response.response_text = attach_ticker_context(response.response_text, req.ticker, req.mode or "structured")
            self._record_turn(session_id, user_text, response.response_text, req, kind="payoff")
            return response

        if mode == "structured" and not user_text.strip():
            self._log("Strategy builder selection", session_id=session_id)
            response = await self._handle_strategy_builder(req)
            if mode == "freeform":
                response.response_text = to_freeform(response.response_text)
            response.response_text = attach_ticker_context(response.response_text, req.ticker, mode)
            self._record_turn(session_id, user_text, response.response_text, req, kind="strategy")
            return response

        if is_illegal_request(user_text):
            self._log("Illegal request", session_id=session_id)
            response_text = illegal_refusal_freeform() if mode == "freeform" else illegal_refusal_template()
            self._record_turn(session_id, user_text, response_text, req, kind="refusal")
            return ChatResponse(response_text=response_text)

        direct_response = answer_known_question(
            user_text,
            mode,
            selected_view=req.view,
            selected_strategy=req.strategy,
        )
        if direct_response:
            self._log("Direct answer", session_id=session_id)
            direct_response = attach_ticker_context(direct_response, req.ticker, mode)
            self._record_turn(session_id, user_text, direct_response, req, kind="answer")
            return ChatResponse(response_text=direct_response)

        if not is_options_related(user_text) and not history:
            self._log("Off-topic request", session_id=session_id)
            if mode == "freeform":
                return ChatResponse(response_text=off_topic_freeform(user_text))
            response_text = off_topic_template()
            return ChatResponse(response_text=response_text)

        response_text = await self.llm_service.generate(
            build_context_message(user_text, req, history),
            ensure_structured_response=mode != "freeform",
        )
        response_text = apply_backstop(response_text)
        if mode == "freeform":
            response_text = to_freeform(response_text)
        response_text = attach_ticker_context(response_text, req.ticker, mode)
        self._record_turn(session_id, user_text, response_text, req, kind="answer")
        self._log("Chat response", session_id=session_id, length=len(response_text))
        return ChatResponse(response_text=response_text)

    async def _handle_payoff(self, req: ChatRequest) -> ChatResponse:
        ticker = (req.ticker or "").upper().strip() or "UNKNOWN"

        legs = normalize_legs(req.legs or [])
        if not legs:
            return ChatResponse(
                response_text=ensure_sections(
                    "Please provide the position legs (option/stock, side, strike, expiration, quantity, and optional premium) so I can compute payoff."
                )
            )
        premiums_missing = any(l.premium is None for l in legs)
        self._log(
            "Payoff request",
            ticker=ticker,
            legs=len(legs),
            premiums_missing=premiums_missing,
            session_id=req.session_id or "default",
        )
        resolved_legs = []
        premiums_used = []
        for leg in legs:
            assumed_premium = leg.premium if leg.premium is not None else 0.0
            premiums_used.append(
                {**leg.model_dump(), "assumed_premium": assumed_premium}
            )
            if isinstance(leg, OptionLeg):
                resolved_legs.append(OptionLeg(**{**leg.model_dump(), "premium": assumed_premium}))
            else:
                resolved_legs.append(StockLeg(**{**leg.model_dump(), "premium": assumed_premium}))

        strikes = [l.strike for l in legs if isinstance(l, OptionLeg)]
        if strikes:
            underlying = round(sum(strikes) / len(strikes), 2)
        else:
            stock_premiums = [l.premium for l in legs if isinstance(l, StockLeg) and l.premium is not None]
            if stock_premiums:
                underlying = round(sum(stock_premiums) / len(stock_premiums), 2)
            else:
                underlying = 1.0
        computed = compute_payoff(
            ticker,
            resolved_legs,
            premiums_used,
            underlying,
            quote_source="user_provided",
        )
        computed["computed"]["premiums_missing"] = premiums_missing
        if premiums_missing:
            computed["computed"]["premium_note"] = (
                "One or more premiums were missing; payoff assumes 0 premium for those legs."
            )
        if not strikes:
            computed["computed"]["underlying_price_note"] = (
                "Underlying price not provided; inferred from stock leg premiums when available, otherwise assumed 1.0."
            )

        response_text = render_payoff_response(computed)
        response_text = apply_backstop(response_text)
        if (req.mode or "structured") == "freeform":
            response_text = to_freeform(response_text, keep_json=True)
        return ChatResponse(response_text=response_text, computed=computed)

    async def _handle_strategy_builder(self, req: ChatRequest) -> ChatResponse:
        strategy_name = normalize_strategy_name(req.strategy or "")
        if strategy_name:
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
                f"Max Profit: {ensure_sentence(strat['max_profit'])}\n"
                f"Max Loss: {ensure_sentence(strat['max_loss'])}\n"
                f"Breakeven(s): {strat['breakevens']}\n"
                f"Key Sensitivities: {strat['key_sensitivities']}\n"
                f"Typical Use Case: {strat['typical_use_case']}\n"
                f"Main Risks: {strat['main_risks']}\n"
                "Assumptions / What I need from you: If you want payoff calculations, share ticker, legs, and optional premiums."
            )
            return ChatResponse(response_text=response_text)

        if req.view:
            response_text = format_view_menu(req.view)
            return ChatResponse(response_text=response_text)

        return ChatResponse(
            response_text=ensure_sections(
                "Select a strategy or a market view to get an explanation."
            )
        )

    def _record_turn(self, session_id: str, user_text: str, response_text: str, req: ChatRequest, kind: str) -> None:
        if kind == "off_topic":
            return
        if not session_id:
            return
        user_turn = _summarize_user_turn(req, user_text)
        assistant_turn = strip_json_block(to_freeform(response_text)).strip()
        history = self.memory_store.get(session_id)
        history.extend(
            [
                {"role": "user", "text": user_turn},
                {"role": "assistant", "text": assistant_turn},
            ]
        )
        history = history[-self.memory_store.max_turns * 2 :]
        self.memory_store.set(session_id, history)
