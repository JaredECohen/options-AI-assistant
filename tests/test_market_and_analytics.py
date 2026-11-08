"""T1 chain, T2/T3/T5 payoff via API, T4 compare, P4/P5 analytics via routes."""
from __future__ import annotations

import datetime as dt

import pytest

from app.analytics.chain import AnalyticsConfig, enrich_chain, skew_25d
from app.analytics.compare import TickerSnapshot, compare
from app.analytics.surface import ExpirySlice, build_surface
from app.analytics.vol import Metric
from app.api.routes_market import freshness
from app.providers.options.base import OptionQuote
from tests.helpers import auth, client


def test_chain_is_paged_labelled_and_never_real_time_for_mock(tmp_path):
    c, keys = client(tmp_path)
    exps = c.get("/api/expirations?ticker=SPY", headers=auth(keys)).json()
    assert exps["data_status"] == "synthetic" and "not market prices" in exps["data_status_label"].lower()
    exp = exps["expirations"][0]
    r = c.get(f"/api/chain?ticker=SPY&expiration={exp}&page_size=5", headers=auth(keys)).json()
    assert len(r["chain"]) == 5 and r["has_more"] is True and r["total_strikes"] == 21
    row = r["chain"][0]
    assert {"call", "put", "strike"} <= set(row)
    assert row["call"]["multiplier"] == 100 and row["call"]["greeks"] is None  # not fabricated
    assert r["units"]["prices"] == "USD per share"
    page2 = c.get(f"/api/chain?ticker=SPY&expiration={exp}&page_size=5&page=2", headers=auth(keys)).json()
    assert page2["chain"][0]["strike"] > r["chain"][-1]["strike"]
    puts = c.get(f"/api/chain?ticker=SPY&expiration={exp}&side=put&page_size=5", headers=auth(keys)).json()
    assert all("call" not in row for row in puts["chain"])


def test_chain_input_validation(tmp_path):
    c, keys = client(tmp_path)
    assert c.get("/api/chain?ticker=SPY;DROP&expiration=2026-12-18", headers=auth(keys)).status_code == 422
    assert c.get("/api/chain?ticker=SPY&expiration=12/18/2026", headers=auth(keys)).status_code == 422
    assert c.get("/api/chain?ticker=SPY&expiration=2026-12-18&page_size=1000", headers=auth(keys)).status_code == 422


def test_freshness_flags_stale_by_declared_status():
    now = dt.datetime(2026, 11, 6, 20, 0, tzinfo=dt.UTC)
    old = (now - dt.timedelta(hours=2)).isoformat()
    assert freshness([old], "delayed", now)["stale"] is True
    assert freshness([old], "eod", now)["stale"] is False
    assert freshness([old], "synthetic", now)["stale"] is None
    assert freshness([], "live", now)["as_of"] is None


def test_payoff_endpoint_t5_example_and_quote_pricing(tmp_path):
    c, keys = client(tmp_path)
    leg = {"instrument": "option", "option_type": "put", "side": "sell", "strike": 100, "expiration": "2026-12-18", "premium": 5}
    r = c.post("/api/payoff", json={"ticker": "SPY", "legs": [leg]}, headers=auth(keys))
    assert r.status_code == 200 and r.json()["computed"]["max_loss"] == 9500.0
    exp = c.get("/api/expirations?ticker=SPY", headers=auth(keys)).json()["expirations"][0]
    quote = c.get(f"/api/chain?ticker=SPY&expiration={exp}&page_size=5", headers=auth(keys)).json()["chain"][2]
    unpriced = {"instrument": "option", "option_type": "call", "side": "buy", "strike": quote["strike"], "expiration": exp}
    r = c.post("/api/payoff", json={"ticker": "SPY", "legs": [unpriced], "fill_convention": "conservative"}, headers=auth(keys))
    used = r.json()["computed"]["premiums_used"][0]
    assert used["premium"] == quote["call"]["ask"] and used["data_status"] == "synthetic"
    mixed = [leg, {**leg, "expiration": "2027-01-15", "strike": 90}]
    r = c.post("/api/payoff", json={"ticker": "SPY", "legs": mixed}, headers=auth(keys))
    assert r.status_code == 422 and r.json()["error"] == "mixed_expirations"
    bogus = {**unpriced, "strike": 1.23}
    r = c.post("/api/payoff", json={"ticker": "SPY", "legs": [bogus]}, headers=auth(keys))
    assert r.status_code == 422 and r.json()["error"] == "quote_unavailable"


def test_chat_payoff_never_assumes_zero_premium(tmp_path):
    c, keys = client(tmp_path)
    leg = {"instrument": "option", "option_type": "put", "side": "sell", "strike": 100, "expiration": "2026-12-18"}
    r = c.post("/api/chat", json={"message": "compute payoff", "legs": [leg]}, headers=auth(keys))
    assert r.status_code == 200
    assert r.json()["computed"] == {"error": "premium_missing"} and "zero premium" in r.json()["response_text"]


def test_market_data_unavailable_is_a_controlled_503(tmp_path):
    from app.providers.options.tradier import TradierProvider
    c, keys = client(tmp_path, options_provider=TradierProvider(api_token=""))
    r = c.get("/api/quote?ticker=SPY", headers=auth(keys))
    assert r.status_code == 503 and r.json()["error"] == "market_data_unavailable"


def _chain(S=100.0, exp="2026-12-18", vol_put=0.30, vol_call=0.22):
    from app.analytics import bsm
    now = dt.datetime(2026, 11, 6, 15, 0, tzinfo=dt.UTC)
    from app.analytics.chain import time_to_expiry
    T = time_to_expiry(exp, now)
    quotes = []
    for K in range(80, 121, 5):
        for typ, sig in (("put", vol_put), ("call", vol_call)):
            p = float(bsm.price(S, K, T, 0.04, 0.0, sig, typ == "call"))
            quotes.append(OptionQuote(typ, float(K), exp, round(p - 0.01, 4), round(p + 0.01, 4), None, None, symbol=f"X{K}{typ}"))
    return quotes, now


def test_enrich_chain_recovers_iv_and_skew_sign():
    quotes, now = _chain()
    enriched = enrich_chain(quotes, 100.0, "2026-12-18", now, AnalyticsConfig())
    puts = [e for e in enriched if e["option_type"] == "put" and e["iv_model"]]
    assert puts and all(abs(e["iv_model"] - 0.30) < 0.02 for e in puts if 85 <= e["strike"] <= 110)
    assert skew_25d(enriched).value > 0  # put vol > call vol
    crossed = OptionQuote("call", 100.0, "2026-12-18", 3.0, 2.0, None, None)
    e = enrich_chain([crossed], 100.0, "2026-12-18", now, AnalyticsConfig())[0]
    assert e["iv_model"] is None and e["iv_model_unavailable_reason"] == "crossed_quote" and e["greeks_model"] is None


def test_surface_separates_observed_and_fitted_and_flags_dislocation():
    pts = [(k / 10, 0.25 + 0.1 * (k / 10) ** 2, "call", 100 + k) for k in range(-3, 4)]
    bumped = pts[:3] + [(0.0, 0.40, "call", 100.0)] + pts[4:]
    s = build_surface([ExpirySlice("2026-12-18", 30, bumped), ExpirySlice("2027-01-15", 60, pts)])
    assert any(d["k"] == 0.0 and d["direction"] == "rich_vs_fit" for d in s["dislocations"])
    assert all("iv_observed" in o and "iv_fitted" in o for o in s["observed"])
    assert "Not arbitrage-free" in s["method"]
    # never extrapolated outside the observed k range
    wide = build_surface([ExpirySlice("2026-12-18", 30, pts)], k_grid=[-0.5, 0.0, 0.5])
    assert wide["fitted"][0]["iv"][0] is None and wide["fitted"][0]["iv"][2] is None
    assert wide["fitted"][0]["iv"][1] == pytest.approx(0.25, abs=1e-6)
    assert s["interpolated"] and s["interpolated"][0]["kind"] == "interpolated_total_variance"


def test_compare_normalizes_excludes_and_sorts_deterministically():
    now = dt.datetime(2026, 11, 6, 20, 0, tzinfo=dt.UTC)
    snaps = [
        TickerSnapshot("BBB", now, "USD", "delayed", 50, [(20, 0.30), (40, 0.30)], Metric(0.20)),
        TickerSnapshot("AAA", now, "USD", "delayed", 50, [(20, 0.30), (40, 0.30)], Metric(0.25)),
        TickerSnapshot("CCC", now, "USD", "delayed", 50, [(20, 0.50), (40, 0.50)], Metric(None, "no_price_history")),
        TickerSnapshot("EUR", now, "EUR", "delayed", 50, [(20, 0.9), (40, 0.9)]),
        TickerSnapshot("SYN", now, "USD", "synthetic", 50, [(20, 0.9), (40, 0.9)]),
        TickerSnapshot("OLD", now - dt.timedelta(hours=3), "USD", "delayed", 50, [(20, 0.9), (40, 0.9)]),
        TickerSnapshot("SHORT", now, "USD", "delayed", 50, [(40, 0.3), (60, 0.3)]),
    ]
    out = compare(snaps, 30, "iv")
    order = [r["ticker"] for r in out["rows"]]
    assert order[:3] == ["CCC", "AAA", "BBB"]  # by iv desc, ticker tie-break
    excluded = {r["ticker"]: r["excluded_reasons"] for r in out["rows"] if not r["comparable"]}
    assert set(excluded) == {"EUR", "SYN", "OLD", "SHORT"}
    assert "mispricing" in out["note"].lower()
    ratio = next(r for r in out["rows"] if r["ticker"] == "AAA")["iv_hv_ratio"]["value"]
    assert ratio == pytest.approx(0.30 / 0.25, rel=1e-4)
    ccc = next(r for r in out["rows"] if r["ticker"] == "CCC")
    assert ccc["iv_hv_ratio"] == {"value": None, "unavailable_reason": "no_price_history"}


def test_analytics_routes_end_to_end_on_synthetic_data(tmp_path):
    c, keys = client(tmp_path)
    r = c.get("/api/analytics/SPY", headers=auth(keys))
    assert r.status_code == 200
    summary = r.json()["summary"]
    assert summary["iv_rank"]["unavailable_reason"] == "insufficient_history"  # no stored history, no backfill
    assert summary["hv"]["hv20"]["value"] is not None and r.json()["data_status"] == "synthetic"
    assert "not a market curve" in r.json()["conventions"]["risk_free_rate_source"]
    assert c.get("/api/analytics/SPY/surface", headers=auth(keys)).status_code == 200
    cmp = c.post("/api/compare", json={"tickers": ["SPY", "QQQ"]}, headers=auth(keys))
    assert cmp.status_code == 200 and len(cmp.json()["rows"]) == 2
    scr = c.post("/api/screener/run", json={"preset": "high_iv_rank", "definition": {"universe": ["SPY", "QQQ"]}},
                 headers=auth(keys))
    assert scr.status_code == 200 and scr.json()["matches"] == []
    assert all(any(f["reason"] == "insufficient_history" for f in r["failed"]) for r in scr.json()["rejected"])
