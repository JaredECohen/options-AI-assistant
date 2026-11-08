"""Identity, entitlements and cross-user isolation through the real routes."""
from __future__ import annotations

import httpx
import pytest

from app.auth.entitlements import EntitlementCache, MarketMosaicEntitlementSource
from app.auth.tokens import AuthUnavailable
from tests.helpers import ORIGIN, Keys, auth, client, jwks_cache

PROTECTED = [("get", "/api/conversations"), ("get", "/api/positions"), ("get", "/api/screens"),
             ("get", "/api/quote?ticker=SPY"), ("get", "/api/me/export")]


@pytest.mark.parametrize("method,path", PROTECTED)
def test_missing_token_is_401(tmp_path, method, path):
    c, _ = client(tmp_path)
    r = getattr(c, method)(path)
    assert r.status_code == 401 and r.json()["error"] == "auth_required"


@pytest.mark.parametrize("kw,expected", [
    ({"exp_in": -120}, "token expired"),
    ({"nbf_in": 600}, "token not yet valid"),
    ({"iss": "https://evil.example"}, "token issuer not trusted"),
    ({"azp": "https://evil.example"}, "token authorized party not allowed"),
    ({"azp": None}, "token authorized party not allowed"),
    ({"alg": "HS256"}, "unexpected signing algorithm"),
    ({"kid": "unknown-kid"}, "unknown signing key"),
])
def test_bad_tokens_are_rejected(tmp_path, kw, expected):
    c, keys = client(tmp_path)
    r = c.get("/api/conversations", headers=auth(keys, **kw))
    assert r.status_code == 401, r.text
    assert r.json() == {"error": "auth_invalid", "detail": expected}


def test_forged_signature_rejected(tmp_path):
    c, keys = client(tmp_path)
    forger = Keys()  # different private key, same kid
    r = c.get("/api/conversations", headers={"Authorization": f"Bearer {forger.token('user_a')}"})
    assert r.status_code == 401 and r.json()["error"] == "auth_invalid"


def test_token_in_query_string_is_ignored(tmp_path):
    c, keys = client(tmp_path)
    r = c.get(f"/api/conversations?token={keys.token('user_a')}")
    assert r.status_code == 401


def test_jwks_unavailable_fails_closed(tmp_path):
    keys = Keys()
    c, _ = client(tmp_path, keys=keys, jwks=jwks_cache(keys, fail=True))
    r = c.get("/api/conversations", headers=auth(keys))
    assert r.status_code == 503 and r.json()["error"] == "auth_unavailable"


@pytest.mark.parametrize("row,status,code", [
    ({"account_state": "suspended", "beta_status": "approved", "plan": "pro"}, 403, "account_suspended"),
    ({"account_state": "active", "beta_status": "pending", "plan": "pro"}, 403, "beta_access_required"),
    ({"account_state": "active", "beta_status": "revoked", "plan": "pro"}, 403, "beta_access_required"),
])
def test_entitlement_denials(tmp_path, row, status, code):
    c, keys = client(tmp_path, entitlements={"*": row})
    r = c.get("/api/conversations", headers=auth(keys))
    assert r.status_code == status and r.json()["error"] == code


def test_plan_gate_on_backtests(tmp_path):
    c, keys = client(tmp_path, entitlements={"*": {"account_state": "active", "beta_status": "approved", "plan": "free"}})
    r = c.post("/api/backtests", json={"kind": "short_put"}, headers=auth(keys))
    assert r.status_code == 402 and r.json()["error"] == "plan_required"
    # free users still get the free features
    assert c.get("/api/conversations", headers=auth(keys)).status_code == 200


def test_backtests_blocked_with_reasons_for_pro(tmp_path):
    c, keys = client(tmp_path)
    r = c.post("/api/backtests", json={"kind": "short_put"}, headers=auth(keys))
    assert r.status_code == 503
    assert r.json()["error"] == "backtest_unavailable" and len(r.json()["blocked_by"]) == 2


def test_revoked_user_can_still_export_their_data(tmp_path):
    c, keys = client(tmp_path, entitlements={"*": {"account_state": "active", "beta_status": "revoked", "plan": "free"}})
    r = c.get("/api/me/export", headers=auth(keys))
    assert r.status_code == 200 and r.json()["format"] == "optionscoach.export.v1"


def _chat(c, keys, sub, message="what is a covered call", **extra):
    return c.post("/api/chat", json={"message": message, **extra}, headers=auth(keys, sub))


def test_conversations_are_owned(tmp_path):
    c, keys = client(tmp_path)
    r = _chat(c, keys, "user_a")
    assert r.status_code == 200
    conv = r.json()["conversation_id"]
    assert r.json()["served_by"]["mode"] in ("deterministic", "heuristic_demo")
    # A sees it; B gets 404 for read, append, clear and delete
    assert c.get(f"/api/conversations/{conv}/messages", headers=auth(keys, "user_a")).json()["messages"]
    assert c.get(f"/api/conversations/{conv}/messages", headers=auth(keys, "user_b")).status_code == 404
    assert _chat(c, keys, "user_b", conversation_id=conv).status_code == 404
    assert c.post("/api/clear", json={"conversation_id": conv}, headers=auth(keys, "user_b")).status_code == 404
    assert c.delete(f"/api/conversations/{conv}", headers=auth(keys, "user_b")).status_code == 404
    assert c.get("/api/conversations", headers=auth(keys, "user_b")).json()["items"] == []
    assert c.get("/api/me/export", headers=auth(keys, "user_b")).json()["conversations"] == []
    # A still has everything
    assert len(c.get("/api/conversations", headers=auth(keys, "user_a")).json()["items"]) == 1


def test_client_session_id_is_not_trusted(tmp_path):
    c, keys = client(tmp_path)
    a = _chat(c, keys, "user_a", session_id="shared-123").json()["conversation_id"]
    b = _chat(c, keys, "user_b", session_id="shared-123").json()["conversation_id"]
    assert a != b
    msgs_b = c.get(f"/api/conversations/{b}/messages", headers=auth(keys, "user_b")).json()["messages"]
    assert len(msgs_b) == 2  # only B's own turn


def test_history_is_durable_across_app_restarts(tmp_path):
    from tests.helpers import settings
    c, keys = client(tmp_path)
    s = c.app.state.settings
    conv = _chat(c, keys, "user_a").json()["conversation_id"]
    c2, _ = client(tmp_path, keys=keys, settings_overrides={"database_url": s.database_url})
    _ = settings  # noqa: F841
    assert len(c2.get(f"/api/conversations/{conv}/messages", headers=auth(keys)).json()["messages"]) == 2


def test_positions_and_screens_are_owned(tmp_path):
    c, keys = client(tmp_path)
    leg = {"instrument": "option", "option_type": "put", "side": "sell", "strike": 100, "expiration": "2026-12-18", "premium": 5}
    p = c.post("/api/positions", json={"name": "csp", "ticker": "SPY", "legs": [leg]}, headers=auth(keys, "user_a"))
    assert p.status_code == 200
    pid = p.json()["id"]
    assert c.delete(f"/api/positions/{pid}", headers=auth(keys, "user_b")).status_code == 404
    assert c.put(f"/api/positions/{pid}", json={"name": "x", "ticker": "SPY", "legs": [leg]}, headers=auth(keys, "user_b")).status_code == 404
    assert c.get("/api/positions", headers=auth(keys, "user_b")).json()["items"] == []
    dup = c.post("/api/positions", json={"name": "csp", "ticker": "SPY", "legs": [leg]}, headers=auth(keys, "user_a"))
    assert dup.status_code == 409
    s = c.post("/api/screens", json={"name": "hi", "definition": {"universe": ["SPY"], "filters": []}}, headers=auth(keys, "user_a"))
    assert s.status_code == 200
    sid = s.json()["id"]
    assert c.get(f"/api/screens/{sid}", headers=auth(keys, "user_b")).status_code == 404
    assert c.get(f"/api/screens/{sid}", headers=auth(keys, "user_a")).status_code == 200
    bad = c.post("/api/screens", json={"name": "bad", "definition": {"universe": ["SPY"], "filters": [{"field": "pe", "op": "<", "value": 1}]}},
                 headers=auth(keys, "user_a"))
    assert bad.status_code == 422 and "unknown field" in bad.json()["detail"]


def test_jobs_are_owned(tmp_path):
    from app.jobs import queue
    c, keys = client(tmp_path)
    _chat(c, keys, "user_a")
    _chat(c, keys, "user_b")
    db = c.app.state.db
    from app.db import repo
    uid_a = repo.ensure_user(db, "user_a")
    job = queue.enqueue(db, "backtest", "k1", {}, user_id=uid_a)
    assert c.get(f"/api/jobs/{job.id}", headers=auth(keys, "user_a")).status_code == 200
    assert c.get(f"/api/jobs/{job.id}", headers=auth(keys, "user_b")).status_code == 404
    assert c.post(f"/api/jobs/{job.id}/cancel", headers=auth(keys, "user_b")).status_code == 404
    assert c.post(f"/api/jobs/{job.id}/cancel", headers=auth(keys, "user_a")).json()["status"] == "cancelled"


def test_delete_my_data(tmp_path):
    c, keys = client(tmp_path)
    _chat(c, keys, "user_a")
    assert c.delete("/api/me/data", headers=auth(keys)).status_code == 200
    # the account is tombstoned: further requests with the same identity are refused
    assert c.get("/api/conversations", headers=auth(keys)).status_code == 403


async def test_marketmosaic_entitlements_contract_and_fail_closed():
    calls = {"n": 0, "fail": False}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert request.headers["authorization"] == "Bearer tok" and request.headers["x-service-token"] == "svc"
        if calls["fail"]:
            return httpx.Response(503)
        return httpx.Response(200, json={"contract": "mm.entitlements.v1", "external_id": "user_a",
                                         "account_state": "active", "beta_status": "approved", "plan": "pro"})

    clock = {"t": 0.0}
    source = MarketMosaicEntitlementSource("https://mm.internal", "svc", transport=httpx.MockTransport(handler))
    cache = EntitlementCache(source, ttl_seconds=60, clock=lambda: clock["t"])
    ent = await cache.get("user_a", "tok")
    assert ent.plan == "pro" and ent.source == "marketmosaic"
    await cache.get("user_a", "tok")
    assert calls["n"] == 1  # cached within TTL
    calls["fail"] = True
    clock["t"] = 61.0
    with pytest.raises(AuthUnavailable):
        await cache.get("user_a", "tok")  # stale beyond TTL + upstream down -> closed


async def test_marketmosaic_entitlements_subject_mismatch_refused():
    from app.auth.entitlements import EntitlementError

    def handler(request):
        return httpx.Response(200, json={"contract": "mm.entitlements.v1", "external_id": "someone_else",
                                         "account_state": "active", "beta_status": "approved", "plan": "pro"})

    source = MarketMosaicEntitlementSource("https://mm.internal", "svc", transport=httpx.MockTransport(handler))
    with pytest.raises(EntitlementError):
        await source.fetch("user_a", "tok")


def test_cors_is_exact(tmp_path):
    c, _ = client(tmp_path)
    ok = c.options("/api/chat", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"})
    assert ok.headers.get("access-control-allow-origin") == ORIGIN
    bad = c.options("/api/chat", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in bad.headers


async def test_marketmosaic_suspended_is_an_answer_not_an_outage():
    def handler(request):
        return httpx.Response(403, json={"detail": {"code": "account_suspended"}})

    source = MarketMosaicEntitlementSource("https://mm.internal", "svc", transport=httpx.MockTransport(handler))
    ent = await source.fetch("user_a", "tok")
    assert ent.account_state == "suspended" and ent.denial_for("chat") == (403, "account_suspended")

    def other_403(request):
        return httpx.Response(403, json={"detail": {"code": "something_else"}})

    source = MarketMosaicEntitlementSource("https://mm.internal", "svc", transport=httpx.MockTransport(other_403))
    with pytest.raises(AuthUnavailable):
        await source.fetch("user_a", "tok")
