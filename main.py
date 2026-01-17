import asyncio
import logging
import os
import json
import base64
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from fastapi import FastAPI, Request
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = "8504812197:AAGId9ij2-85veGUvtQNqbMB5uUWDOHn-Po"
SHEET_ID = "1WY0M1uS4VEOXNOtD2bQoVyRo_v12IK1jpbkefQR8YCg"
PORT = int(os.getenv("PORT", 10000))

CREDENTIALS_BASE64 = os.getenv("GOOGLE_CREDENTIALS_BASE64")
if not CREDENTIALS_BASE64:
    raise ValueError("GOOGLE_CREDENTIALS_BASE64 не установлен")

json_str = base64.b64decode(CREDENTIALS_BASE64).decode("utf-8")
creds_dict = json.loads(json_str)

scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)
sheet = client.open_by_key(SHEET_ID).sheet1

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

user_to_row = {}

def normalize_fio(text: str) -> set:
    if not text: return set()
    words = text.lower().replace(".", " ").replace("-", " ").split()
    return set(w for w in words if w and len(w) > 1)

def find_row_by_fio(fio: str) -> int | None:
    search_set = normalize_fio(fio)
    if len(search_set) < 2: return None
    values = sheet.get_all_values()
    for i, row in enumerate(values, 1):
        if len(row) > 1:
            cell = row[1]
            cell_set = normalize_fio(cell)
            if len(search_set & cell_set) >= 2:
                return i
    return None

def save_user_info(row: int, user_id: int, username: str | None):
    sheet.update_cell(row, 7, str(user_id))
    sheet.update_cell(row, 8, f"@{username}" if username else "")

async def set_row_color(row: int, stage: int):
    COLORS = {1: "#ADD8E6", 2: "#FFA500", 3: "#90EE90"}
    color = COLORS.get(stage)
    if not color or row < 1: return
    r = int(color[1:3], 16) / 255
    g = int(color[3:5], 16) / 255
    b = int(color[5:7], 16) / 255
    try:
        sheet.format(f"A{row}:Z{row}", {"backgroundColor": {"red": r, "green": g, "blue": b}})
    except Exception as e:
        logger.error(f"Не удалось закрасить строку {row}: {e}")

main_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Статистика")]],
    resize_keyboard=True,
    one_time_keyboard=False
)

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "Перешли сообщение от человека или напиши ФИО",
        reply_markup=main_kb
    )

@dp.message()
async def handle_message(message: types.Message):
    if message.text == "Статистика":
        values = sheet.get_all_values()
        unique_names = set()
        for row in values[1:]:
            if len(row) > 1:
                fio = row[1].strip().lower()
                if fio:
                    unique_names.add(fio)
        count = len(unique_names)
        await message.answer(
            f"Прошло регистрацию: {count} человек\n(уникальные ФИО)",
            reply_markup=main_kb
        )
        return

    target_user = message.forward_origin.sender_user if message.forward_origin and hasattr(message.forward_origin, 'sender_user') else message.from_user
    user_id = target_user.id
    username = target_user.username

    row = user_to_row.get(user_id)
    if row:
        text = f"Строка {row} | @{username or 'без ника'}"
    else:
        search_text = message.text or message.caption or ""
        row = find_row_by_fio(search_text)
        if row:
            user_to_row[user_id] = row
            save_user_info(row, user_id, username)
            text = f"Строка {row} | Записал @{username or 'без ника'}"
        else:
            await message.answer("Не нашёл.\nПопробуй ФИО ещё раз.")
            return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 • Прошёл регистрацию", callback_data=f"s1_{row}")],
        [InlineKeyboardButton(text="2 • Выдал реквизиты",   callback_data=f"s2_{row}")],
        [InlineKeyboardButton(text="3 • Оплатил",           callback_data=f"s3_{row}")]
    ])

    await message.answer(text, reply_markup=kb)

@dp.callback_query()
async def process_callback(callback: types.CallbackQuery):
    if "_" not in callback.data: return
    stage_str, row_str = callback.data.split("_")
    stage = int(stage_str[1:])
    row = int(row_str)
    await set_row_color(row, stage)
    status = {1: "Синий ✓ регистрация", 2: "Оранжевый ✓ реквизиты", 3: "Зелёный ✓ оплачено"}[stage]
    try:
        await callback.message.edit_text(callback.message.text + f"\n\n→ {status}", reply_markup=None)
    except:
        pass
    await callback.answer()

app = FastAPI()

@app.get("/")
async def root():
    return {"status": "bot alive"}

@app.post("/webhook")
async def webhook(request: Request):
    update = await request.json()
    update_obj = types.Update.model_validate(update)
    await dp.feed_update(bot, update_obj)
    return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=PORT)
