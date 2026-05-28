"""Options execution helpers.

Responsibilities
----------------
* Locate the chain row for an instrument and build a :class:`MarketQuote`.
* Mark open option positions to the chain (falling back to Black-Scholes when a
  quote is missing, e.g. a deep-ITM strike that stopped trading).
* Compute greeks at fill time.
* Settle expirations: ITM options are assigned/exercised (cash-settled at
  intrinsic for v0 clarity), OTM options expire worthless.
* Flag undefined-risk (naked short call) positions without blocking them, and
  warn when short puts are not fully cash-secured (simplified Reg-T).
"""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from backtesting_engine.core.portfolio import Portfolio
from backtesting_engine.core.types import (
    Fill,
    Instrument,
    MarketQuote,
    OptionRight,
    Side,
)
from backtesting_engine.pricing import Greeks, as_option_type, implied_volatility
from backtesting_engine.pricing import greeks as bs_greeks

logger = logging.getLogger("backtesting_engine.options")

# Default annual carry assumptions for greeks/expiry pricing (rf=0, no dividend).
DEFAULT_R = 0.0
DEFAULT_Q = 0.0


def _as_date(value: object) -> date:
    if isinstance(value, date) and not isinstance(value, pd.Timestamp):
        return value
    return pd.Timestamp(value).date()  # type: ignore[arg-type]


def find_chain_row(chain: pd.DataFrame | None, instrument: Instrument) -> pd.Series | None:
    """Return the chain row matching ``instrument`` (expiry, strike, right), or None."""
    if chain is None or chain.empty or not instrument.is_option or instrument.expiry is None:
        return None
    right = (instrument.right or OptionRight.CALL).value
    expiry = instrument.expiry
    strike = instrument.strike
    mask = (
        chain["type"].astype(str).str.lower().eq(right)
        & chain["strike"].astype(float).round(4).eq(round(float(strike or 0.0), 4))
        & chain["expiry"].map(_as_date).eq(expiry)
    )
    matches = chain[mask]
    if matches.empty:
        return None
    return matches.iloc[0]


def quote_from_chain(chain: pd.DataFrame, instrument: Instrument) -> MarketQuote | None:
    """Build a :class:`MarketQuote` for an option order from the chain."""
    row = find_chain_row(chain, instrument)
    if row is None:
        return None

    def _f(name: str) -> float | None:
        val = row.get(name)
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return None
        return float(val)

    return MarketQuote(bid=_f("bid"), ask=_f("ask"), last=_f("last"), close=_f("mark"))


def intrinsic_value(instrument: Instrument, underlying_price: float) -> float:
    """Per-share intrinsic value of an option at the given underlying price."""
    strike = float(instrument.strike or 0.0)
    if instrument.right is OptionRight.CALL:
        return max(underlying_price - strike, 0.0)
    return max(strike - underlying_price, 0.0)


def mark_option(
    chain: pd.DataFrame | None,
    instrument: Instrument,
    underlying_price: float | None,
    asof: date,
    *,
    r: float = DEFAULT_R,
    q: float = DEFAULT_Q,
) -> float | None:
    """Best available per-share mark for valuing an open option position.

    Prefers the chain mark/mid; falls back to a Black-Scholes price using the
    row's IV (or a 20% default) when the strike is no longer quoted.
    """
    row = find_chain_row(chain, instrument) if chain is not None else None
    if row is not None:
        mark = row.get("mark")
        if mark is not None and not pd.isna(mark):
            return float(mark)
        bid, ask = row.get("bid"), row.get("ask")
        if bid is not None and ask is not None and not pd.isna(bid) and not pd.isna(ask):
            return 0.5 * (float(bid) + float(ask))
    if underlying_price is None:
        return None
    iv = 0.20
    if row is not None and row.get("iv") is not None and not pd.isna(row.get("iv")):
        iv = float(row["iv"])
    t = max((instrument.expiry - asof).days, 0) / 365.0 if instrument.expiry else 0.0
    return bs_greeks(
        underlying_price,
        float(instrument.strike or 0.0),
        t,
        r,
        iv,
        as_option_type((instrument.right or OptionRight.CALL).value),
        q,
    ).price


def greeks_at_fill(
    instrument: Instrument,
    underlying_price: float,
    fill_price: float,
    asof: date,
    *,
    iv: float | None = None,
    r: float = DEFAULT_R,
    q: float = DEFAULT_Q,
) -> Greeks:
    """Compute Black-Scholes greeks for an option at the moment it is filled.

    If ``iv`` is not supplied it is backed out from ``fill_price`` via the IV
    solver, falling back to 20% if that fails.
    """
    t = max((instrument.expiry - asof).days, 1) / 365.0 if instrument.expiry else 1.0 / 365.0
    right = as_option_type((instrument.right or OptionRight.CALL).value)
    if iv is None or pd.isna(iv):
        solved = implied_volatility(
            fill_price,
            underlying_price,
            float(instrument.strike or 0.0),
            t,
            r,
            right,
            q,
        )
        iv = solved if (not pd.isna(solved) and solved > 0) else 0.20
    return bs_greeks(
        underlying_price,
        float(instrument.strike or 0.0),
        t,
        r,
        iv,
        right,
        q,
    )


def settle_expirations(
    portfolio: Portfolio,
    asof: date,
    underlying_prices: dict[str, float],
    timestamp: pd.Timestamp,
) -> list[Fill]:
    """Settle any option positions expiring on or before ``asof``.

    ITM options are cash-settled at intrinsic value (an assignment/exercise
    proxy); OTM options expire worthless. Returns the settlement fills applied so
    the caller can dispatch ``on_fill`` callbacks.
    """
    fills: list[Fill] = []
    expiring = [
        inst
        for inst in list(portfolio.positions)
        if inst.is_option and inst.expiry is not None and inst.expiry <= asof
    ]
    for inst in expiring:
        pos = portfolio.position(inst)
        if not pos.is_open:
            continue
        underlying = underlying_prices.get(inst.symbol)
        intrinsic = intrinsic_value(inst, underlying) if underlying is not None else 0.0
        # Close the position: opposite side, at intrinsic value.
        close_side = Side.SELL if pos.quantity > 0 else Side.BUY
        fill = Fill(
            instrument=inst,
            quantity=abs(pos.quantity),
            side=close_side,
            price=intrinsic,
            commission=0.0,
            timestamp=timestamp,
            tag="expiration",
        )
        portfolio.apply_fill(fill, extra={"event": "expiration"})
        fills.append(fill)
        if intrinsic > 0:
            logger.debug("Assigned/exercised ITM option %s at intrinsic %.2f", inst, intrinsic)
    return fills


def risk_warnings(portfolio: Portfolio) -> list[str]:
    """Return simplified Reg-T / undefined-risk warnings (never blocks trading).

    * Naked short calls have theoretically unlimited risk → flagged.
    * Short puts should be cash-secured; if portfolio cash is below the aggregate
      strike requirement we warn that the position is under-secured.
    """
    warnings: list[str] = []
    short_put_requirement = 0.0
    for inst, pos in portfolio.positions.items():
        if not inst.is_option or pos.quantity >= 0:
            continue
        if inst.right is OptionRight.CALL:
            warnings.append(f"Undefined-risk naked short call: {inst} (qty {pos.quantity})")
        elif inst.right is OptionRight.PUT:
            short_put_requirement += float(inst.strike or 0.0) * abs(pos.quantity) * inst.multiplier
    if short_put_requirement > portfolio.cash:
        warnings.append(
            f"Short puts not fully cash-secured: need ${short_put_requirement:,.0f}, "
            f"cash ${portfolio.cash:,.0f}"
        )
    return warnings
