# UZEX AI Hackathon 2026 — AI Data Analyst Agent (`@uzex_selection_agent_bot`)

**Telegram Bot:** [@uzex_selection_agent_bot](https://t.me/uzex_selection_agent_bot)  
**Live Web UI & Analytics Console:** [https://uzex.shermatov.uz/](https://uzex.shermatov.uz/)  
**Detailed Architecture & Evaluation Report:** [SOLUTION.md](SOLUTION.md)

ИИ-агент аналитики биржевых сделок **Узбекской республиканской товарно-сырьевой биржи (UZEX)** для отборочного этапа **UZEX AI Hackathon 2026**. Отвечает на вопросы на **русском** и **узбекском** языках по реестру из **500 000 биржевых сделок** (`03.01.2022 – 30.10.2026`), демонстрируя ход расчёта, применённые фильтры, примеры номеров сделок и выполненный SQL-запрос.

---

## Ключевые особенности (Zero-Hallucination Design)

1. **Никакой арифметики внутри LLM (Text-to-SQL over DuckDB):**
   - Все 500 000 сделок, 6 616 контрактов, 14 392 участника, 120 брокеров и 1 765 курсов USD загружаются и очищаются в аналитическую СУБД **DuckDB** (`data/uzex.duckdb`) с денормализованным представлением `deals_full`.
   - Предварительно рассчитаны точные поля: физический объём `volume_units = quantity * lot`, цена за единицу `unit_price`, суммы в единой валюте `amount_uzs` и `amount_usd` по курсу на дату сделки, флаг аннулирования `is_cancelled`, а также фактические сроки оплаты и поставки (`actual_payment_days`, `actual_delivery_days`).
2. **Трёхуровневая нормализация товаров и двуязычный поиск (`RapidFuzz` + Транслитерация):**
   - 230 сырых названий товаров сведены к 135 каноническим (`product_canonical`) и 42 базовым товарным группам (`product_base`) с проверкой по 6-значным кодам ТН ВЭД (`hs_code`) и словарём узбекских синонимов (*paxta yog‘i*, *suyultirilgan gaz*, *keramik g‘isht*, *po‘lat armatura* и др.).
   - 14 регионов сопоставлены между узбекским (`Farg‘ona viloyati`, `Toshkent shahri`) и русским (`Ферганская область`, `г. Ташкент`) языками с нормализацией всех видов апострофов (`‘`, `’`, `'`, `` ` ``).
3. **Многоуровневая защита от галлюцинаций (`agent/verifier.py`):**
   - **Read-Only SQL Guard:** разрешены только одиночные `SELECT`/`WITH` запросы с таймаутом 10 с и лимитом строк.
   - **Empty-Result Gate:** если SQL вернул `0` строк или запрошены данные вне диапазона `03.01.2022 – 30.10.2026`, агент возвращает строгий шаблон отказа без генерации текста моделью.
   - **Numeric Verifier:** каждое число в тексте ответа (`answer_text`) сверяется с ячейками результата SQL-запроса. При любом расхождении ответ отклоняется и перестраивается строго по строкам SQL.
   - **Исключение аннулированных сделок по умолчанию:** сделки со статусом `Аннулирован(Не оплачен)` (`is_cancelled = TRUE`) автоматически исключаются из общих сумм и объёмов (если пользователь явно не спрашивает о них), что явно указывается в ответе.
4. **Отказоустойчивый мульти-провайдерный LLM Router (`agent/llm_router.py`):**
   - Приоритетная цепочка из 7 моделей **Google Gemini** и **Groq** с автоматическим переключением при `429 Rate Limit` / `5xx` / таймаутах, индивидуальными cooldown-таймерами и общим бюджетом времени.
5. **Два интерфейса (Telegram Bot + Web UI):**
   - **Telegram-бот (`aiogram 3`):** мгновенный отклик, индикатор загрузки, блок доказательной базы (`Основание / Asos`) и сворачиваемый блок `<blockquote expandable>` с SQL-запросом.
   - **Веб-интерфейс (`aiohttp`):** интерактивная консоль аналитика в фирменном стиле `hackathon.uzex.uz/saralash/` с таблицей данных DuckDB, просмотром SQL и переключателем `RU / UZ`.

---

## Результаты тестирования (`tests/run_eval.py`)

На золотом бенчмарке из **40 двуязычных вопросов** (`20 RU`, `20 UZ`, включая `10` вопросов-ловушек на несуществующие товары, компании и даты):

- **Точность (Accuracy):** `40 / 40 (100.0%)`
- **Галлюцинации (Hallucinations):** `0 (0.0%)`
- **Отработка вопросов-ловушек (Out-of-Scope Refusal):** `10 / 10 (100.0%)`
- **Медианное время ответа (p50):** `~4.5 с` (Fast-path запросы по номеру сделки или курсу валют: `< 0.03 с`)

---

## Структура проекта

```text
├── agent/
│   ├── llm_router.py      # Отказоустойчивый роутер Gemini + Groq с cooldown и квотами
│   ├── orchestrator.py    # Агентский цикл (≤6 шагов), кэш SQLite, быстрые пути (fast-paths)
│   ├── prompts.py         # Системный промпт со схемой БД, правилами и примерами SQL
│   ├── tools.py           # search_entities (RapidFuzz) и run_sql (read-only DuckDB guard)
│   ├── verifier.py        # Числовой верификатор (проверка каждого числа ответа по SQL)
│   └── formatter.py       # Форматирование чисел, определение языка RU/UZ, сборка ответа
├── bot/
│   └── main.py            # Telegram-бот (aiogram 3) + запуск веб-сервера
├── web/
│   ├── app.py             # REST API (/api/ask, /api/status) и веб-сервер (aiohttp)
│   └── index.html         # Веб-интерфейс в дизайне UZEX AI Hackathon 2026
├── etl/
│   ├── build_db.py        # Сборка и валидация data/uzex.duckdb из сырых CSV
│   └── normalize.py       # Нормализация товаров, регионов, компаний и транслитерация
├── tests/
│   ├── questions.yaml     # 40 проверочных вопросов (RU/UZ + вопросы-ловушки)
│   ├── run_eval.py        # Скрипт автоматической проверки точности и галлюцинаций
│   └── test_router_chaos.py # Стресс-тест LLM роутера (429 / 5xx / таймауты)
├── deploy/
│   ├── setup_hostinger.sh # Скрипт развёртывания на Ubuntu VPS
│   └── uzex-bot.service   # Конфигурация systemd-сервиса (Restart=always)
├── config.yaml            # Цепочка моделей и лимиты роутера
└── SOLUTION.md            # Подробное описание архитектуры решения
```

---

## Быстрый запуск

```bash
# 1. Установка зависимостей
python -m venv .venv
source .venv/bin/activate  # Windows: .\.venv\Scripts\activate
pip install -r requirements.txt

# 2. Настройка ключей (.env)
cp .env.example .env
# Заполните GEMINI_API_KEY, GROQ_API_KEY и TELEGRAM_BOT_TOKEN в .env

# 3. Сборка базы данных DuckDB из CSV (data/raw/*.csv -> data/uzex.duckdb)
python -X utf8 -m etl.build_db

# 4. Запуск Telegram-бота и веб-интерфейса (порт 8080)
python -X utf8 -m bot.main
```

