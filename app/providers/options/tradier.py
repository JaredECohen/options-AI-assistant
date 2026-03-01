from __future__ import annotations

import os
from typing import List

import httpx

from .base import OptionQuote, OptionsDataProvider


class TradierProvider:
    name = "tradier"

    def __init__(self, api_token: str | None = None, base_url: str | None = None):
        self.api_token = api_token or os.getenv("TRADIER_API_TOKEN")
        self.base_url = base_url or "https://api.tradier.com/v1"

    def _headers(self) -> dict:
        if not self.api_token:
            raise RuntimeError("TRADIER_API_TOKEN is required for Tradier provider")
        return {"Authorization": f"Bearer {self.api_token}", "Accept": "application/json"}

    async def _get_json(self, path: str, params: dict) -> dict:
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=params, headers=self._headers())
            resp.raise_for_status()
            return resp.json()

    async def get_underlying_price(self, ticker: str) -> float:
        data = await self._get_json("/markets/quotes", {"symbols": ticker})
        quote = data.get("quotes", {}).get("quote", {})
        last = quote.get("last") or quote.get("close") or quote.get("bid") or quote.get("ask")
        if last is None:
            raise RuntimeError("No quote data available")
        return float(last)

    async def list_expirations(self, ticker: str) -> List[str]:
        data = await self._get_json("/markets/options/expirations", {"symbol": ticker})
        expirations = data.get("expirations", {}).get("date")
        if isinstance(expirations, list):
            return expirations
        if isinstance(expirations, str):
            return [expirations]
        return []

    async def get_chain(self, ticker: str, expiration: str) -> List[OptionQuote]:
        data = await self._get_json(
            "/markets/options/chains",
            {
                "symbol": ticker,
                "expiration": expiration,
            },
        )
        options = data.get("options", {}).get("option", [])
        if isinstance(options, dict):
            options = [options]
        quotes: List[OptionQuote] = []
        for opt in options:
            bid = opt.get("bid")
            ask = opt.get("ask")
            last = opt.get("last")
            mark = None
            if bid is not None and ask is not None:
                mark = (float(bid) + float(ask)) / 2
            elif last is not None:
                mark = float(last)
            quotes.append(
                OptionQuote(
                    option_type=str(opt.get("option_type", "")).lower(),
                    strike=float(opt.get("strike")),
                    expiration=str(opt.get("expiration_date")),
                    bid=float(bid) if bid is not None else None,
                    ask=float(ask) if ask is not None else None,
                    last=float(last) if last is not None else None,
                    mark=mark,
                    symbol=opt.get("symbol"),
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
        raise RuntimeError("Option not found in chain")


def build_tradier_provider() -> OptionsDataProvider:
    return TradierProvider()
