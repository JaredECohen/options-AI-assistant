"""Explicit options-provider selection.

No silent substitution: a missing Tradier token used to fall back to the mock
provider, which made synthetic prices look like market data. Now the provider
is whatever OPTIONS_PROVIDER names; Tradier without a token reports itself
unavailable (503 at the API), and mock requires an explicit demo/test setting.
"""
from __future__ import annotations

from app.config import Settings

from .base import OptionsDataProvider
from .mock import MockOptionsProvider
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
    raise ProviderConfigError(f"Unknown OPTIONS_PROVIDER '{name}' (expected tradier or mock).")
