"""Rendering of computed payoff results and ticker context. Numbers come from app.core.payoff."""
from __future__ import annotations

import json


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


def attach_ticker_context(response_text: str, ticker: str | None, mode: str) -> str:
    symbol = (ticker or "").upper().strip()
    if not symbol:
        return response_text

    upper_text = response_text.upper()
    if symbol in upper_text:
        return response_text

    if mode == "freeform":
        stripped = response_text.strip()
        if not stripped:
            return response_text
        return f"For {symbol}, {stripped[0].lower() + stripped[1:]}" if stripped[0].isupper() else f"For {symbol}, {stripped}"

    marker = "Summary: "
    if response_text.startswith(marker):
        return f"{marker}For {symbol}, {response_text[len(marker):]}"
    return response_text


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
