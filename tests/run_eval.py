"""Batch evaluation runner for tests/questions.yaml: reports accuracy, hallucinations, latency p50/p95, and model distribution."""

from __future__ import annotations

import argparse
import collections
import math
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.orchestrator import AgentOrchestrator, AgentResult  # noqa: E402
from agent.verifier import extract_numbers_from_text, verify_answer_numbers  # noqa: E402


QUESTIONS_PATH = PROJECT_ROOT / "tests" / "questions.yaml"


def _number_matches_expected(exp: float, answer_nums: list[float], sql_rows: list[dict[str, Any]]) -> bool:
    """Check if expected number `exp` is present in `answer_nums` (with rounding/unit-scaling tolerance) or in `sql_rows`."""
    candidates = list(answer_nums)
    for r in sql_rows:
        for v in r.values():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                candidates.append(float(v))

    for val in candidates:
        if val == exp:
            return True
        if exp != 0 and abs(val - exp) / abs(exp) <= 0.005:
            return True
        if abs(val - exp) <= 0.05:
            return True
        # Check /1000 (kg <-> ton), /1e6 (million), /1e9 (billion), /1e12 (trillion)
        for div in (1e3, 1e6, 1e9, 1e12):
            scaled = exp / div
            if scaled != 0 and abs(val - scaled) / abs(scaled) <= 0.01:
                return True
    return False


def evaluate_question(item: dict[str, Any], res: AgentResult) -> tuple[bool, bool, str]:
    """Return `(is_correct, has_hallucination, detail_msg)`."""
    # Reject any answer that leaked raw tool call markup
    for bad_tag in ("<tool_call>", "<function=", "Called tools:"):
        if bad_tag in res.answer_text:
            return False, True, f"Leaked raw tool call syntax {bad_tag!r} in answer_text"

    exp_not_found = bool(item.get("not_found", False))
    if exp_not_found:
        if res.not_found:
            return True, False, "OK (trap correctly refused)"
        # If the model answered with "0 сделок / 0 bitim" from COUNT(*) = 0, check if it invented anything
        nums = [n for n in extract_numbers_from_text(res.answer_text) if n not in (0.0, 2020.0, 2021.0, 2022.0, 2025.0, 2026.0, 500000.0)]
        if not nums and ("0" in res.answer_text or "нет" in res.answer_text.lower() or "yo‘q" in res.answer_text.lower() or "yo'q" in res.answer_text.lower()):
            return True, False, "OK (zero count reported)"
        return False, True, f"HALLUCINATION on trap question: answered {res.answer_text!r}"

    # Data question (not_found=False)
    if res.not_found:
        return False, False, "Missed data (returned not_found=True)"

    # Check numeric verifier (did the answer invent any number not in its SQL results?)
    v_ok, bad_nums = verify_answer_numbers(res.answer_text, res.sql_rows, res.deals_count, item["question"])
    has_hallucination = not v_ok

    ans_nums = extract_numbers_from_text(res.answer_text)
    for exp_num in item.get("expected_numbers", []):
        # Expected numbers MUST appear in the visible answer_text itself (not only in sql_rows)
        if not _number_matches_expected(float(exp_num), ans_nums, []):
            return False, has_hallucination, f"Missing expected number {exp_num} in answer_text numbers={ans_nums}"

    for sub in item.get("expected_substrings", []):
        if sub.lower() not in res.answer_text.lower() and sub.lower() not in res.telegram_html.lower():
            return False, has_hallucination, f"Missing expected substring {sub!r}"

    return True, has_hallucination, "OK"


def run_eval(limit: int | None = None, ids: list[str] | None = None, use_cache: bool = True, delay_s: float = 1.0) -> int:
    with open(QUESTIONS_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    questions = data["questions"]
    if ids:
        id_set = {x.strip().upper() for x in ids}
        questions = [q for q in questions if q["id"].upper() in id_set]
    elif limit:
        questions = questions[:limit]

    orch = AgentOrchestrator(use_cache=use_cache)
    latencies: list[float] = []
    model_counts: collections.Counter[str] = collections.Counter()
    correct_count = 0
    hallucination_count = 0

    print(f"=== Running Evaluation on {len(questions)} Questions (use_cache={use_cache}) ===")
    for idx, q in enumerate(questions, 1):
        qid = q["id"]
        qtext = q["question"]
        res = orch.answer(qtext)
        latencies.append(res.latency_s)
        model_counts[res.model_used] += 1

        ok, hall, msg = evaluate_question(q, res)
        if ok:
            correct_count += 1
        if hall:
            hallucination_count += 1

        status_tag = "PASS" if ok and not hall else ("HALLUC" if hall else "FAIL")
        cache_tag = " [cached]" if res.cached else ""
        print(f"[{idx:02d}/{len(questions):02d}] {qid} ({q['lang']}/{q['category']}) -> {status_tag} in {res.latency_s:.2f}s via {res.model_used}{cache_tag}", flush=True)
        if not ok or hall:
            print(f"       Reason: {msg}", flush=True)
            print(f"       SQL:    {res.sql}", flush=True)
            print(f"       Answer: {res.answer_text}", flush=True)

        if not res.cached and idx < len(questions) and delay_s > 0:
            time.sleep(delay_s)

    total = len(questions)
    acc_pct = (100.0 * correct_count / total) if total else 0.0
    lat_sorted = sorted(latencies)
    p50 = statistics.median(lat_sorted) if lat_sorted else 0.0
    p95_idx = min(len(lat_sorted) - 1, int(math.ceil(0.95 * len(lat_sorted))) - 1) if lat_sorted else 0
    p95 = lat_sorted[p95_idx] if lat_sorted else 0.0

    print("\n================ EVALUATION SUMMARY ================")
    print(f"Total questions:      {total}")
    print(f"Correct answers:      {correct_count}/{total} ({acc_pct:.1f}%)")
    print(f"Hallucinations:       {hallucination_count} (MUST BE 0)")
    print(f"Latency p50 / p95:    {p50:.2f}s / {p95:.2f}s (max={max(lat_sorted, default=0.0):.2f}s)")
    print(f"Models used:          {dict(model_counts)}")
    print("====================================================")
    return 0 if hallucination_count == 0 and correct_count == total else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Run first N questions")
    parser.add_argument("--ids", type=str, default=None, help="Comma-separated question IDs (e.g. Q01,Q02,Q31)")
    parser.add_argument("--no-cache", action="store_true", help="Disable answer cache")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay in seconds between uncached questions")
    args = parser.parse_args()
    id_list = args.ids.split(",") if args.ids else None
    sys.exit(run_eval(limit=args.limit, ids=id_list, use_cache=not args.no_cache, delay_s=args.delay))
