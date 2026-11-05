# UZEX AI Hackathon 2026: Telegram data agent (project brief for AI agents)

Read this first. It is the handoff from the planning session (2026-11-04) and contains everything needed to continue.

## What we are building
A **Telegram bot** that answers questions in **Russian and Uzbek** about 500k fictional commodity-exchange deals (`data/raw/`).
Answers must be **exact and grounded in the data**. **Any invented number, company, or product means disqualification.**
If the data doesn't contain the answer, the bot must say so explicitly.

**Hard deadline:** submit by **2026-11-05 18:00 Tashkent (UTC+5)** via https://forms.gle/5ALB3PrWdKf6TTCf8.
Judges then ask 10 unannounced RU/UZ questions, with **≤ 90 s per answer**. Scoring: 60 correctness, 20 prototype quality (evidence shown, RU+UZ, speed), 20 solution description.
Target: a working version submitted by ~14:00 Nov 5, then improvements resubmitted before 18:00.

## Where things are
| File | Content |
|---|---|
| `PLAN.md` | **Full implementation plan**: architecture, ETL, LLM router, agent loop, anti-hallucination, bot, tests, timeline |
| `docs/HACKATHON.md` | Hackathon rules, scoring, deadlines, links |
| `docs/DATA.md` | Dataset analysis: columns, enum values, region mapping, quirks, formulas, statuses |
| `docs/DATASET_README_original.md` | Organizers' dataset README (authoritative) |
| `docs/products_raw.txt` | All 230 raw product names with contract counts (input for normalization) |
| `docs/hackathon_page_saralash.txt` | Raw text of the hackathon page |
| `data/raw/*.csv` | The dataset (≈158 MB; gitignored) |
| `TASKS.md` | Progress checklist. **Keep it updated** as you work |

## Decisions already made (with the user)
- **Interface:** Telegram bot only (aiogram 3, long polling). The user will create the bot token via @BotFather when we reach that step; provide the steps then.
- **Hosting:** the user's **Hostinger server** (always-on, e.g. systemd service). Develop and test locally on Windows first.
- **LLMs:** free tiers only, with **1 Gemini API key + 1 Groq API key**. Both are called through the **`openai` Python SDK** on their OpenAI-compatible endpoints:
  - Gemini: `https://generativelanguage.googleapis.com/v1beta/openai/`
  - Groq: `https://api.groq.com/openai/v1`
- **Router:** a priority chain in `config.yaml` with automatic fallback across models and providers on 429/5xx/timeout/bad output, plus per-model cooldowns and a total time budget of ~70 s. Model names must be **verified at startup** via each provider's `/models` endpoint, since free-tier models and limits change. Candidate chain: gemini-2.5-flash → groq openai/gpt-oss-120b → groq llama-3.3-70b-versatile → gemini-2.5-flash-lite → groq qwen/qwen3-32b.
- **Quota protection:** answer cache, deterministic fast paths (deal by number, USD rate by date), daily usage counters, compact prompts (Groq's tokens-per-minute limit is the tight constraint). Do heavy evals on Nov 4; Gemini's free quota resets at midnight Pacific time, which is 13:00 Tashkent.
- **Do NOT** suggest creating extra Google accounts or keys to get more quota (ToS). Another free provider (Cerebras, OpenRouter) is OK if the user wants it.
- **Show SQL to judges:** yes, in a collapsible/spoiler block together with the evidence (filters, row count, sample deal numbers, model used, latency).
- **Cancelled deals** (`Аннулирован(Не оплачен)`) are **excluded from totals by default**, and the answer states this.
- **Approach:** Text-to-SQL over **DuckDB** with a wide `deals_full` view and precomputed `volume_units`, `unit_price`, `amount_uzs`, `amount_usd`, plus normalized `product_canonical` / `product_base`. The LLM never does arithmetic; Python formats the numbers. The final answer is built only from SQL results, and a numeric verifier checks every number in it.

## Environment
- Windows, PowerShell, Python 3.12.8. Use `python -X utf8` for any script that prints Cyrillic.
- Project root: `C:\Dev\uzex_agent`. Secrets go in `.env` (see `.env.example`), never commit them.

## Working conventions
- Code, identifiers, and comments are in English. User-facing bot text is RU/UZ.
- Before claiming an answer is correct, verify it with hand-written SQL against the DB.
- Update `TASKS.md` after each completed step.
