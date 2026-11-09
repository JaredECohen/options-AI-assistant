"""OptionsCoach's own schema (its own database; never MarketMosaic's).

Ownership: every user-scoped row carries user_id and every query in
app/db/repo.py filters on it. Market-data tables are shared reference data with
provenance (provider, data_status, ingest batch) and natural-key uniqueness so
re-ingestion is idempotent.

Migrations live in migrations/versions and are additive.
"""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    type_annotation_map = {dt.datetime: DateTime(timezone=True)}


# --------------------------------------------------------------------------- identity


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Clerk `sub` (user_...). Never the email: emails change and are not unique identities.
    external_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    auth_provider: Mapped[str] = mapped_column(String(32), default="clerk", nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow, nullable=False)
    last_seen_at: Mapped[dt.datetime] = mapped_column(default=utcnow, nullable=False)
    deleted_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)


# --------------------------------------------------------------------------- user state


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, nullable=False)
    __table_args__ = (Index("ix_conversations_user_updated", "user_id", "updated_at"),)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user | assistant
    text: Mapped[str] = mapped_column(Text, nullable=False)  # what the user saw
    context_text: Mapped[str] = mapped_column(Text, default="", nullable=False)  # summarized turn fed back to the LLM
    kind: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    served_by: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow, nullable=False)
    __table_args__ = (Index("ix_messages_conversation_id_id", "conversation_id", "id"),)


class Position(Base):
    __tablename__ = "positions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    ticker: Mapped[str] = mapped_column(String(16), nullable=False)
    legs: Mapped[list] = mapped_column(JSON, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_positions_user_name"),
        Index("ix_positions_user_updated", "user_id", "updated_at"),
    )


class SavedScreen(Base):
    __tablename__ = "saved_screens"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    definition: Mapped[dict] = mapped_column(JSON, nullable=False)  # validated screener DSL
    dsl_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, nullable=False)
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_saved_screens_user_name"),)


# --------------------------------------------------------------------------- cost & jobs


class LLMUsageEvent(Base):
    """One row per model call. Budgets and per-user rate limits are sums/counts
    over this table, so they hold across processes and restarts."""

    __tablename__ = "llm_usage_events"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    day: Mapped[dt.date] = mapped_column(Date, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model_requested: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    model_served: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)  # ok | truncated | refusal | error:<code> | budget
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    est_cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    pricing_version: Mapped[str] = mapped_column(String(48), default="", nullable=False)
    __table_args__ = (
        Index("ix_llm_usage_user_day", "user_id", "day"),
        Index("ix_llm_usage_day", "day"),
        Index("ix_llm_usage_user_created", "user_id", "created_at"),
    )


class Job(Base):
    """Durable background job (ingestion, backtests). Claimed with a lease; a
    worker that dies loses the lease and the job is retried."""

    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    run_after: Mapped[dt.datetime] = mapped_column(default=utcnow, nullable=False)
    lease_expires_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    locked_by: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, nullable=False)
    __table_args__ = (
        Index("ix_jobs_claim", "status", "run_after"),
        Index("ix_jobs_user_created", "user_id", "created_at"),
    )


# --------------------------------------------------------------------------- market data (P3)


class IngestBatch(Base):
    __tablename__ = "ingest_batches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # chain_snapshot | daily_bars | iv_daily
    params: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="running", nullable=False)
    rows_written: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    started_at: Mapped[dt.datetime] = mapped_column(default=utcnow, nullable=False)
    finished_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)


class OptionContract(Base):
    __tablename__ = "option_contracts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    occ_symbol: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    underlying: Mapped[str] = mapped_column(String(16), nullable=False)
    expiration: Mapped[dt.date] = mapped_column(Date, nullable=False)
    strike: Mapped[float] = mapped_column(Float, nullable=False)
    option_type: Mapped[str] = mapped_column(String(4), nullable=False)
    multiplier: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    adjusted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    __table_args__ = (Index("ix_option_contracts_underlying_exp", "underlying", "expiration"),)


class OptionQuoteSnapshot(Base):
    __tablename__ = "option_quote_snapshots"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    contract_id: Mapped[int] = mapped_column(ForeignKey("option_contracts.id", ondelete="CASCADE"), nullable=False)
    as_of: Mapped[dt.datetime] = mapped_column(nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    data_status: Mapped[str] = mapped_column(String(16), nullable=False)
    bid: Mapped[float | None] = mapped_column(Float, nullable=True)
    ask: Mapped[float | None] = mapped_column(Float, nullable=True)
    last: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    open_interest: Mapped[int | None] = mapped_column(Integer, nullable=True)
    implied_volatility: Mapped[float | None] = mapped_column(Float, nullable=True)
    iv_source: Mapped[str] = mapped_column(String(48), default="", nullable=False)
    underlying_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    batch_id: Mapped[str] = mapped_column(ForeignKey("ingest_batches.id"), nullable=False)
    __table_args__ = (
        UniqueConstraint("contract_id", "as_of", "provider", name="uq_quote_snapshot_natural"),
        Index("ix_quote_snapshots_asof", "as_of"),
    )


class UnderlyingDailyBar(Base):
    __tablename__ = "underlying_daily_bars"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    adj_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    batch_id: Mapped[str] = mapped_column(ForeignKey("ingest_batches.id"), nullable=False)
    __table_args__ = (UniqueConstraint("symbol", "date", "provider", name="uq_daily_bar_natural"),)


class IVDaily(Base):
    """Daily implied-volatility observation used for IV rank/percentile. The
    method column states exactly what was measured (e.g. 30-day constant-maturity
    ATM IV interpolated in total variance)."""

    __tablename__ = "iv_daily"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    iv: Mapped[float] = mapped_column(Float, nullable=False)
    method: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    batch_id: Mapped[str] = mapped_column(ForeignKey("ingest_batches.id"), nullable=False)
    __table_args__ = (UniqueConstraint("symbol", "date", "method", "provider", name="uq_iv_daily_natural"),)
