# Progress checklist

Legend: [x] done · [/] in progress · [ ] todo. Phases follow PLAN.md section 9.

## Phase 0: Planning (done 2026-11-04)
- [x] Analyze the hackathon page → docs/HACKATHON.md
- [x] Analyze the dataset → docs/DATA.md, docs/products_raw.txt
- [x] Implementation plan → PLAN.md
- [x] Decisions agreed with the user → AGENTS.md
- [x] Copy dataset into data/raw/

## Phase 1: Scaffold
- [x] venv + `requirements.txt` install
- [x] User fills `.env` (GEMINI_API_KEY, GROQ_API_KEY; TELEGRAM_BOT_TOKEN later)
- [x] `scripts/check_models.py`: list models + ping each chain entry; finalize `config.yaml`

## Phase 2: ETL (etl/)
- [x] build_db.py: CSV → data/uzex.duckdb (clean numbers/dates, precomputed columns, deals_full view)
- [x] regions mapping table (UZ Latin ↔ RU ↔ aliases)
- [x] product normalization (product_canonical, product_base) + print the mapping for user review
- [x] company/broker name normalization + transliteration for search
- [x] validation asserts (row counts per year, FK integrity, spot checks)

## Phase 3: LLM router (agent/llm_router.py)
- [x] priority chain, error classification, cooldowns, time budget, logging, usage counters
- [x] chaos test (bad key / forced 429 / timeout)

## Phase 4: Agent
- [x] tools: search_entities, run_sql (read-only guard, LIMIT, timeout)
- [x] prompts: schema, business rules, few-shot RU/UZ examples
- [x] orchestrator loop (≤6 LLM calls), language detection, fast paths, cache

## Phase 5: Verifier + formatter
- [x] empty-result → fixed "no data" templates (RU/UZ)
- [x] numeric/name verifier + deterministic fallback answer
- [x] number formatting + evidence block

## Phase 6: Telegram bot & Web UI
- [x] BotFather steps for the user
- [x] aiogram handlers: /start (examples), /help, /status, free text with placeholder→edit
- [x] concurrency + per-user rate limits, global error handler
- [x] Web UI & HTTP API (`web/app.py` + `web/index.html`) styled after `hackathon.uzex.uz/saralash/` running alongside the bot on port 8080

## Phase 7: Evaluation
- [x] tests/questions.yaml: ~40 RU/UZ questions incl. ≥10 trap (not-in-data) questions, expected answers from hand-written SQL
- [x] tests/run_eval.py: accuracy, hallucinations (must be 0), latency p50/p95
- [x] prompt tuning loop

## Phase 8: Deploy + submit
- [/] Deploy to Hostinger (systemd service, DB built on server or uploaded)
- [x] SOLUTION.md (architecture, router, verification method, eval results)
- [ ] Submit form (early version ~14:00 Nov 5, final before 18:00)
- [ ] Smoke test on Nov 5 only (save quota)
