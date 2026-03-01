from __future__ import annotations

from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field


class OptionLeg(BaseModel):
    instrument: Literal["option"] = "option"
    option_type: Literal["call", "put"]
    side: Literal["buy", "sell"]
    strike: float
    expiration: str
    premium: Optional[float] = None
    quantity: int = 1


class StockLeg(BaseModel):
    instrument: Literal["stock"] = "stock"
    side: Literal["buy", "sell"]
    premium: Optional[float] = None
    quantity: int = 1


Leg = Union[OptionLeg, StockLeg]


class ChatRequest(BaseModel):
    message: str = Field(default="")
    ticker: Optional[str] = None
    view: Optional[str] = None
    strategy: Optional[str] = None
    legs: Optional[List[Leg]] = None


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
