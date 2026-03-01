import argparse
import asyncio
import json
import os
import sys
from typing import Any, Dict, Tuple

# Allow environment overrides before importing app code

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock", action="store_true", help="Use mock options provider")
    parser.add_argument("--deterministic", action="store_true", help="Disable LLM judge and use heuristics")
    return parser.parse_args()


def load_cases(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def has_required_headings(text: str) -> bool:
    headings = [
        "Summary",
        "Setup",
        "Payoff at Expiration",
        "Max Profit / Max Loss",
        "Breakeven(s)",
        "Key Sensitivities",
        "Typical Use Case",
        "Main Risks",
        "Assumptions / What I need from you",
    ]
    return all(h in text for h in headings)


def heuristic_golden(expected_keywords: list[str], response_text: str) -> bool:
    hits = 0
    text = response_text.lower()
    for kw in expected_keywords:
        if kw.lower() in text:
            hits += 1
    return hits >= max(1, int(len(expected_keywords) * 0.6))


def heuristic_rubric(must_include: list[str], response_text: str) -> bool:
    text = response_text.lower()
    return all(m.lower() in text for m in must_include)


async def build_services():
    from app.providers.options.factory import build_options_provider
    from app.services.chat_service import ChatService
    from app.services.llm_service import LLMService

    options_provider = build_options_provider()
    llm_service = LLMService()
    return ChatService(options_provider, llm_service)


async def run_case(chat_service, case: dict, judge_provider=None, deterministic=False) -> Tuple[bool, Dict[str, bool]]:
    from app.models import ChatRequest

    req = ChatRequest(**case["input"])
    response = await chat_service.handle(req)

    deterministic_checks = {}
    deterministic_checks["headings"] = has_required_headings(response.response_text)

    requires_refusal = case.get("deterministic", {}).get("requires_refusal", False)
    if requires_refusal:
        deterministic_checks["refusal"] = "provide" in response.response_text.lower() and "legs" in response.response_text.lower()
    else:
        deterministic_checks["refusal"] = True

    requires_autofill = case.get("deterministic", {}).get("requires_premium_autofill", False)
    if requires_autofill:
        premiums = response.computed["computed"]["premiums_used"] if response.computed else []
        deterministic_checks["premium_autofill"] = all(p.get("premium") is not None for p in premiums)
    else:
        deterministic_checks["premium_autofill"] = True

    # MaaJ evaluation
    maaj = case.get("maaj", {})
    maaj_pass = True
    if maaj:
        if deterministic or judge_provider is None:
            if maaj.get("type") == "golden":
                maaj_pass = heuristic_golden(maaj.get("keywords", []), response.response_text)
            else:
                maaj_pass = heuristic_rubric(maaj.get("must_include", []), response.response_text)
        else:
            maaj_pass = await llm_judge(judge_provider, maaj, response.response_text)

    all_pass = all(deterministic_checks.values()) and maaj_pass
    deterministic_checks["maaj"] = maaj_pass
    return all_pass, deterministic_checks


async def llm_judge(provider, maaj: dict, response_text: str) -> bool:
    expected = maaj.get("expected", "")
    rubric = maaj.get("rubric", [])
    judge_prompt = (
        "You are a strict evaluator. Reply with PASS or FAIL, then one short reason.\n"
        f"Type: {maaj.get('type')}\n"
        f"Expected: {expected}\n"
        f"Rubric: {rubric}\n"
        f"Response: {response_text}\n"
    )
    verdict = await provider.generate(system="Judge", user=judge_prompt, max_output_tokens=120, temperature=0.0)
    return verdict.strip().upper().startswith("PASS")


async def main_async():
    args = parse_args()
    if args.mock:
        os.environ["OPTIONS_PROVIDER"] = "mock"
    if args.deterministic:
        os.environ["EVAL_DETERMINISTIC"] = "1"

    cases = load_cases(os.path.join(os.path.dirname(__file__), "golden_cases.json"))
    chat_service = await build_services()

    judge_provider = None
    if not args.deterministic:
        try:
            from app.providers.llm.vertex import VertexProvider

            judge_provider = VertexProvider()
        except Exception:
            judge_provider = None

    results = []
    category_totals = {}
    category_pass = {}

    for case in cases:
        passed, checks = await run_case(chat_service, case, judge_provider=judge_provider, deterministic=args.deterministic)
        results.append((case["id"], case["category"], passed, checks))
        category_totals[case["category"]] = category_totals.get(case["category"], 0) + 1
        category_pass[case["category"]] = category_pass.get(case["category"], 0) + (1 if passed else 0)

    for case_id, category, passed, checks in results:
        status = "PASS" if passed else "FAIL"
        print(f"[{status}] {case_id} ({category}) checks={checks}")

    print("\nPass rates by category:")
    for category, total in category_totals.items():
        passed = category_pass.get(category, 0)
        rate = passed / total if total else 0
        print(f"- {category}: {passed}/{total} ({rate:.0%})")

    overall = sum(1 for _, _, passed, _ in results if passed) / len(results)
    print(f"\nOverall: {overall:.0%}")

    # non-zero exit on failure
    if any(not passed for _, _, passed, _ in results):
        sys.exit(1)


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
