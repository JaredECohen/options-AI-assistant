"""Screener DSL v1 (P6).

A definition is plain JSON, validated strictly (unknown keys, fields or
operators are rejected, never ignored):

    {"version": 1,
     "universe": ["SPY", "QQQ"],                 # 1..25 tickers
     "filters": [{"field": "iv_rank", "op": ">=", "value": 70}],   # 0..12
     "sort": {"field": "iv_rank", "direction": "desc"},
     "limit": 25}

A ticker whose metric is unavailable FAILS that filter and is reported with
the reason; it is never treated as zero. Pagination is keyset-based on
(sort value, ticker), so pages are stable for a given result set.
"""
from __future__ import annotations

import base64
import json
import math
import re
from dataclasses import dataclass

DSL_VERSION = 1
MAX_UNIVERSE = 25
MAX_FILTERS = 12
TICKER_RE = re.compile(r"^[A-Z.]{1,10}$")

# field -> (description, min, max)
FIELDS: dict[str, tuple[str, float, float]] = {
    "iv_30d": ("30-day constant-maturity ATM IV (decimal)", 0.0, 10.0),
    "hv20": ("20-day close-to-close HV (decimal)", 0.0, 10.0),
    "hv60": ("60-day close-to-close HV (decimal)", 0.0, 10.0),
    "iv_hv_ratio": ("iv_30d / hv20", 0.0, 100.0),
    "iv_rank": ("IV rank over 252 observations, 0-100", 0.0, 100.0),
    "iv_percentile": ("IV percentile over 252 observations, 0-100", 0.0, 100.0),
    "skew_25d": ("25-delta put IV minus 25-delta call IV (decimal)", -5.0, 5.0),
    "median_spread_pct": ("median (ask-bid)/mid across the nearest expiry", 0.0, 10.0),
    "total_volume": ("contracts traded, nearest expiry", 0.0, 1e12),
    "total_open_interest": ("open interest, nearest expiry", 0.0, 1e12),
    "underlying_price": ("underlying price (USD)", 0.0, 1e7),
}
OPS = {"<", "<=", ">", ">=", "between"}

PRESETS: dict[str, dict] = {
    "cheap_by_hv": {"filters": [{"field": "iv_hv_ratio", "op": "<=", "value": 0.9},
                                {"field": "median_spread_pct", "op": "<=", "value": 0.10}],
                    "sort": {"field": "iv_hv_ratio", "direction": "asc"}},
    "high_iv_rank": {"filters": [{"field": "iv_rank", "op": ">=", "value": 70},
                                 {"field": "median_spread_pct", "op": "<=", "value": 0.10}],
                     "sort": {"field": "iv_rank", "direction": "desc"}},
    "iv_hv_gap": {"filters": [{"field": "iv_hv_ratio", "op": ">=", "value": 1.3},
                              {"field": "median_spread_pct", "op": "<=", "value": 0.10}],
                  "sort": {"field": "iv_hv_ratio", "direction": "desc"}},
    "skew_dislocation": {"filters": [{"field": "skew_25d", "op": ">=", "value": 0.08},
                                     {"field": "total_open_interest", "op": ">=", "value": 1000}],
                         "sort": {"field": "skew_25d", "direction": "desc"}},
}


class ScreenError(ValueError):
    pass


@dataclass(frozen=True)
class Filter:
    field: str
    op: str
    value: float | tuple[float, float]

    def test(self, x: float | None) -> bool:
        if x is None or not math.isfinite(x):
            return False
        if self.op == "between":
            lo, hi = self.value  # type: ignore[misc]
            return lo <= x <= hi
        v = float(self.value)  # type: ignore[arg-type]
        return {"<": x < v, "<=": x <= v, ">": x > v, ">=": x >= v}[self.op]


@dataclass(frozen=True)
class Screen:
    universe: tuple[str, ...]
    filters: tuple[Filter, ...]
    sort_field: str
    sort_desc: bool
    limit: int

    def canonical(self) -> dict:
        return {"version": DSL_VERSION, "universe": list(self.universe),
                "filters": [{"field": f.field, "op": f.op, "value": list(f.value) if isinstance(f.value, tuple) else f.value}
                            for f in self.filters],
                "sort": {"field": self.sort_field, "direction": "desc" if self.sort_desc else "asc"},
                "limit": self.limit}


def _num(value, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ScreenError(f"value for {field} must be a finite number")
    _desc, lo, hi = FIELDS[field]
    if not lo <= value <= hi:
        raise ScreenError(f"value for {field} must be between {lo} and {hi}")
    return float(value)


def parse(definition: dict, preset: str | None = None) -> Screen:
    if not isinstance(definition, dict):
        raise ScreenError("definition must be an object")
    if preset:
        if preset not in PRESETS:
            raise ScreenError(f"unknown preset {preset!r}")
        definition = {**PRESETS[preset], **{k: v for k, v in definition.items() if k in ("universe", "limit", "version")}}
    allowed = {"version", "universe", "filters", "sort", "limit"}
    extra = set(definition) - allowed
    if extra:
        raise ScreenError(f"unknown keys: {sorted(extra)}")
    if definition.get("version", DSL_VERSION) != DSL_VERSION:
        raise ScreenError(f"unsupported DSL version {definition.get('version')!r}")
    universe = definition.get("universe")
    if not isinstance(universe, list) or not 1 <= len(universe) <= MAX_UNIVERSE:
        raise ScreenError(f"universe must list 1-{MAX_UNIVERSE} tickers")
    tickers = []
    for t in universe:
        if not isinstance(t, str) or not TICKER_RE.match(t.upper()):
            raise ScreenError(f"invalid ticker {t!r}")
        if t.upper() not in tickers:
            tickers.append(t.upper())
    raw_filters = definition.get("filters", [])
    if not isinstance(raw_filters, list) or len(raw_filters) > MAX_FILTERS:
        raise ScreenError(f"filters must be a list of at most {MAX_FILTERS}")
    filters = []
    for f in raw_filters:
        if not isinstance(f, dict) or set(f) != {"field", "op", "value"}:
            raise ScreenError("each filter needs exactly field, op and value")
        field, op, value = f["field"], f["op"], f["value"]
        if field not in FIELDS:
            raise ScreenError(f"unknown field {field!r}")
        if op not in OPS:
            raise ScreenError(f"unknown operator {op!r}")
        if op == "between":
            if not isinstance(value, list) or len(value) != 2:
                raise ScreenError("between needs [low, high]")
            lo, hi = _num(value[0], field), _num(value[1], field)
            if lo > hi:
                raise ScreenError("between low must be <= high")
            filters.append(Filter(field, op, (lo, hi)))
        else:
            filters.append(Filter(field, op, _num(value, field)))
    sort = definition.get("sort", {"field": "ticker", "direction": "asc"})
    if not isinstance(sort, dict) or set(sort) - {"field", "direction"}:
        raise ScreenError("sort must be {field, direction}")
    sort_field = sort.get("field", "ticker")
    if sort_field != "ticker" and sort_field not in FIELDS:
        raise ScreenError(f"unknown sort field {sort_field!r}")
    direction = sort.get("direction", "asc")
    if direction not in ("asc", "desc"):
        raise ScreenError("sort direction must be asc or desc")
    limit = definition.get("limit", 25)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ScreenError("limit must be an integer 1-100")
    return Screen(tuple(tickers), tuple(filters), sort_field, direction == "desc", limit)


def _cursor(value, ticker: str) -> str:
    return base64.urlsafe_b64encode(json.dumps([value, ticker]).encode()).decode()


def _decode(cursor: str) -> tuple:
    try:
        value, ticker = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return value, ticker
    except Exception as exc:  # noqa: BLE001
        raise ScreenError("invalid cursor") from exc


def run(screen: Screen, metrics: dict[str, dict], after: str | None = None) -> dict:
    """metrics: ticker -> {field: value|None, "_reasons": {field: reason}, "_meta": {...}}."""
    matched, rejected = [], []
    for ticker in screen.universe:
        row = metrics.get(ticker)
        if row is None:
            rejected.append({"ticker": ticker, "failed": [{"field": "*", "reason": "no data"}]})
            continue
        failures = []
        for f in screen.filters:
            value = row.get(f.field)
            if value is None:
                failures.append({"field": f.field, "reason": (row.get("_reasons") or {}).get(f.field, "unavailable")})
            elif not f.test(value):
                failures.append({"field": f.field, "reason": f"{value!r} fails {f.op} {f.value}"})
        (rejected if failures else matched).append(
            {"ticker": ticker, **({"failed": failures} if failures else {}),
             "metrics": {k: v for k, v in row.items() if not k.startswith("_")}, "meta": row.get("_meta", {})})

    def sort_key(item):
        if screen.sort_field == "ticker":
            return (0, 0.0, item["ticker"])
        v = item["metrics"].get(screen.sort_field)
        if v is None:
            return (1, 0.0, item["ticker"])
        return (0, -v if screen.sort_desc else v, item["ticker"])

    matched.sort(key=sort_key)
    if after:
        last_value, last_ticker = _decode(after)
        last_key = (0 if last_value is not None else 1, (-last_value if screen.sort_desc else last_value) if last_value is not None else 0.0, last_ticker) \
            if screen.sort_field != "ticker" else (0, 0.0, last_ticker)
        matched = [m for m in matched if sort_key(m) > last_key]
    page = matched[: screen.limit]
    nxt = None
    if len(matched) > screen.limit:
        last = page[-1]
        nxt = _cursor(last["metrics"].get(screen.sort_field) if screen.sort_field != "ticker" else None, last["ticker"])
    return {"definition": screen.canonical(), "matches": page, "next_cursor": nxt,
            "rejected": sorted(rejected, key=lambda r: r["ticker"])}
