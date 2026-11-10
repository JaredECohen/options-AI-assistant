"""A trading view ("SPY up ~5% by mid-December, limited loss") as structured fields.

Rules parse the common phrasings for free. When they cannot find a direction,
the LLM gateway's strict extract call fills the same fields. Either way the page
shows the interpretation, and every field can be edited before searching again.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Direction = Literal["bullish", "bearish", "neutral", "volatile", "income"]
DEFAULT_HORIZON_DAYS = 30


class View(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: Direction | None = None
    target_price: float | None = Field(default=None, gt=0, le=1_000_000)
    move_pct: float | None = Field(default=None, ge=-95, le=500)  # signed expected move
    horizon_days: int = Field(default=DEFAULT_HORIZON_DAYS, ge=1, le=730)
    defined_risk_only: bool = True
    max_loss: float | None = Field(default=None, gt=0, le=100_000_000)  # USD per position
    owns_shares: bool = False


# (direction, strong words, weak words). Strong words name the view outright.
WORDS: list[tuple[Direction, tuple[str, ...], tuple[str, ...]]] = [
    ("volatile", ("volatile", "big move", "either way", "either direction", "straddle"),
     ("volatility", "earnings", "explode", "large move", "move a lot", "whipsaw", "breakout")),
    ("income", ("income", "collect premium", "sell premium"),
     ("yield", "collect the premium", "theta", "rent out")),
    ("neutral", ("neutral", "sideways", "range-bound", "rangebound"),
     ("range", "flat", "stay between", "stay near", "not move", "won't move", "calm", "pin", "chop")),
    ("bearish", ("bearish", "bear "),
     ("down", "fall", "drop", "decline", "crash", "sell off", "selloff", "go lower", "move lower")),
    ("bullish", ("bullish", "bull "),
     (" up", "rise", "rally", "higher", "grow", "climb", "gain", "go up", "move up")),
]
# Phrases that contain a direction word without expressing a view.
NOISE = (r"limit\w*\s+(?:my\s+|the\s+)?(?:downside|losses|loss|risk)", r"(?:short|long)[- ]term",
         r"lower\s+(?:my\s+)?risk", r"downside\s+protection", r"cap\w*\s+(?:my\s+)?(?:downside|loss|risk)",
         r"down\s*payment", r"upside\s+cap")
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                       "dec"], start=1)}
UNIT_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}


def _direction(text: str) -> Direction | None:
    t = f" {text} "
    for pattern in NOISE:
        t = re.sub(pattern, " ", t)
    scores = {d: 3 * sum(w in t for w in strong) + sum(w in t for w in weak) for d, strong, weak in WORDS}
    best = max(scores.values())
    if best == 0:
        return None
    return next(d for d, *_ in WORDS if scores[d] == best)  # ties: the order above


def _horizon(text: str, today: dt.date) -> int | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*[- ]?\s*(day|week|month|year)s?", text)
    if m:
        return max(1, round(float(m.group(1)) * UNIT_DAYS[m.group(2)]))
    m = re.search(r"\b(?:by|until|through|before|into|in)\s+(?:mid[- ]|end of |early |late )?"
                  r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?(?:\s+(\d{1,2}))?", text)
    if m:
        month, day = MONTHS[m.group(1)], int(m.group(2)) if m.group(2) else None
        if day is None:
            day = 15 if "mid" in text[max(0, m.start() - 6):m.end()] else 28 if "end of" in text[m.start():m.end()] \
                else 20
        year = today.year if (month, day) >= (today.month, today.day) else today.year + 1
        try:
            return max(1, (dt.date(year, month, day) - today).days)
        except ValueError:
            return None
    if "end of year" in text or "year end" in text or "year-end" in text:
        return max(1, (dt.date(today.year, 12, 31) - today).days)
    return None


def parse_rules(text: str, spot: float | None = None, today: dt.date | None = None) -> tuple[View, bool]:
    """(view, confident). Confident means a direction was recognised."""
    today = today or dt.date.today()
    t = (text or "").lower().replace("’", "'")
    direction = _direction(t)
    move = None
    m = re.search(r"([+-]?\d+(?:\.\d+)?)\s*(?:%|percent|pct)", t)
    if m:
        move = float(m.group(1))
        if direction == "bearish" and move > 0:
            move = -move
    target = None
    m = re.search(r"(?:to|reach|hit|at|above|below|toward|towards|near|around)\s+\$?\s*(\d{1,6}(?:\.\d+)?)(?!\s*(?:%|"
                  r"day|week|month|year|percent|pct|shares))", t)
    if m:
        value = float(m.group(1))
        if spot is None or 0.3 * spot <= value <= 3 * spot:  # a price, not a count of days
            target = value
    if target and spot and direction is None:
        direction = "bullish" if target > spot else "bearish"
    max_loss = None
    m = re.search(r"(?:risk|lose|loss|losing|spend|budget)\D{0,20}\$\s*([\d,]+(?:\.\d+)?)", t) or \
        re.search(r"\$\s*([\d,]+(?:\.\d+)?)\s*(?:max|maximum)?\s*(?:risk|loss)", t)
    if m:
        max_loss = float(m.group(1).replace(",", ""))
    undefined_ok = any(w in t for w in ("undefined risk", "naked", "unlimited risk is ok", "margin is fine",
                                        "don't need defined", "dont need defined"))
    owns = bool(re.search(r"\b(i own|i hold|i'm holding|im holding|holding \d+|own \d+|have \d+ shares|my shares)\b", t))
    view = View(direction=direction, move_pct=move, target_price=target,
                horizon_days=_horizon(t, today) or DEFAULT_HORIZON_DAYS, defined_risk_only=not undefined_ok,
                max_loss=max_loss, owns_shares=owns)
    return view, direction is not None


EXTRACT_INSTRUCTION = (
    "You convert a retail investor's description of a market view on one stock or ETF into fields. Use only what the "
    "text states or clearly implies; leave a field null when it is not stated. direction: bullish (expects a rise), "
    "bearish (a fall), neutral (stays in a range), volatile (a big move, direction unknown), income (wants to collect "
    "premium). move_pct is the signed expected move in percent. horizon_days counts calendar days from today. "
    "defined_risk_only is false only if the text accepts undefined or unlimited risk. max_loss is the most they will "
    "lose in dollars. owns_shares is true only if they say they hold the stock."
)
EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        # Strict tools reject an enum alongside a ["string", "null"] type list; anyOf is accepted.
        "direction": {"anyOf": [{"type": "string", "enum": ["bullish", "bearish", "neutral", "volatile", "income"]},
                                {"type": "null"}]},
        "target_price": {"type": ["number", "null"]},
        "move_pct": {"type": ["number", "null"]},
        "horizon_days": {"type": ["integer", "null"]},
        "defined_risk_only": {"type": "boolean"},
        "max_loss": {"type": ["number", "null"]},
        "owns_shares": {"type": "boolean"},
    },
    "required": ["direction", "target_price", "move_pct", "horizon_days", "defined_risk_only", "max_loss",
                 "owns_shares"],
    "additionalProperties": False,
}


def view_from_model(fields: dict, fallback: View) -> View:
    """Validate model output field by field; anything out of range keeps the rules' value."""
    merged = fallback.model_dump()
    for key, value in (fields or {}).items():
        if key not in merged or value is None:
            continue
        candidate = {**merged, key: value}
        try:
            View.model_validate(candidate)
        except ValueError:
            continue
        merged = candidate
    return View.model_validate(merged)


async def interpret(text: str, spot: float | None, llm, today: dt.date | None = None) -> tuple[View, str]:
    """(view, parsed_by): 'rules', 'model', or 'none' (no direction found)."""
    view, confident = parse_rules(text, spot, today)
    if confident or not (text or "").strip():
        return view, "rules" if confident else "none"
    fields = await llm.extract(EXTRACT_INSTRUCTION, f"Today is {(today or dt.date.today()).isoformat()}. "
                                                   f"Current price: {spot}. Text: {text}", "record_view",
                               EXTRACT_SCHEMA) if llm is not None else None
    if not fields:
        return view, "none"
    model_view = view_from_model(fields, view)
    return model_view, "model" if model_view.direction else "none"
