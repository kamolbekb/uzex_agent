"""Build cleaned DuckDB database from data/raw/*.csv with precomputed columns and validation."""

from __future__ import annotations

import csv
import sys
import time
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from etl.normalize import (  # noqa: E402
    REGIONS_DATA,
    RU_TO_UZ_REGION,
    UZ_TO_RU_REGION,
    normalize_class,
    normalize_company_name,
    normalize_product_canonical,
    normalize_sort,
    resolve_product_base,
)

RAW_DIR = PROJECT_ROOT / "data" / "raw"
DB_PATH = PROJECT_ROOT / "data" / "uzex.duckdb"


def build_db() -> None:
    t0 = time.perf_counter()
    if DB_PATH.exists():
        DB_PATH.unlink()
    wal_path = DB_PATH.with_suffix(".duckdb.wal")
    if wal_path.exists():
        wal_path.unlink()

    con = duckdb.connect(str(DB_PATH))
    try:
        print(f"Building DuckDB at {DB_PATH} ...")

        # ------------------------------------------------------------------
        # 1. regions (14 rows)
        # ------------------------------------------------------------------
        con.execute(
            """
            CREATE TABLE regions (
                region_id VARCHAR PRIMARY KEY,
                region_uz VARCHAR NOT NULL,
                region_ru VARCHAR NOT NULL,
                aliases VARCHAR NOT NULL
            )
            """
        )
        con.executemany(
            "INSERT INTO regions VALUES (?, ?, ?, ?)",
            [(r.region_id, r.region_uz, r.region_ru, r.aliases) for r in REGIONS_DATA],
        )
        print(f"  [1/6] regions loaded: {len(REGIONS_DATA)} rows")

        # ------------------------------------------------------------------
        # 2. usd_rates (1,765 rows)
        # ------------------------------------------------------------------
        usd_csv = str((RAW_DIR / "usd_rates.csv").as_posix())
        con.execute(
            f"""
            CREATE TABLE usd_rates AS
            SELECT
                strptime("Дата", '%d.%m.%Y')::DATE AS rate_date,
                CAST(REPLACE("Курс", ' ', '') AS DOUBLE) AS usd_rate
            FROM read_csv('{usd_csv}', header=true, all_varchar=true)
            ORDER BY rate_date
            """
        )
        usd_cnt = con.execute("SELECT COUNT(*) FROM usd_rates").fetchone()[0]
        print(f"  [2/6] usd_rates loaded: {usd_cnt:,} rows")

        # ------------------------------------------------------------------
        # 3. participants (14,392 rows)
        # ------------------------------------------------------------------
        part_rows = []
        with open(RAW_DIR / "participants.csv", "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                inn = row["ИНН"].strip()
                name = row["Наименование"].strip()
                region_uz = row["Регион"].strip()
                region_ru = UZ_TO_RU_REGION.get(region_uz, region_uz)
                activity_type = row["Вид деятельности"].strip()
                role = row["Роль"].strip()
                vat_payer_raw = row["Плательщик НДС"].strip()
                is_vat_payer = vat_payer_raw == "Да"
                name_norm, name_search = normalize_company_name(name)
                part_rows.append(
                    (
                        inn,
                        name,
                        name_norm,
                        name_search,
                        region_uz,
                        region_ru,
                        activity_type,
                        role,
                        vat_payer_raw,
                        is_vat_payer,
                    )
                )

        con.execute(
            """
            CREATE TABLE participants (
                inn VARCHAR PRIMARY KEY,
                name VARCHAR NOT NULL,
                name_norm VARCHAR NOT NULL,
                name_search VARCHAR NOT NULL,
                region_uz VARCHAR NOT NULL,
                region_ru VARCHAR NOT NULL,
                activity_type VARCHAR NOT NULL,
                role VARCHAR NOT NULL,
                vat_payer VARCHAR NOT NULL,
                is_vat_payer BOOLEAN NOT NULL
            )
            """
        )
        con.executemany(
            "INSERT INTO participants VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            part_rows,
        )
        print(f"  [3/6] participants loaded: {len(part_rows):,} rows")

        # ------------------------------------------------------------------
        # 4. brokers (120 rows)
        # ------------------------------------------------------------------
        broker_rows = []
        with open(RAW_DIR / "brokers.csv", "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                code = row["Код брокера"].strip()
                name = row["Наименование"].strip()
                inn = row["ИНН"].strip()
                region_uz = row["Регион"].strip()
                region_ru = UZ_TO_RU_REGION.get(region_uz, region_uz)
                name_norm, name_search = normalize_company_name(name)
                broker_rows.append(
                    (code, name, name_norm, name_search, inn, region_uz, region_ru)
                )

        con.execute(
            """
            CREATE TABLE brokers (
                broker_code VARCHAR PRIMARY KEY,
                name VARCHAR NOT NULL,
                name_norm VARCHAR NOT NULL,
                name_search VARCHAR NOT NULL,
                inn VARCHAR NOT NULL,
                region_uz VARCHAR NOT NULL,
                region_ru VARCHAR NOT NULL
            )
            """
        )
        con.executemany(
            "INSERT INTO brokers VALUES (?, ?, ?, ?, ?, ?, ?)",
            broker_rows,
        )
        print(f"  [4/6] brokers loaded: {len(broker_rows):,} rows")

        # ------------------------------------------------------------------
        # 5. contracts (6,616 rows)
        # ------------------------------------------------------------------
        contract_rows = []
        with open(RAW_DIR / "contracts.csv", "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                cid = int(row["Номер контракта"].strip())
                pname = row["Наименование продукта"].strip()
                hs_code = row["Код ТН ВЭД"].strip()
                p_canon = normalize_product_canonical(pname)
                p_base = resolve_product_base(pname, hs_code)

                brand = row["Марка товара"].strip()
                size = row["Размер товара"].strip()
                raw_sort = row["Сорт товара"].strip()
                sort_norm = normalize_sort(raw_sort, pname)
                ptype = row["Тип товара"].strip()
                raw_class = row["Класс товара"].strip()
                class_norm = normalize_class(raw_class, pname)

                pgroup = row["Группа товара"].strip()
                pcat = row["Категория товара"].strip()
                industry = row["Отрасль промышленности"].strip()
                unit = row["Единица измерения"].strip()
                lot = int(row["Лот"].strip())
                currency_name = row["Валюта"].strip()
                currency_code = "USD" if currency_name == "Доллар США" else "UZS"
                base_price = float(row["Базисная цена"].replace(" ", "").strip())
                deposit_raw = row["Сумма залога"].replace("%", "").replace(" ", "").strip()
                deposit_pct = float(deposit_raw) if deposit_raw else 0.0
                delivery_basis = row["Базис поставки"].strip()
                region_ru = row["Регион"].strip()
                region_uz = RU_TO_UZ_REGION.get(region_ru, region_ru)
                warehouse_address = row["Адрес склада"].strip()
                auction_type = row["Тип торгов"].strip()
                contract_type = row["Тип контракта"].strip()
                contract_form = row["Форма контракта"].strip()
                payment_term = int(row["Срок оплаты"].strip())
                delivery_term = int(row["Срок поставки"].strip())
                packaging = row["Упаковка"].strip()
                seller_inn = row["ИНН продавца"].strip()
                reg_date = row["Дата регистрации"].strip()
                valid_until = row["Действует до"].strip()

                contract_rows.append(
                    (
                        cid,
                        pname,
                        p_canon,
                        p_base,
                        brand,
                        size,
                        raw_sort,
                        sort_norm,
                        ptype,
                        raw_class,
                        class_norm,
                        hs_code,
                        pgroup,
                        pcat,
                        industry,
                        unit,
                        lot,
                        currency_name,
                        currency_code,
                        base_price,
                        deposit_pct,
                        delivery_basis,
                        region_uz,
                        region_ru,
                        warehouse_address,
                        auction_type,
                        contract_type,
                        contract_form,
                        payment_term,
                        delivery_term,
                        packaging,
                        seller_inn,
                        reg_date,
                        valid_until,
                    )
                )

        con.execute(
            """
            CREATE TEMP TABLE contracts_staging (
                contract_id BIGINT,
                product_name VARCHAR,
                product_canonical VARCHAR,
                product_base VARCHAR,
                brand VARCHAR,
                size VARCHAR,
                product_sort_raw VARCHAR,
                product_sort VARCHAR,
                product_type VARCHAR,
                product_class_raw VARCHAR,
                product_class VARCHAR,
                hs_code VARCHAR,
                product_group VARCHAR,
                category VARCHAR,
                industry VARCHAR,
                unit VARCHAR,
                lot BIGINT,
                currency_name VARCHAR,
                currency VARCHAR,
                base_price DOUBLE,
                deposit_pct DOUBLE,
                delivery_basis VARCHAR,
                region_uz VARCHAR,
                region_ru VARCHAR,
                warehouse_address VARCHAR,
                auction_type VARCHAR,
                contract_type VARCHAR,
                contract_form VARCHAR,
                payment_term BIGINT,
                delivery_term BIGINT,
                packaging VARCHAR,
                seller_inn VARCHAR,
                reg_date_str VARCHAR,
                valid_until_str VARCHAR
            )
            """
        )
        con.executemany(
            f"INSERT INTO contracts_staging VALUES ({','.join(['?'] * 34)})",
            contract_rows,
        )
        con.execute(
            """
            CREATE TABLE contracts AS
            SELECT
                contract_id,
                product_name,
                product_canonical,
                product_base,
                brand,
                size,
                product_sort_raw,
                product_sort,
                product_type,
                product_class_raw,
                product_class,
                hs_code,
                product_group,
                category,
                industry,
                unit,
                lot,
                currency_name,
                currency,
                base_price,
                deposit_pct,
                delivery_basis,
                region_uz,
                region_ru,
                warehouse_address,
                auction_type,
                contract_type,
                contract_form,
                payment_term,
                delivery_term,
                packaging,
                seller_inn,
                strptime(reg_date_str, '%d.%m.%Y')::DATE AS reg_date,
                strptime(valid_until_str, '%d.%m.%Y')::DATE AS valid_until
            FROM contracts_staging
            ORDER BY contract_id
            """
        )
        con.execute("DROP TABLE contracts_staging")
        print(f"  [5/6] contracts loaded: {len(contract_rows):,} rows")

        # ------------------------------------------------------------------
        # 6. deals (500,000 rows from deals_2022.csv .. deals_2026.csv)
        # ------------------------------------------------------------------
        deals_glob = str((RAW_DIR / "deals_*.csv").as_posix())
        con.execute(
            f"""
            CREATE TABLE deals AS
            WITH raw_deals AS (
                SELECT
                    CAST("Номер сделки" AS BIGINT) AS deal_id,
                    strptime("Дата сделки", '%d.%m.%Y')::DATE AS deal_date,
                    "Время сделки" AS deal_time,
                    strptime("Дата сделки" || ' ' || "Время сделки", '%d.%m.%Y %H:%M:%S') AS deal_ts,
                    "Тип торгов" AS auction_type,
                    CAST("Количество" AS BIGINT) AS quantity,
                    CAST(REPLACE("Сумма сделки", ' ', '') AS DOUBLE) AS amount,
                    CAST(REPLACE("Стартовая цена", ' ', '') AS DOUBLE) AS start_price,
                    CAST("Номер контракта" AS BIGINT) AS contract_id,
                    "Наименование товара" AS deal_product_title,
                    CAST("Лот" AS BIGINT) AS lot,
                    "Единица измерения" AS unit,
                    "Регион" AS region_uz,
                    "Тип контракта" AS contract_type,
                    "Форма контракта" AS contract_form,
                    "Состояние сделки" AS status,
                    "ИНН продавца" AS seller_inn,
                    "ИНН покупателя" AS buyer_inn,
                    "Брокер продавца" AS seller_broker_code,
                    "Брокер покупателя" AS buyer_broker_code,
                    CASE WHEN "Дата оплаты" IS NOT NULL AND TRIM("Дата оплаты") <> ''
                         THEN strptime("Дата оплаты", '%d.%m.%Y')::DATE
                         ELSE NULL END AS payment_date,
                    CASE WHEN "Дата поставки" IS NOT NULL AND TRIM("Дата поставки") <> ''
                         THEN strptime("Дата поставки", '%d.%m.%Y')::DATE
                         ELSE NULL END AS delivery_date,
                    CASE WHEN "Поставлено" IS NOT NULL AND TRIM("Поставлено") <> ''
                         THEN CAST("Поставлено" AS BIGINT)
                         ELSE NULL END AS delivered_lots
                FROM read_csv('{deals_glob}', header=true, all_varchar=true)
            )
            SELECT
                d.deal_id,
                d.deal_date,
                d.deal_time,
                d.deal_ts,
                EXTRACT(YEAR FROM d.deal_date)::INT AS year,
                EXTRACT(MONTH FROM d.deal_date)::INT AS month,
                d.auction_type,
                d.quantity,
                d.lot,
                (d.quantity * d.lot) AS volume_units,
                d.unit,
                d.amount,
                d.start_price,
                (d.amount / (d.quantity * d.lot)) AS unit_price,
                c.currency,
                c.currency_name,
                u.usd_rate,
                CASE WHEN c.currency = 'UZS' THEN d.amount ELSE d.amount * u.usd_rate END AS amount_uzs,
                CASE WHEN c.currency = 'USD' THEN d.amount ELSE d.amount / u.usd_rate END AS amount_usd,
                CASE WHEN c.currency = 'UZS'
                     THEN (d.amount / (d.quantity * d.lot))
                     ELSE (d.amount * u.usd_rate) / (d.quantity * d.lot)
                END AS unit_price_uzs,
                CASE WHEN c.currency = 'USD'
                     THEN (d.amount / (d.quantity * d.lot))
                     ELSE (d.amount / u.usd_rate) / (d.quantity * d.lot)
                END AS unit_price_usd,
                d.contract_id,
                d.deal_product_title,
                c.product_name,
                c.product_canonical,
                c.product_base,
                c.brand,
                c.size,
                c.product_sort,
                c.product_type,
                c.product_class,
                c.hs_code,
                c.product_group,
                c.category,
                c.industry,
                c.base_price,
                c.deposit_pct,
                c.delivery_basis,
                c.packaging,
                c.payment_term,
                c.delivery_term,
                d.region_uz,
                r.region_ru,
                d.contract_type,
                d.contract_form,
                d.status,
                (d.status = 'Аннулирован(Не оплачен)') AS is_cancelled,
                d.seller_inn,
                d.buyer_inn,
                d.seller_broker_code,
                d.buyer_broker_code,
                d.payment_date,
                d.delivery_date,
                d.delivered_lots,
                (d.delivered_lots * d.lot) AS delivered_units,
                CASE WHEN d.payment_date IS NOT NULL
                     THEN (d.payment_date - d.deal_date)
                     ELSE NULL END AS actual_payment_days,
                CASE WHEN d.delivery_date IS NOT NULL AND d.payment_date IS NOT NULL
                     THEN (d.delivery_date - d.payment_date)
                     ELSE NULL END AS actual_delivery_days
            FROM raw_deals d
            JOIN contracts c ON d.contract_id = c.contract_id
            JOIN usd_rates u ON d.deal_date = u.rate_date
            LEFT JOIN regions r ON d.region_uz = r.region_uz
            ORDER BY d.deal_id
            """
        )
        deals_cnt = con.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
        print(f"  [6/6] deals loaded: {deals_cnt:,} rows")

        # ------------------------------------------------------------------
        # 7. Create wide view `deals_full`
        # ------------------------------------------------------------------
        con.execute(
            """
            CREATE VIEW deals_full AS
            SELECT
                d.*,
                sp.name AS seller_name,
                sp.region_uz AS seller_region_uz,
                sp.region_ru AS seller_region_ru,
                sp.activity_type AS seller_activity_type,
                sp.is_vat_payer AS seller_is_vat_payer,
                bp.name AS buyer_name,
                bp.region_uz AS buyer_region_uz,
                bp.region_ru AS buyer_region_ru,
                bp.activity_type AS buyer_activity_type,
                bp.is_vat_payer AS buyer_is_vat_payer,
                sb.name AS seller_broker_name,
                sb.region_uz AS seller_broker_region_uz,
                sb.region_ru AS seller_broker_region_ru,
                bb.name AS buyer_broker_name,
                bb.region_uz AS buyer_broker_region_uz,
                bb.region_ru AS buyer_broker_region_ru
            FROM deals d
            LEFT JOIN participants sp ON d.seller_inn = sp.inn
            LEFT JOIN participants bp ON d.buyer_inn = bp.inn
            LEFT JOIN brokers sb ON d.seller_broker_code = sb.broker_code
            LEFT JOIN brokers bb ON d.buyer_broker_code = bb.broker_code
            """
        )

        # ------------------------------------------------------------------
        # 8. Validation Asserts
        # ------------------------------------------------------------------
        validate_db(con)

        dt = time.perf_counter() - t0
        size_mb = DB_PATH.stat().st_size / (1024 * 1024)
        print(f"\nSUCCESS: Built and validated {DB_PATH.name} ({size_mb:.1f} MB) in {dt:.2f}s")
    finally:
        con.close()


def validate_db(con: duckdb.DuckDBPyConnection) -> None:
    print("\n=== Running Validation Checks ===")

    # 1. Row counts per year
    expected_years = {
        2022: 84_299,
        2023: 93_985,
        2024: 106_647,
        2025: 115_763,
        2026: 99_306,
    }
    year_rows = dict(con.execute("SELECT year, COUNT(*) FROM deals GROUP BY year ORDER BY year").fetchall())
    assert year_rows == expected_years, f"Year counts mismatch: {year_rows} != {expected_years}"
    print(f"  [OK] Year row counts match: {year_rows} (total {sum(year_rows.values()):,})")

    # 2. Table counts
    c_cnt = con.execute("SELECT COUNT(*) FROM contracts").fetchone()[0]
    p_cnt = con.execute("SELECT COUNT(*) FROM participants").fetchone()[0]
    b_cnt = con.execute("SELECT COUNT(*) FROM brokers").fetchone()[0]
    u_cnt = con.execute("SELECT COUNT(*) FROM usd_rates").fetchone()[0]
    r_cnt = con.execute("SELECT COUNT(*) FROM regions").fetchone()[0]
    assert c_cnt == 6_616, f"Expected 6,616 contracts, got {c_cnt}"
    assert p_cnt == 14_392, f"Expected 14,392 participants, got {p_cnt}"
    assert b_cnt == 120, f"Expected 120 brokers, got {b_cnt}"
    assert u_cnt == 1_765, f"Expected 1,765 usd_rates, got {u_cnt}"
    assert r_cnt == 14, f"Expected 14 regions, got {r_cnt}"
    print(f"  [OK] Reference table counts: contracts={c_cnt}, participants={p_cnt}, brokers={b_cnt}, usd_rates={u_cnt}, regions={r_cnt}")

    # 3. No NULL amounts, prices, or unresolved FKs in deals_full
    null_checks = con.execute(
        """
        SELECT
            SUM(CASE WHEN amount IS NULL OR amount <= 0 THEN 1 ELSE 0 END) AS bad_amount,
            SUM(CASE WHEN amount_uzs IS NULL OR amount_uzs <= 0 THEN 1 ELSE 0 END) AS bad_uzs,
            SUM(CASE WHEN amount_usd IS NULL OR amount_usd <= 0 THEN 1 ELSE 0 END) AS bad_usd,
            SUM(CASE WHEN volume_units IS NULL OR volume_units <= 0 THEN 1 ELSE 0 END) AS bad_vol,
            SUM(CASE WHEN unit_price IS NULL OR unit_price <= 0 THEN 1 ELSE 0 END) AS bad_uprice,
            SUM(CASE WHEN seller_name IS NULL THEN 1 ELSE 0 END) AS missing_seller,
            SUM(CASE WHEN buyer_name IS NULL THEN 1 ELSE 0 END) AS missing_buyer,
            SUM(CASE WHEN seller_broker_name IS NULL THEN 1 ELSE 0 END) AS missing_sbroker,
            SUM(CASE WHEN buyer_broker_name IS NULL THEN 1 ELSE 0 END) AS missing_bbroker,
            SUM(CASE WHEN region_ru IS NULL THEN 1 ELSE 0 END) AS missing_region
        FROM deals_full
        """
    ).fetchone()
    assert all(v == 0 for v in null_checks), f"Null/FK check failed: {null_checks}"
    print("  [OK] Zero NULL/invalid amounts, volumes, unit prices, and 100% FK resolution (sellers, buyers, brokers, regions)")

    # 4. Date range check
    min_d, max_d = con.execute("SELECT MIN(deal_date), MAX(deal_date) FROM deals").fetchone()
    assert str(min_d) == "2022-01-03" and str(max_d) == "2026-10-30", f"Unexpected date range: {min_d} .. {max_d}"
    print(f"  [OK] Deal date range: {min_d} to {max_d}")

    # 5. Spot check deal 100001
    d1 = con.execute(
        "SELECT deal_id, deal_date, quantity, lot, volume_units, amount, start_price, product_base, status FROM deals_full WHERE deal_id = 100001"
    ).fetchone()
    assert d1 is not None, "Deal 100001 not found"
    assert abs(d1[5] - 304547938.4) < 1e-3, f"Deal 100001 amount mismatch: {d1[5]}"
    assert d1[4] == 79720, f"Deal 100001 volume mismatch: {d1[4]}"
    print(f"  [OK] Spot check deal 100001: {d1}")


if __name__ == "__main__":
    build_db()

