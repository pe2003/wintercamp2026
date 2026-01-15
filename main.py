import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ─── НАСТРОЙКИ ───────────────────────────────────────────────
BOT_TOKEN = "8504812197:AAGId9ij2-85veGUvtQNqbMB5uUWDOHn-Po"
SHEET_ID = "1WY0M1uS4VEOXNOtD2bQoVyRo_v12IK1jpbkefQR8YCg"
CREDENTIALS_FILE = "wintercamprestart-83c93f7f7be1.json"

COLORS = {
    1: "#ADD8E6",   # синий
    2: "#FFA500",   # оранжевый
    3: "#90EE90"    # зелёный
}

scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]
creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
client = gspread.authorize(creds)
sheet = client.open_by_key(SHEET_ID).sheet1

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

user_to_row = {}

# ─── Функции ─────────────────────────────────────────────────
def normalize_fio(text: str) -> set:
    words = text.lower().replace(".", " ").replace("-", " ").split()
    return set(w for w in words if w and len(w) > 1)

def find_row_by_fio(fio: str) -> int | None:
    search_set = normalize_fio(fio)
    if len(search_set) < 2:
        return None

    values = sheet.get_all_values()
    for i, row in enumerate(values, 1):
        if len(row) > 1:
            cell = row[1]  # столбец B — ФИО
            cell_set = normalize_fio(cell)
            if len(search_set & cell_set) >= 2:
                return i
    return None

def save_user_info(row: int, user_id: int, username: str | None):
    sheet.update_cell(row, 7, user_id)                    # G — Telegram ID
    sheet.update_cell(row, 8, f"@{username}" if username else "")  # H — @username

async def set_row_color(row: int, stage: int):
    color = COLORS.get(stage)
    if not color or row < 1:
        return
    r = int(color[1:3], 16) / 255
    g = int(color[3:5], 16) / 255
    b = int(color[5:7], 16) / 255
    sheet.format(f"A{row}:Z{row}", {"backgroundColor": {"red": r, "green": g, "blue": b}})

# ─── Хэндлеры ─────────────────────────────────────────────────
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("Напиши своё ФИО")

@dp.message()
async def handle_message(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username

    if user_id in user_to_row:
        row = user_to_row[user_id]
        text = f"Строка {row} | @{username or 'без ника'}"
    else:
        row = find_row_by_fio(message.text)
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
    if "_" not in callback.data:
        await callback.answer()
        return

    stage_str, row_str = callback.data.split("_")
    stage = int(stage_str[1:])
    row = int(row_str)

    await set_row_color(row, stage)

    status = {1: "Синий ✓ регистрация", 2: "Оранжевый ✓ реквизиты", 3: "Зелёный ✓ оплачено"}[stage]

    try:
        await callback.message.edit_text(
            callback.message.text + f"\n\n→ {status}",
            reply_markup=None
        )
    except:
        pass

    await callback.answer()

# ─── ЗАПУСК ───────────────────────────────────────────────────
async def main():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
