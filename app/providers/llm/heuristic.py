from __future__ import annotations

from app.core.safety import SECTION_TITLES
from app.core.strategies import get_strategy


class HeuristicProvider:
    name = "heuristic"

    async def generate(self, system: str, user: str, max_output_tokens: int, temperature: float) -> str:
        current_message = _extract_current_message(user)
        lower = current_message.lower()
        if is_straddle_strangle_comparison(lower):
            return format_straddle_vs_strangle()
        if is_debit_credit_spread_question(lower):
            return format_debit_credit_spreads()
        if is_otm_call_comparison(lower):
            return format_otm_call_comparison()
        if is_gamma_convexity_question(lower):
            return format_gamma_convexity()
        if is_vega_iv_question(lower):
            return format_vega_iv()
        comparison = detect_comparison(lower)
        if comparison:
            return format_comparison(comparison)
        greeks = detect_greeks(lower)
        if greeks:
            if is_all_greeks_request(lower) or len(greeks) >= 4:
                return format_all_greeks()
            if "convexity" in greeks and len(greeks) == 1:
                return format_convexity()
            if len(greeks) == 1:
                return format_greek(greeks[0])
            return format_greek_comparison(greeks)
        if is_general_strike_selection_question(lower):
            return format_general_strike_selection()
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


def _extract_current_message(text: str) -> str:
    marker = "Current user message:"
    if marker not in text:
        return text
    after = text.split(marker, 1)[1]
    stop = "Context (use only if the user asked for payoff/evaluation):"
    if stop in after:
        after = after.split(stop, 1)[0]
    return after.strip()


def is_general_strike_selection_question(text: str) -> bool:
    triggers = [
        "pick strike",
        "picking strike",
        "choose strike",
        "choosing strike",
        "strike price",
        "strike prices",
        "how should i think about strike",
        "how should i think bout strike",
        "how to choose strikes",
    ]
    if any(t in text for t in triggers):
        return True
    if "strike" in text and any(k in text for k in ["how", "think", "choose", "pick", "select"]):
        return True
    return False


def format_general_strike_selection() -> str:
    return (
        "Strike selection is about trade-offs: closer-to-the-money strikes cost more but respond more quickly to price moves, "
        "while further OTM strikes are cheaper but need a larger move to pay off. For debit trades, the breakeven rises as you go "
        "further OTM; for credit trades, moving strikes further away typically lowers the credit but increases the cushion. "
        "Shorter expirations are cheaper but give less time; longer expirations cost more but give the thesis time to play out. "
        "Tell me the strategy, time horizon, or target move and I can tailor the strike logic."
    )


def is_straddle_strangle_comparison(text: str) -> bool:
    return "straddle" in text and "strangle" in text and any(
        t in text for t in ["compare", "difference", "differ", "vs", "versus", "how do"]
    )


def format_straddle_vs_strangle() -> str:
    return (
        "A long straddle uses a call and put at the same strike, so it costs more but needs a smaller move to break even. "
        "A long strangle uses OTM call and put strikes, so it's cheaper but needs a larger move to pay off. "
        "Both benefit from big moves and are hurt by time decay; the straddle is more responsive, the strangle is lower cost."
    )


def is_debit_credit_spread_question(text: str) -> bool:
    return "spread" in text and "debit" in text and "credit" in text


def format_debit_credit_spreads() -> str:
    return (
        "Debit spreads pay upfront and have defined, limited risk equal to the debit; they profit from a directional move. "
        "Credit spreads collect premium and profit if price stays on the safe side of the short strike; their max loss is the "
        "spread width minus the credit. Debit spreads are long premium (positive convexity), while credit spreads are short premium "
        "(negative convexity) and are more vulnerable to sharp moves."
    )


def is_otm_call_comparison(text: str) -> bool:
    if "call" not in text or "otm" not in text:
        return False
    return any(p in text for p in ["10%", "10 %"]) and any(p in text for p in ["20%", "20 %"])


def format_otm_call_comparison() -> str:
    return (
        "A 10% OTM call is closer to the current price, so it costs more, has higher delta, and needs a smaller move to break even. "
        "A 20% OTM call is cheaper but has lower delta and needs a larger move to pay off. The 10% OTM call is more responsive; the "
        "20% OTM call is higher leverage but more likely to expire worthless."
    )


def is_gamma_convexity_question(text: str) -> bool:
    return "gamma" in text and "convexity" in text


def format_gamma_convexity() -> str:
    return (
        "Gamma is the rate at which delta changes; that's what creates convexity in options. "
        "Higher gamma means P/L curves more sharply as price moves, which is why long options are positive convexity and short options are negative convexity. "
        "Gamma is highest near the strike and for shorter expirations."
    )


def is_vega_iv_question(text: str) -> bool:
    return "vega" in text and ("implied volatility" in text or " iv" in text or "iv " in text)


def format_vega_iv() -> str:
    return (
        "Vega measures how much an option's price changes when implied volatility (IV) changes. "
        "When IV rises, option premiums rise and long options benefit; when IV falls, premiums drop and long options suffer. "
        "Vega is larger for longer-dated and near-the-money options."
    )


def detect_greeks(text: str) -> list[str]:
    terms = []
    if "delta" in text:
        terms.append("delta")
    if "gamma" in text:
        terms.append("gamma")
    if "theta" in text:
        terms.append("theta")
    if "vega" in text:
        terms.append("vega")
    if "rho" in text:
        terms.append("rho")
    if "convexity" in text:
        terms.append("convexity")
    if "implied volatility" in text or " iv" in text or text.strip().startswith("iv"):
        terms.append("implied volatility")
    if "volatility" in text and "implied volatility" not in terms:
        if any(word in text for word in ["implied", "iv"]):
            terms.append("implied volatility")
    # Deduplicate preserve order
    seen = set()
    ordered = []
    for t in terms:
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    return ordered


def is_all_greeks_request(text: str) -> bool:
    return any(
        t in text
        for t in [
            "greeks",
            "the greeks",
            "option greeks",
            "explain the greeks",
            "explain greeks",
        ]
    )


def format_all_greeks() -> str:
    return (
        "The main Greeks describe how an option's price changes: "
        "Delta is sensitivity to a $1 move in the underlying; "
        "Gamma is how fast delta changes (convexity); "
        "Theta is time decay; "
        "Vega is sensitivity to implied volatility; "
        "Rho is sensitivity to interest rates. "
        "If you want, I can explain any one of these in more detail or apply them to a specific strategy."
    )


def format_greek(term: str) -> str:
    if term == "delta":
        summary = "Delta is the option's sensitivity to a $1 move in the underlying."
        setup = "Calls have positive delta; puts have negative delta. Delta grows as options move ITM."
        payoff = "Higher delta means the option price moves more with the stock."
        max_profit = "Depends on the position; delta does not cap profit."
        max_loss = "Depends on the position; long options risk premium paid."
        breakevens = "Breakevens depend on strikes and premiums."
        sensitivities = "Delta changes with price and time; gamma governs how fast delta changes."
        use_case = "Use delta to gauge directional exposure and hedge ratios."
        risks = "Delta is not constant; large moves and time changes alter it."
    elif term == "gamma":
        summary = "Gamma is how quickly delta changes as the underlying moves; it drives convexity."
        setup = "Long options have positive gamma; short options have negative gamma."
        payoff = "Higher gamma means P/L accelerates with larger moves (positive convexity)."
        max_profit = "Depends on the position; long gamma can benefit from large moves."
        max_loss = "Short gamma can lose quickly on sharp moves; long gamma is limited to premium."
        breakevens = "Breakevens depend on strikes and premiums."
        sensitivities = "Gamma is highest near the strike and for shorter expirations."
        use_case = "Use gamma to assess convexity and risk to large moves."
        risks = "Short gamma is risky in volatile markets; long gamma pays for time."
    elif term == "theta":
        summary = "Theta is time decay: how option value changes as time passes."
        setup = "Long options are typically short theta; short options are long theta."
        payoff = "All else equal, long options lose value as expiration approaches."
        max_profit = "Depends on the position."
        max_loss = "Long options can lose the premium from decay if price doesn't move."
        breakevens = "Breakevens depend on strikes and premiums."
        sensitivities = "Theta accelerates as expiration nears and is highest near the strike."
        use_case = "Use theta to understand carry costs for long options and income for short premium."
        risks = "Time decay can erode positions even if direction is correct but slow."
    elif term == "vega":
        summary = "Vega measures sensitivity to implied volatility."
        setup = "Long options are long vega; short options are short vega."
        payoff = "Rising IV helps long options; falling IV hurts them."
        max_profit = "Depends on the position."
        max_loss = "Short vega positions can lose if IV spikes."
        breakevens = "Breakevens depend on strikes and premiums."
        sensitivities = "Vega is higher for longer-dated and near-the-money options."
        use_case = "Use vega to assess exposure to volatility changes."
        risks = "IV crush can hurt long options after events."
    elif term == "rho":
        summary = "Rho measures sensitivity to interest rates."
        setup = "Calls tend to benefit from rising rates; puts tend to be hurt."
        payoff = "Rho is usually small for short-dated equity options."
        max_profit = "Depends on the position."
        max_loss = "Depends on the position."
        breakevens = "Breakevens depend on strikes and premiums."
        sensitivities = "Rho matters more for longer expirations."
        use_case = "Use rho for long-dated options or rate-sensitive periods."
        risks = "Rate changes can shift option values modestly."
    elif term == "implied volatility":
        summary = "Implied volatility is the market's estimate of future price movement."
        setup = "Higher IV means higher option premiums; lower IV means cheaper options."
        payoff = "Long options benefit from IV expansion; short options benefit from IV contraction."
        max_profit = "Depends on the position."
        max_loss = "Depends on the position."
        breakevens = "Breakevens depend on strikes and premiums."
        sensitivities = "IV impacts vega; longer-dated options are more sensitive."
        use_case = "Use IV to compare relative option richness or cheapness."
        risks = "IV can drop after catalysts, hurting long options."
    else:  # convexity
        return format_convexity()
    return (
        f"Summary: {summary}\n"
        f"Setup: {setup}\n"
        f"Payoff at Expiration: {payoff}\n"
        f"Max Profit: {max_profit}\n"
        f"Max Loss: {max_loss}\n"
        f"Breakeven(s): {breakevens}\n"
        f"Key Sensitivities: {sensitivities}\n"
        f"Typical Use Case: {use_case}\n"
        f"Main Risks: {risks}\n"
        "Assumptions / What I need from you: Ask about a specific strategy if you want this applied to a trade."
    )


def format_greek_comparison(terms: list[str]) -> str:
    term_list = [t for t in terms if t != "convexity"]
    if not term_list:
        return format_convexity()
    summary = " and ".join([t.title() if t != "implied volatility" else "Implied Volatility" for t in term_list])
    setup = "; ".join([f"{t.title() if t != 'implied volatility' else 'Implied Volatility'}: {strip_label(format_greek(t), 'Setup:')}" for t in term_list])
    payoff = "; ".join([f"{t.title() if t != 'implied volatility' else 'Implied Volatility'}: {strip_label(format_greek(t), 'Payoff at Expiration:')}" for t in term_list])
    sensitivities = "; ".join([f"{t.title() if t != 'implied volatility' else 'Implied Volatility'}: {strip_label(format_greek(t), 'Key Sensitivities:')}" for t in term_list])
    return (
        f"Summary: Comparison of {summary}.\n"
        f"Setup: {setup}\n"
        f"Payoff at Expiration: {payoff}\n"
        "Max Profit: Depends on the position.\n"
        "Max Loss: Depends on the position.\n"
        "Breakeven(s): Depend on strikes and premiums.\n"
        f"Key Sensitivities: {sensitivities}\n"
        "Typical Use Case: Use these to understand different risk drivers in options pricing.\n"
        "Main Risks: Misreading the dominant risk driver can lead to poor positioning.\n"
        "Assumptions / What I need from you: Ask about a specific strategy if you want these applied to a trade."
    )


def strip_label(text: str, label: str) -> str:
    for line in text.splitlines():
        if line.startswith(label):
            return line[len(label):].strip()
    return ""


def format_convexity() -> str:
    return (
        "Summary: Convexity is the curvature of P/L, driven by gamma (delta changing as price moves).\n"
        "Setup: Long options are positive convexity (long gamma); short options are negative convexity (short gamma).\n"
        "Payoff at Expiration: Positive convexity benefits from large moves; negative convexity benefits from small moves and time decay.\n"
        "Max Profit: Positive convexity can accelerate gains on large moves; negative convexity is often capped by credit.\n"
        "Max Loss: Positive convexity is often limited to premium; negative convexity can be large without defined risk.\n"
        "Breakeven(s): Depend on strikes and premiums; convexity affects how quickly P/L improves beyond breakevens.\n"
        "Key Sensitivities: Gamma is highest near the strike and for shorter expirations.\n"
        "Typical Use Case: Use positive convexity when expecting large moves; negative convexity for range-bound views.\n"
        "Main Risks: Long gamma pays for time; short gamma can suffer in sharp moves.\n"
        "Assumptions / What I need from you: Name a strategy if you want convexity for a specific structure."
    )


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
