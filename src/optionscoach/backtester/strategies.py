"""Pre-built option strategies for OptionsCoach.AI.

Each strategy is a :class:`backtesting_engine.Strategy` subclass parameterized by a
common config (``target_delta``, ``dte``, ``profit_target_pct``,
``stop_loss_multiple``, ``time_exit_dte``). They open a position when flat, then
manage it to a profit target, stop loss, or time-based exit — whichever comes
first — and let the engine settle anything held to expiration.

The exit logic measures P&L against the **net option credit** collected at entry
(a clean, broker-style "% of max profit" rule), which keeps all six strategies on
the same simple, testable footing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd
from backtesting_engine import Instrument, OptionRight, Order, Side, Strategy
from backtesting_engine.engines.options import find_chain_row


@dataclass
class _Leg:
    """One leg of an open position, with the side used to open it."""

    instrument: Instrument
    open_side: Side
    quantity: float


class OptionStrategy(Strategy):
    """Base class with strike selection and position management shared by all screens."""

    def __init__(
        self,
        symbol: str = "SPY",
        *,
        target_delta: float = 0.30,
        dte: int = 30,
        profit_target_pct: float = 0.50,
        stop_loss_multiple: float = 2.0,
        time_exit_dte: int = 7,
        quantity: int = 1,
    ) -> None:
        self.symbol = symbol
        self.target_delta = target_delta
        self.dte = dte
        self.profit_target_pct = profit_target_pct
        self.stop_loss_multiple = stop_loss_multiple
        self.time_exit_dte = time_exit_dte
        self.quantity = quantity

    # -- lifecycle --------------------------------------------------------
    def on_start(self, portfolio) -> None:  # type: ignore[no-untyped-def]
        self._legs: list[_Leg] = []
        self._entry_credit: float = 0.0
        self._expiry: date | None = None

    # -- helpers ----------------------------------------------------------
    def _expiry_cohort(self, chain: pd.DataFrame, ts: pd.Timestamp) -> pd.DataFrame:
        """Rows whose expiry is closest to the configured DTE."""
        df = chain.copy()
        df["_dte"] = (pd.to_datetime(df["expiry"]) - ts).dt.days
        df = df[df["_dte"] > 0]
        if df.empty:
            return df
        target = df.iloc[(df["_dte"] - self.dte).abs().argmin()]["expiry"]
        return df[df["expiry"] == target]

    @staticmethod
    def _pick_by_delta(
        cohort: pd.DataFrame, right: OptionRight, target_delta: float
    ) -> pd.Series | None:
        sub = cohort[cohort["type"] == right.value]
        if sub.empty:
            return None
        return sub.iloc[(sub["delta"] - target_delta).abs().argmin()]

    def _instrument(self, row: pd.Series, right: OptionRight) -> Instrument:
        return Instrument.option(self.symbol, row["expiry"], float(row["strike"]), right)

    def _mark(self, chain: pd.DataFrame, instrument: Instrument) -> float:
        row = find_chain_row(chain, instrument)
        if row is None or pd.isna(row.get("mark")):
            return 0.0
        return float(row["mark"])

    # -- to be implemented by subclasses ---------------------------------
    def select_legs(self, cohort: pd.DataFrame, spot: float) -> list[tuple[Instrument, Side]]:
        """Return the option legs (instrument, side) to open for this strategy."""
        raise NotImplementedError

    def equity_leg(self, spot: float) -> tuple[Instrument, Side, float] | None:
        """Optional equity leg (e.g. the long stock in a covered call)."""
        return None

    # -- main hook --------------------------------------------------------
    def on_bar(self, bar, portfolio):  # type: ignore[no-untyped-def]
        chain = bar.chain(self.symbol)
        spot = bar.price(self.symbol)
        if chain is None or chain.empty or spot is None:
            return []
        if self._legs:
            return self._manage(bar, chain, portfolio)
        return self._open(bar, chain, spot)

    def _open(self, bar, chain: pd.DataFrame, spot: float):  # type: ignore[no-untyped-def]
        cohort = self._expiry_cohort(chain, bar.timestamp)
        if cohort.empty:
            return []
        legs = self.select_legs(cohort, spot)
        if not legs:
            return []
        orders: list[Order] = []
        credit = 0.0
        for instrument, side in legs:
            orders.append(Order(instrument, self.quantity, side, tag=type(self).__name__))
            mark = self._mark(chain, instrument)
            sign = 1.0 if side is Side.SELL else -1.0  # credit for shorts, debit for longs
            credit += sign * mark * self.quantity * instrument.multiplier
            self._legs.append(_Leg(instrument, side, self.quantity))
            self._expiry = instrument.expiry

        equity = self.equity_leg(spot)
        if equity is not None:
            inst, side, qty = equity
            orders.append(Order(inst, qty, side, tag=type(self).__name__))
            self._legs.append(_Leg(inst, side, qty))

        self._entry_credit = credit
        return orders

    def _cost_to_close(self, chain: pd.DataFrame) -> float:
        """Current net cash needed to flatten the option legs (positive == costs money)."""
        cost = 0.0
        for leg in self._legs:
            if not leg.instrument.is_option:
                continue
            mark = self._mark(chain, leg.instrument)
            # Closing a short means buying back (cost); closing a long means selling (credit).
            sign = 1.0 if leg.open_side is Side.SELL else -1.0
            cost += sign * mark * leg.quantity * leg.instrument.multiplier
        return cost

    def _manage(self, bar, chain: pd.DataFrame, portfolio):  # type: ignore[no-untyped-def]
        # If the engine already settled our options at expiration, reset and re-arm.
        still_open = any(
            leg.instrument.is_option and leg.instrument in portfolio.positions for leg in self._legs
        )
        if not still_open:
            self._reset()
            return []

        dte_left = (self._expiry - bar.timestamp.date()).days if self._expiry else 0
        cost_to_close = self._cost_to_close(chain)
        pnl = self._entry_credit - cost_to_close

        hit_profit = self._entry_credit > 0 and pnl >= self.profit_target_pct * self._entry_credit
        hit_stop = self._entry_credit > 0 and pnl <= -self.stop_loss_multiple * self._entry_credit
        hit_time = dte_left <= self.time_exit_dte

        if hit_profit or hit_stop or hit_time:
            return self._close_orders()
        return []

    def _close_orders(self) -> list[Order]:
        orders: list[Order] = []
        exit_tag = f"{type(self).__name__}_exit"
        for leg in self._legs:
            close_side = Side.BUY if leg.open_side is Side.SELL else Side.SELL
            orders.append(Order(leg.instrument, leg.quantity, close_side, tag=exit_tag))
        self._reset()
        return orders

    def _reset(self) -> None:
        self._legs = []
        self._entry_credit = 0.0
        self._expiry = None


class ShortPut(OptionStrategy):
    """Sell a single OTM put at the target delta (negative)."""

    def select_legs(self, cohort, spot):  # type: ignore[no-untyped-def]
        row = self._pick_by_delta(cohort, OptionRight.PUT, -abs(self.target_delta))
        if row is None:
            return []
        return [(self._instrument(row, OptionRight.PUT), Side.SELL)]


class CashSecuredPut(ShortPut):
    """Identical leg to a short put; semantically the put is cash-secured.

    The engine's simplified Reg-T check warns if portfolio cash is insufficient to
    secure the strike, so this models the cash-secured constraint.
    """


class CoveredCall(OptionStrategy):
    """Long 100 shares of the underlying plus a short OTM call at the target delta."""

    def select_legs(self, cohort, spot):  # type: ignore[no-untyped-def]
        row = self._pick_by_delta(cohort, OptionRight.CALL, abs(self.target_delta))
        if row is None:
            return []
        return [(self._instrument(row, OptionRight.CALL), Side.SELL)]

    def equity_leg(self, spot):  # type: ignore[no-untyped-def]
        return (Instrument.equity(self.symbol), Side.BUY, 100.0 * self.quantity)


class ShortStraddle(OptionStrategy):
    """Sell an ATM call and an ATM put at the same strike (delta ~ 0.50)."""

    def select_legs(self, cohort, spot):  # type: ignore[no-untyped-def]
        call = self._pick_by_delta(cohort, OptionRight.CALL, 0.50)
        put = self._pick_by_delta(cohort, OptionRight.PUT, -0.50)
        if call is None or put is None:
            return []
        return [
            (self._instrument(call, OptionRight.CALL), Side.SELL),
            (self._instrument(put, OptionRight.PUT), Side.SELL),
        ]


class ShortStrangle(OptionStrategy):
    """Sell an OTM call and an OTM put at the target delta on each side."""

    def select_legs(self, cohort, spot):  # type: ignore[no-untyped-def]
        call = self._pick_by_delta(cohort, OptionRight.CALL, abs(self.target_delta))
        put = self._pick_by_delta(cohort, OptionRight.PUT, -abs(self.target_delta))
        if call is None or put is None:
            return []
        return [
            (self._instrument(call, OptionRight.CALL), Side.SELL),
            (self._instrument(put, OptionRight.PUT), Side.SELL),
        ]


class IronCondor(OptionStrategy):
    """Short strangle at the target delta with long wings at roughly half the delta."""

    def __init__(self, *args, wing_delta: float | None = None, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self.wing_delta = wing_delta if wing_delta is not None else abs(self.target_delta) * 0.5

    def select_legs(self, cohort, spot):  # type: ignore[no-untyped-def]
        short_call = self._pick_by_delta(cohort, OptionRight.CALL, abs(self.target_delta))
        short_put = self._pick_by_delta(cohort, OptionRight.PUT, -abs(self.target_delta))
        long_call = self._pick_by_delta(cohort, OptionRight.CALL, self.wing_delta)
        long_put = self._pick_by_delta(cohort, OptionRight.PUT, -self.wing_delta)
        legs = [short_call, short_put, long_call, long_put]
        if any(leg is None for leg in legs):
            return []
        return [
            (self._instrument(short_call, OptionRight.CALL), Side.SELL),
            (self._instrument(short_put, OptionRight.PUT), Side.SELL),
            (self._instrument(long_call, OptionRight.CALL), Side.BUY),
            (self._instrument(long_put, OptionRight.PUT), Side.BUY),
        ]
