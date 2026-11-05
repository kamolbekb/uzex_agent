"""Anti-hallucination verifier: checks that every number in the LLM's answer comes from SQL results or the user's question."""

from __future__ import annotations

import math
import re
from typing import Any


# Match formatted or plain numbers in prose (e.g. "304 547 938,4", "12,34%", "2025", "10839.03")
NUMBER_TOKEN_RE = re.compile(
    r"(?<![A-Za-zА-Яа-яЁёЎўҚқҒғҲҳ0-9_])"
    r"(\d{1,3}(?:[\s\u00a0\u202f]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
    r"(?![A-Za-zА-Яа-яЁёЎўҚқҒғҲҳ0-9_])"
)

# Common small numbers that may legitimately appear in phrasing ("за 1 тонну", "в 2 раза", "топ-5", "100%", "1-сорт")
TRIVIAL_WHITELIST = {0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 10.0, 100.0}


def extract_numbers_from_text(text: str) -> list[float]:
    """Extract numeric values from a Russian or Uzbek text string."""
    # Remove broker codes (BK001), HS codes in parentheses, grades like АИ-92, М-100, ПФ-115, ЦЕМ II/А-Ш 32,5Н, Ø110мм
    cleaned = re.sub(r"\b(?:BK|БК)\d{1,3}\b", " ", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:АИ|AI|М|M|Г|G|ПФ|PF|Вр|VR|PP|PE|HD|LL|LD|SG|КС|KS)[-\s]?\d+(?:[.,/]\d+)?", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:Ne|ЦЕМ|CEM)\s*[I123/А-ЯA-Z\-]*\s*\d+(?:[.,/]\d+)?", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"Ø\s*\d+", " ", cleaned)
    cleaned = re.sub(r"\b\d+х\d+(?:х\d+)?", " ", cleaned, flags=re.IGNORECASE)
    # Remove DD.MM.YYYY and YYYY-MM-DD dates so day/month digits aren't misread as standalone numbers
    cleaned = re.sub(r"\b\d{2}\.\d{2}\.\d{4}\b", " ", cleaned)
    cleaned = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", " ", cleaned)
    cleaned = re.sub(r"\b\d{2}:\d{2}(?::\d{2})?\b", " ", cleaned)

    nums: list[float] = []
    for m in NUMBER_TOKEN_RE.finditer(cleaned):
        raw = m.group(1)
        norm = re.sub(r"[\s\u00a0\u202f]", "", raw).replace(",", ".")
        try:
            val = float(norm)
            nums.append(val)
        except ValueError:
            continue
    return nums


def collect_allowed_numbers(sql_rows: list[dict[str, Any]], row_count: int, question: str) -> set[float]:
    """Build the set of ground-truth numbers from SQL results, row_count, and the user's question."""
    allowed: set[float] = set(TRIVIAL_WHITELIST)
    allowed.add(float(row_count))

    for qnum in extract_numbers_from_text(question):
        allowed.add(qnum)
    # Also extract years or digits from dates in the question
    for m_yr in re.findall(r"\b(202\d)\b", question):
        allowed.add(float(m_yr))

    raw_vals: list[float] = []
    for row in sql_rows:
        for val in row.values():
            if val is None or isinstance(val, bool):
                continue
            if isinstance(val, (int, float)):
                fval = float(val)
                if math.isfinite(fval):
                    allowed.add(fval)
                    raw_vals.append(fval)
                    # Also allow common unit conversions: thousands, millions, billions, trillions (e.g. "52,06 трлн", "304,55 млн")
                    for div in (1e3, 1e6, 1e9, 1e12):
                        if abs(fval) >= div * 0.1:
                            allowed.add(fval / div)
            elif isinstance(val, str):
                for snum in extract_numbers_from_text(val):
                    allowed.add(snum)
                for m_yr in re.findall(r"\b(202\d)\b", val):
                    allowed.add(float(m_yr))

    # Also allow simple sums or percentages across small result sets (up to 10 rows) in case the SQL returned grouped rows
    if 1 < len(raw_vals) <= 30:
        # Column-wise sums and percentages
        cols = list(sql_rows[0].keys())
        for c in cols:
            col_nums = [
                float(r[c])
                for r in sql_rows
                if isinstance(r.get(c), (int, float)) and not isinstance(r.get(c), bool)
            ]
            if col_nums:
                s = sum(col_nums)
                allowed.add(s)
                for div in (1e3, 1e6, 1e9, 1e12):
                    if abs(s) >= div * 0.1:
                        allowed.add(s / div)
                if s > 0:
                    for v in col_nums:
                        allowed.add(100.0 * v / s)

    return allowed


def _matches_any_allowed(val: float, allowed: set[float]) -> bool:
    for target in allowed:
        if val == target:
            return True
        # Rounding tolerance:
        # 1) Relative tolerance 0.5% (covers rounding 304 547 938.4 -> 304.5 млн or 304 547 938)
        if target != 0 and abs(val - target) / abs(target) <= 0.005:
            return True
        # 2) Absolute tolerance 0.05 (covers 2-decimal rounding like 12.345 -> 12.35)
        if abs(val - target) <= 0.05:
            return True
        # 3) Integer rounding of a float (round, floor, ceil)
        if abs(val - round(target)) <= 1e-6 or abs(val - math.floor(target)) <= 1e-6 or abs(val - math.ceil(target)) <= 1e-6:
            return True
    return False


def verify_answer_numbers(
    answer_text: str,
    sql_rows: list[dict[str, Any]],
    row_count: int,
    question: str,
) -> tuple[bool, list[float]]:
    """Verify that every number in `answer_text` matches `sql_rows`, `row_count`, or `question`.

    Returns `(is_valid, unmatched_numbers)`.
    """
    if not answer_text.strip():
        return False, []

    allowed = collect_allowed_numbers(sql_rows, row_count, question)
    answer_nums = extract_numbers_from_text(answer_text)

    unmatched: list[float] = []
    for n in answer_nums:
        if not _matches_any_allowed(n, allowed):
            unmatched.append(n)

    return len(unmatched) == 0, unmatched

