"""Cost-model tests: conservative adverse fills + commissions."""

from __future__ import annotations

import pytest

from backtesting_engine.core.types import Instrument, MarketQuote, Order, OrderType, Side
from backtesting_engine.costs import StandardEquityCostModel, StandardOptionsCostModel


def test_equity_buy_fills_above_close() -> None:
    model = StandardEquityCostModel()
    order = Order(Instrument.equity("SPY"), 100, Side.BUY)
    quote = MarketQuote(close=400.0)
    assert model.compute_fill_price(order, quote) == pytest.approx(400.01)
    assert model.compute_commission(order) == pytest.approx(0.50)


def test_equity_sell_fills_below_close() -> None:
    model = StandardEquityCostModel()
    order = Order(Instrument.equity("SPY"), 100, Side.SELL)
    assert model.compute_fill_price(order, MarketQuote(close=400.0)) == pytest.approx(399.99)


def test_equity_marketable_limit_respected() -> None:
    model = StandardEquityCostModel()
    order = Order(Instrument.equity("SPY"), 100, Side.BUY, OrderType.LIMIT, limit_price=399.5)
    # adverse price 400.01 is worse than limit, so it fills at the limit.
    assert model.compute_fill_price(order, MarketQuote(close=400.0)) == pytest.approx(399.5)


def test_equity_requires_price() -> None:
    model = StandardEquityCostModel()
    order = Order(Instrument.equity("SPY"), 1, Side.BUY)
    with pytest.raises(ValueError):
        model.compute_fill_price(order, MarketQuote())


def _opt(side: Side) -> Order:
    inst = Instrument.option(
        "SPY",
        __import__("datetime").date(2025, 1, 17),
        400.0,
        __import__("backtesting_engine").OptionRight.PUT,
    )
    return Order(inst, 1, side)


def test_options_buy_at_ask_plus_tick_penny() -> None:
    model = StandardOptionsCostModel()
    quote = MarketQuote(bid=1.00, ask=1.10)  # < $3 -> penny tick
    assert model.compute_fill_price(_opt(Side.BUY), quote) == pytest.approx(1.11)


def test_options_sell_at_bid_minus_tick_nickel() -> None:
    model = StandardOptionsCostModel()
    quote = MarketQuote(bid=5.00, ask=5.20)  # >= $3 -> nickel tick
    assert model.compute_fill_price(_opt(Side.SELL), quote) == pytest.approx(4.95)


def test_options_commission() -> None:
    model = StandardOptionsCostModel()
    inst = _opt(Side.BUY).instrument
    assert model.compute_commission(Order(inst, 3, Side.BUY)) == pytest.approx(1.95)


def test_options_fallback_when_no_bid_ask() -> None:
    model = StandardOptionsCostModel()
    quote = MarketQuote(last=2.00)
    price = model.compute_fill_price(_opt(Side.BUY), quote)
    assert price > 2.00  # adverse of synthetic spread + tick
