"""End-to-end example: a rolling 30-DTE ~30-delta short put on SPY.

Run it directly::

    python examples/short_put_spy.py

It uses the deterministic :class:`SyntheticOptionsAdapter` so it runs offline with
no credentials. Swap in ``CachedDataSource(FMPEquityAdapter())`` for equity bars
and ``CachedDataSource(AlphaVantageOptionsAdapter())`` for live chains.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

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


class ShortPut(Strategy):
    """Sell one ~30-delta put around 30 DTE whenever flat; hold to expiry."""

    def __init__(self, symbol: str = "SPY", target_delta: float = -0.30, dte: int = 30) -> None:
        self.symbol = symbol
        self.target_delta = target_delta
        self.dte = dte

    def on_bar(self, bar, portfolio):  # type: ignore[no-untyped-def]
        # Already holding an option? Do nothing (expiration will close it).
        if any(inst.is_option for inst in portfolio.positions):
            return []
        chain = bar.chain(self.symbol)
        if chain is None or chain.empty:
            return []

        puts = chain[chain["type"] == "put"].copy()
        if puts.empty:
            return []
        # Pick the expiry closest to the target DTE.
        puts["dte"] = (pd.to_datetime(puts["expiry"]) - bar.timestamp).dt.days
        target_expiry = puts.iloc[(puts["dte"] - self.dte).abs().argmin()]["expiry"]
        cohort = puts[puts["expiry"] == target_expiry]
        # Pick the strike whose delta is closest to the target.
        row = cohort.iloc[(cohort["delta"] - self.target_delta).abs().argmin()]
        instrument = Instrument.option(
            self.symbol, row["expiry"], float(row["strike"]), OptionRight.PUT
        )
        return [Order(instrument, quantity=1, side=Side.SELL, tag="short_put")]


def main() -> None:
    source = SyntheticOptionsAdapter(start_price=400.0, seed=7)
    bt = Backtest(
        ShortPut(),
        source,
        StandardEquityCostModel(),
        StandardOptionsCostModel(),
        starting_cash=100_000.0,
        equity_symbols=["SPY"],
        option_symbols=["SPY"],
    )
    result = bt.run(date(2020, 1, 1), date(2021, 6, 30))
    print(result.summary())
    print(f"\nTrades executed: {len(result.trade_log)}")
    print(result.trade_log[["timestamp", "strike", "side", "price", "realized_pnl"]].head(10))


if __name__ == "__main__":
    main()
