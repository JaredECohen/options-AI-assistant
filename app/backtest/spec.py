"""Backtest request schema and strategy presets (contract: docs/BACKTESTING.md).

Every strategy is a list of 1-4 option legs, plus optional long stock for
covered calls. A preset is just a named default leg list, so the engine has one
code path and the page can edit any preset into a custom structure.
"""
from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_SPAN_DAYS = 5 * 366
MAX_LEGS = 4


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StrikeRule(_Strict):
    method: Literal["delta", "atm", "pct_otm", "offset"]
    value: float = 0.0
    from_leg: int | None = Field(default=None, ge=0, le=MAX_LEGS - 1)

    @model_validator(mode="after")
    def _ranges(self) -> StrikeRule:
        if self.method == "delta" and not 0.01 <= self.value <= 0.99:
            raise ValueError("delta must be between 0.01 and 0.99")
        if self.method == "pct_otm" and not -50 <= self.value <= 50:
            raise ValueError("pct_otm must be between -50 and 50")
        if self.method == "offset":
            if self.from_leg is None:
                raise ValueError("offset needs from_leg")
            if not -500 <= self.value <= 500:
                raise ValueError("offset must be between -500 and 500 dollars")
        return self


class LegSpec(_Strict):
    right: Literal["call", "put"]
    side: Literal["buy", "sell"]
    ratio: int = Field(default=1, ge=1, le=10)
    strike: StrikeRule


class StockSpec(_Strict):
    shares_per_contract: int = Field(default=100, ge=1, le=1000)


class EntryRules(_Strict):
    dte_target: int = Field(default=45, ge=0, le=365)
    dte_min: int = Field(default=30, ge=0, le=365)
    dte_max: int = Field(default=60, ge=0, le=365)
    expirations: Literal["any", "fridays", "monthly"] = "any"
    frequency: Literal["daily", "weekly", "monthly", "when_flat"] = "weekly"
    weekday: int = Field(default=0, ge=0, le=4)
    max_open: int = Field(default=3, ge=1, le=20)
    min_iv: float | None = Field(default=None, ge=0, le=5)
    max_iv: float | None = Field(default=None, ge=0, le=5)
    min_credit: float | None = Field(default=None, ge=0, le=1000)
    delta_tolerance: float = Field(default=0.05, gt=0, le=0.5)

    @model_validator(mode="after")
    def _order(self) -> EntryRules:
        if not self.dte_min <= self.dte_target <= self.dte_max:
            raise ValueError("DTE must satisfy dte_min <= dte_target <= dte_max")
        if self.min_iv is not None and self.max_iv is not None and self.min_iv > self.max_iv:
            raise ValueError("min_iv must not exceed max_iv")
        return self


class ExitRules(_Strict):
    profit_target_pct: float | None = Field(default=50, gt=0, le=1000)
    stop_loss_pct: float | None = Field(default=200, gt=0, le=1000)
    exit_dte: int | None = Field(default=21, ge=0, le=365)
    max_hold_days: int | None = Field(default=None, ge=1, le=730)


class CostModel(_Strict):
    commission_per_contract: float = Field(default=0.65, ge=0, le=50)
    fill: Literal["conservative", "mid", "spread_fraction"] = "conservative"
    spread_fraction: float = Field(default=0.25, ge=0, le=1)
    assignment_fee: float = Field(default=0.0, ge=0, le=100)


class Sizing(_Strict):
    capital: float = Field(default=100_000, ge=1_000, le=100_000_000)
    mode: Literal["fixed", "risk_pct"] = "fixed"
    contracts: int = Field(default=1, ge=1, le=100)
    risk_pct: float = Field(default=5, gt=0, le=100)


PresetKey = Literal["short_put", "cash_secured_put", "covered_call", "bull_put_spread", "bear_call_spread",
                    "bull_call_spread", "bear_put_spread", "iron_condor", "iron_butterfly", "short_strangle",
                    "short_straddle", "long_straddle", "long_strangle", "custom"]


class BacktestRequest(_Strict):
    underlying: str = Field(pattern=r"^[A-Za-z.]{1,10}$")
    start: dt.date
    end: dt.date
    strategy: PresetKey = "short_put"
    legs: list[LegSpec] = Field(default_factory=list, max_length=MAX_LEGS)
    stock: StockSpec | None = None
    entry: EntryRules = Field(default_factory=EntryRules)
    exit: ExitRules = Field(default_factory=ExitRules)
    costs: CostModel = Field(default_factory=CostModel)
    sizing: Sizing = Field(default_factory=Sizing)

    @model_validator(mode="after")
    def _check(self) -> BacktestRequest:
        self.underlying = self.underlying.upper()
        if self.end <= self.start:
            raise ValueError("end must be after start")
        if (self.end - self.start).days > MAX_SPAN_DAYS:
            raise ValueError("a backtest may span at most 5 years")
        if self.end >= dt.date.today():
            raise ValueError("end must be before today (history is end-of-day)")
        if not self.legs:
            if self.strategy == "custom":
                raise ValueError("a custom strategy needs at least one leg")
            preset = PRESETS[self.strategy]
            self.legs = [LegSpec.model_validate(leg) for leg in preset["legs"]]
            if self.stock is None and preset["stock"] is not None:
                self.stock = StockSpec.model_validate(preset["stock"])
        for i, leg in enumerate(self.legs):
            if leg.strike.method == "offset" and (leg.strike.from_leg is None or leg.strike.from_leg >= i):
                raise ValueError(f"leg {i + 1}: an offset must reference an earlier leg")
        return self


def _leg(right, side, method, value=0.0, from_leg=None, ratio=1) -> dict:
    strike = {"method": method, "value": value}
    if from_leg is not None:
        strike["from_leg"] = from_leg
    return {"right": right, "side": side, "ratio": ratio, "strike": strike}


_CREDIT_EXIT = {"profit_target_pct": 50, "stop_loss_pct": 200, "exit_dte": 21, "max_hold_days": None}
_DEBIT_EXIT = {"profit_target_pct": 100, "stop_loss_pct": 50, "exit_dte": 21, "max_hold_days": None}
_HOLD_EXIT = {"profit_target_pct": None, "stop_loss_pct": None, "exit_dte": None, "max_hold_days": None}

PRESETS: dict[str, dict] = {
    "short_put": {"label": "Short put", "kind": "credit",
                  "description": "Sell one out-of-the-money put. Undefined risk; margined at a Reg-T estimate.",
                  "legs": [_leg("put", "sell", "delta", 0.30)], "stock": None, "exit": _CREDIT_EXIT},
    "cash_secured_put": {"label": "Cash-secured put", "kind": "credit",
                         "description": "Short put with the full strike value held as collateral.",
                         "legs": [_leg("put", "sell", "delta", 0.30)], "stock": None, "exit": _HOLD_EXIT},
    "covered_call": {"label": "Covered call", "kind": "credit",
                     "description": "Own 100 shares per contract and sell an out-of-the-money call against them.",
                     "legs": [_leg("call", "sell", "delta", 0.30)], "stock": {"shares_per_contract": 100},
                     "exit": _HOLD_EXIT},
    "bull_put_spread": {"label": "Bull put spread", "kind": "credit",
                        "description": "Sell a put, buy a lower put. Defined risk = width - credit.",
                        "legs": [_leg("put", "sell", "delta", 0.30), _leg("put", "buy", "offset", -5, 0)],
                        "stock": None, "exit": _CREDIT_EXIT},
    "bear_call_spread": {"label": "Bear call spread", "kind": "credit",
                         "description": "Sell a call, buy a higher call. Defined risk = width - credit.",
                         "legs": [_leg("call", "sell", "delta", 0.30), _leg("call", "buy", "offset", 5, 0)],
                         "stock": None, "exit": _CREDIT_EXIT},
    "bull_call_spread": {"label": "Bull call debit spread", "kind": "debit",
                         "description": "Buy a call, sell a higher call. Max loss = debit paid.",
                         "legs": [_leg("call", "buy", "delta", 0.50), _leg("call", "sell", "offset", 10, 0)],
                         "stock": None, "exit": _DEBIT_EXIT},
    "bear_put_spread": {"label": "Bear put debit spread", "kind": "debit",
                        "description": "Buy a put, sell a lower put. Max loss = debit paid.",
                        "legs": [_leg("put", "buy", "delta", 0.50), _leg("put", "sell", "offset", -10, 0)],
                        "stock": None, "exit": _DEBIT_EXIT},
    "iron_condor": {"label": "Iron condor", "kind": "credit",
                    "description": "Bull put spread plus bear call spread around the price. Defined risk.",
                    "legs": [_leg("put", "sell", "delta", 0.16), _leg("put", "buy", "offset", -5, 0),
                             _leg("call", "sell", "delta", 0.16), _leg("call", "buy", "offset", 5, 2)],
                    "stock": None, "exit": _CREDIT_EXIT},
    "iron_butterfly": {"label": "Iron butterfly", "kind": "credit",
                       "description": "Sell the at-the-money straddle and buy wings. Defined risk.",
                       "legs": [_leg("put", "sell", "atm"), _leg("put", "buy", "offset", -10, 0),
                                _leg("call", "sell", "atm"), _leg("call", "buy", "offset", 10, 2)],
                       "stock": None, "exit": {**_CREDIT_EXIT, "profit_target_pct": 25}},
    "short_strangle": {"label": "Short strangle", "kind": "credit",
                       "description": "Sell an out-of-the-money put and call. Undefined risk.",
                       "legs": [_leg("put", "sell", "delta", 0.16), _leg("call", "sell", "delta", 0.16)],
                       "stock": None, "exit": _CREDIT_EXIT},
    "short_straddle": {"label": "Short straddle", "kind": "credit",
                       "description": "Sell the at-the-money put and call. Undefined risk.",
                       "legs": [_leg("put", "sell", "atm"), _leg("call", "sell", "atm")],
                       "stock": None, "exit": {**_CREDIT_EXIT, "profit_target_pct": 25}},
    "long_straddle": {"label": "Long straddle", "kind": "debit",
                      "description": "Buy the at-the-money put and call; profits from a large move either way.",
                      "legs": [_leg("put", "buy", "atm"), _leg("call", "buy", "atm")],
                      "stock": None, "exit": _DEBIT_EXIT},
    "long_strangle": {"label": "Long strangle", "kind": "debit",
                      "description": "Buy an out-of-the-money put and call.",
                      "legs": [_leg("put", "buy", "delta", 0.25), _leg("call", "buy", "delta", 0.25)],
                      "stock": None, "exit": _DEBIT_EXIT},
    "custom": {"label": "Custom", "kind": "either",
               "description": "Up to four legs; strikes by delta, at the money, % out of the money, or a $ offset.",
               "legs": [_leg("put", "sell", "delta", 0.30)], "stock": None, "exit": _CREDIT_EXIT},
}


def default_request(today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    end = today - dt.timedelta(days=1)
    return {"underlying": "SPY", "start": (end - dt.timedelta(days=365)).isoformat(), "end": end.isoformat(),
            "strategy": "short_put", "legs": PRESETS["short_put"]["legs"], "stock": None,
            "entry": EntryRules().model_dump(), "exit": PRESETS["short_put"]["exit"],
            "costs": CostModel().model_dump(), "sizing": Sizing().model_dump()}
