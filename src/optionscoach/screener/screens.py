"""Three starter screens built on the filter DSL.

All screens operate on a DataFrame that has at least ``iv`` and ``hv`` (decimal
vols, e.g. 0.20 == 20%), ``iv_rank`` (0..100), and liquidity columns ``volume``
and ``rel_spread`` (spread / mid). Derived helper columns are added as needed.

Vol-point conventions: "10 vol points" == 0.10 in decimal IV/HV space.
"""

from __future__ import annotations

import pandas as pd

from optionscoach.screener.dsl import apply_screen


def _with_derived(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "iv_hv_ratio" not in out.columns:
        out["iv_hv_ratio"] = out["iv"] / out["hv"].replace(0, pd.NA)
    if "iv_hv_gap" not in out.columns:
        out["iv_hv_gap"] = (out["iv"] - out["hv"]).abs()
    return out


def cheap_by_hv(df: pd.DataFrame) -> pd.DataFrame:
    """Options cheap vs realized vol: IV/HV ratio < 0.8 AND IV rank < 30."""
    derived = _with_derived(df)
    return apply_screen(derived, {"iv_hv_ratio": {"lt": 0.8}, "iv_rank": {"lt": 30}})


def high_iv_rank(df: pd.DataFrame) -> pd.DataFrame:
    """Rich premium with a liquidity floor: IV rank > 70, volume >= 100, spread <= 10% of mid."""
    derived = _with_derived(df)
    return apply_screen(
        derived,
        {"iv_rank": {"gt": 70}, "min_volume": 100, "rel_spread": {"le": 0.10}},
    )


def iv_hv_gap(df: pd.DataFrame) -> pd.DataFrame:
    """Vol mispricing: absolute(IV - HV) > 10 vol points (0.10 decimal)."""
    derived = _with_derived(df)
    return apply_screen(derived, {"iv_hv_gap": {"gt": 0.10}})


# Registry so the UI / chatbot can list and run screens by name.
SCREENS = {
    "cheap_by_hv": cheap_by_hv,
    "high_iv_rank": high_iv_rank,
    "iv_hv_gap": iv_hv_gap,
}


def run_screen(name: str, df: pd.DataFrame) -> pd.DataFrame:
    """Run a named screen from :data:`SCREENS`."""
    if name not in SCREENS:
        raise KeyError(f"unknown screen {name!r}; available: {sorted(SCREENS)}")
    return SCREENS[name](df)
