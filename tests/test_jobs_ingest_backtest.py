"""Durable jobs, ingestion idempotency, and backtest arithmetic (hand-checked)."""
from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import func, select

from app.analytics.chain import AnalyticsConfig
from app.backtest import metrics
from app.db.models import Base, IngestBatch, IVDaily, OptionQuoteSnapshot, UnderlyingDailyBar
from app.db.session import Database
from app.jobs import queue
from app.jobs.ingest import SyntheticDataRefused, ingest_chain_snapshot, ingest_daily_bars
from app.providers.options.mock import MockOptionsProvider
from app.worker import Worker


@pytest.fixture
def db(tmp_path):
    d = Database(f"sqlite:///{tmp_path}/jobs-{uuid.uuid4().hex}.db")
    Base.metadata.create_all(d.engine)
    return d


def test_enqueue_is_idempotent(db):
    a = queue.enqueue(db, "ingest_daily_bars", "k", {"ticker": "SPY"})
    b = queue.enqueue(db, "ingest_daily_bars", "k", {"ticker": "SPY"})
    assert a.id == b.id


def test_claim_retry_backoff_dead_letter(db):
    now = dt.datetime(2026, 11, 6, tzinfo=dt.UTC)
    j = queue.enqueue(db, "x", "k", {}, max_attempts=2, run_after=now)
    got = queue.claim(db, "w1", now=now)
    assert got.id == j.id and got.status == "running" and queue.claim(db, "w2", now=now) is None
    queue.fail(db, j.id, "w1", "boom", now=now)
    assert queue.claim(db, "w1", now=now) is None  # backing off
    later = now + dt.timedelta(seconds=31)
    queue.claim(db, "w1", now=later)
    queue.fail(db, j.id, "w1", "boom again", now=later)
    with db.session() as s:
        from app.db.models import Job
        assert s.get(Job, j.id).status == "dead"


def test_lease_expiry_recovers_and_stale_worker_cannot_complete(db):
    now = dt.datetime(2026, 11, 6, tzinfo=dt.UTC)
    j = queue.enqueue(db, "x", "k", {}, run_after=now)
    queue.claim(db, "dead-worker", now=now)
    assert queue.recover_expired(db, now=now + dt.timedelta(seconds=queue.LEASE_SECONDS + 1)) == 1
    queue.claim(db, "w2", now=now + dt.timedelta(seconds=200))
    queue.complete(db, j.id, "dead-worker", {"stale": True})  # ignored: lease lost
    queue.complete(db, j.id, "w2", {"ok": True})
    with db.session() as s:
        from app.db.models import Job
        job = s.get(Job, j.id)
        assert job.status == "succeeded" and job.result == {"ok": True} and job.attempts == 2


def test_cancel_queued_and_running(db):
    j1 = queue.enqueue(db, "x", "k1", {}, user_id=None)
    from app.db import repo
    uid = repo.ensure_user(db, "u")
    j2 = queue.enqueue(db, "x", "k2", {}, user_id=uid)
    assert queue.cancel_owned(db, uid, j2.id).status == "cancelled"
    j3 = queue.enqueue(db, "x", "k3", {}, user_id=uid)
    queue.claim(db, "w")  # claims j1 (oldest) — cancel only applies to owned jobs
    _ = j1
    running = queue.claim(db, "w")
    assert running.id == j3.id
    assert queue.cancel_owned(db, uid, j3.id).cancel_requested is True
    assert queue.heartbeat(db, j3.id, "w") is False  # handler sees cancellation
    queue.complete(db, j3.id, "w", {})
    assert queue.get_owned(db, uid, j3.id).status == "cancelled"


class LiveishProvider(MockOptionsProvider):
    """Mock prices relabelled as delayed, ONLY to exercise ingestion plumbing in tests."""
    name = "fixture"
    data_status = "delayed"


async def test_ingest_refuses_synthetic_and_is_idempotent(db):
    with pytest.raises(SyntheticDataRefused):
        await ingest_chain_snapshot(db, MockOptionsProvider(), {"ticker": "SPY"}, AnalyticsConfig())
    p = LiveishProvider(today=dt.date.today())
    # five weekly expiries so the 30-day point is bracketed (no extrapolation otherwise)
    first = await ingest_chain_snapshot(db, p, {"ticker": "SPY", "max_expiries": 5}, AnalyticsConfig())
    second = await ingest_chain_snapshot(db, p, {"ticker": "SPY", "max_expiries": 5}, AnalyticsConfig())
    assert first["iv_30d"] is not None
    assert first["rows_written"] > 0 and second["rows_written"] == 0  # dedupe on natural keys
    with db.session() as s:
        assert s.execute(select(func.count()).select_from(IVDaily)).scalar_one() == 1
        assert s.execute(select(func.count()).select_from(OptionQuoteSnapshot)).scalar_one() == first["rows_written"] - 1
        assert s.execute(select(func.count()).select_from(IngestBatch)).scalar_one() == 2
    bars = await ingest_daily_bars(db, p, {"ticker": "SPY", "days": 30, "end": "2026-11-06"})
    again = await ingest_daily_bars(db, p, {"ticker": "SPY", "days": 30, "end": "2026-11-06"})
    assert bars["rows_written"] == bars["bars_seen"] > 0 and again["rows_written"] == 0
    with db.session() as s:
        assert s.execute(select(func.count()).select_from(UnderlyingDailyBar)).scalar_one() == bars["bars_seen"]


async def test_worker_runs_jobs_and_records_failures(db):
    w = Worker(db, LiveishProvider(today=dt.date.today()), AnalyticsConfig(), worker_id="w-test")
    assert w.schedule(["SPY"], dt.date(2026, 11, 6)) == 2
    assert w.schedule(["SPY"], dt.date(2026, 11, 6)) == 2  # idempotent keys: still 2 jobs
    assert await w.run_once() and await w.run_once()
    assert not await w.run_once()
    from app.db.models import Job
    with db.session() as s:
        assert sorted(j.status for j in s.execute(select(Job)).scalars()) == ["succeeded", "succeeded"]
    queue.enqueue(db, "backtest", "bt", {}, max_attempts=1)
    await w.run_once()
    with db.session() as s:
        bt = s.execute(select(Job).where(Job.kind == "backtest")).scalar_one()
        assert bt.status == "dead" and "blocked" in bt.error


# ---- P7 arithmetic, by hand ----

def test_metrics_by_hand():
    equity = [100.0, 110.0, 99.0, 108.9]
    rets = metrics.daily_returns(equity)
    assert list(rets) == pytest.approx([0.10, -0.10, 0.10])
    assert metrics.max_drawdown(equity) == pytest.approx(0.10)
    mean, sd = 1 / 30, (0.04 / 3) ** 0.5  # sample std (ddof=1) of [.1, -.1, .1]
    assert metrics.sharpe(rets) == pytest.approx(mean / sd * 252 ** 0.5)
    assert metrics.win_rate([10, -5, 0, 3]) == 0.5
    assert metrics.cvar([0.01, -0.02, 0.03, -0.05], 0.95) == pytest.approx(0.05)
    assert metrics.sharpe([0.01, 0.01]) is None and metrics.win_rate([]) is None
    down = (0.01 / 3) ** 0.5  # sqrt(mean(min(r,0)^2)) = sqrt(0.01/3)
    assert metrics.sortino(rets) == pytest.approx(mean / down * 252 ** 0.5)
    assert metrics.sortino([0.01, 0.02]) is None
