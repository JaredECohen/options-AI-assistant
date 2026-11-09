"""Explicit options-provider selection (tradier, thetadata, mock).

No silent substitution: a missing Tradier token used to fall back to the mock
provider, which made synthetic prices look like market data. Now the provider
is whatever OPTIONS_PROVIDER names; Tradier without a token reports itself
unavailable (503 at the API), and mock requires an explicit demo/test setting.
"""
from __future__ import annotations

from app.config import Settings

from .base import OptionsDataProvider
from .mock import MockOptionsProvider
from .thetadata import ThetaDataProvider
from .tradier import TradierProvider


class ProviderConfigError(RuntimeError):
    pass


def build_options_provider(settings: Settings) -> OptionsDataProvider:
    name = settings.options_provider
    if name == "mock":
        if not settings.synthetic_data_allowed:
            raise ProviderConfigError(
                "OPTIONS_PROVIDER=mock serves synthetic prices; it is only allowed when APP_ENV is development/test "
                "or DEMO_MODE=1."
            )
        return MockOptionsProvider()
    if name == "tradier":
        return TradierProvider(data_status=settings.tradier_data_status)
    if name == "thetadata":
        return _thetadata(settings)
    raise ProviderConfigError(f"Unknown OPTIONS_PROVIDER '{name}' (expected tradier, thetadata or mock).")


def _thetadata(settings: Settings) -> ThetaDataProvider:
    return ThetaDataProvider(base_url=settings.thetadata_base_url, data_status=settings.thetadata_data_status,
                             timeout_seconds=settings.thetadata_timeout_seconds,
                             max_concurrency=settings.thetadata_max_concurrency, access=settings.thetadata_access,
                             email=settings.thetadata_email, password=settings.thetadata_password,
                             api_key=settings.thetadata_api_key)


def build_history_provider(settings: Settings) -> ThetaDataProvider | None:
    """Source for the IV-history backfill. Only ThetaData has historical option
    quotes among the supported providers; None when not configured."""
    if settings.options_history_provider == "thetadata":
        return _thetadata(settings)
    if settings.options_history_provider:
        raise ProviderConfigError(f"Unknown OPTIONS_HISTORY_PROVIDER '{settings.options_history_provider}'.")
    return None
