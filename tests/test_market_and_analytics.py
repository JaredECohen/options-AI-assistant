"""T1 chain, T2/T3/T5 payoff via API, T4 compare, P4/P5 analytics via routes."""
from __future__ import annotations

import datetime as dt

import pytest

from app.analytics.chain import AnalyticsConfig, enrich_chain, skew_25d
from app.analytics.compare import TickerSnapshot, compare
from app.analytics.surface import ExpirySlice, build_surface
from app.analytics.vol import Metric
from app.api.routes_market import atm_strike, centered_window, freshness
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


def test_chain_centred_on_atm_with_strikes_each_side(tmp_path):
    c, keys = client(tmp_path)
    exp = c.get("/api/expirations?ticker=SPY", headers=auth(keys)).json()["expirations"][0]
    spot = c.get("/api/quote?ticker=SPY", headers=auth(keys)).json()["price"]
    r = c.get(f"/api/chain?ticker=SPY&expiration={exp}&strikes=3", headers=auth(keys)).json()
    ks = [row["strike"] for row in r["chain"]]
    assert len(ks) == 7 and ks == sorted(ks)
    assert r["atm_strike"] == min(ks, key=lambda k: abs(k - spot)) and ks[3] == r["atm_strike"]
    assert r["atm_basis"] == "underlying" and r["strikes_each_side"] == 3
    assert r["more_below"] is True and r["more_above"] is True and r["has_more"] is True
    assert r["total_strikes"] == 21 and all({"call", "put"} <= set(row) for row in r["chain"])
    calls = c.get(f"/api/chain?ticker=SPY&expiration={exp}&strikes=3&side=call", headers=auth(keys)).json()
    assert [row["strike"] for row in calls["chain"]] == ks and all("put" not in row for row in calls["chain"])
    assert calls["atm_strike"] == r["atm_strike"]
    wide = c.get(f"/api/chain?ticker=SPY&expiration={exp}&strikes=500", headers=auth(keys)).json()
    assert len(wide["chain"]) == 21 and wide["more_below"] is False and wide["has_more"] is False
    assert c.get(f"/api/chain?ticker=SPY&expiration={exp}&strikes=0", headers=auth(keys)).status_code == 422
    assert c.get(f"/api/chain?ticker=SPY&expiration={exp}&strikes=501", headers=auth(keys)).status_code == 422


def _side(bid, ask):
    return {"bid": bid, "ask": ask}


def test_atm_strike_nearest_underlying_then_put_call_parity():
    rows = {k: {"strike": k, "call": _side(c, c + 0.2), "put": _side(p, p + 0.2)}
            for k, c, p in [(95, 7.0, 2.0), (100, 4.0, 4.1), (105, 2.0, 6.5)]}
    assert atm_strike(rows, 101.0) == (100, "underlying")
    assert atm_strike(rows, 102.5) == (100, "underlying")  # a tie goes to the lower strike
    assert atm_strike(rows, None) == (100, "put_call_parity")
    rows[100]["put"] = _side(None, 4.3)  # no two-sided quote at 100 any more
    assert atm_strike(rows, None) == (105, "put_call_parity")
    assert atm_strike({95: {"strike": 95, "call": _side(None, None)}}, None) == (None, None)
    assert atm_strike({}, 100.0) == (None, None)


def test_centered_window_clamps_at_the_edges():
    ks = [90.0, 95.0, 100.0, 105.0, 110.0]
    assert centered_window(ks, 100.0, 1) == (1, 4, 2)
    assert centered_window(ks, 91.0, 2) == (0, 3, 0)
    assert centered_window(ks, 200.0, 2) == (2, 5, 4)
    assert centered_window(ks, 102.0, 10) == (0, 5, 2)
    assert centered_window(ks, None, 1) == (0, 3, None)
    assert centered_window([], 100.0, 3) == (0, 0, None)


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


def test_closing_quotes_are_not_stale_while_the_market_is_closed():
    # Regression: ISSUE-005. At 8 PM ET the 4:15 PM closing chain was badged STALE.
    close = dt.datetime(2026, 11, 9, 21, 15, tzinfo=dt.UTC)  # Mon 16:15 ET
    evening = dt.datetime(2026, 11, 10, 1, 0, tzinfo=dt.UTC)  # Mon 20:00 ET
    f = freshness([close.isoformat()], "live", evening)
    assert f["stale"] is False and f["market_open"] is False and f["age_seconds"] == 13500
    saturday = dt.datetime(2026, 11, 14, 15, 0, tzinfo=dt.UTC)
    fri_close = dt.datetime(2026, 11, 13, 21, 15, tzinfo=dt.UTC)
    assert freshness([fri_close.isoformat()], "live", saturday)["stale"] is False
    assert freshness([close.isoformat()], "live", saturday)["stale"] is True  # Monday's close on Saturday is old
    tuesday_open = dt.datetime(2026, 11, 10, 15, 0, tzinfo=dt.UTC)  # 10:00 ET, market open
    assert freshness([close.isoformat()], "live", tuesday_open)["stale"] is True


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
    assert summary["iv_30d"]["value"] is not None  # bracketing expiries are fetched
    assert summary["hv"]["hv20"]["value"] is not None and r.json()["data_status"] == "synthetic"
    assert "not a market curve" in r.json()["conventions"]["risk_free_rate_source"]
    assert c.get("/api/analytics/SPY/surface", headers=auth(keys)).status_code == 200
    cmp = c.post("/api/compare", json={"tickers": ["SPY", "QQQ"]}, headers=auth(keys))
    assert cmp.status_code == 200 and len(cmp.json()["rows"]) == 2
    scr = c.post("/api/screener/run", json={"preset": "high_iv_rank", "definition": {"universe": ["SPY", "QQQ"]}},
                 headers=auth(keys))
    assert scr.status_code == 200 and scr.json()["matches"] == []
    assert all(any(f["reason"] == "insufficient_history" for f in r["failed"]) for r in scr.json()["rejected"])


def test_snapshot_expiry_selection_brackets_30_days():
    from app.analytics.snapshot import select_expiries
    weekly = [(f"e{d}", float(d)) for d in (7, 14, 21, 28, 35, 42)]
    assert [d for _e, d in select_expiries(weekly)] == [7.0, 28.0, 35.0]
    assert [d for _e, d in select_expiries([("a", 45.0), ("b", 60.0)])] == [45.0, 60.0]  # nothing below: no bracket
    assert select_expiries([]) == []


# ---- QA 2026-11-09 regressions ----------------------------------------------------------

def _flaky_provider(fail_expiry_index: int):
    from app.providers.options.base import ProviderUnavailable
    from app.providers.options.mock import MockOptionsProvider

    class OneBadExpiry(MockOptionsProvider):
        async def get_chain(self, ticker, expiration):
            exps = await self.list_expirations(ticker)
            if expiration == exps[fail_expiry_index]:
                raise ProviderUnavailable("vendor hiccup")
            return await super().get_chain(ticker, expiration)

    return OneBadExpiry()


def test_one_unpriceable_expiry_does_not_blank_analytics(tmp_path):
    # Regression: ISSUE-002. A single expiry ThetaData refused failed the whole Analytics page.
    c, keys = client(tmp_path, options_provider=_flaky_provider(3))
    r = c.get("/api/analytics/SPY", headers=auth(keys))
    assert r.status_code == 200
    term = r.json()["summary"]["term_structure"]
    assert any(t["unavailable_reason"] == "chain_unavailable" for t in term)
    assert any(t["atm_iv"] is not None for t in term)


def test_first_look_queues_the_iv_history_backfill_once(tmp_path):
    # Regression: ISSUE-003. On a single web service nothing scheduled the backfill, so IV rank
    # stayed "insufficient_history" forever.
    from sqlalchemy import select

    from app.db.models import Job

    c, keys = client(tmp_path)
    assert c.get("/api/analytics/SPY", headers=auth(keys)).json()["iv_history"]["status"] == "unavailable"
    c, keys = client(tmp_path, settings_overrides={"options_history_provider": "thetadata"})
    first = c.get("/api/analytics/SPY", headers=auth(keys)).json()["iv_history"]
    c.post("/api/compare", json={"tickers": ["SPY", "QQQ"]}, headers=auth(keys))
    c.post("/api/screener/run", json={"definition": {"universe": ["SPY", "IWM"]}, "preset": "high_iv_rank"},
           headers=auth(keys))
    assert first["status"] == "building" and "15 minutes" in first["detail"]
    with c.app.state.db.session() as s:
        keys_ = sorted(j.idempotency_key for j in s.execute(select(Job).where(Job.kind == "backfill_iv_history")).scalars())
    assert keys_ == ["backfill_iv_history:IWM:full", "backfill_iv_history:QQQ:full", "backfill_iv_history:SPY:full"]


def test_failures_are_reported_as_codes_not_class_names(tmp_path):
    # Regression: ISSUE-004. Compare and Screener showed "(ProviderUnavailable)" / "(QuoteUnavailable)".
    from app.api.routes_analytics import failure_code
    from app.providers.options.base import ProviderUnavailable, QuoteUnavailable
    from app.providers.options.thetadata import TransientUnavailable

    assert failure_code(TransientUnavailable("x")) == "market_data_unavailable"
    assert failure_code(ProviderUnavailable("x")) == "market_data_unavailable"
    assert failure_code(QuoteUnavailable("x")) == "quote_unavailable"
    assert failure_code(RuntimeError("x")) == "error"


def test_surface_spreads_expiries_and_fits_only_liquid_quotes(tmp_path):
    # Regression: ISSUE-012. With SPY dailies the surface's "first four" expiries were all in
    # one week, and 0.01-bid far-wing calls showed up as 46%-IV "dislocations".
    from app.api.routes_analytics import surface_expiries, surface_quality

    daily = [(f"d{i}", float(i)) for i in range(7, 400)]
    assert surface_expiries(daily, 4) == sorted(["d7", "d30", "d60", "d90"])
    assert surface_quality({"bid": 1.0, "spread_pct": 0.05})
    assert not surface_quality({"bid": 0.0, "spread_pct": 2.0})
    assert not surface_quality({"bid": 0.01, "spread_pct": 0.67})
    c, keys = client(tmp_path)
    r = c.get("/api/analytics/SPY/surface", headers=auth(keys))
    assert r.status_code == 200 and r.json()["surface"]
