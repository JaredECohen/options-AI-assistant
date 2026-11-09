"""Runtime configuration, read once from the environment.

Every variable NAME the service reads is declared here (docs/ENV_VARS.md is
generated from the same list by tests/test_config.py). Values never appear in
logs; `Settings.redacted()` reports only whether secrets are set.

`validate_for_startup` refuses configurations that would make a staging or
production deployment silently unsafe: auth disabled, no authorized-party
allowlist, wildcard origins, SQLite, synthetic market data or a heuristic
"LLM" without an explicit demo flag.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from typing import Literal

AppEnv = Literal["development", "test", "staging", "production"]

SECRET_VARS = {
    "DATABASE_URL",
    "ANTHROPIC_API_KEY",
    "TRADIER_API_TOKEN",
    "MARKETMOSAIC_SERVICE_TOKEN",
    "OC_SERVICE_TOKEN_HASHES_JSON",
}

# name -> (purpose, required_in) ; required_in uses "prod" for staging+production.
ENV_VARS: dict[str, tuple[str, str]] = {
    "APP_ENV": ("development | test | staging | production", "all"),
    "DEMO_MODE": ("1 allows synthetic data / heuristic responses outside dev, always labelled", "optional"),
    "PORT": ("HTTP port; Render injects it (default 10000)", "render"),
    "LOG_LEVEL": ("INFO by default", "optional"),
    "RELEASE_REVISION": ("deployed revision; falls back to RENDER_GIT_COMMIT", "optional"),
    "PUBLIC_BASE_URL": ("canonical public origin, e.g. https://options.marketmosaic.ai", "prod"),
    "ALLOWED_ORIGINS": ("comma-separated exact origins for credentialed CORS", "prod"),
    "DATABASE_URL": ("Postgres URL for OptionsCoach's own database (secret)", "prod"),
    "DB_POOL_SIZE": ("SQLAlchemy pool size (default 5)", "optional"),
    "AUTH_ENABLED": ("must be true in staging/production", "prod"),
    "CLERK_ISSUER": ("Clerk Frontend API origin, exact iss claim", "prod"),
    "CLERK_JWKS_URL": ("<frontend-api>/.well-known/jwks.json", "prod"),
    "CLERK_AUTHORIZED_PARTIES": ("comma-separated origins allowed in azp", "prod"),
    "CLERK_PUBLISHABLE_KEY": ("served to the SPA at runtime (not secret)", "prod"),
    "CLERK_JWT_TEMPLATE": ("token template the SPA requests (default marketmosaic)", "optional"),
    "AUTH_LEEWAY_SECONDS": ("exp/nbf/iat clock-skew leeway (default 10)", "optional"),
    "ENTITLEMENTS_SOURCE": ("marketmosaic | static (static only in dev/test)", "prod"),
    "STATIC_ENTITLEMENTS_JSON": ("dev/test entitlements fixture", "optional"),
    "ENTITLEMENT_CACHE_SECONDS": ("max staleness of a cached entitlement (default 60)", "optional"),
    "MARKETMOSAIC_BASE_URL": ("MarketMosaic API base (Render internal URL preferred)", "prod"),
    "MARKETMOSAIC_SERVICE_TOKEN": ("identifies OptionsCoach to MarketMosaic (secret)", "prod"),
    "MARKETMOSAIC_TIMEOUT_SECONDS": ("default 5", "optional"),
    "OC_SERVICE_TOKEN_HASHES_JSON": ('{"consumer": "<sha256 hex of token>"} for inbound service calls (secret)', "optional"),
    "LLM_PROVIDER": ("anthropic | heuristic | disabled", "prod"),
    "LLM_FALLBACK": ("none | heuristic (labelled, bounded, recovers via circuit breaker)", "optional"),
    "ANTHROPIC_API_KEY": ("personally owned Anthropic key (secret)", "prod"),
    "ANTHROPIC_MODEL": ("default claude-haiku-5-5", "optional"),
    "ANTHROPIC_ESCALATION_MODEL": ("default claude-sonnet-5-5", "optional"),
    "LLM_ESCALATION_ENABLED": ("0 by default", "optional"),
    "LLM_EFFORT": ("low | medium | high (default low)", "optional"),
    "LLM_MAX_OUTPUT_TOKENS": ("default 2000", "optional"),
    "LLM_TIMEOUT_SECONDS": ("per attempt, default 30", "optional"),
    "LLM_MAX_RETRIES": ("SDK retries on 408/409/429/5xx, default 2", "optional"),
    "LLM_MAX_TOOL_ROUNDS": ("max tool-use iterations per chat turn, default 4", "optional"),
    "LLM_USER_DAILY_TOKEN_BUDGET": ("input+output tokens per user per UTC day, default 200000", "optional"),
    "LLM_GLOBAL_DAILY_TOKEN_BUDGET": ("all users per UTC day, default 5000000", "optional"),
    "LLM_CIRCUIT_FAILURES": ("consecutive failures that open the breaker, default 5", "optional"),
    "LLM_CIRCUIT_COOLDOWN_SECONDS": ("breaker half-open delay, default 60", "optional"),
    "OPTIONS_PROVIDER": ("tradier | mock (mock only in dev/test or DEMO_MODE)", "prod"),
    "TRADIER_API_TOKEN": ("Tradier market-data token (secret)", "prod"),
    "TRADIER_ENV": ("production | sandbox", "optional"),
    "TRADIER_DATA_STATUS": ("live | delayed | eod as entitled; default delayed", "optional"),
    "RATE_LIMIT_PER_MIN": ("chat requests per user per minute, default 20", "optional"),
    "RETENTION_DAYS_CONVERSATIONS": ("conversation retention, default 365", "optional"),
    "RESEARCH_INTEGRATION_ENABLED": ("1 enables MarketMosaic stored-research reads", "optional"),
    "COURSE_CONTEXT_DIR": ("directory of rights-cleared course notes (T8); unset = disabled", "optional"),
    "ANALYTICS_RISK_FREE_RATE": ("flat annual rate assumption for BSM, default 0.04 (labelled)", "optional"),
    "ANALYTICS_DIVIDEND_YIELD": ("flat dividend-yield assumption, default 0 (labelled)", "optional"),
    "INGEST_UNIVERSE": ("worker only: comma-separated tickers to ingest daily", "worker"),
    "ENABLE_SCHEDULER": ("worker only: 1 lets this process enqueue scheduled ingestion", "worker"),
    "BACKTEST_ENGINE_URL": ("shared backtesting engine endpoint; unset = backtests blocked", "optional"),
}


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    return float(raw) if raw else default


def _list(name: str) -> list[str]:
    return [p.strip() for p in os.getenv(name, "").split(",") if p.strip()]


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    app_env: AppEnv = "development"
    demo_mode: bool = False
    log_level: str = "INFO"
    release_revision: str = "unknown"
    public_base_url: str = ""
    allowed_origins: tuple[str, ...] = ()
    database_url: str = ""
    db_pool_size: int = 5
    auth_enabled: bool = False
    clerk_issuer: str = ""
    clerk_jwks_url: str = ""
    clerk_authorized_parties: tuple[str, ...] = ()
    clerk_publishable_key: str = ""
    clerk_jwt_template: str = "marketmosaic"
    auth_leeway_seconds: int = 10
    entitlements_source: str = "static"
    static_entitlements: dict = field(default_factory=dict)
    entitlement_cache_seconds: int = 60
    marketmosaic_base_url: str = ""
    marketmosaic_service_token: str = ""
    marketmosaic_timeout_seconds: float = 5.0
    service_token_hashes: dict = field(default_factory=dict)
    llm_provider: str = "anthropic"
    llm_fallback: str = "none"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-5-5"
    anthropic_escalation_model: str = "claude-sonnet-5-5"
    llm_escalation_enabled: bool = False
    llm_effort: str = "low"
    llm_max_output_tokens: int = 2000
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 2
    llm_max_tool_rounds: int = 4
    llm_user_daily_token_budget: int = 200_000
    llm_global_daily_token_budget: int = 5_000_000
    llm_circuit_failures: int = 5
    llm_circuit_cooldown_seconds: float = 60.0
    options_provider: str = "mock"
    tradier_data_status: str = "delayed"
    rate_limit_per_min: int = 20
    retention_days_conversations: int = 365
    research_integration_enabled: bool = False
    course_context_dir: str = ""
    analytics_risk_free_rate: float = 0.04
    analytics_dividend_yield: float = 0.0
    backtest_engine_url: str = ""

    @property
    def is_prod_like(self) -> bool:
        return self.app_env in ("staging", "production")

    @property
    def synthetic_data_allowed(self) -> bool:
        return self.app_env in ("development", "test") or self.demo_mode

    def redacted(self) -> dict:
        out = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name in {"database_url", "anthropic_api_key", "marketmosaic_service_token", "service_token_hashes"}:
                out[f.name] = "set" if value else "unset"
            else:
                out[f.name] = value
        return out


def load_settings() -> Settings:
    raw_env = os.getenv("APP_ENV", "").strip().lower()
    if os.getenv("RENDER") and raw_env not in ("staging", "production"):
        # Render sets RENDER=true. A missing or development APP_ENV there would
        # boot with auth off and shared fixture entitlements: refuse instead.
        raise ConfigError("On Render, APP_ENV must be set explicitly to staging or production.")
    app_env = raw_env or "development"
    if app_env not in ("development", "test", "staging", "production"):
        raise ConfigError(f"APP_ENV must be development, test, staging or production (got {app_env!r}).")
    is_dev = app_env in ("development", "test")
    try:
        static_ent = json.loads(os.getenv("STATIC_ENTITLEMENTS_JSON", "") or "{}")
        token_hashes = json.loads(os.getenv("OC_SERVICE_TOKEN_HASHES_JSON", "") or "{}")
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in an entitlement/service-token variable: {exc.msg}") from exc
    return Settings(
        app_env=app_env,  # type: ignore[arg-type]
        demo_mode=_bool("DEMO_MODE"),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        release_revision=os.getenv("RELEASE_REVISION") or os.getenv("RENDER_GIT_COMMIT") or "unknown",
        public_base_url=os.getenv("PUBLIC_BASE_URL", "").rstrip("/"),
        allowed_origins=tuple(_list("ALLOWED_ORIGINS")) or (("http://localhost:5173",) if is_dev else ()),
        database_url=os.getenv("DATABASE_URL", "") or ("sqlite:///./optionscoach-dev.db" if is_dev else ""),
        db_pool_size=_int("DB_POOL_SIZE", 5),
        auth_enabled=_bool("AUTH_ENABLED", default=not is_dev),
        clerk_issuer=os.getenv("CLERK_ISSUER", "").rstrip("/"),
        clerk_jwks_url=os.getenv("CLERK_JWKS_URL", ""),
        clerk_authorized_parties=tuple(_list("CLERK_AUTHORIZED_PARTIES")),
        clerk_publishable_key=os.getenv("CLERK_PUBLISHABLE_KEY", ""),
        clerk_jwt_template=os.getenv("CLERK_JWT_TEMPLATE", "marketmosaic"),
        auth_leeway_seconds=_int("AUTH_LEEWAY_SECONDS", 10),
        entitlements_source=os.getenv("ENTITLEMENTS_SOURCE", "static" if is_dev else "marketmosaic").lower(),
        static_entitlements=static_ent,
        entitlement_cache_seconds=_int("ENTITLEMENT_CACHE_SECONDS", 60),
        marketmosaic_base_url=os.getenv("MARKETMOSAIC_BASE_URL", "").rstrip("/"),
        marketmosaic_service_token=os.getenv("MARKETMOSAIC_SERVICE_TOKEN", ""),
        marketmosaic_timeout_seconds=_float("MARKETMOSAIC_TIMEOUT_SECONDS", 5.0),
        service_token_hashes=token_hashes,
        llm_provider=os.getenv("LLM_PROVIDER", "heuristic" if is_dev else "anthropic").lower(),
        llm_fallback=os.getenv("LLM_FALLBACK", "none").lower(),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-haiku-5-5"),
        anthropic_escalation_model=os.getenv("ANTHROPIC_ESCALATION_MODEL", "claude-sonnet-5-5"),
        llm_escalation_enabled=_bool("LLM_ESCALATION_ENABLED"),
        llm_effort=os.getenv("LLM_EFFORT", "low").lower(),
        llm_max_output_tokens=_int("LLM_MAX_OUTPUT_TOKENS", 2000),
        llm_timeout_seconds=_float("LLM_TIMEOUT_SECONDS", 30.0),
        llm_max_retries=_int("LLM_MAX_RETRIES", 2),
        llm_max_tool_rounds=_int("LLM_MAX_TOOL_ROUNDS", 4),
        llm_user_daily_token_budget=_int("LLM_USER_DAILY_TOKEN_BUDGET", 200_000),
        llm_global_daily_token_budget=_int("LLM_GLOBAL_DAILY_TOKEN_BUDGET", 5_000_000),
        llm_circuit_failures=_int("LLM_CIRCUIT_FAILURES", 5),
        llm_circuit_cooldown_seconds=_float("LLM_CIRCUIT_COOLDOWN_SECONDS", 60.0),
        options_provider=os.getenv("OPTIONS_PROVIDER", "mock" if is_dev else "tradier").lower(),
        tradier_data_status=os.getenv("TRADIER_DATA_STATUS", "delayed").lower(),
        rate_limit_per_min=_int("RATE_LIMIT_PER_MIN", 20),
        retention_days_conversations=_int("RETENTION_DAYS_CONVERSATIONS", 365),
        research_integration_enabled=_bool("RESEARCH_INTEGRATION_ENABLED"),
        course_context_dir=os.getenv("COURSE_CONTEXT_DIR", ""),
        analytics_risk_free_rate=_float("ANALYTICS_RISK_FREE_RATE", 0.04),
        analytics_dividend_yield=_float("ANALYTICS_DIVIDEND_YIELD", 0.0),
        backtest_engine_url=os.getenv("BACKTEST_ENGINE_URL", ""),
    )


def validate_for_startup(s: Settings) -> list[str]:
    """Return fatal problems. Empty list means the configuration may boot."""
    problems: list[str] = []
    if s.llm_provider not in ("anthropic", "heuristic", "disabled"):
        problems.append(f"LLM_PROVIDER={s.llm_provider!r} is not supported (anthropic, heuristic, disabled).")
    if s.llm_fallback not in ("none", "heuristic"):
        problems.append("LLM_FALLBACK must be none or heuristic.")
    if s.llm_effort not in ("low", "medium", "high"):
        problems.append("LLM_EFFORT must be low, medium or high.")
    if s.entitlements_source not in ("marketmosaic", "static"):
        problems.append("ENTITLEMENTS_SOURCE must be marketmosaic or static.")
    if any(o == "*" or o.endswith("*") for o in s.allowed_origins):
        problems.append("ALLOWED_ORIGINS must list exact origins; wildcards are refused with credentials.")
    if s.auth_enabled and not (s.clerk_issuer and s.clerk_jwks_url):
        problems.append("AUTH_ENABLED requires CLERK_ISSUER and CLERK_JWKS_URL.")
    if s.llm_provider == "anthropic" and not s.anthropic_api_key and s.is_prod_like:
        problems.append("LLM_PROVIDER=anthropic requires ANTHROPIC_API_KEY.")
    if not s.is_prod_like:
        if not s.auth_enabled and s.database_url and not s.database_url.startswith("sqlite"):
            # Auth-off development against a real database is how a misconfigured
            # deploy would look; local Postgres testing should enable auth.
            problems.append("AUTH_ENABLED=false is only allowed with a SQLite DATABASE_URL (local development).")
        return problems
    # staging / production only
    if not s.auth_enabled:
        problems.append("AUTH_ENABLED must be true in staging/production.")
    if not s.clerk_authorized_parties:
        problems.append("CLERK_AUTHORIZED_PARTIES must list the allowed origins in staging/production.")
    if not s.allowed_origins:
        problems.append("ALLOWED_ORIGINS must be set in staging/production.")
    if not s.database_url or s.database_url.startswith("sqlite"):
        problems.append("DATABASE_URL must point at Postgres in staging/production.")
    if s.entitlements_source == "static":
        problems.append("ENTITLEMENTS_SOURCE=static is a dev/test fixture; use marketmosaic.")
    if s.entitlements_source == "marketmosaic" and not s.marketmosaic_base_url:
        problems.append("ENTITLEMENTS_SOURCE=marketmosaic requires MARKETMOSAIC_BASE_URL.")
    if s.options_provider == "mock" and not s.demo_mode:
        problems.append("OPTIONS_PROVIDER=mock serves synthetic prices; set DEMO_MODE=1 to allow it (it is labelled).")
    if s.llm_provider == "heuristic" and not s.demo_mode:
        problems.append("LLM_PROVIDER=heuristic is canned text; set DEMO_MODE=1 to allow it (it is labelled).")
    return problems
