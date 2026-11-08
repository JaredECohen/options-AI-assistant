"""Deterministic answers to recognised questions, resolved before any LLM call."""
from __future__ import annotations

from app.core.safety import ensure_sections
from app.core.safety import illegal_refusal_template
from app.core.safety import is_illegal_request
from app.core.safety import is_trade_recommendation_request
from app.core.strategies import get_strategy
from app.core.strategies import normalize_strategy_name
from app.chat.intents import detect_basic_option_question, detect_view_from_text, extract_greek_terms, extract_strategies, is_expiration_selection_question, is_long_short_comparison_question, is_payoff_mechanics_question, is_strike_selection_question, is_view_menu_request
from app.chat.render_freeform import format_assignment_freeform, format_breakeven_freeform, format_convexity_freeform, format_extrinsic_value_freeform, format_greek_comparison_freeform, format_greek_freeform, format_intrinsic_value_freeform, format_key_terms_freeform, format_long_short_comparison_freeform, format_moneyness_freeform, format_payoff_mechanics_freeform, illegal_refusal_freeform, income_view_suggestions_freeform, to_freeform, use_case_phrase, view_trade_suggestions_freeform
from app.chat.render_structured import format_basic_option_structured, format_expiration_selection_structured, format_greek_comparison_structured, format_greek_structured, format_long_short_comparison_structured, format_payoff_mechanics_structured, format_strike_selection_structured, income_view_suggestions_structured, view_trade_suggestions_structured


def answer_known_question(
    user_text: str,
    mode: str,
    selected_view: str | None = None,
    selected_strategy: str | None = None,
) -> str | None:
    lower = (user_text or "").lower()
    if not lower.strip():
        return None

    selected_view = (selected_view or "").lower() or None
    selected_strategy = normalize_strategy_name(selected_strategy or "") if selected_strategy else None

    if is_illegal_request(lower):
        return illegal_refusal_freeform() if mode == "freeform" else illegal_refusal_template()

    greek_terms = extract_greek_terms(lower)
    if greek_terms:
        if mode == "freeform":
            if len(greek_terms) == 1:
                if greek_terms[0] == "convexity":
                    return format_convexity_freeform()
                return format_greek_freeform(greek_terms[0])
            return format_greek_comparison_freeform(greek_terms)
        if len(greek_terms) == 1:
            return format_greek_structured(greek_terms[0])
        return format_greek_comparison_structured(greek_terms)

    view = detect_view_from_text(lower) or selected_view
    if "income" in lower and view:
        if mode == "freeform":
            return income_view_suggestions_freeform(view, lower)
        return income_view_suggestions_structured(view, lower)

    if view and any(term in lower for term in ["strategy", "strategies", "trade", "trades", "fit", "fits"]):
        if mode == "freeform":
            return view_trade_suggestions_freeform(view, lower)
        return view_trade_suggestions_structured(view, lower)

    if is_view_menu_request(lower) and view:
        return view_trade_suggestions_freeform(view, lower) if mode == "freeform" else view_trade_suggestions_structured(view, lower)

    if is_trade_recommendation_request(lower) and view:
        if mode == "freeform":
            return view_trade_suggestions_freeform(view, lower)
        return view_trade_suggestions_structured(view, lower)

    if is_strike_selection_question(lower) and selected_strategy:
        text = format_strike_selection_structured(selected_strategy)
        return to_freeform(text) if mode == "freeform" else text

    if ("option payoffs" in lower or "payoffs" in lower and "option" in lower) and not extract_strategies(lower):
        return format_payoff_mechanics_freeform() if mode == "freeform" else format_payoff_mechanics_structured()

    if is_long_short_comparison_question(lower) or "difference between long options and short options" in lower:
        return format_long_short_comparison_freeform() if mode == "freeform" else format_long_short_comparison_structured()

    if "otm" in lower and "call" in lower and "%" in lower:
        return None

    basic_option = detect_basic_option_question(lower)
    if basic_option:
        text = format_basic_option_structured(basic_option)
        return to_freeform(text) if mode == "freeform" else text

    if is_payoff_mechanics_question(lower) and not extract_strategies(lower):
        return format_payoff_mechanics_freeform() if mode == "freeform" else format_payoff_mechanics_structured()

    if is_long_short_comparison_question(lower):
        return format_long_short_comparison_freeform() if mode == "freeform" else format_long_short_comparison_structured()

    if is_expiration_selection_question(lower):
        text = format_expiration_selection_structured(selected_strategy or "")
        return to_freeform(text) if mode == "freeform" else text

    if "assignment" in lower and any(term in lower for term in ["what is", "how does", "explain", "work"]):
        text = format_assignment_freeform()
        return text if mode == "freeform" else ensure_sections(text)

    if "moneyness" in lower:
        text = format_moneyness_freeform()
        return text if mode == "freeform" else ensure_sections(text)

    if "intrinsic value" in lower:
        text = format_intrinsic_value_freeform()
        return text if mode == "freeform" else ensure_sections(text)

    if "extrinsic value" in lower or "time value" in lower:
        text = format_extrinsic_value_freeform()
        return text if mode == "freeform" else ensure_sections(text)

    if "breakeven" in lower and any(term in lower for term in ["what is", "how does", "explain"]) and not extract_strategies(lower):
        text = format_breakeven_freeform()
        return text if mode == "freeform" else ensure_sections(text)

    if "key terms" in lower or ("beginner" in lower and "term" in lower):
        text = format_key_terms_freeform()
        return text if mode == "freeform" else ensure_sections(text)

    return None


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
