"""ThetaData (v3) options adapter.

Two transports, same parsing:
- "library" (default): ThetaData's official Python library (`thetadata`), which
  signs in with the account email/password (or an API key) and fetches over
  gRPC from ThetaData's servers. No Terminal process is needed.
- "terminal": the Theta Terminal's local REST server (http://127.0.0.1:25503).

Written from ThetaData's v3 docs, OpenAPI file and the library's source
(thetadata 1.0.17), and exercised against fixtures only; run
scripts/thetadata_smoke.py against the live service before relying on it.

Documented facts this relies on (thetadata.net/docs):
- Base http://127.0.0.1:25503, GET only. `format=json` must be explicit (CSV is
  the default). JSON is `{"response": [...]}`. Option rows are nested
  `{"contract": {symbol, strike, right, expiration}, "data": [...]}`; ndjson rows
  are flat. Both are accepted here.
- Strikes are dollars; right is call/put; dates YYYYMMDD; timestamps are New
  York local time with no offset.
- Tiers: quotes, open interest and OHLC need Value; greeks and IV need
  Standard (`first_order` has no gamma; `all` is Pro only); EOD greeks history
  needs Standard; plain EOD history is in every tier.
- Errors are HTTP codes: 471 permission, 472 no data, 473/475 bad request,
  474 disconnected, 478 second terminal, 429 queue full, 571/572 server-side.
- Concurrency is capped per account (Value 2, Standard 4, Pro 8); excess
  requests queue in the Terminal, so a semaphore keeps us under the cap.

Licence: ThetaData's individual plans are personal use only, with no display or
redistribution to others. Showing this data to anyone else needs their Business
plan.

Data status is configured (THETADATA_DATA_STATUS), never inferred, and defaults
to "delayed" so nothing is labelled real-time by accident. Multipliers are not
in ThetaData's option rows; 100 is assumed and stated in `conventions`.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import math
import re
import threading
from collections.abc import Callable
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .base import EodOption, Greeks, OptionQuote, ProviderUnavailable, QuoteUnavailable

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://127.0.0.1:25503"
NY = ZoneInfo("America/New_York")
_ALLOWED_STATUS = {"live", "delayed", "eod"}
_SYMBOL_RE = re.compile(r"^[A-Z.]{1,10}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SOURCE = "thetadata"


class PermissionDenied(ProviderUnavailable):
    """HTTP 471: the subscription tier does not include this endpoint."""


class NoData(QuoteUnavailable):
    """HTTP 472: nothing matched."""


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    # The library rebuilds prices as integer x scale factor, so 30.11 arrives as
    # 30.110000000000003; 8 decimals removes that noise without touching real digits.
    return round(out, 8) if math.isfinite(out) else None


def _int(value: Any) -> int | None:
    f = _num(value)
    return None if f is None else int(f)


def ny_iso(value: Any) -> str | None:
    """ThetaData timestamps are New York local time without an offset."""
    if not value:
        return None
    try:
        t = dt.datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=NY)
    return t.isoformat()


def _day(value: Any) -> dt.date | None:
    if not value:
        return None
    text = str(value)
    try:
        return dt.date.fromisoformat(text[:10]) if "-" in text[:10] else dt.datetime.strptime(text[:8], "%Y%m%d").date()
    except ValueError:
        return None


def _right(value: Any) -> str | None:
    r = str(value or "").strip().lower()
    return {"call": "call", "c": "call", "put": "put", "p": "put"}.get(r)


def _exp(value: Any) -> str | None:
    d = _day(value)
    return d.isoformat() if d else None


def occ_symbol(root: str, expiration: str, option_type: str, strike: float) -> str:
    d = dt.date.fromisoformat(expiration)
    return f"{root.upper():<6}{d:%y%m%d}{'C' if option_type == 'call' else 'P'}{round(strike * 1000):08d}"


def flatten(body: Any) -> list[dict]:
    """Normalise the documented shapes into flat rows carrying the contract fields:
    {"response": [{"contract": {...}, "data": [...]}]}, a bare list of those, or flat rows."""
    rows = body.get("response", body) if isinstance(body, dict) else body
    if isinstance(rows, dict):
        rows = [rows]
    out: list[dict] = []
    for item in rows or []:
        if not isinstance(item, dict):
            continue
        contract = item.get("contract")
        data = item.get("data")
        if isinstance(contract, dict) and isinstance(data, list):
            out.extend({**d, **contract} for d in data if isinstance(d, dict))
        elif isinstance(contract, dict) and isinstance(data, dict):
            out.append({**data, **contract})
        else:
            out.append(item)
    return out


def _library_client(email: str, password: str, api_key: str):
    """Sign in with ThetaData's library. Its logger prints the whole sign-in
    response (including the session id) at INFO, so it is held at WARNING."""
    logging.getLogger("thetadata").setLevel(logging.WARNING)
    if not (api_key or (email and password)):
        raise ProviderUnavailable("ThetaData is not configured (THETADATA_EMAIL and THETADATA_PASSWORD, or THETADATA_API_KEY).")
    from thetadata import ThetaClient  # imported only when ThetaData is in use (pulls in grpc, polars)

    if api_key:
        return ThetaClient(api_key=api_key, dataframe_type="polars")
    return ThetaClient(email=email, password=password, dataframe_type="polars")


def _as_date(value: str) -> dt.date | str:
    if value == "*":
        return value
    return dt.datetime.strptime(value, "%Y%m%d").date()


def _library_call(path: str, p: dict) -> tuple[str, dict]:
    """Translate a v3 REST path + params into the equivalent library method call."""
    sym = p["symbol"]
    def opt(extra=()):
        kw = {"symbol": sym, "expiration": _as_date(p["expiration"])}
        for k in ("max_dte", "strike_range", *extra):
            if k in p:
                kw[k] = int(p[k])
        return kw
    def hist():
        kw = opt()
        kw.update(start_date=_as_date(p["start_date"]), end_date=_as_date(p["end_date"]))
        for k in ("strike", "right"):  # one contract: ~0.5 s instead of ~4 s per chain-day
            if k in p:
                kw[k] = str(p[k])
        return kw
    table: dict[str, tuple[str, Callable[[], dict]]] = {
        "/v3/stock/snapshot/quote": ("stock_snapshot_quote", lambda: {"symbol": [sym]}),
        "/v3/stock/history/eod": ("stock_history_eod", lambda: {
            "symbol": sym, "start_date": _as_date(p["start_date"]), "end_date": _as_date(p["end_date"])}),
        "/v3/option/list/expirations": ("option_list_expirations", lambda: {"symbol": sym}),
        "/v3/option/snapshot/greeks/first_order": ("option_snapshot_greeks_first_order", opt),
        "/v3/option/snapshot/greeks/implied_volatility": ("option_snapshot_greeks_implied_volatility", opt),
        "/v3/option/snapshot/quote": ("option_snapshot_quote", opt),
        "/v3/option/snapshot/open_interest": ("option_snapshot_open_interest", opt),
        "/v3/option/snapshot/ohlc": ("option_snapshot_ohlc", opt),
        "/v3/option/history/greeks/eod": ("option_history_greeks_eod", hist),
        "/v3/option/history/eod": ("option_history_eod", hist),
    }
    if path not in table:
        raise QuoteUnavailable(f"no library mapping for {path}")
    method, build = table[path]
    return method, build()


class ThetaDataProvider:
    name = "thetadata"
    max_history_range_days = 28  # ThetaData's sizing guide: about one month of EOD per request

    def __init__(self, base_url: str | None = None, data_status: str = "delayed", timeout_seconds: float = 15.0,
                 max_concurrency: int = 2, transport: httpx.AsyncBaseTransport | None = None,
                 access: str = "terminal", email: str = "", password: str = "", api_key: str = "",
                 client_factory: Callable[[], object] | None = None, sign_in_timeout_seconds: float = 60.0):
        status = (data_status or "delayed").lower()
        if status not in _ALLOWED_STATUS:
            raise ValueError(f"THETADATA_DATA_STATUS must be one of {sorted(_ALLOWED_STATUS)}")
        if access not in ("library", "terminal"):
            raise ValueError("THETADATA_ACCESS must be library or terminal")
        self.access = access
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._client_factory = client_factory or (lambda: _library_client(email, password, api_key))
        self._client = None
        self._client_lock = threading.Lock()
        # Signing in takes ~10-15 s, about as long as a whole request may take, so it
        # gets its own budget, and concurrent first requests share one sign-in.
        self._sign_in_lock = asyncio.Lock()
        self._sign_in_timeout = sign_in_timeout_seconds
        self.data_status = status
        self._timeout = timeout_seconds
        self._transport = transport
        self._sem = asyncio.Semaphore(max(1, max_concurrency))
        self.requests = 0  # for cost reporting

    @staticmethod
    def conventions() -> dict:
        return {"multiplier": "100 assumed (ThetaData option rows carry no deliverable)",
                "greeks": "ThetaData first_order (no gamma); IV from national bid/mid/ask",
                "timestamps": "New York local time"}

    @staticmethod
    def _symbol(ticker: str) -> str:
        t = ticker.upper()
        if not _SYMBOL_RE.match(t):
            raise QuoteUnavailable(f"invalid symbol {ticker!r}")
        return t

    async def _get(self, path: str, params: dict) -> list[dict]:
        if self.access == "library":
            return await self._get_library(path, params)
        return await self._get_terminal(path, params)

    # ---- library transport ---------------------------------------------------
    def _library(self):
        with self._client_lock:  # sign in once, lazily, off the event loop
            if self._client is None:
                self._client = self._client_factory()
            return self._client

    def _call_library(self, path: str, params: dict) -> list[dict]:
        method, kwargs = _library_call(path, params)
        try:
            frame = getattr(self._library(), method)(**kwargs)
        except Exception as exc:  # noqa: BLE001 - mapped to the provider contract below
            raise self._map_library_error(exc) from exc
        if hasattr(frame, "to_dicts"):  # polars
            return frame.to_dicts()
        if hasattr(frame, "to_dict"):  # pandas
            return frame.to_dict("records")
        return list(frame or [])

    def _map_library_error(self, exc: Exception) -> Exception:
        if isinstance(exc, (ProviderUnavailable, QuoteUnavailable)):
            return exc
        kind = type(exc).__name__
        if kind == "NoDataFoundError":
            return NoData("ThetaData has no data for this request.")
        if kind == "AuthenticationError":
            return ProviderUnavailable("ThetaData sign-in failed (check THETADATA_EMAIL/PASSWORD or API key).")
        code = getattr(exc, "code", None)
        name = getattr(code(), "name", "") if callable(code) else ""
        if name == "PERMISSION_DENIED":
            return PermissionDenied("ThetaData subscription does not include this data.")
        if name == "UNAUTHENTICATED":
            with self._client_lock:
                self._client = None  # sign in again on the next request
            return ProviderUnavailable("ThetaData session expired; it will sign in again.")
        if name == "INVALID_ARGUMENT":
            return QuoteUnavailable("ThetaData rejected the request parameters.")
        if name:
            return ProviderUnavailable(f"ThetaData returned {name}.")
        return ProviderUnavailable(f"ThetaData request failed ({kind}).")

    async def _ensure_library_client(self) -> None:
        if self._client is not None:
            return
        async with self._sign_in_lock:
            if self._client is not None:
                return
            try:
                await asyncio.wait_for(asyncio.to_thread(self._library), self._sign_in_timeout)
            except TimeoutError as exc:
                raise ProviderUnavailable("ThetaData sign-in did not finish in time.") from exc
            except Exception as exc:  # noqa: BLE001 - mapped to the provider contract
                raise self._map_library_error(exc) from exc

    async def warm_up(self) -> bool:
        """Sign in before the first request arrives (a cold start would otherwise
        spend the first request's whole budget signing in). Never raises."""
        if self.access != "library":
            return True
        try:
            await self._ensure_library_client()
            return True
        except Exception as exc:  # noqa: BLE001 - the first real request reports it
            log.warning("thetadata_warm_up_failed %s", exc.__class__.__name__)
            return False

    async def _get_library(self, path: str, params: dict) -> list[dict]:
        await self._ensure_library_client()
        async with self._sem:
            self.requests += 1
            try:
                return await asyncio.wait_for(asyncio.to_thread(self._call_library, path, params), self._timeout)
            except TimeoutError as exc:
                raise ProviderUnavailable("ThetaData did not answer in time.") from exc

    # ---- terminal transport --------------------------------------------------
    async def _get_terminal(self, path: str, params: dict) -> list[dict]:
        params = {**params, "format": "json"}
        async with self._sem:
            self.requests += 1
            try:
                async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                    resp = await client.get(f"{self.base_url}{path}", params=params)
            except httpx.ConnectError as exc:
                raise ProviderUnavailable("Theta Terminal is not reachable (is it running?).") from exc
            except httpx.HTTPError as exc:
                raise ProviderUnavailable(f"Theta Terminal request failed ({exc.__class__.__name__}).") from exc
        code = resp.status_code
        if code == 471:
            raise PermissionDenied(f"ThetaData subscription does not include {path}.")
        if code == 472:
            raise NoData(f"ThetaData has no data for {path}.")
        if code in (473, 475):
            raise QuoteUnavailable(f"ThetaData rejected the request parameters for {path}.")
        if code == 404:
            raise ProviderUnavailable("Theta Terminal does not know this endpoint (outdated terminal?).")
        if code == 476:
            raise ProviderUnavailable("Theta Terminal saw a different client IP (use one host name consistently).")
        if code >= 400:
            raise ProviderUnavailable(f"Theta Terminal returned HTTP {code}.")
        try:
            return flatten(resp.json())
        except ValueError as exc:
            raise ProviderUnavailable("Theta Terminal returned a non-JSON body.") from exc

    # ---- live --------------------------------------------------------------
    async def get_underlying_price(self, ticker: str) -> float:
        sym = self._symbol(ticker)
        try:
            rows = await self._get("/v3/stock/snapshot/quote", {"symbol": sym})
            for r in rows:
                bid, ask = _num(r.get("bid")), _num(r.get("ask"))
                if bid is not None and ask is not None and 0 < bid <= ask:
                    return (bid + ask) / 2
        except (PermissionDenied, NoData):
            pass  # an options-only plan may not include stock quotes; greeks rows carry the underlying
        rows = await self._get("/v3/option/snapshot/greeks/implied_volatility",
                               {"symbol": sym, "expiration": "*", "max_dte": 45, "strike_range": 1})
        prices = [p for p in (_num(r.get("underlying_price")) for r in rows) if p and p > 0]
        if not prices:
            raise QuoteUnavailable(f"No usable underlying price for {sym}.")
        return prices[0]

    async def list_expirations_all(self, ticker: str) -> list[str]:
        rows = await self._get("/v3/option/list/expirations", {"symbol": self._symbol(ticker)})
        return sorted({e for e in (_exp(r.get("expiration")) for r in rows) if e})

    async def list_expirations(self, ticker: str) -> list[str]:
        today = dt.datetime.now(NY).date().isoformat()
        return [e for e in await self.list_expirations_all(ticker) if e >= today]

    async def _optional(self, path: str, params: dict) -> list[dict]:
        try:
            return await self._get(path, params)
        except (PermissionDenied, NoData, QuoteUnavailable):
            return []

    async def get_chain(self, ticker: str, expiration: str) -> list[OptionQuote]:
        sym = self._symbol(ticker)
        if not _DATE_RE.match(expiration):
            raise QuoteUnavailable("expiration must be YYYY-MM-DD")
        params = {"symbol": sym, "expiration": expiration.replace("-", "")}

        async def priced() -> tuple[list[dict], bool]:
            try:
                return await self._get("/v3/option/snapshot/greeks/first_order", params), True
            except PermissionDenied:  # Value tier: quotes without greeks
                return await self._get("/v3/option/snapshot/quote", params), False

        (rows, has_greeks), oi_rows, ohlc_rows = await asyncio.gather(
            priced(), self._optional("/v3/option/snapshot/open_interest", params),
            self._optional("/v3/option/snapshot/ohlc", params))

        def key(r: dict):
            return (_right(r.get("right")), _num(r.get("strike")))

        oi = {key(r): _int(r.get("open_interest")) for r in oi_rows}
        vol = {key(r): (_int(r.get("volume")), _num(r.get("close"))) for r in ohlc_rows}
        out = []
        for r in rows:
            right, strike = key(r)
            if right is None or strike is None:
                continue
            exp = _exp(r.get("expiration")) or expiration
            bid, ask = _num(r.get("bid")), _num(r.get("ask"))
            mark = (bid + ask) / 2 if bid is not None and ask is not None and 0 <= bid <= ask else None
            quote_time = ny_iso(r.get("timestamp"))
            greeks = iv = None
            if has_greeks:
                greeks = Greeks(delta=_num(r.get("delta")), gamma=_num(r.get("gamma")), theta=_num(r.get("theta")),
                                vega=_num(r.get("vega")), rho=_num(r.get("rho")), source=SOURCE, as_of=quote_time)
                iv = _num(r.get("implied_vol"))
                if iv is not None and iv <= 0:
                    iv = None
            volume, last = vol.get((right, strike), (None, None))
            out.append(OptionQuote(
                option_type=right, strike=strike, expiration=exp, bid=bid, ask=ask, last=last, mark=mark,
                symbol=occ_symbol(sym, exp, right, strike), volume=volume, open_interest=oi.get((right, strike)),
                implied_volatility=iv, iv_source="thetadata implied_vol (mid)" if iv is not None else None,
                greeks=greeks, quote_time=quote_time, multiplier=100, adjusted=False,
                extra={"underlying_price": _num(r.get("underlying_price"))},
            ))
        return sorted(out, key=lambda q: (q.strike, q.option_type))

    async def get_option_quote(self, ticker: str, expiration: str, strike: float, option_type: str) -> OptionQuote:
        for q in await self.get_chain(ticker, expiration):
            if q.option_type == option_type.lower() and abs(q.strike - float(strike)) < 1e-6:
                return q
        raise QuoteUnavailable(f"{ticker.upper()} {expiration} {strike:g} {option_type} is not in the chain.")

    # ---- history -----------------------------------------------------------
    async def get_daily_bars(self, ticker: str, start: dt.date, end: dt.date) -> list[tuple[dt.date, float]]:
        try:
            rows = await self._get("/v3/stock/history/eod", {"symbol": self._symbol(ticker),
                                                              "start_date": f"{start:%Y%m%d}", "end_date": f"{end:%Y%m%d}"})
        except (PermissionDenied, NoData):
            return []
        out = {}
        for r in rows:
            day = _day(r.get("last_trade") or r.get("created") or r.get("timestamp") or r.get("date"))
            close = _num(r.get("close"))
            if day and close and close > 0:
                out[day] = close
        return sorted(out.items())

    async def get_daily_closes(self, ticker: str, start: dt.date, end: dt.date) -> list[float]:
        return [c for _d, c in await self.get_daily_bars(ticker, start, end)]

    async def get_eod_options(self, ticker: str, expiration: str, start: dt.date, end: dt.date,
                              strike_range: int = 40) -> list[EodOption]:
        params = {"symbol": self._symbol(ticker), "expiration": expiration.replace("-", ""),
                  "start_date": f"{start:%Y%m%d}", "end_date": f"{end:%Y%m%d}", "strike_range": strike_range}
        return await self._eod(params, expiration)

    async def get_eod_chain(self, ticker: str, expiration: str, day: dt.date, strike_range: int = 40) -> list[EodOption]:
        """The closing chain of one expiration on one day (~4 s live, about the
        same cost whatever the strike count, so backtests fetch it only on entry days)."""
        return await self.get_eod_options(ticker, expiration, day, day, strike_range)

    async def get_contract_eod(self, ticker: str, expiration: str, strike: float, option_type: str,
                               start: dt.date, end: dt.date) -> list[EodOption]:
        """Daily closes of ONE contract over a range (~0.5 s live for 70 days; no
        28-day cap observed for single contracts)."""
        if option_type not in ("call", "put"):
            raise QuoteUnavailable(f"invalid option type {option_type!r}")
        params = {"symbol": self._symbol(ticker), "expiration": expiration.replace("-", ""),
                  "start_date": f"{start:%Y%m%d}", "end_date": f"{end:%Y%m%d}",
                  "strike": f"{strike:g}", "right": option_type}
        return await self._eod(params, expiration)

    async def _eod(self, params: dict, expiration: str) -> list[EodOption]:
        try:
            try:
                rows = await self._get("/v3/option/history/greeks/eod", params)  # Standard: carries underlying_price
            except PermissionDenied:
                rows = await self._get("/v3/option/history/eod", params)  # any tier; underlying from stock bars
        except NoData:
            return []
        out = []
        for r in rows:
            right, strike = _right(r.get("right")), _num(r.get("strike"))
            day = _day(r.get("timestamp") or r.get("created") or r.get("last_trade") or r.get("date"))
            if right is None or strike is None or day is None:
                continue
            out.append(EodOption(date=day, expiration=_exp(r.get("expiration")) or expiration, strike=strike,
                                 option_type=right, bid=_num(r.get("bid")), ask=_num(r.get("ask")),
                                 underlying_price=_num(r.get("underlying_price")), delta=_num(r.get("delta")),
                                 implied_vol=_num(r.get("implied_vol"))))
        return out
