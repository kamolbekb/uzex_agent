"""Number formatting, RU/UZ language templates, deterministic fallback builder, and Telegram HTML evidence formatter."""

from __future__ import annotations

import html
import re
from typing import Any


def detect_language(text: str) -> str:
    """Detect whether the user's question is in Uzbek ('uz') or Russian ('ru')."""
    s = text.lower()
    # 1. Explicit Uzbek Cyrillic letters or Uzbek Cyrillic words
    if any(ch in s for ch in ("ў", "қ", "ғ", "ҳ")):
        return "uz"
    if re.search(r"\b(битим|битимлар|шартнома|сотувчи|харидор|йилда|нечта|қанча|сўм|вилоят)\b", s):
        return "uz"

    # 2. Explicit Uzbek Latin apostrophes or words (even in mixed script queries)
    uz_markers = (
        r"\bo['‘’ʼʻ`]|\bg['‘’ʼʻ`]|sh|ch|"
        r"\b(necha|nechta|qancha|qaysi|bo['‘’ʼʻ`]?yicha|bitim\w*|bitm\w*|shartnoma\w*|"
        r"sotuvchi\w*|xaridor\w*|so['‘’ʼʻ`]?m\w*|yil\w*|oy\w*|mayda|yanvar\w*|fevral\w*|"
        r"mart\w*|aprel\w*|iyun\w*|iyul\w*|avgust\w*|sentabr\w*|oktabr\w*|noyabr\w*|dekabr\w*|"
        r"viloyat\w*|shahr\w*|shahar\w*|ulush\w*|o['‘’ʼʻ`]?rtacha|umumiy|umummiy|aylanma\w*|"
        r"jami|hajm\w*|summa\w*|narx\w*|haqida|ma['‘’ʼʻ`]?lumot|bering|toping|ko['‘’ʼʻ`]?rsating|"
        r"kurs\w*|eng|ko['‘’ʼʻ`]?p|kam|paxta|sement|benzin|g['‘’ʼʻ`]?isht|po['‘’ʼʻ`]?lat|oltin)\b"
    )
    if re.search(uz_markers, s):
        return "uz"

    # 3. If the text has Latin letters and no Russian Cyrillic letters, treat as Uzbek Latin
    has_latin = bool(re.search(r"[a-z]", s))
    has_cyrillic = bool(re.search(r"[а-яё]", s))
    if has_latin and not has_cyrillic:
        return "uz"

    return "ru"


def format_number(val: int | float, decimals: int = 2) -> str:
    """Format a number with space thousands separator and comma decimal separator (e.g. `1 234 567,89`)."""
    if isinstance(val, bool):
        return str(val)
    if isinstance(val, int) or (isinstance(val, float) and val.is_integer()):
        return f"{int(val):,}".replace(",", " ")
    rounded = round(float(val), decimals)
    if rounded.is_integer():
        return f"{int(rounded):,}".replace(",", " ")
    s = f"{rounded:,.{decimals}f}"
    parts = s.split(".")
    int_part = parts[0].replace(",", " ")
    dec_part = parts[1].rstrip("0")
    return f"{int_part},{dec_part}" if dec_part else int_part


def format_deal_id(deal_id: int | str) -> str:
    """Format deal number as №412 334 (or raw if not int)."""
    try:
        n = int(deal_id)
        return f"№{n:,}".replace(",", " ")
    except (ValueError, TypeError):
        return f"№{deal_id}"


def build_not_found_reply(lang: str, context_note: str = "", sql: str = "", model: str = "fast-path", latency_s: float = 0.0) -> str:
    """Fixed anti-hallucination template when SQL returns 0 rows or entity/date is out of scope."""
    if lang == "uz":
        header = "Maʼlumotlarda bu so‘rov bo‘yicha javob yo‘q."
        if context_note:
            header += f"\n({html.escape(context_note)})"
        scope = (
            "ℹ️ Reestrda 2022-yil 3-yanvardan 2026-yil 30-oktabrgacha bo‘lgan "
            "500 000 ta birja bitimi mavjud."
        )
    else:
        header = "В данных нет информации по этому запросу."
        if context_note:
            header += f"\n({html.escape(context_note)})"
        scope = (
            "ℹ️ В реестре содержатся данные о 500 000 биржевых сделках "
            "за период с 03.01.2022 по 30.10.2026."
        )

    parts = [header, "", scope]
    if sql:
        sql_escaped = html.escape(sql.strip())
        parts.append(f"<blockquote expandable><pre>{sql_escaped}</pre></blockquote>")
    parts.append(f"⚙️ <code>{html.escape(model)}</code> · {latency_s:.1f} s")
    return "\n".join(parts)


def _humanize_col_name(col: str, lang: str) -> str:
    """Map technical SQL column names to clean human-readable Russian or Uzbek labels."""
    cl = col.lower()
    ru_map = {
        "deals_count": "Количество сделок",
        "deal_count": "Количество сделок",
        "cnt": "Количество сделок",
        "count": "Количество",
        "total_deals": "Всего сделок",
        "total_uzs": "Общая сумма (UZS)",
        "sum_uzs": "Сумма (UZS)",
        "amount_uzs": "Сумма (UZS)",
        "total_usd": "Общая сумма (USD)",
        "sum_usd": "Сумма (USD)",
        "amount_usd": "Сумма (USD)",
        "total_vol": "Общий объём",
        "vol": "Объём",
        "volume_units": "Объём",
        "unit": "Ед. изм.",
        "currency": "Валюта",
        "weighted_avg_uzs": "Средневзвешенная цена (UZS)",
        "weighted_avg_price_uzs": "Средневзвешенная цена (UZS)",
        "simple_avg_uzs": "Средняя цена (UZS)",
        "buyer_name": "Покупатель",
        "seller_name": "Продавец",
        "buyer_inn": "ИНН покупателя",
        "seller_inn": "ИНН продавца",
        "region_ru": "Регион",
        "region_uz": "Регион",
        "seller_region_ru": "Регион продавца",
        "buyer_region_ru": "Регион покупателя",
        "product_base": "Товар",
        "product_name": "Наименование товара",
        "year": "Год",
        "month": "Месяц",
    }
    uz_map = {
        "deals_count": "Bitimlar soni",
        "deal_count": "Bitimlar soni",
        "cnt": "Bitimlar soni",
        "count": "Soni",
        "total_deals": "Jami bitimlar",
        "total_uzs": "Jami summa (UZS)",
        "sum_uzs": "Summa (UZS)",
        "amount_uzs": "Summa (UZS)",
        "total_usd": "Jami summa (USD)",
        "sum_usd": "Summa (USD)",
        "amount_usd": "Summa (USD)",
        "total_vol": "Jami hajm",
        "vol": "Hajm",
        "volume_units": "Hajm",
        "unit": "O‘lchov birligi",
        "currency": "Valyuta",
        "weighted_avg_uzs": "O‘rtacha tortilgan narx (UZS)",
        "weighted_avg_price_uzs": "O‘rtacha tortilgan narx (UZS)",
        "simple_avg_uzs": "O‘rtacha narx (UZS)",
        "buyer_name": "Xaridor",
        "seller_name": "Sotuvchi",
        "buyer_inn": "Xaridor STIR (INN)",
        "seller_inn": "Sotuvchi STIR (INN)",
        "region_ru": "Hudud",
        "region_uz": "Hudud",
        "seller_region_ru": "Sotuvchi hududi",
        "buyer_region_ru": "Xaridor hududi",
        "product_base": "Tovar",
        "product_name": "Tovar nomi",
        "year": "Yil",
        "month": "Oy",
    }
    table = uz_map if lang == "uz" else ru_map
    return table.get(cl, col)


def build_deterministic_answer_from_rows(
    lang: str,
    rows: list[dict[str, Any]],
    filters_applied: str = "",
) -> str:
    """Deterministic fallback answer built directly from SQL result rows when LLM output fails numeric verification."""
    if not rows:
        return (
            "Maʼlumotlarda bu so‘rov bo‘yicha javob yo‘q."
            if lang == "uz"
            else "В данных нет информации по этому запросу."
        )

    lines: list[str] = []
    if lang == "uz":
        lines.append("Maʼlumotlar bazasidan olingan aniq natija (bekor qilingan bitimlarsiz):")
    else:
        lines.append("Точный результат по данным реестра (без учёта аннулированных сделок):")

    for idx, row in enumerate(rows[:10], 1):
        kv_parts = []
        for k, v in row.items():
            kl = k.lower()
            # Never print internal evidence sample_deal columns in prose
            if v is None or "sample_deal" in kl:
                continue
            label = _humanize_col_name(k, lang)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                if kl in ("year", "month", "deal_id", "contract_id") or "inn" in kl:
                    kv_parts.append(f"{label}: {v}")
                else:
                    kv_parts.append(f"{label}: {format_number(v)}")
            else:
                kv_parts.append(f"{label}: {v}")
        prefix = f"{idx}. " if len(rows) > 1 else "• "
        lines.append(prefix + " · ".join(kv_parts))

    return "\n".join(lines)


def extract_evidence_from_sql_rows(
    sql: str,
    rows: list[dict[str, Any]],
    llm_deals_count: int | None = None,
    llm_sample_deals: list[Any] | None = None,
) -> tuple[int, list[int]]:
    """Extract total matched deals count and sample deal IDs from SQL rows and LLM metadata."""
    deals_count = 0
    sample_deals: list[int] = []

    # Check columns in rows for deal_id, sample_deal*, deals_count, cnt
    for r in rows:
        for k, v in r.items():
            kl = k.lower()
            if v is None:
                continue
            if kl in ("deals_count", "cnt", "count", "deal_count", "overdue_deals_count", "total_deals") and isinstance(v, (int, float)):
                deals_count += int(v)
            if ("deal_id" in kl or "sample_deal" in kl) and isinstance(v, (int, float)):
                did = int(v)
                if did not in sample_deals:
                    sample_deals.append(did)
            elif isinstance(v, list):
                for item in v:
                    if isinstance(item, int) and 100000 <= item <= 999999 and item not in sample_deals:
                        sample_deals.append(item)

    if deals_count == 0:
        if llm_deals_count and llm_deals_count > 0:
            deals_count = int(llm_deals_count)
        else:
            deals_count = len(rows)

    if llm_sample_deals:
        for item in llm_sample_deals:
            try:
                did = int(str(item).replace("№", "").replace(" ", ""))
                if did not in sample_deals:
                    sample_deals.append(did)
            except ValueError:
                pass

    return deals_count, sample_deals[:4]


def infer_filters_from_sql(sql: str, lang: str) -> str:
    """Build a concise human-readable filter summary from the SQL WHERE clause if not provided."""
    s = sql.strip()
    parts: list[str] = []
    m_prod = re.search(r"product_base\s*=\s*'([^']+)'", s, flags=re.IGNORECASE)
    if m_prod:
        parts.append(f"товар = {m_prod.group(1)}" if lang == "ru" else f"tovar = {m_prod.group(1)}")
    m_yr = re.search(r"\byear\s*=\s*(\d{4})\b", s, flags=re.IGNORECASE)
    if m_yr:
        parts.append(f"год = {m_yr.group(1)}" if lang == "ru" else f"yil = {m_yr.group(1)}")
    m_reg = re.search(r"region_(?:ru|uz)\s*=\s*'([^']+)'", s, flags=re.IGNORECASE)
    if m_reg:
        parts.append(f"регион = {m_reg.group(1)}" if lang == "ru" else f"hudud = {m_reg.group(1)}")
    if re.search(r"NOT\s+is_cancelled|is_cancelled\s*=\s*false", s, flags=re.IGNORECASE):
        parts.append("без аннулированных" if lang == "ru" else "bekor qilinganlarsiz")
    if not parts:
        return "по запросу SQL" if lang == "ru" else "SQL so‘rovi bo‘yicha"
    return ", ".join(parts)


def format_telegram_response(
    lang: str,
    answer_text: str,
    filters_applied: str,
    deals_count: int,
    sample_deals: list[int],
    sql: str,
    model_used: str,
    latency_s: float,
) -> str:
    """Format the final response in Telegram HTML with the required evidence block and collapsible SQL."""
    ans_clean = html.escape(answer_text.strip())
    filt_clean = html.escape(filters_applied.strip() or infer_filters_from_sql(sql, lang))
    cnt_str = format_number(deals_count, 0)

    if lang == "uz":
        ev_title = "📊 <b>Asos:</b>"
        filt_line = f"• Filtrlar: {filt_clean}"
        cnt_line = f"• Topilgan bitimlar / satrlar: {cnt_str}"
        samples_prefix = "• Bitim namunalari: "
    else:
        ev_title = "📊 <b>Основание:</b>"
        filt_line = f"• Фильтры: {filt_clean}"
        cnt_line = f"• Найдено сделок / строк: {cnt_str}"
        samples_prefix = "• Примеры сделок: "

    ev_lines = [ev_title, filt_line, cnt_line]
    if sample_deals:
        formatted_ids = ", ".join(format_deal_id(d) for d in sample_deals[:4])
        ev_lines.append(f"{samples_prefix}{html.escape(formatted_ids)}")

    if sql.strip():
        sql_escaped = html.escape(sql.strip())
        ev_lines.append(f"▸ <b>SQL:</b>\n<blockquote expandable><pre>{sql_escaped}</pre></blockquote>")

    ev_lines.append(f"⚙️ <code>{html.escape(model_used)}</code> · {latency_s:.1f} s")

    return f"{ans_clean}\n\n" + "\n".join(ev_lines)

