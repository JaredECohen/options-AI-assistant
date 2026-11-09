"""OptionsCoach FastAPI application factory.

`create_app()` builds everything from Settings; every external dependency can be
injected so tests exercise the real routes and middleware with fake keys,
transports and databases. `app` (module level) is what uvicorn serves.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.auth.entitlements import EntitlementCache, MarketMosaicEntitlementSource, StaticEntitlementSource
from app.auth.tokens import JWKSCache
from app.config import ConfigError, Settings, load_settings, validate_for_startup
from app.core.payoff import PayoffInputError
from app.db import repo
from app.db.models import Base
from app.db.session import Database
from app.llm.gateway import LLMGateway
from app.llm.types import LLMUnavailable
from app.providers.options.base import ProviderUnavailable, QuoteUnavailable
from app.providers.options.factory import build_options_provider

BASE_DIR = Path(__file__).resolve().parents[1]
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
MAX_BODY_BYTES = 64 * 1024
log = logging.getLogger("app")


def _configure_logging(level: str) -> None:
    logging.basicConfig(level=level, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    # httpx logs full request URLs at INFO; some providers put credentials in
    # query strings, so keep transport logs at WARNING.
    for noisy in ("httpx", "httpcore", "httpx2", "httpcore2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _migration_head() -> str | None:
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        cfg = Config(str(BASE_DIR / "alembic.ini"))
        cfg.set_main_option("script_location", str(BASE_DIR / "migrations"))
        return ScriptDirectory.from_config(cfg).get_current_head()
    except Exception:  # noqa: BLE001
        return None


def create_app(
    settings: Settings | None = None,
    *,
    db: Database | None = None,
    options_provider=None,
    llm_client=None,
    jwks: JWKSCache | None = None,
    entitlement_source=None,
    research_client=None,
    create_schema: bool | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    problems = validate_for_startup(settings)
    if problems:
        raise ConfigError("Refusing to start:\n- " + "\n- ".join(problems))
    _configure_logging(settings.log_level)

    app = FastAPI(title="OptionsCoach", version="0.2.0", docs_url=None if settings.is_prod_like else "/docs",
                  redoc_url=None, openapi_url=None if settings.is_prod_like else "/openapi.json")
    state = app.state
    state.settings = settings
    state.db = db or Database(settings.database_url, settings.db_pool_size)
    if create_schema if create_schema is not None else settings.database_url.startswith("sqlite"):
        Base.metadata.create_all(state.db.engine)  # dev/test convenience; Postgres uses alembic
    state.migration_head = _migration_head()
    state.jwks = jwks or JWKSCache(settings.clerk_jwks_url)
    if entitlement_source is None:
        if settings.entitlements_source == "marketmosaic":
            entitlement_source = MarketMosaicEntitlementSource(
                settings.marketmosaic_base_url, settings.marketmosaic_service_token, settings.marketmosaic_timeout_seconds
            )
        else:
            entitlement_source = StaticEntitlementSource(settings.static_entitlements)
    state.entitlements = EntitlementCache(entitlement_source, settings.entitlement_cache_seconds)
    state.options_provider = options_provider or build_options_provider(settings)
    state.llm = LLMGateway(settings, state.db, client=llm_client)
    from app.analytics.chain import AnalyticsConfig
    from app.analytics.snapshot import SnapshotService

    state.analytics_cfg = AnalyticsConfig(risk_free_rate=settings.analytics_risk_free_rate,
                                          dividend_yield=settings.analytics_dividend_yield)
    state.snapshots = SnapshotService(state.options_provider, state.analytics_cfg, state.db)
    if research_client is None and settings.research_integration_enabled:
        from app.integrations.marketmosaic import MarketMosaicResearchClient

        research_client = MarketMosaicResearchClient(
            settings.marketmosaic_base_url, settings.marketmosaic_service_token, settings.marketmosaic_timeout_seconds
        )
    state.research = research_client

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=False,  # bearer tokens, not cookies
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        max_age=600,
    )

    @app.middleware("http")
    async def body_limit(request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH"):
            length = request.headers.get("content-length")
            if length is None:
                if request.headers.get("transfer-encoding"):
                    return _error(411, "length_required", "Content-Length is required")
            elif not length.isdigit() or int(length) > MAX_BODY_BYTES:
                return _error(413, "body_too_large", f"request bodies are limited to {MAX_BODY_BYTES} bytes")
        return await call_next(request)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        rid = incoming if _REQUEST_ID.match(incoming) else uuid.uuid4().hex
        request.state.correlation_id = rid
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Revision"] = settings.release_revision
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        path = request.url.path
        if path.startswith("/assets/"):
            response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
        elif path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    _install_error_handlers(app)

    from app.api import routes_analytics, routes_chat, routes_contracts, routes_market, routes_public, routes_state

    for module in (routes_public, routes_chat, routes_state, routes_market, routes_analytics, routes_contracts):
        app.include_router(module.router)

    _mount_frontend(app)
    log.info("startup %s", json.dumps({"env": settings.app_env, "revision": settings.release_revision,
                                       "llm": settings.llm_provider, "options": settings.options_provider,
                                       "auth": settings.auth_enabled, "demo": settings.demo_mode}))
    return app


def _error(status: int, error: str, detail: str, **extra) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": error, "detail": detail, **extra})


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_exc(_request: Request, exc: HTTPException):
        if isinstance(exc.detail, dict):
            return JSONResponse(status_code=exc.status_code, content=exc.detail, headers=exc.headers)
        return _error(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(LLMUnavailable)
    async def llm_exc(_request: Request, exc: LLMUnavailable):
        return _error(exc.http_status, "llm_unavailable", exc.user_message, code=exc.code)

    @app.exception_handler(ProviderUnavailable)
    async def provider_exc(_request: Request, exc: ProviderUnavailable):
        return _error(503, "market_data_unavailable", str(exc))

    @app.exception_handler(QuoteUnavailable)
    async def quote_exc(_request: Request, exc: QuoteUnavailable):
        return _error(404, "quote_unavailable", str(exc))

    @app.exception_handler(PayoffInputError)
    async def payoff_exc(_request: Request, exc: PayoffInputError):
        return _error(422, exc.code, str(exc))

    @app.exception_handler(repo.NotFound)
    async def not_found(_request: Request, exc: repo.NotFound):
        return _error(404, "not_found", str(exc))

    @app.exception_handler(repo.BadCursor)
    async def bad_cursor(_request: Request, exc: repo.BadCursor):
        return _error(422, "invalid_cursor", str(exc))

    @app.exception_handler(repo.Conflict)
    async def conflict(_request: Request, exc: repo.Conflict):
        return _error(409, "conflict", str(exc))


def _mount_frontend(app: FastAPI) -> None:
    if (FRONTEND_DIST / "assets").exists():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    async def index():
        if FRONTEND_DIST.exists():
            return FileResponse(FRONTEND_DIST / "index.html", headers={"Cache-Control": "no-cache"})
        return HTMLResponse("<h2>Frontend not built</h2><p>Run <code>npm ci && npm run build</code> in <code>frontend/</code>.</p>")


def app_factory() -> FastAPI:
    """Entry point for `uvicorn app.main:app_factory --factory`. Loads .env for
    local development only; Render injects real environment variables."""
    load_dotenv()
    return create_app()
