"""Live request logger for tracking incoming Web UI and Telegram bot questions."""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = PROJECT_ROOT / "logs"
QUESTIONS_LOG_PATH = LOGS_DIR / "questions.log"
QUESTIONS_JSONL_PATH = LOGS_DIR / "questions.jsonl"

# Tashkent timezone (UTC+5)
TASHKENT_TZ = datetime.timezone(datetime.timedelta(hours=5))


def log_incoming_question(
    *,
    source_ip_or_user: str,
    status_code: int,
    latency_s: float,
    question: str,
    is_followup: bool = False,
    cached: bool = False,
    not_found: bool = False,
    model_used: str = "",
    answer_text: str = "",
    sql: str = "",
    deals_count: int = 0,
) -> None:
    """Append a single-line summary to `logs/questions.log` and full JSON details to `logs/questions.jsonl`."""
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        now_str = datetime.datetime.now(TASHKENT_TZ).strftime("%Y-%m-%d %H:%M:%S")
        q_single = " ".join(question.splitlines()).strip()

        tags: list[str] = []
        if is_followup:
            tags.append("[follow-up]")
        if cached:
            tags.append("[cached]")
        if not_found:
            tags.append("[not-found]")
        tag_str = f"  {' '.join(tags)}" if tags else ""

        line = f"{now_str}  {source_ip_or_user:<16}  {status_code}  {latency_s:.3f}s  {q_single}{tag_str}\n"
        with open(QUESTIONS_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)

        record: dict[str, Any] = {
            "ts": now_str,
            "client": source_ip_or_user,
            "status": status_code,
            "latency_s": round(latency_s, 3),
            "question": q_single,
            "follow_up": is_followup,
            "cached": cached,
            "not_found": not_found,
            "model": model_used,
            "deals_count": deals_count,
            "answer": answer_text,
            "sql": sql,
        }
        with open(QUESTIONS_JSONL_PATH, "a", encoding="utf-8") as jf:
            jf.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass

