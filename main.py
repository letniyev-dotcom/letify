#!/usr/bin/env python3
"""letify ☀️ — Telegram-бот"""

import subprocess, sys
def _pip(pkg): subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "-q"])
try:    import aiogram
except: _pip("aiogram")

import asyncio, logging, os
from datetime import date

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

TOKEN = os.environ.get("BOT_TOKEN")
APP_URL = os.environ.get("APP_URL", "https://your-app-url.com")  # URL мини-приложения

if not TOKEN:
    raise RuntimeError("BOT_TOKEN не задан. Установи переменную окружения BOT_TOKEN.")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=TOKEN)
dp  = Dispatcher()


def days_to_summer() -> int:
    """Количество дней до 1 июня."""
    today = date.today()
    summer = date(today.year, 6, 1)
    if today >= summer:
        summer = date(today.year + 1, 6, 1)
    return (summer - today).days


def days_word(n: int) -> str:
    """Склонение слова 'день'."""
    if 11 <= n % 100 <= 19:
        return "дней"
    r = n % 10
    if r == 1:
        return "день"
    if 2 <= r <= 4:
        return "дня"
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


async def main():
    log.info("letify ☀️ запущен!")
    await dp.start_polling(bot, drop_pending_updates=True)


if __name__ == "__main__":
    asyncio.run(main())