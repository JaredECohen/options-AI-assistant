"""On-disk Parquet cache and a :class:`CachedDataSource` wrapper.

Caching is implemented once here so individual adapters never reimplement it. A
:class:`CachedDataSource` wraps any :class:`DataSource`: on a **cache hit** it
returns immediately and never touches the wrapped adapter (so the adapter's rate
limiter is bypassed entirely); on a **miss** it delegates to the adapter (which
performs rate-limiting + network I/O) and persists the result.

Cache files live under ``~/.cache/backtesting-engine/<namespace>/`` and are keyed
by a stable hash of ``(method, params)``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date
from pathlib import Path

import pandas as pd

from backtesting_engine.core.protocols import DataSource

logger = logging.getLogger("backtesting_engine.cache")

DEFAULT_CACHE_ROOT = Path.home() / ".cache" / "backtesting-engine"


class DiskCache:
    """A tiny content-addressed Parquet cache for DataFrames."""

    def __init__(self, namespace: str, root: Path | None = None) -> None:
        self.root = (root or DEFAULT_CACHE_ROOT) / namespace
        self.namespace = namespace

    def _key(self, endpoint: str, params: dict[str, object]) -> str:
        payload = json.dumps({"endpoint": endpoint, "params": params}, sort_keys=True, default=str)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
        return f"{endpoint}-{digest}.parquet"

    def path(self, endpoint: str, params: dict[str, object]) -> Path:
        return self.root / self._key(endpoint, params)

    def get(self, endpoint: str, params: dict[str, object]) -> pd.DataFrame | None:
        path = self.path(endpoint, params)
        if path.exists():
            logger.debug("cache hit %s %s", self.namespace, endpoint)
            return pd.read_parquet(path)
        return None

    def put(self, endpoint: str, params: dict[str, object], df: pd.DataFrame) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        df.to_parquet(self.path(endpoint, params))
        logger.debug("cache store %s %s", self.namespace, endpoint)


class CachedDataSource:
    """Wrap a :class:`DataSource` with a transparent on-disk Parquet cache.

    Implements the :class:`DataSource` protocol itself, so it is a drop-in
    replacement anywhere a data source is expected.
    """

    def __init__(
        self,
        source: DataSource,
        namespace: str,
        *,
        cache_root: Path | None = None,
        enabled: bool = True,
    ) -> None:
        self.source = source
        self.cache = DiskCache(namespace, cache_root)
        self.enabled = enabled

    def get_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        params = {"symbol": symbol, "start": start, "end": end}
        if self.enabled:
            cached = self.cache.get("bars", params)
            if cached is not None:
                return cached
        df = self.source.get_bars(symbol, start, end)
        if self.enabled:
            self.cache.put("bars", params, df)
        return df

    def get_chain(self, symbol: str, asof: date) -> pd.DataFrame:
        params = {"symbol": symbol, "asof": asof}
        if self.enabled:
            cached = self.cache.get("chain", params)
            if cached is not None:
                return cached
        df = self.source.get_chain(symbol, asof)
        if self.enabled:
            self.cache.put("chain", params, df)
        return df
