"""Canonical entitlements come from MarketMosaic; OptionsCoach only enforces them.

MarketMosaicEntitlementSource calls MarketMosaic's GET /api/v1/entitlements/me
(contract mm.entitlements.v1) with the user's own Clerk token, so MarketMosaic
applies its normal verification and resolves beta, plan and account state from
its own tables. X-Service-Token identifies the caller as OptionsCoach. The
response must echo the token's subject; a mismatch is refused.

Results are cached per subject for ENTITLEMENT_CACHE_SECONDS (default 60), which
bounds how long a revocation can take to land. When MarketMosaic is unreachable
and the cache entry is older than that, access fails CLOSED (503).

StaticEntitlementSource exists for development and tests only.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx

from app.auth.tokens import AuthUnavailable

CONTRACT = "mm.entitlements.v1"
PLAN_RANK = {"none": 0, "free": 1, "pro": 2}

# Minimum plan per OptionsCoach feature. Beta approval and an active account are
# required for everything. "none" (no MarketMosaic trial/subscription yet) may
# use the free features.
FEATURE_MIN_PLAN: dict[str, str] = {
    "chat": "none",
    "market_data": "none",
    "payoff": "none",
    "analytics": "none",
    "screens": "none",
    "research": "none",  # MarketMosaic meters memo views itself
    "backtest": "pro",
}


@dataclass(frozen=True)
class Entitlements:
    external_id: str
    account_state: str  # active | suspended | deleted
    beta_status: str  # approved | pending | declined | revoked | none
    plan: str  # none | free | pro
    source: str
    fetched_at: float = field(default_factory=time.monotonic)

    def denial_for(self, feature: str) -> tuple[int, str] | None:
        if self.account_state != "active":
            return 403, "account_suspended"
        if self.beta_status != "approved":
            return 403, "beta_access_required"
        need = FEATURE_MIN_PLAN.get(feature)
        if need is None:
            return 403, "feature_unknown"
        if PLAN_RANK.get(self.plan, -1) < PLAN_RANK[need]:
            return 402, "plan_required"
        return None


class EntitlementError(Exception):
    pass


class StaticEntitlementSource:
    """Dev/test fixture: {"<sub>" | "*": {"account_state", "beta_status", "plan"}}."""

    name = "static"

    def __init__(self, table: dict):
        self.table = table or {"*": {"account_state": "active", "beta_status": "approved", "plan": "pro"}}

    async def fetch(self, sub: str, bearer: str) -> Entitlements:
        row = self.table.get(sub) or self.table.get("*")
        if row is None:
            return Entitlements(sub, "active", "none", "none", self.name)
        return Entitlements(sub, row.get("account_state", "active"), row.get("beta_status", "none"),
                            row.get("plan", "none"), self.name)


class MarketMosaicEntitlementSource:
    name = "marketmosaic"

    def __init__(self, base_url: str, service_token: str, timeout: float = 5.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.service_token = service_token
        self.timeout = timeout
        self.transport = transport

    async def fetch(self, sub: str, bearer: str) -> Entitlements:
        headers = {"Authorization": f"Bearer {bearer}", "Accept": "application/json"}
        if self.service_token:
            headers["X-Service-Token"] = self.service_token
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                resp = await client.get(f"{self.base_url}/api/v1/entitlements/me", headers=headers)
        except httpx.HTTPError as exc:
            raise AuthUnavailable("entitlement service unreachable") from exc
        if resp.status_code == 401:
            raise EntitlementError("entitlement service rejected the token")
        if resp.status_code != 200:
            raise AuthUnavailable(f"entitlement service returned HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise AuthUnavailable("entitlement service returned non-JSON") from exc
        if data.get("contract") != CONTRACT:
            raise AuthUnavailable("entitlement contract mismatch")
        if data.get("external_id") != sub:
            raise EntitlementError("entitlement subject mismatch")
        return Entitlements(sub, str(data.get("account_state", "")), str(data.get("beta_status", "")),
                            str(data.get("plan", "none")), self.name)


class EntitlementCache:
    def __init__(self, source, ttl_seconds: int, clock=time.monotonic):
        self.source = source
        self.ttl = ttl_seconds
        self.clock = clock
        self._entries: dict[str, Entitlements] = {}

    async def get(self, sub: str, bearer: str) -> Entitlements:
        cached = self._entries.get(sub)
        now = self.clock()
        if cached is not None and now - cached.fetched_at < self.ttl:
            return cached
        try:
            fresh = await self.source.fetch(sub, bearer)
        except AuthUnavailable:
            self._entries.pop(sub, None)  # never serve beyond the TTL
            raise
        fresh = Entitlements(fresh.external_id, fresh.account_state, fresh.beta_status, fresh.plan, fresh.source, now)
        self._entries[sub] = fresh
        return fresh
