"""First-contact check against ThetaData (run once with a subscription).

    # library (default): reads THETADATA_EMAIL / THETADATA_PASSWORD (or THETADATA_API_KEY) from the environment
    python scripts/thetadata_smoke.py SPY
    # terminal: a running Theta Terminal
    THETADATA_ACCESS=terminal THETADATA_BASE_URL=http://127.0.0.1:25503 python scripts/thetadata_smoke.py SPY

The adapter was written from ThetaData's documentation and tested on fixtures
only. This makes a handful of real requests (no per-request charge on
ThetaData's flat subscriptions; they count toward the concurrency cap) and
reports, for each capability, whether the live response parsed into usable
values and which tier-dependent fallback was taken. It prints values, never
credentials.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.providers.options.thetadata import ThetaDataProvider  # noqa: E402


async def check(ticker: str) -> dict:
    access = os.getenv("THETADATA_ACCESS", "library")
    p = ThetaDataProvider(base_url=os.getenv("THETADATA_BASE_URL"), data_status="delayed", max_concurrency=2,
                          timeout_seconds=60.0, access=access, email=os.getenv("THETADATA_EMAIL", ""),
                          password=os.getenv("THETADATA_PASSWORD", ""), api_key=os.getenv("THETADATA_API_KEY", ""))
    report: dict = {"ticker": ticker, "access": access}

    async def step(name, coro, summarize):
        try:
            value = await coro
            report[name] = {"ok": True, **summarize(value)}
            return value
        except Exception as exc:  # noqa: BLE001 - this script reports, it does not fix
            report[name] = {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}
            return None

    spot = await step("underlying_price", p.get_underlying_price(ticker), lambda v: {"value": v})
    exps = await step("list_expirations", p.list_expirations(ticker),
                      lambda v: {"count": len(v), "first": v[:3]})
    if exps:
        today = dt.date.today()
        target = min(exps, key=lambda e: abs((dt.date.fromisoformat(e) - today).days - 30))
        chain = await step("get_chain", p.get_chain(ticker, target), lambda v: {
            "expiration": target, "contracts": len(v),
            "with_bid_ask": sum(q.bid is not None and q.ask is not None for q in v),
            "with_iv": sum(q.implied_volatility is not None for q in v),
            "with_greeks": sum(q.greeks is not None for q in v),
            "with_open_interest": sum(q.open_interest is not None for q in v),
            "with_volume": sum(q.volume is not None for q in v),
            "sample": v[len(v) // 2].as_json() if v else None})
        if chain is not None:
            end = today - dt.timedelta(days=1)
            await step("get_eod_options", p.get_eod_options(ticker, target, end - dt.timedelta(days=7), end),
                       lambda v: {"rows": len(v), "days": sorted({r.date.isoformat() for r in v}),
                                  "with_underlying": sum(r.underlying_price is not None for r in v)})
    await step("get_daily_bars", p.get_daily_bars(ticker, dt.date.today() - dt.timedelta(days=10), dt.date.today()),
               lambda v: {"bars": len(v), "last": [d.isoformat() for d, _c in v[-2:]]})
    report["requests"] = p.requests
    report["spot_sane"] = bool(spot and spot > 0)
    return report


def main() -> int:
    ticker = (sys.argv[1] if len(sys.argv) > 1 else "SPY").upper()
    report = asyncio.run(check(ticker))
    print(json.dumps(report, indent=2, default=str))
    return 0 if all(v.get("ok", True) for v in report.values() if isinstance(v, dict)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
