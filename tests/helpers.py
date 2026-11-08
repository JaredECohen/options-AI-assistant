"""Test fixtures: real RSA keys, a fake JWKS endpoint, fake MarketMosaic and a
scripted Anthropic transport. No network, no paid calls."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field

import anthropic
import httpx
import httpx2
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from app.auth.entitlements import StaticEntitlementSource
from app.auth.tokens import JWKSCache
from app.config import Settings
from app.main import create_app

ISSUER = "https://clerk.test.example"
ORIGIN = "https://options.test.example"
KID = "test-key-1"


@dataclass
class Keys:
    private: rsa.RSAPrivateKey = field(default_factory=lambda: rsa.generate_private_key(public_exponent=65537, key_size=2048))
    kid: str = KID

    def jwks(self) -> dict:
        jwk = json.loads(RSAAlgorithm.to_jwk(self.private.public_key()))
        jwk.update({"kid": self.kid, "use": "sig", "alg": "RS256"})
        return {"keys": [jwk]}

    def token(self, sub: str = "user_a", *, iss: str = ISSUER, azp: str | None = ORIGIN, exp_in: int = 60,
              nbf_in: int = 0, alg: str = "RS256", kid: str | None = None, key=None, **claims) -> str:
        now = int(time.time())
        payload = {"sub": sub, "iss": iss, "iat": now, "nbf": now + nbf_in, "exp": now + exp_in, "sid": "sess_1", **claims}
        if azp is not None:
            payload["azp"] = azp
        headers = {"kid": kid or self.kid}
        if alg == "HS256":
            return jwt.encode(payload, "not-a-real-secret-but-long-enough-for-hmac-test", algorithm="HS256", headers=headers)
        return jwt.encode(payload, key or self.private, algorithm=alg, headers=headers)


def jwks_cache(keys: Keys, *, fail: bool = False) -> JWKSCache:
    def handler(request: httpx.Request) -> httpx.Response:
        if fail:
            return httpx.Response(503)
        return httpx.Response(200, json=keys.jwks())

    return JWKSCache("https://clerk.test.example/.well-known/jwks.json", transport=httpx.MockTransport(handler))


class ScriptedAnthropic:
    """Returns queued responses in order; records request bodies."""

    def __init__(self, responses: list):
        self.responses = list(responses)
        self.requests: list[dict] = []

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(json.loads(request.content))
        item = self.responses.pop(0)
        if isinstance(item, int):
            return httpx2.Response(item, json={"type": "error", "error": {"type": "api_error", "message": "x"}})
        return httpx2.Response(200, json=item)

    def client(self) -> anthropic.AsyncAnthropic:
        return anthropic.AsyncAnthropic(api_key="sk-test", max_retries=0,
                                        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(self.handler)))


def message(text: str = "", *, stop: str = "end_turn", tool_uses: list | None = None, model: str = "claude-haiku-5-5",
            input_tokens: int = 100, output_tokens: int = 50) -> dict:
    content = []
    if text:
        content.append({"type": "text", "text": text})
    for name, args in tool_uses or []:
        content.append({"type": "tool_use", "id": "toolu_" + uuid.uuid4().hex[:8], "name": name, "input": args})
    return {"id": "msg_" + uuid.uuid4().hex[:8], "type": "message", "role": "assistant", "model": model, "content": content,
            "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens, "cache_read_input_tokens": 0}}


def settings(tmp_path, **overrides) -> Settings:
    base = dict(app_env="test", auth_enabled=True, clerk_issuer=ISSUER,
                clerk_jwks_url="https://clerk.test.example/.well-known/jwks.json", clerk_authorized_parties=(ORIGIN,),
                database_url=f"sqlite:///{tmp_path}/oc-{uuid.uuid4().hex}.db", llm_provider="heuristic",
                options_provider="mock", allowed_origins=(ORIGIN,))
    base.update(overrides)
    return Settings(**base)


def client(tmp_path, *, keys: Keys | None = None, entitlements: dict | None = None, settings_overrides: dict | None = None,
           **app_kwargs) -> tuple[TestClient, Keys]:
    keys = keys or Keys()
    s = settings(tmp_path, **(settings_overrides or {}))
    app_kwargs.setdefault("jwks", jwks_cache(keys))
    app_kwargs.setdefault("entitlement_source", StaticEntitlementSource(entitlements or {
        "*": {"account_state": "active", "beta_status": "approved", "plan": "pro"}}))
    app = create_app(s, **app_kwargs)
    return TestClient(app), keys


def auth(keys: Keys, sub: str = "user_a", **kw) -> dict:
    return {"Authorization": f"Bearer {keys.token(sub, **kw)}"}
