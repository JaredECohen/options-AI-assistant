"""A small thread-safe token-bucket rate limiter.

Used by the live adapters to stay within free-tier request budgets. Two buckets
are typically combined: a fast per-second/minute bucket and a slow per-day
bucket. Cache hits bypass the limiter entirely (the adapters call ``acquire``
only on a cache miss).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class TokenBucket:
    """Classic token bucket.

    ``capacity`` tokens refill at ``refill_rate`` tokens per second. ``acquire``
    blocks until a token is available (or returns immediately if one is).

    ``monotonic`` is the clock source; injecting a fake clock (together with a
    ``sleeper`` that advances it) makes the limiter fully deterministic in tests.
    """

    capacity: float
    refill_rate: float
    monotonic: Callable[[], float] = time.monotonic
    _tokens: float = field(init=False)
    _last: float = field(init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def __post_init__(self) -> None:
        if self.capacity <= 0 or self.refill_rate <= 0:
            raise ValueError("capacity and refill_rate must be positive")
        self._tokens = self.capacity
        self._last = self.monotonic()

    def _refill(self) -> None:
        now = self.monotonic()
        elapsed = now - self._last
        self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_rate)
        self._last = now

    def acquire(
        self, tokens: float = 1.0, *, sleeper: Callable[[float], None] | None = None
    ) -> float:
        """Block until ``tokens`` are available; return the seconds waited.

        ``sleeper`` is an injectable callable (defaults to :func:`time.sleep`) used
        by tests to avoid real waiting.
        """
        sleep = sleeper if sleeper is not None else time.sleep
        waited = 0.0
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return waited
                deficit = tokens - self._tokens
                wait_for = deficit / self.refill_rate
            sleep(wait_for)
            waited += wait_for


@dataclass
class RateLimiter:
    """Combines any number of token buckets; ``acquire`` waits on all of them."""

    buckets: list[TokenBucket] = field(default_factory=list)

    @classmethod
    def per_minute_and_day(cls, per_minute: float, per_day: float) -> RateLimiter:
        """Build a limiter from a per-minute and a per-day request budget."""
        return cls(
            buckets=[
                TokenBucket(capacity=per_minute, refill_rate=per_minute / 60.0),
                TokenBucket(capacity=per_day, refill_rate=per_day / 86_400.0),
            ]
        )

    @classmethod
    def per_second_and_day(cls, per_second: float, per_day: float) -> RateLimiter:
        """Build a limiter from a per-second and a per-day request budget."""
        return cls(
            buckets=[
                TokenBucket(capacity=per_second, refill_rate=per_second),
                TokenBucket(capacity=per_day, refill_rate=per_day / 86_400.0),
            ]
        )

    def acquire(self, *, sleeper: Callable[[float], None] | None = None) -> float:
        total = 0.0
        for bucket in self.buckets:
            total += bucket.acquire(1.0, sleeper=sleeper)
        return total
