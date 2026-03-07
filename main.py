#!/usr/bin/env python3
"""letify ☀️ — Telegram-бот + HTTP-сервер (webhook mode)"""

import asyncio, logging, os
from datetime import date
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

TOKEN       = os.environ.get("BOT_TOKEN")
APP_URL     = os.environ.get("APP_URL", "https://letify-production.up.railway.app")
PORT        = int(os.environ.get("PORT", 8080))
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL  = f"{APP_URL}{WEBHOOK_PATH}"

HTML_FILE = Path(__file__).parent / "static" / "letify.html"

if not TOKEN:
    raise RuntimeError("BOT_TOKEN не задан!")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=TOKEN)
dp  = Dispatcher()


def days_to_summer() -> int:
    today  = date.today()
    summer = date(today.year, 6, 1)
    if today >= summer:
        summer = date(today.year + 1, 6, 1)
    return (summer - today).days


def days_word(n: int) -> str:
    if 11 <= n % 100 <= 19:
        return "дней"
    r = n % 10
    if r == 1:      return "день"
    if 2 <= r <= 4: return "дня"
    return "дней"


@dp.message(CommandStart())
async def cmd_start(message: Message):
    n = days_to_summer()
    text = (
        f"☀️ <b>Привет! Я letify — твой трекер пути к лету.</b>\n\n"
        f"До лета осталось <b>{n} {days_word(n)}</b> — каждый день на счету! 🏃\n\n"
        f"Отслеживай вес, воду, калории и сон прямо здесь, в Telegram. "
        f"Нажми кнопку ниже, чтобы открыть приложение 👇"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=f"🌅 Открыть letify · {n} {days_word(n)} до лета",
            web_app=WebAppInfo(url=APP_URL)
        )
    ]])
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


async def handle_index(request: web.Request) -> web.Response:
    if HTML_FILE.exists():
        return web.FileResponse(HTML_FILE)
    return web.Response(text="letify ☀️ is running!")


async def handle_health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def on_startup(app: web.Application) -> None:
    await bot.set_webhook(WEBHOOK_URL)
    log.info(f"Webhook установлен: {WEBHOOK_URL}")


async def on_shutdown(app: web.Application) -> None:
    await bot.delete_webhook()
    log.info("Webhook удалён")


async def main():
    app = web.Application()

    # Регистрируем обработчик webhook-запросов от Telegram
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    # Свои роуты
    app.router.add_get("/",       handle_index)
    app.router.add_get("/health", handle_health)

    # Хуки старта/остановки
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    log.info(f"letify ☀️ запущен на порту {PORT}")

    # Держим сервер живым
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())