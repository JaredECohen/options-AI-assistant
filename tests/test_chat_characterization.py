"""T7 safety net: chat text behaviour is byte-identical to the 762fe0d snapshots.

The snapshots were captured before app/services/chat_service.py was split into
app/chat/. Any intentional behaviour change must regenerate them in its own
commit with the diff reviewed.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.characterization.harness import build_service

SNAP = json.loads((Path(__file__).parent / "characterization" / "chat_snapshots.json").read_text())


@pytest.mark.parametrize("case_id", sorted(SNAP))
async def test_chat_matches_snapshot(case_id):
    case = SNAP[case_id]
    service, make_request = build_service()
    got = []
    for turn in case["turns"]:
        resp = await service.handle(make_request(turn, f"char-{case_id}"))
        got.append(resp.response_text)
    assert got == case["responses"]
