"""User-scoped data access.

Rule: every function that touches user state takes the owning user_id and
filters on it. A row owned by someone else is indistinguishable from a missing
row (NotFound), so IDs cannot be probed. Old anonymous session ids from the
in-memory era are never adopted into an account.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    Conversation,
    LLMUsageEvent,
    Message,
    Position,
    SavedScreen,
    User,
    utcnow,
)
from app.db.session import Database

MAX_PAGE = 100


class NotFound(LookupError):
    pass


class Conflict(ValueError):
    pass


class BadCursor(ValueError):
    pass


# --------------------------------------------------------------------------- users


def ensure_user(db: Database, external_id: str) -> int:
    """Return the local user id for a verified Clerk `sub`, creating it once.
    Race-safe: concurrent first requests resolve to the same row."""
    with db.session() as s:
        row = s.execute(select(User.id, User.deleted_at).where(User.external_id == external_id)).first()
        if row is not None:
            if row.deleted_at is not None:
                raise NotFound("account deleted")
            s.execute(User.__table__.update().where(User.id == row.id).values(last_seen_at=utcnow()))
            return int(row.id)
    try:
        with db.session() as s:
            user = User(external_id=external_id)
            s.add(user)
            s.flush()
            return int(user.id)
    except IntegrityError:
        with db.session() as s:
            return int(s.execute(select(User.id).where(User.external_id == external_id)).scalar_one())


def delete_user_data(db: Database, user_id: int) -> None:
    """Hard-delete everything OptionsCoach holds for a user. The identity row
    stays (keyed only by the Clerk sub) so the person can keep using the app;
    account deletion itself is MarketMosaic's (it owns the identity)."""
    from app.db.models import Job

    with db.session() as s:
        s.execute(delete(Conversation).where(Conversation.user_id == user_id))
        s.execute(delete(Position).where(Position.user_id == user_id))
        s.execute(delete(SavedScreen).where(SavedScreen.user_id == user_id))
        s.execute(delete(Job).where(Job.user_id == user_id))
        s.execute(LLMUsageEvent.__table__.update().where(LLMUsageEvent.user_id == user_id).values(user_id=None))


# --------------------------------------------------------------------------- conversations


def _owned_conversation(s: Session, user_id: int, conversation_id: str) -> Conversation:
    conv = s.get(Conversation, conversation_id)
    if conv is None or conv.user_id != user_id:
        raise NotFound("conversation not found")
    return conv


def create_conversation(db: Database, user_id: int, title: str = "") -> str:
    with db.session() as s:
        conv = Conversation(user_id=user_id, title=title[:200])
        s.add(conv)
        s.flush()
        return conv.id


def require_conversation(db: Database, user_id: int, conversation_id: str) -> None:
    with db.session() as s:
        _owned_conversation(s, user_id, conversation_id)


def list_conversations(db: Database, user_id: int, limit: int = 20, before: str | None = None) -> dict:
    """Newest first; cursor is "<iso updated_at>|<id>" for stable pagination."""
    limit = max(1, min(limit, MAX_PAGE))
    with db.session() as s:
        q = select(Conversation).where(Conversation.user_id == user_id)
        if before:
            ts_raw, _, cid = before.partition("|")
            try:
                ts = dt.datetime.fromisoformat(ts_raw)
            except ValueError as exc:
                raise BadCursor("invalid cursor") from exc
            q = q.where((Conversation.updated_at < ts) | ((Conversation.updated_at == ts) & (Conversation.id < cid)))
        rows = s.execute(q.order_by(Conversation.updated_at.desc(), Conversation.id.desc()).limit(limit + 1)).scalars().all()
    items = [{"id": c.id, "title": c.title, "updated_at": c.updated_at.isoformat()} for c in rows[:limit]]
    nxt = f"{rows[limit - 1].updated_at.isoformat()}|{rows[limit - 1].id}" if len(rows) > limit else None
    return {"items": items, "next_cursor": nxt}


def conversation_messages(db: Database, user_id: int, conversation_id: str, limit: int = 200) -> list[dict]:
    with db.session() as s:
        _owned_conversation(s, user_id, conversation_id)
        rows = s.execute(
            select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id.desc()).limit(min(limit, 500))
        ).scalars().all()
    return [
        {"role": m.role, "text": m.text, "kind": m.kind, "served_by": m.served_by, "created_at": m.created_at.isoformat()}
        for m in reversed(rows)
    ]


def delete_conversation(db: Database, user_id: int, conversation_id: str) -> None:
    with db.session() as s:
        conv = _owned_conversation(s, user_id, conversation_id)
        s.delete(conv)


def export_user_data(db: Database, user_id: int) -> dict:
    with db.session() as s:
        convs = s.execute(select(Conversation).where(Conversation.user_id == user_id).order_by(Conversation.created_at)).scalars().all()
        out_convs = []
        for c in convs:
            msgs = s.execute(select(Message).where(Message.conversation_id == c.id).order_by(Message.id)).scalars().all()
            out_convs.append(
                {
                    "id": c.id,
                    "title": c.title,
                    "created_at": c.created_at.isoformat(),
                    "messages": [{"role": m.role, "text": m.text, "created_at": m.created_at.isoformat()} for m in msgs],
                }
            )
        positions = s.execute(select(Position).where(Position.user_id == user_id)).scalars().all()
        screens = s.execute(select(SavedScreen).where(SavedScreen.user_id == user_id)).scalars().all()
    return {
        "format": "optionscoach.export.v1",
        "conversations": out_convs,
        "positions": [_position_json(p) for p in positions],
        "saved_screens": [_screen_json(x) for x in screens],
    }


def purge_expired_conversations(db: Database, retention_days: int, now: dt.datetime | None = None) -> int:
    cutoff = (now or utcnow()) - dt.timedelta(days=retention_days)
    with db.session() as s:
        result = s.execute(delete(Conversation).where(Conversation.updated_at < cutoff))
        return int(result.rowcount or 0)


@dataclass
class DurableHistory:
    """ChatService history store backed by the messages table. The caller must
    have verified ownership of every key (conversation id) it passes in."""

    db: Database
    user_id: int
    max_turns: int = 6

    def get(self, conversation_id: str) -> list[dict]:
        with self.db.session() as s:
            _owned_conversation(s, self.user_id, conversation_id)
            rows = s.execute(
                select(Message.role, Message.context_text)
                .where(Message.conversation_id == conversation_id, Message.context_text != "")
                .order_by(Message.id.desc())
                .limit(self.max_turns * 2)
            ).all()
        return [{"role": r.role, "text": r.context_text} for r in reversed(rows)]

    def append_turn(
        self,
        conversation_id: str,
        *,
        user_context: str,
        assistant_context: str,
        user_display: str = "",
        assistant_display: str = "",
        kind: str = "",
        served_by: str = "",
    ) -> None:
        with self.db.session() as s:
            conv = _owned_conversation(s, self.user_id, conversation_id)
            s.add_all(
                [
                    Message(conversation_id=conversation_id, role="user", text=user_display or user_context,
                            context_text=user_context, kind=kind),
                    Message(conversation_id=conversation_id, role="assistant", text=assistant_display or assistant_context,
                            context_text=assistant_context, kind=kind, served_by=served_by),
                ]
            )
            conv.updated_at = utcnow()
            if not conv.title and user_display:
                conv.title = user_display.strip()[:80]

    def clear(self, conversation_id: str) -> None:
        with self.db.session() as s:
            _owned_conversation(s, self.user_id, conversation_id)
            s.execute(delete(Message).where(Message.conversation_id == conversation_id))


# --------------------------------------------------------------------------- positions & screens


def _position_json(p: Position) -> dict:
    return {"id": p.id, "name": p.name, "ticker": p.ticker, "legs": p.legs, "notes": p.notes,
            "updated_at": p.updated_at.isoformat()}


def _screen_json(x: SavedScreen) -> dict:
    return {"id": x.id, "name": x.name, "definition": x.definition, "dsl_version": x.dsl_version,
            "updated_at": x.updated_at.isoformat()}


def save_position(db: Database, user_id: int, name: str, ticker: str, legs: list, notes: str = "",
                  position_id: str | None = None) -> dict:
    try:
        with db.session() as s:
            if position_id:
                p = s.get(Position, position_id)
                if p is None or p.user_id != user_id:
                    raise NotFound("position not found")
                p.name, p.ticker, p.legs, p.notes, p.updated_at = name, ticker, legs, notes, utcnow()
            else:
                p = Position(user_id=user_id, name=name, ticker=ticker, legs=legs, notes=notes)
                s.add(p)
            s.flush()
            return _position_json(p)
    except IntegrityError as exc:
        raise Conflict("a position with that name already exists") from exc


def list_positions(db: Database, user_id: int) -> list[dict]:
    with db.session() as s:
        rows = s.execute(select(Position).where(Position.user_id == user_id).order_by(Position.updated_at.desc()).limit(MAX_PAGE)).scalars().all()
        return [_position_json(p) for p in rows]


def delete_position(db: Database, user_id: int, position_id: str) -> None:
    with db.session() as s:
        p = s.get(Position, position_id)
        if p is None or p.user_id != user_id:
            raise NotFound("position not found")
        s.delete(p)


def save_screen(db: Database, user_id: int, name: str, definition: dict, screen_id: str | None = None) -> dict:
    try:
        with db.session() as s:
            if screen_id:
                x = s.get(SavedScreen, screen_id)
                if x is None or x.user_id != user_id:
                    raise NotFound("screen not found")
                x.name, x.definition, x.updated_at = name, definition, utcnow()
            else:
                x = SavedScreen(user_id=user_id, name=name, definition=definition)
                s.add(x)
            s.flush()
            return _screen_json(x)
    except IntegrityError as exc:
        raise Conflict("a screen with that name already exists") from exc


def list_screens(db: Database, user_id: int) -> list[dict]:
    with db.session() as s:
        rows = s.execute(select(SavedScreen).where(SavedScreen.user_id == user_id).order_by(SavedScreen.name).limit(MAX_PAGE)).scalars().all()
        return [_screen_json(x) for x in rows]


def get_screen(db: Database, user_id: int, screen_id: str) -> dict:
    with db.session() as s:
        x = s.get(SavedScreen, screen_id)
        if x is None or x.user_id != user_id:
            raise NotFound("screen not found")
        return _screen_json(x)


def delete_screen(db: Database, user_id: int, screen_id: str) -> None:
    with db.session() as s:
        x = s.get(SavedScreen, screen_id)
        if x is None or x.user_id != user_id:
            raise NotFound("screen not found")
        s.delete(x)


# --------------------------------------------------------------------------- usage / budgets


def record_usage(db: Database, **fields) -> None:
    fields.setdefault("day", utcnow().date())
    with db.session() as s:
        s.add(LLMUsageEvent(**fields))


def tokens_today(db: Database, user_id: int | None) -> int:
    today = utcnow().date()
    with db.session() as s:
        q = select(func.coalesce(func.sum(LLMUsageEvent.input_tokens + LLMUsageEvent.output_tokens), 0)).where(
            LLMUsageEvent.day == today
        )
        if user_id is not None:
            q = q.where(LLMUsageEvent.user_id == user_id)
        return int(s.execute(q).scalar_one())


def chat_turns_in_window(db: Database, user_id: int, seconds: int = 60) -> int:
    """User chat messages across all of the user's conversations in the window."""
    since = utcnow() - dt.timedelta(seconds=seconds)
    with db.session() as s:
        return int(
            s.execute(
                select(func.count())
                .select_from(Message)
                .join(Conversation, Conversation.id == Message.conversation_id)
                .where(Conversation.user_id == user_id, Message.role == "user", Message.created_at >= since)
            ).scalar_one()
        )


class AdmissionDenied(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


TURN_OUTCOME = "turn"
_GLOBAL_LOCK_KEY = 0x0C0A  # arbitrary, stable


def reserve_turn(db: Database, user_id: int | None, *, estimate_tokens: int, rate_per_min: int,
                 user_budget: int, global_budget: int, correlation_id: str = "") -> int:
    """Atomically admit one model turn and reserve its estimated tokens.

    The checks and the reservation row are written in ONE transaction, and on
    Postgres under advisory locks (per user, then global), so concurrent
    requests cannot all pass a check before any of them is counted. The
    reservation row (outcome "turn") is what the per-minute limit counts; its
    estimate counts toward budgets until finish_turn() zeroes it, after which
    the per-call usage rows carry the real tokens."""
    today = utcnow().date()
    since = utcnow() - dt.timedelta(seconds=60)
    with db.session() as s:
        if s.bind.dialect.name == "postgresql":
            if user_id is not None:
                s.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": int(user_id)})
            s.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _GLOBAL_LOCK_KEY})
        tokens = LLMUsageEvent.input_tokens + LLMUsageEvent.output_tokens
        if user_id is not None:
            recent = s.execute(select(func.count()).select_from(LLMUsageEvent).where(
                LLMUsageEvent.user_id == user_id, LLMUsageEvent.outcome == TURN_OUTCOME,
                LLMUsageEvent.created_at >= since)).scalar_one()
            if recent >= rate_per_min:
                raise AdmissionDenied("too_many_requests")
            used = s.execute(select(func.coalesce(func.sum(tokens), 0)).where(
                LLMUsageEvent.user_id == user_id, LLMUsageEvent.day == today)).scalar_one()
            if used + estimate_tokens > user_budget:
                raise AdmissionDenied("budget_user")
        used_all = s.execute(select(func.coalesce(func.sum(tokens), 0)).where(LLMUsageEvent.day == today)).scalar_one()
        if used_all + estimate_tokens > global_budget:
            raise AdmissionDenied("budget_global")
        row = LLMUsageEvent(user_id=user_id, day=today, correlation_id=correlation_id, provider="anthropic",
                            outcome=TURN_OUTCOME, output_tokens=estimate_tokens)
        s.add(row)
        s.flush()
        return int(row.id)


def finish_turn(db: Database, reservation_id: int) -> None:
    """Release the estimate; the per-call rows already hold the actual usage."""
    with db.session() as s:
        s.execute(update(LLMUsageEvent).where(LLMUsageEvent.id == reservation_id).values(output_tokens=0))
