"""Clerk session-token verification (networkless after JWKS is cached).

Mirrors MarketMosaic's backend/app/auth/jwks.py so both services accept and
reject the same tokens, with two deliberate tightenings:
- `azp` is REQUIRED and must be in CLERK_AUTHORIZED_PARTIES whenever the list
  is configured (MarketMosaic skips the check when azp is absent). OptionsCoach
  is only ever called from a browser origin or by a server forwarding a browser
  token, so a token without azp is not one we should accept.
- Staging/production refuse to boot with an empty allowlist
  (app.config.validate_for_startup).

Checks: alg == RS256 (before any key work), kid present and known, signature,
exact iss, exp/nbf/iat with bounded leeway, non-empty sub, azp allowlist.
JWKS: cached for an hour, refresh throttled, unknown kid forces one refresh,
stale keys usable for at most 24h during an outage; beyond that verification
fails CLOSED with AuthUnavailable (HTTP 503), never open.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm

ALGORITHMS = ("RS256",)
LIFESPAN = 3600.0
REFRESH_THROTTLE = 60.0
STALE_MAX = 24 * 3600.0
FETCH_TIMEOUT = 5.0


class TokenInvalid(Exception):
    """401: the presented token must not be accepted."""


class AuthUnavailable(Exception):
    """503: we cannot currently verify any token (fail closed)."""


@dataclass
class JWKSCache:
    url: str
    transport: httpx.BaseTransport | None = None
    clock: callable = time.monotonic  # type: ignore[valid-type]
    _keys: dict = field(default_factory=dict)
    _fetched_at: float | None = None
    _last_attempt: float = -1e18
    _last_forced: float = -1e18
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _fetch(self) -> None:
        self._last_attempt = self.clock()
        try:
            with httpx.Client(timeout=FETCH_TIMEOUT, transport=self.transport) as client:
                resp = client.get(self.url)
                resp.raise_for_status()
                data = resp.json()
            keys = {}
            for jwk in data.get("keys", []):
                if jwk.get("kty") == "RSA" and jwk.get("kid") and jwk.get("use", "sig") == "sig":
                    keys[jwk["kid"]] = RSAAlgorithm.from_jwk(json.dumps(jwk))
            if not keys:
                raise ValueError("JWKS contained no RSA signing keys")
        except Exception:
            return  # keep whatever we had; staleness is enforced in get_key
        self._keys = keys
        self._fetched_at = self.clock()

    def get_key(self, kid: str):
        with self._lock:
            now = self.clock()
            age = None if self._fetched_at is None else now - self._fetched_at
            if (age is None or age > LIFESPAN) and now - self._last_attempt >= REFRESH_THROTTLE:
                self._fetch()
            elif kid not in self._keys and now - self._last_forced >= REFRESH_THROTTLE:
                self._last_forced = now
                self._fetch()
            age = None if self._fetched_at is None else self.clock() - self._fetched_at
            if age is None or age > STALE_MAX:
                raise AuthUnavailable("signing keys unavailable")
            key = self._keys.get(kid)
        if key is None:
            raise TokenInvalid("unknown signing key")
        return key

    @property
    def loaded(self) -> bool:
        return self._fetched_at is not None


@dataclass(frozen=True)
class VerifiedToken:
    sub: str
    sid: str | None
    azp: str | None
    claims: dict


def verify_token(
    token: str,
    *,
    jwks: JWKSCache,
    issuer: str,
    authorized_parties: tuple[str, ...],
    leeway: int = 10,
) -> VerifiedToken:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise TokenInvalid("malformed token") from exc
    if header.get("alg") not in ALGORITHMS:
        raise TokenInvalid("unexpected signing algorithm")
    kid = header.get("kid")
    if not kid:
        raise TokenInvalid("token has no key id")
    key = jwks.get_key(kid)
    try:
        claims = jwt.decode(
            token,
            key=key,
            algorithms=list(ALGORITHMS),
            issuer=issuer,
            leeway=leeway,
            options={"require": ["exp", "iat", "sub", "iss"], "verify_aud": False},
        )
    except jwt.InvalidIssuerError as exc:
        raise TokenInvalid("token issuer not trusted") from exc
    except jwt.ExpiredSignatureError as exc:
        raise TokenInvalid("token expired") from exc
    except jwt.ImmatureSignatureError as exc:
        raise TokenInvalid("token not yet valid") from exc
    except jwt.PyJWTError as exc:
        raise TokenInvalid("token rejected") from exc
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise TokenInvalid("token has no subject")
    azp = claims.get("azp")
    if authorized_parties:
        if not isinstance(azp, str) or azp not in authorized_parties:
            raise TokenInvalid("token authorized party not allowed")
    sid = claims.get("sid") if isinstance(claims.get("sid"), str) else None
    return VerifiedToken(sub=sub, sid=sid, azp=azp if isinstance(azp, str) else None, claims=claims)
