"""Structured (sectioned) response templates."""
from __future__ import annotations

from app.core.safety import ensure_sections
from app.core.strategies import get_strategy
from app.core.strategies import normalize_strategy_name
from app.chat.intents import compute_moneyness_range, extract_horizon, extract_percent_range
from app.chat.labels import convexity_detail_for_strategy, friendly_name, strategy_view_label
from app.chat.render_freeform import format_greek_comparison_freeform


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
    if name in ("calendar spread", "diagonal spread"):
        return (
            f"Summary: Strike selection in a {display} trades directional bias against time-decay positioning.\n"
            "Setup: A higher long strike leans more bullish; a lower long strike leans more bearish. The short strike typically sits near where you expect price to gravitate in the front month.\n"
            "Payoff at Expiration: Closer short strikes increase near-term decay benefit; wider spacing shifts the break-even zone and directional bias.\n"
            "Max Profit: Depends on how price behaves into the short expiration and how IV shifts; not a simple cap.\n"
            "Max Loss: Typically limited to the net debit.\n"
            "Breakeven(s): Vary by structure and premiums; strike spacing shifts the profitable zone.\n"
            "Key Sensitivities: Longer-dated long option adds vega; short option adds theta; gamma can be concentrated in the front month.\n"
            "Typical Use Case: Mild directional view with a time-decay edge.\n"
            "Main Risks: Large move against the short strike or IV crush.\n"
            "Assumptions / What I need from you: Share your directional bias and preferred strike spacing for specifics."
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


def format_view_clarify_structured() -> str:
    return ensure_sections(
        "Tell me which strategy you mean, and I'll describe the market view it expresses."
    )


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


def ensure_sentence(text: str) -> str:
    clean = text.strip()
    return clean if clean.endswith(".") else f"{clean}."


def view_menu_prompt_structured() -> str:
    return ensure_sections(
        "Tell me which market view you want (bullish, bearish, neutral, volatile, or short volatility), and I'll give a strategy menu."
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
