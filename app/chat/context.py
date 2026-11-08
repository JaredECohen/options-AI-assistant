"""Conversation context: history formatting and the user message sent to the LLM."""
from __future__ import annotations

from app.models import ChatRequest
from app.models import OptionLeg
from app.models import StockLeg
import json


def normalize_legs(legs):
    valid = []
    for leg in legs:
        if isinstance(leg, OptionLeg):
            if (
                leg.option_type
                and leg.side
                and leg.strike is not None
                and leg.expiration
                and leg.quantity
            ):
                valid.append(leg)
        elif isinstance(leg, StockLeg):
            if leg.side and leg.quantity:
                valid.append(leg)
    return valid


def build_context_message(user_text: str, req: ChatRequest, history: list[dict]) -> str:
    context = {
        "ticker": req.ticker,
        "view": req.view,
        "strategy": req.strategy,
        "legs": [leg.model_dump() for leg in req.legs] if req.legs else [],
    }
    history_text = format_history(history)
    parts = []
    if history_text:
        parts.append("Conversation history (most recent last):\n" + history_text)
    parts.append(f"Current user message:\n{user_text}")
    parts.append("Context (use only if the user asked for payoff/evaluation):\n" + json.dumps(context, indent=2))
    return "\n\n".join(parts)


def format_history(history: list[dict], max_chars: int = 2000) -> str:
    lines = []
    total = 0
    for turn in history[-12:]:
        role = turn.get("role", "user")
        content = turn.get("text", "").strip()
        if not content:
            continue
        line = f"{role.title()}: {content}"
        if total + len(line) + 1 > max_chars:
            break
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines)


def history_text(history: list[dict]) -> str:
    return " ".join([turn.get("text", "") for turn in history if turn.get("text")])


def history_user_text(history: list[dict]) -> str:
    return " ".join([turn.get("text", "") for turn in history if turn.get("role") == "user" and turn.get("text")])


def strip_json_block(text: str) -> str:
    if "```json" not in text:
        return text
    return text.split("```json")[0].strip()


def _summarize_user_turn(req: ChatRequest, user_text: str) -> str:
    if user_text.strip():
        return user_text.strip()
    pieces = []
    if req.view:
        pieces.append(f"view={req.view}")
    if req.strategy:
        pieces.append(f"strategy={req.strategy}")
    if pieces:
        return "Strategy builder selection: " + ", ".join(pieces)
    return "User request"


def _truncate(text: str, max_len: int = 160) -> str:
    if not text:
        return ""
    clean = " ".join(text.split())
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 3] + "..."
