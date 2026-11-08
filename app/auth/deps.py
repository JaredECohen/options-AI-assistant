"""FastAPI dependencies that turn a request into an authorized Principal.

The bearer token is read from the Authorization header only (never a query
string or a browser-supplied user id). Every protected route declares the
feature it serves; the same check runs for routes, chat tools and jobs.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request

from app.auth.entitlements import EntitlementError, Entitlements
from app.auth.tokens import AuthUnavailable, TokenInvalid, verify_token
from app.db import repo

DEV_SUB = "dev-local-user"


@dataclass(frozen=True)
class Principal:
    user_id: int
    external_id: str
    entitlements: Entitlements
    bearer: str  # forwarded only to MarketMosaic for user-delegated reads

    def allows(self, feature: str) -> bool:
        return self.entitlements.denial_for(feature) is None


def _error(status: int, code: str, detail: str = "") -> HTTPException:
    return HTTPException(status_code=status, detail={"error": code, "detail": detail or code.replace("_", " ")})


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


async def authenticate(request: Request) -> Principal:
    state = request.app.state
    settings = state.settings
    if not settings.auth_enabled:
        # Only reachable in development/test: validate_for_startup refuses
        # AUTH_ENABLED=false in staging/production.
        user_id = repo.ensure_user(state.db, DEV_SUB)
        ent = await state.entitlements.get(DEV_SUB, "")
        return Principal(user_id, DEV_SUB, ent, "")
    token = _bearer(request)
    if token is None:
        raise _error(401, "auth_required", "sign in required")
    try:
        verified = verify_token(
            token,
            jwks=state.jwks,
            issuer=settings.clerk_issuer,
            authorized_parties=settings.clerk_authorized_parties,
            leeway=settings.auth_leeway_seconds,
        )
    except TokenInvalid as exc:
        raise _error(401, "auth_invalid", str(exc)) from exc
    except AuthUnavailable as exc:
        raise _error(503, "auth_unavailable", "identity verification is temporarily unavailable") from exc
    try:
        ent = await state.entitlements.get(verified.sub, token)
    except AuthUnavailable as exc:
        raise _error(503, "entitlements_unavailable", "access check is temporarily unavailable") from exc
    except EntitlementError as exc:
        raise _error(401, "auth_invalid", str(exc)) from exc
    try:
        user_id = repo.ensure_user(state.db, verified.sub)
    except repo.NotFound as exc:
        raise _error(403, "account_suspended", "account deleted") from exc
    return Principal(user_id, verified.sub, ent, token)


def require(feature: str):
    async def dependency(principal: Principal = Depends(authenticate)) -> Principal:
        denial = principal.entitlements.denial_for(feature)
        if denial is not None:
            status, code = denial
            raise _error(status, code)
        return principal

    dependency.__name__ = f"require_{feature}"
    return dependency
