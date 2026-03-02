from __future__ import annotations

import os
import logging
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from app.models import ChainResponse, ChatRequest, ChatResponse, QuoteResponse, ClearRequest
from app.providers.options.factory import build_options_provider
from app.services.chat_service import ChatService
from app.services.llm_service import LLMService
from app.services.memory_store import MemoryStore

load_dotenv()

BASE_DIR = Path(__file__).resolve().parents[1]
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("app")

app = FastAPI(title="Options Strategy Explainer")

options_provider = build_options_provider()
llm_service = LLMService()
memory_store = MemoryStore()
chat_service = ChatService(options_provider, llm_service, memory_store)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if FRONTEND_DIST.exists():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")


@app.get("/")
async def index():
    if FRONTEND_DIST.exists():
        return FileResponse(FRONTEND_DIST / "index.html")
    return HTMLResponse(
        "<h2>Frontend not built</h2><p>Run <code>npm install</code> and <code>npm run build</code> in <code>frontend/</code>, "
        "or run the Vite dev server with <code>npm run dev</code> and open <code>http://localhost:5173</code>.</p>"
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/llm_status")
async def llm_status():
    return {
        "provider": llm_service.provider_name,
        "deterministic": llm_service.deterministic,
    }


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


@app.post("/api/clear")
async def clear(req: ClearRequest):
    memory_store.clear(req.session_id)
    logger.info("Cleared chat history | session_id=%s", req.session_id)
    return {"status": "ok"}


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
