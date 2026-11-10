"""A worked example with live prices for the Strategy Builder's "Explain" answer.

The textbook explanation says "max profit = strike width - net debit"; this adds
the same strategy priced from the current chain: real strikes, mid premiums and
the exact payoff numbers from app.core.payoff. Every figure carries its source
and data status. If the chain cannot be priced, the example says so and the
explanation still stands.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from app.core.payoff import compute_payoff
from app.models import OptionLeg, StockLeg
from app.providers.options.base import DATA_STATUS_LABELS

NY = ZoneInfo("America/New_York")
TARGET_DTE = 30
MIN_DTE = 7

# Each leg: ("stock", side) or (option_type, side, strike as a multiple of spot).
SHAPES: dict[str, list[tuple]] = {
    "long call": [("call", "buy", 1.00)],
    "long put": [("put", "buy", 1.00)],
    "covered call": [("stock", "buy"), ("call", "sell", 1.05)],
    "cash-secured put": [("put", "sell", 0.95)],
    "bull call spread": [("call", "buy", 1.00), ("call", "sell", 1.05)],
    "bear call spread": [("call", "sell", 1.03), ("call", "buy", 1.06)],
    "bull put spread": [("put", "sell", 0.97), ("put", "buy", 0.94)],
    "bear put spread": [("put", "buy", 1.00), ("put", "sell", 0.95)],
    "long straddle": [("call", "buy", 1.00), ("put", "buy", 1.00)],
    "long strangle": [("call", "buy", 1.05), ("put", "buy", 0.95)],
    "short straddle": [("call", "sell", 1.00), ("put", "sell", 1.00)],
    "short strangle": [("call", "sell", 1.07), ("put", "sell", 0.93)],
    "iron condor": [("put", "buy", 0.90), ("put", "sell", 0.95), ("call", "sell", 1.05), ("call", "buy", 1.10)],
    "iron butterfly": [("put", "buy", 0.95), ("put", "sell", 1.00), ("call", "sell", 1.00), ("call", "buy", 1.05)],
    "collar": [("stock", "buy"), ("put", "buy", 0.95), ("call", "sell", 1.05)],
    "protective put": [("stock", "buy"), ("put", "buy", 0.95)],
}


def pick_expiration(expirations: list[str], today: dt.date) -> str | None:
    dated = [(abs((dt.date.fromisoformat(e) - today).days - TARGET_DTE), e) for e in expirations
             if (dt.date.fromisoformat(e) - today).days >= MIN_DTE]
    return min(dated)[1] if dated else None


def pick_strikes(shape: list[tuple], spot: float, quoted: dict[str, list[float]]) -> list[float | None]:
    """For each option leg, the listed strike (with a two-sided quote) nearest
    spot x multiple. Two legs of the same type never share a strike, so a spread
    on a coarse strike grid widens instead of collapsing to zero width."""
    out: list[float | None] = []
    used: dict[str, set[float]] = {"call": set(), "put": set()}
    for leg in shape:
        if leg[0] == "stock":
            out.append(None)
            continue
        kind, _side, mult = leg
        candidates = [k for k in quoted.get(kind, []) if k not in used[kind]]
        if not candidates:
            raise LookupError(f"not enough quoted {kind}s")
        target = spot * mult
        choice = min(candidates, key=lambda k: (abs(k - target), k))
        used[kind].add(choice)
        out.append(choice)
    return out


def _money(v) -> str:
    if v == "unlimited":
        return "unlimited"
    return f"${float(v):,.2f}"


async def live_example(strategy: str, ticker: str, provider, now: dt.datetime | None = None) -> str | None:
    """Markdown block, or None when the strategy has no simple example shape."""
    shape = SHAPES.get(strategy)
    if not shape:
        return None
    today = (now or dt.datetime.now(NY)).astimezone(NY).date()
    spot = await provider.get_underlying_price(ticker)
    exp = pick_expiration(await provider.list_expirations(ticker), today)
    if exp is None:
        raise LookupError("no expiration a week or more out")
    chain = await provider.get_chain(ticker, exp)
    mids: dict[tuple[str, float], float] = {}
    quoted: dict[str, list[float]] = {"call": [], "put": []}
    for q in chain:
        if q.bid is not None and q.ask is not None and q.bid > 0 and q.ask >= q.bid:
            mids[(q.option_type, q.strike)] = round((q.bid + q.ask) / 2, 2)
            quoted[q.option_type].append(q.strike)
    strikes = pick_strikes(shape, spot, quoted)
    legs, used, lines = [], [], []
    for leg, strike in zip(shape, strikes, strict=True):
        if leg[0] == "stock":
            legs.append(StockLeg(side=leg[1], quantity=100, premium=round(spot, 2)))
            used.append({"leg": "stock", "premium": round(spot, 2), "source": "underlying price"})
            lines.append(f"- {leg[1].capitalize()} 100 shares at {_money(spot)}")
            continue
        kind, side, _mult = leg
        premium = mids[(kind, strike)]
        legs.append(OptionLeg(option_type=kind, side=side, strike=strike, expiration=exp, premium=premium))
        used.append({"leg": f"{side} {strike:g} {kind}", "premium": premium, "source": f"{provider.name} mid"})
        lines.append(f"- {side.capitalize()} 1 {strike:g} {kind} at {_money(premium)} (mid)")
    c = compute_payoff(ticker, legs, used, spot, provider.name, provider.data_status)["computed"]
    label = DATA_STATUS_LABELS.get(provider.data_status, provider.data_status)
    dte = (dt.date.fromisoformat(exp) - today).days
    net = f"Net credit: {_money(c['net_credit'])}" if c["net_credit"] else f"Net debit: {_money(c['net_debit'])}"
    bes = ", ".join(f"${b:,.2f}" for b in c["breakevens"]) or "none"
    return "\n".join([
        f"### Live example: {ticker} at {_money(spot)}, {exp} expiry ({dte} days)",
        *lines,
        f"- {net} (one contract = 100 shares)",
        f"- Max profit: {_money(c['max_profit'])}; max loss: {_money(c['max_loss'])}",
        f"- Breakeven(s) at expiration: {bes}",
        "",
        f"*Prices: {provider.name}, {label}, mid of bid/ask. Strikes are illustrative (about "
        "the usual distance from the price), not a recommendation. Excludes commissions and early exercise.*",
    ])
