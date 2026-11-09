"""Options market-data provider contract.

Every quote carries enough provenance for a consumer to judge it: OCC contract
symbol, quote timestamp (ISO-8601 with offset), provider, the provider's data
status, multiplier and whether the deliverable is adjusted. Greeks and IV are
optional and must name their source; a provider that does not supply them
returns None rather than a fabricated value.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field
from typing import Literal, Protocol

DataStatus = Literal["live", "delayed", "eod", "synthetic"]
DATA_STATUS_LABELS: dict[str, str] = {
    "live": "Real-time",
    "delayed": "Delayed",
    "eod": "End of day",
    "synthetic": "Synthetic demo data — not market prices",
}


class ProviderUnavailable(RuntimeError):
    """The provider cannot serve requests (missing credentials, outage, quota)."""


class QuoteUnavailable(LookupError):
    """The specific instrument or quote does not exist or has no usable price."""


@dataclass
class Greeks:
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None  # per calendar day, per share
    vega: float | None = None  # per 1.00 (100 vol points) change, per share -- see source docs
    rho: float | None = None
    source: str = ""  # e.g. "tradier/orats", "oc-bsm"
    as_of: str | None = None


@dataclass
class OptionQuote:
    option_type: str
    strike: float
    expiration: str
    bid: float | None
    ask: float | None
    last: float | None
    mark: float | None
    symbol: str | None = None
    volume: int | None = None
    open_interest: int | None = None
    implied_volatility: float | None = None  # annualized decimal, provider-sourced
    iv_source: str | None = None
    greeks: Greeks | None = None
    quote_time: str | None = None  # ISO-8601 with UTC offset
    multiplier: int = 100
    adjusted: bool = False  # non-standard deliverable (corporate action)
    currency: str = "USD"
    extra: dict = field(default_factory=dict)

    def as_json(self) -> dict:
        data = asdict(self)
        data.pop("extra", None)
        return data


@dataclass(frozen=True)
class EodOption:
    """One contract's end-of-day close quote (history providers only)."""

    date: dt.date
    expiration: str  # YYYY-MM-DD
    strike: float
    option_type: str  # "call" | "put"
    bid: float | None
    ask: float | None
    underlying_price: float | None = None


class OptionsDataProvider(Protocol):
    name: str
    data_status: DataStatus

    async def get_underlying_price(self, ticker: str) -> float: ...

    async def list_expirations(self, ticker: str) -> list[str]: ...

    async def get_chain(self, ticker: str, expiration: str) -> list[OptionQuote]: ...

    async def get_option_quote(self, ticker: str, expiration: str, strike: float, option_type: str) -> OptionQuote: ...
