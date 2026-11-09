"""Run or enqueue an IV-history backfill.

    python -m app.jobs.backfill_cli SPY QQQ --days 365            # run now, in this process
    python -m app.jobs.backfill_cli SPY --days 365 --enqueue      # hand it to the worker

Running inline needs DATABASE_URL and a reachable history provider
(OPTIONS_HISTORY_PROVIDER=thetadata, THETADATA_BASE_URL). Each provider request
counts against the ThetaData subscription's limits; the output reports how many
requests were made.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys

from app.analytics.chain import AnalyticsConfig
from app.config import load_settings
from app.db.session import Database
from app.jobs import queue
from app.jobs.iv_backfill import MAX_BACKFILL_DAYS, backfill_iv_history
from app.providers.options.factory import build_history_provider


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("tickers", nargs="+")
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--end", help="YYYY-MM-DD, default yesterday (New York)")
    ap.add_argument("--enqueue", action="store_true", help="enqueue for the worker instead of running inline")
    args = ap.parse_args(argv)
    if not 1 <= args.days <= MAX_BACKFILL_DAYS:
        ap.error(f"--days must be between 1 and {MAX_BACKFILL_DAYS}")
    settings = load_settings()
    db = Database(settings.database_url, settings.db_pool_size)
    payloads = [{"ticker": t.upper(), "days": args.days, **({"end": args.end} if args.end else {})} for t in args.tickers]
    if args.enqueue:
        stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S")
        for p in payloads:
            job = queue.enqueue(db, "backfill_iv_history", f"backfill_iv_history:{p['ticker']}:manual:{stamp}", p)
            print(json.dumps({"enqueued": job.id, "ticker": p["ticker"]}))
        return 0
    provider = build_history_provider(settings)
    if provider is None:
        print("OPTIONS_HISTORY_PROVIDER is not set (expected thetadata).", file=sys.stderr)
        return 2
    status = 0
    for p in payloads:
        try:
            result = asyncio.run(backfill_iv_history(db, provider, p, AnalyticsConfig()))
            print(json.dumps(result))
        except Exception as exc:  # noqa: BLE001 - report and continue with the next ticker
            print(json.dumps({"ticker": p["ticker"], "error": f"{exc.__class__.__name__}: {exc}"}))
            status = 1
    return status


if __name__ == "__main__":
    raise SystemExit(main())
