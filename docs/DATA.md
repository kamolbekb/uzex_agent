# Dataset analysis (data/raw/)

Original README (RU + UZ, authoritative): `DATASET_README_original.md`. This file is our analysis on top of it.

All files: UTF-8 CSV, comma separator, header row, quoted values that contain commas (company names have escaped `""` quotes).
Dates are `DD.MM.YYYY`, times are Tashkent local. Deal statuses are the snapshot at the end of **30.10.2026**.
**Windows note:** run Python with `-X utf8` (or set `PYTHONUTF8=1`), otherwise printing Cyrillic fails with cp1252 errors.

## Files

| File | Rows | Notes |
|---|---|---|
| deals_2022.csv … deals_2026.csv | 500,000 (84,299 / 93,985 / 106,647 / 115,763 / 99,306) | 03.01.2022 – 30.10.2026 |
| contracts.csv | 6,616 | contract cards |
| participants.csv | 14,392 | sellers and buyers |
| brokers.csv | 120 | codes BK001…BK120 |
| usd_rates.csv | 1,765 | daily, 01.01.2022 → 30.10.2026, UZS per 1 USD |

## Columns

**deals_*.csv**: `Номер сделки, Дата сделки, Время сделки, Тип торгов, Количество, Сумма сделки, Стартовая цена, Номер контракта, Наименование товара, Лот, Единица измерения, Регион, Тип контракта, Форма контракта, Состояние сделки, ИНН продавца, ИНН покупателя, Брокер продавца, Брокер покупателя, Дата оплаты, Дата поставки, Поставлено`

Sample: `100001,03.01.2022,10:04:49,Английский аукцион,79720,304 547 938.4,3 731.07,100034,Шрот хлопковых семян Сорт первый сорт Тип тостированный,1,килограмм,Namangan viloyati,Внутренний,Спот,Исполнен,921912462,978906167,BK060,BK001,05.01.2022,17.01.2022,79720`

**contracts.csv**: `Номер контракта, Наименование продукта, Марка товара, Размер товара, Сорт товара, Тип товара, Класс товара, Код ТН ВЭД, Группа товара, Категория товара, Отрасль промышленности, Единица измерения, Лот, Валюта, Базисная цена, Сумма залога, Базис поставки, Регион, Адрес склада, Тип торгов, Тип контракта, Форма контракта, Срок оплаты, Срок поставки, Упаковка, ИНН продавца, Дата регистрации, Действует до`

**participants.csv**: `ИНН, Наименование, Регион, Вид деятельности, Роль, Плательщик НДС`

**brokers.csv**: `Код брокера, Наименование, ИНН, Регион`

**usd_rates.csv**: `Дата, Курс`

## Enum values (verified)
- Тип торгов: Английский аукцион, Двусторонний аукцион, Обратный аукцион
- Тип контракта: Внутренний, Экспортный
- Форма контракта: Спот, Форвард, Фьючерс
- Валюта (contracts): Узбекский сум, Доллар США
- Состояние сделки: Новый, Не подписан, Заключен, Ждёт оплату, Оплачен, Исполнен, Исполнен(Частично), Аннулирован(Не оплачен)
- Группа товара: Высоколиквидные товары, Прочие товары
- Категория товара (8): Металлопродукция, Нефтепродукты, Полимерная продукция, Продовольственные товары, Промышленные товары, Сельскохозяйственная продукция, Строительные материалы, Химическая продукция
- Отрасль промышленности (10): Лесная, Машиностроение, Металлургия, Нефтегазовая, Пищевая, Сельское хозяйство, Строительная индустрия, Текстильная, Химическая, Электротехническая (full strings end in "промышленность" where applicable)
- Единица измерения: кв.метр, килограмм, куб.метр, литр, метр, тонна, штука
- participants Вид деятельности: Кластер, Перерабатывающее предприятие, Производитель, Производственное предприятие, Строительная компания, Торговая компания, Транспортная компания
- participants Роль: Покупатель, Продавец. Плательщик НДС: Да, Нет

## Regions: two naming systems (needs a mapping table)
- deals / participants / brokers use **Uzbek Latin**: Andijon viloyati, Buxoro viloyati, Farg‘ona viloyati, Jizzax viloyati, Namangan viloyati, Navoiy viloyati, Qashqadaryo viloyati, Qoraqalpog‘iston Respublikasi, Samarqand viloyati, Sirdaryo viloyati, Surxondaryo viloyati, Toshkent shahri, Toshkent viloyati, Xorazm viloyati
- contracts use **Russian**: Андижанская область, Бухарская область, Ферганская область, Джизакская область, Наманганская область, Навоийская область, Кашкадарьинская область, Республика Каракалпакстан, Самаркандская область, Сырдарьинская область, Сурхандарьинская область, г. Ташкент, Ташкентская область, Хорезмская область
- Note the apostrophe character `‘` (U+2018) in Farg‘ona and Qoraqalpog‘iston. Users may type `'`, `ʻ`, or `` ` ``, so normalize all of them.

## Data quirks (must be handled in ETL)
1. `Сумма сделки` and `Стартовая цена` use **spaces as thousands separators** and `.` for decimals (`304 547 938.4`). Strip spaces and cast to DOUBLE.
2. `Сумма залога` looks like `10 %`.
3. Dates are `DD.MM.YYYY`. Convert to DATE. `Дата оплаты` / `Дата поставки` / `Поставлено` are empty if that hasn't happened yet.
4. Company/broker names mix **Uzbek Cyrillic** (`"ҲАРАЙЎМ КИМЁ" МЧЖ`) and **Latin** (`"Pudexiz Plast" MChJ`, `KAHIR BROKER MCHJ`). Search needs transliteration plus fuzzy matching, and must strip legal forms (МЧЖ / MChJ / MCHJ / ООО / АЖ / AJ ...).
5. **Product names are seller-written**: 230 distinct `Наименование продукта` values with case, hyphen, and sort/class spelling variants (`ХЛОПОК СЫРЕЦ (СПОТ) II СОРТ I КЛАСС` vs `Хлопок-сырец (спот) 2 сорт 1-класс` vs `Хлопок сырец (спот) второй сорт 1 класс`). Full list with counts: `products_raw.txt`. The deals column `Наименование товара` is an even messier one-line "name + attributes" string, so always classify via the contract join.
6. Export deals are in USD (contract `Валюта` = Доллар США). Never sum UZS and USD raw.

## Business formulas (from the README; judges will likely test these)
- Volume in units = `Количество × Лот`
- Unit price = `Сумма сделки ÷ (Количество × Лот)`
- UZS amount of a USD deal = `Сумма сделки × курс on Дата сделки` (usd_rates.csv)
- `Срок оплаты` = calendar days from the deal date to pay; `Срок поставки` = calendar days from the payment date to deliver
- Delivered units = `Поставлено × Лот`
- Contract is active between `Дата регистрации` and `Действует до`

## Status meanings
| Status | Meaning |
|---|---|
| Новый | concluded on the last export day, 30.10.2026 |
| Не подписан | contract not yet signed by the parties |
| Заключен | forward/futures signed, payment term not expired |
| Ждёт оплату | spot signed, payment term not expired |
| Оплачен | paid, not yet delivered |
| Исполнен | paid and fully delivered |
| Исполнен(Частично) | paid, partly delivered (see `Поставлено`) |
| Аннулирован(Не оплачен) | not paid in time and cancelled; **the deal did not take place** |
