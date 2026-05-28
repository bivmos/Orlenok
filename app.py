import asyncio
import logging
import os
from datetime import date
from flask import Flask
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage
import sqlite3
from contextlib import closing

# ========== КОНФИГУРАЦИЯ ==========
BOT_TOKEN = "8746282512:AAFotEFrvk2GIZGhlJRbCsrZ_CoLxaexY1Y"  # ваш токен
ADMIN_ID = 87471475     # ваш Telegram ID

DEPARTURE_DATE = date(2026, 6, 21)

# ========== ЧЕК-ЛИСТ (только латиница для ключей) ==========
SECTIONS = {
    "sec0": {
        "title": "ДО 9 ИЮНЯ – ДОМАШНИЕ ДОКУМЕНТЫ",
        "items": [
            {"key": "send_birth_cert", "text": "Отправить скан свидетельства о рождении"},
            {"key": "print_ticket", "text": "Распечатать и подписать путёвку"},
            {"key": "consent_personal", "text": "Согласие на обработку персональных данных"},
            {"key": "consent_medical", "text": "Согласие на медицинское вмешательство"},
            {"key": "personal_account", "text": "Заполнить лицевой счёт"},
            {"key": "navigator", "text": "Скачать и подписать Навигатор"}
        ]
    },
    "sec1": {
        "title": "9 – 18 ИЮНЯ – МЕДИЦИНСКИЕ СПРАВКИ",
        "items": [
            {"key": "form_079", "text": "Получить справку 079/у (с психиатром)"},
            {"key": "vaccine_cert", "text": "Выписка прививок (если не вписаны в 079)"},
            {"key": "diaskin_test", "text": "Диаскин-тест (декабрь 2025 или 2026)"},
            {"key": "send_scan", "text": "Отправить сканы на проверку (info-orlyonok@mail.ru)"}
        ]
    },
    "sec2": {
        "title": "19 ИЮНЯ",
        "items": [
            {"key": "no_contact", "text": "Справка об отсутствии контактов"}
        ]
    },
    "sec3": {
        "title": "20 ИЮНЯ",
        "items": [
            {"key": "wagon_number", "text": "Получить номер вагона"}
        ]
    },
    "sec4": {
        "title": "21 ИЮНЯ – ОТПРАВЛЕНИЕ",
        "items": [
            {"key": "pack_complete", "text": "Собрать 3 комплекта документов"},
            {"key": "arrive_station", "text": "Приехать на вокзал за 30–40 минут до поезда"}
        ]
    }
}

ALL_KEYS = []
for sec in SECTIONS.values():
    for item in sec["items"]:
        ALL_KEYS.append(item["key"])

# ========== БАЗА ДАННЫХ ==========
def init_db():
    with closing(sqlite3.connect("checklist.db")) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_progress (
                user_id INTEGER,
                item_key TEXT,
                is_done INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, item_key)
            )
        """)
        conn.commit()

def get_user_progress(user_id: int):
    with closing(sqlite3.connect("checklist.db")) as conn:
        cur = conn.execute("SELECT item_key, is_done FROM user_progress WHERE user_id = ?", (user_id,))
        return {row[0]: bool(row[1]) for row in cur.fetchall()}

def set_user_progress(user_id: int, item_key: str, is_done: bool):
    with closing(sqlite3.connect("checklist.db")) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO user_progress (user_id, item_key, is_done) VALUES (?, ?, ?)",
            (user_id, item_key, int(is_done))
        )
        conn.commit()

def reset_user_progress(user_id: int):
    with closing(sqlite3.connect("checklist.db")) as conn:
        conn.execute("DELETE FROM user_progress WHERE user_id = ?", (user_id,))
        conn.commit()

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def days_until_departure():
    today = date.today()
    delta = (DEPARTURE_DATE - today).days
    if delta < 0:
        return "🚀 Отправление уже было!"
    elif delta == 0:
        return "🚀 СЕГОДНЯ ОТПРАВЛЕНИЕ!"
    else:
        return f"⏳ До отправления осталось: {delta} дн."

def get_section_progress(user_id: int, section_items):
    progress = get_user_progress(user_id)
    done_count = sum(1 for item in section_items if progress.get(item["key"], False))
    total = len(section_items)
    return done_count, total

def format_checklist_full(user_id: int):
    progress = get_user_progress(user_id)
    lines = [f"<b>📋 ПОЛНЫЙ ЧЕК-ЛИСТ</b>", f"{days_until_departure()}\n"]
    for sec_id, sec in SECTIONS.items():
        done, total = get_section_progress(user_id, sec["items"])
        lines.append(f"<b>{sec['title']}</b> — {done}/{total} выполнено")
        for item in sec["items"]:
            status = "✅" if progress.get(item["key"], False) else "☐"
            lines.append(f"{status} {item['text']}")
        lines.append("")
    return "\n".join(lines)

def get_sections_keyboard(user_id: int):
    buttons = []
    for sec_id, sec in SECTIONS.items():
        done, total = get_section_progress(user_id, sec["items"])
        buttons.append([InlineKeyboardButton(
            text=f"{sec['title']} ({done}/{total})",
            callback_data=f"sec_{sec_id}"
        )])
    buttons.append([InlineKeyboardButton(text="📋 Показать весь чек-лист", callback_data="full_checklist")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_items_keyboard(user_id: int, sec_id: str):
    progress = get_user_progress(user_id)
    items = SECTIONS[sec_id]["items"]
    buttons = []
    for idx, item in enumerate(items):
        status = "✅" if progress.get(item["key"], False) else "☐"
        buttons.append([InlineKeyboardButton(
            text=f"{status} {item['text']}",
            callback_data=f"toggle_{sec_id}_{idx}"
        )])
    buttons.append([InlineKeyboardButton(text="🔙 Назад к разделам", callback_data="back_to_sections")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ========== ОБРАБОТЧИКИ БОТА ==========
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    progress = get_user_progress(user_id)
    for key in ALL_KEYS:
        if key not in progress:
            set_user_progress(user_id, key, False)
    await message.answer(
        f"Привет! Я бот-чеклист для поездки в Орлёнок.\n\n{days_until_departure()}\n\n"
        "Выбери раздел, чтобы отметить выполненные пункты:",
        reply_markup=get_sections_keyboard(user_id)
    )

@dp.message(Command("checklist"))
async def cmd_checklist(message: types.Message):
    user_id = message.from_user.id
    full_text = format_checklist_full(user_id)
    await message.answer(full_text, parse_mode="HTML")

@dp.callback_query()
async def handle_callback(call: types.CallbackQuery):
    user_id = call.from_user.id
    data = call.data

    if data.startswith("sec_"):
        sec_id = data[4:]
        if sec_id in SECTIONS:
            await call.message.edit_text(
                f"<b>{SECTIONS[sec_id]['title']}</b>\n\nОтмечай выполненные пункты:",
                parse_mode="HTML",
                reply_markup=get_items_keyboard(user_id, sec_id)
            )
    elif data.startswith("toggle_"):
        parts = data.split("_")
        if len(parts) == 3:
            sec_id = parts[1]
            idx = int(parts[2])
            if sec_id in SECTIONS and 0 <= idx < len(SECTIONS[sec_id]["items"]):
                item = SECTIONS[sec_id]["items"][idx]
                item_key = item["key"]
                progress = get_user_progress(user_id)
                new_status = not progress.get(item_key, False)
                set_user_progress(user_id, item_key, new_status)
                await call.message.edit_reply_markup(reply_markup=get_items_keyboard(user_id, sec_id))
                await call.answer(f"{'✅ Выполнено' if new_status else '☐ Отмечено как невыполненное'}")
            else:
                await call.answer("Ошибка: пункт не найден")
        else:
            await call.answer("Неверный формат")
    elif data == "full_checklist":
        full_text = format_checklist_full(user_id)
        await call.message.edit_text(full_text, parse_mode="HTML")
        await call.message.answer(
            "Вернуться к разделам: /start",
            reply_markup=types.ReplyKeyboardRemove()
        )
    elif data == "back_to_sections":
        await call.message.edit_text(
            f"Выбери раздел:\n\n{days_until_departure()}",
            reply_markup=get_sections_keyboard(user_id)
        )
    else:
        await call.answer("Неизвестная команда")

async def run_bot():
    """Запускает long polling бота"""
    init_db()
    await dp.start_polling(bot)

# ========== FLASK ПРИЛОЖЕНИЕ ДЛЯ RENDER ==========
app = Flask(__name__)

@app.route('/')
def index():
    return "Бот работает! Используйте Telegram."

@app.route('/health')
def health():
    return "OK", 200

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    # Запускаем бота в фоновом asyncio-событии
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(run_bot())
    
    # Запускаем Flask-сервер на порту, который даёт Render
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)