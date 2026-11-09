"""P6: DSL validation and golden outputs over fixed metric fixtures."""
from __future__ import annotations

import pytest

from app.analytics import screener as S

METRICS = {
    "AAA": {"iv_30d": 0.20, "hv20": 0.30, "iv_hv_ratio": 0.667, "iv_rank": 15.0, "skew_25d": 0.03,
            "median_spread_pct": 0.04, "total_open_interest": 50000},
    "BBB": {"iv_30d": 0.45, "hv20": 0.25, "iv_hv_ratio": 1.8, "iv_rank": 92.0, "skew_25d": 0.11,
            "median_spread_pct": 0.06, "total_open_interest": 9000},
    "CCC": {"iv_30d": 0.35, "hv20": 0.34, "iv_hv_ratio": 1.03, "iv_rank": 75.0, "skew_25d": 0.02,
            "median_spread_pct": 0.25, "total_open_interest": 300},
    "DDD": {"iv_30d": 0.30, "hv20": None, "iv_hv_ratio": None, "iv_rank": None, "skew_25d": 0.09,
            "median_spread_pct": 0.05, "total_open_interest": 2000,
            "_reasons": {"hv20": "no_price_history", "iv_hv_ratio": "no_price_history", "iv_rank": "insufficient_history"}},
}
U = ["AAA", "BBB", "CCC", "DDD"]


@pytest.mark.parametrize("preset,expected", [
    ("cheap_by_hv", ["AAA"]),
    ("high_iv_rank", ["BBB"]),  # CCC fails the liquidity floor
    ("iv_hv_gap", ["BBB"]),
    ("skew_dislocation", ["BBB", "DDD"]),
])
def test_golden_presets(preset, expected):
    out = S.run(S.parse({"universe": U}, preset), METRICS)
    assert [m["ticker"] for m in out["matches"]] == expected


def test_unavailable_metric_fails_with_reason_not_zero():
    out = S.run(S.parse({"universe": U, "filters": [{"field": "iv_rank", "op": "<", "value": 50}]}), METRICS)
    assert [m["ticker"] for m in out["matches"]] == ["AAA"]
    ddd = next(r for r in out["rejected"] if r["ticker"] == "DDD")
    assert ddd["failed"] == [{"field": "iv_rank", "reason": "insufficient_history"}]


@pytest.mark.parametrize("bad,msg", [
    ({"universe": ["SPY"], "filters": [{"field": "pe_ratio", "op": "<", "value": 1}]}, "unknown field"),
    ({"universe": ["SPY"], "filters": [{"field": "iv_rank", "op": "~", "value": 1}]}, "unknown operator"),
    ({"universe": ["SPY"], "filters": [{"field": "iv_rank", "op": "<", "value": 1, "x": 1}]}, "exactly field"),
    ({"universe": ["SPY"], "filters": [{"field": "iv_rank", "op": "<", "value": 500}]}, "between 0.0 and 100.0"),
    ({"universe": ["SPY"], "filters": [{"field": "iv_rank", "op": "<", "value": "1; DROP"}]}, "finite number"),
    ({"universe": ["SPY"], "filters": [{"field": "iv_rank", "op": "between", "value": [9, 1]}]}, "low must be"),
    ({"universe": [], "filters": []}, "universe"),
    ({"universe": ["SP Y"]}, "invalid ticker"),
    ({"universe": ["SPY"], "sql": "x"}, "unknown keys"),
    ({"universe": ["SPY"], "version": 2}, "unsupported DSL version"),
    ({"universe": ["SPY"], "limit": 0}, "limit"),
])
def test_rejects_invalid_definitions(bad, msg):
    with pytest.raises(S.ScreenError, match=msg):
        S.parse(bad)


def test_stable_keyset_pagination():
    names = [a + b for a in "ABCDE" for b in "WXYZ"]
    many = {n: {"iv_rank": float(i % 5), "median_spread_pct": 0.01} for i, n in enumerate(names)}
    screen = S.parse({"universe": sorted(many), "sort": {"field": "iv_rank", "direction": "desc"}, "limit": 6})
    seen, cursor = [], None
    while True:
        page = S.run(screen, many, cursor)
        seen += [m["ticker"] for m in page["matches"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == 20 and len(set(seen)) == 20
    ranks = [many[t]["iv_rank"] for t in seen]
    assert ranks == sorted(ranks, reverse=True)
    full = S.run(S.parse({"universe": sorted(many), "sort": {"field": "iv_rank", "direction": "desc"}, "limit": 100}), many)
    assert seen == [m["ticker"] for m in full["matches"]]
    with pytest.raises(S.ScreenError):
        S.run(screen, many, "not-a-cursor")
