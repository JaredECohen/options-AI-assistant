"""Intent detection: classify the user turn (payoff, view, greek, comparison, income, off-topic...).

Pure functions over text and history; no rendering, no I/O."""
from __future__ import annotations

from app.core.strategies import ALIASES, STRATEGIES


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


def detect_basic_option_question(text: str) -> str | None:
    lower = (text or "").lower()
    if extract_strategies(lower):
        return None
    if "call" in lower and (
        "option" in lower
        or "how does" in lower
        or "what is" in lower
        or "explain" in lower
        or "when should" in lower
    ):
        if any(term in lower for term in ["spread", "covered", "straddle", "strangle", "calendar", "diagonal", "ratio", "backspread"]):
            return None
        return "call"
    if "put" in lower and (
        "option" in lower
        or "how does" in lower
        or "what is" in lower
        or "explain" in lower
        or "when should" in lower
    ):
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


def asked_horizon_before(history: list[dict]) -> bool:
    for turn in history:
        if turn.get("role") != "assistant":
            continue
        text = (turn.get("text") or "").lower()
        if "time horizon" in text or "horizon" in text:
            return True
    return False


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


def is_view_menu_request(text: str) -> bool:
    lower = (text or "").lower()
    triggers = [
        "view-based trade menu",
        "view based trade menu",
        "trade menu",
        "strategy menu",
        "menu of strategies",
        "view menu",
    ]
    return any(t in lower for t in triggers)


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
        "option payoffs",
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


def needs_clarification(text: str) -> bool:
    lower = (text or "").lower().strip()
    if not lower:
        return True
    if is_view_menu_request(lower):
        return False
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


def is_options_related(text: str) -> bool:
    lower = text.lower()
    if not lower.strip():
        return False
    trade_triggers = [
        "best trade",
        "best options trade",
        "recommend a trade",
        "recommend a strategy",
        "what should i buy",
        "what should i sell",
        "tell me what to buy",
        "tell me what to sell",
        "tell me what to buy and sell",
        "pick strikes",
        "best strike",
        "price target",
        "target price",
    ]
    if any(t in lower for t in trade_triggers):
        return True
    for name in STRATEGIES.keys():
        if name in lower:
            return True
    for alias in ALIASES.keys():
        if alias in lower:
            return True
    keywords = [
        "option",
        "options",
        "strategy",
        "trade",
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
        "moneyness",
        "intrinsic",
        "extrinsic",
        "time value",
        "breakeven",
        "payoff",
        "assignment",
        "exercise",
        "key terms",
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
