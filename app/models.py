from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel, Field


class OptionLeg(BaseModel):
    instrument: Literal["option"] = "option"
    option_type: Literal["call", "put"]
    side: Literal["buy", "sell"]
    strike: float = Field(gt=0, le=1_000_000)
    expiration: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    premium: float | None = Field(default=None, ge=0, le=1_000_000)
    quantity: int = Field(default=1, ge=1, le=10_000)
    # Shares per contract. 100 for standard US equity options; adjusted
    # contracts after corporate actions can differ and must say so.
    multiplier: int = Field(default=100, ge=1, le=100_000)


class StockLeg(BaseModel):
    instrument: Literal["stock"] = "stock"
    side: Literal["buy", "sell"]
    premium: float | None = Field(default=None, ge=0, le=1_000_000)
    quantity: int = Field(default=1, ge=1, le=10_000_000)  # shares


Leg = Union[OptionLeg, StockLeg]


class ChatRequest(BaseModel):
    message: str = Field(default="", max_length=4000)
    ticker: str | None = None
    view: str | None = None
    strategy: str | None = None
    legs: list[Leg] | None = None
    mode: Literal["structured", "freeform"] | None = None
    # Server-issued id of a conversation the caller owns. Omit to start one.
    conversation_id: str | None = Field(default=None, max_length=36)
    # Deprecated: client-chosen session ids used to be trusted blindly. Accepted
    # from old clients and ignored; history is keyed by conversation_id + owner.
    session_id: str | None = Field(default=None, max_length=128)


class ChatResponse(BaseModel):
    response_text: str
    computed: dict | None = None
    conversation_id: str | None = None
    served_by: dict | None = None
    correlation_id: str | None = None


class ChainResponse(BaseModel):
    ticker: str
    expiration: str | None = None
    expirations: list[str] | None = None
    chain: list[dict] | None = None


class QuoteResponse(BaseModel):
    ticker: str
    price: float
    source: str


class ClearRequest(BaseModel):
    conversation_id: str = Field(max_length=36)
