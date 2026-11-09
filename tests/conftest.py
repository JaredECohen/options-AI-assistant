"""Test harness.

- APP_ENV=test and blank provider credentials before the app is imported, so a
  developer .env can never route a test to a paid API.
- A socket guard refuses every non-loopback connect / DNS lookup. Tests that
  need HTTP use injected transports (httpx MockTransport) or fixtures.
"""
from __future__ import annotations

import os
import socket

os.environ["APP_ENV"] = "test"
for _name in ("ANTHROPIC_API_KEY", "TRADIER_API_TOKEN", "MARKETMOSAIC_SERVICE_TOKEN", "OPENAI_API_KEY"):
    os.environ[_name] = ""
os.environ.setdefault("LLM_PROVIDER", "heuristic")
os.environ.setdefault("OPTIONS_PROVIDER", "mock")

import pytest  # noqa: E402

_real_connect = socket.socket.connect
_real_getaddrinfo = socket.getaddrinfo
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _host_of(address) -> str | None:
    if isinstance(address, tuple) and address:
        return str(address[0])
    return None  # AF_UNIX etc.


def _guarded_connect(self, address):
    host = _host_of(address)
    if host is not None and host not in _LOOPBACK and not os.getenv("OC_ALLOW_NETWORK"):
        raise RuntimeError(f"test tried to open a network connection to {host!r}; tests must use fixtures")
    return _real_connect(self, address)


def _guarded_getaddrinfo(host, *args, **kwargs):
    if host not in _LOOPBACK and host is not None and not os.getenv("OC_ALLOW_NETWORK"):
        raise RuntimeError(f"test tried to resolve {host!r}; tests must use fixtures")
    return _real_getaddrinfo(host, *args, **kwargs)


socket.socket.connect = _guarded_connect  # type: ignore[method-assign]
socket.getaddrinfo = _guarded_getaddrinfo  # type: ignore[assignment]


@pytest.fixture
def anyio_backend():
    return "asyncio"
