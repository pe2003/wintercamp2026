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

# ────────────────────────────────────────────────
# Нормализация ФИО — только буквы, нижний регистр
# ────────────────────────────────────────────────
def normalize_fio(fio: str) -> str:
    if not fio:
        return ""
    cleaned = ''.join(c for c in fio.lower() if c.isalpha())
    return cleaned if len(cleaned) >= 6 else ""


# ────────────────────────────────────────────────
# Определение цвета — очень мягкие условия
# ────────────────────────────────────────────────
def get_status_from_color(bg):
    if not bg:
        return 0

    r = bg.get("red", 0.0)
    g = bg.get("green", 0.0)
    b = bg.get("blue", 0.0)

    # Зелёный (оплачено) — самый важный
    if g > 0.58 and g > r + 0.12 and g > b + 0.12:
        return 3

    # Оранжевый / жёлто-оранжевый
    if r > 0.78 and g > 0.38 and b < 0.48 and r >= g:
        return 2

    # Синий / голубой
    if b > 0.58 and b > r + 0.08 and b > g + 0.03:
        return 1

    return 0


def get_stats():
    values = sheet.get_all_values()
    if not values or len(values) < 2:
        return 0, 0, 0, 0

    # fio_norm → лучший статус (1,2,3)
    by_fio = {}
    # tg_id → лучший статус
    by_tg = {}

    for row_idx, row in enumerate(values[1:], start=2):
        if len(row) < 2 or not row[1].strip():
            continue

        fio_norm = normalize_fio(row[1])
        if not fio_norm:
            continue

        tg_id = ""
        if len(row) > 8:
            tg_raw = str(row[8]).strip()
            if tg_raw.isdigit() and len(tg_raw) >= 5:
                tg_id = tg_raw

        # Пробуем получить цвет — сначала из B, потом из A, потом из K
        bg = None
        for col in ["B", "A", "K"]:
            try:
                fmt = sheet.format(f"{col}{row_idx}")
                bg_candidate = fmt.get("backgroundColor")
                if bg_candidate:
                    bg = bg_candidate
                    break
            except Exception:
                continue

        status = get_status_from_color(bg)
        if status == 0:
            continue

        # Записываем в словарь по tg (если есть)
        if tg_id:
            prev = by_tg.get(tg_id, 0)
            if status > prev:
                by_tg[tg_id] = status

        # Записываем в словарь по fio (всегда)
        prev = by_fio.get(fio_norm, 0)
        if status > prev:
            by_fio[fio_norm] = status

    # Объединяем — если человек есть и по tg и по fio → берём максимальный статус
    all_people = {}

    for tg, st in by_tg.items():
        all_people[("tg", tg)] = st

    for fio, st in by_fio.items():
        key = ("fio", fio)
        # Если уже есть по tg — не перезаписываем, если fio-статус хуже
        if key not in all_people or st > all_people[key]:
            all_people[key] = st

    # Подсчёт
    blue   = sum(1 for s in all_people.values() if s == 1)
    orange = sum(1 for s in all_people.values() if s == 2)
    green  = sum(1 for s in all_people.values() if s == 3)
    total  = len(all_people)

    return total, blue, orange, green


stats_kb = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="📊 Статистика")]],
    resize_keyboard=True,
    one_time_keyboard=False
)


def find_row_by_fio(fio: str) -> int | None:
    if not fio:
        return None
    search = set(w for w in fio.lower().replace(".", "").replace("-", "").split() if len(w) > 1)
    if len(search) < 2:
        return None

    values = sheet.get_all_values()
    for i, row in enumerate(values, 1):
        if len(row) > 1:
            cell_words = set(w for w in row[1].lower().replace(".", "").replace("-", "").split() if len(w) > 1)
            if len(search & cell_words) >= 2:
                return i
    return None


def save_user_info(row: int, user_id: int, username: str | None):
    sheet.update_cell(row, 9, str(user_id))
    sheet.update_cell(row, 10, f"@{username}" if username else "")


async def set_row_color(row: int, stage: int):
    COLORS = {1: "#ADD8E6", 2: "#FFA500", 3: "#90EE90"}
    color = COLORS.get(stage)
    if not color or row < 1:
        return
    r = int(color[1:3], 16) / 255.0
    g = int(color[3:5], 16) / 255.0
    b = int(color[5:7], 16) / 255.0
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
        counts = [0] * (REQUISITES_COUNT + 1)
        values = sheet.get_all_values()
        for r in values[1:]:
            if len(r) >= 12 and r[11].strip().isdigit():
                n = int(r[11])
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

    sheet.update_cell(row, 12, str(num))
    sheet.update_cell(row, 11, "Выдал реквизиты")
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