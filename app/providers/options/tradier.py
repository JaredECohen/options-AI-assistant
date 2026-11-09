"""Tradier market-data adapter.

Field names follow Tradier's documented /markets responses (options chains with
greeks=true carry ORATS-sourced greeks and bid/mid/ask IV). Re-verify against
current Tradier documentation before production acceptance; this adapter has
only been exercised against recorded fixtures.

Data status is NOT inferred: whether a Tradier account gets real-time or
delayed options data depends on the account's market-data entitlements. It
comes from TRADIER_DATA_STATUS and defaults to "delayed" so nothing is ever
labelled real-time by accident.
"""
from __future__ import annotations

import datetime as dt
import math
import os
import re
from typing import Any

import httpx

from .base import Greeks, OptionQuote, ProviderUnavailable, QuoteUnavailable

PRODUCTION_URL = "https://api.tradier.com/v1"
SANDBOX_URL = "https://sandbox.tradier.com/v1"
_ALLOWED_STATUS = {"live", "delayed", "eod"}
_SYMBOL_RE = re.compile(r"^[A-Z.]{1,10}$")


def _ms_to_iso(value: Any) -> str | None:
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return None
    if ms <= 0:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.UTC).isoformat()


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None  # "nan"/"inf" are not prices


def _int(value: Any) -> int | None:
    f = _num(value)
    return None if f is None else int(f)


class TradierProvider:
    name = "tradier"

    def __init__(
        self,
        api_token: str | None = None,
        base_url: str | None = None,
        data_status: str | None = None,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.api_token = api_token if api_token is not None else os.getenv("TRADIER_API_TOKEN", "")
        env = os.getenv("TRADIER_ENV", "production").lower()
        self.base_url = base_url or (SANDBOX_URL if env == "sandbox" else PRODUCTION_URL)
        status = (data_status or os.getenv("TRADIER_DATA_STATUS", "delayed")).lower()
        if status not in _ALLOWED_STATUS:
            raise ValueError(f"TRADIER_DATA_STATUS must be one of {sorted(_ALLOWED_STATUS)}")
        self.data_status = status
        self._timeout = timeout_seconds
        self._transport = transport

    @staticmethod
    def _symbol(ticker: str) -> str:
        t = ticker.upper()
        if not _SYMBOL_RE.match(t):  # e.g. "AAPL,MSFT" would silently quote AAPL
            raise QuoteUnavailable(f"invalid symbol {ticker!r}")
        return t

    def _headers(self) -> dict:
        if not self.api_token:
            raise ProviderUnavailable("Tradier is not configured (TRADIER_API_TOKEN is unset).")
        return {"Authorization": f"Bearer {self.api_token}", "Accept": "application/json"}

    async def _get_json(self, path: str, params: dict) -> dict:
        headers = self._headers()
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                resp = await client.get(f"{self.base_url}{path}", params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"Tradier request failed ({exc.__class__.__name__}).") from exc
        if resp.status_code in (401, 403):
            raise ProviderUnavailable("Tradier rejected the credentials or entitlement.")
        if resp.status_code == 429:
            raise ProviderUnavailable("Tradier rate limit reached.")
        if resp.status_code >= 500:
            raise ProviderUnavailable(f"Tradier returned HTTP {resp.status_code}.")
        if resp.status_code >= 400:
            raise QuoteUnavailable(f"Tradier returned HTTP {resp.status_code} for {path}.")
        try:
            return resp.json()
        except ValueError as exc:
            raise ProviderUnavailable("Tradier returned a non-JSON body.") from exc

    async def get_underlying_price(self, ticker: str) -> float:
        data = await self._get_json("/markets/quotes", {"symbols": self._symbol(ticker)})
        quote = (data.get("quotes") or {}).get("quote") or {}
        if isinstance(quote, list):
            quote = quote[0] if quote else {}
        bid, ask, last = _num(quote.get("bid")), _num(quote.get("ask")), _num(quote.get("last"))
        if bid is not None and ask is not None and 0 < bid <= ask:
            return (bid + ask) / 2
        if last is not None and last > 0:
            return last
        raise QuoteUnavailable(f"No usable quote for {ticker.upper()}.")

    async def list_expirations(self, ticker: str) -> list[str]:
        data = await self._get_json("/markets/options/expirations", {"symbol": self._symbol(ticker)})
        expirations = (data.get("expirations") or {}).get("date")
        if isinstance(expirations, list):
            return [str(e) for e in expirations]
        if isinstance(expirations, str):
            return [expirations]
        return []

    def _parse_option(self, opt: dict) -> OptionQuote:
        bid, ask, last = _num(opt.get("bid")), _num(opt.get("ask")), _num(opt.get("last"))
        mark = (bid + ask) / 2 if bid is not None and ask is not None and bid <= ask else None
        g = opt.get("greeks") or None
        greeks = None
        iv = None
        if isinstance(g, dict):
            greeks = Greeks(
                delta=_num(g.get("delta")),
                gamma=_num(g.get("gamma")),
                theta=_num(g.get("theta")),
                vega=_num(g.get("vega")),
                rho=_num(g.get("rho")),
                source="tradier/orats",
                as_of=str(g.get("updated_at")) if g.get("updated_at") else None,
            )
            iv = _num(g.get("mid_iv")) or _num(g.get("smv_vol"))
        times = [t for t in (_ms_to_iso(opt.get("bid_date")), _ms_to_iso(opt.get("ask_date"))) if t]
        root, underlying = opt.get("root_symbol"), opt.get("underlying")
        multiplier = _int(opt.get("contract_size")) or 100
        return OptionQuote(
            option_type=str(opt.get("option_type", "")).lower(),
            strike=float(opt.get("strike")),
            expiration=str(opt.get("expiration_date")),
            bid=bid,
            ask=ask,
            last=last,
            mark=mark,
            symbol=opt.get("symbol"),
            volume=_int(opt.get("volume")),
            open_interest=_int(opt.get("open_interest")),
            implied_volatility=iv,
            iv_source="tradier/orats mid_iv" if iv is not None else None,
            greeks=greeks,
            quote_time=min(times) if times else _ms_to_iso(opt.get("trade_date")),
            multiplier=multiplier,
            adjusted=bool(root and underlying and root != underlying) or multiplier != 100,
        )

    async def get_chain(self, ticker: str, expiration: str) -> list[OptionQuote]:
        data = await self._get_json(
            "/markets/options/chains",
            {"symbol": self._symbol(ticker), "expiration": expiration, "greeks": "true"},
        )
        options = (data.get("options") or {}).get("option") or []
        if isinstance(options, dict):
            options = [options]
        quotes = []
        for opt in options:
            if opt.get("strike") is None or opt.get("option_type") not in ("call", "put"):
                continue
            quotes.append(self._parse_option(opt))
        return quotes

    async def get_option_quote(self, ticker: str, expiration: str, strike: float, option_type: str) -> OptionQuote:
        for opt in await self.get_chain(ticker, expiration):
            if opt.option_type == option_type.lower() and abs(opt.strike - float(strike)) < 1e-6:
                return opt
        raise QuoteUnavailable(f"{ticker.upper()} {expiration} {strike:g} {option_type} is not in the chain.")


    async def get_daily_bars(self, ticker: str, start: dt.date, end: dt.date) -> list[tuple[dt.date, float]]:
        data = await self._get_json("/markets/history", {"symbol": self._symbol(ticker), "interval": "daily",
                                                          "start": start.isoformat(), "end": end.isoformat()})
        days = (data.get("history") or {}).get("day") or []
        if isinstance(days, dict):
            days = [days]
        out = []
        for d in days:
            c = _num(d.get("close"))
            try:
                day = dt.date.fromisoformat(str(d.get("date")))
            except ValueError:
                continue
            if c is not None and c > 0:
                out.append((day, c))
        return sorted(out)

    async def get_daily_closes(self, ticker: str, start: dt.date, end: dt.date) -> list[float]:
        return [c for _d, c in await self.get_daily_bars(ticker, start, end)]


def build_tradier_provider() -> TradierProvider:
    return TradierProvider()
