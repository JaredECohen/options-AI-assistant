import argparse
import asyncio
import json
import os
import sys
from typing import Any, Dict, Tuple

from dotenv import load_dotenv

# Ensure repo root is on sys.path so `app` imports work when run directly.
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# Load repo .env so local evals pick up API keys and provider config without manual export.
load_dotenv(os.path.join(REPO_ROOT, ".env"))

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock", action="store_true", help="Use mock options provider")
    parser.add_argument(
        "--generator",
        choices=["heuristic", "vertex"],
        default="heuristic",
        help="Generator backend for chatbot responses during eval. Defaults to heuristic so the judge path can be kept separate.",
    )
    parser.add_argument(
        "--deterministic",
        action="store_true",
        help="Force local deterministic evaluation regardless of generator/judge settings.",
    )
    parser.add_argument(
        "--judge",
        choices=["none", "vertex", "claude"],
        default="none",
        help="Judge backend for MaaJ evals. Use 'none' for local heuristic judging, or 'vertex'/'claude' for LLM-as-judge.",
    )
    return parser.parse_args()


def load_cases(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def has_required_headings(text: str) -> bool:
    headings = [
        "Summary",
        "Setup",
        "Payoff at Expiration",
        "Max Profit",
        "Max Loss",
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


def summary_keywords_present(keywords: list[str], response_text: str) -> bool:
    if not keywords:
        return True
    text = response_text.lower()
    hits = 0
    for k in keywords:
        if k.lower() in text:
            hits += 1
    return hits >= max(1, int(len(keywords) * 0.6))


async def build_services():
    from app.providers.options.factory import build_options_provider
    from app.services.chat_service import ChatService
    from app.services.llm_service import LLMService
    from app.services.memory_store import MemoryStore

    options_provider = build_options_provider()
    llm_service = LLMService()
    memory_store = MemoryStore()
    return ChatService(options_provider, llm_service, memory_store)


async def run_case(chat_service, case: dict, judge_provider=None, deterministic=False) -> Tuple[bool, Dict[str, bool]]:
    from app.models import ChatRequest

    payload = dict(case["input"])
    payload.setdefault("session_id", case["id"])
    req = ChatRequest(**payload)
    response = await chat_service.handle(req)

    deterministic_checks = {}
    requires_headings = case.get("deterministic", {}).get("requires_headings", False)
    if requires_headings:
        deterministic_checks["headings"] = has_required_headings(response.response_text)
    else:
        deterministic_checks["headings"] = True

    requires_refusal = case.get("deterministic", {}).get("requires_refusal", False)
    if requires_refusal:
        text = response.response_text.lower()
        deterministic_checks["refusal"] = any(t in text for t in ["illegal", "cannot", "can't", "not allowed"])
    else:
        deterministic_checks["refusal"] = True

    requires_view_prompt = case.get("deterministic", {}).get("requires_view_prompt", False)
    if requires_view_prompt:
        text = response.response_text.lower()
        view_terms = [
            "view",
            "market view",
            "outlook",
            "bullish",
            "bearish",
            "neutral",
            "volatile",
            "horizon",
            "time horizon",
            "timeframe",
            "time frame",
        ]
        deterministic_checks["view_prompt"] = any(term in text for term in view_terms)
    else:
        deterministic_checks["view_prompt"] = True

    requires_moneyness = case.get("deterministic", {}).get("requires_moneyness", False)
    if requires_moneyness:
        text = response.response_text.lower()
        deterministic_checks["moneyness"] = "%" in text or "otm" in text or "out of the money" in text
    else:
        deterministic_checks["moneyness"] = True

    forbidden_phrases = case.get("deterministic", {}).get("forbidden_phrases", [])
    if forbidden_phrases:
        text = response.response_text.lower()
        deterministic_checks["forbidden_phrases"] = all(phrase.lower() not in text for phrase in forbidden_phrases)
    else:
        deterministic_checks["forbidden_phrases"] = True

    summary_keywords = case.get("deterministic", {}).get("summary_keywords", [])
    deterministic_checks["summary_keywords"] = summary_keywords_present(summary_keywords, response.response_text)

    requires_autofill = case.get("deterministic", {}).get("requires_premium_autofill", False)
    if requires_autofill:
        premiums = response.computed["computed"]["premiums_used"] if response.computed else []
        deterministic_checks["premium_autofill"] = all(
            p.get("premium") is not None or p.get("assumed_premium") is not None for p in premiums
        )
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

    if args.judge != "none" and args.generator == args.judge:
        raise SystemExit(
            f"Refusing to run eval with generator='{args.generator}' and judge='{args.judge}' from the same provider family. "
            "Use a different generator or judge backend."
        )

    os.environ["LLM_PROVIDER"] = args.generator
    deterministic = args.deterministic or (args.judge == "none" and args.generator == "heuristic")
    if deterministic:
        os.environ["EVAL_DETERMINISTIC"] = "1"
    else:
        os.environ.pop("EVAL_DETERMINISTIC", None)

    cases = load_cases(os.path.join(os.path.dirname(__file__), "golden_cases.json"))
    chat_service = await build_services()

    judge_provider = None
    if args.judge == "vertex":
        try:
            from app.providers.llm.vertex import VertexProvider

            judge_provider = VertexProvider()
        except Exception:
            judge_provider = None
            print("Warning: Vertex judge requested but unavailable; falling back to local heuristic judging.")
    elif args.judge == "claude":
        try:
            from app.providers.llm.anthropic import AnthropicProvider

            judge_provider = AnthropicProvider()
        except Exception:
            judge_provider = None
            print("Warning: Claude judge requested but unavailable; falling back to local heuristic judging.")

    print(f"Eval config: generator={args.generator} judge={args.judge} deterministic={deterministic}")

    results = []
    category_totals = {}
    category_pass = {}
    judge_runtime_failed = False

    for case in cases:
        case_deterministic = deterministic or judge_runtime_failed
        try:
            passed, checks = await run_case(
                chat_service,
                case,
                judge_provider=judge_provider,
                deterministic=case_deterministic,
            )
        except Exception as exc:
            if judge_provider is not None and not case_deterministic:
                judge_runtime_failed = True
                print(
                    f"Warning: judge backend '{args.judge}' failed during evaluation ({exc.__class__.__name__}: {exc}). "
                    "Falling back to local heuristic judging for the remaining cases."
                )
                passed, checks = await run_case(
                    chat_service,
                    case,
                    judge_provider=None,
                    deterministic=True,
                )
            else:
                raise
        results.append((case["id"], case["category"], passed, checks))
        category_totals[case["category"]] = category_totals.get(case["category"], 0) + 1
        category_pass[case["category"]] = category_pass.get(case["category"], 0) + (1 if passed else 0)

    def has_deterministic_metric(checks: dict) -> bool:
        for key in [
            "summary_keywords",
            "refusal",
            "view_prompt",
            "moneyness",
            "headings",
            "premium_autofill",
            "forbidden_phrases",
        ]:
            if key in checks:
                return True
        return False

    for case_id, category, passed, checks in results:
        status = "PASS" if passed else "FAIL"
        print(f"[{status}] {case_id} ({category}) checks={checks}")
        print(f"  deterministic_metric_included={has_deterministic_metric(checks)}")

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
