"""Durable job queue on the jobs table.

Lifecycle: queued -> running -> succeeded | failed (retry -> queued) | dead | cancelled.
- enqueue is idempotent on idempotency_key: re-enqueueing returns the existing job.
- claim takes one due job with a lease (FOR UPDATE SKIP LOCKED on Postgres), so
  several workers never run the same job.
- heartbeat extends the lease; a worker that dies stops heartbeating and
  recover_expired() puts the job back in the queue (restart recovery). An
  expired lease counts as an attempt, so a job that kills its worker repeatedly
  ends up dead instead of looping.
- fail() retries with exponential backoff until max_attempts, then dead (the
  dead-letter state, kept for inspection).
- cancel(): queued jobs are cancelled immediately; running jobs get
  cancel_requested and the handler stops at its next checkpoint.
Ownership: user-facing reads go through get_owned / cancel_owned, which filter
on user_id like every other user-state query.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.db.models import Job, utcnow
from app.db.repo import NotFound
from app.db.session import Database

LEASE_SECONDS = 120
BACKOFF_BASE_SECONDS = 30
TERMINAL = {"succeeded", "dead", "cancelled"}


@dataclass(frozen=True)
class JobView:
    id: str
    kind: str
    status: str
    attempts: int
    max_attempts: int
    error: str
    result: dict | None
    cancel_requested: bool
    created_at: str
    updated_at: str

    @classmethod
    def of(cls, j: Job) -> JobView:
        return cls(j.id, j.kind, j.status, j.attempts, j.max_attempts, j.error, j.result, j.cancel_requested,
                   j.created_at.isoformat(), j.updated_at.isoformat())


def enqueue(db: Database, kind: str, idempotency_key: str, payload: dict, user_id: int | None = None,
            max_attempts: int = 3, run_after: dt.datetime | None = None) -> JobView:
    try:
        with db.session() as s:
            job = Job(kind=kind, idempotency_key=idempotency_key, payload=payload, user_id=user_id,
                      max_attempts=max_attempts, run_after=run_after or utcnow())
            s.add(job)
            s.flush()
            return JobView.of(job)
    except IntegrityError:
        with db.session() as s:
            job = s.execute(select(Job).where(Job.idempotency_key == idempotency_key)).scalar_one()
            if user_id is not None and job.user_id != user_id:
                raise NotFound("job not found") from None
            return JobView.of(job)


def claim(db: Database, worker_id: str, kinds: list[str] | None = None, now: dt.datetime | None = None) -> Job | None:
    now = now or utcnow()
    with db.session() as s:
        q = select(Job).where(Job.status == "queued", Job.run_after <= now, Job.cancel_requested.is_(False))
        if kinds:
            q = q.where(Job.kind.in_(kinds))
        q = q.order_by(Job.run_after, Job.created_at).limit(1)
        if s.bind.dialect.name == "postgresql":
            q = q.with_for_update(skip_locked=True)
        job = s.execute(q).scalar_one_or_none()
        if job is None:
            return None
        job.status = "running"
        job.attempts += 1
        job.locked_by = worker_id
        job.lease_expires_at = now + dt.timedelta(seconds=LEASE_SECONDS)
        job.updated_at = now
        s.flush()
        s.expunge(job)
        return job


def heartbeat(db: Database, job_id: str, worker_id: str, now: dt.datetime | None = None) -> bool:
    """Extend the lease. Returns False if the lease was lost or cancellation was requested."""
    now = now or utcnow()
    with db.session() as s:
        job = s.get(Job, job_id)
        if job is None or job.status != "running" or job.locked_by != worker_id:
            return False
        job.lease_expires_at = now + dt.timedelta(seconds=LEASE_SECONDS)
        job.updated_at = now
        return not job.cancel_requested


def progress(db: Database, job_id: str, worker_id: str, info: dict) -> None:
    """Publish progress while running; it is shown as result["progress"] until the job finishes."""
    with db.session() as s:
        job = s.get(Job, job_id)
        if job is None or job.locked_by != worker_id or job.status != "running":
            return
        job.result = {"progress": info}
        job.updated_at = utcnow()


def complete(db: Database, job_id: str, worker_id: str, result: dict) -> None:
    with db.session() as s:
        job = s.get(Job, job_id)
        if job is None or job.locked_by != worker_id or job.status != "running":
            return  # lease lost: someone else owns the outcome
        job.status = "cancelled" if job.cancel_requested else "succeeded"
        job.result = result
        job.lease_expires_at = None
        job.updated_at = utcnow()


def fail(db: Database, job_id: str, worker_id: str, error: str, now: dt.datetime | None = None) -> None:
    now = now or utcnow()
    with db.session() as s:
        job = s.get(Job, job_id)
        if job is None or job.locked_by != worker_id or job.status != "running":
            return
        job.error = error[:2000]
        job.lease_expires_at = None
        job.updated_at = now
        if job.cancel_requested:
            job.status = "cancelled"
        elif job.attempts >= job.max_attempts:
            job.status = "dead"
        else:
            job.status = "queued"
            job.run_after = now + dt.timedelta(seconds=BACKOFF_BASE_SECONDS * 2 ** (job.attempts - 1))


def recover_expired(db: Database, now: dt.datetime | None = None) -> int:
    now = now or utcnow()
    recovered = 0
    with db.session() as s:
        stale = s.execute(select(Job).where(Job.status == "running", Job.lease_expires_at < now)).scalars().all()
        for job in stale:
            job.error = (job.error + "\nlease expired (worker lost)").strip()[:2000]
            job.lease_expires_at = None
            job.locked_by = ""
            job.updated_at = now
            if job.cancel_requested:
                job.status = "cancelled"
            elif job.attempts >= job.max_attempts:
                job.status = "dead"
            else:
                job.status = "queued"
                job.run_after = now
            recovered += 1
    return recovered


def get_owned(db: Database, user_id: int, job_id: str) -> JobView:
    with db.session() as s:
        job = s.get(Job, job_id)
        if job is None or job.user_id != user_id:
            raise NotFound("job not found")
        return JobView.of(job)


def cancel_owned(db: Database, user_id: int, job_id: str) -> JobView:
    with db.session() as s:
        job = s.get(Job, job_id)
        if job is None or job.user_id != user_id:
            raise NotFound("job not found")
        if job.status == "queued":
            job.status = "cancelled"
        elif job.status == "running":
            job.cancel_requested = True
        job.updated_at = utcnow()
        s.flush()
        return JobView.of(job)


def requeue_dead(db: Database, job_id: str) -> None:
    """Operator action: give a dead job a fresh set of attempts."""
    with db.session() as s:
        s.execute(update(Job).where(Job.id == job_id, Job.status == "dead").values(
            status="queued", attempts=0, run_after=utcnow(), error=""))
