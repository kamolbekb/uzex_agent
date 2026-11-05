"""Telegram bot (aiogram 3, long polling) for UZEX AI Hackathon 2026."""

from __future__ import annotations

import asyncio
import html
import logging
import os
import sys
import time
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    ErrorEvent,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.formatter import detect_language  # noqa: E402
from agent.orchestrator import AgentOrchestrator  # noqa: E402
from web.app import start_web_server  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("uzex_bot")

# Example questions for /start inline buttons (2 RU, 2 UZ)
EXAMPLE_QUESTIONS: dict[str, str] = {
    "ex_1": "Какой объём и сумма сделок по хлопку-сырцу в 2025 году?",
    "ex_2": "Топ-5 покупателей портландцемента в 2024 году по сумме сделок",
    "ex_3": "2025-yilda paxta yog‘i bo‘yicha nechta bitim tuzilgan va jami summasi qancha?",
    "ex_4": "Сделка №100001",
}

# Global concurrency limit (3 parallel questions) and per-user rate limit (1 req per 2s)
GLOBAL_SEMAPHORE = asyncio.Semaphore(3)
USER_LAST_REQ: dict[int, float] = {}
USER_COOLDOWN_S = 2.0

dp = Dispatcher()
orchestrator: AgentOrchestrator | None = None


def get_orchestrator() -> AgentOrchestrator:
    global orchestrator
    if orchestrator is None:
        orchestrator = AgentOrchestrator(use_cache=True)
    return orchestrator


def build_examples_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="🇷🇺 Объём и сумма по хлопку-сырцу (2025)", callback_data="ex_1")],
        [InlineKeyboardButton(text="🇷🇺 Топ-5 покупателей цемента (2024)", callback_data="ex_2")],
        [InlineKeyboardButton(text="🇺🇿 Paxta yog‘i bitimlari va summasi (2025)", callback_data="ex_3")],
        [InlineKeyboardButton(text="🔎 Сделка №100001 / Bitim №100001", callback_data="ex_4")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    text = (
        "👋 <b>Здравствуйте! / Assalomu alaykum!</b>\n\n"
        "🇷🇺 Я ИИ-аналитик реестра биржевых сделок <b>UZEX</b> (500 000 сделок за 03.01.2022 – 30.10.2026).\n"
        "Задайте вопрос на <b>русском</b> или <b>узбекском</b> языке — я выполню точный SQL-запрос к базе данных и покажу расчёт с доказательной базой.\n\n"
        "🇺🇿 Men <b>UZEX</b> birja bitimlari reestrining AI-tahlilchisiman (03.01.2022 – 30.10.2026 oralig‘idagi 500 000 ta bitim).\n"
        "Savolingizni <b>o‘zbek</b> yoki <b>rus</b> tilida yozing — maʼlumotlar bazasidan aniq hisob-kitob va asosni taqdim etaman.\n\n"
        "👇 <i>Примеры вопросов / Savol namunalari:</i>"
    )
    await message.answer(text, reply_markup=build_examples_keyboard())


@dp.message(Command("help"))
async def cmd_help(message: Message) -> None:
    text = (
        "ℹ️ <b>Справка / Maʼlumot:</b>\n\n"
        "• <b>Данные / Maʼlumotlar:</b> 500 000 сделок (2022–2026), 6 616 контрактов, 14 392 участника, 120 брокеров, ежедневный курс USD.\n"
        "• <b>Правило расчётов / Hisoblash qoidasi:</b> Аннулированные сделки (<code>Аннулирован(Не оплачен)</code>) по умолчанию исключаются из общих сумм и объёмов, если вы явно не спросите о них.\n"
        "• <b>Команды / Buyruqlar:</b>\n"
        "  /start — Приветствие и примеры / Boshlash va namunalar\n"
        "  /status — Состояние цепочки LLM-моделей / LLM zanjiri holati\n"
        "  /help — Эта справка / Yordam"
    )
    await message.answer(text)


@dp.message(Command("status"))
async def cmd_status(message: Message) -> None:
    orch = get_orchestrator()
    report = orch.router.get_status_report()
    lines = ["⚙️ <b>Статус LLM Router / LLM Router holati:</b>\n"]
    for idx, item in enumerate(report, 1):
        icon = "🟢" if item["state"] == "ok" else ("🟡" if "cooldown" in item["state"] else "🔴")
        line = (
            f"{idx}. {icon} <code>{html.escape(item['provider'])}/{html.escape(item['model'])}</code> — "
            f"<b>{html.escape(item['state'])}</b> "
            f"(ok: {item['calls_ok']}, err: {item['calls_err']}, today: {item['daily_calls']})"
        )
        lines.append(line)
    await message.answer("\n".join(lines))


async def _process_question(target_msg: Message, question: str, user_id: int) -> None:
    now = time.time()
    last = USER_LAST_REQ.get(user_id, 0.0)
    if now - last < USER_COOLDOWN_S:
        await asyncio.sleep(USER_COOLDOWN_S - (now - last))
    USER_LAST_REQ[user_id] = time.time()

    lang = detect_language(question)
    placeholder_text = (
        "⏳ <i>Maʼlumotlar bazasidan qidirilmoqda...</i>"
        if lang == "uz"
        else "⏳ <i>Ищу в данных реестра...</i>"
    )
    status_msg = await target_msg.answer(placeholder_text)

    try:
        async with GLOBAL_SEMAPHORE:
            orch = get_orchestrator()
            result = await asyncio.to_thread(orch.answer, question)

        reply_html = result.telegram_html
        if len(reply_html) > 4000:
            reply_html = reply_html[:4000] + "\n..."

        try:
            await status_msg.edit_text(reply_html, parse_mode=ParseMode.HTML)
        except Exception:
            # Fallback if HTML tag truncation or formatting issue occurs
            await status_msg.edit_text(result.answer_text, parse_mode=None)

    except Exception as e:
        logger.exception("Error answering question %r: %s", question, e)
        err_text = (
            "⚠️ So‘rovni bajarishda vaqtinchalik xatolik yuz berdi. Iltimos, qayta urinib ko‘ring."
            if lang == "uz"
            else "⚠️ Произошла временная ошибка при обработке запроса. Пожалуйста, повторите попытку через несколько секунд."
        )
        try:
            await status_msg.edit_text(err_text, parse_mode=None)
        except Exception:
            await target_msg.answer(err_text, parse_mode=None)


@dp.callback_query(F.data.in_(EXAMPLE_QUESTIONS.keys()))
async def on_example_click(callback: CallbackQuery) -> None:
    q = EXAMPLE_QUESTIONS.get(callback.data or "", "")
    await callback.answer()
    if callback.message and q:
        await callback.message.answer(f"💬 <b>{html.escape(q)}</b>")
        await _process_question(callback.message, q, callback.from_user.id)


@dp.message(F.text)
async def on_text_message(message: Message) -> None:
    question = (message.text or "").strip()
    if not question:
        return
    uid = message.from_user.id if message.from_user else 0
    await _process_question(message, question, uid)


@dp.errors()
async def global_error_handler(event: ErrorEvent) -> bool:
    logger.exception("Unhandled Telegram update error: %s", event.exception)
    return True


async def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN is not set in .env! Please set it before running bot/main.py.")
        sys.exit(1)

    # Pre-warm database tools & router at startup
    logger.info("Initializing AgentOrchestrator and DuckDB indices...")
    get_orchestrator()

    # Start Web UI & HTTP API server alongside Telegram polling
    web_port = int(os.getenv("WEB_PORT", os.getenv("PORT", "8080")))
    web_host = os.getenv("WEB_HOST", "0.0.0.0")
    web_runner = await start_web_server(
        get_orchestrator,
        semaphore=GLOBAL_SEMAPHORE,
        host=web_host,
        port=web_port,
    )

    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    me = await bot.get_me()
    logger.info("Starting polling for @%s (id=%s)...", me.username, me.id)
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot)
    finally:
        await web_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())

