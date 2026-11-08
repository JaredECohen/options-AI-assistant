"""Read-only client for STORED MarketMosaic research (contract mm.research_memo.v1).

What it calls (MarketMosaic side: backend/app/api/routes_v1_research.py on the
companion branch):
  GET {base}/api/v1/research/{ticker}/memos?limit=N        -> mm.research_memo_index.v1
  GET {base}/api/v1/research/{ticker}/memos/{version}      -> mm.research_memo.v1
Both are exact-version or metadata reads of immutable stored snapshots. There is
deliberately no "latest" read and no parameter that can trigger generation or
refresh; MarketMosaic's own wall (beta, plan, memo_view meter) applies because
the user's Clerk token is forwarded, and X-Service-Token identifies the caller.

Safety: payloads are size-bounded, fields are validated against the contract,
research text is returned as UNTRUSTED data (callers must never treat it as
instructions), and responses are cached per (user, ticker, version) only.
"""
from __future__ import annotations

import re
import time

import httpx

INDEX_CONTRACT = "mm.research_memo_index.v1"
MEMO_CONTRACT = "mm.research_memo.v1"
MAX_BYTES = 256 * 1024
CACHE_SECONDS = 300
TICKER_RE = re.compile(r"^[A-Z.]{1,10}$")


class ResearchUnavailable(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.detail = detail


class MarketMosaicResearchClient:
    def __init__(self, base_url: str, service_token: str, timeout: float = 5.0,
                 transport: httpx.AsyncBaseTransport | None = None, clock=time.monotonic):
        self.base_url = base_url.rstrip("/")
        self.service_token = service_token
        self.timeout = timeout
        self.transport = transport
        self.clock = clock
        self._cache: dict[tuple[str, str, str], tuple[float, dict]] = {}

    async def _get(self, path: str, bearer: str, params: dict | None = None) -> dict:
        headers = {"Authorization": f"Bearer {bearer}", "Accept": "application/json",
                   "X-Service-Token": self.service_token}
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                resp = await client.get(f"{self.base_url}{path}", headers=headers, params=params)
        except httpx.HTTPError as exc:
            raise ResearchUnavailable("unreachable") from exc
        if len(resp.content) > MAX_BYTES:
            raise ResearchUnavailable("payload_too_large")
        if resp.status_code == 404:
            raise ResearchUnavailable("not_found")
        if resp.status_code in (401, 403):
            body = _json(resp)
            raise ResearchUnavailable("not_authorized", str(body.get("error", "")))
        if resp.status_code == 402:
            raise ResearchUnavailable("plan_required")
        if resp.status_code == 429:
            raise ResearchUnavailable("rate_limited")
        if resp.status_code == 422:
            raise ResearchUnavailable("unreadable", str(_json(resp).get("error", "")))
        if resp.status_code != 200:
            raise ResearchUnavailable("upstream_error", f"HTTP {resp.status_code}")
        return _json(resp)

    async def list_versions(self, ticker: str, bearer: str, user_key: str, limit: int = 10) -> dict:
        ticker = _ticker(ticker)
        data = await self._get(f"/api/v1/research/{ticker}/memos", bearer, {"limit": min(max(limit, 1), 20)})
        if data.get("contract") != INDEX_CONTRACT or data.get("ticker") != ticker:
            raise ResearchUnavailable("contract_mismatch")
        versions = data.get("versions") or []
        return {"contract": INDEX_CONTRACT, "ticker": ticker, "versions": [
            {k: v.get(k) for k in ("version", "generated_at", "trigger", "rating_label", "confidence_score")}
            for v in versions[:20] if isinstance(v, dict) and isinstance(v.get("version"), int)]}

    async def get_memo(self, ticker: str, version: int, bearer: str, user_key: str) -> dict:
        ticker = _ticker(ticker)
        if not isinstance(version, int) or version < 1:
            raise ResearchUnavailable("bad_version")
        key = (user_key, ticker, str(version))
        hit = self._cache.get(key)
        if hit and self.clock() - hit[0] < CACHE_SECONDS:
            return hit[1]
        data = await self._get(f"/api/v1/research/{ticker}/memos/{version}", bearer)
        memo = validate_memo(data, ticker, version)
        self._cache[key] = (self.clock(), memo)
        return memo


def _json(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _ticker(t: str) -> str:
    t = (t or "").upper()
    if not TICKER_RE.match(t):
        raise ResearchUnavailable("bad_ticker")
    return t


def _text(value, limit: int) -> str:
    return value[:limit] if isinstance(value, str) else ""


def validate_memo(data: dict, ticker: str, version: int) -> dict:
    """Keep only contract fields, bounded. Missing review/freshness stay explicit nulls."""
    if data.get("contract") != MEMO_CONTRACT or data.get("ticker") != ticker or data.get("version") != version:
        raise ResearchUnavailable("contract_mismatch")
    review = data.get("review") if isinstance(data.get("review"), dict) else None
    freshness = data.get("freshness") if isinstance(data.get("freshness"), dict) else None
    confidence = data.get("confidence") if isinstance(data.get("confidence"), dict) else None
    citations = []
    for c in (data.get("citations") or [])[:30]:
        if isinstance(c, dict):
            citations.append({k: _text(c.get(k), 300) for k in ("title", "source", "url", "date")})
    return {
        "contract": MEMO_CONTRACT,
        "ticker": ticker,
        "version": version,
        "generated_at": _text(data.get("generated_at"), 40) or None,
        "as_of": _text(data.get("as_of"), 40) or None,
        "trigger": _text(data.get("trigger"), 40) or None,
        "rating_label": _text(data.get("rating_label"), 40) or None,
        "confidence": None if confidence is None else {
            "score": confidence.get("score") if isinstance(confidence.get("score"), (int, float)) else None,
            "available": bool(confidence.get("available")),
            "caps": [_text(x, 120) for x in (confidence.get("caps") or [])[:10]],
        },
        "summary": _text(data.get("summary"), 4000),
        "citations": citations,
        "review": None if review is None else {
            "status": _text(review.get("status"), 40) or None,
            "verdict": _text(review.get("verdict"), 40) or None,
            "open_material_issues": review.get("open_material_issues") if isinstance(review.get("open_material_issues"), int) else None,
            "open_minor_issues": review.get("open_minor_issues") if isinstance(review.get("open_minor_issues"), int) else None,
        },
        "freshness": None if freshness is None else {"stale": freshness.get("stale"), "reason": _text(freshness.get("reason"), 200) or None},
        "sections_unavailable": [_text(x, 80) for x in (data.get("sections_unavailable") or [])[:20]],
        "served_from": "stored_snapshot",
        "untrusted_text": True,
    }
