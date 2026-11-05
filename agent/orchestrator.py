"""Agent orchestrator: language detection, SQLite cache, deterministic fast paths, tool-calling loop, and verifier integration."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.formatter import (
    build_deterministic_answer_from_rows,
    build_not_found_reply,
    detect_language,
    extract_evidence_from_sql_rows,
    format_deal_id,
    format_number,
    format_telegram_response,
    infer_filters_from_sql,
)
from agent.llm_router import LLMRouter
from agent.prompts import SYSTEM_PROMPT
from agent.tools import OPENAI_TOOLS_SCHEMA, DatabaseTools
from agent.verifier import verify_answer_numbers
from etl.normalize import normalize_apostrophes


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DB_PATH = PROJECT_ROOT / "logs" / "answer_cache.sqlite"


@dataclass
class AgentResult:
    question: str
    lang: str
    answer_text: str
    telegram_html: str
    sql: str = ""
    sql_rows: list[dict[str, Any]] = field(default_factory=list)
    deals_count: int = 0
    sample_deals: list[int] = field(default_factory=list)
    model_used: str = ""
    latency_s: float = 0.0
    not_found: bool = False
    cached: bool = False
    verifier_passed: bool = True


def _normalize_cache_key(question: str, history: list[dict[str, Any]] | None = None) -> str:
    s = normalize_apostrophes(question.lower()).replace("ё", "е")
    s = re.sub(r"\s+", " ", s).strip()
    if history:
        last_q = str(history[-1].get("question", "")).strip().lower()
        last_sql = str(history[-1].get("sql", "")).strip().lower()
        if last_q or last_sql:
            s = f"{last_q}|{last_sql}>>{s}"
    return s


def _extract_xml_tool_calls(text: str) -> list[dict[str, Any]]:
    """Parse XML-style `<tool_call><function=NAME><parameter=KEY>VAL</parameter></function></tool_call>` emitted by Qwen when tools=None."""
    calls: list[dict[str, Any]] = []
    for idx, m in enumerate(re.finditer(r"<tool_call>\s*<function=([a-zA-Z0-9_]+)>(.*?)</function>\s*</tool_call>", text, flags=re.DOTALL)):
        fn_name = m.group(1).strip()
        body = m.group(2)
        args: dict[str, Any] = {}
        for pm in re.finditer(r"<parameter=([a-zA-Z0-9_]+)>\s*(.*?)\s*</parameter>", body, flags=re.DOTALL):
            args[pm.group(1).strip()] = pm.group(2).strip()
        calls.append({"id": f"xml_tc_{idx}_{int(time.time()*1000)}", "name": fn_name, "arguments": json.dumps(args, ensure_ascii=False)})
    return calls


def _is_garbage_answer_text(text: str) -> bool:
    """Return True if text contains raw tool call markup, JSON dumps, or internal prompts instead of user-facing prose."""
    s = text.strip()
    if not s:
        return True
    bad_markers = (
        "<tool_call>",
        "<function=",
        "Called tools:",
        "\"tool_calls\"",
        "\"function\":",
        "```json",
        "\"answer_text\"",
        "\"filters_applied\"",
    )
    return any(marker in s for marker in bad_markers)


def _extract_json_block(text: str) -> dict[str, Any] | None:
    """Parse JSON object from LLM text (handles ```json fences, leading/trailing prose, or truncated JSON tails)."""
    s = text.strip()
    if any(m in s for m in ("<tool_call>", "<function=", "Called tools:")):
        return None
    # Strip markdown code fence if present
    m_fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, flags=re.DOTALL | re.IGNORECASE)
    if m_fence:
        s_cand = m_fence.group(1)
    else:
        start = s.find("{")
        end = s.rfind("}")
        s_cand = s[start : end + 1] if (start != -1 and end != -1 and end > start) else s

    try:
        obj = json.loads(s_cand)
        if isinstance(obj, dict) and "answer_text" in obj:
            if _is_garbage_answer_text(str(obj["answer_text"])):
                return None
            return obj
    except Exception:
        pass

    # Fallback: recover fields if JSON output was truncated after "answer_text"
    m_ans = re.search(r'"answer_text"\s*:\s*"((?:[^"\\]|\\.)*)"', s, flags=re.DOTALL)
    if m_ans:
        try:
            ans_val = json.loads(f'"{m_ans.group(1)}"')
        except Exception:
            ans_val = m_ans.group(1).replace('\\"', '"').replace("\\n", "\n")
        if not _is_garbage_answer_text(ans_val):
            m_filt = re.search(r'"filters_applied"\s*:\s*"((?:[^"\\]|\\.)*)"', s, flags=re.DOTALL)
            filt_val = m_filt.group(1) if m_filt else ""
            m_cnt = re.search(r'"deals_count"\s*:\s*(\d+)', s)
            cnt_val = int(m_cnt.group(1)) if m_cnt else 0
            m_nf = re.search(r'"not_found"\s*:\s*(true|false)', s, flags=re.IGNORECASE)
            nf_val = (m_nf.group(1).lower() == "true") if m_nf else False
            return {
                "answer_text": ans_val,
                "filters_applied": filt_val,
                "deals_count": cnt_val,
                "sample_deals": [],
                "sql": "",
                "not_found": nf_val,
            }
    return None


class AgentOrchestrator:
    """Coordinates fast paths, caching, LLM tool-calling loop, and anti-hallucination verification."""

    def __init__(
        self,
        router: LLMRouter | None = None,
        tools: DatabaseTools | None = None,
        use_cache: bool = True,
    ) -> None:
        self.router = router or LLMRouter()
        self.tools = tools or DatabaseTools()
        self.use_cache = use_cache
        self._init_cache()

    def _init_cache(self) -> None:
        CACHE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(CACHE_DB_PATH) as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS answer_cache (
                    q_key TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    created_ts REAL NOT NULL
                )
                """
            )
            con.execute(
                """
                DELETE FROM answer_cache
                WHERE payload_json LIKE '%```json%'
                   OR payload_json LIKE '%"verifier_passed": false%'
                """
            )
            con.commit()

    def _get_cached(self, question: str, history: list[dict[str, Any]] | None = None) -> AgentResult | None:
        if not self.use_cache:
            return None
        key = _normalize_cache_key(question, history)
        try:
            with sqlite3.connect(CACHE_DB_PATH) as con:
                row = con.execute("SELECT payload_json FROM answer_cache WHERE q_key = ?", (key,)).fetchone()
                if row:
                    data = json.loads(row[0])
                    res = AgentResult(**data)
                    res.cached = True
                    return res
        except Exception:
            pass
        return None

    def _set_cached(self, question: str, result: AgentResult, history: list[dict[str, Any]] | None = None) -> None:
        if not self.use_cache:
            return
        key = _normalize_cache_key(question, history)
        try:
            payload = {
                "question": result.question,
                "lang": result.lang,
                "answer_text": result.answer_text,
                "telegram_html": result.telegram_html,
                "sql": result.sql,
                "sql_rows": result.sql_rows,
                "deals_count": result.deals_count,
                "sample_deals": result.sample_deals,
                "model_used": result.model_used,
                "latency_s": result.latency_s,
                "not_found": result.not_found,
                "cached": True,
                "verifier_passed": result.verifier_passed,
            }
            with sqlite3.connect(CACHE_DB_PATH) as con:
                con.execute(
                    "INSERT OR REPLACE INTO answer_cache VALUES (?, ?, ?)",
                    (key, json.dumps(payload, ensure_ascii=False), time.time()),
                )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Deterministic Fast Paths (0 LLM tokens used!)
    # ------------------------------------------------------------------
    def _try_fast_path(self, question: str, lang: str, t0: float) -> AgentResult | None:
        q_low = normalize_apostrophes(question.lower()).strip()

        # Fast path 0a: Casual greeting ("hey", "hi", "hello", "hellp", "salom", "assalomu alaykum", "привет", "здравствуйте")
        if re.match(r"^\s*(hey+|hi+|hello+|helo+|hellp+|salom+|assalomu\s+alaykum|привет+|здравствуй(?:те)?|добрый\s+день)\s*[!?.]*\s*$", q_low):
            dt = time.perf_counter() - t0
            if lang == "uz" or re.match(r"^\s*(salom|assalomu|hey|hi|hello|helo|hellp)", q_low):
                ans = (
                    "Assalomu alaykum! Men UZEX birja bitimlari reestri (03.01.2022 – 30.10.2026, 500 000 ta bitim) "
                    "bo‘yicha AI-tahlilchiman. O‘zbek yoki rus tilida savol bering — masalan: "
                    "«2025-yilda paxta yog‘i bo‘yicha nechta bitim tuzilgan va jami summasi qancha?»"
                )
                return AgentResult(
                    question=question,
                    lang="uz",
                    answer_text=ans,
                    telegram_html=ans,
                    model_used="fast-path",
                    latency_s=dt,
                )
            else:
                ans = (
                    "Здравствуйте! Я ИИ-аналитик реестра биржевых сделок UZEX (03.01.2022 – 30.10.2026, 500 000 сделок). "
                    "Задайте вопрос на русском или узбекском языке — например: "
                    "«Какой объём и сумма сделок по хлопку-сырцу в 2025 году?»"
                )
                return AgentResult(
                    question=question,
                    lang="ru",
                    answer_text=ans,
                    telegram_html=ans,
                    model_used="fast-path",
                    latency_s=dt,
                )

        # Fast path 0b: Identity / Help / Capabilities ("кто ты", "что ты умеешь", "help", "sen kimsan", "nima qila olasan", "yordam")
        if re.match(
            r"^\s*(кто\s+ты|что\s+ты\s+умеешь|расскажи\s+о\s+себе|помощь|помоги(?:те)?|справка|help|"
            r"sen\s+kimsan|kimsan|nima\s+qila\s+olasan|yordam(?:\s+bering)?|qanday\s+savollar(?:\s+berish\s+mumkin)?)\s*[!?.]*\s*$",
            q_low,
        ):
            dt = time.perf_counter() - t0
            if lang == "uz":
                ans = (
                    "Men UZEX birja bitimlari reestri bo‘yicha AI-tahlilchiman.\n"
                    "• Maʼlumotlar bazasi: 500 000 ta bitim (03.01.2022 – 30.10.2026), 6 616 ta shartnoma, "
                    "14 392 ta ishtirokchi (sotuvchi/xaridor), 120 ta broker, 14 ta hudud va 1 765 ta kunlik USD kursi.\n"
                    "• Imkoniyatlarim: tovarlar, davrlar, viloyatlar, korxonalar (STIR/nomi) va brokerlar kesimida hajm, "
                    "summa (so‘m/USD), o‘rtacha narx, reytinglar (Top-N) hamda alohida bitimlarni (masalan, №100001) aniq SQL orqali hisoblab beraman."
                )
            else:
                ans = (
                    "Я ИИ-аналитик реестра биржевых сделок UZEX.\n"
                    "• База данных: 500 000 сделок (03.01.2022 – 30.10.2026), 6 616 контрактов, "
                    "14 392 участника (продавцы и покупатели), 120 брокеров, 14 регионов и 1 765 ежедневных курсов USD.\n"
                    "• Что я умею: рассчитываю точные объёмы, суммы (в сумах и USD), средневзвешенные цены, рейтинги (Топ-N), "
                    "динамику по годам/месяцам и проверяю отдельные сделки по номеру (например, №100001) с показом SQL-запроса."
                )
            return AgentResult(
                question=question,
                lang=lang,
                answer_text=ans,
                telegram_html=ans,
                model_used="fast-path",
                latency_s=dt,
            )

        # Fast path 0c: Thanks / Closing ("спасибо", "благодарю", "rahmat", "katta rahmat", "thanks")
        if re.match(r"^\s*(спасибо(?:\s+большое)?|благодарю|rahmat|katta\s+rahmat|tashakkur|thanks|thank\s+you)\s*[!?.]*\s*$", q_low):
            dt = time.perf_counter() - t0
            ans = (
                "Arzimaydi! UZEX birja bitimlari bo‘yicha yana savollaringiz bo‘lsa, marhamat."
                if lang == "uz"
                else "Пожалуйста! Если у вас есть ещё вопросы по биржевым сделкам UZEX — обращайтесь."
            )
            return AgentResult(
                question=question,
                lang=lang,
                answer_text=ans,
                telegram_html=ans,
                model_used="fast-path",
                latency_s=dt,
            )

        # Fast path 1: Single deal lookup by number (e.g. "сделка №412334", "bitim 100001", "номер сделки 100005")
        m_deal = re.search(
            r"^\s*(?:информация\s+о\s+|покажи\s+|найди\s+|расскажи\s+про\s+)?(?:сделк[аеиуой]|номер\s+сделки|bitim(?:\s+raqami)?)\s*[№#]?\s*(\d{5,7})\s*[?.\s]*$",
            q_low,
        )
        if m_deal:
            deal_id = int(m_deal.group(1))
            sql = f"SELECT * FROM deals_full WHERE deal_id = {deal_id}"
            res = self.tools.run_sql(sql)
            dt = time.perf_counter() - t0
            if not res.get("ok") or res.get("row_count", 0) == 0:
                note = f"Сделка №{deal_id}" if lang == "ru" else f"Bitim №{deal_id}"
                html_out = build_not_found_reply(lang, note, sql, "fast-path", dt)
                return AgentResult(
                    question=question,
                    lang=lang,
                    answer_text="В данных нет информации по этому запросу." if lang == "ru" else "Maʼlumotlarda bu so‘rov bo‘yicha javob yo‘q.",
                    telegram_html=html_out,
                    sql=sql,
                    not_found=True,
                    model_used="fast-path",
                    latency_s=dt,
                )
            row = res["rows"][0]
            cur_label = "сум" if row["currency"] == "UZS" else "USD"
            if lang == "uz":
                ans = (
                    f"{format_deal_id(deal_id)} bitimi ({row['deal_date']} {row['deal_time']}): "
                    f"tovar — {row['product_name']} ({row['product_base']}), "
                    f"hajmi — {format_number(row['volume_units'], 0)} {row['unit']} ({row['quantity']} lot × {row['lot']}), "
                    f"summasi — {format_number(row['amount'])} {cur_label} "
                    f"({format_number(row['amount_uzs'])} so‘m), "
                    f"birlik narxi — {format_number(row['unit_price'])} {cur_label}, "
                    f"sotuvchi — {row['seller_name']} (STIR {row['seller_inn']}, broker {row['seller_broker_code']}), "
                    f"xaridor — {row['buyer_name']} (STIR {row['buyer_inn']}, broker {row['buyer_broker_code']}), "
                    f"hudud — {row['region_uz']}, holati — {row['status']}."
                )
                filt = f"deal_id = {deal_id}"
            else:
                ans = (
                    f"Сделка {format_deal_id(deal_id)} от {row['deal_date']} {row['deal_time']}: "
                    f"товар — {row['product_name']} ({row['product_base']}), "
                    f"объём — {format_number(row['volume_units'], 0)} {row['unit']} ({row['quantity']} лот × {row['lot']}), "
                    f"сумма — {format_number(row['amount'])} {cur_label} "
                    f"({format_number(row['amount_uzs'])} сум), "
                    f"цена за единицу — {format_number(row['unit_price'])} {cur_label}, "
                    f"продавец — {row['seller_name']} (ИНН {row['seller_inn']}, брокер {row['seller_broker_code']}), "
                    f"покупатель — {row['buyer_name']} (ИНН {row['buyer_inn']}, брокер {row['buyer_broker_code']}), "
                    f"регион — {row['region_ru']}, статус — {row['status']}."
                )
                filt = f"deal_id = {deal_id}"

            html_out = format_telegram_response(lang, ans, filt, 1, [deal_id], sql, "fast-path", dt)
            return AgentResult(
                question=question,
                lang=lang,
                answer_text=ans,
                telegram_html=html_out,
                sql=sql,
                sql_rows=res["rows"],
                deals_count=1,
                sample_deals=[deal_id],
                model_used="fast-path",
                latency_s=dt,
            )

        # Fast path 2: USD exchange rate on a specific date DD.MM.YYYY or YYYY-MM-DD
        if re.search(r"\b(курс\s+доллара|курс\s+usd|курс\s+валют|dollar\s+kursi|usd\s+kursi|valyutalar\s+kursi|valyuta\s+kursi)\b", q_low):
            m_dmy = re.search(r"\b(\d{2})\.(\d{2})\.(\d{4})\b", q_low)
            m_ymd = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", q_low)
            iso_date = None
            disp_date = None
            if m_dmy:
                disp_date = m_dmy.group(0)
                iso_date = f"{m_dmy.group(3)}-{m_dmy.group(2)}-{m_dmy.group(1)}"
            elif m_ymd:
                iso_date = m_ymd.group(0)
                disp_date = f"{m_ymd.group(3)}.{m_ymd.group(2)}.{m_ymd.group(1)}"

            if iso_date and not re.search(r"\b(сделк|bitim|товар|tovar|экспорт|eksport)\b", q_low):
                sql = f"SELECT rate_date, usd_rate FROM usd_rates WHERE rate_date = DATE '{iso_date}'"
                res = self.tools.run_sql(sql)
                dt = time.perf_counter() - t0
                if not res.get("ok") or res.get("row_count", 0) == 0:
                    note = f"Курс USD на {disp_date}" if lang == "ru" else f"{disp_date} sanadagi USD kursi"
                    html_out = build_not_found_reply(lang, note, sql, "fast-path", dt)
                    return AgentResult(
                        question=question,
                        lang=lang,
                        answer_text="В данных нет информации по этому запросу." if lang == "ru" else "Maʼlumotlarda bu so‘rov bo‘yicha javob yo‘q.",
                        telegram_html=html_out,
                        sql=sql,
                        not_found=True,
                        model_used="fast-path",
                        latency_s=dt,
                    )
                rate = res["rows"][0]["usd_rate"]
                if lang == "uz":
                    ans = f"{disp_date} sanasida 1 AQSh dollari kursi {format_number(rate)} so‘mni tashkil etgan."
                    filt = f"sana = {iso_date}"
                else:
                    ans = f"Курс доллара США на {disp_date} составлял {format_number(rate)} сум за 1 USD."
                    filt = f"дата = {iso_date}"
                html_out = format_telegram_response(lang, ans, filt, 1, [], sql, "fast-path", dt)
                return AgentResult(
                    question=question,
                    lang=lang,
                    answer_text=ans,
                    telegram_html=html_out,
                    sql=sql,
                    sql_rows=res["rows"],
                    deals_count=1,
                    sample_deals=[],
                    model_used="fast-path",
                    latency_s=dt,
                )

        return None

    # ------------------------------------------------------------------
    # Main Question Answering Loop (<= 6 LLM calls)
    # ------------------------------------------------------------------
    def answer(self, question: str, history: list[dict[str, Any]] | None = None) -> AgentResult:
        t0 = time.perf_counter()
        lang = detect_language(question)

        # 1. Check cache (context-aware when history is present)
        cached = self._get_cached(question, history)
        if cached is not None:
            cached.latency_s = time.perf_counter() - t0
            return cached

        # 2. Check deterministic fast paths
        fast = self._try_fast_path(question, lang, t0)
        if fast is not None:
            self._set_cached(question, fast, history)
            return fast

        # 3. Bounded LLM tool-calling loop (max 6 calls, 70s budget)
        deadline_ts = time.time() + self.router.total_budget_s
        lang_instruction = (
            "Respond in Russian (Русский язык)."
            if lang == "ru"
            else "CRITICAL: Respond strictly in Uzbek (O‘zbek tilida javob bering, both `answer_text` and `filters_applied` MUST be in Uzbek, NOT Russian)."
        )

        context_prefix = ""
        if history:
            ctx_lines = ["Previous conversation context (use this if the current question is a follow-up like 'in dollars?', 'what about 2024?', 'and how many tons?'):"]
            for h_turn in history[-3:]:
                hq = str(h_turn.get("question", "")).strip()
                hsql = str(h_turn.get("sql", "")).strip()
                hans = str(h_turn.get("answer", "")).strip()[:220]
                if hq:
                    ctx_lines.append(f"- Previous Q: {hq} | Previous SQL: {hsql} | Previous A: {hans}")
            context_prefix = "\n".join(ctx_lines) + "\n\n"

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"{context_prefix}User question ({lang.upper()}): {question}\n{lang_instruction}",
            },
        ]

        last_sql: str = ""
        last_sql_rows: list[dict[str, Any]] = []
        last_sql_row_count: int = 0
        sql_executed: bool = False
        model_used: str = "unknown"
        final_json: dict[str, Any] | None = None

        max_steps = 6
        for step in range(max_steps):
            # Pass tools on all steps except the final step so the model can refine queries if needed
            allow_tools = OPENAI_TOOLS_SCHEMA if step < max_steps - 1 else None
            resp = self.router.chat(
                messages=messages,
                tools=allow_tools,
                temperature=0.0,
                max_tokens=2048,
                deadline_ts=deadline_ts,
            )
            model_used = f"{resp.provider}/{resp.model}"

            # Also check if model emitted XML-style <tool_call> in content when tools were not used
            effective_tool_calls = resp.tool_calls or _extract_xml_tool_calls(resp.content)

            if effective_tool_calls and step < max_steps - 1:
                # Preserve exact provider message dict (including Gemini 3 thought_signature in extra_content)
                if resp.tool_calls and resp.raw_message is not None and hasattr(resp.raw_message, "model_dump"):
                    raw_dump = resp.raw_message.model_dump(exclude_none=True)
                    messages.append(raw_dump)
                else:
                    assistant_tc_msg: dict[str, Any] = {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": tc["id"],
                                "type": "function",
                                "function": {"name": tc["name"], "arguments": tc["arguments"]},
                            }
                            for tc in effective_tool_calls
                        ],
                    }
                    messages.append(assistant_tc_msg)

                for tc in effective_tool_calls:
                    t_name = tc["name"]
                    try:
                        t_args = json.loads(tc["arguments"]) if isinstance(tc["arguments"], str) else tc["arguments"]
                    except Exception:
                        t_args = {}

                    if t_name == "search_entities":
                        tool_out = self.tools.search_entities(
                            entity_type=str(t_args.get("entity_type", "")),
                            query=str(t_args.get("query", "")),
                        )
                    elif t_name == "run_sql":
                        q_sql = str(t_args.get("query", ""))
                        tool_out = self.tools.run_sql(q_sql)
                        if tool_out.get("ok"):
                            sql_executed = True
                            last_sql = tool_out.get("sql", q_sql)
                            last_sql_rows = tool_out.get("rows", [])
                            last_sql_row_count = int(tool_out.get("row_count", 0))
                    else:
                        tool_out = {"error": f"Unknown tool: {t_name}"}

                    tool_str = json.dumps(tool_out, ensure_ascii=False)
                    if len(tool_str) > 4500:
                        tool_str = tool_str[:4500] + "... (truncated)"
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "name": t_name,
                            "content": tool_str,
                        }
                    )
                continue

            # No tool calls -> parse final JSON answer
            parsed = _extract_json_block(resp.content)
            if parsed is not None and "answer_text" in parsed:
                final_json = parsed
                break
            else:
                # Model returned plain text instead of JSON, or garbage tool markup, or forgot to call run_sql
                if (not sql_executed or _is_garbage_answer_text(resp.content)) and step < max_steps - 1:
                    messages.append({"role": "assistant", "content": resp.content})
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Do NOT output raw tool tags or plain text. "
                                "If you already ran `run_sql`, output ONLY a valid JSON object matching the schema: "
                                '{"answer_text": "...", "filters_applied": "...", "deals_count": ..., "sample_deals": [...], "sql": "...", "not_found": false}'
                            ),
                        }
                    )
                    continue
                # If plain text is clean prose and SQL was executed, wrap it; otherwise build deterministic answer from rows
                clean_ans = (
                    build_deterministic_answer_from_rows(lang, last_sql_rows)
                    if _is_garbage_answer_text(resp.content)
                    else resp.content
                )
                final_json = {
                    "answer_text": clean_ans,
                    "filters_applied": infer_filters_from_sql(last_sql, lang),
                    "deals_count": last_sql_row_count,
                    "sample_deals": [],
                    "sql": last_sql,
                    "not_found": not sql_executed or last_sql_row_count == 0,
                }
                break

        if final_json is None:
            final_json = {
                "answer_text": build_deterministic_answer_from_rows(lang, last_sql_rows),
                "filters_applied": infer_filters_from_sql(last_sql, lang),
                "deals_count": last_sql_row_count,
                "sample_deals": [],
                "sql": last_sql,
                "not_found": not sql_executed or last_sql_row_count == 0,
            }

        # ------------------------------------------------------------------
        # 4. Anti-Hallucination Gate: Empty Result Rule & Numeric Verifier
        # ------------------------------------------------------------------
        is_not_found = bool(final_json.get("not_found", False))
        # Also check if SQL returned 1 row where all aggregate values are NULL or 0 deals_count
        if sql_executed and last_sql_row_count == 0:
            is_not_found = True
        elif sql_executed and last_sql_row_count == 1:
            row0 = last_sql_rows[0]
            non_null_vals = [v for v in row0.values() if v is not None]
            if not non_null_vals:
                is_not_found = True
            # If the only non-null value is deals_count/cnt == 0 and no other positive metric
            elif all(
                (k.lower() in ("deals_count", "cnt", "count", "deal_count", "total_deals") and v == 0)
                for k, v in row0.items()
                if v is not None
            ):
                # Check if question specifically asked "сколько ... / nechta ..." where 0 is a valid count vs a lookup
                if not re.search(r"\b(сколько|nechta|necha)\b", question.lower()):
                    is_not_found = True

        if not sql_executed:
            is_not_found = True

        dt = time.perf_counter() - t0
        if is_not_found:
            filt_note = str(final_json.get("filters_applied") or "").strip()
            html_out = build_not_found_reply(lang, filt_note, last_sql, model_used, dt)
            ans_fixed = (
                "Maʼlumotlarda bu so‘rov bo‘yicha javob yo‘q."
                if lang == "uz"
                else "В данных нет информации по этому запросу."
            )
            res = AgentResult(
                question=question,
                lang=lang,
                answer_text=ans_fixed,
                telegram_html=html_out,
                sql=last_sql,
                sql_rows=last_sql_rows,
                deals_count=0,
                sample_deals=[],
                model_used=model_used,
                latency_s=dt,
                not_found=True,
            )
            self._set_cached(question, res, history)
            return res

        # Verify every number in `answer_text` against `last_sql_rows`
        answer_text = str(final_json.get("answer_text", "")).strip()
        verified_ok, bad_nums = verify_answer_numbers(answer_text, last_sql_rows, last_sql_row_count, question)

        if not verified_ok and (deadline_ts - time.time()) > 5.0:
            # Regenerate once with explicit warning about unmatched numbers
            regen_prompt = (
                f"REJECTED: Your answer contained numbers {bad_nums} that do NOT match the SQL results: "
                f"{json.dumps(last_sql_rows[:10], ensure_ascii=False)}.\n"
                f"Rewrite the JSON response using ONLY exact numbers from the SQL result above."
            )
            messages.append({"role": "assistant", "content": json.dumps(final_json, ensure_ascii=False)})
            messages.append({"role": "user", "content": regen_prompt})
            try:
                regen_resp = self.router.chat(
                    messages=messages,
                    tools=None,
                    temperature=0.0,
                    max_tokens=1000,
                    deadline_ts=deadline_ts,
                )
                model_used = f"{regen_resp.provider}/{regen_resp.model}"
                regen_json = _extract_json_block(regen_resp.content)
                if regen_json and "answer_text" in regen_json:
                    cand_text = str(regen_json["answer_text"]).strip()
                    v2_ok, _ = verify_answer_numbers(cand_text, last_sql_rows, last_sql_row_count, question)
                    if v2_ok:
                        final_json = regen_json
                        answer_text = cand_text
                        verified_ok = True
            except Exception:
                pass

        if not verified_ok:
            # Deterministic fallback built strictly from the SQL rows
            answer_text = build_deterministic_answer_from_rows(
                lang,
                last_sql_rows,
                str(final_json.get("filters_applied", "")),
            )

        deals_count, sample_deals = extract_evidence_from_sql_rows(
            last_sql,
            last_sql_rows,
            final_json.get("deals_count"),
            final_json.get("sample_deals"),
        )
        filters_applied = str(final_json.get("filters_applied") or infer_filters_from_sql(last_sql, lang))
        dt = time.perf_counter() - t0

        html_out = format_telegram_response(
            lang=lang,
            answer_text=answer_text,
            filters_applied=filters_applied,
            deals_count=deals_count,
            sample_deals=sample_deals,
            sql=last_sql,
            model_used=model_used,
            latency_s=dt,
        )

        result = AgentResult(
            question=question,
            lang=lang,
            answer_text=answer_text,
            telegram_html=html_out,
            sql=last_sql,
            sql_rows=last_sql_rows,
            deals_count=deals_count,
            sample_deals=sample_deals,
            model_used=model_used,
            latency_s=dt,
            not_found=False,
            cached=False,
            verifier_passed=verified_ok,
        )
        self._set_cached(question, result, history)
        return result
