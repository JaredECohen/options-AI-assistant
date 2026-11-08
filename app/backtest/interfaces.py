"""Consumer-side contract for backtesting (P7).

The shared JaredECohen/backtesting-engine repository was NOT found (gh: "Could
not resolve to a Repository", 2026-11-08) and no licensed historical options
data is available, so genuine end-to-end backtesting is BLOCKED. This module
defines what OptionsCoach will send to and expect from an engine, so the
integration is a configuration change once both exist. Nothing here produces
economic evidence; test doubles are labelled as such.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Literal, Protocol

StrategyKind = Literal["short_put", "cash_secured_put", "covered_call", "long_straddle", "short_strangle",
                       "vertical_spread", "iron_condor"]
PHASE_1 = ("short_put", "cash_secured_put", "covered_call")


@dataclass(frozen=True)
class EntryRule:
    target_dte: int = 45
    dte_tolerance: int = 10
    target_abs_delta: float = 0.30
    delta_tolerance: float = 0.05


@dataclass(frozen=True)
class ExitRule:
    profit_target_pct: float | None = 0.50  # of credit received
    stop_loss_multiple: float | None = 2.0  # x credit
    exit_dte: int | None = 21


@dataclass(frozen=True)
class Costs:
    commission_per_contract: float = 0.65
    assignment_fee: float = 0.0
    fill: Literal["conservative", "mid"] = "conservative"  # sells at bid, buys at ask
    slippage_per_share: float = 0.0


@dataclass(frozen=True)
class StrategySpec:
    kind: StrategyKind
    underlying: str
    start: dt.date
    end: dt.date
    entry: EntryRule = field(default_factory=EntryRule)
    exit: ExitRule = field(default_factory=ExitRule)
    costs: Costs = field(default_factory=Costs)
    contracts: int = 1
    capital: float = 100_000.0
    seed: int = 0  # reproducibility for any engine-side randomness

    def validate(self) -> None:
        if self.end <= self.start:
            raise ValueError("end must be after start")
        if self.kind not in PHASE_1:
            raise ValueError(f"{self.kind} is phase 2; phase 1 is {PHASE_1}")


@dataclass(frozen=True)
class EodOptionRow:
    date: dt.date
    expiration: dt.date
    strike: float
    option_type: str
    bid: float | None
    ask: float | None
    delta: float | None
    underlying_close: float


class HistoricalOptionsSource(Protocol):
    """Point-in-time EOD chains. Must never return rows dated after `date`."""

    def chain_on(self, underlying: str, date: dt.date) -> list[EodOptionRow]: ...

    def trading_days(self, start: dt.date, end: dt.date) -> list[dt.date]: ...


@dataclass
class Trade:
    entry_date: dt.date
    exit_date: dt.date
    strike: float
    expiration: dt.date
    entry_price: float
    exit_price: float
    exit_reason: str
    pnl: float


@dataclass
class BacktestResult:
    spec: StrategySpec
    trades: list[Trade]
    daily_equity: list[tuple[dt.date, float]]
    metrics: dict
    engine: str
    data_source: str
    synthetic: bool


class BacktestEngine(Protocol):
    name: str

    def run(self, spec: StrategySpec, data: HistoricalOptionsSource) -> BacktestResult: ...
