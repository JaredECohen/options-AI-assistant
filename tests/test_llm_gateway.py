"""Model path: attribution, accounting, failure handling, tools, validation.
All calls go to a scripted transport; nothing reaches Anthropic."""
from __future__ import annotations

from sqlalchemy import select

from app.db.models import LLMUsageEvent
from app.llm.tools import numbers_validator
from tests.helpers import ScriptedAnthropic, auth, client, message

OFF_SCRIPT = "Is there a reason someone might hedge an options book with variance swaps?"


def _client(tmp_path, script, **overrides):
    s = {"llm_provider": "anthropic", "anthropic_api_key": "sk-test", "llm_circuit_failures": 2,
         "llm_circuit_cooldown_seconds": 3600.0, **overrides}
    return client(tmp_path, settings_overrides=s, llm_client=script.client())


def _usage(c):
    with c.app.state.db.session() as s:
        return s.execute(select(LLMUsageEvent).order_by(LLMUsageEvent.id)).scalars().all()


def test_success_is_attributed_and_accounted(tmp_path):
    script = ScriptedAnthropic([message("Summary: variance swaps hedge vol.", input_tokens=1200, output_tokens=300)])
    c, keys = _client(tmp_path, script)
    r = c.post("/api/chat", json={"message": OFF_SCRIPT, "mode": "freeform"}, headers=auth(keys))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["served_by"]["mode"] == "llm" and body["served_by"]["model"] == "claude-haiku-5-5"
    assert body["correlation_id"] and r.headers["x-request-id"] == body["correlation_id"]
    sent = script.requests[0]
    assert sent["model"] == "claude-haiku-5-5"
    assert sent["output_config"] == {"effort": "low"}
    assert "temperature" not in sent and sent["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert all(t["strict"] is True and t["input_schema"]["additionalProperties"] is False for t in sent["tools"])
    rows = _usage(c)
    assert len(rows) == 1 and rows[0].outcome == "ok" and rows[0].input_tokens == 1200
    assert rows[0].est_cost_usd > 0 and rows[0].pricing_version and rows[0].correlation_id == body["correlation_id"]


def test_rate_limited_is_controlled_and_opens_breaker_then_recovers(tmp_path):
    script = ScriptedAnthropic([429, 429, message("Summary: ok")])
    c, keys = _client(tmp_path, script)
    for _ in range(2):
        r = c.post("/api/chat", json={"message": OFF_SCRIPT}, headers=auth(keys))
        assert r.status_code == 503 and r.json()["code"] == "rate_limited"
    r = c.post("/api/chat", json={"message": OFF_SCRIPT}, headers=auth(keys))
    assert r.status_code == 503 and r.json()["code"] == "circuit_open"  # no call made
    assert len(script.requests) == 2
    c.app.state.llm.breaker.opened_at = -1e9  # cooldown elapsed -> half-open trial
    r = c.post("/api/chat", json={"message": OFF_SCRIPT}, headers=auth(keys))
    assert r.status_code == 200 and c.app.state.llm.breaker.state == "closed"


def test_explicit_heuristic_fallback_is_labelled_and_bounded(tmp_path):
    script = ScriptedAnthropic([500, 500])
    c, keys = _client(tmp_path, script, llm_fallback="heuristic")
    r = c.post("/api/chat", json={"message": OFF_SCRIPT}, headers=auth(keys))
    assert r.status_code == 200
    assert r.json()["served_by"] == {"mode": "heuristic_fallback", "provider": "heuristic", "model": None,
                                     "fallback_reason": "upstream_error", "tools_used": [], "escalated": False}


def test_refusal_and_truncation_never_return_partial_text(tmp_path):
    script = ScriptedAnthropic([message("partial", stop="refusal"), message("half an answ", stop="max_tokens")])
    c, keys = _client(tmp_path, script)
    r1 = c.post("/api/chat", json={"message": OFF_SCRIPT}, headers=auth(keys))
    r2 = c.post("/api/chat", json={"message": OFF_SCRIPT}, headers=auth(keys))
    assert (r1.status_code, r1.json()["code"]) == (422, "refusal")
    assert (r2.status_code, r2.json()["code"]) == (502, "truncated")
    assert "partial" not in r1.text and "half an answ" not in r2.text
    assert [u.outcome for u in _usage(c)] == ["refusal", "truncated"]


def test_missing_key_is_not_configured_not_heuristic(tmp_path):
    c, keys = client(tmp_path, settings_overrides={"llm_provider": "anthropic", "anthropic_api_key": ""})
    r = c.post("/api/chat", json={"message": OFF_SCRIPT}, headers=auth(keys))
    assert r.status_code == 503 and r.json()["code"] == "not_configured"
    # deterministic routes still work without a model
    r = c.post("/api/chat", json={"message": "what is theta"}, headers=auth(keys))
    assert r.status_code == 200 and r.json()["served_by"]["mode"] == "deterministic"


def test_user_and_global_budgets(tmp_path):
    script = ScriptedAnthropic([message("Summary: a", input_tokens=900, output_tokens=200)])
    c, keys = _client(tmp_path, script, llm_user_daily_token_budget=1000)
    assert c.post("/api/chat", json={"message": OFF_SCRIPT}, headers=auth(keys)).status_code == 200
    r = c.post("/api/chat", json={"message": OFF_SCRIPT}, headers=auth(keys))
    assert r.status_code == 429 and r.json()["code"] == "budget_user"
    script2 = ScriptedAnthropic([message("Summary: b", input_tokens=900, output_tokens=200)])
    c2, keys2 = _client(tmp_path, script2, llm_global_daily_token_budget=1000)
    assert c2.post("/api/chat", json={"message": OFF_SCRIPT}, headers=auth(keys2, "user_a")).status_code == 200
    r = c2.post("/api/chat", json={"message": OFF_SCRIPT}, headers=auth(keys2, "user_b"))
    assert r.status_code == 429 and r.json()["code"] == "budget_global"


def test_per_user_rate_limit(tmp_path):
    script = ScriptedAnthropic([message("Summary: x")] * 5)
    c, keys = _client(tmp_path, script, rate_limit_per_min=2)
    codes = [c.post("/api/chat", json={"message": OFF_SCRIPT}, headers=auth(keys)).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_tool_loop_uses_deterministic_payoff_and_validates_numbers(tmp_path):
    legs = [{"instrument": "option", "option_type": "put", "side": "sell", "strike": 100, "expiration": "2026-12-18",
             "quantity": 1, "premium": 5}]
    script = ScriptedAnthropic([
        message(stop="tool_use", tool_uses=[("compute_payoff", {"ticker": "SPY", "legs": legs})]),
        message("Summary: Max loss: $4,500 if the stock falls."),  # wrong (old chart-bounded bug)
        message(stop="tool_use", tool_uses=[("compute_payoff", {"ticker": "SPY", "legs": legs})], model="claude-sonnet-5-5"),
        message("Summary: Max loss: $9,500 at a price of zero; max profit $500.", model="claude-sonnet-5-5"),
    ])
    c, keys = _client(tmp_path, script, llm_escalation_enabled=True)
    r = c.post("/api/chat", json={"message": OFF_SCRIPT}, headers=auth(keys))
    assert r.status_code == 200, r.text
    served = r.json()["served_by"]
    assert served["escalated"] is True and served["model"] == "claude-sonnet-5-5"
    assert served["tools_used"] == ["compute_payoff", "compute_payoff"]
    assert "9,500" in r.json()["response_text"]
    # the tool result sent back to the model carries the exact bound
    tool_result = script.requests[1]["messages"][-1]["content"][0]
    assert '"max_loss": 9500.0' in tool_result["content"]


def test_invalid_numbers_without_escalation_are_not_shown(tmp_path):
    legs = [{"instrument": "option", "option_type": "call", "side": "sell", "strike": 100, "expiration": "2026-12-18",
             "quantity": 1, "premium": 4}]
    script = ScriptedAnthropic([
        message(stop="tool_use", tool_uses=[("compute_payoff", {"ticker": "SPY", "legs": legs})]),
        message("Summary: Max loss: $10,000."),
    ])
    c, keys = _client(tmp_path, script)
    r = c.post("/api/chat", json={"message": OFF_SCRIPT}, headers=auth(keys))
    assert r.status_code == 502 and r.json()["code"] == "invalid_output"
    assert "10,000" not in r.text


def test_tool_entitlement_is_rechecked(tmp_path):
    script = ScriptedAnthropic([
        message(stop="tool_use", tool_uses=[("run_screen", {"preset": "high_iv_rank", "tickers": ["SPY"]})]),
        message("Summary: screening is unavailable to you."),
    ])
    ent = {"*": {"account_state": "active", "beta_status": "approved", "plan": "pro"}}
    c, keys = client(tmp_path, entitlements=ent, llm_client=script.client(),
                     settings_overrides={"llm_provider": "anthropic", "anthropic_api_key": "sk-test"})
    from app.auth import entitlements as E
    original = dict(E.FEATURE_MIN_PLAN)
    E.FEATURE_MIN_PLAN["screens"] = "pro"
    E.FEATURE_MIN_PLAN["chat"] = "none"
    try:
        c.app.state.entitlements.source.table["*"]["plan"] = "free"
        r = c.post("/api/chat", json={"message": OFF_SCRIPT}, headers=auth(keys))
    finally:
        E.FEATURE_MIN_PLAN.clear()
        E.FEATURE_MIN_PLAN.update(original)
    assert r.status_code == 200
    result = script.requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True and "not_entitled" in result["content"]


def test_numbers_validator_unit():
    out = {"max_loss": "unlimited", "max_profit": 400.0, "breakevens": [104.0], "data_status": "delayed"}
    tr = [{"tool": "compute_payoff", "output": out}]
    assert numbers_validator("Max profit: $400. Max loss is unlimited.", tr) is None
    assert numbers_validator("Max loss: $1,200", tr) is not None
    assert numbers_validator("This uses real-time quotes.", tr) is not None
    assert numbers_validator("Maximum profit of $4.00 per share", tr) is None  # per-share form of 400
