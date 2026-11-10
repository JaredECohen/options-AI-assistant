"""ThetaData adapter against fixtures in the shapes documented by ThetaData's v3
OpenAPI examples (nested contract/data rows, uppercase right, some greeks as
strings). No Terminal and no network: an httpx MockTransport serves them."""
from __future__ import annotations

import asyncio
import datetime as dt

import httpx
import pytest

from app.config import Settings, validate_for_startup
from app.providers.options.base import ProviderUnavailable, QuoteUnavailable
from app.providers.options.factory import ProviderConfigError, build_history_provider, build_options_provider
from app.providers.options.thetadata import ThetaDataProvider, flatten, ny_iso, occ_symbol

EXP = "2026-12-18"


def chain_row(strike, right, bid, ask, iv, delta, theta="-0.0217"):
    return {"contract": {"symbol": "SPY", "strike": strike, "right": right, "expiration": EXP},
            "data": [{"bid": bid, "ask": ask, "implied_vol": iv, "delta": delta, "theta": theta, "vega": 26.3,
                      "rho": 9.1, "iv_error": "-0.0003", "underlying_price": 600.5,
                      "underlying_timestamp": "2026-11-06T16:37:06.98", "timestamp": "2026-11-06T15:59:59.805"}]}


FIRST_ORDER = {"response": [chain_row(600.0, "CALL", 10.1, 10.3, 0.18, 0.52),
                            chain_row(600.0, "PUT", 9.0, 9.2, 0.19, -0.48),
                            chain_row(610.0, "CALL", 5.0, 5.2, "0", 0.35),  # zero IV -> None
                            {"contract": {"symbol": "SPY", "strike": 620.0, "right": "weird", "expiration": EXP},
                             "data": [{"bid": 1, "ask": 2}]}]}
OI = {"response": [{"contract": {"symbol": "SPY", "strike": 600.0, "right": "CALL", "expiration": EXP},
                    "data": [{"open_interest": 8066, "timestamp": "2026-11-06T06:30:13"}]}]}
OHLC = {"response": [{"contract": {"symbol": "SPY", "strike": 600.0, "right": "PUT", "expiration": EXP},
                      "data": [{"open": 9, "high": 9.5, "low": 8.8, "close": 9.1, "volume": 1234, "count": 50}]}]}


class Terminal:
    """Routes by path; `codes` forces an HTTP status per path."""

    def __init__(self, routes=None, codes=None, delay=0.0):
        self.routes = routes or {}
        self.codes = codes or {}
        self.delay = delay
        self.calls: list[tuple[str, dict]] = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def handler(self, request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, dict(request.url.params)
        self.calls.append((path, params))
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if path in self.codes:
                return httpx.Response(self.codes[path], text="error")
            if path not in self.routes:
                return httpx.Response(472, text="NO_DATA")
            return httpx.Response(200, json=self.routes[path])
        finally:
            self.in_flight -= 1

    def provider(self, **kw) -> ThetaDataProvider:
        return ThetaDataProvider(base_url="http://terminal:25503", transport=httpx.MockTransport(self.handler), **kw)


def test_flatten_accepts_nested_wrapped_bare_and_flat():
    nested = flatten({"response": [chain_row(1.0, "CALL", 1, 2, 0.2, 0.5)]})
    assert nested[0]["strike"] == 1.0 and nested[0]["bid"] == 1 and nested[0]["right"] == "CALL"
    assert flatten([{"symbol": "X", "expiration": "2026-01-16"}]) == [{"symbol": "X", "expiration": "2026-01-16"}]
    assert flatten({"response": {"contract": {"strike": 2}, "data": {"bid": 1}}}) == [{"bid": 1, "strike": 2}]


def test_occ_symbol_and_new_york_timestamps():
    assert occ_symbol("SPY", "2026-12-18", "put", 600.0) == "SPY   261218P00600000"
    assert occ_symbol("BRK.B", "2026-01-16", "call", 412.5) == "BRK.B 260116C00412500"
    assert ny_iso("2026-11-06T15:59:59.805") == "2026-11-06T15:59:59.805000-05:00"  # EST after Nov 1
    assert ny_iso("2026-07-06T16:37:06.98").endswith("-04:00")  # 2 fractional digits, EDT
    assert ny_iso("garbage") is None


async def test_chain_merges_greeks_oi_and_volume_with_provenance():
    t = Terminal({"/v3/option/snapshot/greeks/first_order": FIRST_ORDER, "/v3/option/snapshot/open_interest": OI,
                  "/v3/option/snapshot/ohlc": OHLC})
    chain = await t.provider().get_chain("spy", EXP)
    assert [(q.strike, q.option_type) for q in chain] == [(600.0, "call"), (600.0, "put"), (610.0, "call")]
    call, put, otm = chain
    assert call.symbol == "SPY   261218C00600000" and call.mark == pytest.approx(10.2)
    assert call.open_interest == 8066 and call.volume is None
    assert put.volume == 1234 and put.last == 9.1 and put.open_interest is None
    assert call.implied_volatility == 0.18 and call.iv_source.startswith("thetadata")
    assert call.greeks.theta == -0.0217 and call.greeks.gamma is None and call.greeks.source == "thetadata"
    assert otm.implied_volatility is None  # a zero IV is not a volatility
    assert call.quote_time.endswith("-05:00") and call.multiplier == 100
    for _path, params in t.calls:
        assert params["format"] == "json" and params["expiration"] == "20261218" and params["symbol"] == "SPY"


async def test_value_tier_falls_back_to_quotes_without_greeks():
    quote = {"response": [{"contract": {"symbol": "SPY", "strike": 600.0, "right": "CALL", "expiration": EXP},
                           "data": [{"bid": 10.1, "ask": 10.3, "bid_size": 5, "timestamp": "2026-11-06T15:59:59.805"}]}]}
    t = Terminal({"/v3/option/snapshot/quote": quote}, codes={"/v3/option/snapshot/greeks/first_order": 471})
    (q,) = await t.provider().get_chain("SPY", EXP)
    assert q.greeks is None and q.implied_volatility is None and q.bid == 10.1


@pytest.mark.parametrize("code,exc", [(471, ProviderUnavailable), (474, ProviderUnavailable), (478, ProviderUnavailable),
                                      (429, ProviderUnavailable), (571, ProviderUnavailable), (404, ProviderUnavailable),
                                      (473, QuoteUnavailable), (472, QuoteUnavailable)])
async def test_error_codes_map_to_honest_failures(code, exc):
    t = Terminal(codes={"/v3/option/list/expirations": code})
    with pytest.raises(exc):
        await t.provider().list_expirations_all("SPY")


async def test_terminal_not_running_is_provider_unavailable():
    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    p = ThetaDataProvider(base_url="http://terminal:25503", transport=httpx.MockTransport(refuse))
    with pytest.raises(ProviderUnavailable, match="not reachable"):
        await p.get_underlying_price("SPY")


async def test_underlying_prefers_stock_mid_then_option_rows():
    stock = {"response": [{"symbol": "SPY", "bid": 600.0, "ask": 600.2}]}
    assert await Terminal({"/v3/stock/snapshot/quote": stock}).provider().get_underlying_price("SPY") == pytest.approx(600.1)
    t = Terminal({"/v3/option/snapshot/greeks/implied_volatility": FIRST_ORDER},
                 codes={"/v3/stock/snapshot/quote": 471})
    assert await t.provider().get_underlying_price("SPY") == 600.5
    with pytest.raises(QuoteUnavailable):
        await Terminal(codes={"/v3/stock/snapshot/quote": 471}).provider().get_underlying_price("SPY")


async def test_expirations_all_and_future_only():
    rows = {"response": [{"symbol": "SPY", "expiration": "2012-06-01"}, {"symbol": "SPY", "expiration": "2099-01-15"},
                         {"symbol": "SPY", "expiration": "20990122"}]}
    p = Terminal({"/v3/option/list/expirations": rows}).provider()
    assert await p.list_expirations_all("SPY") == ["2012-06-01", "2099-01-15", "2099-01-22"]
    assert await p.list_expirations("SPY") == ["2099-01-15", "2099-01-22"]


async def test_eod_history_parses_rows_and_falls_back_by_tier():
    eod_greeks = {"response": [{"contract": {"symbol": "SPY", "strike": 600.0, "right": "PUT", "expiration": EXP},
                                "data": [{"bid": 9.0, "ask": 9.2, "implied_vol": 0.19, "underlying_price": 600.5,
                                          "timestamp": "2026-11-05T17:15:00.000"},
                                         {"bid": 8.0, "ask": 8.3, "underlying_price": 603.0,
                                          "timestamp": "2026-11-06T17:15:00.000"}]}]}
    t = Terminal({"/v3/option/history/greeks/eod": eod_greeks})
    rows = await t.provider().get_eod_options("SPY", EXP, dt.date(2026, 11, 5), dt.date(2026, 11, 6))
    assert [(r.date, r.bid, r.underlying_price) for r in rows] == [(dt.date(2026, 11, 5), 9.0, 600.5),
                                                                    (dt.date(2026, 11, 6), 8.0, 603.0)]
    path, params = t.calls[0]
    assert params["start_date"] == "20261105" and params["end_date"] == "20261106" and params["strike_range"] == "40"
    plain = {"response": [{"contract": {"symbol": "SPY", "strike": 600.0, "right": "CALL", "expiration": EXP},
                           "data": [{"bid": 1.0, "ask": 1.1, "created": "2026-11-05T17:15:00"}]}]}
    t2 = Terminal({"/v3/option/history/eod": plain}, codes={"/v3/option/history/greeks/eod": 471})
    (r,) = await t2.provider().get_eod_options("SPY", EXP, dt.date(2026, 11, 5), dt.date(2026, 11, 5))
    assert r.underlying_price is None and r.date == dt.date(2026, 11, 5)
    assert await Terminal().provider().get_eod_options("SPY", EXP, dt.date(2026, 11, 5), dt.date(2026, 11, 5)) == []


async def test_daily_bars_and_missing_stock_permission():
    bars = {"response": [{"symbol": "SPY", "close": 600.0, "last_trade": "2026-11-05T15:59:59"},
                         {"symbol": "SPY", "close": "nan", "last_trade": "2026-11-06T15:59:59"}]}
    assert await Terminal({"/v3/stock/history/eod": bars}).provider().get_daily_bars(
        "SPY", dt.date(2026, 11, 1), dt.date(2026, 11, 6)) == [(dt.date(2026, 11, 5), 600.0)]
    assert await Terminal(codes={"/v3/stock/history/eod": 471}).provider().get_daily_bars(
        "SPY", dt.date(2026, 11, 1), dt.date(2026, 11, 6)) == []


async def test_concurrency_is_capped():
    t = Terminal({"/v3/option/list/expirations": {"response": []}}, delay=0.02)
    p = t.provider(max_concurrency=2)
    await asyncio.gather(*(p.list_expirations_all("SPY") for _ in range(8)))
    assert t.max_in_flight == 2 and p.requests == 8


async def test_symbol_and_expiration_validation():
    p = Terminal().provider()
    with pytest.raises(QuoteUnavailable):
        await p.get_chain("SPY,QQQ", EXP)
    with pytest.raises(QuoteUnavailable):
        await p.get_chain("SPY", "20261218")


def test_factory_and_config():
    s = Settings(app_env="test", options_provider="thetadata", thetadata_data_status="eod",
                 thetadata_base_url="http://theta:25503", options_history_provider="thetadata")
    p = build_options_provider(s)
    assert isinstance(p, ThetaDataProvider) and p.data_status == "eod" and p.base_url == "http://theta:25503"
    assert isinstance(build_history_provider(s), ThetaDataProvider)
    assert build_history_provider(Settings(app_env="test")) is None
    with pytest.raises(ProviderConfigError):
        build_history_provider(Settings(app_env="test", options_history_provider="fmp"))
    with pytest.raises(ValueError):
        ThetaDataProvider(data_status="real-time")
    prod = dict(app_env="production", auth_enabled=True, clerk_issuer="i", clerk_jwks_url="j",
                clerk_authorized_parties=("https://x",), allowed_origins=("https://x",),
                database_url="postgresql://x/y", marketmosaic_base_url="https://mm", anthropic_api_key="k")
    assert not any("OPTIONS" in p for p in validate_for_startup(Settings(**prod, options_provider="thetadata")))
    assert any("OPTIONS_HISTORY_PROVIDER" in p for p in
               validate_for_startup(Settings(**prod, options_provider="tradier", options_history_provider="fmp")))


# ---- library transport (ThetaData's Python library; no Terminal) -------------
import logging  # noqa: E402
import time as _time  # noqa: E402
import zoneinfo  # noqa: E402

import polars as pl  # noqa: E402

from app.providers.options.thetadata import _library_client  # noqa: E402

NYZ = zoneinfo.ZoneInfo("America/New_York")


class NoDataFoundError(Exception):  # same name as thetadata.errors.NoDataFoundError
    pass


class AuthenticationError(Exception):  # same name as thetadata.errors.AuthenticationError
    pass


class RpcError(Exception):
    def __init__(self, name, details=""):
        super().__init__(name)
        self._name = name
        self._details = details

    def details(self):
        return self._details

    def code(self):
        return type("Code", (), {"name": self._name})()


def lib_rows(rows):
    return pl.DataFrame(rows)


class FakeClient:
    def __init__(self, responses=None, raises=None, sleep=0.0):
        self.responses = responses or {}
        self.raises = raises or {}
        self.sleep = sleep
        self.calls: list[tuple[str, dict]] = []

    def __getattr__(self, method):
        def call(**kwargs):
            self.calls.append((method, kwargs))
            if self.sleep:
                _time.sleep(self.sleep)
            if method in self.raises:
                raise self.raises[method]
            if method not in self.responses:
                raise NoDataFoundError(method)
            return lib_rows(self.responses[method])
        return call


def lib_provider(client, **kw):
    made = []

    def factory():
        made.append(1)
        return client

    p = ThetaDataProvider(access="library", client_factory=factory, **kw)
    return p, made


TS = dt.datetime(2026, 11, 6, 15, 59, 59, 805000, tzinfo=NYZ)
GREEKS_ROWS = [
    {"symbol": "SPY", "expiration": dt.date(2026, 12, 18), "strike": 600.0, "right": "CALL", "timestamp": TS,
     "bid": 10.1, "ask": 10.3, "delta": 0.52, "theta": "-0.0217", "vega": 26.3, "rho": 9.1, "implied_vol": 0.18,
     "underlying_price": 600.5},
    {"symbol": "SPY", "expiration": dt.date(2026, 12, 18), "strike": 600.0, "right": "PUT", "timestamp": TS,
     "bid": float("nan"), "ask": 9.2, "delta": -0.48, "theta": "-0.02", "vega": 26.0, "rho": -9.0,
     "implied_vol": 0.19, "underlying_price": 600.5},
]


async def test_library_chain_parses_dataframes_and_passes_dates():
    client = FakeClient({"option_snapshot_greeks_first_order": GREEKS_ROWS,
                         "option_snapshot_open_interest": [{"expiration": dt.date(2026, 12, 18), "strike": 600.0,
                                                            "right": "CALL", "open_interest": 8066, "timestamp": TS}]})
    p, made = lib_provider(client)
    call, put = await p.get_chain("SPY", EXP)
    assert call.bid == 10.1 and call.greeks.theta == -0.0217 and call.open_interest == 8066
    assert call.quote_time == "2026-11-06T15:59:59.805000-05:00" and call.symbol == "SPY   261218C00600000"
    assert put.bid is None and put.mark is None  # NaN price from the library is not a price
    assert _num_noise_free()
    method, kwargs = next(c for c in client.calls if c[0] == "option_snapshot_greeks_first_order")
    assert kwargs == {"symbol": "SPY", "expiration": dt.date(2026, 12, 18)}
    await p.get_chain("SPY", EXP)
    assert made == [1]  # signed in once, reused


async def test_library_history_kwargs_and_rows():
    rows = [{"symbol": "SPY", "expiration": dt.date(2026, 12, 18), "strike": 600.0, "right": "put",
             "timestamp": dt.datetime(2026, 11, 5, 17, 15, tzinfo=NYZ), "bid": 9.0, "ask": 9.2,
             "underlying_price": 600.5}]
    client = FakeClient({"option_history_greeks_eod": rows})
    p, _ = lib_provider(client)
    (r,) = await p.get_eod_options("SPY", EXP, dt.date(2026, 11, 5), dt.date(2026, 11, 6))
    assert (r.date, r.option_type, r.bid, r.underlying_price) == (dt.date(2026, 11, 5), "put", 9.0, 600.5)
    _m, kwargs = client.calls[0]
    assert kwargs == {"symbol": "SPY", "expiration": dt.date(2026, 12, 18), "strike_range": 40,
                      "start_date": dt.date(2026, 11, 5), "end_date": dt.date(2026, 11, 6)}


async def test_library_errors_map_to_the_provider_contract():
    quote_rows = [{k: v for k, v in GREEKS_ROWS[0].items() if k in ("expiration", "strike", "right", "bid", "ask", "timestamp")}]
    c = FakeClient({"option_snapshot_quote": quote_rows},
                   raises={"option_snapshot_greeks_first_order": RpcError("PERMISSION_DENIED")})
    (q,) = (await lib_provider(c)[0].get_chain("SPY", EXP))
    assert q.greeks is None and q.bid == 10.1  # Value-tier fallback works through the library too
    assert await lib_provider(FakeClient())[0].get_eod_options("SPY", EXP, dt.date(2026, 11, 5),
                                                                dt.date(2026, 11, 5)) == []
    for err, exc in [(RpcError("UNAVAILABLE"), ProviderUnavailable), (RpcError("INVALID_ARGUMENT"), QuoteUnavailable),
                     (AuthenticationError("bad"), ProviderUnavailable), (RuntimeError("boom"), ProviderUnavailable)]:
        with pytest.raises(exc):
            await lib_provider(FakeClient(raises={"option_list_expirations": err}))[0].list_expirations_all("SPY")


async def test_expired_session_signs_in_again():
    client = FakeClient(raises={"option_list_expirations": RpcError("UNAUTHENTICATED")})
    p, made = lib_provider(client)
    with pytest.raises(ProviderUnavailable, match="sign in again"):
        await p.list_expirations_all("SPY")
    client.raises.clear()
    client.responses["option_list_expirations"] = [{"symbol": "SPY", "expiration": dt.date(2099, 1, 15)}]
    assert await p.list_expirations_all("SPY") == ["2099-01-15"]
    assert made == [1, 1]


async def test_library_timeout_is_provider_unavailable():
    p, _ = lib_provider(FakeClient({"option_list_expirations": []}, sleep=0.3), timeout_seconds=0.05)
    with pytest.raises(ProviderUnavailable, match="in time"):
        await p.list_expirations_all("SPY")


def test_library_client_requires_credentials_and_silences_its_logger():
    logging.getLogger("thetadata").setLevel(logging.INFO)
    with pytest.raises(ProviderUnavailable, match="not configured"):
        _library_client("", "", "")
    assert logging.getLogger("thetadata").level == logging.WARNING  # its INFO line carries the session id


def test_library_settings_validation_and_redaction():
    prod = dict(app_env="production", auth_enabled=True, clerk_issuer="i", clerk_jwks_url="j",
                clerk_authorized_parties=("https://x",), allowed_origins=("https://x",),
                database_url="postgresql://x/y", marketmosaic_base_url="https://mm", anthropic_api_key="k")
    missing = validate_for_startup(Settings(**prod, options_provider="thetadata"))
    assert any("THETADATA_EMAIL" in m for m in missing)
    ok = Settings(**prod, options_provider="tradier", options_history_provider="thetadata",
                  thetadata_email="me@example.com", thetadata_password="hunter2")
    assert not any("THETADATA" in m or "OPTIONS" in m for m in validate_for_startup(ok))
    assert "hunter2" not in repr(ok) and "me@example.com" not in repr(ok)
    red = ok.redacted()
    assert red["thetadata_password"] == "set" and red["thetadata_api_key"] == "unset"
    p = build_history_provider(ok)
    assert p.access == "library"
    assert any("THETADATA_ACCESS" in m for m in validate_for_startup(Settings(**prod, thetadata_access="cloud")))


def _num_noise_free() -> bool:
    from app.providers.options.thetadata import _num
    return _num(30.110000000000003) == 30.11 and _num(0.000123456789) == 0.00012346 and _num(float("nan")) is None


def slow_sign_in_provider(client, sign_in_seconds, **kw):
    made = []

    def factory():
        made.append(1)
        _time.sleep(sign_in_seconds)
        return client

    return ThetaDataProvider(access="library", client_factory=factory, **kw), made


async def test_sign_in_does_not_count_against_the_request_timeout():
    # Live sign-in took ~15 s against a 15 s request budget: the first request
    # after every cold start failed with "did not answer in time".
    client = FakeClient({"option_list_expirations": [{"symbol": "SPY", "expiration": dt.date(2099, 1, 15)}]})
    p, made = slow_sign_in_provider(client, 0.3, timeout_seconds=0.1, sign_in_timeout_seconds=5)
    assert await p.list_expirations_all("SPY") == ["2099-01-15"]
    assert made == [1]


async def test_concurrent_first_requests_share_one_sign_in():
    client = FakeClient({"option_list_expirations": [{"symbol": "SPY", "expiration": dt.date(2099, 1, 15)}]})
    p, made = slow_sign_in_provider(client, 0.2, max_concurrency=4)
    results = await asyncio.gather(*(p.list_expirations_all("SPY") for _ in range(4)))
    assert results == [["2099-01-15"]] * 4
    assert made == [1]


async def test_sign_in_timeout_is_provider_unavailable():
    p, _ = slow_sign_in_provider(FakeClient(), 0.3, sign_in_timeout_seconds=0.05)
    with pytest.raises(ProviderUnavailable, match="sign-in did not finish"):
        await p.list_expirations_all("SPY")


async def test_warm_up_signs_in_once_and_never_raises():
    client = FakeClient({"option_list_expirations": [{"symbol": "SPY", "expiration": dt.date(2099, 1, 15)}]})
    p, made = lib_provider(client)
    assert await p.warm_up() is True
    await p.list_expirations_all("SPY")
    assert made == [1]  # the request reused the warmed session

    def failing():
        raise RuntimeError("network down")

    broken = ThetaDataProvider(access="library", client_factory=failing)
    assert await broken.warm_up() is False
    assert await ThetaDataProvider(access="terminal").warm_up() is True  # nothing to warm


# Regression: QA 2026-11-09. After hours, ThetaData's greeks snapshots alternated between a
# 0.1 s success and a 10 s "INVALID_ARGUMENT: Expected exactly one underlying STOCK tick; got 0".
# The app called that "rejected the request parameters" and failed the whole page.
TICK = "Expected exactly one underlying STOCK tick; got 0"


class Flaky(FakeClient):
    """Fails the first `fails` calls of each method with the transient tick error."""

    def __init__(self, responses, fails):
        super().__init__(responses)
        self.fails = dict.fromkeys(responses, fails) if isinstance(fails, int) else fails

    def __getattr__(self, method):
        inner = super().__getattr__(method)

        def call(**kwargs):
            if self.fails.get(method, 0) > 0:
                self.fails[method] -= 1
                self.calls.append((method, kwargs))
                raise RpcError("INVALID_ARGUMENT", TICK)
            return inner(**kwargs)
        return call


IV_ROWS = [{"symbol": "SPY", "expiration": dt.date(2099, 1, 15), "strike": 750.0, "right": "CALL",
            "implied_vol": 0.2, "underlying_price": 752.43}]


async def test_transient_underlying_tick_error_is_retried():
    client = Flaky({"option_snapshot_greeks_implied_volatility": IV_ROWS}, fails=2)
    p, _ = lib_provider(client)
    assert await p.get_underlying_price("SPY") == 752.43
    assert len([c for c in client.calls if c[0] == "option_snapshot_greeks_implied_volatility"]) == 3


async def test_persistent_transient_error_is_unavailable_not_bad_parameters():
    from app.providers.options.thetadata import TransientUnavailable
    p, _ = lib_provider(Flaky({"option_snapshot_greeks_implied_volatility": IV_ROWS}, fails=99))
    with pytest.raises(TransientUnavailable, match="try again"):
        await p.get_underlying_price("SPY")
    p, _ = lib_provider(FakeClient(raises={"option_list_expirations": RpcError("INVALID_ARGUMENT", "bad date")}))
    with pytest.raises(QuoteUnavailable, match="rejected"):  # a genuine parameter error stays one
        await p.list_expirations_all("SPY")


async def test_last_good_price_covers_a_vendor_hiccup():
    client = Flaky({"option_snapshot_greeks_implied_volatility": IV_ROWS}, fails=0)
    p, _ = lib_provider(client)
    assert await p.get_underlying_price("SPY") == 752.43
    client.fails["option_snapshot_greeks_implied_volatility"] = 99
    assert await p.get_underlying_price("SPY") == 752.43  # remembered, minutes old
    import app.providers.options.thetadata as T
    later = _time.monotonic() + T.PRICE_MEMORY_SECONDS + 1
    p._clock = lambda: later
    with pytest.raises(ProviderUnavailable):
        await p.get_underlying_price("SPY")


async def test_chain_falls_back_to_quotes_when_greeks_are_down_and_hides_no_bid_iv():
    quote_rows = [{"symbol": "SPY", "expiration": dt.date(2099, 1, 15), "strike": 750.0, "right": "CALL",
                   "bid": 4.0, "ask": 4.2, "timestamp": TS}]
    client = Flaky({"option_snapshot_greeks_first_order": GREEKS_ROWS, "option_snapshot_quote": quote_rows},
                   fails={"option_snapshot_greeks_first_order": 99})
    p, _ = lib_provider(client)
    chain = await p.get_chain("SPY", "2099-01-15")
    assert [(q.strike, q.bid, q.greeks) for q in chain] == [(750.0, 4.0, None)]
    p, _ = lib_provider(FakeClient({"option_snapshot_greeks_first_order": GREEKS_ROWS}))
    by_type = {q.option_type: q for q in await p.get_chain("SPY", "2026-12-18")}
    assert by_type["call"].implied_volatility == 0.18
    no_bid = dict(GREEKS_ROWS[0], bid=0.0, ask=0.01, implied_vol=0.25)
    p, _ = lib_provider(FakeClient({"option_snapshot_greeks_first_order": [no_bid]}))
    [q] = await p.get_chain("SPY", "2026-12-18")
    assert q.implied_volatility is None and q.bid == 0.0  # 0.00/0.01 quote: no meaningful IV


async def test_todays_expiration_drops_out_after_the_close():
    exps = [{"symbol": "SPY", "expiration": dt.date(2026, 11, 9)}, {"symbol": "SPY", "expiration": dt.date(2026, 11, 10)}]
    p, _ = lib_provider(FakeClient({"option_list_expirations": exps}))
    ny = zoneinfo.ZoneInfo("America/New_York")
    assert await p.list_expirations("SPY", now=dt.datetime(2026, 11, 9, 15, 0, tzinfo=ny)) == ["2026-11-09", "2026-11-10"]
    assert await p.list_expirations("SPY", now=dt.datetime(2026, 11, 9, 20, 0, tzinfo=ny)) == ["2026-11-10"]


async def test_daily_bars_split_ranges_over_the_vendors_365_day_limit():
    # Regression: ISSUE-011. Analytics asked for 400 days in one request; ThetaData refused
    # ("max 365 days allowed") and every HV figure read "no price history".
    seen = []

    class Bars(FakeClient):
        def __getattr__(self, method):
            def call(**kw):
                seen.append((kw["start_date"], kw["end_date"]))
                assert (kw["end_date"] - kw["start_date"]).days <= 365
                return lib_rows([{"symbol": "SPY", "close": 700.0 + len(seen),
                                  "last_trade": dt.datetime.combine(kw["end_date"], dt.time(16), NYZ)}])
            return call

    p, _ = lib_provider(Bars())
    bars = await p.get_daily_bars("SPY", dt.date(2025, 10, 5), dt.date(2026, 11, 9))
    assert len(seen) == 2 and seen[0][1] + dt.timedelta(days=1) == seen[1][0]
    assert [d for d, _c in bars] == [seen[0][1], dt.date(2026, 11, 9)]
