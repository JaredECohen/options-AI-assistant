from __future__ import annotations

from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field


class OptionLeg(BaseModel):
    instrument: Literal["option"] = "option"
    option_type: Literal["call", "put"]
    side: Literal["buy", "sell"]
    strike: float = Field(gt=0, le=1_000_000)
    expiration: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    premium: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    quantity: int = Field(default=1, ge=1, le=10_000)
    # Shares per contract. 100 for standard US equity options; adjusted
    # contracts after corporate actions can differ and must say so.
    multiplier: int = Field(default=100, ge=1, le=100_000)


class StockLeg(BaseModel):
    instrument: Literal["stock"] = "stock"
    side: Literal["buy", "sell"]
    premium: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    quantity: int = Field(default=1, ge=1, le=10_000_000)  # shares


Leg = Union[OptionLeg, StockLeg]


class ChatRequest(BaseModel):
    message: str = Field(default="")
    ticker: Optional[str] = None
    view: Optional[str] = None
    strategy: Optional[str] = None
    legs: Optional[List[Leg]] = None
    mode: Optional[Literal["structured", "freeform"]] = None
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    response_text: str
    computed: Optional[dict] = None


class ChainResponse(BaseModel):
    ticker: str
    expiration: Optional[str] = None
    expirations: Optional[List[str]] = None
    chain: Optional[List[dict]] = None


class QuoteResponse(BaseModel):
    ticker: str
    price: float
    source: str


class ClearRequest(BaseModel):
    session_id: str
