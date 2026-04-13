import os

from .base import OptionsDataProvider
from .mock import build_mock_provider
from .tradier import build_tradier_provider


def build_options_provider() -> OptionsDataProvider:
    provider = os.getenv("OPTIONS_PROVIDER", "").lower().strip()
    if not provider:
        provider = "tradier" if os.getenv("TRADIER_API_TOKEN") else "mock"
    if provider == "tradier" and not os.getenv("TRADIER_API_TOKEN"):
        provider = "mock"
    if provider == "mock":
        return build_mock_provider()
    return build_tradier_provider()
