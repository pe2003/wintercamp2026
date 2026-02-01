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
from collections import Counter

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

def normalize_fio_key(fio: str) -> str:
    if not fio or len(fio.strip()) < 5:
        return ""
    cleaned = ''.join(c for c in fio.lower() if c.isalpha() or c.isspace())
    words = [w for w in cleaned.split() if len(w) >= 2]
    if len(words) < 2:
        return ""
    key_words = sorted(words[:4])
    return ' '.join(key_words)


def is_blueish(bg):
    if not bg:
        return False
    r = bg.get("red", 0)
    g = bg.get("green", 0)
    b = bg.get("blue", 0)
    return (
        r < 0.80 and
        g > 0.65 and
        b > 0.75 and
        b > r * 1.15 and
        abs(r - g) < 0.30
    )


def is_orangeish(bg):
    if not bg:
        return False
    r = bg.get("red", 0)
    g = bg.get("green", 0)
    b = bg.get("blue", 0)
    return (
        r > 0.88 and
        g > 0.40 and g < 0.82 and
        b < 0.35 and
        r > g * 1.08
    )


def is_greenish(bg):
    if not bg:
        return False
    r = bg.get("red", 0)
    g = bg.get("green", 0)
    b = bg.get("blue", 0)
    return (
        g > 0.72 and
        r < 0.68 and
        b < 0.68 and
        g > r * 1.35 and
        g > b * 1.25
    )


def get_stats():
    values = sheet.get_all_values()
    if not values or len(values) < 2:
        return 0, 0, 0, 0, 0, 0

    unique = {}  # key → status (1,2,3)

    tg_count = 0
    fio_count = 0

    for i, row in enumerate(values[1:], start=2):
        if len(row) < 2:
            continue

        tg_id_raw = (row[8].strip() if len(row) > 8 else "") or ""
        has_tg = tg_id_raw.isdigit() and len(tg_id_raw) >= 5

        if has_tg:
            key = f"tg_{tg_id_raw}"
            tg_count += 1
        else:
            norm = normalize_fio_key(row[1])
            if not norm:
                continue
            key = f"fio_{norm}"
            fio_count += 1

        # Определяем статус по цвету ячейки B{i}
        try:
            cell_format = sheet.format(f"B{i}")
            bg = cell_format.get("backgroundColor") if cell_format else None

            if not bg:
                continue

            status = 0
            if is_blueish(bg):
                status = 1
            elif is_orangeish(bg):
                status = 2
            elif is_greenish(bg):
                status = 3

            if status == 0:
                continue

            # Обновляем только если статус выше
            if key not in unique or status > unique.get(key, 0):
                unique[key] = status

        except Exception as e:
            logger.warning(f"Не удалось получить цвет строки {i}: {e}")
            continue

    blue   = sum(1 for s in unique.values() if s == 1)
    orange = sum(1 for s in unique.values() if s == 2)
    green  = sum(1 for s in unique.values() if s == 3)
    total  = len(unique)

    return total, blue, orange, green, tg_count, fio_count


stats_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="📊 Статистика")]],
    resize_keyboard=True,
    one_time_keyboard=False
)


def find_row_by_fio(fio: str) -> int | None:
    if not fio:
        return None
    search_set = set(w for w in fio.lower().replace(".", " ").replace("-", " ").split() if len(w) > 1)
    if len(search_set) < 2:
        return None
    values = sheet.get_all_values()
    for i, row in enumerate(values, 1):
        if len(row) > 1:
            cell_set = set(w for w in row[1].lower().replace(".", " ").replace("-", " ").split() if len(w) > 1)
            if len(search_set & cell_set) >= 2:
                return i
    return None


def save_user_info(row: int, user_id: int, username: str | None):
    sheet.update_cell(row, 9, str(user_id))          # I — Telegram ID
    sheet.update_cell(row, 10, f"@{username}" if username else "")


async def set_row_color(row: int, stage: int):
    COLORS = {1: "#ADD8E6", 2: "#FFA500", 3: "#90EE90"}
    color = COLORS.get(stage)
    if not color or row < 1:
        return
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
    total, blue, orange, green, tg_count, fio_count = get_stats()
    text = (
        f"📊 Статистика:\n\n"
        f"Уникальных человек: {total}\n"
        f"  • по Telegram ID: {tg_count}\n"
        f"  • по ФИО: {fio_count}\n\n"
        f"Синий (регистрация): {blue}\n"
        f"Оранжевый (реквизиты): {orange}\n"
        f"Зелёный (оплачено): {green}"
    )
    await message.answer(text)


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
        [InlineKeyboardButton(text="2 • Выдал реквизиты",    callback_data=f"s2_{row}")],
        [InlineKeyboardButton(text="3 • Оплатил",            callback_data=f"s3_{row}")]
    ])

    await message.answer(info, reply_markup=kb)


@dp.callback_query()
async def process_callback(callback: types.CallbackQuery):
    data = callback.data

    if data.startswith("req_"):
        await process_requisites(callback)
        return

    if "_" not in data:
        await callback.answer()
        return

    parts = data.split("_")
    if len(parts) != 2 or not parts[0].startswith("s"):
        await callback.answer("Ошибка данных")
        return

    stage = int(parts[0][1:])
    row = int(parts[1])

    if stage == 2:
        # Здесь можно оставить старую логику подсчёта реквизитов или убрать, если не нужна
        counts = [0] * (REQUISITES_COUNT + 1)
        values = sheet.get_all_values()
        for r in values[1:]:
            if len(r) >= 12 and r[11].strip().isdigit():
                n = int(r[11].strip())
                if 1 <= n <= REQUISITES_COUNT:
                    counts[n] += 1

        kb = InlineKeyboardMarkup(inline_keyboard=[])
        for i in range(1, REQUISITES_COUNT + 1):
            text = f"Реквизиты {i} ({counts[i]}/{REQUISITES_LIMIT})"
            kb.inline_keyboard.append([InlineKeyboardButton(text=text, callback_data=f"req_{row}_{i}")])

        await callback.message.edit_text(
            callback.message.text + "\n\nВыберите комплект:",
            reply_markup=kb
        )
        await callback.answer()
        return

    # Для 1 и 3 — просто меняем статус и цвет
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

    sheet.update_cell(row, 12, str(num))           # L — номер реквизитов
    sheet.update_cell(row, 11, "Выдал реквизиты")  # K — статус
    await set_row_color(row, 2)

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