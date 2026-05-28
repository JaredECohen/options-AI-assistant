"""Portfolio accounting tests: cash, P&L, shorts, flips, expiry."""

from __future__ import annotations

import pandas as pd
import pytest

from backtesting_engine.core.portfolio import Portfolio
from backtesting_engine.core.types import Fill, Instrument, OptionRight, Side

TS = pd.Timestamp("2020-01-02")
SPY = Instrument.equity("SPY")


def _fill(
    side: Side, qty: float, price: float, commission: float = 0.0, inst: Instrument = SPY
) -> Fill:
    return Fill(inst, qty, side, price, commission, TS)


def test_buy_then_sell_realizes_pnl() -> None:
    p = Portfolio(starting_cash=100_000)
    p.apply_fill(_fill(Side.BUY, 100, 50.0))
    assert p.cash == pytest.approx(95_000)
    assert p.position(SPY).quantity == 100
    realized = p.apply_fill(_fill(Side.SELL, 100, 55.0))
    assert realized == pytest.approx(500.0)
    assert p.realized_pnl == pytest.approx(500.0)
    assert p.cash == pytest.approx(100_500)
    assert not p.position(SPY).is_open


def test_average_price_on_add() -> None:
    p = Portfolio()
    p.apply_fill(_fill(Side.BUY, 100, 10.0))
    p.apply_fill(_fill(Side.BUY, 100, 20.0))
    assert p.position(SPY).avg_price == pytest.approx(15.0)


def test_short_then_cover() -> None:
    p = Portfolio()
    p.apply_fill(_fill(Side.SELL, 100, 50.0))
    assert p.position(SPY).quantity == -100
    assert p.cash == pytest.approx(100_000 + 5_000)
    realized = p.apply_fill(_fill(Side.BUY, 100, 45.0))
    assert realized == pytest.approx(500.0)  # shorted high, covered low


def test_flip_long_to_short() -> None:
    p = Portfolio()
    p.apply_fill(_fill(Side.BUY, 100, 10.0))
    realized = p.apply_fill(_fill(Side.SELL, 150, 12.0))
    assert realized == pytest.approx(200.0)  # closed 100 @ +2
    pos = p.position(SPY)
    assert pos.quantity == -50
    assert pos.avg_price == pytest.approx(12.0)


def test_commission_reduces_cash() -> None:
    p = Portfolio()
    p.apply_fill(_fill(Side.BUY, 100, 10.0, commission=1.0))
    assert p.cash == pytest.approx(100_000 - 1_000 - 1.0)


def test_option_multiplier_in_cash_flow() -> None:
    opt = Instrument.option("SPY", pd.Timestamp("2025-01-17").date(), 400.0, OptionRight.PUT)
    p = Portfolio()
    p.apply_fill(_fill(Side.SELL, 1, 3.0, inst=opt))
    assert p.cash == pytest.approx(100_000 + 300.0)  # 1 contract * 100 * 3.0


def test_equity_curve_and_trade_log() -> None:
    p = Portfolio()
    p.apply_fill(_fill(Side.BUY, 100, 10.0))
    p.record_equity(TS, {SPY: 11.0})
    curve = p.equity_curve()
    assert len(curve) == 1
    assert curve.iloc[0] == pytest.approx(100_000 - 1_000 + 1_100)
    log = p.trade_log()
    assert len(log) == 1
    assert {"symbol", "side", "realized_pnl"}.issubset(log.columns)


def test_unrealized_pnl() -> None:
    p = Portfolio()
    p.apply_fill(_fill(Side.BUY, 100, 10.0))
    assert p.position(SPY).unrealized_pnl(12.0) == pytest.approx(200.0)
