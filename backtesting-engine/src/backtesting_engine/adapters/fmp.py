"""Financial Modeling Prep (FMP) equity OHLCV adapter.

Primary source for daily equity bars. Reads ``FMP_API_KEY`` from the environment
and fails loudly if it is missing at call time (never at import). Token-bucket
rate-limited to fit the free tier (default 4 req/sec, 250 req/day). Wrap it in a
:class:`CachedDataSource` so idempotent re-runs hit the Parquet cache and bypass
the limiter entirely.
"""

from __future__ import annotations

import os
from datetime import date

import pandas as pd

from backtesting_engine.adapters.rate_limit import RateLimiter

FMP_BASE = "https://financialmodelingprep.com/api/v3"


class FMPEquityAdapter:
    """Equity OHLCV via FMP. Implements the :class:`DataSource` protocol."""

    name = "fmp"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        per_second: float = 4.0,
        per_day: float = 250.0,
        timeout: float = 30.0,
        session: object | None = None,
    ) -> None:
        self._api_key = api_key
        self.rate_limiter = RateLimiter.per_second_and_day(per_second, per_day)
        self.timeout = timeout
        self._session = session

    @property
    def api_key(self) -> str:
        key = self._api_key or os.environ.get("FMP_API_KEY")
        if not key:
            raise RuntimeError(
                "FMP_API_KEY is not set. Export it in your environment or pass "
                "api_key=... to FMPEquityAdapter."
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
        """Fetch daily OHLCV bars for ``symbol`` in ``[start, end]`` from FMP."""
        self.rate_limiter.acquire()
        session = self._get_session()
        url = f"{FMP_BASE}/historical-price-full/{symbol}"
        params = {
            "from": start.isoformat(),
            "to": end.isoformat(),
            "apikey": self.api_key,
        }
        resp = session.get(url, params=params, timeout=self.timeout)  # type: ignore[attr-defined]
        resp.raise_for_status()
        payload = resp.json()
        historical = payload.get("historical", []) if isinstance(payload, dict) else []
        if not historical:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = pd.DataFrame(historical)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        cols = ["open", "high", "low", "close", "volume"]
        return df[cols].astype("float64")

    def get_chain(self, symbol: str, asof: date) -> pd.DataFrame:
        raise NotImplementedError(
            "FMPEquityAdapter provides equity bars only; use an options adapter for chains."
        )
