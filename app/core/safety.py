import re
from typing import Iterable

ADVICE_LIKE_PATTERNS = [
    r"\b(best|top|optimal)\s+(trade|strategy)\b",
    r"\bbest\b.*\btrade\b",
    r"\bwhat('?s| is) the best\b.*\btrade\b",
    r"\b(what|which)\s+should\s+i\s+(trade|buy|sell)\b",
    r"\b(tell me what to buy|tell me what to sell)\b",
    r"\bwhat to buy\b",
    r"\bwhat to sell\b",
    r"\b(recommend|suggest)\b.*\b(trade|strategy|position)\b",
]

PRICE_TARGET_PATTERNS = [
    r"\b(target price|price target|forecast|predict)\b",
]

STRIKE_PICK_PATTERNS = [
    r"\b(pick|choose|select)\s+strikes?\b",
    r"\b(which strike|best strike|what strike)\b",
    r"\b(select an expiration|best expiration)\b",
]

ILLEGAL_PATTERNS = [
    r"\binsider\b",
    r"\bfront[- ]?run\b",
    r"\bmanipulat\w+\b",
    r"\bpump and dump\b",
    r"\bfraud\b",
]

OUT_OF_SCOPE_PATTERNS = [
    r"\btax advice\b",
    r"\blegal advice\b",
    r"\btax\b",
    r"\blegal\b",
]

SECTION_TITLES = [
    "Summary",
    "Setup",
    "Payoff at Expiration",
    "Max Profit",
    "Max Loss",
    "Breakeven(s)",
    "Key Sensitivities",
    "Typical Use Case",
    "Main Risks",
    "Assumptions / What I need from you",
]


def off_topic_template() -> str:
    return (
        "Summary: I focus on listed equity options strategies and payoff mechanics.\n"
        "Setup: Ask an options-related question (strategy, market view, or mechanics).\n"
        "Payoff at Expiration: I can explain how payoffs behave at expiration.\n"
        "Max Profit: I can describe general limits; provide legs for exact numbers.\n"
        "Max Loss: Provide legs for exact numbers.\n"
        "Breakeven(s): Provide legs and premiums if you want calculations.\n"
        "Key Sensitivities: I can explain delta, vega, and theta.\n"
        "Typical Use Case: Education on options strategies and when they fit.\n"
        "Main Risks: Options involve risk and time decay.\n"
        "Assumptions / What I need from you: Ask an options-related question or provide legs."
    )


def guarantee_misconception_template() -> str:
    return (
        "Summary: Options outcomes are not guaranteed; there are no guaranteed profit strategies.\n"
        "Setup: I can explain strategies and evaluate user-specified legs; premiums are optional.\n"
        "Payoff at Expiration: Payoffs are conditional on the underlying price at expiration.\n"
        "Max Profit: Depends on the specific legs and premiums you choose.\n"
        "Max Loss: Also depends on the specific legs and premiums you choose.\n"
        "Breakeven(s): Computed from your specified strikes and premiums when available.\n"
        "Key Sensitivities: Time decay and volatility materially affect outcomes.\n"
        "Typical Use Case: Education and evaluation of a strategy you already selected.\n"
        "Main Risks: Market moves can create losses; outcomes are not guaranteed.\n"
        "Assumptions / What I need from you: Provide the exact legs you want evaluated."
    )


def _matches(patterns: Iterable[str], text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def needs_refusal(user_text: str) -> bool:
    return (
        _matches(PRICE_TARGET_PATTERNS, user_text)
        or _matches(ILLEGAL_PATTERNS, user_text)
        or _matches(OUT_OF_SCOPE_PATTERNS, user_text)
    )


def response_violates(response_text: str) -> bool:
    lowered = response_text.lower()
    if "guaranteed profit" in lowered and "not guaranteed" not in lowered and "no guaranteed" not in lowered:
        return True
    if _matches(ILLEGAL_PATTERNS, response_text):
        return True
    if _matches(PRICE_TARGET_PATTERNS, response_text):
        if any(term in lowered for term in ["cannot", "can't", "do not", "don't", "not able"]):
            return False
        return True
    return False


def safe_refusal_template() -> str:
    return (
        "Summary: I can provide general education on listed equity options and evaluate positions you specify.\n"
        "Setup: Share ticker, expiration, strikes, call/put, buy/sell, quantity, and optional premiums.\n"
        "Payoff at Expiration: I'll compute the expiration payoff once the legs are provided.\n"
        "Max Profit: Will be computed from your specified legs.\n"
        "Max Loss: Will be computed from your specified legs.\n"
        "Breakeven(s): I'll compute breakevens from the specified premiums when available.\n"
        "Key Sensitivities: I can summarize delta/vega/theta for your chosen structure.\n"
        "Typical Use Case: Use this when you already have a strategy idea to evaluate.\n"
        "Main Risks: Options involve risk; selection and timing are your decision.\n"
        "Assumptions / What I need from you: Provide the exact legs to evaluate. If you share a market view and horizon, I can also outline candidate trades using % moneyness."
    )


def strike_refusal_template() -> str:
    return (
        "Summary: I can outline strike ideas if you share a clear market view and horizon.\n"
        "Setup: Tell me your view (bullish/bearish/neutral/volatile) and timeframe, and I can suggest % moneyness strikes.\n"
        "Payoff at Expiration: I'll compute the expiration payoff once the legs are provided.\n"
        "Max Profit: Will be computed from your specified legs.\n"
        "Max Loss: Will be computed from your specified legs.\n"
        "Breakeven(s): I'll compute breakevens from the specified premiums when available.\n"
        "Key Sensitivities: I can summarize delta/vega/theta for your chosen structure.\n"
        "Typical Use Case: Evaluation of a strategy you already selected.\n"
        "Main Risks: Options carry risk and time decay.\n"
        "Assumptions / What I need from you: Provide the exact legs to evaluate, or share your view so I can suggest % moneyness strikes."
    )


def illegal_refusal_template() -> str:
    return (
        "Summary: I cannot help with illegal activity. I can provide general education and evaluate user-specified legs.\n"
        "Setup: Share ticker, expiration, strikes, call/put, buy/sell, quantity, and optional premiums.\n"
        "Payoff at Expiration: I'll compute the expiration payoff once the legs are provided.\n"
        "Max Profit: Will be computed from your specified legs.\n"
        "Max Loss: Will be computed from your specified legs.\n"
        "Breakeven(s): I'll compute breakevens from the specified premiums when available.\n"
        "Key Sensitivities: I can summarize delta/vega/theta for your chosen structure.\n"
        "Typical Use Case: Educational evaluation of a known strategy.\n"
        "Main Risks: Illegal activity has serious consequences; options trading also carries risk.\n"
        "Assumptions / What I need from you: Provide the exact legs to evaluate."
    )


def is_illegal_request(user_text: str) -> bool:
    return _matches(ILLEGAL_PATTERNS, user_text)


def is_strike_request(user_text: str) -> bool:
    return _matches(STRIKE_PICK_PATTERNS, user_text)


def is_trade_recommendation_request(user_text: str) -> bool:
    return _matches(ADVICE_LIKE_PATTERNS, user_text)


def ensure_sections(text: str) -> str:
    if all(title in text for title in SECTION_TITLES):
        return text
    # Fallback wrapper
    summary = text.strip() if text.strip() else "I can explain strategies and evaluate specified legs."
    return (
        f"Summary: {summary}\n"
        "Setup: Provide ticker, expiration, strikes, call/put, buy/sell, quantity, and optional premiums.\n"
        "Payoff at Expiration: I'll compute expiration payoff from your legs.\n"
        "Max Profit: Computed from the specified premiums when available.\n"
        "Max Loss: Computed from the specified premiums when available.\n"
        "Breakeven(s): Computed from the specified premiums when available.\n"
        "Key Sensitivities: I can summarize delta/vega/theta for your structure.\n"
        "Typical Use Case: Evaluating a user-specified options position.\n"
        "Main Risks: Options carry risk and time decay.\n"
        "Assumptions / What I need from you: Provide the exact legs to evaluate."
    )


def apply_backstop(response_text: str) -> str:
    if response_violates(response_text):
        return safe_refusal_template()
    return response_text
