from __future__ import annotations

from dataclasses import asdict
from typing import Any, List, Tuple

from app.models import OptionLeg, StockLeg
from app.providers.options.base import OptionQuote, OptionsDataProvider

CONTRACT_MULTIPLIER = 100


def _leg_to_dict(leg: OptionLeg | StockLeg, resolved_premium: float | None) -> dict:
    data = leg.model_dump()
    data["premium"] = resolved_premium
    return data


def _mark_from_quote(quote: OptionQuote) -> float:
    if quote.mark is not None:
        return float(quote.mark)
    if quote.bid is not None and quote.ask is not None:
        return (float(quote.bid) + float(quote.ask)) / 2
    if quote.last is not None:
        return float(quote.last)
    raise RuntimeError("No premium available from quote")


async def resolve_premiums(
    ticker: str,
    legs: List[OptionLeg | StockLeg],
    provider: OptionsDataProvider,
) -> Tuple[List[OptionLeg | StockLeg], List[dict], float]:
    premiums_used = []
    underlying = await provider.get_underlying_price(ticker)

    resolved_legs: List[OptionLeg | StockLeg] = []
    for leg in legs:
        premium = leg.premium
        if premium is None:
            if isinstance(leg, OptionLeg):
                quote = await provider.get_option_quote(ticker, leg.expiration, leg.strike, leg.option_type)
                premium = _mark_from_quote(quote)
            else:
                premium = underlying
        premiums_used.append(_leg_to_dict(leg, premium))
        if isinstance(leg, OptionLeg):
            resolved_legs.append(OptionLeg(**{**leg.model_dump(), "premium": premium}))
        else:
            resolved_legs.append(StockLeg(**{**leg.model_dump(), "premium": premium}))
    return resolved_legs, premiums_used, underlying


def _leg_payoff_at_price(leg: OptionLeg | StockLeg, price: float) -> float:
    qty = leg.quantity
    if isinstance(leg, OptionLeg):
        intrinsic = 0.0
        if leg.option_type == "call":
            intrinsic = max(price - leg.strike, 0.0)
        else:
            intrinsic = max(leg.strike - price, 0.0)
        premium = leg.premium or 0.0
        if leg.side == "buy":
            payoff = intrinsic - premium
        else:
            payoff = premium - intrinsic
        return payoff * qty * CONTRACT_MULTIPLIER
    else:
        premium = leg.premium or 0.0
        if leg.side == "buy":
            payoff = price - premium
        else:
            payoff = premium - price
        return payoff * qty


def _price_grid(underlying: float, strikes: List[float]) -> List[float]:
    if strikes:
        min_strike = min(strikes)
        max_strike = max(strikes)
    else:
        min_strike = underlying
        max_strike = underlying
    low = max(0.01, min(min_strike, underlying) * 0.5)
    high = max(max_strike, underlying) * 1.5
    steps = 201
    step = (high - low) / (steps - 1)
    return [round(low + step * i, 4) for i in range(steps)]


def _breakevens(prices: List[float], payoffs: List[float]) -> List[float]:
    bes = []
    for i in range(len(prices) - 1):
        p1, p2 = prices[i], prices[i + 1]
        y1, y2 = payoffs[i], payoffs[i + 1]
        if abs(y1) < 1e-8:
            bes.append(p1)
        if y1 == 0:
            continue
        if y1 * y2 < 0:
            # linear interpolation
            x = p1 + (0 - y1) * (p2 - p1) / (y2 - y1)
            bes.append(x)
    # dedupe and round
    unique = sorted({round(b, 2) for b in bes})
    return unique


def _slope_high(legs: List[OptionLeg | StockLeg]) -> float:
    slope_high = 0.0
    for leg in legs:
        qty = leg.quantity
        if isinstance(leg, StockLeg):
            delta = qty if leg.side == "buy" else -qty
            slope_high += delta
        else:
            if leg.option_type == "call":
                delta = qty * CONTRACT_MULTIPLIER
                slope_high += delta if leg.side == "buy" else -delta
    return slope_high


def compute_payoff(
    ticker: str,
    legs: List[OptionLeg | StockLeg],
    premiums_used: List[dict],
    underlying_price: float,
    quote_source: str,
) -> dict:
    strikes = [leg.strike for leg in legs if isinstance(leg, OptionLeg)]
    prices = _price_grid(underlying_price, strikes)
    payoffs = [sum(_leg_payoff_at_price(leg, price) for leg in legs) for price in prices]

    net = 0.0
    for leg in legs:
        premium = leg.premium or 0.0
        qty = leg.quantity
        if isinstance(leg, OptionLeg):
            signed = -premium if leg.side == "buy" else premium
            net += signed * qty * CONTRACT_MULTIPLIER
        else:
            signed = -premium if leg.side == "buy" else premium
            net += signed * qty

    net_debit = round(-net, 2) if net < 0 else 0.0
    net_credit = round(net, 2) if net > 0 else 0.0

    max_profit_val = max(payoffs)
    max_loss_val = min(payoffs)

    slope_high = _slope_high(legs)
    max_profit: Any = round(max_profit_val, 2)
    max_loss: Any = round(abs(min(payoffs)), 2) if max_loss_val < 0 else 0.0

    if slope_high > 0:
        max_profit = "unlimited"
    if slope_high < 0:
        max_loss = "unlimited"
    else:
        max_loss = round(abs(max_loss_val), 2) if max_loss_val < 0 else 0.0

    breakevens = _breakevens(prices, payoffs)

    payoff_curve = [
        {"price": round(p, 2), "payoff": round(v, 2)}
        for p, v in zip(prices, payoffs)
    ]

    return {
        "computed": {
            "net_debit": net_debit,
            "net_credit": net_credit,
            "max_profit": max_profit,
            "max_loss": max_loss,
            "breakevens": breakevens,
            "premiums_used": premiums_used,
            "underlying_price": round(underlying_price, 2),
            "quote_source": quote_source,
            "inputs_used": {
                "ticker": ticker,
                "legs": [leg.model_dump() for leg in legs],
            },
            "payoff_curve": payoff_curve,
        }
    }
