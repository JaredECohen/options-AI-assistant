"""End-to-end engine tests for the equity and options paths."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from backtesting_engine import (
    Backtest,
    Instrument,
    OptionRight,
    Order,
    Side,
    StandardEquityCostModel,
    StandardOptionsCostModel,
    Strategy,
    SyntheticOptionsAdapter,
)
from backtesting_engine.core.portfolio import Portfolio
from backtesting_engine.core.result import Result
from backtesting_engine.core.types import Fill
from backtesting_engine.engines import options as opt_engine


class BuyAndHold(Strategy):
    def on_start(self, portfolio) -> None:  # type: ignore[no-untyped-def]
        self.bought = False

    def on_bar(self, bar, portfolio):  # type: ignore[no-untyped-def]
        if not self.bought and bar.price("SPY") is not None:
            self.bought = True
            return [Order(Instrument.equity("SPY"), 100, Side.BUY)]
        return []


def test_equity_backtest_produces_result() -> None:
    bt = Backtest(
        BuyAndHold(),
        SyntheticOptionsAdapter(start_price=300, seed=5),
        StandardEquityCostModel(),
        StandardOptionsCostModel(),
        equity_symbols=["SPY"],
    )
    result = bt.run(date(2020, 1, 1), date(2020, 12, 31))
    assert isinstance(result, Result)
    assert not result.equity_curve.empty
    assert len(result.trade_log) == 1
    assert set(result.metrics).issuperset({"sharpe", "max_drawdown", "total_return"})
    assert "Final equity" in result.summary()


def test_no_symbols_raises() -> None:
    bt = Backtest(
        BuyAndHold(),
        SyntheticOptionsAdapter(),
        StandardEquityCostModel(),
        StandardOptionsCostModel(),
    )
    with pytest.raises(ValueError):
        bt.run(date(2020, 1, 1), date(2020, 2, 1))


class SellOnePut(Strategy):
    """Sell exactly one ~30 DTE ATM put on the first bar, then hold to expiry."""

    def on_start(self, portfolio) -> None:  # type: ignore[no-untyped-def]
        self.done = False

    def on_bar(self, bar, portfolio):  # type: ignore[no-untyped-def]
        if self.done:
            return []
        chain = bar.chain("SPY")
        if chain is None or chain.empty:
            return []
        puts = chain[chain["type"] == "put"].copy()
        puts["dte"] = (pd.to_datetime(puts["expiry"]) - bar.timestamp).dt.days
        cohort = puts[puts["dte"] == puts["dte"].min()]
        spot = bar.price("SPY")
        row = cohort.iloc[(cohort["strike"] - spot).abs().argmin()]
        self.done = True
        inst = Instrument.option("SPY", row["expiry"], float(row["strike"]), OptionRight.PUT)
        return [Order(inst, 1, Side.SELL, tag="put")]


def test_options_backtest_settles_at_expiry() -> None:
    bt = Backtest(
        SellOnePut(),
        SyntheticOptionsAdapter(start_price=400, seed=11),
        StandardEquityCostModel(),
        StandardOptionsCostModel(),
        equity_symbols=["SPY"],
        option_symbols=["SPY"],
    )
    result = bt.run(date(2020, 1, 1), date(2020, 3, 31))
    # One opening sell + one expiration close.
    assert len(result.trade_log) == 2
    assert "expiration" in set(result.trade_log["tag"])
    # Greeks captured at fill on the opening trade.
    open_row = result.trade_log.iloc[0]
    assert "delta" in result.trade_log.columns
    assert open_row["delta"] < 0  # short... the logged greek is the option's own delta (put<0)


def test_expiration_itm_vs_otm_settlement() -> None:
    """Directly exercise settle_expirations for ITM (assigned) and OTM (worthless)."""
    p = Portfolio()
    ts = pd.Timestamp("2020-02-01")
    expiry = date(2020, 2, 1)
    itm = Instrument.option("SPY", expiry, 110.0, OptionRight.PUT)  # spot 100 -> ITM put
    otm = Instrument.option("SPY", expiry, 90.0, OptionRight.PUT)  # spot 100 -> OTM put
    p.apply_fill(Fill(itm, 1, Side.SELL, 5.0, 0.0, ts))
    p.apply_fill(Fill(otm, 1, Side.SELL, 1.0, 0.0, ts))

    fills = opt_engine.settle_expirations(p, expiry, {"SPY": 100.0}, ts)
    assert len(fills) == 2
    # ITM put intrinsic = 10; short sold at 5 -> realized loss of (5-10)*100 = -500
    # OTM put intrinsic = 0; short sold at 1 -> realized gain of 100
    assert p.realized_pnl == pytest.approx(-500.0 + 100.0)
    assert not p.positions  # both closed


def test_risk_warnings_flag_naked_call() -> None:
    p = Portfolio(starting_cash=1_000)
    ts = pd.Timestamp("2020-02-01")
    call = Instrument.option("SPY", date(2020, 3, 1), 100.0, OptionRight.CALL)
    put = Instrument.option("SPY", date(2020, 3, 1), 100.0, OptionRight.PUT)
    p.apply_fill(Fill(call, 1, Side.SELL, 2.0, 0.0, ts))
    p.apply_fill(Fill(put, 1, Side.SELL, 2.0, 0.0, ts))
    warnings = opt_engine.risk_warnings(p)
    assert any("naked short call" in w for w in warnings)
    assert any("cash-secured" in w for w in warnings)
