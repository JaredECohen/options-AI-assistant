"""A small, dict-based filter DSL for screening option/ticker DataFrames.

A filter spec maps column names to conditions. A condition is either a dict of
operators or, for the common case, a ``min_<col>`` / ``max_<col>`` shorthand.

Examples
--------
>>> {"iv_rank": {"lt": 20}, "min_volume": 100}
>>> {"delta": {"between": [-0.35, -0.25]}, "rel_spread": {"le": 0.10}}

Supported operators: ``lt, le, gt, ge, eq, between``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pandas as pd

_OPS: dict[str, Callable[[pd.Series, Any], pd.Series]] = {
    "lt": lambda s, v: s < v,
    "le": lambda s, v: s <= v,
    "gt": lambda s, v: s > v,
    "ge": lambda s, v: s >= v,
    "eq": lambda s, v: s == v,
    "between": lambda s, v: (s >= v[0]) & (s <= v[1]),
}


def _condition_mask(series: pd.Series, condition: Any) -> pd.Series:
    if isinstance(condition, dict):
        mask = pd.Series(True, index=series.index)
        for op, value in condition.items():
            if op not in _OPS:
                raise ValueError(f"unsupported operator {op!r}; valid: {sorted(_OPS)}")
            mask &= _OPS[op](series, value).fillna(False)
        return mask
    # Bare scalar means equality.
    return (series == condition).fillna(False)


def build_mask(df: pd.DataFrame, filters: dict[str, Any]) -> pd.Series:
    """Return a boolean mask selecting rows of ``df`` that satisfy all ``filters``.

    Handles the ``min_<col>`` / ``max_<col>`` shorthands as well as the operator
    dict form. Rows with NaN in a filtered column fail that condition.
    """
    mask = pd.Series(True, index=df.index)
    for key, condition in filters.items():
        if key.startswith("min_"):
            col = key[4:]
            mask &= _condition_mask(df[col], {"ge": condition})
        elif key.startswith("max_"):
            col = key[4:]
            mask &= _condition_mask(df[col], {"le": condition})
        else:
            if key not in df.columns:
                raise KeyError(f"column {key!r} not in DataFrame columns {list(df.columns)}")
            mask &= _condition_mask(df[key], condition)
    return mask


def apply_screen(df: pd.DataFrame, filters: dict[str, Any]) -> pd.DataFrame:
    """Return the subset of ``df`` matching ``filters`` (DSL described above)."""
    return df[build_mask(df, filters)].copy()
