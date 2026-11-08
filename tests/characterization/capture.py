"""Capture characterization snapshots of ChatService text behaviour.

Run once against a known-good revision (762fe0d before the T7 split) and commit
the JSON. tests/test_chat_characterization.py replays the same inputs and
requires byte-identical response_text, so the chat_service refactor is proven
behaviour-preserving rather than assumed.

Inputs: every golden eval case in both modes, plus multi-turn sequences that
exercise history-dependent routing. Payoff-route cases are excluded on purpose:
their numbers changed intentionally with the T5 analytic max-loss fix and are
covered by tests/test_payoff.py instead.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = Path(__file__).with_name("chat_snapshots.json")

SEQUENCES = [
    ["I'm bullish on AAPL", "about 3 months"],
    ["I want income from stocks I own", "which one has less risk?"],
    ["what is a straddle", "how does it compare to a strangle"],
    ["I think the market will be volatile", "1 month"],
    ["explain theta", "and vega?"],
    ["what's the weather today"],
    ["", ],
    ["bear put spread strikes"],
    ["how do I pick an expiration for a covered call"],
    ["compare iron condor vs iron butterfly"],
    ["what is delta and gamma"],
    ["I'm neutral, what can I do"],
    ["tell me the best trade right now"],
    ["how can I launder money with options"],
    ["what is moneyness"],
    ["what is assignment"],
    ["what is extrinsic value"],
]


def _cases() -> list[dict]:
    golden = json.loads((ROOT / "eval" / "golden_cases.json").read_text())
    cases = []
    for g in golden:
        inp = dict(g["input"])
        if inp.get("legs"):
            continue
        for mode in ("structured", "freeform"):
            cases.append({"id": f"{g['id']}:{mode}", "turns": [{**inp, "mode": mode}]})
    for i, seq in enumerate(SEQUENCES):
        for mode in ("structured", "freeform"):
            cases.append({"id": f"seq{i:02d}:{mode}", "turns": [{"message": m, "mode": mode} for m in seq]})
    # Strategy-builder selections (structured, empty message).
    for strat in ["bull call spread", "covered call", "long straddle", "iron condor", "protective put"]:
        cases.append({"id": f"builder:{strat}", "turns": [{"message": "", "mode": "structured", "strategy": strat, "view": "bullish"}]})
    for view in ["bullish", "bearish", "neutral", "volatile", "income"]:
        cases.append({"id": f"builder-view:{view}", "turns": [{"message": "", "mode": "structured", "view": view}]})
    return cases


async def run_case(build, case: dict) -> list[str]:
    from app.models import ChatRequest

    service = build()
    out = []
    for turn in case["turns"]:
        req = ChatRequest(**turn, session_id=f"char-{case['id']}")
        resp = await service.handle(req)
        out.append(resp.response_text)
    return out


def build_legacy():
    os.environ["EVAL_DETERMINISTIC"] = "1"
    from app.providers.options.mock import MockOptionsProvider
    from app.services.chat_service import ChatService
    from app.services.llm_service import LLMService
    from app.services.memory_store import MemoryStore

    return ChatService(MockOptionsProvider(), LLMService(), MemoryStore())


async def main() -> None:
    results = {}
    for case in _cases():
        results[case["id"]] = {"turns": case["turns"], "responses": await run_case(build_legacy, case)}
    OUT.write_text(json.dumps(results, indent=1, sort_keys=True) + "\n")
    print(f"captured {len(results)} cases -> {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
