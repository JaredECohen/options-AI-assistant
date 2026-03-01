import re
from typing import Iterable

ADVICE_LIKE_PATTERNS = [
    r"\b(best|top|optimal)\s+(trade|strategy)\b",
    r"\b(you should|i recommend|i suggest|recommend)\b",
    r"\b(go long|buy calls|sell puts|short the stock)\b",
    r"\b(target price|price target|forecast)\b",
    r"\bwhat to buy\b",
    r"\bwhat to sell\b",
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
    "Max Profit / Max Loss",
    "Breakeven(s)",
    "Key Sensitivities",
    "Typical Use Case",
    "Main Risks",
    "Assumptions / What I need from you",
]


def guarantee_misconception_template() -> str:
    return (
        "Summary: Options outcomes are not guaranteed; there are no guaranteed profit strategies.\n"
        "Setup: I can explain strategies and evaluate user-specified legs with live premiums.\n"
        "Payoff at Expiration: Payoffs are conditional on the underlying price at expiration.\n"
        "Max Profit / Max Loss: These depend on the specific legs and premiums you choose.\n"
        "Breakeven(s): Computed from your specified strikes and premiums.\n"
        "Key Sensitivities: Time decay and volatility materially affect outcomes.\n"
        "Typical Use Case: Education and evaluation of a strategy you already selected.\n"
        "Main Risks: Market moves can create losses; outcomes are not guaranteed.\n"
        "Assumptions / What I need from you: Provide the exact legs you want evaluated."
    )


def _matches(patterns: Iterable[str], text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def needs_refusal(user_text: str) -> bool:
    return (
        _matches(ADVICE_LIKE_PATTERNS, user_text)
        or _matches(STRIKE_PICK_PATTERNS, user_text)
        or _matches(ILLEGAL_PATTERNS, user_text)
        or _matches(OUT_OF_SCOPE_PATTERNS, user_text)
    )


def response_violates(response_text: str) -> bool:
    lowered = response_text.lower()
    if "guaranteed profit" in lowered and "not guaranteed" not in lowered and "no guaranteed" not in lowered:
        return True
    return _matches(ADVICE_LIKE_PATTERNS, response_text) or _matches(STRIKE_PICK_PATTERNS, response_text) or _matches(
        ILLEGAL_PATTERNS, response_text
    )


def safe_refusal_template() -> str:
    return (
        "Summary: I can provide general education on listed equity options and evaluate positions you specify using live premiums.\n"
        "Setup: Share ticker, expiration, strikes, call/put, buy/sell, quantity, and optional premiums (I can fetch them).\n"
        "Payoff at Expiration: I'll compute the expiration payoff once the legs are provided.\n"
        "Max Profit / Max Loss: I'll compute these from your specified legs.\n"
        "Breakeven(s): I'll compute breakevens from the specified premiums.\n"
        "Key Sensitivities: I can summarize delta/vega/theta for your chosen structure.\n"
        "Typical Use Case: Use this when you already have a strategy idea to evaluate.\n"
        "Main Risks: Options involve risk; selection and timing are your decision.\n"
        "Assumptions / What I need from you: Provide the exact legs to evaluate. I don't select trades or strikes."
    )


def strike_refusal_template() -> str:
    return (
        "Summary: I can evaluate user-specified legs but I don't choose strikes or expirations.\n"
        "Setup: Please choose strikes and expirations via the chain UI, then share the legs.\n"
        "Payoff at Expiration: I'll compute the expiration payoff once the legs are provided.\n"
        "Max Profit / Max Loss: I'll compute these from your specified legs.\n"
        "Breakeven(s): I'll compute breakevens from the specified premiums.\n"
        "Key Sensitivities: I can summarize delta/vega/theta for your chosen structure.\n"
        "Typical Use Case: Evaluation of a strategy you already selected.\n"
        "Main Risks: Options carry risk and time decay.\n"
        "Assumptions / What I need from you: Provide the exact legs to evaluate."
    )


def illegal_refusal_template() -> str:
    return (
        "Summary: I cannot help with illegal activity. I can provide general education and evaluate user-specified legs.\n"
        "Setup: Share ticker, expiration, strikes, call/put, buy/sell, quantity, and optional premiums.\n"
        "Payoff at Expiration: I'll compute the expiration payoff once the legs are provided.\n"
        "Max Profit / Max Loss: I'll compute these from your specified legs.\n"
        "Breakeven(s): I'll compute breakevens from the specified premiums.\n"
        "Key Sensitivities: I can summarize delta/vega/theta for your chosen structure.\n"
        "Typical Use Case: Educational evaluation of a known strategy.\n"
        "Main Risks: Illegal activity has serious consequences; options trading also carries risk.\n"
        "Assumptions / What I need from you: Provide the exact legs to evaluate."
    )


def is_illegal_request(user_text: str) -> bool:
    return _matches(ILLEGAL_PATTERNS, user_text)


def is_strike_request(user_text: str) -> bool:
    return _matches(STRIKE_PICK_PATTERNS, user_text)


def ensure_sections(text: str) -> str:
    if all(title in text for title in SECTION_TITLES):
        return text
    # Fallback wrapper
    summary = text.strip() if text.strip() else "I can explain strategies and evaluate specified legs."
    return (
        f"Summary: {summary}\n"
        "Setup: Provide ticker, expiration, strikes, call/put, buy/sell, quantity, and optional premiums.\n"
        "Payoff at Expiration: I'll compute expiration payoff from your legs.\n"
        "Max Profit / Max Loss: Computed from the specified premiums.\n"
        "Breakeven(s): Computed from the specified premiums.\n"
        "Key Sensitivities: I can summarize delta/vega/theta for your structure.\n"
        "Typical Use Case: Evaluating a user-specified options position.\n"
        "Main Risks: Options carry risk and time decay.\n"
        "Assumptions / What I need from you: Provide the exact legs to evaluate."
    )


def apply_backstop(response_text: str) -> str:
    if response_violates(response_text):
        return safe_refusal_template()
    return response_text
