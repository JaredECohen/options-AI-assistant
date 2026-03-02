from __future__ import annotations

from app.core.safety import SECTION_TITLES
from app.core.strategies import get_strategy


class HeuristicProvider:
    name = "heuristic"

    async def generate(self, system: str, user: str, max_output_tokens: int, temperature: float) -> str:
        lower = user.lower()
        comparison = detect_comparison(lower)
        if comparison:
            return format_comparison(comparison)
        if any(term in lower for term in ["straddle", "strangle", "stradle", "stangle"]) and any(
            term in lower for term in ["when should", "when to use", "use", "choose"]
        ):
            return format_straddle_strangle_choice()
        if "bull call spread" in lower and "strike" in lower:
            return format_bull_call_strike_behavior()
        for name in list(get_strategy_names()):
            if name in lower:
                strat = get_strategy(name)
                if strat:
                    return format_strategy(strat)
        if "put spread" in lower and "bull" not in lower and "bear" not in lower:
            return format_ambiguous_put_spread()
        if "call spread" in lower and "bull" not in lower and "bear" not in lower:
            return format_ambiguous_call_spread()
        view = detect_market_view(lower)
        if view:
            return format_view_menu(view)
        return default_response()


def get_strategy_names():
    from app.core.strategies import STRATEGIES

    return STRATEGIES.keys()


def _sentence(text: str) -> str:
    clean = text.strip()
    return clean if clean.endswith(".") else f"{clean}."


def format_strategy(strat: dict) -> str:
    return (
        f"Summary: {strat['summary']}\n"
        f"Setup: {strat['setup']}\n"
        f"Payoff at Expiration: {strat['payoff']}\n"
        f"Max Profit: {_sentence(strat['max_profit'])}\n"
        f"Max Loss: {_sentence(strat['max_loss'])}\n"
        f"Breakeven(s): {strat['breakevens']}\n"
        f"Key Sensitivities: {strat['key_sensitivities']}\n"
        f"Typical Use Case: {strat['typical_use_case']}\n"
        f"Main Risks: {strat['main_risks']}\n"
        "Assumptions / What I need from you: Provide ticker, expiration, strikes, call/put, buy/sell, quantity, and optional premiums if you want calculations."
    )


def default_response() -> str:
    return (
        "Summary: I can explain listed equity options strategies and evaluate positions you specify using user-provided premiums.\n"
        "Setup: Ask about a specific strategy or provide legs to evaluate.\n"
        "Payoff at Expiration: I compute expiration payoff for user-specified legs.\n"
        "Max Profit: Computed from the specified premiums.\n"
        "Max Loss: Computed from the specified premiums.\n"
        "Breakeven(s): Computed from the specified premiums.\n"
        "Key Sensitivities: I can summarize delta/vega/theta for the structure you choose.\n"
        "Typical Use Case: Evaluating a known strategy or set of legs.\n"
        "Main Risks: Options carry risk and time decay.\n"
        "Assumptions / What I need from you: Provide ticker, expiration, strikes, call/put, buy/sell, quantity, and optional premiums."
    )


def detect_market_view(text: str) -> str | None:
    if "bullish" in text or "bearish" in text or "neutral" in text or "volatile" in text:
        if "bullish" in text:
            return "bullish"
        if "bearish" in text:
            return "bearish"
        if "neutral" in text:
            return "neutral"
        if "volatile" in text or "volatility" in text:
            return "volatile"
    if "range-bound" in text or "range bound" in text:
        return "neutral"
    return None


def detect_comparison(text: str) -> list[str] | None:
    triggers = ["compare", "difference", "vs", "versus"]
    if not any(t in text for t in triggers):
        return None
    strategies = extract_strategies(text)
    if len(strategies) >= 2:
        return strategies
    return None


def extract_strategies(text: str) -> list[str]:
    from app.core.strategies import ALIASES, STRATEGIES

    found: list[str] = []
    for name in STRATEGIES.keys():
        if name in text:
            found.append(name)
    for alias, canonical in ALIASES.items():
        if alias in text:
            found.append(canonical)
    # Deduplicate while preserving order
    seen = set()
    ordered = []
    for item in found:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def friendly_name(name: str) -> str:
    return name.title()


def format_comparison(strategies: list[str]) -> str:
    from app.core.strategies import get_strategy

    cards = []
    for name in strategies:
        strat = get_strategy(name)
        if strat:
            cards.append((friendly_name(name), strat))

    if not cards:
        return default_response()

    setup = " ".join([f"{n}: {s['setup']}" for n, s in cards])
    payoff = " ".join([f"{n}: {s['payoff']}" for n, s in cards])
    max_profit = " ".join([f"{n}: {_sentence(s['max_profit'])}" for n, s in cards])
    max_loss = " ".join([f"{n}: {_sentence(s['max_loss'])}" for n, s in cards])
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


def format_view_menu(view: str) -> str:
    if view == "bullish":
        strategies = "Long call, bull call spread, bull put spread, covered call (if long stock)."
        thesis = (
            "Long call seeks a larger upside move; bull call spread fits mild-to-moderate upside; "
            "bull put spread benefits if price stays above the short put; covered call earns income with capped upside."
        )
    elif view == "bearish":
        strategies = "Long put, bear put spread, bear call spread."
        thesis = (
            "Long put seeks downside; bear put spread fits mild-to-moderate downside; "
            "bear call spread benefits if price stays below the short call."
        )
    elif view == "neutral":
        strategies = "Iron condor, short strangle, short straddle (higher risk), calendar spread."
        thesis = (
            "These strategies benefit if price stays within a range or near a strike while time decay works."
        )
    else:
        strategies = "Long straddle, long strangle, backspread."
        thesis = (
            "These strategies benefit from a large move in either direction or rising volatility."
        )
    return (
        "Summary: Here is a short, educational menu of strategies for that market view.\n"
        f"Setup: {strategies}\n"
        f"Payoff at Expiration: {thesis}\n"
        "Max Profit: Varies by strategy (spreads are capped).\n"
        "Max Loss: Limited for defined-risk spreads; limited to premium for single long options.\n"
        "Breakeven(s): Depend on strikes and premiums; provide legs if you want calculations.\n"
        "Key Sensitivities: Directional delta for bullish/bearish; short theta for long options; long theta for short premium.\n"
        "Typical Use Case: Use this to choose a strategy type before selecting strikes/expiration.\n"
        "Main Risks: Move against the position or insufficient move before expiration.\n"
        "Assumptions / What I need from you: If you want payoff calculations, share ticker, expiration, strikes, call/put, buy/sell, quantity, and premiums."
    )


def format_bull_call_strike_behavior() -> str:
    return (
        "Summary: Strike selection changes cost, breakeven, and the capped payout for a bull call spread.\n"
        "Setup: A bull call spread buys a lower-strike call and sells a higher-strike call with the same expiration.\n"
        "Payoff at Expiration: A wider spread (higher short strike) increases the payoff range but usually costs more; a tighter spread is cheaper but caps gains sooner.\n"
        "Max Profit: Strike width minus net debit.\n"
        "Max Loss: The net debit. Wider strikes raise both max profit and cost.\n"
        "Breakeven(s): Lower strike + net debit; further OTM long strikes require a larger move to break even.\n"
        "Key Sensitivities: Closer strikes increase delta and responsiveness; further strikes reduce cost but need a bigger move.\n"
        "Typical Use Case: Use tighter strikes for modest upside views; wider strikes when expecting a bigger move and willing to pay more.\n"
        "Main Risks: Insufficient upside before expiration; time decay reduces value.\n"
        "Assumptions / What I need from you: For calculations, provide specific strikes, expiration, and premiums."
    )


def format_ambiguous_put_spread() -> str:
    return (
        "Summary: An 'OTM put spread' can refer to either a bull put (credit) spread or a bear put (debit) spread.\n"
        "Setup: Bull put spread sells a higher-strike put and buys a lower-strike put; bear put spread buys a higher-strike put and sells a lower-strike put.\n"
        "Payoff at Expiration: Bull put spread benefits if price stays above the short put; bear put spread benefits if price falls toward/through the long put.\n"
        "Max Profit: Bull put spread is the net credit; bear put spread is strike width minus net debit.\n"
        "Max Loss: Bull put max loss is strike width minus credit; bear put max loss is the net debit.\n"
        "Breakeven(s): Bull put spread breakeven is short strike minus credit; bear put spread breakeven is long strike minus debit.\n"
        "Key Sensitivities: Bull put is short volatility and benefits from time decay; bear put is long volatility and needs downside movement.\n"
        "Typical Use Case: Use bull put if mildly bullish/neutral; use bear put if moderately bearish.\n"
        "Main Risks: Bull put risks losses on a sharp drop; bear put risks time decay if price doesn't fall.\n"
        "Assumptions / What I need from you: Tell me your market view (bullish or bearish) and strikes if you want payoff calculations."
    )


def format_ambiguous_call_spread() -> str:
    return (
        "Summary: A 'call spread' could be a bull call (debit) spread or a bear call (credit) spread.\n"
        "Setup: Bull call spread buys a lower-strike call and sells a higher-strike call; bear call spread sells a lower-strike call and buys a higher-strike call.\n"
        "Payoff at Expiration: Bull call benefits from a rise toward/through the short call; bear call benefits if price stays below the short call.\n"
        "Max Profit: Bull call is strike width minus debit; bear call is the credit received.\n"
        "Max Loss: Bull call max loss is the debit; bear call max loss is strike width minus credit.\n"
        "Breakeven(s): Bull call breakeven is long strike plus debit; bear call breakeven is short strike plus credit.\n"
        "Key Sensitivities: Bull call is long delta; bear call is short delta and short volatility.\n"
        "Typical Use Case: Use bull call if mildly bullish; use bear call if neutral to mildly bearish.\n"
        "Main Risks: Bull call risks insufficient upside; bear call risks losses on a strong rally.\n"
        "Assumptions / What I need from you: Tell me your market view (bullish or bearish) and strikes if you want payoff calculations."
    )


def format_straddle_strangle_choice() -> str:
    return (
        "Summary: A straddle fits a bigger, faster move; a strangle fits the same idea but cheaper with a larger required move.\n"
        "Setup: A long straddle buys a call and put at the same strike; a long strangle buys OTM call and OTM put at different strikes.\n"
        "Payoff at Expiration: Straddles profit from large moves in either direction; strangles need a larger move beyond the strikes to profit.\n"
        "Max Profit: Unlimited upside; large downside profit if the stock drops sharply (bounded by zero).\n"
        "Max Loss: Total premium paid.\n"
        "Breakeven(s): Straddle at strike +/- total premium; strangle at call strike + total premium and put strike - total premium.\n"
        "Key Sensitivities: Both are long gamma and vega; short theta. Straddles have higher delta near the strike.\n"
        "Typical Use Case: Use a straddle when you expect a big move and want closer strikes; use a strangle to reduce cost if you can tolerate needing a larger move.\n"
        "Main Risks: Time decay if the move does not materialize; strangles can expire worthless more often.\n"
        "Assumptions / What I need from you: If you want payoff calculations, provide strikes, expiration, and optional premiums."
    )
