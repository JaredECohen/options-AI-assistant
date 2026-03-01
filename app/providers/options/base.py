from __future__ import annotations

from dataclasses import dataclass
from typing import List, Protocol


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


class OptionsDataProvider(Protocol):
    name: str

    async def get_underlying_price(self, ticker: str) -> float:
        ...

    async def list_expirations(self, ticker: str) -> List[str]:
        ...

    async def get_chain(self, ticker: str, expiration: str) -> List[OptionQuote]:
        ...

    async def get_option_quote(
        self, ticker: str, expiration: str, strike: float, option_type: str
    ) -> OptionQuote:
        ...
