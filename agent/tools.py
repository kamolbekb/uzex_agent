"""Agent tools: fuzzy entity lookup (`search_entities`) and read-only SQL execution (`run_sql`)."""

from __future__ import annotations

import datetime
import re
import threading
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
from rapidfuzz import fuzz, process

from etl.normalize import (
    REGIONS_DATA,
    cyr_to_lat,
    normalize_apostrophes,
    normalize_company_name,
    normalize_product_canonical,
    resolve_product_base,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "uzex.duckdb"

# Uzbek product keywords -> Russian product_base / product keywords for cross-lingual product search
UZ_PRODUCT_SYNONYMS: dict[str, str] = {
    "paxta": "хлопок-сырец",
    "paxta xomashyosi": "хлопок-сырец",
    "chigit shroti": "шрот хлопковый",
    "shrot": "шрот хлопковый",
    "paxta yog'i": "масло хлопковое",
    "paxta yogi": "масло хлопковое",
    "kungaboqar yog'i": "масло подсолнечное",
    "kungaboqar yogi": "масло подсолнечное",
    "yog'": "масло",
    "un": "мука пшеничная",
    "bug'doy uni": "мука пшеничная",
    "bug'doy": "пшеница",
    "bugdoy": "пшеница",
    "guruch": "рис",
    "shakar": "сахар",
    "tuz": "соль пищевая",
    "osh tuzi": "соль пищевая",
    "ichimlik suvi": "вода питьевая",
    "suv": "вода питьевая",
    "sement": "портландцемент",
    "portlandsement": "портландцемент",
    "benzin": "бензин автомобильный",
    "dizel": "дизельное топливо",
    "dizel yoqilg'isi": "дизельное топливо",
    "mazut": "мазут",
    "suyultirilgan gaz": "газ сжиженный",
    "propan": "газ сжиженный",
    "bitum": "битум нефтяной",
    "gips": "гипс строительный",
    "ohak": "известь",
    "argon": "аргон газообразный",
    "azot": "азот",
    "sulfat kislota": "кислота серная",
    "kislota": "кислота серная",
    "kaustik soda": "сода каустическая",
    "soda": "сода каустическая",
    "karbamid": "карбамид",
    "mochevina": "карбамид",
    "ammiakli selitra": "селитра аммиачная",
    "selitra": "селитра аммиачная",
    "emal": "эмаль",
    "polietilen quvur": "трубы полиэтиленовые",
    "quvur": "трубы полиэтиленовые",
    "polietilen": "полиэтилен",
    "polipropilen": "полипропилен",
    "polivinilxlorid": "поливинилхлорид",
    "pvx": "поливинилхлорид",
    "taxta": "пиломатериалы",
    "yog'och": "пиломатериалы",
    "g'isht": "кирпич керамический",
    "gisht": "кирпич керамический",
    "oyna": "стекло листовое",
    "shisha": "стекло листовое",
    "po'lat list": "лист стальной",
    "armatura simi": "проволока арматурная",
    "sim": "проволока арматурная",
    "armatura": "арматура стальная",
    "mis katod": "катод медный",
    "mis": "катод медный",
    "alyuminiy profil": "профиль алюминиевый",
    "podshipnik": "подшипник",
    "kabel": "кабель силовой",
    "ip-kalava": "пряжа хлопчатобумажная",
    "kalava": "пряжа хлопчатобумажная",
    "ombixorm": "комбикорм",
    "omonat yem": "комбикорм",
    "yem": "комбикорм",
}


FORBIDDEN_SQL_PATTERN = re.compile(
    r"\b("
    r"insert|update|delete|drop|alter|create|replace|truncate|"
    r"attach|detach|copy|export|import|pragma|install|load|call|execute"
    r")\b",
    flags=re.IGNORECASE,
)


def _clean_val(v: Any) -> Any:
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, float):
        return round(v, 4)
    return v


class DatabaseTools:
    """Thread-safe read-only DuckDB query runner and in-memory fuzzy entity index."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or DB_PATH
        self._lock = threading.Lock()
        self._con = duckdb.connect(str(self.db_path), read_only=True)
        self._load_entity_indices()

    def _load_entity_indices(self) -> None:
        with self._lock:
            # 1. Participants
            self._participants = self._con.execute(
                "SELECT inn, name, name_norm, name_search, region_uz, region_ru, role, activity_type FROM participants"
            ).fetchall()
            # Map index -> search string for rapidfuzz
            self._part_choices = {i: row[3] for i, row in enumerate(self._participants)}

            # 2. Brokers
            self._brokers = self._con.execute(
                "SELECT broker_code, name, name_norm, name_search, inn, region_uz, region_ru FROM brokers"
            ).fetchall()
            self._broker_choices = {
                i: f"{row[0].lower()} {row[3]}" for i, row in enumerate(self._brokers)
            }

            # 3. Products (distinct product_base, product_canonical, product_name, hs_code, category, unit)
            self._products_base = self._con.execute(
                """
                SELECT
                    product_base,
                    hs_code,
                    category,
                    LIST(DISTINCT unit) AS units,
                    LIST(DISTINCT product_canonical) AS canonicals,
                    COUNT(*) AS contract_count
                FROM contracts
                GROUP BY product_base, hs_code, category
                ORDER BY product_base
                """
            ).fetchall()

            self._products_canonical = self._con.execute(
                """
                SELECT
                    product_canonical,
                    product_base,
                    hs_code,
                    LIST(DISTINCT product_name) AS raw_names,
                    COUNT(*) AS contract_count
                FROM contracts
                GROUP BY product_canonical, product_base, hs_code
                ORDER BY product_canonical
                """
            ).fetchall()

    # ------------------------------------------------------------------
    # Tool 1: search_entities
    # ------------------------------------------------------------------
    def search_entities(self, entity_type: str, query: str, limit: int = 5) -> dict[str, Any]:
        """Fuzzy lookup for 'company' ('participant'), 'broker', 'product', or 'region'."""
        etype = (entity_type or "").strip().lower()
        q_raw = (query or "").strip()
        if not q_raw:
            return {"entity_type": etype, "query": q_raw, "matches": []}

        if etype in ("company", "participant", "seller", "buyer", "participants"):
            return self._search_participants(q_raw, limit)
        if etype in ("broker", "brokers"):
            return self._search_brokers(q_raw, limit)
        if etype in ("product", "products", "commodity", "товар"):
            return self._search_products(q_raw, limit)
        if etype in ("region", "regions", "область", "вилоят"):
            return self._search_regions(q_raw, limit)

        # If unknown type, search across all four
        return {
            "entity_type": "all",
            "query": q_raw,
            "companies": self._search_participants(q_raw, 3)["matches"],
            "brokers": self._search_brokers(q_raw, 3)["matches"],
            "products": self._search_products(q_raw, 3)["matches"],
            "regions": self._search_regions(q_raw, 2)["matches"],
        }

    def _search_participants(self, query: str, limit: int = 5) -> dict[str, Any]:
        q_digits = re.sub(r"\D", "", query)
        matches = []

        # Exact/prefix INN match if numeric
        if len(q_digits) >= 5:
            for row in self._participants:
                if q_digits in row[0]:
                    matches.append(
                        {
                            "inn": row[0],
                            "name": row[1],
                            "region_uz": row[4],
                            "region_ru": row[5],
                            "role": row[6],
                            "activity_type": row[7],
                            "score": 100.0 if row[0] == q_digits else 95.0,
                        }
                    )
                    if len(matches) >= limit:
                        return {"entity_type": "company", "query": query, "matches": matches}

        q_clean, _ = normalize_company_name(query)
        q_lat = cyr_to_lat(q_clean).replace("'", "")
        if not q_lat:
            q_lat = normalize_apostrophes(query.lower())

        scored: list[tuple[float, int]] = []
        for idx, row in enumerate(self._participants):
            parts = [p.strip() for p in row[3].split("|") if p.strip()]
            best = max((float(fuzz.WRatio(q_lat, p)) for p in parts), default=0.0)
            best_raw = float(fuzz.WRatio(q_clean, row[2]))
            sc = max(best, best_raw)
            if sc >= 58.0:
                scored.append((sc, idx))
        scored.sort(key=lambda x: -x[0])

        for score, idx in scored[:limit]:
            row = self._participants[idx]
            matches.append(
                {
                    "inn": row[0],
                    "name": row[1],
                    "region_uz": row[4],
                    "region_ru": row[5],
                    "role": row[6],
                    "activity_type": row[7],
                    "score": round(float(score), 1),
                }
            )

        return {"entity_type": "company", "query": query, "matches": matches}

    def _search_brokers(self, query: str, limit: int = 5) -> dict[str, Any]:
        q_up = query.strip().upper()
        matches = []
        # Exact broker code check (e.g. BK001, БК001)
        m_code = re.search(r"(?:BK|БК)[-\s]?(\d{1,3})", q_up)
        if m_code:
            target_code = f"BK{int(m_code.group(1)):03d}"
            for row in self._brokers:
                if row[0] == target_code:
                    matches.append(
                        {
                            "broker_code": row[0],
                            "name": row[1],
                            "inn": row[4],
                            "region_uz": row[5],
                            "region_ru": row[6],
                            "score": 100.0,
                        }
                    )
                    return {"entity_type": "broker", "query": query, "matches": matches}

        q_clean, _ = normalize_company_name(query)
        q_lat = cyr_to_lat(q_clean).replace("'", "")
        if not q_lat:
            q_lat = normalize_apostrophes(query.lower())

        scored: list[tuple[float, int]] = []
        for idx, row in enumerate(self._brokers):
            parts = [p.strip() for p in row[3].split("|") if p.strip()]
            # Also compare without the word 'broker'/'брокер' if query doesn't have it
            parts_no_brk = [re.sub(r"\b(broker|брокер)\b", "", p).strip() for p in parts]
            all_parts = [p for p in (parts + parts_no_brk) if p]
            best = max((float(fuzz.WRatio(q_lat, p)) for p in all_parts), default=0.0)
            best_raw = float(fuzz.WRatio(q_clean, row[2]))
            sc = max(best, best_raw)
            if sc >= 58.0:
                scored.append((sc, idx))
        scored.sort(key=lambda x: -x[0])

        for score, idx in scored[:limit]:
            row = self._brokers[idx]
            matches.append(
                {
                    "broker_code": row[0],
                    "name": row[1],
                    "inn": row[4],
                    "region_uz": row[5],
                    "region_ru": row[6],
                    "score": round(float(score), 1),
                }
            )
        return {"entity_type": "broker", "query": query, "matches": matches}

    def _search_products(self, query: str, limit: int = 5) -> dict[str, Any]:
        q_norm = normalize_apostrophes(query.lower()).replace("ё", "е").strip()
        # Check Uzbek synonyms first
        q_uz_lat = cyr_to_lat(q_norm)
        mapped_ru = None
        for uz_kw, ru_base in sorted(UZ_PRODUCT_SYNONYMS.items(), key=lambda x: -len(x[0])):
            if uz_kw in q_norm or uz_kw in q_uz_lat:
                mapped_ru = ru_base
                break

        search_target = mapped_ru or resolve_product_base(q_norm)
        canon_target = normalize_product_canonical(mapped_ru or q_norm)

        base_choices = {i: row[0] for i, row in enumerate(self._products_base)}
        base_hits = process.extract(
            search_target,
            base_choices,
            scorer=fuzz.WRatio,
            limit=limit,
            score_cutoff=55,
        )

        matches = []
        for _, score, idx in base_hits:
            row = self._products_base[idx]
            matches.append(
                {
                    "product_base": row[0],
                    "hs_code": row[1],
                    "category": row[2],
                    "units": list(row[3]),
                    "product_canonical_variants": list(row[4])[:10],
                    "contract_count": int(row[5]),
                    "score": round(float(score), 1),
                }
            )

        # Also check canonical level if query has specific grade/sort
        canon_choices = {i: row[0] for i, row in enumerate(self._products_canonical)}
        canon_hits = process.extract(
            canon_target,
            canon_choices,
            scorer=fuzz.WRatio,
            limit=limit,
            score_cutoff=65,
        )
        canon_matches = [
            {
                "product_canonical": self._products_canonical[idx][0],
                "product_base": self._products_canonical[idx][1],
                "hs_code": self._products_canonical[idx][2],
                "raw_names_sample": list(self._products_canonical[idx][3])[:5],
                "score": round(float(sc), 1),
            }
            for _, sc, idx in canon_hits
        ]

        return {
            "entity_type": "product",
            "query": query,
            "matches": matches,
            "canonical_matches": canon_matches,
        }

    def _search_regions(self, query: str, limit: int = 5) -> dict[str, Any]:
        q_norm = normalize_apostrophes(query.lower()).strip()
        q_lat = cyr_to_lat(q_norm).replace("'", "")
        scored: list[tuple[float, int]] = []
        for i, r in enumerate(REGIONS_DATA):
            tokens = [r.region_uz.lower(), r.region_ru.lower()] + r.aliases.split("|")
            best = 0.0
            for tok in tokens:
                tok_clean = normalize_apostrophes(tok.strip())
                sc1 = fuzz.WRatio(q_norm, tok_clean)
                sc2 = fuzz.WRatio(q_lat, cyr_to_lat(tok_clean).replace("'", ""))
                best = max(best, float(sc1), float(sc2))
            if best >= 55.0:
                scored.append((best, i))
        scored.sort(key=lambda x: -x[0])
        matches = []
        for score, idx in scored[:limit]:
            r = REGIONS_DATA[idx]
            matches.append(
                {
                    "region_uz": r.region_uz,
                    "region_ru": r.region_ru,
                    "score": round(score, 1),
                }
            )
        return {"entity_type": "region", "query": query, "matches": matches}

    # ------------------------------------------------------------------
    # Tool 2: run_sql (read-only guarded DuckDB execution)
    # ------------------------------------------------------------------
    def run_sql(self, query: str, max_rows: int = 30) -> dict[str, Any]:
        """Validate and run a read-only SELECT/WITH SQL query on uzex.duckdb."""
        sql = (query or "").strip()
        # Strip trailing semicolon
        sql = re.sub(r";+\s*$", "", sql)

        if not sql:
            return {"ok": False, "error": "Empty SQL query."}

        # Ensure single statement (no unquoted semicolons)
        sql_no_strings = re.sub(r"'[^']*'", "''", sql)
        if ";" in sql_no_strings:
            return {"ok": False, "error": "Only a single SELECT statement is allowed (no semicolons)."}

        if not re.match(r"^\s*(SELECT|WITH)\b", sql, flags=re.IGNORECASE):
            return {"ok": False, "error": "Query must start with SELECT or WITH."}

        m_bad = FORBIDDEN_SQL_PATTERN.search(sql_no_strings)
        if m_bad:
            return {"ok": False, "error": f"Forbidden SQL keyword: {m_bad.group(1)}"}

        # Add automatic LIMIT 200 if query has no top-level LIMIT
        if not re.search(r"\bLIMIT\s+\d+\s*$", sql_no_strings, flags=re.IGNORECASE):
            exec_sql = f"{sql}\nLIMIT 200"
        else:
            exec_sql = sql

        # Execute with timeout guard
        result_holder: dict[str, Any] = {}

        def _worker() -> None:
            try:
                with self._lock:
                    cur = self._con.execute(exec_sql)
                    cols = [d[0] for d in cur.description] if cur.description else []
                    raw_rows = cur.fetchall()
                result_holder["ok"] = True
                result_holder["columns"] = cols
                result_holder["raw_rows"] = raw_rows
            except Exception as e:
                result_holder["ok"] = False
                result_holder["error"] = f"{type(e).__name__}: {e}"

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join(timeout=10.0)
        if t.is_alive():
            try:
                self._con.interrupt()
            except Exception:
                pass
            return {"ok": False, "error": "Query exceeded 10s timeout. Please simplify or add filters."}

        if not result_holder.get("ok"):
            return {"ok": False, "error": result_holder.get("error", "Unknown SQL error"), "sql": sql}

        cols: list[str] = result_holder["columns"]
        raw_rows: list[tuple[Any, ...]] = result_holder["raw_rows"]
        total_returned = len(raw_rows)

        cleaned_rows: list[dict[str, Any]] = []
        for r in raw_rows[:max_rows]:
            cleaned_rows.append({col: _clean_val(val) for col, val in zip(cols, r)})

        return {
            "ok": True,
            "sql": sql,
            "columns": cols,
            "row_count": total_returned,
            "truncated": total_returned > max_rows,
            "rows": cleaned_rows,
        }


# OpenAI tool schema definitions for the LLM
OPENAI_TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_entities",
            "description": (
                "Fuzzy search for companies (participants), brokers, products, or regions in the database. "
                "Always call this first when the user mentions a company name, broker name, or specific product in Russian or Uzbek."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_type": {
                        "type": "string",
                        "enum": ["company", "broker", "product", "region"],
                        "description": "Type of entity to search for.",
                    },
                    "query": {
                        "type": "string",
                        "description": "Search text (company name, INN, broker name/code, product name, or region).",
                    },
                },
                "required": ["entity_type", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_sql",
            "description": (
                "Execute a read-only DuckDB SELECT query against `deals_full`, `contracts`, `participants`, `brokers`, `usd_rates`, or `regions`. "
                "Returns columns, row_count, and up to 30 rows."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Valid DuckDB SQL SELECT query.",
                    }
                },
                "required": ["query"],
            },
        },
    },
]
