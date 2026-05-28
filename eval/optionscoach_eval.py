"""Deterministic eval checks for the OptionsCoach analytics/screener/backtester.

These are fully offline and reproducible (synthetic data, fixed seeds). They are
imported and run by ``eval/run_eval.py`` and can also be run standalone::

    uv run python eval/optionscoach_eval.py
"""

from __future__ import annotations

import os
import sys
from datetime import date

# Make src/ and the vendored engine importable when run directly.
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for _p in (os.path.join(_ROOT, "src"), os.path.join(_ROOT, "backtesting-engine", "src"), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pandas as pd  # noqa: E402
from backtesting_engine import SyntheticOptionsAdapter  # noqa: E402
from backtesting_engine.pricing import bs_price  # noqa: E402

from optionscoach.analytics import implied_vol_df  # noqa: E402
from optionscoach.backtester import ShortPut, StrategyConfig, run_strategy  # noqa: E402
from optionscoach.screener import cheap_by_hv, high_iv_rank, iv_hv_gap  # noqa: E402


def check_iv_computation() -> tuple[bool, str]:
    """IV solved from a known BS price must match the input vol within tolerance."""
    price = bs_price(100, 100, 30 / 365, 0.0, 0.20, "call")
    chain = pd.DataFrame({"strike": [100.0], "type": ["call"], "mark": [price], "t": [30 / 365]})
    solved = implied_vol_df(chain, spot=100.0, price_col="mark")
    iv = float(solved["iv_solved"].iloc[0])
    ok = abs(iv - 0.20) < 1e-3
    return ok, f"iv_computation: solved IV={iv:.5f} (expected 0.20), ok={ok}"


def check_screener_fixed_chain() -> tuple[bool, str]:
    """Screens over a fixed synthetic-derived chain return the expected counts."""
    df = pd.DataFrame(
        {
            "iv": [0.15, 0.45, 0.30, 0.18, 0.50],
            "hv": [0.25, 0.20, 0.32, 0.30, 0.20],
            "iv_rank": [20.0, 80.0, 50.0, 10.0, 95.0],
            "volume": [500, 150, 80, 1200, 50],
            "rel_spread": [0.05, 0.05, 0.30, 0.04, 0.26],
        }
    )
    ok = len(cheap_by_hv(df)) == 2 and len(high_iv_rank(df)) == 1 and len(iv_hv_gap(df)) == 3
    return ok, (
        f"screener: cheap_by_hv={len(cheap_by_hv(df))} (exp 2), "
        f"high_iv_rank={len(high_iv_rank(df))} (exp 1), "
        f"iv_hv_gap={len(iv_hv_gap(df))} (exp 3), ok={ok}"
    )


def check_backtest_shape() -> tuple[bool, str]:
    """A short-put backtest returns a well-formed Result with all metrics."""
    config = StrategyConfig(symbol="SPY", target_delta=0.30, dte=30)
    source = SyntheticOptionsAdapter(start_price=400.0, seed=7)
    result = run_strategy(
        ShortPut, date(2020, 1, 1), date(2020, 12, 31), config=config, data_source=source
    )
    expected_metrics = {
        "total_return",
        "cagr",
        "sharpe",
        "max_drawdown",
        "win_rate",
        "profit_factor",
    }
    ok = (
        not result.equity_curve.empty
        and len(result.trade_log) > 0
        and expected_metrics.issubset(result.metrics)
        and result.final_equity > 0
    )
    return ok, (
        f"backtest_shape: trades={len(result.trade_log)}, "
        f"metrics_keys={len(result.metrics)}, final_equity={result.final_equity:.0f}, ok={ok}"
    )


CHECKS = {
    "iv_computation": check_iv_computation,
    "screener": check_screener_fixed_chain,
    "backtest_shape": check_backtest_shape,
}


def run_all() -> bool:
    """Run every deterministic check; print results and return overall pass/fail."""
    print("\nOptionsCoach deterministic evals:")
    all_ok = True
    for fn in CHECKS.values():
        ok, msg = fn()
        all_ok = all_ok and ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {msg}")
    print(f"OptionsCoach evals overall: {'PASS' if all_ok else 'FAIL'}")
    return all_ok


if __name__ == "__main__":
    sys.exit(0 if run_all() else 1)
