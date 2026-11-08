"""Deterministic synthetic options data for demos and tests.

data_status is "synthetic": the UI labels it as demo data and the factory
refuses to construct it in production unless OPTIONS_PROVIDER=mock and
DEMO_MODE=1 are both set explicitly.
"""
from __future__ import annotations

import datetime as dt

from .base import OptionQuote, QuoteUnavailable

_FIXED_QUOTE_TIME = "1970-01-01T00:00:00+00:00"  # obviously not a market timestamp


class MockOptionsProvider:
    name = "mock"
    data_status = "synthetic"

    def __init__(self, today: dt.date | None = None):
        self._today = today

    def _base_price(self, ticker: str) -> float:
        seed = sum(ord(c) for c in ticker.upper())
        return float(80 + (seed % 60))

    async def get_underlying_price(self, ticker: str) -> float:
        return self._base_price(ticker)

    async def list_expirations(self, ticker: str) -> list[str]:
        today = self._today or dt.date.today()
        return [(today + dt.timedelta(days=7 * i)).isoformat() for i in range(1, 7)]

    @staticmethod
    def _make_premium(base: float, strike: float, option_type: str) -> float:
        intrinsic = max(base - strike, 0.0) if option_type == "call" else max(strike - base, 0.0)
        time_value = max(1.0, 0.08 * base)
        return round(intrinsic + time_value, 2)

    def _quote(self, ticker: str, expiration: str, strike: float, option_type: str) -> OptionQuote:
        base = self._base_price(ticker)
        mark = self._make_premium(base, strike, option_type)
        return OptionQuote(
            option_type=option_type,
            strike=float(strike),
            expiration=expiration,
            bid=round(mark - 0.1, 2),
            ask=round(mark + 0.1, 2),
            last=mark,
            mark=mark,
            symbol=f"{ticker.upper()}-{expiration}-{strike:g}-{option_type}-SYNTHETIC",
            volume=None,
            open_interest=None,
            implied_volatility=None,
            greeks=None,
            quote_time=_FIXED_QUOTE_TIME,
            multiplier=100,
        )

    async def get_chain(self, ticker: str, expiration: str) -> list[OptionQuote]:
        base = self._base_price(ticker)
        strikes = [round(base * 0.5 + 5 * i, 2) for i in range(21)]
        return [self._quote(ticker, expiration, k, t) for k in strikes for t in ("call", "put")]

    async def get_option_quote(self, ticker: str, expiration: str, strike: float, option_type: str) -> OptionQuote:
        for opt in await self.get_chain(ticker, expiration):
            if opt.option_type == option_type.lower() and abs(opt.strike - float(strike)) < 1e-6:
                return opt
        raise QuoteUnavailable(f"No synthetic contract at strike {strike} {option_type} for {ticker.upper()} {expiration}.")


def build_mock_provider() -> MockOptionsProvider:
    return MockOptionsProvider()
