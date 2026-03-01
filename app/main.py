from __future__ import annotations

import os

from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.models import ChainResponse, ChatRequest, ChatResponse, QuoteResponse
from app.providers.options.factory import build_options_provider
from app.services.chat_service import ChatService
from app.services.llm_service import LLMService

load_dotenv()

app = FastAPI(title="Options Strategy Explainer")

options_provider = build_options_provider()
llm_service = LLMService()
chat_service = ChatService(options_provider, llm_service)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/")
async def index():
    with open("app/static/index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/quote", response_model=QuoteResponse)
async def quote(ticker: str = Query(...)):
    price = await options_provider.get_underlying_price(ticker)
    return QuoteResponse(ticker=ticker.upper(), price=price, source=options_provider.name)


@app.get("/api/chain", response_model=ChainResponse)
async def chain(ticker: str = Query(...), expiration: str | None = Query(default=None)):
    if not expiration:
        expirations = await options_provider.list_expirations(ticker)
        return ChainResponse(ticker=ticker.upper(), expirations=expirations)
    quotes = await options_provider.get_chain(ticker, expiration)
    chain_rows = normalize_chain(quotes)
    return ChainResponse(ticker=ticker.upper(), expiration=expiration, chain=chain_rows)


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    return await chat_service.handle(req)


def normalize_chain(quotes):
    by_strike = {}
    for q in quotes:
        row = by_strike.setdefault(q.strike, {"strike": q.strike})
        row[q.option_type] = {
            "bid": q.bid,
            "ask": q.ask,
            "last": q.last,
            "mark": q.mark,
        }
    return [by_strike[strike] for strike in sorted(by_strike.keys())]
