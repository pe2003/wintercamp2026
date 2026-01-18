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

REQUISITES_LIMIT = 15
REQUISITES_COUNT = 8

def get_requisites_counts():
    values = sheet.get_all_values()
    counts = [0] * (REQUISITES_COUNT + 1)
    for row in values[1:]:
        if len(row) >= 12:
            req = row[11].strip()
            if req.isdigit():
                n = int(req)
                if 1 <= n <= REQUISITES_COUNT:
                    counts[n] += 1
    return counts

def get_stats():
    values = sheet.get_all_values()
    if not values or len(values) < 2:
        return 0, 0, 0, 0
    
    seen = set()
    blue = orange = green = 0
    
    for row in values[1:]:
        if len(row) < 2: continue
        fio = row[1].strip().lower()
# Фамилия + имя + отчество — разные комбинации = разные люди
norm = ' '.join(fio.replace('.', '').replace('-', '').split()[:3])  # первые три слова        if norm in seen: continue
        seen.add(norm)
        
        if len(row) >= 11:
            status = row[10].strip().lower()
            if status in ["прошёл регистрацию", "1", "синий"]:
                blue += 1
            elif status in ["выдал реквизиты", "2", "оранжевый"]:
                orange += 1
            elif status in ["оплатил", "3", "зелёный", "оплачено"]:
                green += 1
    
    return len(seen), blue, orange, green

stats_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="📊 Статистика")]],
    resize_keyboard=True,
    one_time_keyboard=False
)

def normalize_fio(text: str) -> set:
    if not text: return set()
    words = text.lower().replace(".", " ").replace("-", " ").split()
    return set(w for w in words if w and len(w) > 1)

def find_row_by_fio(fio: str) -> int | None:
    if not fio: return None
    search_set = normalize_fio(fio)
    if len(search_set) < 2: return None
    values = sheet.get_all_values()
    for i, row in enumerate(values, 1):
        if len(row) > 1:
            cell_set = normalize_fio(row[1])
            if len(search_set & cell_set) >= 2:
                return i
    return None

def save_user_info(row: int, user_id: int, username: str | None):
    sheet.update_cell(row, 9, str(user_id))
    sheet.update_cell(row, 10, f"@{username}" if username else "")

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
        logger.error(f"Ошибка окрашивания строки {row}: {e}")

STATUS_TEXTS = {
    1: "Прошёл регистрацию",
    2: "Выдал реквизиты",
    3: "Оплатил"
}

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Перешли сообщение или напиши ФИО", reply_markup=stats_kb)

@dp.message(lambda m: m.text == "📊 Статистика")
async def show_stats(message: types.Message):
    total, blue, orange, green = get_stats()
    await message.answer(
        f"📊 Статистика:\n\n"
        f"Уникальных человек: {total}\n"
        f"Синий (регистрация): {blue}\n"
        f"Оранжевый (реквизиты): {orange}\n"
        f"Зелёный (оплачено): {green}"
    )

@dp.message()
async def handle_message(message: types.Message):
    target_user = message.from_user
    is_forward = False
    
    if message.forward_origin:
        if isinstance(message.forward_origin, types.MessageOriginUser):
            target_user = message.forward_origin.sender_user
            is_forward = True
        else:
            await message.answer("Невозможно получить ID пользователя.")
            return
    
    user_id = target_user.id
    username = target_user.username
    row = user_to_row.get(user_id)
    
    if not row:
        search_text = message.text or message.caption or ""
        row = find_row_by_fio(search_text)
        if row:
            user_to_row[user_id] = row
            save_user_info(row, user_id, username)
    
    if not row:
        await message.answer("Не нашёл строку по ФИО.")
        return
    
    row_data = sheet.row_values(row)
    
    info = f"Строка {row} | @{username or 'без ника'}\n"
    info += f"Пользователь: {user_id}\n\n"
    
    if len(row_data) >= 8:
        info += f"A: {row_data[0]}\n"
        info += f"B: {row_data[1]}\n"
        info += f"C: {row_data[2]}\n"
        info += f"D: {row_data[3]}\n"
        info += f"E: {row_data[4]}\n"
        info += f"G: {row_data[6] if len(row_data) > 6 else '—'}\n"
        info += f"H: {row_data[7] if len(row_data) > 7 else '—'}\n"
    
    status = sheet.cell(row, 11).value or "—"
    info += f"\nСтатус (K): {status}"
    
    if is_forward:
        info += " (переслано)"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 • Прошёл регистрацию", callback_data=f"s1_{row}")],
        [InlineKeyboardButton(text="2 • Выдал реквизиты", callback_data=f"s2_{row}")],
        [InlineKeyboardButton(text="3 • Оплатил", callback_data=f"s3_{row}")]
    ])
    
    await message.answer(info, reply_markup=kb)

@dp.callback_query()
async def process_callback(callback: types.CallbackQuery):
    if "_" not in callback.data:
        await callback.answer()
        return
    
    parts = callback.data.split("_")
    if len(parts) < 2 or not parts[0].startswith("s"):
        await callback.answer("Ошибка данных")
        return
    
    stage = int(parts[0][1:])
    row = int(parts[1])
    
    if stage == 2:
        counts = get_requisites_counts()
        kb = InlineKeyboardMarkup(inline_keyboard=[])
        for i in range(1, REQUISITES_COUNT + 1):
            text = f"Реквизиты {i} ({counts[i]}/{REQUISITES_LIMIT})"
            kb.inline_keyboard.append([
                InlineKeyboardButton(text=text, callback_data=f"req_{row}_{i}")
            ])
        await callback.message.edit_text(
            callback.message.text + "\n\nВыберите комплект:",
            reply_markup=kb
        )
        await callback.answer()
        return
    
    # Для 1 и 3
    sheet.update_cell(row, 11, STATUS_TEXTS.get(stage, ""))
    await set_row_color(row, stage)
    
    status_text = {
        1: "Синий ✓ регистрация",
        2: "Оранжевый ✓ реквизиты",
        3: "Зелёный ✓ оплачено"
    }.get(stage, "неизвестно")
    
    try:
        await callback.message.edit_text(
            callback.message.text + f"\n\n→ {status_text}",
            reply_markup=None
        )
    except:
        pass
    
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("req_"))
async def process_requisites(callback: types.CallbackQuery):
    _, row_str, num_str = callback.data.split("_")
    row = int(row_str)
    num = int(num_str)
    
    sheet.update_cell(row, 12, str(num))  # L = колонка 12
    
    try:
        text = callback.message.text.split("\n\nВыберите комплект:")[0]
        await callback.message.edit_text(
            text + f"\n\n→ Выданы Реквизиты {num}",
            reply_markup=None
        )
    except:
        pass
    
    await callback.answer()

app = FastAPI()

@app.get("/")
async def root():
    return {"status": "bot alive"}

@app.post("/webhook")
async def webhook(request: Request):
    try:
        update = await request.json()
        update_obj = types.Update.model_validate(update)
        await dp.feed_update(bot, update_obj)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return {"status": "error"}, 500

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, log_level="info")
