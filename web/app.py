"""aiohttp Web Server & REST API for the UZEX AI Data Analyst Agent."""

from __future__ import annotations

import asyncio
import decimal
import datetime
import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable

from aiohttp import web
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.formatter import infer_filters_from_sql  # noqa: E402
from agent.orchestrator import AgentOrchestrator  # noqa: E402
from agent.request_logger import log_incoming_question  # noqa: E402

logger = logging.getLogger("uzex_web")
INDEX_HTML_PATH = Path(__file__).resolve().parent / "index.html"


def _json_safe(val: Any) -> Any:
    """Convert Decimal, date, datetime, or other DuckDB types to JSON-safe primitives."""
    if isinstance(val, decimal.Decimal):
        return float(val)
    if isinstance(val, (datetime.date, datetime.datetime)):
        return val.isoformat()
    if isinstance(val, dict):
        return {str(k): _json_safe(v) for k, v in val.items()}
    if isinstance(val, (list, tuple)):
        return [_json_safe(v) for v in val]
    return val


def create_web_app(
    get_orchestrator_fn: Callable[[], AgentOrchestrator],
    semaphore: asyncio.Semaphore | None = None,
) -> web.Application:
    """Create the aiohttp web.Application serving the UI and API endpoints."""
    sem = semaphore or asyncio.Semaphore(3)
    app = web.Application()

    async def handle_index(request: web.Request) -> web.Response:
        if not INDEX_HTML_PATH.exists():
            return web.Response(text="index.html not found", status=404)
        html_content = INDEX_HTML_PATH.read_text(encoding="utf-8")
        return web.Response(text=html_content, content_type="text/html", charset="utf-8")

    async def handle_status(request: web.Request) -> web.Response:
        orch = get_orchestrator_fn()
        models_report = orch.router.get_status_report()
        payload = {
            "status": "ok",
            "bot_username": "@uzex_selection_agent_bot",
            "bot_url": "https://t.me/uzex_selection_agent_bot",
            "dataset": {
                "deals_total": 500_000,
                "contracts_total": 6_616,
                "participants_total": 14_392,
                "brokers_total": 120,
                "regions_total": 14,
                "date_min": "03.01.2022",
                "date_max": "30.10.2026",
                "engine": "DuckDB (deals_full view)",
            },
            "models": models_report,
        }
        return web.json_response(payload)

    async def handle_ask(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON body"}, status=400)

        question = str(body.get("question", "")).strip()
        if not question:
            return web.json_response({"error": "Question is required"}, status=400)
        if len(question) > 1000:
            question = question[:1000]

        raw_history = body.get("history")
        history: list[dict[str, Any]] | None = None
        if isinstance(raw_history, list):
            history = [h for h in raw_history[-3:] if isinstance(h, dict)]

        client_ip = (
            request.headers.get("CF-Connecting-IP")
            or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or request.remote
            or "unknown"
        )

        try:
            async with sem:
                orch = get_orchestrator_fn()
                result = await asyncio.to_thread(orch.answer, question, history)

            log_incoming_question(
                source_ip_or_user=client_ip,
                status_code=200,
                latency_s=result.latency_s,
                question=question,
                is_followup=bool(history),
                cached=result.cached,
                not_found=result.not_found,
                model_used=result.model_used,
                answer_text=result.answer_text,
                sql=result.sql,
                deals_count=result.deals_count,
            )

            safe_rows = _json_safe(result.sql_rows[:50]) if result.sql_rows else []
            columns = list(safe_rows[0].keys()) if safe_rows else []
            filters_ru = infer_filters_from_sql(result.sql, "ru") if result.sql else "—"
            filters_uz = infer_filters_from_sql(result.sql, "uz") if result.sql else "—"
            filters_applied = filters_uz if result.lang == "uz" else filters_ru

            return web.json_response(
                {
                    "question": result.question,
                    "lang": result.lang,
                    "answer_text": result.answer_text,
                    "filters_applied": filters_applied,
                    "filters_ru": filters_ru,
                    "filters_uz": filters_uz,
                    "deals_count": result.deals_count,
                    "sample_deals": result.sample_deals[:5],
                    "sql": result.sql,
                    "sql_rows": safe_rows,
                    "columns": columns,
                    "model_used": result.model_used,
                    "latency_s": round(result.latency_s, 2),
                    "not_found": result.not_found,
                    "cached": result.cached,
                    "verifier_passed": result.verifier_passed,
                }
            )
        except Exception as e:
            logger.exception("Web API error answering %r: %s", question, e)
            log_incoming_question(
                source_ip_or_user=client_ip,
                status_code=500,
                latency_s=0.0,
                question=question,
                is_followup=bool(history),
            )
            return web.json_response(
                {"error": f"Internal error processing question: {e}"}, status=500
            )

    app.router.add_get("/", handle_index)
    app.router.add_get("/api/status", handle_status)
    app.router.add_post("/api/ask", handle_ask)
    return app


async def start_web_server(
    get_orchestrator_fn: Callable[[], AgentOrchestrator],
    semaphore: asyncio.Semaphore | None = None,
    host: str = "0.0.0.0",
    port: int = 8080,
) -> web.AppRunner:
    """Start the aiohttp web server in the current event loop and return its AppRunner."""
    app = create_web_app(get_orchestrator_fn, semaphore=semaphore)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
    logger.info("Web UI & API server listening on http://%s:%d", host, port)
    return runner


async def _standalone_main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    load_dotenv(PROJECT_ROOT / ".env")
    port = int(os.getenv("WEB_PORT", os.getenv("PORT", "8080")))
    host = os.getenv("WEB_HOST", "0.0.0.0")
    orch = AgentOrchestrator(use_cache=True)
    runner = await start_web_server(lambda: orch, host=host, port=port)
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(_standalone_main())

