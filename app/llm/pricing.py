"""Versioned price table for cost ESTIMATES (USD per million tokens).

Source: Anthropic first-party list prices as cached in the Claude API reference
on 2026-10-06. Re-verify against https://platform.claude.com/docs pricing before
relying on these for billing decisions; the version string is logged with every
usage row so historical estimates stay interpretable after a price change.
Cache-read rates not in the reference are estimated at 10% of input and flagged.
"""
from __future__ import annotations

PRICING_VERSION = "anthropic-list-2026-10-06"

# model: (input, output, cache_read, cache_read_is_estimate)
PRICES: dict[str, tuple[float, float, float, bool]] = {
    "claude-haiku-5-5": (0.10, 0.50, 0.01, True),  # prompts <= 100K tokens; 0.50/2.50 beyond
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, False),
    "claude-opus-5-5": (4.00, 20.00, 0.20, False),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0) -> float:
    price = PRICES.get(model)
    if price is None:
        return 0.0
    inp, out, cread, _ = price
    if model == "claude-haiku-5-5" and input_tokens + cache_read + cache_write > 100_000:
        inp, out = 0.50, 2.50
    # cache writes billed at ~1.25x input for the 5-minute TTL
    return round((input_tokens * inp + output_tokens * out + cache_read * cread + cache_write * inp * 1.25) / 1_000_000, 6)
