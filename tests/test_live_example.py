"""Explain Strategy with a ticker adds a live worked example (QA 2026-11-09, ISSUE-009)."""
from __future__ import annotations

import datetime as dt

import pytest

from app.chat.live_example import SHAPES, live_example, pick_expiration, pick_strikes
from app.providers.options.base import ProviderUnavailable
from app.providers.options.mock import MockOptionsProvider
from tests.helpers import auth, client

NOW = dt.datetime(2026, 11, 9, 20, 0, tzinfo=dt.UTC)


def test_expiry_nearest_thirty_days_but_never_inside_a_week():
    today = dt.date(2026, 11, 9)
    assert pick_expiration(["2026-11-10", "2026-11-13", "2026-12-04", "2026-12-11", "2027-01-15"], today) == "2026-12-11"
    assert pick_expiration(["2026-11-10", "2026-11-13"], today) is None


def test_same_type_legs_never_share_a_strike_on_a_coarse_grid():
    shape = SHAPES["bull call spread"]
    assert pick_strikes(shape, 10.2, {"call": [10.0, 11.0, 12.0], "put": []}) == [10.0, 11.0]
    with pytest.raises(LookupError):
        pick_strikes(shape, 10.2, {"call": [10.0], "put": []})


async def test_every_shape_prices_exactly_on_the_mock_chain():
    p = MockOptionsProvider(today=dt.date(2026, 11, 9))
    for name in SHAPES:
        text = await live_example(name, "SPY", p, now=NOW)
        assert text.startswith("### Live example: SPY") and "Max profit:" in text and "synthetic" in text.lower(), name


def test_explain_strategy_with_a_ticker_shows_the_example_and_survives_a_dead_feed(tmp_path):
    c, keys = client(tmp_path)
    body = {"message": "", "ticker": "SPY", "strategy": "bull call spread", "mode": "structured"}
    text = c.post("/api/chat", json=body, headers=auth(keys)).json()["response_text"]
    assert "Max Profit: Strike width" in text and "### Live example: SPY" in text and "Net debit:" in text

    class Dead(MockOptionsProvider):
        async def get_underlying_price(self, ticker):
            raise ProviderUnavailable("feed down")

    c, keys = client(tmp_path, options_provider=Dead())
    text = c.post("/api/chat", json=body, headers=auth(keys)).json()["response_text"]
    assert "Max Profit: Strike width" in text and "live SPY example is unavailable" in text
