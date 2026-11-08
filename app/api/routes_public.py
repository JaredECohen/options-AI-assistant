"""Unauthenticated endpoints: liveness, readiness and SPA bootstrap config."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.providers.options.base import DATA_STATUS_LABELS

router = APIRouter()


@router.get("/health")
async def health(request: Request):
    """Liveness only: the process is up and serving. Never touches dependencies."""
    return {"status": "ok", "revision": request.app.state.settings.release_revision}


@router.get("/ready")
def ready(request: Request):
    """Readiness: database reachable and at the expected migration revision.
    External providers are reported but do not fail readiness (their outages
    surface as controlled 503s on the routes that need them)."""
    state = request.app.state
    checks: dict = {}
    ok = True
    try:
        state.db.ping()
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"error:{exc.__class__.__name__}"
        ok = False
    if ok and not state.settings.database_url.startswith("sqlite"):
        try:
            with state.db.engine.connect() as conn:
                current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            checks["migrations"] = "ok" if current == state.migration_head else f"behind:{current}->{state.migration_head}"
            ok = ok and current == state.migration_head
        except Exception as exc:  # noqa: BLE001
            checks["migrations"] = f"error:{exc.__class__.__name__}"
            ok = False
    checks["auth"] = {"enabled": state.settings.auth_enabled, "jwks_loaded": state.jwks.loaded}
    checks["llm"] = state.llm.status()
    provider = state.options_provider
    checks["market_data"] = {"provider": provider.name, "data_status": provider.data_status}
    body = {"status": "ready" if ok else "not_ready", "revision": state.settings.release_revision, "checks": checks}
    return JSONResponse(status_code=200 if ok else 503, content=body)


@router.get("/api/public/config")
async def public_config(request: Request):
    s = request.app.state.settings
    provider = request.app.state.options_provider
    return {
        "app_env": s.app_env,
        "demo_mode": s.demo_mode,
        "auth_enabled": s.auth_enabled,
        "clerk_publishable_key": s.clerk_publishable_key or None,
        "clerk_jwt_template": s.clerk_jwt_template,
        "market_data": {"provider": provider.name, "data_status": provider.data_status,
                        "label": DATA_STATUS_LABELS.get(provider.data_status, provider.data_status)},
        "llm": {"provider": s.llm_provider, "labelled_demo": s.llm_provider == "heuristic"},
        "revision": s.release_revision,
    }


@router.get("/api/llm_status")
async def llm_status(request: Request):
    status = request.app.state.llm.status()
    return {"provider": status["provider"], "deterministic": status["provider"] == "heuristic", "circuit": status["circuit"]}
