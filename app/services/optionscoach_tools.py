"""Chatbot-callable tools backed by the OptionsCoach analytics + screener modules.

These functions expose deterministic analytics to the chatbot so it can answer
quantitative questions such as "What's the IV rank on SPY?". They run entirely on
the offline, deterministic ``SyntheticOptionsAdapter`` (no API keys), which makes
them safe to call from the eval harness.

In offline/synthetic mode the synthetic chain's IV depends only on moneyness/term
(it has no calendar dynamics), so **realized-volatility history is used as the IV
proxy** when computing IV rank/percentile. Wire a live data source here to use
true historical IV.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

# Ensure the src-layout optionscoach package and the vendored engine are importable
# even when the FastAPI app is launched without them on PYTHONPATH.
_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "src", _ROOT / "backtesting-engine" / "src"):
    _sp = str(_p)
    if _sp not in sys.path:
        sys.path.insert(0, _sp)

from backtesting_engine import SyntheticOptionsAdapter  # noqa: E402

from optionscoach.analytics import hv_windows, iv_percentile, iv_rank  # noqa: E402
from optionscoach.screener import SCREENS, run_screen  # noqa: E402

DEFAULT_TICKERS = ("SPY", "QQQ", "IWM")
_SYNTH_START = {"SPY": 400.0, "QQQ": 350.0, "IWM": 190.0}


def _adapter(ticker: str) -> SyntheticOptionsAdapter:
    return SyntheticOptionsAdapter(start_price=_SYNTH_START.get(ticker, 300.0), seed=7)


def get_iv_rank(ticker: str, asof: date | None = None) -> dict[str, Any]:
    """Return IV rank, IV percentile, ATM IV and 20-day HV for ``ticker``.

    Deterministic and offline. IV rank/percentile use a 252-day lookback.
    """
    ticker = ticker.upper().strip()
    asof = asof or date(2022, 6, 1)
    adapter = _adapter(ticker)
    bars = adapter.get_bars(ticker, asof - timedelta(days=420), asof)
    closes = bars["close"]
    hv = hv_windows(closes)["hv_20"].dropna()
    # Realized-vol history as the IV proxy (see module docstring).
    iv_history = hv.tail(252)
    chain = adapter.get_chain(ticker, asof)
    spot = float(chain["underlying_price"].iloc[0])
    atm = chain.iloc[(chain["strike"] - spot).abs().argsort()].head(20)
    atm_iv = float(atm["iv"].mean())
    return {
        "ticker": ticker,
        "asof": asof.isoformat(),
        "iv_rank": round(float(iv_rank(iv_history)), 1),
        "iv_percentile": round(float(iv_percentile(iv_history)), 1),
        "atm_iv": round(atm_iv, 4),
        "hv_20": round(float(iv_history.iloc[-1]), 4) if not iv_history.empty else None,
    }


def run_named_screen(screen: str, ticker: str = "SPY", asof: date | None = None) -> dict[str, Any]:
    """Run a named screen over ``ticker``'s current option chain and summarize matches."""
    if screen not in SCREENS:
        raise ValueError(f"unknown screen {screen!r}; available: {sorted(SCREENS)}")
    ticker = ticker.upper().strip()
    asof = asof or date(2022, 6, 1)
    adapter = _adapter(ticker)
    chain = adapter.get_chain(ticker, asof).copy()
    bars = adapter.get_bars(ticker, asof - timedelta(days=120), asof)
    chain["hv"] = float(hv_windows(bars["close"])["hv_20"].dropna().iloc[-1])
    chain["mid"] = 0.5 * (chain["bid"] + chain["ask"])
    chain["rel_spread"] = (chain["ask"] - chain["bid"]) / chain["mid"].where(chain["mid"] > 0)
    iv_min, iv_max = chain["iv"].min(), chain["iv"].max()
    chain["iv_rank"] = 100.0 * (chain["iv"] - iv_min) / max(iv_max - iv_min, 1e-9)
    matched = run_screen(screen, chain)
    return {
        "screen": screen,
        "ticker": ticker,
        "matched": int(len(matched)),
        "total": int(len(chain)),
    }


# LLM tool-call schemas (Anthropic/OpenAI-compatible "function" definitions).
TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "get_iv_rank",
        "description": "Get the IV rank, IV percentile, ATM implied volatility and 20-day "
        "historical volatility for a ticker (SPY, QQQ, IWM).",
        "parameters": {
            "type": "object",
            "properties": {"ticker": {"type": "string", "description": "Equity ticker symbol"}},
            "required": ["ticker"],
        },
    },
    {
        "name": "run_named_screen",
        "description": "Run an options screen (cheap_by_hv, high_iv_rank, iv_hv_gap) for a "
        "ticker and report how many contracts matched.",
        "parameters": {
            "type": "object",
            "properties": {
                "screen": {"type": "string", "enum": sorted(SCREENS)},
                "ticker": {"type": "string"},
            },
            "required": ["screen"],
        },
    },
]

_DISPATCH = {"get_iv_rank": get_iv_rank, "run_named_screen": run_named_screen}


def dispatch_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Invoke a tool by name with keyword ``arguments`` (LLM tool-call entry point)."""
    if name not in _DISPATCH:
        raise ValueError(f"unknown tool {name!r}; available: {sorted(_DISPATCH)}")
    return _DISPATCH[name](**arguments)
