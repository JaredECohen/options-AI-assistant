"""Cross-ticker pricing / IV comparison (T4).

Normalization: every ticker is compared at the same constant maturity (default
30 calendar days, total-variance interpolation, no extrapolation) and at the
money (forward). Rows that cannot be compared like-for-like are kept but
excluded from ranking with an explicit reason:
- currency differs from the comparison currency,
- data status differs from the reference (e.g. synthetic vs delayed),
- as-of time more than `max_asof_skew_minutes` from the newest row,
- the normalized IV is unavailable.
Sorting is deterministic (key descending, None last, ticker ascending as the
tie-break). The output describes relative implied volatility; it makes no claim
that any option is mispriced.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.analytics.vol import Metric, constant_maturity, ratio

NOTE = "Relative implied-volatility comparison at a common maturity and moneyness. Not a valuation or mispricing signal."
SORT_KEYS = {"iv", "hv20", "iv_hv_ratio", "iv_rank", "skew_25d", "ticker"}


@dataclass
class TickerSnapshot:
    ticker: str
    as_of: dt.datetime
    currency: str
    data_status: str
    underlying: float | None
    term: list[tuple[float, float]]  # (dte, atm_iv)
    hv20: Metric = field(default_factory=lambda: Metric(None, "not_computed"))
    iv_rank: Metric = field(default_factory=lambda: Metric(None, "not_computed"))
    skew_25d: Metric = field(default_factory=lambda: Metric(None, "not_computed"))


def compare(snapshots: Sequence[TickerSnapshot], target_dte: float = 30, sort_by: str = "iv",
            currency: str = "USD", max_asof_skew_minutes: float = 60) -> dict:
    if sort_by not in SORT_KEYS:
        raise ValueError(f"sort_by must be one of {sorted(SORT_KEYS)}")
    if not snapshots:
        return {"rows": [], "target_dte": target_dte, "note": NOTE}
    statuses = [s.data_status for s in snapshots]
    reference_status = max(set(statuses), key=lambda st: (statuses.count(st), st != "synthetic", st))
    newest = max(s.as_of for s in snapshots)
    rows = []
    for s in snapshots:
        iv = constant_maturity(s.term, target_dte)
        reasons = []
        if s.currency != currency:
            reasons.append(f"currency {s.currency} != {currency}")
        if s.data_status != reference_status:
            reasons.append(f"data status {s.data_status} != {reference_status}")
        skew_minutes = (newest - s.as_of).total_seconds() / 60
        if skew_minutes > max_asof_skew_minutes:
            reasons.append(f"as-of {int(skew_minutes)} min older than newest")
        if iv.value is None:
            reasons.append(f"iv unavailable ({iv.reason})")
        rows.append({
            "ticker": s.ticker, "as_of": s.as_of.isoformat(), "data_status": s.data_status, "currency": s.currency,
            "underlying": s.underlying, "iv": iv.as_json(), "hv20": s.hv20.as_json(),
            "iv_hv_ratio": ratio(iv, s.hv20).as_json(), "iv_rank": s.iv_rank.as_json(2),
            "skew_25d": s.skew_25d.as_json(), "comparable": not reasons, "excluded_reasons": reasons,
        })

    def key(row):
        if sort_by == "ticker":
            return (0, 0.0, row["ticker"])
        value = row[sort_by]["value"]
        return (0 if value is not None else 1, -(value or 0.0), row["ticker"])

    comparable = sorted([r for r in rows if r["comparable"]], key=key)
    excluded = sorted([r for r in rows if not r["comparable"]], key=lambda r: r["ticker"])
    return {"rows": comparable + excluded, "target_dte": target_dte, "moneyness": "ATM forward",
            "reference_data_status": reference_status, "sorted_by": sort_by, "note": NOTE}
