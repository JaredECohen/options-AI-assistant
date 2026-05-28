"""Tests for the screener DSL and the three starter screens (golden outputs)."""

from __future__ import annotations

import pandas as pd
import pytest

from optionscoach.analytics import add_liquidity_metrics
from optionscoach.screener import apply_screen, cheap_by_hv, high_iv_rank, iv_hv_gap, run_screen


@pytest.fixture
def universe() -> pd.DataFrame:
    """A small, fixed candidate universe (golden fixture).

    iv/hv are decimal vols; iv_rank is 0..100; bid/ask drive liquidity metrics.
    """
    df = pd.DataFrame(
        {
            "ticker": ["AAA", "BBB", "CCC", "DDD", "EEE"],
            "iv": [0.15, 0.45, 0.30, 0.18, 0.50],
            "hv": [0.25, 0.20, 0.32, 0.30, 0.20],
            "iv_rank": [20.0, 80.0, 50.0, 10.0, 95.0],
            "volume": [500, 150, 80, 1200, 50],
            "bid": [1.00, 2.00, 0.50, 1.50, 3.00],
            "ask": [1.05, 2.10, 0.70, 1.56, 3.90],
        }
    )
    return add_liquidity_metrics(df)


def test_dsl_operators() -> None:
    df = pd.DataFrame({"x": [1, 2, 3, 4, 5]})
    assert apply_screen(df, {"x": {"gt": 3}})["x"].tolist() == [4, 5]
    assert apply_screen(df, {"x": {"between": [2, 4]}})["x"].tolist() == [2, 3, 4]
    assert apply_screen(df, {"x": {"le": 2}})["x"].tolist() == [1, 2]
    assert apply_screen(df, {"min_x": 4})["x"].tolist() == [4, 5]
    assert apply_screen(df, {"max_x": 2})["x"].tolist() == [1, 2]
    assert apply_screen(df, {"x": 3})["x"].tolist() == [3]


def test_dsl_unknown_operator_raises() -> None:
    with pytest.raises(ValueError):
        apply_screen(pd.DataFrame({"x": [1]}), {"x": {"approx": 1}})


def test_dsl_unknown_column_raises() -> None:
    with pytest.raises(KeyError):
        apply_screen(pd.DataFrame({"x": [1]}), {"y": {"gt": 0}})


def test_cheap_by_hv_golden(universe: pd.DataFrame) -> None:
    # IV/HV < 0.8 AND iv_rank < 30: AAA (0.6, 20) and DDD (0.6, 10) qualify.
    result = cheap_by_hv(universe)
    assert sorted(result["ticker"]) == ["AAA", "DDD"]


def test_high_iv_rank_golden(universe: pd.DataFrame) -> None:
    # iv_rank > 70, volume >= 100, rel_spread <= 0.10.
    # BBB: rank 80, vol 150, spread 0.10/2.05 ~ 0.049 -> qualifies.
    # EEE: rank 95 but spread 0.90/3.45 ~ 0.26 -> fails liquidity.
    result = high_iv_rank(universe)
    assert result["ticker"].tolist() == ["BBB"]


def test_iv_hv_gap_golden(universe: pd.DataFrame) -> None:
    # abs(iv - hv) > 0.10: BBB(0.25), DDD(0.12), EEE(0.30) qualify.
    result = iv_hv_gap(universe)
    assert sorted(result["ticker"]) == ["BBB", "DDD", "EEE"]


def test_run_screen_registry(universe: pd.DataFrame) -> None:
    assert sorted(run_screen("cheap_by_hv", universe)["ticker"]) == ["AAA", "DDD"]
    with pytest.raises(KeyError):
        run_screen("does_not_exist", universe)
