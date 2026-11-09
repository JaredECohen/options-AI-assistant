"""Expiration payoff, exact bounds and breakevens for multi-leg positions.

Correctness notes (T5):
- Expiration P/L of a set of vanilla options plus stock is piecewise linear in
  the underlying price S on [0, inf), with breakpoints only at strikes. The
  extremes are therefore attained at S = 0, at a strike, or in the upper tail.
  Bounds are derived from those points and from the upper-tail slope, never
  from a chart grid. (The old code took min/max over a grid that started at
  half the lowest strike, so a short 100 put for 5 reported a $4,500 max loss
  instead of $9,500.)
- Breakevens are exact roots of each linear segment.
- Positions whose option legs expire on different dates have no single
  expiration payoff; they are rejected rather than silently mixed.
- Every leg must carry an explicit premium. A missing premium is an error, not
  zero. Quote-derived premiums record the contract, source, timestamp and fill
  convention they came from.
- Figures are per position, in the quote currency (USD), before commissions,
  fees, assignment costs and taxes.
- Unbounded results are encoded as the string "unlimited" (valid JSON) plus a
  structured `bounds` block; float infinity never reaches the response.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

from app.models import OptionLeg, StockLeg
from app.providers.options.base import OptionQuote, OptionsDataProvider, QuoteUnavailable

DEFAULT_CONTRACT_MULTIPLIER = 100
FillConvention = Literal["mid", "conservative"]
_EPS = 1e-9


class PayoffInputError(ValueError):
    """The position cannot be evaluated as specified (422, not 500)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Premium resolution
# ---------------------------------------------------------------------------


def premium_from_quote(quote: OptionQuote, side: str, convention: FillConvention) -> float:
    """Return the per-share premium a fill would use.

    mid:          (bid + ask) / 2; requires a two-sided, uncrossed quote.
    conservative: buys pay the ask, sells receive the bid.
    `last` is never used: it can be arbitrarily stale relative to the quote.
    """
    bid, ask = quote.bid, quote.ask
    if bid is None or ask is None:
        raise PayoffInputError(
            "quote_one_sided",
            f"No two-sided quote for {quote.symbol or quote.strike} {quote.option_type}; enter a premium manually.",
        )
    if bid < 0 or ask < 0:
        raise PayoffInputError("quote_invalid", "Quote has a negative price.")
    if bid > ask + _EPS:
        raise PayoffInputError(
            "quote_crossed",
            f"Quote for {quote.symbol or quote.strike} is crossed (bid {bid} > ask {ask}); enter a premium manually.",
        )
    if convention == "conservative":
        return float(ask if side == "buy" else bid)
    return (float(bid) + float(ask)) / 2


async def resolve_premiums(
    ticker: str,
    legs: list[OptionLeg | StockLeg],
    provider: OptionsDataProvider,
    convention: FillConvention = "mid",
) -> tuple[list[OptionLeg | StockLeg], list[dict], float]:
    """Fill in missing premiums from the provider and record their provenance."""
    validate_same_expiration(legs)
    underlying = await provider.get_underlying_price(ticker)
    premiums_used: list[dict] = []
    resolved: list[OptionLeg | StockLeg] = []
    for leg in legs:
        record: dict[str, Any] = {**leg.model_dump()}
        if leg.premium is not None:
            premium = float(leg.premium)
            record.update(premium=premium, premium_source="user_entered")
        elif isinstance(leg, OptionLeg):
            try:
                quote = await provider.get_option_quote(ticker, leg.expiration, leg.strike, leg.option_type)
            except QuoteUnavailable as exc:
                raise PayoffInputError("quote_unavailable", str(exc)) from exc
            premium = premium_from_quote(quote, leg.side, convention)
            record.update(
                premium=round(premium, 4),
                premium_source="quote",
                fill_convention=convention,
                contract_symbol=quote.symbol,
                bid=quote.bid,
                ask=quote.ask,
                quote_time=quote.quote_time,
                provider=provider.name,
                data_status=provider.data_status,
            )
            if quote.multiplier and quote.multiplier != leg.multiplier:
                leg = leg.model_copy(update={"multiplier": quote.multiplier})
                record["multiplier"] = quote.multiplier
        else:
            premium = float(underlying)
            record.update(
                premium=premium,
                premium_source="underlying_quote",
                provider=provider.name,
                data_status=provider.data_status,
            )
        premiums_used.append(record)
        resolved.append(leg.model_copy(update={"premium": premium}))
    return resolved, premiums_used, underlying


def validate_same_expiration(legs: list[OptionLeg | StockLeg]) -> None:
    expirations = {leg.expiration for leg in legs if isinstance(leg, OptionLeg)}
    if len(expirations) > 1:
        raise PayoffInputError(
            "mixed_expirations",
            "Legs expire on different dates (" + ", ".join(sorted(expirations)) + "). An expiration payoff is only "
            "defined when every option leg shares one expiration; calendar and diagonal spreads need a pricing model.",
        )


# ---------------------------------------------------------------------------
# Piecewise-linear payoff
# ---------------------------------------------------------------------------


def _require_premium(leg: OptionLeg | StockLeg) -> float:
    if leg.premium is None:
        raise PayoffInputError("premium_missing", "Every leg needs a premium (or a resolvable quote) before payoff is computed.")
    return float(leg.premium)


def _units(leg: OptionLeg | StockLeg) -> float:
    """Shares controlled by the leg (signed by side handled separately)."""
    if isinstance(leg, OptionLeg):
        return float(leg.quantity) * float(leg.multiplier)
    return float(leg.quantity)


def leg_payoff(leg: OptionLeg | StockLeg, price: float) -> float:
    premium = _require_premium(leg)
    if isinstance(leg, OptionLeg):
        intrinsic = max(price - leg.strike, 0.0) if leg.option_type == "call" else max(leg.strike - price, 0.0)
        per_share = intrinsic - premium if leg.side == "buy" else premium - intrinsic
    else:
        per_share = price - premium if leg.side == "buy" else premium - price
    return per_share * _units(leg)


def position_payoff(legs: list[OptionLeg | StockLeg], price: float) -> float:
    return sum(leg_payoff(leg, price) for leg in legs)


def upper_tail_slope(legs: list[OptionLeg | StockLeg]) -> float:
    """dP/dS for S above every strike: long calls/stock +, short calls/stock -; puts are flat there."""
    slope = 0.0
    for leg in legs:
        sign = 1.0 if leg.side == "buy" else -1.0
        if isinstance(leg, StockLeg) or leg.option_type == "call":
            slope += sign * _units(leg)
    return slope


def breakpoints(legs: list[OptionLeg | StockLeg]) -> list[float]:
    strikes = {float(leg.strike) for leg in legs if isinstance(leg, OptionLeg)}
    return sorted({0.0, *strikes})


@dataclass(frozen=True)
class Bound:
    unbounded: bool
    value: float | None  # P/L at the extreme (negative for a loss); None when unbounded
    at_price: float | None  # underlying price where it is attained; None when unbounded

    def as_json(self) -> dict:
        return {
            "unbounded": self.unbounded,
            "value": None if self.value is None else round(self.value, 2),
            "at_price": None if self.at_price is None else round(self.at_price, 4),
        }


def payoff_bounds(legs: list[OptionLeg | StockLeg]) -> tuple[Bound, Bound]:
    """Exact (max_profit, max_loss) over S in [0, inf)."""
    points = breakpoints(legs)
    values = [(position_payoff(legs, s), s) for s in points]
    slope = upper_tail_slope(legs)

    best_val, best_at = max(values, key=lambda t: (t[0], -t[1]))
    worst_val, worst_at = min(values, key=lambda t: (t[0], t[1]))
    max_profit = Bound(True, None, None) if slope > _EPS else Bound(False, best_val, best_at)
    max_loss = Bound(True, None, None) if slope < -_EPS else Bound(False, worst_val, worst_at)
    return max_profit, max_loss


def exact_breakevens(legs: list[OptionLeg | StockLeg]) -> list[float]:
    """Roots of the expiration payoff on [0, inf), segment by segment."""
    points = breakpoints(legs)
    roots: list[float] = []

    def add(x: float) -> None:
        if x >= -_EPS and not any(abs(x - r) < 1e-6 for r in roots):
            roots.append(max(x, 0.0))

    for left, right in zip(points, points[1:], strict=False):
        y0, y1 = position_payoff(legs, left), position_payoff(legs, right)
        if abs(y0) < _EPS and abs(y1) < _EPS:
            continue  # flat at zero across the segment: no isolated breakeven
        if abs(y0) < _EPS:
            add(left)
        if y0 * y1 < 0:
            add(left + (-y0) * (right - left) / (y1 - y0))
        if abs(y1) < _EPS:
            add(right)

    last = points[-1]
    y_last = position_payoff(legs, last)
    slope = upper_tail_slope(legs)
    if abs(y_last) < _EPS:
        add(last)
    elif abs(slope) > _EPS:
        x = last - y_last / slope
        if x > last + _EPS:
            add(x)
    return sorted(round(r, 4) for r in roots)


def chart_grid(underlying: float, legs: list[OptionLeg | StockLeg], steps: int = 201) -> list[float]:
    """Display grid only. It always contains 0, every strike and every breakeven so the
    chart passes through the true kinks; bounds never depend on it."""
    strikes = [float(leg.strike) for leg in legs if isinstance(leg, OptionLeg)]
    hi_ref = max([underlying, *strikes]) if strikes or underlying else 1.0
    high = max(hi_ref * 1.5, 1.0)
    step = high / (steps - 1)
    grid = {round(step * i, 4) for i in range(steps)}
    grid.update(round(k, 4) for k in strikes)
    grid.update(b for b in exact_breakevens(legs) if b <= high)
    return sorted(grid)


def _money(v: float) -> float:
    r = round(v, 2)
    return 0.0 if r == 0 else r  # no "-0.0"


def compute_payoff(
    ticker: str,
    legs: list[OptionLeg | StockLeg],
    premiums_used: list[dict],
    underlying_price: float,
    quote_source: str,
    data_status: str | None = None,
) -> dict:
    if not legs:
        raise PayoffInputError("no_legs", "No legs supplied.")
    validate_same_expiration(legs)
    for leg in legs:
        _require_premium(leg)
        if leg.quantity <= 0:
            raise PayoffInputError("quantity_invalid", "Quantities must be positive; use side=sell for short legs.")

    net = 0.0
    for leg in legs:
        sign = 1.0 if leg.side == "sell" else -1.0
        net += sign * _require_premium(leg) * _units(leg)
    max_profit, max_loss = payoff_bounds(legs)

    def legacy(bound: Bound, loss: bool) -> Any:
        # Signed, never clamped: a position whose best outcome is a loss reports a
        # NEGATIVE max profit (and one that cannot lose reports a negative max loss)
        # instead of a misleading 0.
        if bound.unbounded:
            return "unlimited"
        assert bound.value is not None
        return _money(-bound.value) if loss else _money(bound.value)

    prices = chart_grid(underlying_price, legs)
    curve = [{"price": round(p, 2), "payoff": _money(position_payoff(legs, p))} for p in prices]
    for point in curve:
        if not all(math.isfinite(v) for v in point.values()):
            raise PayoffInputError("non_finite", "Non-finite payoff value; check inputs.")

    expirations = sorted({leg.expiration for leg in legs if isinstance(leg, OptionLeg)})
    return {
        "computed": {
            "net_debit": _money(-net) if net < 0 else 0.0,
            "net_credit": _money(net) if net > 0 else 0.0,
            "max_profit": legacy(max_profit, loss=False),
            "max_loss": legacy(max_loss, loss=True),
            "bounds": {
                "max_profit": max_profit.as_json(),
                "max_loss": max_loss.as_json(),
                "domain": "underlying price at expiration, S >= 0",
                "method": "analytic: S=0, strike breakpoints and upper-tail slope",
            },
            "breakevens": [round(b, 2) for b in exact_breakevens(legs)],
            "premiums_used": premiums_used,
            "underlying_price": round(underlying_price, 2),
            "quote_source": quote_source,
            "data_status": data_status,
            "expiration": expirations[0] if expirations else None,
            "conventions": {
                "currency": "USD",
                "units": "per position (quantity x multiplier shares per option leg)",
                "excludes": ["commissions", "fees", "assignment costs", "taxes", "early exercise"],
            },
            "inputs_used": {"ticker": ticker, "legs": [leg.model_dump() for leg in legs]},
            "payoff_curve": curve,
        }
    }
