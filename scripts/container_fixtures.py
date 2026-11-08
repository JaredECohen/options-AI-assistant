"""Fixture services for the container boot test (scripts/container_check.sh).

Runs INSIDE the optionscoach image on a private docker network and serves:
  GET /.well-known/jwks.json      a throwaway RSA public key
  GET /api/v1/entitlements/me     mm.entitlements.v1 for whoever the token names
and writes a signed test token to /fixtures/token. Nothing here is a real
credential; the key pair is generated at start and discarded.
"""
from __future__ import annotations

import base64
import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
JWK = json.loads(RSAAlgorithm.to_jwk(KEY.public_key()))
JWK.update({"kid": "fixture", "use": "sig", "alg": "RS256"})
ISSUER = "http://fixtures:9000"
ORIGIN = "http://localhost:18080"


def token(sub: str) -> str:
    now = int(time.time())
    return jwt.encode({"sub": sub, "iss": ISSUER, "azp": ORIGIN, "iat": now, "nbf": now, "exp": now + 3600},
                      KEY, algorithm="RS256", headers={"kid": "fixture"})


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path == "/.well-known/jwks.json":
            return self._send(200, {"keys": [JWK]})
        if self.path == "/api/v1/entitlements/me":
            auth = self.headers.get("Authorization", "")
            if self.headers.get("X-Service-Token") != "fixture-service-token" or not auth.startswith("Bearer "):
                return self._send(401, {"error": "unauthorized"})
            payload = json.loads(base64.urlsafe_b64decode(auth.split(".")[1] + "=="))
            plan = "free" if payload["sub"].endswith("_free") else "pro"
            return self._send(200, {"contract": "mm.entitlements.v1", "external_id": payload["sub"],
                                    "account_state": "active", "beta_status": "approved", "plan": plan,
                                    "plan_source": "fixture"})
        return self._send(404, {"error": "not_found"})

    def log_message(self, *args):  # quiet
        pass


if __name__ == "__main__":
    with open("/fixtures/token_a", "w") as f:
        f.write(token("user_container_a"))
    with open("/fixtures/token_b", "w") as f:
        f.write(token("user_container_b_free"))
    HTTPServer(("0.0.0.0", 9000), Handler).serve_forever()
