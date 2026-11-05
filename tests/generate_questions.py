"""Generate tests/questions.yaml (40 RU/UZ questions incl. 10 trap questions) with ground-truth values computed directly from uzex.duckdb."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "uzex.duckdb"
OUT_PATH = PROJECT_ROOT / "tests" / "questions.yaml"


def generate_questions() -> None:
    con = duckdb.connect(str(DB_PATH), read_only=True)

    # Helper to run a scalar or row query
    def q1(sql: str) -> Any:
        return con.execute(sql).fetchone()

    questions: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # PART A: 15 Russian Data Questions (Q01 .. Q15)
    # ------------------------------------------------------------------
    # Q01: Deal lookup by number
    r = q1("SELECT amount, volume_units, seller_inn FROM deals_full WHERE deal_id = 100001")
    questions.append({
        "id": "Q01",
        "lang": "ru",
        "category": "lookup",
        "question": "Сделка №100001",
        "sql": "SELECT deal_id, amount, volume_units, seller_inn FROM deals_full WHERE deal_id = 100001",
        "not_found": False,
        "expected_numbers": [float(r[0]), float(r[1])],
        "expected_substrings": [str(r[2])],
    })

    # Q02: USD rate on a specific date
    r = q1("SELECT usd_rate FROM usd_rates WHERE rate_date = DATE '2024-03-15'")
    questions.append({
        "id": "Q02",
        "lang": "ru",
        "category": "usd_rate",
        "question": "Какой курс доллара был 15.03.2024?",
        "sql": "SELECT usd_rate FROM usd_rates WHERE rate_date = DATE '2024-03-15'",
        "not_found": False,
        "expected_numbers": [float(r[0])],
        "expected_substrings": [],
    })

    # Q03: Cotton 2025 volume and deal count
    r = q1("SELECT COUNT(*), SUM(volume_units), ROUND(SUM(amount_uzs), 2) FROM deals_full WHERE product_base = 'хлопок-сырец' AND year = 2025 AND NOT is_cancelled")
    questions.append({
        "id": "Q03",
        "lang": "ru",
        "category": "sum_volume",
        "question": "Какой объём и сколько сделок было заключено по хлопку-сырцу в 2025 году?",
        "sql": "SELECT COUNT(*) AS deals_count, SUM(volume_units) AS total_vol, ROUND(SUM(amount_uzs), 2) AS total_uzs FROM deals_full WHERE product_base = 'хлопок-сырец' AND year = 2025 AND NOT is_cancelled",
        "not_found": False,
        "expected_numbers": [float(r[0]), float(r[1])],
        "expected_substrings": [],
    })

    # Q04: Top-1 buyer of portland cement in 2024 by amount_uzs
    r = q1("SELECT buyer_inn, buyer_name, COUNT(*), ROUND(SUM(amount_uzs), 2) FROM deals_full WHERE product_base = 'портландцемент' AND year = 2024 AND NOT is_cancelled GROUP BY buyer_inn, buyer_name ORDER BY SUM(amount_uzs) DESC LIMIT 1")
    questions.append({
        "id": "Q04",
        "lang": "ru",
        "category": "top_n",
        "question": "Какая компания (укажите ИНН и название) заняла 1 место среди покупателей портландцемента в 2024 году по сумме сделок в сумах?",
        "sql": "SELECT buyer_inn, buyer_name, COUNT(*) AS deals_count, ROUND(SUM(amount_uzs), 2) AS total_uzs FROM deals_full WHERE product_base = 'портландцемент' AND year = 2024 AND NOT is_cancelled GROUP BY buyer_inn, buyer_name ORDER BY total_uzs DESC LIMIT 1",
        "not_found": False,
        "expected_numbers": [float(r[2])],
        "expected_substrings": [str(r[0])],
    })

    # Q05: Weighted average price of AI-92 gasoline in 2025 (in tons)
    r = q1("SELECT COUNT(*), ROUND(SUM(amount_uzs)/SUM(volume_units), 2) FROM deals_full WHERE product_base = 'бензин автомобильный' AND brand = 'АИ-92' AND unit = 'тонна' AND year = 2025 AND NOT is_cancelled")
    questions.append({
        "id": "Q05",
        "lang": "ru",
        "category": "avg_price",
        "question": "Какова средневзвешенная цена за 1 тонну автомобильного бензина марки АИ-92 (с единицей измерения тонна) в 2025 году в сумах и сколько было таких сделок?",
        "sql": "SELECT COUNT(*) AS deals_count, ROUND(SUM(amount_uzs)/SUM(volume_units), 2) AS weighted_avg_uzs FROM deals_full WHERE product_base = 'бензин автомобильный' AND brand = 'АИ-92' AND unit = 'тонна' AND year = 2025 AND NOT is_cancelled",
        "not_found": False,
        "expected_numbers": [float(r[0]), float(r[1])],
        "expected_substrings": [],
    })

    # Q06: Export deals count and total USD amount in 2025
    r = q1("SELECT COUNT(*), ROUND(SUM(amount_usd), 2) FROM deals_full WHERE contract_type = 'Экспортный' AND year = 2025 AND NOT is_cancelled")
    questions.append({
        "id": "Q06",
        "lang": "ru",
        "category": "export_currency",
        "question": "Сколько экспортных сделок было совершено в 2025 году и на какую общую сумму в долларах США (USD)?",
        "sql": "SELECT COUNT(*) AS deals_count, ROUND(SUM(amount_usd), 2) AS total_usd FROM deals_full WHERE contract_type = 'Экспортный' AND year = 2025 AND NOT is_cancelled",
        "not_found": False,
        "expected_numbers": [float(r[0]), float(r[1])],
        "expected_substrings": [],
    })

    # Q07: Cancelled deals count in 2024
    r = q1("SELECT COUNT(*) FROM deals_full WHERE year = 2024 AND is_cancelled")
    questions.append({
        "id": "Q07",
        "lang": "ru",
        "category": "status",
        "question": "Сколько сделок в 2024 году имеют статус «Аннулирован(Не оплачен)»?",
        "sql": "SELECT COUNT(*) AS cancelled_count FROM deals_full WHERE year = 2024 AND is_cancelled",
        "not_found": False,
        "expected_numbers": [float(r[0])],
        "expected_substrings": [],
    })

    # Q08: Top seller broker in Tashkent city in 2025 by deal count
    r = q1("SELECT seller_broker_code, seller_broker_name, COUNT(*) FROM deals_full WHERE region_ru = 'г. Ташкент' AND year = 2025 AND NOT is_cancelled GROUP BY seller_broker_code, seller_broker_name ORDER BY COUNT(*) DESC LIMIT 1")
    questions.append({
        "id": "Q08",
        "lang": "ru",
        "category": "broker",
        "question": "Какой брокер продавца (код брокера) провёл больше всего сделок в регионе г. Ташкент в 2025 году?",
        "sql": "SELECT seller_broker_code, seller_broker_name, COUNT(*) AS deals_count FROM deals_full WHERE region_ru = 'г. Ташкент' AND year = 2025 AND NOT is_cancelled GROUP BY seller_broker_code, seller_broker_name ORDER BY deals_count DESC LIMIT 1",
        "not_found": False,
        "expected_numbers": [float(r[2])],
        "expected_substrings": [str(r[0])],
    })

    # Q09: Overdue delivery deals in 2023
    r = q1("SELECT COUNT(*) FROM deals_full WHERE year = 2023 AND NOT is_cancelled AND actual_delivery_days > delivery_term")
    questions.append({
        "id": "Q09",
        "lang": "ru",
        "category": "delivery_delay",
        "question": "В скольких сделках за 2023 год фактический срок поставки превысил установленный контрактом срок поставки (actual_delivery_days > delivery_term)?",
        "sql": "SELECT COUNT(*) AS overdue_count FROM deals_full WHERE year = 2023 AND NOT is_cancelled AND actual_delivery_days > delivery_term",
        "not_found": False,
        "expected_numbers": [float(r[0])],
        "expected_substrings": [],
    })

    # Q10: Copper cathode volume in tons in 2026
    r = q1("SELECT COUNT(*), SUM(volume_units) FROM deals_full WHERE product_base = 'катод медный' AND year = 2026 AND NOT is_cancelled")
    questions.append({
        "id": "Q10",
        "lang": "ru",
        "category": "product_volume",
        "question": "Сколько сделок и какой общий физический объём (в тоннах) по медному катоду был реализован в 2026 году?",
        "sql": "SELECT COUNT(*) AS deals_count, SUM(volume_units) AS total_tons FROM deals_full WHERE product_base = 'катод медный' AND year = 2026 AND NOT is_cancelled",
        "not_found": False,
        "expected_numbers": [float(r[0]), float(r[1])],
        "expected_substrings": [],
    })

    # Q11: Deals by specific company INN 915688255 ("ҲАРАЙЎМ КИМЁ" МЧЖ)
    r = q1("SELECT COUNT(*), ROUND(SUM(amount_uzs), 2) FROM deals_full WHERE seller_inn = '915688255' AND NOT is_cancelled")
    questions.append({
        "id": "Q11",
        "lang": "ru",
        "category": "company",
        "question": "Сколько сделок в качестве продавца заключила компания с ИНН 915688255 («ҲАРАЙЎМ КИМЁ» МЧЖ) за всё время и на какую сумму в сумах?",
        "sql": "SELECT COUNT(*) AS deals_count, ROUND(SUM(amount_uzs), 2) AS total_uzs FROM deals_full WHERE seller_inn = '915688255' AND NOT is_cancelled",
        "not_found": False,
        "expected_numbers": [float(r[0]), float(r[1])],
        "expected_substrings": ["915688255"],
    })

    # Q12: Forward contracts count in 2025
    r = q1("SELECT COUNT(*), ROUND(SUM(amount_uzs), 2) FROM deals_full WHERE contract_form = 'Форвард' AND year = 2025 AND NOT is_cancelled")
    questions.append({
        "id": "Q12",
        "lang": "ru",
        "category": "contract_form",
        "question": "Сколько форвардных сделок (Форма контракта = Форвард) было заключено в 2025 году?",
        "sql": "SELECT COUNT(*) AS deals_count, ROUND(SUM(amount_uzs), 2) AS total_uzs FROM deals_full WHERE contract_form = 'Форвард' AND year = 2025 AND NOT is_cancelled",
        "not_found": False,
        "expected_numbers": [float(r[0])],
        "expected_substrings": [],
    })

    # Q13: Wheat flour deals in Samarkand region in 2024
    r = q1("SELECT COUNT(*), SUM(volume_units) FROM deals_full WHERE product_base = 'мука пшеничная' AND region_ru = 'Самаркандская область' AND year = 2024 AND NOT is_cancelled")
    questions.append({
        "id": "Q13",
        "lang": "ru",
        "category": "region_product",
        "question": "Сколько сделок и какой физический объём пшеничной муки (в единицах измерения контракта — килограммах) продано в Самаркандской области в 2024 году?",
        "sql": "SELECT COUNT(*) AS deals_count, SUM(volume_units) AS total_vol FROM deals_full WHERE product_base = 'мука пшеничная' AND region_ru = 'Самаркандская область' AND year = 2024 AND NOT is_cancelled",
        "not_found": False,
        "expected_numbers": [float(r[0]), float(r[1])],
        "expected_substrings": [],
    })

    # Q14: Deals with status 'Новый' (all on 30.10.2026)
    r = q1("SELECT COUNT(*) FROM deals_full WHERE status = 'Новый'")
    questions.append({
        "id": "Q14",
        "lang": "ru",
        "category": "status",
        "question": "Сколько всего сделок в реестре находятся в статусе «Новый»?",
        "sql": "SELECT COUNT(*) AS deals_count FROM deals_full WHERE status = 'Новый'",
        "not_found": False,
        "expected_numbers": [float(r[0])],
        "expected_substrings": [],
    })

    # Q15: Reverse auction deals count in 2022
    r = q1("SELECT COUNT(*) FROM deals_full WHERE auction_type = 'Обратный аукцион' AND year = 2022 AND NOT is_cancelled")
    questions.append({
        "id": "Q15",
        "lang": "ru",
        "category": "auction_type",
        "question": "Сколько сделок по типу торгов «Обратный аукцион» было проведено в 2022 году (без аннулированных)?",
        "sql": "SELECT COUNT(*) AS deals_count FROM deals_full WHERE auction_type = 'Обратный аукцион' AND year = 2022 AND NOT is_cancelled",
        "not_found": False,
        "expected_numbers": [float(r[0])],
        "expected_substrings": [],
    })

    # ------------------------------------------------------------------
    # PART B: 15 Uzbek Data Questions (Q16 .. Q30)
    # ------------------------------------------------------------------
    # Q16: Fast-path deal lookup in Uzbek
    r = q1("SELECT amount, volume_units, buyer_inn FROM deals_full WHERE deal_id = 100005")
    questions.append({
        "id": "Q16",
        "lang": "uz",
        "category": "lookup",
        "question": "Bitim №100005",
        "sql": "SELECT deal_id, amount, volume_units, buyer_inn FROM deals_full WHERE deal_id = 100005",
        "not_found": False,
        "expected_numbers": [float(r[0]), float(r[1])],
        "expected_substrings": [str(r[2])],
    })

    # Q17: Fast-path USD rate in Uzbek
    r = q1("SELECT usd_rate FROM usd_rates WHERE rate_date = DATE '2025-05-01'")
    questions.append({
        "id": "Q17",
        "lang": "uz",
        "category": "usd_rate",
        "question": "01.05.2025 sanasida dollar kursi qancha edi?",
        "sql": "SELECT usd_rate FROM usd_rates WHERE rate_date = DATE '2025-05-01'",
        "not_found": False,
        "expected_numbers": [float(r[0])],
        "expected_substrings": [],
    })

    # Q18: Cottonseed oil deals and volume in 2025 in Uzbek
    r = q1("SELECT COUNT(*), SUM(volume_units), ROUND(SUM(amount_uzs), 2) FROM deals_full WHERE product_base = 'масло хлопковое' AND year = 2025 AND NOT is_cancelled")
    questions.append({
        "id": "Q18",
        "lang": "uz",
        "category": "sum_volume",
        "question": "2025-yilda paxta yog‘i bo‘yicha nechta bitim tuzilgan va umumiy hajmi (tonnada) qancha?",
        "sql": "SELECT COUNT(*) AS deals_count, SUM(volume_units) AS total_tons, ROUND(SUM(amount_uzs), 2) AS total_uzs FROM deals_full WHERE product_base = 'масло хлопковое' AND year = 2025 AND NOT is_cancelled",
        "not_found": False,
        "expected_numbers": [float(r[0]), float(r[1])],
        "expected_substrings": [],
    })

    # Q19: Fergana region deals count in 2024 in Uzbek
    r = q1("SELECT COUNT(*), ROUND(SUM(amount_uzs), 2) FROM deals_full WHERE region_uz = 'Farg‘ona viloyati' AND year = 2024 AND NOT is_cancelled")
    questions.append({
        "id": "Q19",
        "lang": "uz",
        "category": "region",
        "question": "2024-yilda Farg‘ona viloyatida jami nechta birja bitimi tuzilgan (bekor qilinganlarsiz)?",
        "sql": "SELECT COUNT(*) AS deals_count, ROUND(SUM(amount_uzs), 2) AS total_uzs FROM deals_full WHERE region_uz = 'Farg‘ona viloyati' AND year = 2024 AND NOT is_cancelled",
        "not_found": False,
        "expected_numbers": [float(r[0])],
        "expected_substrings": [],
    })

    # Q20: Sugar deals and volume in 2023 in Uzbek
    r = q1("SELECT COUNT(*), SUM(volume_units) FROM deals_full WHERE product_base = 'сахар' AND year = 2023 AND NOT is_cancelled")
    questions.append({
        "id": "Q20",
        "lang": "uz",
        "category": "product_volume",
        "question": "2023-yilda shakar (product_base = 'сахар') bo‘yicha nechta bitim tuzilgan va necha tonna sotilgan?",
        "sql": "SELECT COUNT(*) AS deals_count, SUM(volume_units) AS total_tons FROM deals_full WHERE product_base = 'сахар' AND year = 2023 AND NOT is_cancelled",
        "not_found": False,
        "expected_numbers": [float(r[0]), float(r[1])],
        "expected_substrings": [],
    })

    # Q21: Broker BK001 deals as buyer broker in 2025 in Uzbek
    r = q1("SELECT COUNT(*), ROUND(SUM(amount_uzs), 2) FROM deals_full WHERE buyer_broker_code = 'BK001' AND year = 2025 AND NOT is_cancelled")
    questions.append({
        "id": "Q21",
        "lang": "uz",
        "category": "broker",
        "question": "BK001 kodli broker 2025-yilda xaridor brokeri (buyer_broker_code) sifatida nechta bitimda qatnashgan?",
        "sql": "SELECT COUNT(*) AS deals_count, ROUND(SUM(amount_uzs), 2) AS total_uzs FROM deals_full WHERE buyer_broker_code = 'BK001' AND year = 2025 AND NOT is_cancelled",
        "not_found": False,
        "expected_numbers": [float(r[0])],
        "expected_substrings": ["BK001"],
    })

    # Q22: Liquefied gas (газ сжиженный) deals in 2026 in Uzbek
    r = q1("SELECT COUNT(*), SUM(volume_units) FROM deals_full WHERE product_base = 'газ сжиженный' AND year = 2026 AND NOT is_cancelled")
    questions.append({
        "id": "Q22",
        "lang": "uz",
        "category": "product_volume",
        "question": "2026-yilda suyultirilgan gaz bo‘yicha nechta bitim tuzilgan va jami necha tonna sotilgan?",
        "sql": "SELECT COUNT(*) AS deals_count, SUM(volume_units) AS total_tons FROM deals_full WHERE product_base = 'газ сжиженный' AND year = 2026 AND NOT is_cancelled",
        "not_found": False,
        "expected_numbers": [float(r[0]), float(r[1])],
        "expected_substrings": [],
    })

    # Q23: Partially executed deals ('Исполнен(Частично)') count in 2025 in Uzbek
    r = q1("SELECT COUNT(*) FROM deals_full WHERE status = 'Исполнен(Частично)' AND year = 2025")
    questions.append({
        "id": "Q23",
        "lang": "uz",
        "category": "status",
        "question": "2025-yilda nechta bitim «Исполнен(Частично)» (qisman bajarilgan) holatida?",
        "sql": "SELECT COUNT(*) AS deals_count FROM deals_full WHERE status = 'Исполнен(Частично)' AND year = 2025",
        "not_found": False,
        "expected_numbers": [float(r[0])],
        "expected_substrings": [],
    })

    # Q24: Steel rebar (арматура стальная) across all regions in 2024 in Uzbek
    r = q1("SELECT COUNT(*), SUM(volume_units) FROM deals_full WHERE product_base = 'арматура стальная' AND year = 2024 AND NOT is_cancelled")
    questions.append({
        "id": "Q24",
        "lang": "uz",
        "category": "product_volume",
        "question": "2024-yilda po‘lat armatura (арматура стальная) bo‘yicha nechta bitim tuzilgan va hajmi necha tonna?",
        "sql": "SELECT COUNT(*) AS deals_count, SUM(volume_units) AS total_tons FROM deals_full WHERE product_base = 'арматура стальная' AND year = 2024 AND NOT is_cancelled",
        "not_found": False,
        "expected_numbers": [float(r[0]), float(r[1])],
        "expected_substrings": [],
    })

    # Q25: Total participants count in registry in Uzbek
    r = q1("SELECT COUNT(*) FROM participants")
    questions.append({
        "id": "Q25",
        "lang": "uz",
        "category": "reference",
        "question": "Ishtirokchilar reestrida (participants jadvalida) jami nechta kompaniya ro‘yxatga olingan?",
        "sql": "SELECT COUNT(*) AS participants_count FROM participants",
        "not_found": False,
        "expected_numbers": [float(r[0])],
        "expected_substrings": [],
    })

    # Q26: Futures contracts count in 2026 in Uzbek
    r = q1("SELECT COUNT(*) FROM deals_full WHERE contract_form = 'Фьючерс' AND year = 2026 AND NOT is_cancelled")
    questions.append({
        "id": "Q26",
        "lang": "uz",
        "category": "contract_form",
        "question": "2026-yilda fyuchers (Фьючерс) shartnomalari bo‘yicha nechta bitim tuzilgan (bekor qilinganlarsiz)?",
        "sql": "SELECT COUNT(*) AS deals_count FROM deals_full WHERE contract_form = 'Фьючерс' AND year = 2026 AND NOT is_cancelled",
        "not_found": False,
        "expected_numbers": [float(r[0])],
        "expected_substrings": [],
    })

    # Q27: Carbamide (карбамид) deals and volume in 2025 in Uzbek
    r = q1("SELECT COUNT(*), SUM(volume_units) FROM deals_full WHERE product_base = 'карбамид' AND year = 2025 AND NOT is_cancelled")
    questions.append({
        "id": "Q27",
        "lang": "uz",
        "category": "product_volume",
        "question": "2025-yilda karbamid bo‘yicha nechta bitim tuzilgan va necha tonna mahsulot sotilgan?",
        "sql": "SELECT COUNT(*) AS deals_count, SUM(volume_units) AS total_tons FROM deals_full WHERE product_base = 'карбамид' AND year = 2025 AND NOT is_cancelled",
        "not_found": False,
        "expected_numbers": [float(r[0]), float(r[1])],
        "expected_substrings": [],
    })

    # Q28: Top-1 seller in Bukhara region in 2025 by deal count in Uzbek
    r = q1("SELECT seller_inn, seller_name, COUNT(*) FROM deals_full WHERE region_uz = 'Buxoro viloyati' AND year = 2025 AND NOT is_cancelled GROUP BY seller_inn, seller_name ORDER BY COUNT(*) DESC LIMIT 1")
    questions.append({
        "id": "Q28",
        "lang": "uz",
        "category": "top_n",
        "question": "2025-yilda Buxoro viloyatida eng ko‘p bitim tuzgan sotuvchi kompaniya (STIR va nomi) qaysi va nechta bitim tuzgan?",
        "sql": "SELECT seller_inn, seller_name, COUNT(*) AS deals_count FROM deals_full WHERE region_uz = 'Buxoro viloyati' AND year = 2025 AND NOT is_cancelled GROUP BY seller_inn, seller_name ORDER BY deals_count DESC LIMIT 1",
        "not_found": False,
        "expected_numbers": [float(r[2])],
        "expected_substrings": [str(r[0])],
    })

    # Q29: Ceramic brick (кирпич керамический) volume in units (штука) in 2024 in Uzbek
    r = q1("SELECT COUNT(*), SUM(volume_units) FROM deals_full WHERE product_base = 'кирпич керамический' AND year = 2024 AND NOT is_cancelled")
    questions.append({
        "id": "Q29",
        "lang": "uz",
        "category": "lot_volume",
        "question": "2024-yilda keramik g‘isht (кирпич керамический) bo‘yicha nechta bitim tuzilgan va jami necha dona (volume_units) sotilgan?",
        "sql": "SELECT COUNT(*) AS deals_count, SUM(volume_units) AS total_pieces FROM deals_full WHERE product_base = 'кирпич керамический' AND year = 2024 AND NOT is_cancelled",
        "not_found": False,
        "expected_numbers": [float(r[0]), float(r[1])],
        "expected_substrings": [],
    })

    # Q30: All export deals in 2024 in Uzbek
    r = q1("SELECT COUNT(*), ROUND(SUM(amount_usd), 2) FROM deals_full WHERE contract_type = 'Экспортный' AND year = 2024 AND NOT is_cancelled")
    questions.append({
        "id": "Q30",
        "lang": "uz",
        "category": "export_currency",
        "question": "2024-yilda jami nechta eksport bitimi tuzilgan va ularning USD dagi umumiy summasi qancha?",
        "sql": "SELECT COUNT(*) AS deals_count, ROUND(SUM(amount_usd), 2) AS total_usd FROM deals_full WHERE contract_type = 'Экспортный' AND year = 2024 AND NOT is_cancelled",
        "not_found": False,
        "expected_numbers": [float(r[0]), float(r[1])],
        "expected_substrings": [],
    })

    # ------------------------------------------------------------------
    # PART C: 10 Trap / Not-in-Data Questions (Q31 .. Q40, 5 RU + 5 UZ)
    # ------------------------------------------------------------------
    questions.extend([
        {
            "id": "Q31",
            "lang": "ru",
            "category": "trap_date",
            "question": "Какой объём сделок по хлопку-сырцу был в 2021 году?",
            "sql": "",
            "not_found": True,
            "expected_numbers": [],
            "expected_substrings": ["нет информации"],
        },
        {
            "id": "Q32",
            "lang": "ru",
            "category": "trap_product",
            "question": "Сколько тонн золота и серебра было продано на бирже в 2025 году?",
            "sql": "",
            "not_found": True,
            "expected_numbers": [],
            "expected_substrings": ["нет информации"],
        },
        {
            "id": "Q33",
            "lang": "ru",
            "category": "trap_company",
            "question": "Сколько сделок заключила компания ООО «Газпром Инвест Ташкент» с ИНН 111222333?",
            "sql": "",
            "not_found": True,
            "expected_numbers": [],
            "expected_substrings": ["нет информации"],
        },
        {
            "id": "Q34",
            "lang": "ru",
            "category": "trap_deal_id",
            "question": "Сделка №999999",
            "sql": "",
            "not_found": True,
            "expected_numbers": [],
            "expected_substrings": ["нет информации"],
        },
        {
            "id": "Q35",
            "lang": "ru",
            "category": "trap_future_date",
            "question": "Какой курс доллара был 15.12.2026?",
            "sql": "",
            "not_found": True,
            "expected_numbers": [],
            "expected_substrings": ["нет информации"],
        },
        {
            "id": "Q36",
            "lang": "uz",
            "category": "trap_date",
            "question": "2020-yilda birjada jami nechta bitim tuzilgan?",
            "sql": "",
            "not_found": True,
            "expected_numbers": [],
            "expected_substrings": ["javob yo‘q"],
        },
        {
            "id": "Q37",
            "lang": "uz",
            "category": "trap_product",
            "question": "2025-yilda ko‘mir va oltin bo‘yicha qancha bitim tuzilgan?",
            "sql": "",
            "not_found": True,
            "expected_numbers": [],
            "expected_substrings": ["javob yo‘q"],
        },
        {
            "id": "Q38",
            "lang": "uz",
            "category": "trap_broker",
            "question": "BK999 kodli broker 2024-yilda nechta bitim o‘tkazgan?",
            "sql": "",
            "not_found": True,
            "expected_numbers": [],
            "expected_substrings": ["javob yo‘q"],
        },
        {
            "id": "Q39",
            "lang": "uz",
            "category": "trap_deal_id",
            "question": "Bitim №888888",
            "sql": "",
            "not_found": True,
            "expected_numbers": [],
            "expected_substrings": ["javob yo‘q"],
        },
        {
            "id": "Q40",
            "lang": "uz",
            "category": "trap_out_of_scope",
            "question": "2026-yil noyabr oyida nechta bitim tuzilgan?",
            "sql": "",
            "not_found": True,
            "expected_numbers": [],
            "expected_substrings": ["javob yo‘q"],
        },
    ])

    con.close()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump({"questions": questions}, f, allow_unicode=True, sort_keys=False)
    print(f"Generated {len(questions)} golden questions at {OUT_PATH}")


if __name__ == "__main__":
    generate_questions()
