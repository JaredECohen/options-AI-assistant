"""Shared strategy vocabulary used by both renderers (display names, view labels, convexity notes)."""
from __future__ import annotations

from app.core.strategies import normalize_strategy_name


def strategy_view_label(strategy_name: str) -> str:
    name = normalize_strategy_name(strategy_name)
    view_map = {
        "long call": "bullish",
        "long put": "bearish",
        "covered call": "neutral to mildly bullish income",
        "cash-secured put": "bullish to neutral income",
        "bull call spread": "bullish",
        "bear call spread": "bearish to neutral income",
        "bull put spread": "bullish to neutral income",
        "bear put spread": "bearish",
        "long straddle": "volatile (long volatility)",
        "long strangle": "volatile (long volatility)",
        "short straddle": "neutral to short volatility",
        "short strangle": "neutral to short volatility",
        "iron condor": "neutral to short volatility",
        "iron butterfly": "neutral to short volatility",
        "collar": "defensive/neutral with capped upside",
        "protective put": "bullish with downside protection",
        "calendar spread": "neutral to mildly directional (often slightly bullish or bearish depending on strikes)",
        "diagonal spread": "neutral to mildly directional (often slightly bullish or bearish depending on strikes)",
        "ratio spread": "directional with tail risk (depends on call/put)",
        "backspread": "volatile with directional convexity (depends on call/put)",
    }
    return view_map.get(name, "")


def convexity_detail_for_strategy(strategy_name: str) -> str:
    name = (strategy_name or "").lower()
    if name == "long straddle":
        return (
            "Convexity is strongest near the shared strike and decays with time if the stock stays near that level."
        )
    if name == "long strangle":
        return (
            "Convexity starts lower because the strikes are further OTM, but it increases as price approaches either strike."
        )
    if name in ("short straddle", "short strangle", "iron condor", "iron butterfly"):
        return (
            "Negative convexity means small moves are fine, but losses accelerate as price moves away from the short strikes."
        )
    if name in ("bull call spread", "bear put spread"):
        return (
            "Convexity is limited: you are long gamma between strikes, but the short leg caps acceleration beyond the short strike."
        )
    if name in ("bull put spread", "bear call spread", "covered call", "cash-secured put"):
        return (
            "Negative convexity comes from the short option; gamma works against you as price moves toward the short strike."
        )
    if name in ("calendar spread", "diagonal spread"):
        return (
            "Convexity depends on where price sits relative to the short strike and how volatility shifts; the front-month option tends to carry the most gamma."
        )
    if name in ("ratio spread", "backspread"):
        return (
            "Convexity can flip: risk is greatest near the short strike, but beyond it the extra long options add positive convexity."
        )
    return "Convexity is strongest near the strike for at-the-money options and decays with time."


def friendly_name(name: str) -> str:
    return name.title()
