"""The :class:`Backtest` orchestrator: the per-bar event loop.

Wires together a :class:`Strategy`, a :class:`DataSource`, a :class:`CostModel`
and a :class:`Portfolio`, then steps day-by-day:

1. Build the :class:`MarketSlice` for the day (equity bars + option chains).
2. Settle any option expirations.
3. Ask the strategy for orders and execute them at conservative fills.
4. Mark the portfolio to market and record the equity-curve point.

EOD only for v0 — one bar per trading day.
"""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from backtesting_engine.core.portfolio import Portfolio
from backtesting_engine.core.protocols import CostModel, DataSource
from backtesting_engine.core.result import Result
from backtesting_engine.core.strategy import Strategy
from backtesting_engine.core.types import (
    Bar,
    Fill,
    Instrument,
    MarketQuote,
    MarketSlice,
)
from backtesting_engine.engines import options as opt_engine
from backtesting_engine.engines.equity import quote_from_bar

logger = logging.getLogger("backtesting_engine.backtest")


class Backtest:
    """Run a single strategy over a date range against a data source.

    Parameters
    ----------
    strategy:
        The strategy to run.
    data_source:
        Provides equity bars and (optionally) option chains.
    equity_cost_model, options_cost_model:
        Cost models applied to equity and option fills respectively.
    starting_cash:
        Initial portfolio cash.
    equity_symbols:
        Equity symbols to load bars for (the universe). The first symbol is also
        used as the underlying for option chains when ``option_symbols`` is empty.
    option_symbols:
        Underlyings to load option chains for. May be empty for equity-only runs.
    """

    def __init__(
        self,
        strategy: Strategy,
        data_source: DataSource,
        equity_cost_model: CostModel,
        options_cost_model: CostModel,
        *,
        starting_cash: float = 100_000.0,
        equity_symbols: list[str] | None = None,
        option_symbols: list[str] | None = None,
    ) -> None:
        self.strategy = strategy
        self.data_source = data_source
        self.equity_cost_model = equity_cost_model
        self.options_cost_model = options_cost_model
        self.portfolio = Portfolio(starting_cash=starting_cash)
        self.equity_symbols = equity_symbols or []
        self.option_symbols = option_symbols or []

    # -- data loading -----------------------------------------------------
    def _load_bars(self, start: date, end: date) -> dict[str, pd.DataFrame]:
        bars: dict[str, pd.DataFrame] = {}
        for symbol in self.equity_symbols:
            df = self.data_source.get_bars(symbol, start, end)
            bars[symbol] = df
        return bars

    def _build_slice(
        self, ts: pd.Timestamp, bars_by_symbol: dict[str, pd.DataFrame]
    ) -> MarketSlice:
        slice_ = MarketSlice(timestamp=ts)
        for symbol, df in bars_by_symbol.items():
            if ts in df.index:
                row = df.loc[ts]
                slice_.bars[symbol] = Bar(
                    timestamp=ts,
                    symbol=symbol,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )
        for symbol in self.option_symbols:
            try:
                chain = self.data_source.get_chain(symbol, ts.date())
            except NotImplementedError:
                continue
            if chain is not None and not chain.empty:
                slice_.chains[symbol] = chain
        return slice_

    # -- execution --------------------------------------------------------
    def _quote_for(self, order_instrument: Instrument, slice_: MarketSlice) -> MarketQuote | None:
        if order_instrument.is_option:
            chain = slice_.chain(order_instrument.symbol)
            if chain is None:
                return None
            return opt_engine.quote_from_chain(chain, order_instrument)
        bar = slice_.bar(order_instrument.symbol)
        return quote_from_bar(bar) if bar is not None else None

    def _execute(self, slice_: MarketSlice) -> None:
        orders = self.strategy.on_bar(slice_, self.portfolio)
        for order in orders:
            quote = self._quote_for(order.instrument, slice_)
            if quote is None:
                logger.warning(
                    "No quote for %s on %s; skipping order", order.instrument, slice_.timestamp
                )
                continue
            if order.instrument.is_option:
                cost_model: CostModel = self.options_cost_model
            else:
                cost_model = self.equity_cost_model
            price = cost_model.compute_fill_price(order, quote)
            commission = cost_model.compute_commission(order)
            fill = Fill(
                instrument=order.instrument,
                quantity=order.quantity,
                side=order.side,
                price=price,
                commission=commission,
                timestamp=slice_.timestamp,
                tag=order.tag,
            )
            extra: dict[str, object] = {}
            if order.instrument.is_option:
                underlying = slice_.price(order.instrument.symbol)
                if underlying is not None:
                    row = opt_engine.find_chain_row(
                        slice_.chain(order.instrument.symbol), order.instrument
                    )
                    iv = (
                        float(row["iv"]) if row is not None and not pd.isna(row.get("iv")) else None
                    )
                    g = opt_engine.greeks_at_fill(
                        order.instrument, underlying, price, slice_.timestamp.date(), iv=iv
                    )
                    extra = {
                        "delta": g.delta,
                        "gamma": g.gamma,
                        "theta": g.theta,
                        "vega": g.vega,
                        "rho": g.rho,
                        "iv_at_fill": iv,
                    }
            self.portfolio.apply_fill(fill, extra=extra or None)
            self.strategy.on_fill(fill, self.portfolio)

    # -- marking ----------------------------------------------------------
    def _mark_prices(self, slice_: MarketSlice) -> dict[Instrument, float]:
        prices: dict[Instrument, float] = {}
        for inst, pos in self.portfolio.positions.items():
            if not pos.is_open:
                continue
            if inst.is_option:
                underlying = slice_.price(inst.symbol)
                mark = opt_engine.mark_option(
                    slice_.chain(inst.symbol), inst, underlying, slice_.timestamp.date()
                )
                if mark is not None:
                    prices[inst] = mark
            else:
                close = slice_.price(inst.symbol)
                if close is not None:
                    prices[inst] = close
        return prices

    # -- main loop --------------------------------------------------------
    def run(self, start: date, end: date) -> Result:
        """Run the backtest from ``start`` to ``end`` (inclusive) and return a Result."""
        bars_by_symbol = self._load_bars(start, end)
        if not bars_by_symbol:
            raise ValueError("no equity symbols configured; set equity_symbols")

        # The trading calendar is the union of all bar dates across symbols.
        all_dates: set[pd.Timestamp] = set()
        for df in bars_by_symbol.values():
            all_dates.update(df.index.tolist())
        calendar = sorted(all_dates)
        if not calendar:
            raise ValueError("data source returned no bars for the requested range")

        self.strategy.on_start(self.portfolio)
        for ts in calendar:
            slice_ = self._build_slice(ts, bars_by_symbol)
            underlying_prices = {s: b.close for s, b in slice_.bars.items()}

            # 1) Settle expirations before the strategy acts on the new day.
            for fill in opt_engine.settle_expirations(
                self.portfolio, ts.date(), underlying_prices, ts
            ):
                self.strategy.on_fill(fill, self.portfolio)

            # 2) Strategy orders + execution.
            self._execute(slice_)

            # 3) Surface (non-blocking) risk warnings.
            for warning in opt_engine.risk_warnings(self.portfolio):
                logger.info("RISK: %s", warning)

            # 4) Mark to market and record equity.
            self.portfolio.record_equity(ts, self._mark_prices(slice_))

        self.strategy.on_end(self.portfolio)
        return Result(
            equity_curve=self.portfolio.equity_curve(),
            trade_log=self.portfolio.trade_log(),
            starting_cash=self.portfolio.starting_cash,
        )
