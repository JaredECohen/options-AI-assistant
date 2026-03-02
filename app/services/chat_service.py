from __future__ import annotations

import json
import logging
from typing import Optional

from app.core.payoff import compute_payoff
from app.core.safety import (
    apply_backstop,
    ensure_sections,
    guarantee_misconception_template,
    illegal_refusal_template,
    is_illegal_request,
    is_strike_request,
    is_trade_recommendation_request,
    needs_refusal,
    off_topic_template,
    safe_refusal_template,
    strike_refusal_template,
)
from app.core.strategies import STRATEGIES, ALIASES, get_strategy, normalize_strategy_name
from app.models import ChatRequest, ChatResponse, OptionLeg, StockLeg
from app.providers.options.base import OptionsDataProvider
from app.services.llm_service import LLMService
from app.services.memory_store import MemoryStore


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
            self._record_turn(session_id, user_text, response.response_text, req, kind="payoff")
            return response

        if (req.strategy or req.view) and not user_text.strip():
            self._log("Strategy builder selection", session_id=session_id)
            response = await self._handle_strategy_builder(req)
            if mode == "freeform":
                response.response_text = to_freeform(response.response_text)
            self._record_turn(session_id, user_text, response.response_text, req, kind="strategy")
            return response

        if "guaranteed profit" in user_text.lower():
            self._log("Guarantee misconception", session_id=session_id)
            response_text = guarantee_misconception_template()
            if mode == "freeform":
                response_text = to_freeform(response_text)
            self._record_turn(session_id, user_text, response_text, req, kind="refusal")
            return ChatResponse(response_text=response_text)

        if is_illegal_request(user_text):
            self._log("Illegal request", session_id=session_id)
            response_text = illegal_refusal_template()
            if mode == "freeform":
                response_text = to_freeform(response_text)
            self._record_turn(session_id, user_text, response_text, req, kind="refusal")
            return ChatResponse(response_text=response_text)

        if needs_refusal(user_text):
            self._log("Refusal", session_id=session_id)
            response_text = safe_refusal_template()
            if mode == "freeform":
                response_text = to_freeform(response_text)
            self._record_turn(session_id, user_text, response_text, req, kind="refusal")
            return ChatResponse(response_text=response_text)

        greek_terms = extract_greek_terms(user_text)
        if greek_terms:
            if "convexity" in greek_terms:
                strategies = extract_strategies(user_text)
                if strategies:
                    response_text = (
                        format_convexity_for_strategy_freeform(strategies[0])
                        if mode == "freeform"
                        else format_convexity_for_strategy_structured(strategies[0])
                    )
                    self._record_turn(session_id, user_text, response_text, req, kind="convexity")
                    return ChatResponse(response_text=response_text)
                if len(greek_terms) == 1:
                    response_text = format_convexity_freeform() if mode == "freeform" else format_convexity_structured()
                    self._record_turn(session_id, user_text, response_text, req, kind="convexity")
                    return ChatResponse(response_text=response_text)
            if len(greek_terms) == 1:
                term = greek_terms[0]
                response_text = (
                    format_greek_freeform(term) if mode == "freeform" else format_greek_structured(term)
                )
                self._record_turn(session_id, user_text, response_text, req, kind="greek")
                return ChatResponse(response_text=response_text)
            response_text = (
                format_greek_comparison_freeform(greek_terms)
                if mode == "freeform"
                else format_greek_comparison_structured(greek_terms)
            )
            self._record_turn(session_id, user_text, response_text, req, kind="greek_compare")
            return ChatResponse(response_text=response_text)

        short_vol = is_short_vol_request(user_text)
        view = detect_view_from_text(user_text) or (req.view or "").lower() or None
        view_hint = detect_view_from_history(history)
        if view and view != "short_vol":
            view_hint = None
        if short_vol:
            view = "short_vol"
        if not view and view_hint and wants_view_based_suggestions(user_text):
            view = view_hint

        if is_horizon_only(user_text):
            income_ctx = detect_income_from_history(history)
            follow_view = view or view_hint or ("short_vol" if income_ctx else None)
            if follow_view:
                response_text = (
                    income_view_suggestions_freeform(follow_view, user_text, history=history)
                    if (income_ctx and mode == "freeform")
                    else income_view_suggestions_structured(follow_view, user_text, history=history)
                    if income_ctx
                    else view_trade_suggestions_freeform(follow_view, user_text, view_hint=view_hint, history=history)
                    if mode == "freeform"
                    else view_trade_suggestions_structured(follow_view, user_text, view_hint=view_hint, history=history)
                )
                self._record_turn(session_id, user_text, response_text, req, kind="advice")
                return ChatResponse(response_text=response_text)

        if is_income_request(user_text) and not view:
            response_text = (
                income_menu_freeform(user_text)
                if mode == "freeform"
                else income_menu_structured(user_text)
            )
            self._record_turn(session_id, user_text, response_text, req, kind="advice")
            return ChatResponse(response_text=response_text)

        if is_income_request(user_text) and view:
            response_text = (
                income_view_suggestions_freeform(view, user_text, history=history)
                if mode == "freeform"
                else income_view_suggestions_structured(view, user_text, history=history)
            )
            self._record_turn(session_id, user_text, response_text, req, kind="advice")
            return ChatResponse(response_text=response_text)
        if is_view_question(user_text):
            strategies = extract_strategies(user_text)
            if not strategies:
                strategies = recent_strategies_from_history(history)
            if strategies:
                response_text = (
                    format_strategy_view_freeform(strategies[0])
                    if mode == "freeform"
                    else format_strategy_view_structured(strategies[0])
                )
                self._record_turn(session_id, user_text, response_text, req, kind="view")
                return ChatResponse(response_text=response_text)

        if is_payoff_mechanics_question(user_text):
            strategies = extract_strategies(user_text)
            if strategies:
                response_text = (
                    format_strategy_structured(strategies[0])
                    if mode == "structured"
                    else to_freeform(format_strategy_structured(strategies[0]))
                )
            else:
                response_text = (
                    format_payoff_mechanics_freeform()
                    if mode == "freeform"
                    else format_payoff_mechanics_structured()
                )
            self._record_turn(session_id, user_text, response_text, req, kind="mechanics")
            return ChatResponse(response_text=response_text)

        if is_trade_recommendation_request(user_text) or is_strike_request(user_text):
            self._log("Trade/strike request", session_id=session_id, view=view)
            if view:
                response_text = (
                    view_trade_suggestions_freeform(view, user_text, view_hint=view_hint, history=history)
                    if mode == "freeform"
                    else view_trade_suggestions_structured(view, user_text, view_hint=view_hint, history=history)
                )
            else:
                response_text = strike_refusal_template()
            if mode == "freeform":
                response_text = response_text if view else to_freeform(response_text)
            self._record_turn(session_id, user_text, response_text, req, kind="advice")
            return ChatResponse(response_text=response_text)

        if view and not mentions_strategy(user_text) and (wants_view_based_suggestions(user_text) or view == "short_vol"):
            self._log("View strategy menu", session_id=session_id, view=view)
            response_text = (
                view_trade_suggestions_freeform(view, user_text, view_hint=view_hint, history=history)
                if mode == "freeform"
                else view_trade_suggestions_structured(view, user_text, view_hint=view_hint, history=history)
            )
            self._record_turn(session_id, user_text, response_text, req, kind="advice")
            return ChatResponse(response_text=response_text)

        basic_option = detect_basic_option_question(user_text)
        if basic_option:
            response_text = format_basic_option_structured(basic_option)
            if mode == "freeform":
                response_text = to_freeform(response_text)
            self._record_turn(session_id, user_text, response_text, req, kind="basic")
            return ChatResponse(response_text=response_text)

        if is_ambiguous_put_spread(user_text):
            response_text = format_ambiguous_put_spread_structured()
            if mode == "freeform":
                response_text = to_freeform(response_text)
            self._record_turn(session_id, user_text, response_text, req, kind="strategy")
            return ChatResponse(response_text=response_text)

        if is_ambiguous_call_spread(user_text):
            response_text = format_ambiguous_call_spread_structured()
            if mode == "freeform":
                response_text = to_freeform(response_text)
            self._record_turn(session_id, user_text, response_text, req, kind="strategy")
            return ChatResponse(response_text=response_text)

        if is_convexity_question(user_text):
            strategies = extract_strategies(user_text)
            if strategies:
                response_text = (
                    format_convexity_for_strategy_freeform(strategies[0])
                    if mode == "freeform"
                    else format_convexity_for_strategy_structured(strategies[0])
                )
            else:
                response_text = format_convexity_freeform() if mode == "freeform" else format_convexity_structured()
            if mode == "freeform" and response_text.startswith("Summary:"):
                response_text = to_freeform(response_text)
            self._record_turn(session_id, user_text, response_text, req, kind="convexity")
            return ChatResponse(response_text=response_text)

        if is_long_short_comparison_question(user_text):
            response_text = (
                format_long_short_comparison_freeform()
                if mode == "freeform"
                else format_long_short_comparison_structured()
            )
            self._record_turn(session_id, user_text, response_text, req, kind="mechanics")
            return ChatResponse(response_text=response_text)

        strategies = extract_strategies(user_text)
        if is_view_question(user_text):
            if not strategies:
                strategies = recent_strategies_from_history(history)
            if strategies:
                response_text = (
                    format_strategy_view_freeform(strategies[0])
                    if mode == "freeform"
                    else format_strategy_view_structured(strategies[0])
                )
                self._record_turn(session_id, user_text, response_text, req, kind="view")
                return ChatResponse(response_text=response_text)
            response_text = (
                format_view_clarify_freeform() if mode == "freeform" else format_view_clarify_structured()
            )
            self._record_turn(session_id, user_text, response_text, req, kind="view")
            return ChatResponse(response_text=response_text)
        if strategies and is_strike_selection_question(user_text):
            response_text = format_strike_selection_structured(strategies[0])
            if mode == "freeform":
                response_text = to_freeform(response_text)
            self._record_turn(session_id, user_text, response_text, req, kind="strategy")
            return ChatResponse(response_text=response_text)
        if not strategies and is_strike_selection_question(user_text):
            recent = recent_strategies_from_history(history)
            if recent:
                response_text = format_strike_selection_structured(recent[0])
                if mode == "freeform":
                    response_text = to_freeform(response_text)
                self._record_turn(session_id, user_text, response_text, req, kind="strategy")
                return ChatResponse(response_text=response_text)
        if strategies and is_expiration_selection_question(user_text):
            response_text = format_expiration_selection_structured(strategies[0])
            if mode == "freeform":
                response_text = to_freeform(response_text)
            self._record_turn(session_id, user_text, response_text, req, kind="strategy")
            return ChatResponse(response_text=response_text)
        if strategies:
            if is_comparison_request(user_text) and len(strategies) >= 2:
                response_text = (
                    format_comparison_freeform(strategies)
                    if mode == "freeform"
                    else format_comparison_structured(strategies)
                )
            else:
                response_text = format_strategy_structured(strategies[0])
            if mode == "freeform":
                response_text = to_freeform(response_text)
            self._record_turn(session_id, user_text, response_text, req, kind="strategy")
            return ChatResponse(response_text=response_text)

        if not is_options_related(user_text) and not history:
            self._log("Off-topic request", session_id=session_id)
            if mode == "freeform":
                return ChatResponse(response_text=off_topic_freeform(user_text))
            response_text = off_topic_template()
            return ChatResponse(response_text=response_text)

        if is_choice_question(user_text):
            recent_strats = recent_strategies_from_history(history)
            if recent_strats:
                view_hint = detect_view_from_text(user_text) or detect_view_from_history(history)
                range_hint = extract_percent_range(user_text) or extract_percent_range(history_text(history))
                horizon_hint = extract_horizon(user_text) or extract_horizon(history_text(history))
                response_text = build_choice_response(
                    recent_strats,
                    view_hint=view_hint,
                    range_hint=range_hint,
                    horizon_hint=horizon_hint,
                )
                if mode == "freeform":
                    response_text = response_text
                else:
                    response_text = ensure_sections(response_text)
                self._record_turn(session_id, user_text, response_text, req, kind="choice")
                return ChatResponse(response_text=response_text)

        if needs_clarification(user_text):
            response_text = (
                clarifying_question_freeform(user_text)
                if mode == "freeform"
                else clarifying_question_structured()
            )
            self._record_turn(session_id, user_text, response_text, req, kind="clarify")
            return ChatResponse(response_text=response_text)

        response_text = await self.llm_service.generate(build_context_message(user_text, req, history))
        response_text = apply_backstop(response_text)
        response_text = ensure_sections(response_text)
        if mode == "freeform":
            response_text = to_freeform(response_text)
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


def render_payoff_response(computed: dict) -> str:
    c = computed["computed"]
    net = c["net_debit"] if c["net_debit"] > 0 else c["net_credit"]
    net_label = "net debit" if c["net_debit"] > 0 else "net credit"
    max_profit = c["max_profit"]
    max_loss = c["max_loss"]
    breakevens = ", ".join([str(b) for b in c["breakevens"]]) or "N/A"

    summary = f"Computed payoff at expiration using {c['quote_source']} premiums."
    setup = f"Net {net_label}: {net}. Underlying price used: {c['underlying_price']}."
    if c.get("premium_note"):
        setup = f"{setup} {c['premium_note']}"
    payoff = "Payoff curve computed at expiration across a price grid."
    max_profit_line = f"Max Profit: {max_profit}."
    max_loss_line = f"Max Loss: {max_loss}."
    be = f"Breakeven(s): {breakevens}."
    sensitivities = "Expiration payoff only; greek sensitivities are structural (delta/vega/theta)."
    beliefs = infer_beliefs_from_legs(c["inputs_used"]["legs"])
    use_case = (
        "Use this to evaluate a user-specified position with user-provided premiums when available. "
        f"Beliefs that fit this position: {beliefs}"
    )
    risks = "Results ignore early exercise, dividends, and transaction costs."
    assumptions = "Assumes listed equity options, contract multiplier of 100, and expiration payoff only."
    if c.get("underlying_price_note"):
        assumptions = f"{assumptions} {c['underlying_price_note']}"

    json_block = json.dumps(computed, indent=2)

    return (
        f"Summary: {summary}\n"
        f"Setup: {setup}\n"
        f"Payoff at Expiration: {payoff}\n"
        f"{max_profit_line}\n"
        f"{max_loss_line}\n"
        f"Breakeven(s): {be}\n"
        f"Key Sensitivities: {sensitivities}\n"
        f"Typical Use Case: {use_case}\n"
        f"Main Risks: {risks}\n"
        f"Assumptions / What I need from you: {assumptions}\n\n"
        "Computed JSON:\n"
        f"```json\n{json_block}\n```"
    )


def infer_beliefs_from_legs(legs: list[dict]) -> str:
    option_legs = [l for l in legs if l.get("instrument") == "option"]
    stock_legs = [l for l in legs if l.get("instrument") == "stock"]
    expirations = {l.get("expiration") for l in option_legs if l.get("expiration")}
    strikes = [l.get("strike") for l in option_legs if l.get("strike") is not None]

    # Single-leg options
    if len(option_legs) == 1 and not stock_legs:
        leg = option_legs[0]
        if leg.get("option_type") == "call" and leg.get("side") == "buy":
            return "Bullish; expects a moderate move higher before expiration."
        if leg.get("option_type") == "put" and leg.get("side") == "buy":
            return "Bearish; expects a moderate move lower before expiration."
        if leg.get("option_type") == "call" and leg.get("side") == "sell":
            return "Neutral to mildly bearish; expects price to stay below the strike."
        if leg.get("option_type") == "put" and leg.get("side") == "sell":
            return "Neutral to mildly bullish; expects price to stay above the strike."

    # Stock + option structures
    if stock_legs and option_legs:
        call_sells = [l for l in option_legs if l.get("option_type") == "call" and l.get("side") == "sell"]
        put_buys = [l for l in option_legs if l.get("option_type") == "put" and l.get("side") == "buy"]
        if call_sells and not put_buys:
            return "Mildly bullish/neutral; expects limited upside with income (covered call)."
        if put_buys and not call_sells:
            return "Bullish long stock with downside protection (protective put)."
        if call_sells and put_buys:
            return "Neutral to mildly bullish; seeks downside protection with capped upside (collar)."

    # Two-leg option structures
    if len(option_legs) == 2:
        calls = [l for l in option_legs if l.get("option_type") == "call"]
        puts = [l for l in option_legs if l.get("option_type") == "put"]
        if len(calls) == 2 and len(expirations) == 1:
            buy = [l for l in calls if l.get("side") == "buy"]
            sell = [l for l in calls if l.get("side") == "sell"]
            if buy and sell:
                return "Bullish; expects a mild to moderate rise, with upside capped (bull call spread)."
        if len(puts) == 2 and len(expirations) == 1:
            buy = [l for l in puts if l.get("side") == "buy"]
            sell = [l for l in puts if l.get("side") == "sell"]
            if buy and sell:
                return "Bearish; expects a mild to moderate decline, with downside capped (bear put spread)."
        if len(calls) == 1 and len(puts) == 1 and len(expirations) == 1:
            if calls[0].get("side") == "buy" and puts[0].get("side") == "buy":
                if calls[0].get("strike") == puts[0].get("strike"):
                    return "Volatility-seeking; expects a large move in either direction (long straddle)."
                return "Volatility-seeking; expects a large move beyond the strikes (long strangle)."
            if calls[0].get("side") == "sell" and puts[0].get("side") == "sell":
                if calls[0].get("strike") == puts[0].get("strike"):
                    return "Low-volatility; expects price to stay near the strike (short straddle)."
                return "Low-volatility; expects price to stay between strikes (short strangle)."
        if len(expirations) == 2 and len(calls) == 2 and len(puts) == 0:
            return "Neutral to mildly directional; expects stability near the strike and time decay (call calendar)."
        if len(expirations) == 2 and len(puts) == 2 and len(calls) == 0:
            return "Neutral to mildly directional; expects stability near the strike and time decay (put calendar)."

    # Four-leg structures
    if len(option_legs) == 4:
        calls = [l for l in option_legs if l.get("option_type") == "call"]
        puts = [l for l in option_legs if l.get("option_type") == "put"]
        if len(calls) == 2 and len(puts) == 2 and len(expirations) == 1:
            strikes_sorted = sorted(s for s in strikes if s is not None)
            if len(strikes_sorted) >= 3 and strikes_sorted[1] == strikes_sorted[2]:
                return "Range-bound; expects price to stay near the center strike (iron butterfly)."
            return "Range-bound; expects price to stay within a defined range (iron condor)."

    # Ratio and backspread (uneven quantities)
    if option_legs:
        for opt_type in ("call", "put"):
            legs_of_type = [l for l in option_legs if l.get("option_type") == opt_type]
            if len(legs_of_type) >= 2:
                buy_qty = sum(int(l.get("quantity", 1)) for l in legs_of_type if l.get("side") == "buy")
                sell_qty = sum(int(l.get("quantity", 1)) for l in legs_of_type if l.get("side") == "sell")
                if buy_qty > sell_qty and sell_qty > 0:
                    return (
                        "Volatility-seeking; expects a larger move beyond the short strike (backspread)."
                        if opt_type == "call"
                        else "Volatility-seeking; expects a larger move lower beyond the short strike (put backspread)."
                    )
                if sell_qty > buy_qty and buy_qty > 0:
                    return (
                        "Directionally biased income; expects price to gravitate toward the short strike (ratio spread)."
                    )

    # Diagonal (different strikes and expirations)
    if len(option_legs) == 2 and len(expirations) == 2:
        if len(set(strikes)) == 2:
            return "Directional with time-decay edge; expects price to move toward the short strike while longer option retains value (diagonal)."

    # Bull/bear put or call credit spreads (inferred by sides)
    if len(option_legs) == 2 and len(expirations) == 1:
        calls = [l for l in option_legs if l.get("option_type") == "call"]
        puts = [l for l in option_legs if l.get("option_type") == "put"]
        if len(calls) == 2:
            sell = [l for l in calls if l.get("side") == "sell"]
            buy = [l for l in calls if l.get("side") == "buy"]
            if sell and buy:
                return "Neutral to mildly bearish; expects price to stay below the short call (bear call spread)."
        if len(puts) == 2:
            sell = [l for l in puts if l.get("side") == "sell"]
            buy = [l for l in puts if l.get("side") == "buy"]
            if sell and buy:
                return "Neutral to mildly bullish; expects price to stay above the short put (bull put spread)."

    return "Depends on the full structure; share your intent if you want a more tailored interpretation."


def is_payoff_intent(text: str, has_legs: bool = False) -> bool:
    lower = text.lower()
    compute_triggers = ["compute", "calculate", "evaluate", "price this", "p&l", "profit/loss"]
    if any(k in lower for k in compute_triggers):
        return True
    if "payoff" in lower:
        conceptual_phrases = ["how do payoffs work", "payoffs work", "payoff work", "explain payoff"]
        if any(p in lower for p in conceptual_phrases):
            return False
        if has_legs:
            return True
    if has_legs and any(k in lower for k in ["max profit", "max loss", "breakeven", "break-even"]):
        return True
    return False


def normalize_legs(legs):
    valid = []
    for leg in legs:
        if isinstance(leg, OptionLeg):
            if (
                leg.option_type
                and leg.side
                and leg.strike is not None
                and leg.expiration
                and leg.quantity
            ):
                valid.append(leg)
        elif isinstance(leg, StockLeg):
            if leg.side and leg.quantity:
                valid.append(leg)
    return valid


def build_context_message(user_text: str, req: ChatRequest, history: list[dict]) -> str:
    context = {
        "ticker": req.ticker,
        "view": req.view,
        "strategy": req.strategy,
        "legs": [leg.model_dump() for leg in req.legs] if req.legs else [],
    }
    history_text = format_history(history)
    parts = []
    if history_text:
        parts.append("Conversation history (most recent last):\n" + history_text)
    parts.append(f"Current user message:\n{user_text}")
    parts.append("Context (use only if the user asked for payoff/evaluation):\n" + json.dumps(context, indent=2))
    return "\n\n".join(parts)


def format_view_menu(view: str) -> str:
    strategies, thesis = view_menu_parts(view)
    view_label = (view or "").replace("_", " ").capitalize()
    return (
        f"Summary: {view_label} market view strategy menu.\n"
        f"Setup: {strategies}\n"
        f"Payoff at Expiration: {thesis}\n"
        "Max Profit: Varies by strategy (spreads are capped).\n"
        "Max Loss: Limited for defined-risk spreads; limited to premium for single long options.\n"
        "Breakeven(s): Depend on strikes and premiums; provide legs if you want calculations.\n"
        "Key Sensitivities: Directional delta; long options are positive convexity (long gamma) with negative theta; short premium is negative convexity (short gamma) with positive theta.\n"
        "Typical Use Case: Use this to choose a strategy type before selecting strikes/expiration.\n"
        "Main Risks: Move against the position or insufficient move before expiration.\n"
        "Assumptions / What I need from you: If you want payoff calculations, share ticker, expiration, strikes, call/put, buy/sell, quantity, and optional premiums."
    )


def income_menu_structured(user_text: str) -> str:
    horizon = extract_horizon(user_text)
    horizon_clause = f" over about {horizon}" if horizon else ""
    return (
        "Summary: Income-focused strategies are usually credit trades, and market-neutral setups are common.\n"
        "Setup: Market-neutral income: iron condor, iron butterfly, or short strangle (higher risk). "
        "Bullish income: covered call (if long stock), cash-secured put, bull put spread. "
        "Bearish income: bear call spread.\n"
        f"Payoff at Expiration: Credit trades earn premium if price stays within a range or below/above a short strike{horizon_clause}.\n"
        "Max Profit: Typically the premium (credit) received.\n"
        "Max Loss: Limited for defined-risk spreads; larger for short strangles/straddles.\n"
        "Breakeven(s): Depend on strikes and credit received.\n"
        "Key Sensitivities: Credit trades are usually negative convexity (short gamma) with positive theta.\n"
        "Typical Use Case: Generating income when you expect range-bound prices or mild directional moves.\n"
        "Main Risks: Sharp moves or volatility spikes can cause losses, especially for naked short options.\n"
        "Assumptions / What I need from you: Tell me your market view and time horizon and I will narrow the list."
    )


def income_menu_freeform(user_text: str) -> str:
    horizon = extract_horizon(user_text)
    horizon_clause = f" over about {horizon}" if horizon else ""
    return (
        "Income strategies are usually credit trades. For market-neutral income, an iron condor, iron butterfly, "
        "or a short strangle (higher risk) are typical choices. If you're bullish, covered calls (if you own shares), "
        "cash-secured puts, or bull put spreads are common. If you're bearish, a bear call spread is the standard income trade. "
        f"These generally work best when price stays within a range or below/above the short strike{horizon_clause}, and they are negative-convexity trades (short gamma). "
        "Tell me your market view and time horizon and I can narrow this to a best-fit menu."
    )


def view_menu_parts(view: str) -> tuple[str, str]:
    view_lower = (view or "").lower()
    if view_lower == "bullish":
        strategies = "Long call, bull call spread, bull put spread, covered call (if long stock)."
        thesis = (
            "Long call seeks a larger upside move; bull call spread fits mild-to-moderate upside; "
            "bull put spread benefits if price stays above the short put; covered call earns income with capped upside."
        )
    elif view_lower == "bearish":
        strategies = "Long put, bear put spread, bear call spread."
        thesis = (
            "Long put seeks downside; bear put spread fits mild-to-moderate downside; "
            "bear call spread benefits if price stays below the short call."
        )
    elif view_lower == "neutral":
        strategies = "Iron condor, iron butterfly, short strangle, short straddle (higher risk), calendar spread."
        thesis = "These strategies benefit if price stays within a range or near a strike while time decay works."
    else:
        strategies = "Long straddle, long strangle, backspread."
        thesis = "These strategies benefit from a large move in either direction or rising volatility."
    return strategies, thesis


def income_view_suggestions_structured(
    view: str,
    user_text: str,
    history: list[dict] | None = None,
) -> str:
    low, high, _ = compute_moneyness_range(extract_percent_range(user_text), view)
    horizon = extract_horizon(user_text)
    horizon_clause = f" with an expiration around {horizon}" if horizon else " with an expiration that matches your horizon"
    if view == "bullish":
        best = f"Bull put spread: sell a put about {low}% OTM and buy a put about {high}% OTM{horizon_clause}."
        alternatives = (
            f"Covered call (if you own shares){horizon_clause}. "
            f"Cash-secured put around {low}% OTM{horizon_clause}."
        )
        payoff = "These generate credit income and benefit if price stays above the short put or below the call strike."
        max_profit = "Typically the credit received; covered calls cap upside."
        max_loss = "Bull put spread is limited to wing width minus credit; covered calls and CSPs have downside risk like stock."
        use_case = "Income with a bullish to neutral bias."
        risks = "A sharp drop can hurt short puts; covered calls cap upside if the stock rallies."
    elif view == "bearish":
        best = f"Bear call spread: sell a call about {low}% OTM and buy a call about {high}% OTM{horizon_clause}."
        alternatives = "Nonequity income is limited in bearish setups; consider smaller size or defined-risk credit spreads."
        payoff = "Credit spread benefits if price stays below the short call."
        max_profit = "Net credit received."
        max_loss = "Wing width minus credit."
        use_case = "Income with a bearish to neutral bias."
        risks = "A sharp rally can hurt short calls."
    elif view in ("neutral", "short_vol"):
        best = (
            f"Iron condor: sell a put about {low}% OTM and sell a call about {low}% OTM, "
            f"buy wings about {high}% OTM on each side{horizon_clause}."
        )
        alternatives = (
            f"Iron butterfly centered near ATM{horizon_clause}. "
            f"Short strangle selling about {low}% OTM call and put (higher risk){horizon_clause}."
        )
        payoff = "These generate income if price stays within a range while time decay works."
        max_profit = "Net credit received; condors and butterflies are capped."
        max_loss = "Limited for condors/butterflies; short strangles have larger risk."
        use_case = "Market-neutral income with short-volatility exposure."
        risks = "A sharp move beyond the short strikes can create losses."
    else:
        best = (
            f"Income trades are usually short volatility; if you still want income with a volatile view, "
            f"consider a wider iron condor with short strikes about {low}% OTM and wings about {high}% OTM{horizon_clause}."
        )
        alternatives = "If you truly expect volatility, long straddles/strangles are more aligned but are not income trades."
        payoff = "Income setups benefit from time decay if price stays within a range."
        max_profit = "Net credit received."
        max_loss = "Defined-risk for condors; larger for naked short options."
        use_case = "Income focus even when expecting volatility, with awareness of the mismatch."
        risks = "Volatility spikes can overwhelm income trades."
    return (
        "Summary: Income-focused trades are typically credit strategies, often market-neutral.\n"
        f"Setup: Best match: {best} Alternatives: {alternatives}\n"
        f"Payoff at Expiration: {payoff}\n"
        f"Max Profit: {max_profit}\n"
        f"Max Loss: {max_loss}\n"
        "Breakeven(s): Depend on strikes and credit received.\n"
        "Key Sensitivities: Credit trades are usually negative convexity (short gamma) with positive theta.\n"
        f"Typical Use Case: {use_case}\n"
        f"Main Risks: {risks}\n"
        "Assumptions / What I need from you: Share your time horizon or target move if you want tighter moneyness guidance."
    )


def income_view_suggestions_freeform(
    view: str,
    user_text: str,
    history: list[dict] | None = None,
) -> str:
    low, high, _ = compute_moneyness_range(extract_percent_range(user_text), view)
    horizon = extract_horizon(user_text)
    horizon_clause = f" over about {horizon}" if horizon else ""
    if view == "bullish":
        best = f"A bull put spread selling about {low}% OTM and buying about {high}% OTM is the cleanest income trade{horizon_clause}."
        alt = "Covered calls (if you own shares) and cash-secured puts are the other common bullish income choices."
        risk = "These are credit trades, so the main risk is a sharp drop; covered calls also cap upside."
    elif view == "bearish":
        best = f"A bear call spread selling about {low}% OTM and buying about {high}% OTM is the standard bearish income trade{horizon_clause}."
        alt = "Defined-risk credit spreads are usually safer than naked calls."
        risk = "A sharp rally can cause losses because the trade is short calls."
    elif view in ("neutral", "short_vol"):
        best = f"An iron condor with short strikes about {low}% OTM and wings around {high}% OTM is the classic market-neutral income trade{horizon_clause}."
        alt = "An iron butterfly is tighter around the money; a short strangle collects more premium but adds risk."
        risk = "These are negative-convexity trades (short gamma), so big moves can hurt."
    else:
        best = "Income trades are usually short volatility, which conflicts with a strongly volatile outlook."
        alt = "If you still want income, a wider iron condor can collect credit but carries volatility risk."
        risk = "Volatility spikes can overwhelm credit income."
    return f"{best} {alt} {risk}"


def view_trade_suggestions_structured(
    view: str,
    user_text: str,
    view_hint: str | None = None,
    history: list[dict] | None = None,
) -> str:
    low, high, _ = compute_moneyness_range(extract_percent_range(user_text), view)
    horizon = extract_horizon(user_text)
    horizon_clause = f" with an expiration around {horizon}" if horizon else " with an expiration that matches your horizon"

    if view == "bullish":
        best = f"Bull call spread: buy a call about {low}% OTM and sell a call about {high}% OTM{horizon_clause}."
        alternatives = (
            f"Long call about {low}% OTM{horizon_clause}. "
            f"Bull put spread selling a put about {low}% OTM and buying a put about {high}% OTM{horizon_clause}."
        )
        payoff = (
            "Bull call spread fits a mild-to-moderate rise with capped upside; long call needs a bigger move but has uncapped upside; "
            "bull put spread benefits if price stays above the short put."
        )
        max_profit = "Bull call spread is capped by strike width; long call is uncapped on the upside; bull put spread is capped by credit."
        max_loss = "Bull call spread and long call are limited to premium paid; bull put spread is limited to strike width minus credit."
        move_text = f"{low}%" if low == high else f"{low}-{high}%"
        use_case = f"Moderately bullish view where a {move_text} move is plausible over the stated horizon."
        risks = "If the move is smaller or slower than expected, time decay and limited upside reduce returns."
    elif view == "bearish":
        best = f"Bear put spread: buy a put about {low}% OTM and sell a put about {high}% OTM{horizon_clause}."
        alternatives = (
            f"Long put about {low}% OTM{horizon_clause}. "
            f"Bear call spread selling a call about {low}% OTM and buying a call about {high}% OTM{horizon_clause}."
        )
        payoff = (
            "Bear put spread fits a mild-to-moderate decline with capped downside profit; long put needs a bigger move but has more upside to the downside; "
            "bear call spread benefits if price stays below the short call."
        )
        max_profit = "Bear put spread is capped by strike width; long put gains as price falls (bounded by zero); bear call spread is capped by credit."
        max_loss = "Bear put spread and long put are limited to premium paid; bear call spread is limited to strike width minus credit."
        move_text = f"{low}%" if low == high else f"{low}-{high}%"
        use_case = f"Moderately bearish view where a {move_text} move down is plausible over the stated horizon."
        risks = "A smaller or delayed move can leave spreads or long puts unprofitable by expiration."
    elif view == "neutral":
        best = (
            f"Iron condor: sell a put about {low}% OTM and sell a call about {low}% OTM, "
            f"buy wings about {high}% OTM on each side{horizon_clause}."
        )
        alternatives = (
            f"Iron butterfly centered near ATM{horizon_clause}. "
            f"Short strangle selling about {low}% OTM call and put (higher risk){horizon_clause}."
        )
        payoff = "These benefit if price stays within a range; time decay helps as long as price avoids the wings."
        max_profit = "Typically the net credit received; condors and butterflies are capped."
        max_loss = "Limited for defined-risk condors/butterflies; short strangles have larger risk."
        use_case = "Neutral or range-bound view with expectation of stable price and time decay."
        risks = "A sharp move beyond the short strikes can create losses."
    elif view == "volatile":
        best = (
            f"Long strangle: buy a call about {low}% OTM and buy a put about {low}% OTM{horizon_clause}."
        )
        alternatives = (
            f"Long straddle near ATM{horizon_clause}. "
            f"Backspread using extra long options beyond the short strike{horizon_clause}."
        )
        payoff = "These seek a large move in either direction or a volatility rise; strangles are cheaper but need a larger move."
        max_profit = "Unlimited on the upside; large gains on a sharp drop (bounded by zero)."
        max_loss = "Limited to the total premium paid for long straddles/strangles."
        use_case = "Volatile outlook where a big move is expected but direction is unclear."
        risks = "Time decay if the move does not materialize by expiration."
    else:
        best = (
            f"Iron condor: sell a put about {low}% OTM and sell a call about {low}% OTM, "
            f"buy wings about {high}% OTM on each side{horizon_clause}."
        )
        alternatives = (
            f"Short strangle selling about {low}% OTM call and put (higher risk){horizon_clause}. "
            f"Iron butterfly centered near ATM{horizon_clause}."
        )
        payoff = "These benefit if price stays within a range while time decay works; short strangles are riskier but collect more premium."
        max_profit = "Typically the net credit received; condors and butterflies are capped."
        max_loss = "Limited for defined-risk condors/butterflies; short strangles have larger risk."
        use_case = "Short-volatility outlook where you expect prices to stay within a range."
        if view_hint == "bullish":
            use_case += " With a bullish tilt, a bull put spread can be a simpler short-vol alternative."
        elif view_hint == "bearish":
            use_case += " With a bearish tilt, a bear call spread can be a simpler short-vol alternative."
        risks = "A sharp move or volatility spike can cause losses."

    return (
        f"Summary: For a {view.replace('_', ' ')} outlook, here is a best-match trade with % moneyness strikes plus alternatives.\n"
        f"Setup: Best match: {best} Alternatives: {alternatives}\n"
        f"Payoff at Expiration: {payoff}\n"
        f"Max Profit: {max_profit}\n"
        f"Max Loss: {max_loss}\n"
        "Breakeven(s): Depend on strikes and premiums; I can compute exact breakevens if you provide legs.\n"
        "Key Sensitivities: Directional delta; long options are positive convexity (long gamma) with negative theta; short premium is negative convexity (short gamma) with positive theta.\n"
        f"Typical Use Case: {use_case}\n"
        f"Main Risks: {risks}\n"
        "Assumptions / What I need from you: These are educational % moneyness examples. Share ticker, strikes, expiration, and premiums if you want payoff calculations."
    )


def view_trade_suggestions_freeform(
    view: str,
    user_text: str,
    view_hint: str | None = None,
    history: list[dict] | None = None,
) -> str:
    range_from_user = extract_percent_range(user_text)
    low, high, range_note = compute_moneyness_range(range_from_user, view)
    horizon = extract_horizon(user_text)
    horizon_clause = f" over about {horizon}" if horizon else ""
    range_clause = f" targeting roughly a {low}-{high}% move" if low != high else f" targeting about a {low}% move"
    ask_horizon = ""
    if not horizon and not asked_horizon_before(history or []):
        ask_horizon = " What time horizon are you thinking?"
    if not range_from_user:
        range_note = " If you have a specific target move, I can adjust the % moneyness."

    if view == "bullish":
        best = f"A bull call spread with a long call around {low}% OTM and a short call around {high}% OTM is a clean fit{horizon_clause}{range_clause}."
        alt = (
            f"If you want more upside, a long call around {low}% OTM is simpler. "
            f"If you prefer credit, a bull put spread selling ~{low}% OTM and buying ~{high}% OTM is another option."
        )
        risk = (
            "Long calls are positive-convexity trades; bull call spreads have limited convexity because upside is capped, "
            "and bull put spreads are negative-convexity (short gamma)."
        )
        risk += " If the move is smaller or slower than expected, time decay can limit returns."
    elif view == "bearish":
        best = f"A bear put spread with a long put around {low}% OTM and a short put around {high}% OTM fits that view{horizon_clause}{range_clause}."
        alt = (
            f"For more downside leverage, a long put around {low}% OTM is simpler. "
            f"If you prefer credit, a bear call spread selling ~{low}% OTM and buying ~{high}% OTM is another option."
        )
        risk = (
            "Long puts are positive-convexity trades; bear put spreads have limited convexity because downside is capped, "
            "and bear call spreads are negative-convexity (short gamma)."
        )
        risk += " If the drop is smaller or slower than expected, time decay can limit returns."
    elif view == "neutral":
        best = (
            f"An iron condor selling about {low}% OTM on both sides and buying wings around {high}% OTM is the classic range trade{horizon_clause}."
        )
        alt = "A short strangle collects more premium but adds risk; an iron butterfly is tighter around the money."
        risk = "These are negative-convexity trades (short gamma), so a sharp move outside the range can cause losses."
    elif view == "volatile":
        best = f"A long strangle buying a call and put around {low}% OTM is a good fit{horizon_clause}."
        alt = "A long straddle is more sensitive to moves but costs more; a backspread can add convexity."
        risk = "These are positive-convexity trades; time decay hurts if the move doesn't show up."
    else:
        best = (
            f"If you want to sell volatility, an iron condor with short strikes around {low}% OTM and wings around {high}% OTM is a solid default{horizon_clause}."
        )
        alt = "A short strangle is higher risk and a bit more premium; an iron butterfly is tighter around the money."
        if view_hint == "bullish":
            alt += " With a bullish tilt, a bull put spread is a simpler short-vol alternative."
        elif view_hint == "bearish":
            alt += " With a bearish tilt, a bear call spread is a simpler short-vol alternative."
        risk = "Short volatility trades are negative-convexity (short gamma) and can lose quickly if the stock moves or IV spikes."

    return f"{best} {alt} {risk}{range_note}{ask_horizon}"


def format_strategy_structured(strategy_name: str) -> str:
    strat = get_strategy(strategy_name)
    if not strat:
        return ensure_sections(
            "I can explain listed equity options strategies and evaluate specified legs. Please choose a supported strategy."
        )
    name = friendly_name(strategy_name)
    summary = strat["summary"].strip()
    summary = summary[0].lower() + summary[1:] if summary else summary
    return (
        f"Summary: {name} is {summary}\n"
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


def format_strike_selection_structured(strategy_name: str) -> str:
    name = normalize_strategy_name(strategy_name)
    display = friendly_name(name)
    if name in ("bull call spread", "bear put spread"):
        return (
            f"Summary: Strike selection in a {display} (debit spread) trades cost, breakeven, and the capped payoff range.\n"
            "Setup: Closer-to-ATM long strikes cost more but need a smaller move; further OTM long strikes are cheaper but need a bigger move. A higher short strike widens the spread and raises max profit but increases the debit.\n"
            "Payoff at Expiration: A wider spread increases the payoff range; a tighter spread is cheaper but caps gains sooner.\n"
            "Max Profit: Strike width minus net debit; wider width raises max profit but typically costs more.\n"
            "Max Loss: The net debit; further OTM strikes reduce debit but lower the probability of finishing ITM.\n"
            "Breakeven(s): Long strike plus/minus net debit (depending on call/put); further OTM long strikes raise the breakeven.\n"
            "Key Sensitivities: Closer strikes increase delta/gamma and responsiveness; further strikes reduce sensitivity; convexity is limited by the short leg.\n"
            "Typical Use Case: Use tighter strikes for modest moves; wider strikes for larger expected moves.\n"
            "Main Risks: Insufficient move before expiration and time decay.\n"
            "Assumptions / What I need from you: Share target moneyness and expiration if you want a more tailored explanation."
        )
    if name in ("bull put spread", "bear call spread"):
        return (
            f"Summary: Strike selection in a {display} (credit spread) trades credit size against probability and tail risk.\n"
            "Setup: Short strikes closer to spot bring in more credit but increase assignment risk; further OTM short strikes reduce credit but provide more cushion. Wing width sets max loss.\n"
            "Payoff at Expiration: Profit if price stays beyond the short strike; moving strikes further OTM reduces credit but improves odds.\n"
            "Max Profit: Net credit; typically higher when the short strike is closer to spot.\n"
            "Max Loss: Wing width minus credit; wider wings increase max loss but give more room.\n"
            "Breakeven(s): Short strike plus/minus credit (depending on call/put); further OTM short strikes push breakeven farther away.\n"
            "Key Sensitivities: Short gamma/negative convexity; closer short strikes increase sensitivity to adverse moves.\n"
            "Typical Use Case: Neutral to mildly directional views with a preference for credit income.\n"
            "Main Risks: Sharp move through the short strike; assignment risk.\n"
            "Assumptions / What I need from you: Share target moneyness and expiration if you want a more tailored explanation."
        )
    if name in ("long call", "long put"):
        return (
            f"Summary: Strike choice for a {display} trades premium cost against the move required to profit.\n"
            "Setup: A closer-to-ATM strike costs more but needs a smaller move; a further OTM strike is cheaper but needs a bigger move.\n"
            "Payoff at Expiration: Closer strikes reach intrinsic value sooner; further strikes require larger moves to finish ITM.\n"
            "Max Profit: Unlimited for long calls; large but bounded by zero for long puts.\n"
            "Max Loss: The premium paid; further OTM strikes typically reduce the premium.\n"
            "Breakeven(s): Strike plus/minus premium; further OTM strikes raise breakevens.\n"
            "Key Sensitivities: Closer strikes have higher delta/gamma; further strikes have more leverage to large moves but are more theta-sensitive.\n"
            "Typical Use Case: Use closer strikes for moderate moves; further OTM for larger move expectations and lower cost.\n"
            "Main Risks: Time decay if the move does not arrive in time.\n"
            "Assumptions / What I need from you: Share the moneyness you're considering if you want specific guidance."
        )
    if name in ("long straddle", "long strangle"):
        return (
            f"Summary: Strike selection in a {display} balances cost versus the size of move required.\n"
            "Setup: Straddles use the same (often ATM) strike; strangles use OTM strikes that are cheaper but further away.\n"
            "Payoff at Expiration: Straddles profit from smaller moves; strangles require larger moves to reach breakeven.\n"
            "Max Profit: Unlimited on the upside; large downside profit if the stock drops sharply (bounded by zero).\n"
            "Max Loss: Total premium paid; strangles usually have lower max loss because they cost less.\n"
            "Breakeven(s): Straddle breakevens are closer; strangle breakevens are further from spot.\n"
            "Key Sensitivities: ATM strikes have higher gamma and convexity; OTM strikes have lower initial gamma but can accelerate if price moves toward them.\n"
            "Typical Use Case: Straddles for big moves with higher conviction; strangles for cheaper exposure when you expect a larger move.\n"
            "Main Risks: Time decay if the move does not materialize by expiration.\n"
            "Assumptions / What I need from you: Share your move expectations and horizon if you want specific moneyness guidance."
        )
    if name in ("short straddle", "short strangle", "iron condor", "iron butterfly"):
        return (
            f"Summary: Strike selection in a {display} trades credit size against risk of a large move.\n"
            "Setup: Short strikes closer to spot increase credit but raise the chance of losses; further OTM strikes reduce credit but widen the safe range. Wing width controls max loss for defined-risk structures.\n"
            "Payoff at Expiration: Profits if price stays within the range; closer strikes reduce the range but pay more.\n"
            "Max Profit: Net credit; higher when short strikes are closer to spot.\n"
            "Max Loss: Defined-risk spreads cap loss at wing width minus credit; short straddles/strangles can be large or unlimited.\n"
            "Breakeven(s): Short strikes plus/minus credit; moving strikes further out shifts breakevens away.\n"
            "Key Sensitivities: Negative convexity (short gamma); risk increases quickly as price moves toward the short strikes.\n"
            "Typical Use Case: Range-bound outlook with an emphasis on time decay.\n"
            "Main Risks: Sharp move or volatility spike through the short strikes.\n"
            "Assumptions / What I need from you: Share your preferred range and expiration if you want specific moneyness guidance."
        )
    if name in ("covered call", "cash-secured put"):
        return (
            f"Summary: Strike choice in a {display} trades income against assignment risk and upside/downside participation.\n"
            "Setup: Higher call strikes (covered call) or lower put strikes (cash-secured put) reduce premium but give more room; closer strikes pay more but increase assignment likelihood.\n"
            "Payoff at Expiration: Closer strikes cap upside sooner (covered call) or raise assignment risk (CSP); further strikes reduce income but give more cushion.\n"
            "Max Profit: Covered call is capped at strike plus premium; CSP max profit is the premium.\n"
            "Max Loss: Downside remains tied to stock movement; premiums provide limited buffer.\n"
            "Breakeven(s): Covered call breakeven is stock cost minus premium; CSP breakeven is strike minus premium.\n"
            "Key Sensitivities: Short gamma/negative convexity; closer strikes increase sensitivity to adverse moves.\n"
            "Typical Use Case: Income focus with willingness to be assigned or called away.\n"
            "Main Risks: Large downside moves or opportunity cost on sharp rallies.\n"
            "Assumptions / What I need from you: Share your income vs. participation preference if you want strike ideas."
        )
    if name in ("collar", "protective put"):
        return (
            f"Summary: Strike choice in a {display} trades protection cost against how much upside you keep.\n"
            "Setup: A higher put strike provides more protection but costs more; a lower put strike is cheaper but leaves more downside. A lower call strike in a collar brings in more premium but caps upside sooner.\n"
            "Payoff at Expiration: Tighter protection reduces losses sooner but can cap gains more; looser protection is cheaper but less effective.\n"
            "Max Profit: Collar profits are capped at the call strike; protective put keeps upside minus the put cost.\n"
            "Max Loss: Protective put limits downside below the put strike; collar limits downside below the put strike.\n"
            "Breakeven(s): Protective put breakeven is stock cost plus put premium; collar breakeven shifts with net credit/debit.\n"
            "Key Sensitivities: Long put adds positive convexity; the short call reduces upside convexity.\n"
            "Typical Use Case: Protecting a long stock position with a chosen cost vs. protection trade-off.\n"
            "Main Risks: Paying for protection that isn't needed or capping upside too soon.\n"
            "Assumptions / What I need from you: Share how much protection vs. upside you want to keep."
        )
    if name in ("ratio spread", "backspread"):
        return (
            f"Summary: Strike selection in a {display} determines where risk concentrates and where convexity kicks in.\n"
            "Setup: The short strike is where losses can concentrate; further OTM long strikes lower cost but require a bigger move to benefit.\n"
            "Payoff at Expiration: Closer long strikes improve responsiveness; further strikes increase convexity but need a larger move.\n"
            "Max Profit: Grows as the move exceeds the long strike; the extra long options drive convexity beyond the short strike.\n"
            "Max Loss: Often near the short strike; wider spacing can increase the loss zone.\n"
            "Breakeven(s): Multiple breakevens that shift with strike spacing and premium.\n"
            "Key Sensitivities: Long gamma beyond the short strike; short gamma near the short strike.\n"
            "Typical Use Case: Expecting a larger move in one direction with limited upfront cost.\n"
            "Main Risks: Losses if price stalls near the short strike.\n"
            "Assumptions / What I need from you: Share target move size and preferred strike spacing for a more tailored explanation."
        )
    return (
        f"Summary: Strike selection changes cost, breakevens, and the shape of the payoff for {display}.\n"
        "Setup: Closer-to-ATM strikes increase sensitivity and cost; further OTM strikes reduce cost but require bigger moves.\n"
        "Payoff at Expiration: Wider strike spacing usually increases payoff range but alters probability and risk.\n"
        "Max Profit: Depends on the specific structure and strike width.\n"
        "Max Loss: Depends on the specific structure and strike width.\n"
        "Breakeven(s): Depend on strikes and premiums.\n"
        "Key Sensitivities: Strike distance affects delta/gamma and convexity exposure.\n"
        "Typical Use Case: Use strike placement to match how far and how fast you expect the move.\n"
        "Main Risks: Mis-sizing strike distance can leave the position underperforming.\n"
        "Assumptions / What I need from you: Share the strategy and your expected move if you want a more tailored explanation."
    )


def format_expiration_selection_structured(strategy_name: str) -> str:
    name = normalize_strategy_name(strategy_name)
    strat = get_strategy(name)
    summary_name = name if strat else "this strategy"
    return (
        f"Summary: Expiration choice changes time decay, convexity, and cost for {summary_name}.\n"
        f"Setup: {strat['setup'] if strat else 'Shorter expirations are cheaper but decay faster; longer expirations cost more but give more time.'}\n"
        "Payoff at Expiration: Shorter expirations need the move sooner; longer expirations give more time for the thesis to play out.\n"
        "Max Profit: Structural limits are the same, but shorter expirations make it harder to reach full payoff.\n"
        "Max Loss: Long-premium trades cost more at longer expirations; short-premium trades collect more credit but keep risk open longer.\n"
        "Breakeven(s): Longer expirations usually imply higher breakevens for long options because premiums are larger.\n"
        "Key Sensitivities: Shorter expirations have higher gamma/convexity and faster theta decay; longer expirations are more sensitive to vega.\n"
        "Typical Use Case: Use shorter expirations for near-term catalysts; longer expirations for slower-moving theses.\n"
        "Main Risks: Short expirations can decay quickly; long expirations can tie up premium and add vega risk.\n"
        "Assumptions / What I need from you: Share your time horizon if you want a more tailored explanation."
    )

def format_comparison_structured(strategies: list[str]) -> str:
    cards = []
    for name in strategies:
        strat = get_strategy(name)
        if strat:
            cards.append((friendly_name(name), strat))
    if not cards:
        return ensure_sections("I can explain listed equity options strategies and evaluate specified legs.")

    setup = " ".join([f"{n}: {s['setup']}" for n, s in cards])
    payoff = " ".join([f"{n}: {s['payoff']}" for n, s in cards])
    max_profit = " ".join([f"{n}: {ensure_sentence(s['max_profit'])}" for n, s in cards])
    max_loss = " ".join([f"{n}: {ensure_sentence(s['max_loss'])}" for n, s in cards])
    breakevens = " ".join([f"{n}: {s['breakevens']}" for n, s in cards])
    sensitivities = " ".join([f"{n}: {s['key_sensitivities']}" for n, s in cards])
    use_case = " ".join([f"{n}: {s['typical_use_case']}" for n, s in cards])
    risks = " ".join([f"{n}: {s['main_risks']}" for n, s in cards])

    return (
        f"Summary: Comparison of {', '.join([n for n, _ in cards])}.\n"
        f"Setup: {setup}\n"
        f"Payoff at Expiration: {payoff}\n"
        f"Max Profit: {max_profit}\n"
        f"Max Loss: {max_loss}\n"
        f"Breakeven(s): {breakevens}\n"
        f"Key Sensitivities: {sensitivities}\n"
        f"Typical Use Case: {use_case}\n"
        f"Main Risks: {risks}\n"
        "Assumptions / What I need from you: If you want payoff calculations, share strikes, expiration, and optional premiums; specify long/short if ambiguous."
    )


def format_comparison_freeform(strategies: list[str]) -> str:
    normalized = [normalize_strategy_name(name) for name in strategies]
    if set(normalized).issubset({"long straddle", "long strangle", "backspread"}):
        parts = []
        if "long straddle" in normalized:
            parts.append(
                "A long straddle buys a call and a put at the same strike, so it is sensitive to moves in either direction."
            )
        if "long strangle" in normalized:
            parts.append(
                "A long strangle uses OTM strikes, so it is cheaper up front but needs a larger move to break even."
            )
        if "backspread" in normalized:
            parts.append(
                "A backspread sells fewer options near the money and buys more further out, so it is built for large moves and stronger convexity beyond the short strike."
            )
            parts.append(
                "Straddles and strangles have losses limited to the premium paid, while a backspread typically has limited loss near the short strike and larger gains if the move is big."
            )
        else:
            parts.append(
                "Both straddles and strangles have losses limited to the premium paid; the strangle is cheaper but needs a bigger move."
            )
        parts.append("Share strikes and expirations if you want exact breakevens or payoff numbers.")
        return " ".join(parts)

    cards = []
    for name in normalized:
        strat = get_strategy(name)
        if strat:
            cards.append((friendly_name(name), strat))
    if not cards:
        return (
            "Tell me which strategies you want compared, and I can walk through the setup, payoff shape, and trade-offs."
        )

    def name_phrase(n: str) -> str:
        return _name_phrase(n)

    def join_clauses(clauses: list[str]) -> str:
        if not clauses:
            return ""
        if len(clauses) == 1:
            return clauses[0]
        if len(clauses) == 2:
            return f"{clauses[0]}, while {clauses[1]}"
        return f"{'; '.join(clauses[:-1])}, and {clauses[-1]}"

    setup_clauses = [f"{name_phrase(n)} {_conjugate_clause(s['setup'])}" for n, s in cards]
    payoff_clauses = []
    for n, s in cards:
        payoff = _normalize_item(s["payoff"])
        if payoff.startswith("profit "):
            payoff = "profits " + payoff[len("profit ") :]
        if payoff.startswith("limited loss"):
            payoff = "has " + payoff
        payoff = payoff.replace(", benefits", " and benefits")
        payoff_clauses.append(f"{name_phrase(n)} {payoff}")

    profit_clauses = [f"{name_phrase(n)} has {_normalize_item(s['max_profit'])}" for n, s in cards]
    loss_clauses = [f"{name_phrase(n)} has {_normalize_item(s['max_loss'])}" for n, s in cards]
    breakeven_clauses = [f"{name_phrase(n)} has breakevens at {_normalize_item(s['breakevens'])}" for n, s in cards]
    use_clauses = [f"{name_phrase(n)} {_use_case_clause(s['typical_use_case'])}" for n, s in cards]
    risk_clauses = [f"{name_phrase(n)} {_risk_clause(s['main_risks'])}" for n, s in cards]

    summary_names = ", ".join([n for n, _ in cards])
    sentences = [
        f"Here is a comparison of {summary_names} and how they differ.",
        f"In setup terms, {join_clauses(setup_clauses)}.",
        f"At expiration, {join_clauses(payoff_clauses)}.",
        f"On the upside, {join_clauses(profit_clauses)}.",
        f"On the downside, {join_clauses(loss_clauses)}.",
        f"Breakevens differ as well: {join_clauses(breakeven_clauses)}.",
        f"Typical use cases: {join_clauses(use_clauses)}.",
        f"Main risks: {join_clauses(risk_clauses)}.",
        "If you want payoff numbers, share strikes, expiration, and premiums.",
    ]
    return " ".join([s for s in sentences if s])


def format_convexity_freeform() -> str:
    return (
        "Convexity is the curvature of a position's P/L as the underlying moves; in options it is driven by gamma (delta changing). "
        "Long options are positive convexity (long gamma), short options are negative convexity (short gamma), and stock is mostly linear. "
        "Positive convexity benefits from large moves, while negative convexity performs best in range-bound markets and with time decay. "
        "Gamma is strongest near the strike and for shorter expirations, and it decays with time and distance from the strike. "
        "If you want convexity for a specific strategy, name it."
    )


def format_convexity_for_strategy_freeform(strategy_name: str) -> str:
    strat = get_strategy(strategy_name)
    if not strat:
        return format_convexity_freeform()
    convexity = "positive" if "positive convexity" in strat["key_sensitivities"].lower() else "negative"
    if "limited positive convexity" in strat["key_sensitivities"].lower():
        convexity = "limited positive"
    if "limited convexity" in strat["key_sensitivities"].lower():
        convexity = "limited"
    setup = _normalize_item(strat["setup"])
    setup = _strip_strategy_subject(setup)
    if setup.startswith(("buy ", "sell ", "own ", "hold ")):
        setup_sentence = f"The setup is to {setup}."
    else:
        setup_sentence = f"The setup is {setup}."
    payoff = _normalize_item(strat["payoff"])
    if payoff.startswith("profit "):
        payoff = "profits " + payoff[len("profit ") :]
    payoff_sentence = f"At expiration, it {payoff}."
    profit_phrase = _normalize_item(strat["max_profit"])
    loss_phrase = _normalize_item(strat["max_loss"])
    convexity_note = convexity_detail_for_strategy(strategy_name)
    return (
        f"A {strategy_name} has {convexity} convexity because of its gamma exposure. {setup_sentence} {payoff_sentence} "
        f"Upside is {profit_phrase}, while downside is {loss_phrase}. {convexity_note} "
        f"Main risks include {_normalize_item(strat['main_risks'])}. If you want to quantify convexity, share strikes and expirations."
    )


def format_basic_option_structured(option_type: str) -> str:
    if option_type == "call":
        return (
            "Summary: A call option gives the right (not the obligation) to buy shares at the strike price by expiration.\n"
            "Setup: Buy a call at a chosen strike and expiration and pay a premium.\n"
            "Payoff at Expiration: If the stock finishes above the strike, the option has intrinsic value; otherwise it expires worthless.\n"
            "Max Profit: Unlimited upside as the stock rises.\n"
            "Max Loss: The premium paid.\n"
            "Breakeven(s): Strike price + premium paid.\n"
            "Key Sensitivities: Positive delta and gamma; negative theta; positive vega.\n"
            "Typical Use Case: A bullish view with defined risk and leveraged upside exposure.\n"
            "Main Risks: Time decay and insufficient upside move before expiration.\n"
            "Assumptions / What I need from you: For exact payoff numbers, share strike, expiration, and premium."
        )
    return (
        "Summary: A put option gives the right (not the obligation) to sell shares at the strike price by expiration.\n"
        "Setup: Buy a put at a chosen strike and expiration and pay a premium.\n"
        "Payoff at Expiration: If the stock finishes below the strike, the option has intrinsic value; otherwise it expires worthless.\n"
        "Max Profit: Large if the stock falls sharply (bounded by the stock going to zero).\n"
        "Max Loss: The premium paid.\n"
        "Breakeven(s): Strike price - premium paid.\n"
        "Key Sensitivities: Negative delta; positive gamma; negative theta; positive vega.\n"
        "Typical Use Case: A bearish view or portfolio downside hedge.\n"
        "Main Risks: Time decay and insufficient downside move before expiration.\n"
        "Assumptions / What I need from you: For exact payoff numbers, share strike, expiration, and premium."
    )


def format_greek_structured(term: str) -> str:
    term = term.lower()
    if term == "delta":
        return (
            "Summary: Delta is the sensitivity of an option's price to a $1 move in the underlying.\n"
            "Setup: Calls have positive delta (0 to 1); puts have negative delta (0 to -1). Delta is higher for ITM options and lower for OTM options.\n"
            "Payoff at Expiration: Before expiration, delta approximates small price changes; as expiration nears, delta can become very steep near the strike.\n"
            "Max Profit: Not a payoff limit; delta is a sensitivity, not a cap.\n"
            "Max Loss: Not a payoff limit; delta does not define max loss.\n"
            "Breakeven(s): Determined by strikes and premiums, not delta alone.\n"
            "Key Sensitivities: Delta increases as options move ITM; gamma measures how fast delta changes; theta and vega also affect price.\n"
            "Typical Use Case: Use delta to gauge directional exposure and approximate hedge ratios.\n"
            "Main Risks: Delta can change quickly near the strike or close to expiration.\n"
            "Assumptions / What I need from you: If you want a delta estimate for a specific option, share strike, expiration, and premium or IV."
        )
    if term == "gamma":
        return (
            "Summary: Gamma is the rate of change of delta as the underlying price moves (it drives convexity).\n"
            "Setup: Gamma is highest for near-the-money options and shorter expirations.\n"
            "Payoff at Expiration: Gamma is strongest near the strike and near expiration, so P/L can accelerate as price moves.\n"
            "Max Profit: Not a payoff limit; gamma describes curvature, not a cap.\n"
            "Max Loss: Not a payoff limit; gamma does not define max loss.\n"
            "Breakeven(s): Determined by strikes and premiums, not gamma alone.\n"
            "Key Sensitivities: Long options have positive gamma (positive convexity); short options have negative gamma (negative convexity). Gamma decays with time.\n"
            "Typical Use Case: Use gamma to understand how sensitive a position is to large price moves.\n"
            "Main Risks: Short gamma positions can lose rapidly if the underlying moves sharply.\n"
            "Assumptions / What I need from you: If you want gamma for a specific option or strategy, share strikes and expirations."
        )
    if term == "theta":
        return (
            "Summary: Theta is the rate of time decay in an option's price, typically quoted per day.\n"
            "Setup: Long options have negative theta; short options have positive theta. Theta is largest near-the-money and for short-dated options.\n"
            "Payoff at Expiration: Time decay accelerates as expiration approaches, especially for ATM options.\n"
            "Max Profit: Not a payoff limit; theta is a sensitivity, not a cap.\n"
            "Max Loss: Not a payoff limit; theta does not define max loss.\n"
            "Breakeven(s): Determined by strikes and premiums, not theta alone.\n"
            "Key Sensitivities: Theta interacts with gamma and vega; higher implied volatility usually increases premiums and absolute theta.\n"
            "Typical Use Case: Use theta to evaluate how much time decay you are paying or collecting.\n"
            "Main Risks: Long options can lose value quickly as expiration nears if the move does not happen.\n"
            "Assumptions / What I need from you: If you want theta for a specific option, share strike, expiration, and IV."
        )
    if term == "vega":
        return (
            "Summary: Vega is the sensitivity of an option's price to a 1% move in implied volatility.\n"
            "Setup: Long options have positive vega; short options have negative vega. Vega is larger for longer-dated and near-the-money options.\n"
            "Payoff at Expiration: Vega matters before expiration; at expiration, implied volatility no longer affects intrinsic value.\n"
            "Max Profit: Not a payoff limit; vega describes volatility sensitivity.\n"
            "Max Loss: Not a payoff limit; vega does not define max loss.\n"
            "Breakeven(s): Determined by strikes and premiums, not vega alone.\n"
            "Key Sensitivities: Longer expirations and ATM strikes increase vega; vega decreases as expiration approaches.\n"
            "Typical Use Case: Use vega to judge exposure to volatility changes or events.\n"
            "Main Risks: Volatility can fall even if price moves in your direction, reducing option value.\n"
            "Assumptions / What I need from you: If you want vega for a specific option, share strike, expiration, and IV."
        )
    if term == "rho":
        return (
            "Summary: Rho is the sensitivity of an option's price to a 1% move in interest rates.\n"
            "Setup: Calls generally have positive rho; puts generally have negative rho. Rho is more significant for longer-dated options.\n"
            "Payoff at Expiration: Rho affects option value before expiration; its impact is usually small for short-dated equity options.\n"
            "Max Profit: Not a payoff limit; rho is a sensitivity.\n"
            "Max Loss: Not a payoff limit; rho does not define max loss.\n"
            "Breakeven(s): Determined by strikes and premiums, not rho alone.\n"
            "Key Sensitivities: Longer expirations increase rho; near-the-money options often show the biggest rho impact.\n"
            "Typical Use Case: Use rho when rates are moving materially or for longer-dated positions.\n"
            "Main Risks: Rho is often a second-order effect relative to delta, theta, and vega.\n"
            "Assumptions / What I need from you: If you want rho for a specific option, share strike and expiration."
        )
    if term == "iv":
        return (
            "Summary: Implied volatility (IV) is the market's priced-in expectation of future movement.\n"
            "Setup: Higher IV raises option premiums; lower IV reduces premiums. IV is not a forecast, but a pricing input.\n"
            "Payoff at Expiration: IV affects option prices before expiration; at expiration, only intrinsic value matters.\n"
            "Max Profit: Not a payoff limit; IV changes can help or hurt before expiration.\n"
            "Max Loss: Not a payoff limit; IV does not define max loss.\n"
            "Breakeven(s): Determined by strikes and premiums; higher IV usually implies higher breakevens for long options.\n"
            "Key Sensitivities: Vega measures IV sensitivity; longer-dated and ATM options have higher vega.\n"
            "Typical Use Case: Use IV to compare option pricing richness/cheapness or to plan around events.\n"
            "Main Risks: IV can fall even if your directional thesis is correct, reducing option value.\n"
            "Assumptions / What I need from you: If you want IV context for a specific option, share strike and expiration."
        )
    return (
        "Summary: The option Greeks describe sensitivities of option prices to price, time, volatility, and rates.\n"
        "Setup: Delta (price), gamma (delta change), theta (time decay), vega (volatility), rho (rates).\n"
        "Payoff at Expiration: Greeks matter most before expiration; at expiration, payoff is intrinsic value only.\n"
        "Max Profit: Not a payoff limit; Greeks describe sensitivities.\n"
        "Max Loss: Not a payoff limit; Greeks do not define max loss.\n"
        "Breakeven(s): Determined by strikes and premiums, not Greeks alone.\n"
        "Key Sensitivities: Gamma drives convexity; theta accelerates near expiration; vega is larger for longer-dated options; rho is usually smaller for equities.\n"
        "Typical Use Case: Use Greeks to understand risk exposure and how it changes with the underlying.\n"
        "Main Risks: Sensitivities can change quickly as price, time, or IV shifts.\n"
        "Assumptions / What I need from you: Ask about a specific Greek if you want more detail."
    )


def format_greek_freeform(term: str) -> str:
    term = term.lower()
    if term == "delta":
        return (
            "Delta tells you roughly how much an option's price changes for a $1 move in the underlying. "
            "Calls have positive delta and puts have negative delta, and delta grows as the option moves deeper in the money. "
            "As expiration approaches, delta can swing quickly near the strike because gamma is higher. "
            "Traders use delta to gauge directional exposure and to size hedges."
        )
    if term == "gamma":
        return (
            "Gamma is how quickly delta changes as the underlying moves, so it is the source of convexity in options. "
            "Gamma is highest for near-the-money options and shorter expirations, which means P/L can accelerate on bigger moves. "
            "Long options are positive gamma; short options are negative gamma and can lose quickly if price moves sharply."
        )
    if term == "theta":
        return (
            "Theta is time decay. Long options usually lose value each day (negative theta), while short options tend to gain value from time passing. "
            "Theta is most intense for near-the-money options and increases as expiration approaches, so a move that is too slow can hurt long positions."
        )
    if term == "vega":
        return (
            "Vega measures sensitivity to implied volatility. Long options benefit when IV rises and are hurt when IV falls; short options have the opposite exposure. "
            "Vega is larger for longer-dated and near-the-money options, and it tends to shrink as expiration approaches."
        )
    if term == "rho":
        return (
            "Rho measures sensitivity to interest rates. Calls generally have positive rho and puts negative rho, but for short-dated equity options the effect is usually small. "
            "Rho becomes more noticeable for longer-dated options or when rates move materially."
        )
    if term == "iv":
        return (
            "Implied volatility is the market's priced-in expectation of future movement. Higher IV makes options more expensive; lower IV makes them cheaper. "
            "IV is not a forecast, and it can drop after events even if price moves in your direction."
        )
    return (
        "The Greeks describe how option prices respond to different factors: delta (price), gamma (delta change/convexity), "
        "theta (time decay), vega (volatility), and rho (interest rates). "
        "They are sensitivities, not payoff limits, and they shift as price, time, and IV change."
    )


def format_greek_comparison_freeform(terms: list[str]) -> str:
    ordered = []
    for term in terms:
        if term not in ordered:
            ordered.append(term)
    term_set = set(ordered)
    if term_set == {"gamma", "convexity"}:
        return (
            "Gamma is how quickly delta changes as price moves, while convexity is the curvature of P/L; "
            "in options, convexity is largely driven by gamma. "
            "Gamma is highest near the money and for shorter expirations, which is why long options have stronger convexity there. "
            "Short options are negative gamma, so their convexity works against them on large moves."
        )
    if term_set == {"vega", "gamma"}:
        return (
            "Vega measures sensitivity to implied volatility, while gamma measures how delta changes as price moves. "
            "Gamma is highest near-the-money and short-dated, while vega is larger for longer-dated and near-the-money options. "
            "Long options are positive gamma and positive vega; short options are negative on both."
        )
    if term_set == {"vega", "gamma", "convexity"}:
        return (
            "Gamma measures how quickly delta changes and is the driver of convexity, while vega measures sensitivity to implied volatility. "
            "Gamma is highest near-the-money and short-dated; vega is larger for longer-dated options. "
            "Long options are positive gamma and vega; short options are negative, which is why short volatility trades have negative convexity."
        )

    def_sentence = {
        "delta": "Delta measures directional sensitivity to price moves.",
        "gamma": "Gamma measures how quickly delta changes as price moves.",
        "theta": "Theta measures time decay in option value.",
        "vega": "Vega measures sensitivity to implied volatility.",
        "rho": "Rho measures sensitivity to interest rates.",
        "iv": "Implied volatility is the market's priced-in expectation of future movement.",
        "convexity": "Convexity is the curvature of P/L, and in options it's largely driven by gamma.",
    }
    sentences = []
    first = " ".join(def_sentence.get(term, term) for term in ordered)
    if first:
        sentences.append(first)
    if "gamma" in term_set:
        sentences.append("Gamma is highest for near-the-money, short-dated options, so convexity is strongest there.")
    if "vega" in term_set:
        sentences.append("Vega is larger for longer-dated and near-the-money options and shrinks into expiration.")
    if "theta" in term_set:
        sentences.append("Theta tends to be most intense near the money and accelerates as expiration approaches.")
    if "rho" in term_set:
        sentences.append("Rho is usually smaller for short-dated equity options but matters more for long-dated contracts.")
    if "convexity" in term_set and "gamma" not in term_set:
        sentences.append("Convexity describes how P/L accelerates with price moves; long options are positive convexity and short options are negative.")
    return " ".join([s for s in sentences if s]).strip()


def format_greek_comparison_structured(terms: list[str]) -> str:
    ordered = []
    for term in terms:
        if term not in ordered:
            ordered.append(term)
    label = " and ".join([t.upper() if t == "iv" else t.capitalize() for t in ordered])
    return ensure_sections(
        f"Comparison of {label}. "
        f"{format_greek_comparison_freeform(ordered)}"
    )


def format_ambiguous_put_spread_structured() -> str:
    return (
        "Summary: An 'OTM put spread' can refer to either a bull put (credit) spread or a bear put (debit) spread.\n"
        "Setup: Bull put spread sells a higher-strike put and buys a lower-strike put; bear put spread buys a higher-strike put and sells a lower-strike put.\n"
        "Payoff at Expiration: Bull put spread benefits if price stays above the short put; bear put spread benefits if price falls toward or below the long put.\n"
        "Max Profit: Bull put spread is the net credit; bear put spread is strike width minus net debit.\n"
        "Max Loss: Bull put max loss is strike width minus credit; bear put max loss is the net debit.\n"
        "Breakeven(s): Bull put spread breakeven is short strike minus credit; bear put spread breakeven is long strike minus debit.\n"
        "Key Sensitivities: Bull put is short gamma (negative convexity); bear put is long gamma (positive convexity).\n"
        "Typical Use Case: Bull put fits neutral-to-bullish views; bear put fits bearish views.\n"
        "Main Risks: Bull put risks losses on a sharp drop; bear put risks time decay if the move is insufficient.\n"
        "Assumptions / What I need from you: Tell me whether you mean bull put (credit) or bear put (debit), plus strikes/expiration if you want calculations."
    )


def format_ambiguous_call_spread_structured() -> str:
    return (
        "Summary: An 'OTM call spread' can refer to either a bull call (debit) spread or a bear call (credit) spread.\n"
        "Setup: Bull call spread buys a lower-strike call and sells a higher-strike call; bear call spread sells a lower-strike call and buys a higher-strike call.\n"
        "Payoff at Expiration: Bull call spread benefits from a rise up to the short strike; bear call spread benefits if price stays below the short call.\n"
        "Max Profit: Bull call spread is strike width minus net debit; bear call spread is the net credit.\n"
        "Max Loss: Bull call max loss is the net debit; bear call max loss is strike width minus credit.\n"
        "Breakeven(s): Bull call spread breakeven is long strike plus debit; bear call spread breakeven is short strike plus credit.\n"
        "Key Sensitivities: Bull call is long gamma (positive convexity) but capped; bear call is short gamma (negative convexity).\n"
        "Typical Use Case: Bull call fits bullish views; bear call fits neutral-to-bearish views.\n"
        "Main Risks: Bull call risks time decay if the rise is insufficient; bear call risks losses on a sharp rally.\n"
        "Assumptions / What I need from you: Tell me whether you mean bull call (debit) or bear call (credit), plus strikes/expiration if you want calculations."
    )


def format_strategy_view_freeform(strategy_name: str) -> str:
    view = strategy_view_label(strategy_name)
    name = friendly_name(strategy_name)
    if not view:
        return (
            f"{name} can be used in different ways depending on strikes and intent; share your view and I can be more specific."
        )
    if normalize_strategy_name(strategy_name) == "ratio spread":
        return (
            "A ratio spread is directional: a call ratio is bullish, while a put ratio is bearish, and the short strike is where risk concentrates."
        )
    if normalize_strategy_name(strategy_name) == "backspread":
        return (
            "A backspread is a volatility-seeking directional trade: call backspreads are bullish and put backspreads are bearish, with convexity beyond the short strike."
        )
    return f"{name} expresses a {view} market view."


def format_strategy_view_structured(strategy_name: str) -> str:
    name = friendly_name(strategy_name)
    view = strategy_view_label(strategy_name)
    if normalize_strategy_name(strategy_name) == "ratio spread":
        summary = "A ratio spread is directional: call ratios are bullish and put ratios are bearish, with risk concentrated near the short strike."
    elif normalize_strategy_name(strategy_name) == "backspread":
        summary = "A backspread is a volatility-seeking directional trade: call backspreads are bullish and put backspreads are bearish."
    else:
        summary = f"{name} expresses a {view} market view." if view else f"{name} can express different views depending on strikes."
    return ensure_sections(summary)


def strategy_view_label(strategy_name: str) -> str:
    name = normalize_strategy_name(strategy_name)
    view_map = {
        "long call": "bullish",
        "long put": "bearish",
        "covered call": "neutral to mildly bullish income",
        "cash-secured put": "bullish to neutral income",
        "bull call spread": "bullish",
        "bear call spread": "bearish to neutral income",
        "bull put spread": "bullish to neutral income",
        "bear put spread": "bearish",
        "long straddle": "volatile (long volatility)",
        "long strangle": "volatile (long volatility)",
        "short straddle": "neutral to short volatility",
        "short strangle": "neutral to short volatility",
        "iron condor": "neutral to short volatility",
        "iron butterfly": "neutral to short volatility",
        "collar": "defensive/neutral with capped upside",
        "protective put": "bullish with downside protection",
        "calendar spread": "neutral to mildly directional (often slightly bullish or bearish depending on strikes)",
        "diagonal spread": "neutral to mildly directional (often slightly bullish or bearish depending on strikes)",
        "ratio spread": "directional with tail risk (depends on call/put)",
        "backspread": "volatile with directional convexity (depends on call/put)",
    }
    return view_map.get(name, "")


def format_convexity_structured() -> str:
    return (
        "Summary: Convexity describes how the slope of a position changes as the underlying price moves; in options it is tied to gamma (delta changing).\n"
        "Setup: Long options are generally positive convexity (long gamma); short options are negative convexity (short gamma); stock is mostly linear with low convexity.\n"
        "Payoff at Expiration: Positive convexity benefits from large moves; negative convexity performs best when price stays range-bound.\n"
        "Max Profit: With positive convexity, gains accelerate as moves get larger; with negative convexity, profit is typically capped at the credit received.\n"
        "Max Loss: Positive convexity is often limited to the premium paid; negative convexity can be large or unlimited without defined risk.\n"
        "Breakeven(s): Depend on strikes and premiums; convexity determines how quickly P/L improves beyond breakevens.\n"
        "Key Sensitivities: Gamma is strongest near-the-money and for shorter-dated options, then decays as time passes or the option goes far ITM/OTM. Higher implied volatility raises option prices; realizing a gain still depends on the move exceeding what was implied.\n"
        "Typical Use Case: Use positive convexity when you expect a large move or volatility expansion; use negative convexity when you expect range-bound prices and stable/declining volatility.\n"
        "Main Risks: Positive convexity pays for time and can decay; negative convexity can suffer sharp losses on large moves or volatility spikes.\n"
        "Assumptions / What I need from you: If you want convexity for a specific strategy, name it (e.g., long straddle, iron condor)."
    )


def format_payoff_mechanics_structured() -> str:
    return (
        "Summary: Option payoffs at expiration depend only on the underlying price relative to strikes and the premiums paid.\n"
        "Setup: Long options have limited loss (premium) and directional exposure; short options collect premium but carry the opposite risk.\n"
        "Payoff at Expiration: Calls are worth max(price - strike, 0); puts are worth max(strike - price, 0). Spreads cap gains/losses by adding a short leg.\n"
        "Max Profit: Long calls have uncapped upside; long puts are capped by the stock going to zero; spreads cap profit by strike width.\n"
        "Max Loss: Long options lose the premium; credit spreads lose up to width minus credit; short naked options can be large or unlimited.\n"
        "Breakeven(s): Strikes plus/minus net premium; spreads shift breakevens by the credit or debit.\n"
        "Key Sensitivities: Delta drives direction, gamma/convexity drives curvature, theta and vega affect value before expiration.\n"
        "Typical Use Case: Use payoff mechanics to understand risk/reward before selecting strikes and expirations.\n"
        "Main Risks: Misjudging the move size or timing, and overestimating what premiums can cover.\n"
        "Assumptions / What I need from you: If you want exact payoff numbers, provide the legs and optional premiums."
    )


def format_payoff_mechanics_freeform() -> str:
    return (
        "At expiration, option payoffs depend only on where the stock finishes versus the strikes and the premiums you paid or received. "
        "Calls are worth max(price - strike, 0) and puts are worth max(strike - price, 0), while spreads cap gains and losses by pairing a long and short leg. "
        "Long options have limited loss (the premium) and directional upside, while short options collect premium but take the opposite risk. "
        "Breakevens are the strike adjusted by the net premium. If you want exact numbers, share the legs and premiums."
    )


def format_long_short_comparison_structured() -> str:
    return (
        "Summary: Long options have limited loss and directional upside; short options collect premium but face the opposite risk profile.\n"
        "Setup: Long calls/puts pay a premium; short calls/puts receive premium and take assignment risk.\n"
        "Payoff at Expiration: Long options gain if the move is large enough; short options profit if the move is limited and time decay helps.\n"
        "Max Profit: Long calls are uncapped; long puts are capped by the stock going to zero; short options are capped at the premium received.\n"
        "Max Loss: Long options lose the premium; short calls can be unlimited; short puts can be large if the stock falls.\n"
        "Breakeven(s): Long options breakeven is strike +/- premium; short options breakeven is strike +/- credit.\n"
        "Key Sensitivities: Long options are positive convexity (long gamma) with negative theta; short options are the opposite.\n"
        "Typical Use Case: Long options for directional or volatility bets; short options for income or range-bound views.\n"
        "Main Risks: Long options can decay; short options can suffer large losses on sharp moves.\n"
        "Assumptions / What I need from you: If you want a specific comparison, name the exact structure."
    )


def format_long_short_comparison_freeform() -> str:
    return (
        "Long options pay a premium for limited loss and upside exposure, while short options collect premium but carry the opposite risk. "
        "A long call can make unlimited upside and loses only the premium; a short call keeps the premium but can face unlimited loss. "
        "Long puts are capped by the stock going to zero, while short puts can lose heavily on a big drop. "
        "Long options are positive convexity with negative theta; short options are negative convexity with positive theta."
    )


def format_convexity_for_strategy_structured(strategy_name: str) -> str:
    strat = get_strategy(strategy_name)
    if not strat:
        return format_convexity_structured()
    convexity = "positive" if "positive convexity" in strat["key_sensitivities"].lower() else "negative"
    if "limited positive convexity" in strat["key_sensitivities"].lower():
        convexity = "limited positive"
    if "limited convexity" in strat["key_sensitivities"].lower():
        convexity = "limited"
    convexity_note = convexity_detail_for_strategy(strategy_name)
    return (
        f"Summary: The {strategy_name} has {convexity} convexity driven by its gamma exposure.\n"
        f"Setup: {strat['setup']}\n"
        f"Payoff at Expiration: {strat['payoff']}\n"
        f"Max Profit: {ensure_sentence(strat['max_profit'])} Convexity explains how quickly P/L accelerates as price moves.\n"
        f"Max Loss: {ensure_sentence(strat['max_loss'])} Long premium positions limit loss; short premium can be large.\n"
        f"Breakeven(s): {strat['breakevens']}\n"
        f"Key Sensitivities: {strat['key_sensitivities']} {convexity_note}\n"
        f"Typical Use Case: {strat['typical_use_case']}\n"
        f"Main Risks: {strat['main_risks']}\n"
        "Assumptions / What I need from you: If you want to quantify convexity, share strikes and expirations so I can discuss gamma exposure."
    )


def convexity_detail_for_strategy(strategy_name: str) -> str:
    name = (strategy_name or "").lower()
    if name == "long straddle":
        return (
            "Convexity is strongest near the shared strike and decays with time if the stock stays near that level."
        )
    if name == "long strangle":
        return (
            "Convexity starts lower because the strikes are further OTM, but it increases as price approaches either strike."
        )
    if name in ("short straddle", "short strangle", "iron condor", "iron butterfly"):
        return (
            "Negative convexity means small moves are fine, but losses accelerate as price moves away from the short strikes."
        )
    if name in ("bull call spread", "bear put spread"):
        return (
            "Convexity is limited: you are long gamma between strikes, but the short leg caps acceleration beyond the short strike."
        )
    if name in ("bull put spread", "bear call spread", "covered call", "cash-secured put"):
        return (
            "Negative convexity comes from the short option; gamma works against you as price moves toward the short strike."
        )
    if name in ("calendar spread", "diagonal spread"):
        return (
            "Convexity depends on where price sits relative to the short strike and how volatility shifts; the front-month option tends to carry the most gamma."
        )
    if name in ("ratio spread", "backspread"):
        return (
            "Convexity can flip: risk is greatest near the short strike, but beyond it the extra long options add positive convexity."
        )
    return "Convexity is strongest near the strike for at-the-money options and decays with time."


def friendly_name(name: str) -> str:
    return name.title()


def detect_basic_option_question(text: str) -> str | None:
    lower = (text or "").lower()
    if extract_strategies(lower):
        return None
    if "call" in lower and ("option" in lower or "how does" in lower or "what is" in lower or "explain" in lower):
        if any(term in lower for term in ["spread", "covered", "straddle", "strangle", "calendar", "diagonal", "ratio", "backspread"]):
            return None
        return "call"
    if "put" in lower and ("option" in lower or "how does" in lower or "what is" in lower or "explain" in lower):
        if any(term in lower for term in ["spread", "protective", "cash-secured", "straddle", "strangle", "calendar", "diagonal", "ratio", "backspread"]):
            return None
        return "put"
    return None


def is_convexity_question(text: str) -> bool:
    lower = (text or "").lower()
    return "convexity" in lower or "gamma" in lower or "convex" in lower


def is_ambiguous_put_spread(text: str) -> bool:
    lower = (text or "").lower()
    return "put spread" in lower and "bull" not in lower and "bear" not in lower


def is_ambiguous_call_spread(text: str) -> bool:
    lower = (text or "").lower()
    return "call spread" in lower and "bull" not in lower and "bear" not in lower


def detect_greek_term(text: str) -> str | None:
    import re

    lower = (text or "").lower()
    if "implied volatility" in lower or re.search(r"\biv\b", lower):
        return "iv"
    if re.search(r"\bdelta\b", lower):
        return "delta"
    if re.search(r"\bgamma\b", lower):
        return "gamma"
    if re.search(r"\btheta\b", lower):
        return "theta"
    if re.search(r"\bvega\b", lower):
        return "vega"
    if re.search(r"\brho\b", lower):
        return "rho"
    if "greeks" in lower or "the greeks" in lower:
        return "greeks"
    return None


def extract_greek_terms(text: str) -> list[str]:
    import re

    lower = (text or "").lower()
    if not lower.strip():
        return []
    patterns = [
        ("convexity", re.compile(r"\bconvexity\b|\bconvex\b")),
        ("iv", re.compile(r"\bimplied volatility\b|\biv\b")),
        ("delta", re.compile(r"\bdelta\b")),
        ("gamma", re.compile(r"\bgamma\b")),
        ("theta", re.compile(r"\btheta\b")),
        ("vega", re.compile(r"\bvega\b")),
        ("rho", re.compile(r"\brho\b")),
    ]
    hits: list[tuple[int, str]] = []
    for term, pattern in patterns:
        match = pattern.search(lower)
        if match:
            hits.append((match.start(), term))
    if "greeks" in lower and not hits:
        return ["greeks"]
    return [t for _, t in sorted(hits, key=lambda x: x[0])]


def is_strike_selection_question(text: str) -> bool:
    lower = (text or "").lower()
    return any(term in lower for term in ["strike selection", "strike price", "strike", "strikes", "otm", "itm", "atm"])


def is_expiration_selection_question(text: str) -> bool:
    lower = (text or "").lower()
    return any(term in lower for term in ["expiration", "expiry", "dte", "time to expiration", "time horizon"])


def is_comparison_request(text: str) -> bool:
    lower = (text or "").lower()
    return any(t in lower for t in ["compare", "difference", "vs", "versus"])


def extract_strategies(text: str) -> list[str]:
    lower = (text or "").lower()
    found: list[str] = []
    for name in STRATEGIES.keys():
        if name in lower:
            found.append(name)
    for alias, canonical in ALIASES.items():
        if alias in lower:
            found.append(canonical)
    seen = set()
    ordered = []
    for item in found:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def extract_percent_range(text: str) -> tuple[int, int] | None:
    import re

    if not text:
        return None
    pattern = re.compile(r"(\d+(?:\.\d+)?)\s*%?\s*(?:-|to)\s*(\d+(?:\.\d+)?)\s*%")
    match = pattern.search(text)
    if match:
        low = float(match.group(1))
        high = float(match.group(2))
        if low > high:
            low, high = high, low
        return int(round(low)), int(round(high))
    single = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    if single:
        val = float(single.group(1))
        return int(round(val)), int(round(val))
    return None


def default_percent_range(view: str) -> tuple[int, int]:
    if view in ("bullish", "bearish"):
        return 10, 20
    if view == "neutral":
        return 5, 15
    if view == "short_vol":
        return 5, 15
    return 5, 10


def compute_moneyness_range(user_range: tuple[int, int] | None, view: str) -> tuple[int, int, str]:
    if user_range:
        low, high = user_range
        if low == high:
            target = low
            low = max(1, int(round(target * 0.75)))
            high = max(low + 2, int(round(target * 1.25)))
            note = f" I mapped your ~{target}% target to a {low}-{high}% band for strike spacing."
        else:
            note = ""
        return low, high, note
    low, high = default_percent_range(view)
    return low, high, ""


def extract_horizon(text: str) -> str | None:
    import re

    if not text:
        return None
    pattern = re.compile(r"(\d+(?:\.\d+)?)\s*[- ]?\s*(day|week|month|year)s?", re.IGNORECASE)
    match = pattern.search(text)
    if not match:
        return None
    value = match.group(1)
    unit = match.group(2).lower()
    return f"{value} {unit}{'' if value == '1' else 's'}"


def mentions_strategy(text: str) -> bool:
    lower = (text or "").lower()
    for name in STRATEGIES.keys():
        if name in lower:
            return True
    for alias in ALIASES.keys():
        if alias in lower:
            return True
    return False


def asks_for_strategies(text: str) -> bool:
    lower = (text or "").lower()
    triggers = [
        "strategy",
        "strategies",
        "trade",
        "trades",
        "ideas",
        "consider",
        "best",
        "good",
        "recommend",
        "outlook",
        "view",
        "bullish",
        "bearish",
        "neutral",
        "volatile",
    ]
    return any(t in lower for t in triggers)


def detect_view_from_text(text: str) -> str | None:
    lower = text.lower()
    if "sell volatility" in lower or "selling volatility" in lower or "short vol" in lower or "short volatility" in lower:
        return "short_vol"
    if "bullish" in lower:
        return "bullish"
    if "bearish" in lower:
        return "bearish"
    if "neutral" in lower or "range" in lower or "sideways" in lower:
        return "neutral"
    if "volatile" in lower or "volatility" in lower or "big move" in lower:
        return "volatile"
    if any(w in lower for w in ["increase", "up", "rise", "rally", "gain"]):
        return "bullish"
    if any(w in lower for w in ["decrease", "down", "fall", "drop", "decline"]):
        return "bearish"
    return None


def ensure_sentence(text: str) -> str:
    clean = text.strip()
    return clean if clean.endswith(".") else f"{clean}."


def format_history(history: list[dict], max_chars: int = 2000) -> str:
    lines = []
    total = 0
    for turn in history[-12:]:
        role = turn.get("role", "user")
        content = turn.get("text", "").strip()
        if not content:
            continue
        line = f"{role.title()}: {content}"
        if total + len(line) + 1 > max_chars:
            break
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines)


def history_text(history: list[dict]) -> str:
    return " ".join([turn.get("text", "") for turn in history if turn.get("text")])


def history_user_text(history: list[dict]) -> str:
    return " ".join([turn.get("text", "") for turn in history if turn.get("role") == "user" and turn.get("text")])


def asked_horizon_before(history: list[dict]) -> bool:
    for turn in history:
        if turn.get("role") != "assistant":
            continue
        text = (turn.get("text") or "").lower()
        if "time horizon" in text or "horizon" in text:
            return True
    return False


def strip_json_block(text: str) -> str:
    if "```json" not in text:
        return text
    return text.split("```json")[0].strip()


def _summarize_user_turn(req: ChatRequest, user_text: str) -> str:
    if user_text.strip():
        return user_text.strip()
    pieces = []
    if req.view:
        pieces.append(f"view={req.view}")
    if req.strategy:
        pieces.append(f"strategy={req.strategy}")
    if pieces:
        return "Strategy builder selection: " + ", ".join(pieces)
    return "User request"


def to_freeform(text: str, keep_json: bool = False) -> str:
    if not text:
        return ""

    json_block = ""
    if keep_json and "```json" in text:
        parts = text.split("```json")
        text = parts[0]
        json_block = "```json" + "```json".join(parts[1:])

    sections = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        for label in [
            "Summary:",
            "Setup:",
            "Payoff at Expiration:",
            "Max Profit:",
            "Max Loss:",
            "Breakeven(s):",
            "Key Sensitivities:",
            "Typical Use Case:",
            "Main Risks:",
            "Assumptions / What I need from you:",
        ]:
            if line.startswith(label):
                key = label[:-1]
                sections[key] = line[len(label):].strip()
                break

    paragraph = ""
    summary = sections.get("Summary", "")
    if summary.lower().startswith("comparison of"):
        paragraph = to_freeform_comparison(sections)
    else:
        paragraph = to_freeform_general(sections)

    if not paragraph:
        paragraph = text.strip()

    if json_block:
        paragraph = f"{paragraph}\n\n{json_block}"

    return paragraph


def _clean_sentence(text: str) -> str:
    s = text.strip()
    return s[:-1] if s.endswith(".") else s


def _normalize_item(text: str) -> str:
    content = _clean_sentence(text)
    if content and content[0].isupper() and not (len(content) > 1 and content[1].isupper()):
        content = content[0].lower() + content[1:]
    content = content.replace(" Put strike", " put strike").replace(" Call strike", " call strike")
    for verb in ["Buy", "Sell", "Own", "Hold", "Profit", "Benefits", "Benefit", "Seeks", "Expecting"]:
        if content.startswith(verb + " "):
            return verb.lower() + content[len(verb):]
    return content


def _should_prefix_it(text: str) -> bool:
    lower = text.strip().lower()
    if lower.startswith(
        (
            "if ",
            "when ",
            "because ",
            "as ",
            "for ",
            "with ",
            "by ",
            "high ",
            "low ",
            "gamma ",
            "delta ",
            "theta ",
            "vega ",
            "rho ",
            "implied ",
            "time ",
            "volatility ",
            "positive ",
            "negative ",
            "long ",
            "short ",
        )
    ):
        return False
    return True


def _conjugate_clause(text: str) -> str:
    clause = _normalize_item(text)
    replacements = {
        "buy ": "buys ",
        "sell ": "sells ",
        "profit ": "profits ",
        "benefit ": "benefits ",
        "seek ": "seeks ",
        "expect ": "expects ",
    }
    for start, repl in replacements.items():
        if clause.startswith(start):
            clause = repl + clause[len(start):]
            break
    clause = clause.replace(" buy ", " buys ").replace(" sell ", " sells ")
    clause = clause.replace(", buys", " and buys").replace(", sells", " and sells")
    if clause.startswith("expecting"):
        return "is typically used when " + clause
    clause = clause.replace(" OTM call", " an OTM call").replace(" OTM put", " an OTM put")
    clause = clause.replace(" ATM call", " an ATM call").replace(" ATM put", " an ATM put")
    clause = clause.replace(" ITM call", " an ITM call").replace(" ITM put", " an ITM put")
    clause = clause.replace(" with same ", " with the same ")
    return clause


def _with_article(summary: str) -> str:
    s = _clean_sentence(summary)
    if s and s[0].isupper():
        s = s[0].lower() + s[1:]
    lower = s.lower()
    if lower.startswith(("a ", "an ", "the ")):
        return s
    article = "an" if lower[:1] in ("a", "e", "i", "o", "u") else "a"
    return f"{article} {s}"


def _name_phrase(name: str) -> str:
    if not name:
        return name
    lower = name.lower()
    if lower.startswith(("a ", "an ", "the ")):
        return lower
    return f"a {lower}"


def _use_case_clause(text: str) -> str:
    s = _normalize_item(text)
    if s.startswith("expecting"):
        s = s.replace("expecting big ", "expecting a big ")
        s = s.replace("expecting large ", "expecting a large ")
        s = s.replace("cheaper than straddle", "cheaper than a straddle")
        s = s.replace("cheaper than strangle", "cheaper than a strangle")
        return "is typically used when " + s
    if s.startswith("used"):
        return "is " + s
    return "is typically used when " + s


def _risk_clause(text: str) -> str:
    s = _normalize_item(text)
    s = s.replace("; need larger", " and it may need a larger")
    s = s.replace("; need", " and it may need")
    if s.startswith("time decay") or s.startswith("loss") or s.startswith("insufficient"):
        return "faces " + s
    return s


def _parse_labeled_items(text: str) -> list[tuple[str, str]]:
    import re

    items = []
    if not text:
        return items
    pattern = re.compile(r"([A-Za-z][A-Za-z0-9 /\\-]+):")
    matches = list(pattern.finditer(text))
    for i, match in enumerate(matches):
        name = match.group(1).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[start:end].strip()
        items.append((name, content))
    return items


def _names_from_summary(summary: str) -> list[str]:
    import re

    s = summary.replace("Comparison of", "").strip().rstrip(".")
    if not s:
        return []
    parts = re.split(r",| and ", s)
    return [p.strip() for p in parts if p.strip()]


def to_freeform_comparison(sections: dict) -> str:
    names = _names_from_summary(sections.get("Summary", ""))
    setup_items = _parse_labeled_items(sections.get("Setup", ""))
    payoff_items = _parse_labeled_items(sections.get("Payoff at Expiration", ""))
    max_profit_items = _parse_labeled_items(sections.get("Max Profit", ""))
    max_loss_items = _parse_labeled_items(sections.get("Max Loss", ""))
    breakeven_items = _parse_labeled_items(sections.get("Breakeven(s)", ""))
    use_items = _parse_labeled_items(sections.get("Typical Use Case", ""))
    risk_items = _parse_labeled_items(sections.get("Main Risks", ""))
    assumptions = sections.get("Assumptions / What I need from you", "")

    def sentence_for_items(prefix: str, items: list[tuple[str, str]], conjugate: bool = False) -> str:
        if conjugate:
            cleaned = [(n, _conjugate_clause(v)) for n, v in items if v]
        else:
            cleaned = [(n, _normalize_item(v)) for n, v in items if v]
        if not cleaned:
            return ""
        def name(n: str) -> str:
            return _name_phrase(n)
        if len(cleaned) == 1:
            n, v = cleaned[0]
            return f"{prefix} {name(n)} {v}."
        if len(cleaned) == 2:
            (n1, v1), (n2, v2) = cleaned
            return f"{prefix} {name(n1)} {v1}, while {name(n2)} {v2}."
        parts = [f"{name(n)} {v}" for n, v in cleaned]
        return f"{prefix} {', '.join(parts[:-1])}, and {parts[-1]}."

    def sentence_for_limits(label: str, items: list[tuple[str, str]]) -> str:
        cleaned = [(n, _normalize_item(v)) for n, v in items if v]
        if not cleaned:
            return ""
        if len(cleaned) == 1:
            n, v = cleaned[0]
            return f"{label} is {v} for {_name_phrase(n)}."
        if len(cleaned) == 2:
            (n1, v1), (n2, v2) = cleaned
            if _normalize_item(v1) == _normalize_item(v2):
                return f"{label} is {_normalize_item(v1)}."
            return f"{label} is {v1} for {_name_phrase(n1)}, while for {_name_phrase(n2)} it is {v2}."
        parts = [f"{v} for {_name_phrase(n)}" for n, v in cleaned]
        return f"{label} is {', '.join(parts[:-1])}, and {parts[-1]}."

    sentences = []
    # First sentence: high-level answer
    summary_sentence = ""
    if names:
        if len(names) == 2:
            left = get_strategy(normalize_strategy_name(names[0].lower()))
            right = get_strategy(normalize_strategy_name(names[1].lower()))
            if left and right:
                left_summary = _with_article(left["summary"])
                right_summary = _with_article(right["summary"])
                summary_sentence = f"{names[0]} is {left_summary}, while {names[1]} is {right_summary}."
            else:
                summary_sentence = f"This compares {names[0]} and {names[1]} and how their setups and payoffs differ."
        else:
            summary_sentence = f"This compares {', '.join(names)} and how their setups and payoffs differ."
    if summary_sentence:
        sentences.append(summary_sentence)

    if setup_items:
        sentences.append(sentence_for_items("In setup terms,", setup_items, conjugate=True))
    if payoff_items:
        sentences.append(sentence_for_items("At expiration,", payoff_items, conjugate=True))
    if max_profit_items:
        sentences.append(sentence_for_limits("Upside", max_profit_items))
    if max_loss_items:
        sentences.append(sentence_for_limits("Downside", max_loss_items))
    if breakeven_items:
        if len(breakeven_items) == 2:
            (n1, v1), (n2, v2) = breakeven_items
            sentences.append(
                f"Breakevens are at {_normalize_item(v1)} for {_name_phrase(n1)}, and at {_normalize_item(v2)} for {_name_phrase(n2)}."
            )
        else:
            sentences.append(sentence_for_items("Breakevens are", breakeven_items))
    if use_items:
        if len(use_items) == 2:
            (n1, v1), (n2, v2) = use_items
            sentences.append(
                f"A common use case is that {_name_phrase(n1)} {_use_case_clause(v1)}, whereas {_name_phrase(n2)} {_use_case_clause(v2)}."
            )
        else:
            sentences.append(sentence_for_items("A common use case is", use_items, conjugate=True))
    if risk_items:
        if len(risk_items) == 2:
            (n1, v1), (n2, v2) = risk_items
            sentences.append(
                f"Main risks include that {_name_phrase(n1)} {_risk_clause(v1)}, whereas {_name_phrase(n2)} {_risk_clause(v2)}."
            )
        else:
            sentences.append(sentence_for_items("Main risks include", risk_items, conjugate=True))
    if assumptions:
        sentences.append(_clean_sentence(assumptions) + ".")

    return " ".join([s for s in sentences if s]).strip()


def to_freeform_general(sections: dict) -> str:
    sentences = []
    if sections.get("Summary"):
        summary_raw = sections["Summary"].strip()
        summary_clean = _clean_sentence(summary_raw)
        summary_lower = summary_clean.lower()
        if summary_lower.startswith(
            (
                "here is",
                "this is",
                "comparison of",
                "convexity ",
                "strike selection",
                "expiration selection",
                "expiration choice",
                "income-focused",
                "income strategies",
                "option payoffs",
            )
        ):
            sentences.append(summary_clean + ".")
        elif summary_clean.startswith(("A ", "An ", "The ")) or " is " in summary_clean or " are " in summary_clean:
            sentences.append(f"{summary_clean}.")
        else:
            summary_phrase = _with_article(summary_clean)
            if summary_phrase:
                sentences.append(f"It's {summary_phrase}.")
    if sections.get("Setup"):
        setup_raw = sections["Setup"]
        if "Best match:" in setup_raw:
            parts = setup_raw.split("Alternatives:")
            best = parts[0].replace("Best match:", "").strip()
            alt = parts[1].strip() if len(parts) > 1 else ""
            best = _normalize_item(best)
            sentences.append(f"A good starting point is {best}.")
            if alt:
                sentences.append(f"Alternatives include {_normalize_item(alt)}.")
        else:
            setup = _normalize_item(setup_raw)
            setup = _strip_strategy_subject(setup)
            if setup.startswith(("buy ", "sell ", "own ", "hold ")):
                sentences.append(f"The setup is to {setup}.")
            else:
                sentences.append(f"The setup is {setup}.")
    if sections.get("Payoff at Expiration"):
        payoff = _normalize_item(sections["Payoff at Expiration"])
        payoff_lower = payoff.lower()
        if payoff.startswith("profit rises"):
            payoff = payoff.replace("profit rises", "profits as price rises", 1)
        if payoff.startswith("profit "):
            payoff = "profits " + payoff[len("profit ") :]
        if "expiration" in payoff_lower or "expires" in payoff_lower:
            sentences.append(_clean_sentence(payoff) + ".")
        elif ";" in payoff:
            sentences.append(f"At expiration, {payoff}.")
        else:
            if _should_prefix_it(payoff):
                sentences.append(f"At expiration, it {payoff}.")
            else:
                sentences.append(f"At expiration, {payoff}.")
    if sections.get("Max Profit") or sections.get("Max Loss"):
        max_profit = sections.get("Max Profit")
        max_loss = sections.get("Max Loss")
        if max_profit and max_loss:
            profit_phrase = _normalize_item(max_profit)
            loss_phrase = _normalize_item(max_loss)
            if any(term in profit_phrase for term in ["with ", "for ", "positive", "negative", "convexity"]) or any(
                term in loss_phrase for term in ["with ", "for ", "positive", "negative", "convexity"]
            ):
                sentences.append(f"On the upside, {profit_phrase}.")
                sentences.append(f"On the downside, {loss_phrase}.")
            else:
                sentences.append(f"Upside is {profit_phrase}, while downside is {loss_phrase}.")
        elif max_profit:
            profit_phrase = _normalize_item(max_profit)
            if any(term in profit_phrase for term in ["with ", "for ", "positive", "negative", "convexity"]):
                sentences.append(f"On the upside, {profit_phrase}.")
            else:
                sentences.append(f"Upside is {profit_phrase}.")
        elif max_loss:
            loss_phrase = _normalize_item(max_loss)
            if any(term in loss_phrase for term in ["with ", "for ", "positive", "negative", "convexity"]):
                sentences.append(f"On the downside, {loss_phrase}.")
            else:
                sentences.append(f"Downside is {loss_phrase}.")
    if sections.get("Breakeven(s)"):
        breakeven = _normalize_item(sections["Breakeven(s)"])
        if breakeven.startswith("depend"):
            cleaned = breakeven.replace("depend on", "", 1).strip()
            sentences.append(f"Breakevens depend on {cleaned}.")
        else:
            sentences.append(f"Breakevens are {breakeven}.")
    if sections.get("Key Sensitivities"):
        sentences.append(f"Key sensitivities include {_normalize_item(sections['Key Sensitivities'])}.")
    if sections.get("Typical Use Case"):
        use_case = _normalize_item(sections["Typical Use Case"])
        if use_case.startswith("expecting"):
            use_case = use_case.replace("expecting big ", "expecting a big ")
            use_case = use_case.replace("expecting large ", "expecting a large ")
            sentences.append(f"A common use case is when {use_case}.")
        else:
            sentences.append(f"A common use case is {use_case}.")
    if sections.get("Main Risks"):
        sentences.append(f"Main risks include {_normalize_item(sections['Main Risks'])}.")
    if sections.get("Assumptions / What I need from you"):
        sentences.append(_clean_sentence(sections["Assumptions / What I need from you"]) + ".")

    return " ".join([s for s in sentences if s]).strip()


def off_topic_freeform(user_text: str) -> str:
    return (
        "I focus on listed equity options strategies and payoff mechanics. "
        "Ask me about a specific strategy, a market view (bullish/bearish/neutral/volatile), "
        "or how strike and expiration choices affect payoffs."
    )


def _lead_in_phrase(seed: str) -> str:
    return ""


def _strip_strategy_subject(text: str) -> str:
    lower = text.lower()
    for name in STRATEGIES.keys():
        if lower.startswith(name + " "):
            return "it " + text[len(name) + 1 :]
    for alias, canonical in ALIASES.items():
        if lower.startswith(alias + " "):
            return "it " + text[len(alias) + 1 :]
    return text


def recent_strategies_from_history(history: list[dict], max_count: int = 2) -> list[str]:
    found: list[str] = []
    for turn in reversed(history):
        text = turn.get("text", "")
        for name in extract_strategies(text):
            if name not in found:
                found.append(name)
        if len(found) >= max_count:
            break
    return list(reversed(found))[:max_count]


def detect_view_from_history(history: list[dict]) -> str | None:
    for turn in reversed(history):
        if turn.get("role") != "user":
            continue
        view = detect_view_from_text(turn.get("text", ""))
        if view and view not in ("volatile", "short_vol"):
            return view
    return None


def is_choice_question(text: str) -> bool:
    lower = (text or "").lower()
    triggers = [
        "which is better",
        "what is better",
        "which should i choose",
        "which should i pick",
        "better for me",
        "which one is better",
        "which one should i",
    ]
    return any(t in lower for t in triggers)


def wants_view_based_suggestions(text: str) -> bool:
    lower = (text or "").lower()
    if extract_percent_range(lower) or extract_horizon(lower):
        return True
    triggers = [
        "expect",
        "outlook",
        "view",
        "bullish",
        "bearish",
        "neutral",
        "volatile",
        "volatility",
        "good strategy",
        "good trade",
        "what should",
        "ideas",
    ]
    if any(t in lower for t in triggers):
        return True
    return False


def is_income_request(text: str) -> bool:
    lower = (text or "").lower()
    triggers = [
        "income",
        "generate income",
        "earn income",
        "earn premium",
        "earn option premium",
        "yield",
        "credit",
        "collect premium",
        "sell premium",
        "sell options",
        "premium selling",
    ]
    return any(t in lower for t in triggers)


def detect_income_from_history(history: list[dict]) -> bool:
    for turn in reversed(history):
        if turn.get("role") != "user":
            continue
        if is_income_request(turn.get("text", "")):
            return True
    return False


def is_horizon_only(text: str) -> bool:
    horizon = extract_horizon(text)
    if not horizon:
        return False
    cleaned = (text or "").lower()
    cleaned = cleaned.replace(horizon, "")
    for token in ["about", "around", "approx", "approximately", "in", "over", "within", "next", "for"]:
        cleaned = cleaned.replace(token, "")
    cleaned = "".join(ch for ch in cleaned if ch.isalpha() or ch.isspace()).strip()
    return cleaned == ""


def is_short_vol_request(text: str) -> bool:
    lower = (text or "").lower()
    return any(term in lower for term in ["sell volatility", "selling volatility", "short vol", "short volatility"])


def is_view_question(text: str) -> bool:
    lower = (text or "").lower()
    triggers = [
        "what market view",
        "market view",
        "what view",
        "view does",
        "what outlook",
        "is it bullish",
        "is it bearish",
        "is it neutral",
        "is it volatile",
        "bullish or bearish",
        "bullish or neutral",
        "bearish or neutral",
        "neutral or bullish",
        "neutral or bearish",
        "volatile or neutral",
    ]
    if any(t in lower for t in triggers):
        return True
    if any(word in lower for word in ["bullish", "bearish", "neutral", "volatile"]) and any(
        phrase in lower for phrase in ["is a", "is an", "is the", "is ", "express", "view"]
    ):
        return True
    return False


def format_view_clarify_freeform() -> str:
    return "Which specific strategy are you referring to? I can describe the market view once you name it."


def format_view_clarify_structured() -> str:
    return ensure_sections(
        "Tell me which strategy you mean, and I'll describe the market view it expresses."
    )


def is_payoff_mechanics_question(text: str) -> bool:
    lower = (text or "").lower()
    triggers = [
        "how do options payoffs work",
        "how do payoffs work",
        "option payoff mechanics",
        "payoff mechanics",
        "payoff at expiration",
        "payoffs at expiration",
        "expiration payoff",
        "how do breakevens work",
        "breakevens work",
    ]
    return any(t in lower for t in triggers)


def is_long_short_comparison_question(text: str) -> bool:
    lower = (text or "").lower()
    return any(
        t in lower
        for t in [
            "long vs short options",
            "long and short options",
            "difference between long and short options",
            "max loss differ between long and short",
            "max loss difference between long and short",
        ]
    )


def use_case_phrase(text: str) -> str:
    clean = text.strip().rstrip(".")
    lower = clean.lower()
    if lower.startswith("expecting"):
        return "when you expect " + clean[len("expecting ") :]
    if lower.startswith("use"):
        return "when you " + clean
    return "when you want " + clean


def build_choice_response(
    strategies: list[str],
    view_hint: str | None = None,
    range_hint: tuple[int, int] | None = None,
    horizon_hint: str | None = None,
) -> str:
    if len(strategies) < 2:
        return (
            "It depends on what you're comparing. Tell me the two strategies you're weighing and your market view, "
            "and I'll recommend which fits your goals best."
        )

    left, right = strategies[0], strategies[1]
    view_clause = f"Given your {view_hint} view, " if view_hint else "Between the two, "
    horizon_clause = f" over about {horizon_hint}" if horizon_hint else ""
    range_clause = ""
    if range_hint:
        low, high = range_hint
        range_clause = f" expecting roughly a {low}-{high}% move"

    if {left, right} == {"bull call spread", "bull put spread"}:
        return (
            f"{view_clause}a bull call spread is usually the cleaner fit{horizon_clause}{range_clause} if you want defined risk "
            "and upside targeted to a specific range. A bull put spread is better if you prefer collecting premium and are comfortable "
            "with downside exposure or potential assignment. If you want limited risk and a clear upside range, choose the bull call spread; "
            "if you want credit income and can tolerate downside, choose the bull put spread."
        )

    left_card = get_strategy(left)
    right_card = get_strategy(right)
    if left_card and right_card:
        return (
            f"{view_clause}{left} tends to fit {use_case_phrase(left_card['typical_use_case'])}{horizon_clause}, "
            f"while {right} fits {use_case_phrase(right_card['typical_use_case'])}. "
            "If you want to prioritize limited downside, pick the defined-risk structure; if you want to collect premium, pick the credit-focused one. "
            "Tell me your risk preference and I can make a clearer recommendation."
        )

    return (
        "It depends on how much risk you're willing to take and whether you prefer paying a debit or collecting a credit. "
        "Tell me your risk preference and I can recommend which is the better fit."
    )


def needs_clarification(text: str) -> bool:
    lower = (text or "").lower().strip()
    if not lower:
        return True
    if not is_options_related(lower):
        return False
    if extract_strategies(lower):
        return False
    if detect_basic_option_question(lower):
        return False
    if detect_view_from_text(lower):
        return False
    if is_comparison_request(lower):
        return False
    if is_payoff_intent(lower, has_legs=False):
        return False
    specific_terms = [
        "theta",
        "vega",
        "gamma",
        "convexity",
        "convex",
        "delta",
        "iv",
        "implied volatility",
        "moneyness",
        "itm",
        "atm",
        "otm",
        "breakeven",
        "assignment",
        "exercise",
        "premium",
        "strike",
        "expiration",
        "debit",
        "credit",
        "spread",
        "straddle",
        "strangle",
        "condor",
        "butterfly",
        "collar",
        "covered",
        "protective",
        "calendar",
        "diagonal",
        "ratio",
        "backspread",
    ]
    if any(term in lower for term in specific_terms):
        return False
    return True


def clarifying_question_freeform(_: str) -> str:
    return (
        "I can help with strategies, market views, or mechanics. "
        "Do you want a strategy overview, a view-based trade menu (bullish/bearish/neutral/volatile), "
        "or an explanation of a specific term like delta/vega/theta?"
    )


def clarifying_question_structured() -> str:
    return (
        "Summary: I can explain strategies, market views, or mechanics, but I need a bit more detail.\n"
        "Setup: Tell me whether you want a strategy overview, a view-based menu (bullish/bearish/neutral/volatile), or a specific term explained.\n"
        "Payoff at Expiration: Once I know the topic, I can describe the payoff behavior.\n"
        "Max Profit: Will depend on the strategy or structure you want to discuss.\n"
        "Max Loss: Will depend on the strategy or structure you want to discuss.\n"
        "Breakeven(s): Depend on strikes and premiums if we get into specifics.\n"
        "Key Sensitivities: I can cover delta/vega/theta once the topic is clear.\n"
        "Typical Use Case: Depends on the strategy or market view you choose.\n"
        "Main Risks: Depends on the strategy or market view you choose.\n"
        "Assumptions / What I need from you: Tell me the specific topic or question you want answered."
    )


def _truncate(text: str, max_len: int = 160) -> str:
    if not text:
        return ""
    clean = " ".join(text.split())
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 3] + "..."


def is_options_related(text: str) -> bool:
    lower = text.lower()
    if not lower.strip():
        return False
    for name in STRATEGIES.keys():
        if name in lower:
            return True
    for alias in ALIASES.keys():
        if alias in lower:
            return True
    keywords = [
        "option",
        "options",
        "call",
        "put",
        "strike",
        "expiration",
        "premium",
        "spread",
        "straddle",
        "strangle",
        "condor",
        "butterfly",
        "collar",
        "covered",
        "protective",
        "calendar",
        "diagonal",
        "ratio",
        "backspread",
        "theta",
        "vega",
        "delta",
        "gamma",
        "rho",
        "convexity",
        "implied volatility",
        "iv",
        "breakeven",
        "payoff",
        "assignment",
        "exercise",
        "bullish",
        "bearish",
        "neutral",
        "volatile",
        "volatility",
        "compare",
        "difference",
        "versus",
    ]
    return any(k in lower for k in keywords)
