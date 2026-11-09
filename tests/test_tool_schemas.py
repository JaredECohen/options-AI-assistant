"""Chat tool schemas must be accepted by the API's strict tool mode.

A keyword strict mode refuses fails the WHOLE request with a 400, so every chat
turn fails, and nothing offline notices: the tests script the Anthropic client.
That shipped once (maxItems on run_screen and compute_payoff, found 2026-11-09).
"""
from __future__ import annotations

import types

import pytest

from app.llm import tools as T


class _AllowAll:
    bearer = external_id = None

    def allows(self, _feature):
        return True


def _specs():
    state = types.SimpleNamespace(options_provider=None, snapshots=None, research=object())
    return {s.name: s for s in T.build_tools(_AllowAll(), state)}


def _keywords(schema):
    found = set()
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key != "properties":  # property names are not keywords
                found.add(key)
            found |= _keywords(value) if key != "properties" else set().union(
                *(_keywords(v) for v in value.values()))
    elif isinstance(schema, list):
        for item in schema:
            found |= _keywords(item)
    return found


def test_every_tool_is_strict_and_uses_only_supported_keywords():
    specs = _specs()
    assert set(specs) == {"get_vol_metrics", "run_screen", "compute_payoff", "get_stored_research"}
    for name, spec in specs.items():
        shape = spec.api_shape()
        assert shape["strict"] is True
        bad = _keywords(shape["input_schema"]) & T.STRICT_UNSUPPORTED_KEYWORDS
        assert not bad, f"{name} uses {sorted(bad)}, which strict tool use rejects"


async def test_caps_the_schema_cannot_express_are_enforced_in_the_handler():
    specs = _specs()
    with pytest.raises(T.ToolInputError, match="at most 5"):
        await specs["run_screen"].run({"preset": "high_iv_rank", "tickers": ["A", "B", "C", "D", "E", "F"]})
    leg = {"instrument": "stock", "side": "buy", "quantity": 1}
    with pytest.raises(T.ToolInputError, match="at most 8"):
        await specs["compute_payoff"].run({"ticker": "SPY", "legs": [leg] * 9})
