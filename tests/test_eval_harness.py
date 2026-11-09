"""P10: a judge that fails, refuses or answers ambiguously is INCONCLUSIVE, never a heuristic pass."""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

from tests.helpers import ScriptedAnthropic, message

spec = importlib.util.spec_from_file_location("run_eval", pathlib.Path("eval/run_eval.py"))
run_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_eval)

MAAJ = {"type": "rubric", "rubric": ["mentions risk"]}


@pytest.mark.parametrize("reply,expected", [("PASS\nfine", True), ("FAIL\nmissing risk", False)])
async def test_judge_verdicts(reply, expected):
    judge = (ScriptedAnthropic([message(reply)]).client(), "claude-sonnet-5-5")
    assert await run_eval.llm_judge(judge, MAAJ, "answer") is expected


@pytest.mark.parametrize("response", [500, message("maybe?"), message("PASS", stop="max_tokens"), message("", stop="refusal")])
async def test_judge_problems_are_inconclusive(response):
    judge = (ScriptedAnthropic([response]).client(), "claude-sonnet-5-5")
    with pytest.raises(run_eval.JudgeInconclusive):
        await run_eval.llm_judge(judge, MAAJ, "answer")


def test_eval_builds_production_wiring_offline():
    service = run_eval.build_services("heuristic")
    assert service.llm_service.status()["provider"] == "heuristic"
    assert service.options_provider.data_status == "synthetic"
