"""Freeform (conversational) rendering and the structured-to-freeform rewriter."""
from __future__ import annotations

from app.chat.intents import asked_horizon_before, compute_moneyness_range, extract_horizon, extract_percent_range
from app.chat.labels import convexity_detail_for_strategy, friendly_name, strategy_view_label
from app.core.strategies import ALIASES, STRATEGIES, get_strategy, normalize_strategy_name


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


def format_view_clarify_freeform() -> str:
    return "Which specific strategy are you referring to? I can describe the market view once you name it."


def format_payoff_mechanics_freeform() -> str:
    return (
        "At expiration, option payoffs depend only on where the stock finishes versus the strikes and the premiums you paid or received. "
        "Calls are worth max(price - strike, 0) and puts are worth max(strike - price, 0), while spreads cap gains and losses by pairing a long and short leg. "
        "Long options have limited loss (the premium) and directional upside, while short options collect premium but take the opposite risk. "
        "Breakevens are the strike adjusted by the net premium. If you want exact numbers, share the legs and premiums."
    )


def format_long_short_comparison_freeform() -> str:
    return (
        "Long options pay a premium for limited loss and upside exposure, while short options collect premium but carry the opposite risk. "
        "A long call can make unlimited upside and loses only the premium; a short call keeps the premium but can face unlimited loss. "
        "Long puts are capped by the stock going to zero, while short puts can lose heavily on a big drop. "
        "Long options are positive convexity with negative theta; short options are negative convexity with positive theta."
    )


def to_freeform(text: str, keep_json: bool = False) -> str:
    import re

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

    paragraph = re.sub(r"(^|[.!?]\s+)(i)(['a-z])", lambda m: f"{m.group(1)}I{m.group(3)}", paragraph)

    if json_block:
        paragraph = f"{paragraph}\n\n{json_block}"

    return paragraph


def _clean_sentence(text: str) -> str:
    s = text.strip()
    return s[:-1] if s.endswith((".", "!", "?")) else s


def _sentence_case(text: str) -> str:
    s = text.strip()
    if s and s[0].islower():
        s = s[0].upper() + s[1:]
    return s


def _normalize_item(text: str) -> str:
    content = _clean_sentence(text)
    if content and content[0].isupper() and not (len(content) > 1 and content[1].isupper()):
        content = content[0].lower() + content[1:]
    content = content.replace(" Put strike", " put strike").replace(" Call strike", " call strike")
    for verb in ["Buy", "Sell", "Own", "Hold", "Profit", "Benefits", "Benefit", "Seeks", "Expecting"]:
        if content.startswith(verb + " "):
            return verb.lower() + content[len(verb):]
    return content


def _is_question_like(text: str) -> bool:
    lower = text.strip().lower()
    if text.strip().endswith("?"):
        return True
    return lower.startswith(
        (
            "are ",
            "is ",
            "do ",
            "does ",
            "did ",
            "can ",
            "could ",
            "would ",
            "will ",
            "should ",
            "what ",
            "which ",
            "why ",
            "how ",
            "when ",
            "where ",
            "who ",
            "have ",
            "has ",
            "had ",
        )
    )


def _is_clause_like(text: str) -> bool:
    lower = text.strip().lower()
    if not lower:
        return False
    if _is_question_like(text):
        return True
    return lower.startswith(
        (
            "i ",
            "you ",
            "we ",
            "they ",
            "he ",
            "she ",
            "it ",
            "it's ",
            "this ",
            "that ",
            "these ",
            "those ",
            "there ",
            "the ",
            "a ",
            "an ",
            "tell me ",
            "please tell me ",
            "depends ",
            "depend ",
            "varies ",
            "vary ",
            "will depend ",
            "can vary ",
            "may vary ",
            "can be ",
            "may be ",
            "will be ",
            "profits ",
            "profit ",
            "losses ",
            "loss ",
            "price ",
            "upside ",
            "downside ",
            "breakeven ",
            "breakevens ",
            "payoff ",
            "p/l ",
            "option ",
            "options ",
            "delta ",
            "gamma ",
            "theta ",
            "vega ",
            "rho ",
            "implied ",
            "time ",
            "volatility ",
            "market ",
            "trading ",
        )
    )


def _strip_placeholder_subject(text: str) -> str:
    s = text.strip()
    lower = s.lower()
    prefixes = (
        "this is ",
        "this ",
        "these are ",
        "these ",
        "it is ",
        "it ",
    )
    useful_starts = (
        "depends",
        "depend",
        "varies",
        "vary",
        "also varies",
        "also depends",
        "specific to",
        "for when",
        "the payoff",
        "greeks ",
        "options trading ",
        "trading decisions ",
        "can vary",
        "may vary",
        "will depend",
    )
    for prefix in prefixes:
        if lower.startswith(prefix):
            remainder = s[len(prefix) :].strip()
            if remainder.lower().startswith(useful_starts):
                return remainder
    return s


def _finish_sentence(text: str) -> str:
    s = _sentence_case(_clean_sentence(text))
    if not s:
        return ""
    return f"{s}?" if _is_question_like(text) else f"{s}."


def _is_imperative_like(text: str) -> bool:
    lower = text.strip().lower()
    return lower.startswith(
        (
            "provide ",
            "share ",
            "tell me ",
            "name ",
            "choose ",
            "pick ",
            "enter ",
            "use ",
            "give ",
        )
    )


def _prefixed_sentence(prefix: str, text: str) -> str:
    content = _strip_placeholder_subject(_normalize_item(text))
    if not content:
        return ""
    lower = content.lower()
    if _is_question_like(content):
        return _finish_sentence(content)
    if lower.startswith(prefix.lower()):
        return _finish_sentence(content)
    return _finish_sentence(f"{prefix} {content}")


def _format_limit_sentence(label: str, text: str) -> str:
    content = _strip_placeholder_subject(_normalize_item(text))
    if not content:
        return ""
    lower = content.lower()
    if _is_question_like(content):
        return _finish_sentence(content)
    if label == "Upside" and lower.startswith("unlimited upside"):
        return _finish_sentence(content)
    if lower.startswith(
        (
            "the premium",
            "the net debit",
            "the net credit",
            "premium",
            "net debit",
            "net credit",
            "debit",
            "credit",
            "strike width",
            "structural limits",
            "long-premium",
            "short-premium",
        )
    ):
        return _finish_sentence(f"{label} is {content}")
    if lower.startswith(("is ", "depends ", "depend ", "varies ", "vary ", "also varies ", "also depend ", "can be ", "may be ", "will be ")):
        return _finish_sentence(f"{label} {content}")
    if lower.startswith(("limited", "unlimited", "capped", "large", "small")):
        return _finish_sentence(f"{label} is {content}")
    if _is_clause_like(content):
        return _finish_sentence(content)
    return _finish_sentence(f"{label} is {content}")


def _should_prefix_it(text: str) -> bool:
    lower = text.strip().lower()
    if _is_clause_like(text):
        return False
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
    if lower in ("delta", "gamma", "theta", "vega", "rho", "convexity", "implied volatility"):
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
        if summary_lower.startswith("it's a to "):
            summary_clean = "To " + summary_clean[len("It's a to ") :].lstrip()
            summary_lower = summary_clean.lower()
        if summary_lower.startswith(
            (
                "here is",
                "this is",
                "it's ",
                "it is ",
                "comparison of",
                "to ",
                "convexity ",
                "strike selection",
                "expiration selection",
                "expiration choice",
                "income-focused",
                "income strategies",
                "option payoffs",
                "i can ",
                "i focus ",
            )
        ):
            sentences.append(summary_clean + ".")
        elif _is_clause_like(summary_clean):
            sentences.append(_finish_sentence(summary_clean))
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
            if _is_clause_like(setup):
                sentences.append(_finish_sentence(setup))
            elif _is_imperative_like(setup):
                sentences.append(_finish_sentence(setup))
            elif setup.startswith(("buy ", "sell ", "own ", "hold ")):
                sentences.append(f"The setup is to {setup}.")
            else:
                sentences.append(f"The setup is {setup}.")
    if sections.get("Payoff at Expiration"):
        payoff = _normalize_item(sections["Payoff at Expiration"])
        payoff = _strip_placeholder_subject(payoff)
        payoff_lower = payoff.lower()
        if payoff.startswith("profit rises"):
            payoff = payoff.replace("profit rises", "profits", 1)
        if payoff.startswith("profit "):
            payoff = "profits " + payoff[len("profit ") :]
        if payoff.startswith("computed from"):
            sentences.append(_finish_sentence(f"At expiration, the payoff is {payoff}"))
        elif "expiration" in payoff_lower or "expires" in payoff_lower:
            sentences.append(_finish_sentence(payoff))
        elif _is_question_like(payoff):
            sentences.append(_finish_sentence(payoff))
        elif _is_clause_like(payoff):
            sentences.append(_finish_sentence(f"At expiration, {payoff}"))
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
            profit_phrase = _strip_placeholder_subject(_normalize_item(max_profit))
            loss_phrase = _strip_placeholder_subject(_normalize_item(max_loss))
            if any(term in profit_phrase for term in ["with ", "for ", "positive", "negative", "convexity"]) or any(
                term in loss_phrase for term in ["with ", "for ", "positive", "negative", "convexity"]
            ):
                sentences.append(f"On the upside, {profit_phrase}.")
                sentences.append(f"On the downside, {loss_phrase}.")
            else:
                upside_sentence = _format_limit_sentence("Upside", profit_phrase)
                downside_sentence = _format_limit_sentence("Downside", loss_phrase)
                if upside_sentence.startswith("Upside") and downside_sentence.startswith("Downside"):
                    sentences.append(
                        f"{_clean_sentence(upside_sentence)} while {_clean_sentence(downside_sentence).lower()}."
                    )
                else:
                    sentences.append(upside_sentence)
                    sentences.append(downside_sentence)
        elif max_profit:
            profit_phrase = _strip_placeholder_subject(_normalize_item(max_profit))
            if any(term in profit_phrase for term in ["with ", "for ", "positive", "negative", "convexity"]):
                sentences.append(f"On the upside, {profit_phrase}.")
            else:
                sentences.append(_format_limit_sentence("Upside", profit_phrase))
        elif max_loss:
            loss_phrase = _strip_placeholder_subject(_normalize_item(max_loss))
            if any(term in loss_phrase for term in ["with ", "for ", "positive", "negative", "convexity"]):
                sentences.append(f"On the downside, {loss_phrase}.")
            else:
                sentences.append(_format_limit_sentence("Downside", loss_phrase))
    if sections.get("Breakeven(s)"):
        breakeven = _strip_placeholder_subject(_normalize_item(sections["Breakeven(s)"]))
        if breakeven.startswith("depend on"):
            cleaned = breakeven.replace("depend on", "", 1).strip()
            sentences.append(f"Breakevens depend on {cleaned}.")
        elif breakeven.startswith("computed from"):
            sentences.append(_finish_sentence(f"Breakevens are {breakeven}"))
        elif breakeven.startswith(("depends ", "depend ", "varies ", "vary ", "will depend ", "can vary ", "may vary ")):
            sentences.append(_finish_sentence(f"Breakevens {breakeven}"))
        else:
            sentences.append(f"Breakevens are {breakeven}.")
    if sections.get("Key Sensitivities"):
        sensitivities = _normalize_item(sections["Key Sensitivities"])
        if sensitivities.startswith("i can "):
            sentences.append(_finish_sentence(sensitivities))
        else:
            sentences.append(f"Key sensitivities include {sensitivities}.")
    if sections.get("Typical Use Case"):
        use_case = _strip_placeholder_subject(_normalize_item(sections["Typical Use Case"]))
        if use_case.startswith("for when "):
            sentences.append(_finish_sentence(f"A common use case is when {use_case[len('for when '):]}"))
        elif use_case.startswith("use "):
            sentences.append(_finish_sentence(f"A common use case is to {use_case}"))
        elif use_case.startswith("expecting"):
            use_case = use_case.replace("expecting big ", "expecting a big ")
            use_case = use_case.replace("expecting large ", "expecting a large ")
            sentences.append(f"A common use case is when {use_case}.")
        elif use_case.startswith(("depends ", "depend ", "varies ", "vary ", "will depend ", "can vary ", "may vary ")):
            sentences.append(_finish_sentence(f"A common use case {use_case}"))
        elif use_case.lower().startswith(("a ", "an ", "the ")):
            sentences.append(_finish_sentence(f"A common use case is {use_case}"))
        elif _is_clause_like(use_case):
            sentences.append(_finish_sentence(use_case))
        else:
            sentences.append(f"A common use case is {use_case}.")
    if sections.get("Main Risks"):
        main_risks = _strip_placeholder_subject(_normalize_item(sections["Main Risks"]))
        if main_risks.startswith(("depends ", "depend ", "varies ", "vary ", "will depend ", "can vary ", "may vary ")):
            sentences.append(_finish_sentence(f"Main risks {main_risks}"))
        elif main_risks.lower().startswith(
            (
                "time decay",
                "iv crush",
                "opportunity cost",
                "large move",
                "sharp move",
                "loss",
                "losses",
                "insufficient",
            )
        ):
            sentences.append(_finish_sentence(f"Main risks include {main_risks}"))
        elif _is_clause_like(main_risks):
            sentences.append(_finish_sentence(main_risks))
        else:
            sentences.append(f"Main risks include {main_risks}.")
    if sections.get("Assumptions / What I need from you"):
        sentences.append(_clean_sentence(sections["Assumptions / What I need from you"]) + ".")

    return " ".join([s for s in sentences if s]).strip()


def off_topic_freeform(user_text: str) -> str:
    return (
        "I focus on listed equity options strategies and payoff mechanics. "
        "Ask me about a specific strategy, a market view (bullish/bearish/neutral/volatile), "
        "or how strike and expiration choices affect payoffs."
    )


def format_moneyness_freeform() -> str:
    return (
        "Moneyness describes where the strike sits relative to the stock price. "
        "A call is in the money when the stock is above the strike, at the money when it's near the strike, and out of the money when the stock is below it; for puts, that relationship is reversed. "
        "Moneyness affects premium, delta, and how likely the option is to finish with intrinsic value. "
        "If you want, I can apply that to a specific strike or strategy."
    )


def format_intrinsic_value_freeform() -> str:
    return (
        "Intrinsic value is the immediate exercise value of an option. "
        "For a call, it's max(stock price minus strike, 0); for a put, it's max(strike minus stock price, 0). "
        "If an option is out of the money, its intrinsic value is zero. "
        "Anything above intrinsic value is extrinsic value."
    )


def format_extrinsic_value_freeform() -> str:
    return (
        "Extrinsic value is the portion of an option's premium above its intrinsic value. "
        "It reflects time remaining, implied volatility, interest rates, and the probability the option could become more valuable before expiration. "
        "Extrinsic value decays with time, especially as expiration approaches. "
        "At expiration, extrinsic value goes to zero."
    )


def format_assignment_freeform() -> str:
    return (
        "Assignment happens when a short option is exercised against you. "
        "If you're short a call, assignment means you may have to deliver shares; if you're short a put, you may have to buy shares at the strike price. "
        "American-style equity options can be assigned before expiration, though assignment risk is usually highest near expiration or around dividends. "
        "Defined-risk spreads can still be assigned on the short leg, so it's important to manage them before expiration."
    )


def format_breakeven_freeform() -> str:
    return (
        "Breakeven is the stock price where the position's profit and loss is zero at expiration. "
        "For a long call, it's strike plus premium; for a long put, it's strike minus premium. "
        "For spreads and multi-leg trades, breakevens shift by the net debit or credit you paid or received. "
        "If you give me the exact legs, I can compute the breakeven precisely."
    )


def format_key_terms_freeform() -> str:
    return (
        "The core beginner terms are call, put, strike, expiration, premium, intrinsic value, extrinsic value, moneyness, breakeven, assignment, and the main Greeks: delta, gamma, theta, vega, and rho. "
        "If you're learning in order, start with call/put, strike, expiration, premium, and moneyness first, then add breakeven and the Greeks. "
        "Once those are clear, strategies like spreads, covered calls, and straddles make much more sense. "
        "I can walk through any of those one by one."
    )


def illegal_refusal_freeform() -> str:
    return (
        "I can’t help with illegal activity. "
        "If you want legal, educational help, I can explain options mechanics, strategy trade-offs, or evaluate a position if you share the legs."
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


def view_menu_prompt_freeform() -> str:
    return "Which market view should I use: bullish, bearish, neutral, volatile, or short volatility?"


def use_case_phrase(text: str) -> str:
    clean = text.strip().rstrip(".")
    lower = clean.lower()
    if lower.startswith("expecting"):
        return "when you expect " + clean[len("expecting ") :]
    if lower.startswith("use"):
        return "when you " + clean
    return "when you want " + clean


def clarifying_question_freeform(_: str) -> str:
    return (
        "I can help with strategies, market views, or mechanics. "
        "Do you want a strategy overview, a view-based trade menu (bullish/bearish/neutral/volatile), "
        "or an explanation of a specific term like delta/vega/theta?"
    )
