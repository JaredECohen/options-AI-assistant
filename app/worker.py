"""Background worker: `python -m app.worker`.

Owns the job queue and (only when ENABLE_SCHEDULER=1) the ingestion schedule.
The web service never runs jobs or schedules, so heavy work never shares a
request's process or memory. Not provisioned yet; see docs/RUNBOOK.md.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import signal
import socket

from app.analytics.chain import AnalyticsConfig
from app.config import load_settings
from app.db.session import Database
from app.jobs import queue
from app.jobs.ingest import ingest_chain_snapshot, ingest_daily_bars
from app.jobs.iv_backfill import backfill_iv_history
from app.providers.options.factory import build_history_provider, build_options_provider

log = logging.getLogger("app.worker")
POLL_SECONDS = 5


class Worker:
    def __init__(self, db: Database, provider, cfg: AnalyticsConfig, worker_id: str | None = None,
                 history_provider=None):
        self.db = db
        self.provider = provider
        self.history_provider = history_provider
        self.cfg = cfg
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
        self.stopping = False

    async def handle(self, job) -> dict:
        cancelled = {"flag": False}

        def should_continue() -> bool:
            ok = queue.heartbeat(self.db, job.id, self.worker_id)
            cancelled["flag"] = not ok
            return ok

        if job.kind == "ingest_chain_snapshot":
            return await ingest_chain_snapshot(self.db, self.provider, job.payload, self.cfg, should_continue)
        if job.kind == "ingest_daily_bars":
            return await ingest_daily_bars(self.db, self.provider, job.payload)
        if job.kind == "backfill_iv_history":
            if self.history_provider is None:
                raise RuntimeError("blocked: OPTIONS_HISTORY_PROVIDER is not configured (ThetaData)")
            return await backfill_iv_history(self.db, self.history_provider, job.payload, self.cfg, should_continue)
        if job.kind == "backtest":
            raise RuntimeError("blocked: no backtesting engine is configured (see docs/TRACEABILITY.md P7)")
        raise RuntimeError(f"unknown job kind {job.kind}")

    async def run_once(self) -> bool:
        queue.recover_expired(self.db)
        job = queue.claim(self.db, self.worker_id)
        if job is None:
            return False
        log.info("job_start id=%s kind=%s attempt=%s", job.id, job.kind, job.attempts)
        try:
            result = await self.handle(job)
            queue.complete(self.db, job.id, self.worker_id, result)
            log.info("job_done id=%s kind=%s", job.id, job.kind)
        except Exception as exc:  # noqa: BLE001
            queue.fail(self.db, job.id, self.worker_id, f"{exc.__class__.__name__}: {exc}")
            log.warning("job_failed id=%s kind=%s error=%s", job.id, job.kind, exc.__class__.__name__)
        return True

    def schedule(self, universe: list[str], today: dt.date) -> int:
        n = 0
        for ticker in universe:
            for kind in ("ingest_daily_bars", "ingest_chain_snapshot"):
                queue.enqueue(self.db, kind, f"{kind}:{ticker}:{today.isoformat()}", {"ticker": ticker})
                n += 1
            if self.history_provider is not None:
                # One full year per ticker, ever (the key never changes), then a
                # nightly one-week top-up so missed days fill themselves in.
                # Both are idempotent through iv_daily's natural key.
                queue.enqueue(self.db, "backfill_iv_history", f"backfill_iv_history:{ticker}:full",
                              {"ticker": ticker, "days": 365})
                queue.enqueue(self.db, "backfill_iv_history", f"backfill_iv_history:{ticker}:{today.isoformat()}",
                              {"ticker": ticker, "days": 7})
                n += 2
        return n

    async def run_forever(self, schedule_universe: list[str] | None = None) -> None:
        last_scheduled: dt.date | None = None
        while not self.stopping:
            if schedule_universe:
                today = dt.datetime.now(dt.UTC).date()
                if last_scheduled != today and dt.datetime.now(dt.UTC).hour >= 22:  # after the US close
                    self.schedule(schedule_universe, today)
                    last_scheduled = today
            worked = await self.run_once()
            if not worked:
                await asyncio.sleep(POLL_SECONDS)


def main() -> None:
    settings = load_settings()
    logging.basicConfig(level=settings.log_level)
    db = Database(settings.database_url, settings.db_pool_size)
    worker = Worker(db, build_options_provider(settings), AnalyticsConfig(),
                    history_provider=build_history_provider(settings))
    universe = [t.strip().upper() for t in os.getenv("INGEST_UNIVERSE", "").split(",") if t.strip()]
    scheduler = os.getenv("ENABLE_SCHEDULER", "0") == "1"
    loop = asyncio.new_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: setattr(worker, "stopping", True))
    loop.run_until_complete(worker.run_forever(universe if scheduler else None))


if __name__ == "__main__":
    main()
