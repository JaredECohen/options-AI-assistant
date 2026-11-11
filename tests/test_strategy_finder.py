"""Strategy finder: view parsing, candidate construction and ranking, the route, and the
hand-off inputs for the builder and backtester. Prices come from a Black-Scholes chain so the
numbers are checkable."""
from __future__ import annotations

import datetime as dt

import pytest

from app.analytics import bsm
from app.analytics.chain import AnalyticsConfig, time_to_expiry
from app.backtest.spec import BacktestRequest
from app.jobs.iv_backfill import close_time
from app.providers.options.base import OptionQuote
from app.providers.options.mock import MockOptionsProvider
from app.strategy.finder import find_strategies, pick_expiration
from app.strategy.view import View, interpret, parse_rules, view_from_model
from tests.helpers import auth, client

NOW = dt.datetime(2026, 11, 9, 21, 30, tzinfo=dt.UTC)  # Mon 16:30 ET
TODAY = dt.date(2026, 11, 9)
CFG = AnalyticsConfig()


class BSChain:
    """SPY-like chain: $1 strikes, Black-Scholes prices at 16% vol, 1% wide quotes."""

    name = "bs-test"
    data_status = "eod"

    def __init__(self, spot=750.0, vol=0.16):
        self.spot, self.vol = spot, vol

    async def get_underlying_price(self, ticker):
        return self.spot

    async def list_expirations(self, ticker):
        return [(TODAY + dt.timedelta(days=d)).isoformat() for d in (1, 4, 11, 18, 25, 32, 39, 46, 74, 102)]

    async def get_chain(self, ticker, expiration):
        T = time_to_expiry(expiration, close_time(TODAY))
        out = []
        for k in range(600, 901):
            for t in ("call", "put"):
                p = float(bsm.price(self.spot, k, T, CFG.risk_free_rate, 0.0, self.vol, t == "call"))
                if p < 0.05:
                    continue
                out.append(OptionQuote(option_type=t, strike=float(k), expiration=expiration, bid=round(p * 0.995, 2),
                                       ask=round(p * 1.005 + 0.01, 2), last=None, mark=None, symbol="X", volume=1,
                                       open_interest=1, implied_volatility=self.vol, iv_source="t", greeks=None,
                                       quote_time=None, multiplier=100, adjusted=False))
        return out


def test_rules_parse_common_phrasings():
    v, ok = parse_rules("I think SPY goes up about 5% by mid-December but I want limited downside", 752, TODAY)
    assert ok and v.direction == "bullish" and v.move_pct == 5 and v.horizon_days == 36 and v.defined_risk_only
    v, _ = parse_rules("bearish on QQQ over the next 3 weeks, willing to risk $500", 680, TODAY)
    assert (v.direction, v.horizon_days, v.max_loss) == ("bearish", 21, 500)
    v, _ = parse_rules("SPY to 790 by Dec 18", 752, TODAY)
    assert (v.direction, v.target_price, v.horizon_days) == ("bullish", 790, 39)
    assert parse_rules("big move after earnings, either way", 752, TODAY)[0].direction == "volatile"
    assert parse_rules("stays in a range, undefined risk is fine", 752, TODAY)[0].defined_risk_only is False
    v, ok = parse_rules("short-term bullish, 2 weeks", 752, TODAY)
    assert ok and (v.direction, v.horizon_days) == ("bullish", 14)  # 'short' is not a bearish view
    assert parse_rules("I own 100 shares and want income", 752, TODAY)[0].owns_shares
    assert parse_rules("what should I do with tesla", 752, TODAY)[1] is False


async def test_model_fills_in_only_when_rules_cannot_and_bad_fields_are_ignored():
    class LLM:
        calls = 0

        async def extract(self, *a, **k):
            LLM.calls += 1
            return {"direction": "bearish", "target_price": None, "move_pct": -8, "horizon_days": 9999,
                    "defined_risk_only": True, "max_loss": None, "owns_shares": False}

    view, by = await interpret("tesla looks toppy into the new year", 300, LLM(), TODAY)
    assert by == "model" and view.direction == "bearish" and view.move_pct == -8 and view.horizon_days == 30
    view, by = await interpret("SPY up 5% in 6 weeks", 752, LLM(), TODAY)
    assert by == "rules" and LLM.calls == 1
    assert view_from_model({"direction": "sideways"}, View()).direction is None


def test_expiry_on_or_after_the_horizon():
    exps = ["2026-11-10", "2026-11-20", "2026-12-11", "2026-12-18"]
    assert pick_expiration(exps, 36, TODAY) == "2026-12-18"
    assert pick_expiration(exps, 30, TODAY) == "2026-12-11"
    assert pick_expiration(exps, 400, TODAY) == "2026-12-18"


async def test_bullish_view_ranks_a_spread_to_the_target_first_with_exact_numbers():
    r = await find_strategies(View(direction="bullish", move_pct=5, horizon_days=36), "SPY", BSChain(), now=NOW)
    top = r["candidates"][0]
    assert top["key"] == "bull_call_spread" and r["target_price"] == 787.5
    long_k, short_k = (leg["strike"] for leg in top["legs"])
    assert long_k == 750 and short_k == 787  # 787.50 target: a tie goes to the lower strike
    m = top["metrics"]
    width = (short_k - long_k) * 100
    assert m["max_profit"] == pytest.approx(width - m["net_debit"]) and m["max_loss"] == m["net_debit"]
    assert m["pnl_at_view"] == pytest.approx(m["max_profit"], abs=0.01)
    assert 0 < m["probability_of_profit"] < 1 and m["capital"] == m["net_debit"]
    assert {c["key"] for c in r["candidates"]} <= {"bull_call_spread", "long_call", "bull_put_spread", "cash_secured_put"}


async def test_limits_exclude_with_reasons():
    r = await find_strategies(View(direction="neutral", horizon_days=30), "SPY", BSChain(), now=NOW)
    assert "short_strangle" not in {c["key"] for c in r["candidates"]}
    assert any("undefined risk" in e["reason"] for e in r["excluded"])
    r = await find_strategies(View(direction="bullish", horizon_days=30, max_loss=300), "SPY", BSChain(), now=NOW)
    assert all(float(c["metrics"]["max_loss"]) <= 300 for c in r["candidates"])
    assert any("above your" in e["reason"] for e in r["excluded"])


async def test_volatile_offers_only_two_sided_structures_and_shareholders_get_covered_calls():
    r = await find_strategies(View(direction="volatile", horizon_days=21), "SPY", BSChain(), now=NOW)
    assert {c["key"] for c in r["candidates"]} == {"long_straddle", "long_strangle"}
    r = await find_strategies(View(direction="income", horizon_days=30, owns_shares=True), "SPY", BSChain(), now=NOW)
    assert r["candidates"][0]["key"] == "covered_call" and "shares you hold" in r["candidates"][0]["explanation"]["fit"]


async def test_every_candidate_opens_in_the_builder_and_the_backtester():
    for direction in ("bullish", "bearish", "neutral", "volatile", "income"):
        r = await find_strategies(View(direction=direction, horizon_days=30, defined_risk_only=False), "SPY",
                                  BSChain(), now=NOW)
        assert r["candidates"], direction
        for c in r["candidates"]:
            assert c["links"]["builder"]["legs"] == c["legs"]
            BacktestRequest.model_validate(c["links"]["backtest"])  # a valid request, as sent
            assert c["links"]["chain"]["expiration"] == r["expiration"]


def test_route_interprets_text_and_asks_when_it_cannot(tmp_path):
    c, keys = client(tmp_path, options_provider=MockOptionsProvider(today=TODAY))
    r = c.post("/api/strategies/suggest", json={"ticker": "spy", "text": "what about tesla"}, headers=auth(keys))
    assert r.status_code == 200 and r.json()["needs_direction"] and r.json()["parsed_by"] == "none"
    r = c.post("/api/strategies/suggest", json={"ticker": "SPY", "text": "",
                                                "view": {"direction": "bullish", "horizon_days": 30}}, headers=auth(keys))
    body = r.json()
    assert r.status_code == 200 and body["parsed_by"] == "edited" and body["interpreted"]["direction"] == "bullish"
    assert c.post("/api/strategies/suggest", json={"ticker": "SPY;", "text": "x"}, headers=auth(keys)).status_code == 422


def test_extract_schema_is_accepted_by_strict_tool_mode():
    # Found live 2026-11-09: strict mode rejected an enum next to a ["string", "null"] type list
    # ("Enum value 'bullish' does not match declared type"), so the model fallback never ran.
    from app.llm.tools import STRICT_UNSUPPORTED_KEYWORDS
    from app.strategy.view import EXTRACT_SCHEMA

    def walk(node):
        if isinstance(node, dict):
            assert not ("enum" in node and isinstance(node.get("type"), list)), node
            assert not (set(node) & STRICT_UNSUPPORTED_KEYWORDS), node
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(EXTRACT_SCHEMA)
    assert set(EXTRACT_SCHEMA["required"]) == set(EXTRACT_SCHEMA["properties"])
