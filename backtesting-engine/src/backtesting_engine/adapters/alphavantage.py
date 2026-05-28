"""Alpha Vantage historical options-chain adapter.

Primary source for historical option chains via the ``HISTORICAL_OPTIONS``
endpoint. Reads ``ALPHA_VANTAGE_API_KEY`` from the environment and fails loudly if
missing at call time. Token-bucket rate-limited for the free tier (default 5
req/min, 25 req/day). Wrap in a :class:`CachedDataSource` for caching.

Returns a normalized chain DataFrame with columns: ``expiry, strike, type, bid,
ask, last, mark, volume, oi, iv, delta, gamma, theta, vega, rho``.
"""

from __future__ import annotations

import os
from datetime import date

import pandas as pd

from backtesting_engine.adapters.rate_limit import RateLimiter

AV_BASE = "https://www.alphavantage.co/query"

# Map the Alpha Vantage field names onto our normalized schema.
_FIELD_MAP = {
    "expiration": "expiry",
    "strike": "strike",
    "type": "type",
    "bid": "bid",
    "ask": "ask",
    "last": "last",
    "mark": "mark",
    "volume": "volume",
    "open_interest": "oi",
    "implied_volatility": "iv",
    "delta": "delta",
    "gamma": "gamma",
    "theta": "theta",
    "vega": "vega",
    "rho": "rho",
}


class AlphaVantageOptionsAdapter:
    """Historical option chains via Alpha Vantage. Implements :class:`DataSource`."""

    name = "alphavantage"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        per_minute: float = 5.0,
        per_day: float = 25.0,
        timeout: float = 30.0,
        session: object | None = None,
    ) -> None:
        self._api_key = api_key
        self.rate_limiter = RateLimiter.per_minute_and_day(per_minute, per_day)
        self.timeout = timeout
        self._session = session

    @property
    def api_key(self) -> str:
        key = self._api_key or os.environ.get("ALPHA_VANTAGE_API_KEY")
        if not key:
            raise RuntimeError(
                "ALPHA_VANTAGE_API_KEY is not set. Export it in your environment or "
                "pass api_key=... to AlphaVantageOptionsAdapter."
            )
        return key

    def _get_session(self) -> object:
        if self._session is not None:
            return self._session
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - import guard
            raise RuntimeError(
                "The 'requests' package is required for live adapters. "
                "Install with: pip install 'backtesting-engine[http]'"
            ) from exc
        self._session = requests.Session()
        return self._session

    def get_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        raise NotImplementedError("AlphaVantageOptionsAdapter provides option chains only.")

    def _normalize(self, records: list[dict[str, object]]) -> pd.DataFrame:
        out_cols = list(dict.fromkeys(_FIELD_MAP.values()))
        if not records:
            return pd.DataFrame(columns=out_cols)
        df = pd.DataFrame(records).rename(columns=_FIELD_MAP)
        for col in out_cols:
            if col not in df.columns:
                df[col] = pd.NA
        df = df[out_cols]
        df["expiry"] = pd.to_datetime(df["expiry"]).dt.date
        df["type"] = df["type"].astype(str).str.lower()
        numeric = [
            "strike",
            "bid",
            "ask",
            "last",
            "mark",
            "volume",
            "oi",
            "iv",
            "delta",
            "gamma",
            "theta",
            "vega",
            "rho",
        ]
        for col in numeric:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        # If the feed omits 'mark', synthesize it from the mid.
        missing_mark = df["mark"].isna()
        df.loc[missing_mark, "mark"] = (df["bid"] + df["ask"]) / 2.0
        return df

    def get_chain(self, symbol: str, asof: date) -> pd.DataFrame:
        """Fetch and normalize the option chain for ``symbol`` as of ``asof``."""
        self.rate_limiter.acquire()
        session = self._get_session()
        params = {
            "function": "HISTORICAL_OPTIONS",
            "symbol": symbol,
            "date": asof.isoformat(),
            "apikey": self.api_key,
        }
        resp = session.get(AV_BASE, params=params, timeout=self.timeout)  # type: ignore[attr-defined]
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, dict) and ("Note" in payload or "Information" in payload):
            raise RuntimeError(f"Alpha Vantage throttled or errored: {payload}")
        records = payload.get("data", []) if isinstance(payload, dict) else []
        return self._normalize(records)
