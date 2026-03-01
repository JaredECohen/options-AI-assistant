from __future__ import annotations

import datetime as dt
from typing import List

from .base import OptionQuote, OptionsDataProvider


class MockOptionsProvider:
    name = "mock"

    def _base_price(self, ticker: str) -> float:
        seed = sum(ord(c) for c in ticker.upper())
        return 80 + (seed % 60)

    async def get_underlying_price(self, ticker: str) -> float:
        return float(self._base_price(ticker))

    async def list_expirations(self, ticker: str) -> List[str]:
        today = dt.date.today()
        expirations = [today + dt.timedelta(days=7 * i) for i in range(1, 7)]
        return [d.isoformat() for d in expirations]

    def _make_premium(self, base: float, strike: float, option_type: str) -> float:
        intrinsic = max(base - strike, 0.0) if option_type == "call" else max(strike - base, 0.0)
        time_value = max(1.0, 0.08 * base)
        return round(intrinsic + time_value, 2)

    async def get_chain(self, ticker: str, expiration: str) -> List[OptionQuote]:
        base = self._base_price(ticker)
        strikes = [round(base * 0.5 + 5 * i, 2) for i in range(0, 21)]
        quotes: List[OptionQuote] = []
        for strike in strikes:
            for opt_type in ["call", "put"]:
                mark = self._make_premium(base, strike, opt_type)
                bid = round(mark - 0.1, 2)
                ask = round(mark + 0.1, 2)
                quotes.append(
                    OptionQuote(
                        option_type=opt_type,
                        strike=float(strike),
                        expiration=expiration,
                        bid=bid,
                        ask=ask,
                        last=mark,
                        mark=mark,
                        symbol=f"{ticker}-{expiration}-{strike}-{opt_type}",
                    )
                )
        return quotes

    async def get_option_quote(
        self, ticker: str, expiration: str, strike: float, option_type: str
    ) -> OptionQuote:
        chain = await self.get_chain(ticker, expiration)
        for opt in chain:
            if opt.option_type == option_type.lower() and abs(opt.strike - float(strike)) < 1e-6:
                return opt
        # If not in the prebuilt chain, synthesize a quote for the requested strike.
        base = self._base_price(ticker)
        mark = self._make_premium(base, float(strike), option_type.lower())
        bid = round(mark - 0.1, 2)
        ask = round(mark + 0.1, 2)
        return OptionQuote(
            option_type=option_type.lower(),
            strike=float(strike),
            expiration=expiration,
            bid=bid,
            ask=ask,
            last=mark,
            mark=mark,
            symbol=f"{ticker}-{expiration}-{strike}-{option_type.lower()}",
        )


def build_mock_provider() -> OptionsDataProvider:
    return MockOptionsProvider()
