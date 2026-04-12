import os
import json
import asyncio
import logging
import random
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ParseMode
from aiogram.utils.keyboard import InlineKeyboardBuilder
import aiohttp

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Переменная окружения BOT_TOKEN не установлена!")

CRYPTO_BOT_API = "465788:AAOxwPgMIPTheqZpyAyN2JotJ9U8fREP7rl"
CRYPTO_API_URL = "https://pay.crypt.bot/api"

ADMIN_IDS = [7973988177]
SUPPORT_USERNAME = "VestSupport"

# ========== ЛОГИРОВАНИЕ ==========
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ========== БАЗА ДАННЫХ ==========
DB_FILE = "users_db.json"

def load_db():
    try:
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_db(data):
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def get_user(user_id):
    db = load_db()
    uid = str(user_id)
    if uid not in db:
        db[uid] = {
            "balance": 0,
            "username": "",
            "first_name": "",
            "stats": {
                "dice": {"wins": 0, "losses": 0},
                "basketball": {"wins": 0, "losses": 0},
                "football": {"wins": 0, "losses": 0},
                "total_won": 0,
                "total_lost": 0
            }
        }
        save_db(db)
    return db[uid]

def update_balance(user_id, amount):
    db = load_db()
    uid = str(user_id)
    if uid not in db:
        get_user(user_id)
        db = load_db()
    db[uid]["balance"] = round(db[uid]["balance"] + amount, 2)
    if amount > 0:
        db[uid]["stats"]["total_won"] = round(db[uid]["stats"].get("total_won", 0) + amount, 2)
    else:
        db[uid]["stats"]["total_lost"] = round(db[uid]["stats"].get("total_lost", 0) + abs(amount), 2)
    save_db(db)
    return db[uid]["balance"]

def set_balance(user_id, amount):
    db = load_db()
    uid = str(user_id)
    if uid not in db:
        get_user(user_id)
        db = load_db()
    old_balance = db[uid]["balance"]
    db[uid]["balance"] = round(amount, 2)
    save_db(db)
    return old_balance, amount

def add_game_stat(user_id, game, is_win):
    db = load_db()
    uid = str(user_id)
    if uid not in db:
        get_user(user_id)
        db = load_db()
    if is_win:
        db[uid]["stats"][game]["wins"] += 1
    else:
        db[uid]["stats"][game]["losses"] += 1
    save_db(db)

def get_all_users():
    return load_db()

def update_user_info(user_id, username, first_name):
    db = load_db()
    uid = str(user_id)
    if uid not in db:
        get_user(user_id)
        db = load_db()
    db[uid]["username"] = username or ""
    db[uid]["first_name"] = first_name or ""
    save_db(db)

# ========== ПРЕМИУМ ЭМОДЗИ ID ==========
EMOJI = {
    "settings": "5870982283724328568",
    "profile": "5870994129244131212",
    "wallet": "5769126056262898415",
    "dice": "5373141891321699086",
    "basketball": "5370810157871667232",
    "football": "5471984997361523302",
    "money": "5904462880941545555",
    "check": "5870633910337015697",
    "cross": "5870657884844462243",
    "back": "6037249452824072506",
    "info": "6028435952299413210",
    "stats": "5870921681735781843",
    "crypto": "5260752406890711732",
    "graph": "5870930636742595124",
    "home": "5873147866364514353",
    "edit": "5870676941614354370",
    "users": "5870772616305839506",
    "broadcast": "5370599459661045441",
    "loading": "5345906554510012647",
    "link": "5769289093221454192",
    "gift": "6032644646587338669",
    "send": "5963103826075456248",
    "games": "5778672437122045013",
    "withdraw": "5890848474563352982",
    "support": "6030400221232501136",
}

def e(emoji_id):
    return f'<tg-emoji emoji-id="{emoji_id}">⚡</tg-emoji>'

# ========== FSM ==========
class DepositState(StatesGroup):
    waiting_for_amount = State()

class WithdrawState(StatesGroup):
    waiting_for_amount = State()

class AdminStates(StatesGroup):
    waiting_for_broadcast = State()
    waiting_for_edit_balance = State()

# ========== КЛАВИАТУРЫ ==========
def main_menu_keyboard():
    """Главное меню под полем ввода"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Профиль", icon_custom_emoji_id=EMOJI["profile"])],
            [KeyboardButton(text="Игры", icon_custom_emoji_id=EMOJI["games"])],
            [
                KeyboardButton(text="Пополнить", icon_custom_emoji_id=EMOJI["wallet"]),
                KeyboardButton(text="Вывод", icon_custom_emoji_id=EMOJI["withdraw"])
            ],
            [
                KeyboardButton(text="Помощь", icon_custom_emoji_id=EMOJI["info"]),
                KeyboardButton(text="Поддержка", icon_custom_emoji_id=EMOJI["support"])
            ],
        ],
        resize_keyboard=True
    )

def games_menu_keyboard():
    """Меню выбора игр"""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🎲 Кубик", callback_data="game_dice"))
    builder.row(InlineKeyboardButton(text="🏀 Баскетбол", callback_data="game_basketball"))
    builder.row(InlineKeyboardButton(text="⚽ Футбол", callback_data="game_football"))
    builder.row(InlineKeyboardButton(text="Назад", callback_data="back_to_menu_msg", icon_custom_emoji_id=EMOJI["back"]))
    return builder.as_markup()

def back_to_menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Назад в меню", callback_data="back_to_menu_msg", icon_custom_emoji_id=EMOJI["back"]))
    return builder.as_markup()

def back_to_admin_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Назад", callback_data="admin_panel", icon_custom_emoji_id=EMOJI["back"]))
    return builder.as_markup()

def cancel_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Отмена", callback_data="cancel_action", icon_custom_emoji_id=EMOJI["cross"]))
    return builder.as_markup()

def support_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Написать в поддержку", url=f"https://t.me/{SUPPORT_USERNAME}", icon_custom_emoji_id=EMOJI["support"]))
    builder.row(InlineKeyboardButton(text="Назад", callback_data="back_to_menu_msg", icon_custom_emoji_id=EMOJI["back"]))
    return builder.as_markup()

def admin_panel_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Рассылка", callback_data="admin_broadcast", icon_custom_emoji_id=EMOJI["broadcast"]))
    builder.row(InlineKeyboardButton(text="Изменить баланс", callback_data="admin_edit_balance", icon_custom_emoji_id=EMOJI["edit"]))
    builder.row(InlineKeyboardButton(text="Статистика", callback_data="admin_stats", icon_custom_emoji_id=EMOJI["stats"]))
    builder.row(InlineKeyboardButton(text="Пользователи", callback_data="admin_users_list", icon_custom_emoji_id=EMOJI["users"]))
    builder.row(InlineKeyboardButton(text="Закрыть", callback_data="close_admin", icon_custom_emoji_id=EMOJI["cross"]))
    return builder.as_markup()

def dice_mode_keyboard():
    """Выбор режима кубика"""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="1-3 / 4-6 (x1.85)", callback_data="dice_mode_highlow", icon_custom_emoji_id=EMOJI["dice"]))
    builder.row(InlineKeyboardButton(text="Чёт / Нечет (x1.85)", callback_data="dice_mode_evenodd", icon_custom_emoji_id=EMOJI["dice"]))
    builder.row(InlineKeyboardButton(text="Угадать число (x5)", callback_data="dice_mode_number", icon_custom_emoji_id=EMOJI["dice"]))
    builder.row(InlineKeyboardButton(text="Два кубика: сумма 7 (x4)", callback_data="dice_mode_twodice", icon_custom_emoji_id=EMOJI["dice"]))
    builder.row(InlineKeyboardButton(text="Три кубика: 10-11 (x3)", callback_data="dice_mode_threedice", icon_custom_emoji_id=EMOJI["dice"]))
    builder.row(InlineKeyboardButton(text="Счастливое: 1 или 6 (x2.5)", callback_data="dice_mode_lucky", icon_custom_emoji_id=EMOJI["dice"]))
    builder.row(InlineKeyboardButton(text="Назад", callback_data="back_to_games", icon_custom_emoji_id=EMOJI["back"]))
    return builder.as_markup()

def dice_choice_keyboard(mode: str):
    """Клавиатура выбора для конкретного режима кубика"""
    builder = InlineKeyboardBuilder()
    if mode == "highlow":
        builder.row(
            InlineKeyboardButton(text="1-3 (Меньше)", callback_data="dice_low", icon_custom_emoji_id=EMOJI["dice"]),
            InlineKeyboardButton(text="4-6 (Больше)", callback_data="dice_high", icon_custom_emoji_id=EMOJI["dice"])
        )
    elif mode == "evenodd":
        builder.row(
            InlineKeyboardButton(text="Чётное", callback_data="dice_even", icon_custom_emoji_id=EMOJI["dice"]),
            InlineKeyboardButton(text="Нечётное", callback_data="dice_odd", icon_custom_emoji_id=EMOJI["dice"])
        )
    elif mode == "number":
        for i in range(1, 7):
            builder.add(InlineKeyboardButton(text=str(i), callback_data=f"dice_num_{i}", icon_custom_emoji_id=EMOJI["dice"]))
        builder.adjust(3)
    elif mode == "lucky":
        builder.row(
            InlineKeyboardButton(text="1", callback_data="dice_lucky_1", icon_custom_emoji_id=EMOJI["dice"]),
            InlineKeyboardButton(text="6", callback_data="dice_lucky_6", icon_custom_emoji_id=EMOJI["dice"])
        )
    builder.row(InlineKeyboardButton(text="Изменить ставку", callback_data="dice_change_bet", icon_custom_emoji_id=EMOJI["edit"]))
    builder.row(InlineKeyboardButton(text="Назад к режимам", callback_data="dice_back_modes", icon_custom_emoji_id=EMOJI["back"]))
    return builder.as_markup()

def dice_bet_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="0.1", callback_data="dice_bet_0.1", icon_custom_emoji_id=EMOJI["money"]),
        InlineKeyboardButton(text="0.5", callback_data="dice_bet_0.5", icon_custom_emoji_id=EMOJI["money"]),
        InlineKeyboardButton(text="1", callback_data="dice_bet_1", icon_custom_emoji_id=EMOJI["money"])
    )
    builder.row(
        InlineKeyboardButton(text="5", callback_data="dice_bet_5", icon_custom_emoji_id=EMOJI["money"]),
        InlineKeyboardButton(text="10", callback_data="dice_bet_10", icon_custom_emoji_id=EMOJI["money"]),
        InlineKeyboardButton(text="50", callback_data="dice_bet_50", icon_custom_emoji_id=EMOJI["money"])
    )
    builder.row(InlineKeyboardButton(text="Своя сумма", callback_data="dice_bet_custom", icon_custom_emoji_id=EMOJI["edit"]))
    builder.row(InlineKeyboardButton(text="Назад", callback_data="back_to_games", icon_custom_emoji_id=EMOJI["back"]))
    return builder.as_markup()

def basketball_bet_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="0.1", callback_data="basketball_bet_0.1", icon_custom_emoji_id=EMOJI["money"]),
        InlineKeyboardButton(text="0.5", callback_data="basketball_bet_0.5", icon_custom_emoji_id=EMOJI["money"]),
        InlineKeyboardButton(text="1", callback_data="basketball_bet_1", icon_custom_emoji_id=EMOJI["money"])
    )
    builder.row(
        InlineKeyboardButton(text="5", callback_data="basketball_bet_5", icon_custom_emoji_id=EMOJI["money"]),
        InlineKeyboardButton(text="10", callback_data="basketball_bet_10", icon_custom_emoji_id=EMOJI["money"]),
        InlineKeyboardButton(text="50", callback_data="basketball_bet_50", icon_custom_emoji_id=EMOJI["money"])
    )
    builder.row(InlineKeyboardButton(text="Своя сумма", callback_data="basketball_bet_custom", icon_custom_emoji_id=EMOJI["edit"]))
    builder.row(InlineKeyboardButton(text="Назад", callback_data="back_to_games", icon_custom_emoji_id=EMOJI["back"]))
    return builder.as_markup()

def basketball_choice_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Попадание (1.85x)", callback_data="basketball_hit", icon_custom_emoji_id=EMOJI["basketball"], style="success"))
    builder.row(InlineKeyboardButton(text="Промах (1.5x)", callback_data="basketball_miss", icon_custom_emoji_id=EMOJI["basketball"], style="danger"))
    builder.row(InlineKeyboardButton(text="Попадание 2 раза (3x)", callback_data="basketball_double", icon_custom_emoji_id=EMOJI["basketball"], style="primary"))
    builder.row(InlineKeyboardButton(text="Изменить ставку", callback_data="basketball_change_bet", icon_custom_emoji_id=EMOJI["edit"]))
    builder.row(InlineKeyboardButton(text="Назад", callback_data="back_to_games", icon_custom_emoji_id=EMOJI["back"]))
    return builder.as_markup()

def football_mode_keyboard():
    """Выбор режима футбола"""
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Гол (x1.85)", callback_data="football_mode_goal", icon_custom_emoji_id=EMOJI["football"], style="success"))
    builder.row(InlineKeyboardButton(text="Промах (x1.5)", callback_data="football_mode_miss", icon_custom_emoji_id=EMOJI["football"], style="danger"))
    builder.row(InlineKeyboardButton(text="Пенальти (x2)", callback_data="football_mode_penalty", icon_custom_emoji_id=EMOJI["football"], style="primary"))
    builder.row(InlineKeyboardButton(text="Штанга/Перекладина (x4)", callback_data="football_mode_post", icon_custom_emoji_id=EMOJI["football"]))
    builder.row(InlineKeyboardButton(text="Назад", callback_data="back_to_games", icon_custom_emoji_id=EMOJI["back"]))
    return builder.as_markup()

def football_bet_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="0.1", callback_data="football_bet_0.1", icon_custom_emoji_id=EMOJI["money"]),
        InlineKeyboardButton(text="0.5", callback_data="football_bet_0.5", icon_custom_emoji_id=EMOJI["money"]),
        InlineKeyboardButton(text="1", callback_data="football_bet_1", icon_custom_emoji_id=EMOJI["money"])
    )
    builder.row(
        InlineKeyboardButton(text="5", callback_data="football_bet_5", icon_custom_emoji_id=EMOJI["money"]),
        InlineKeyboardButton(text="10", callback_data="football_bet_10", icon_custom_emoji_id=EMOJI["money"]),
        InlineKeyboardButton(text="50", callback_data="football_bet_50", icon_custom_emoji_id=EMOJI["money"])
    )
    builder.row(InlineKeyboardButton(text="Своя сумма", callback_data="football_bet_custom", icon_custom_emoji_id=EMOJI["edit"]))
    builder.row(InlineKeyboardButton(text="Назад", callback_data="back_to_games", icon_custom_emoji_id=EMOJI["back"]))
    return builder.as_markup()

def play_again_keyboard(game: str):
    builder = InlineKeyboardBuilder()
    if game == "dice":
        cb = "dice_change_bet"
    elif game == "basketball":
        cb = "basketball_change_bet"
    else:
        cb = "football_change_bet"
    builder.row(InlineKeyboardButton(text="Играть ещё", callback_data=cb, icon_custom_emoji_id=EMOJI[game], style="primary"))
    builder.row(InlineKeyboardButton(text="В меню", callback_data="back_to_menu_msg", icon_custom_emoji_id=EMOJI["home"]))
    return builder.as_markup()

# ========== CRYPTO BOT API ==========
async def create_invoice(amount: float):
    async with aiohttp.ClientSession() as session:
        headers = {"Crypto-Pay-API-Token": CRYPTO_BOT_API}
        data = {
            "asset": "USDT",
            "amount": str(amount),
            "description": "Vest Casino - пополнение баланса",
            "paid_btn_name": "callback",
            "paid_btn_url": "https://t.me/vest_casino_bot"
        }
        async with session.post(f"{CRYPTO_API_URL}/createInvoice", headers=headers, json=data) as resp:
            if resp.status == 200:
                result = await resp.json()
                if result.get("ok"):
                    return result["result"]
            logger.error(f"Crypto Bot API error: {await resp.text()}")
            return None

async def check_invoice(invoice_id: int):
    async with aiohttp.ClientSession() as session:
        headers = {"Crypto-Pay-API-Token": CRYPTO_BOT_API}
        data = {"invoice_ids": [invoice_id]}
        async with session.post(f"{CRYPTO_API_URL}/getInvoices", headers=headers, json=data) as resp:
            if resp.status == 200:
                result = await resp.json()
                if result.get("ok") and result["result"]["items"]:
                    return result["result"]["items"][0]
            return None

async def create_check(amount: float):
    async with aiohttp.ClientSession() as session:
        headers = {"Crypto-Pay-API-Token": CRYPTO_BOT_API}
        data = {"asset": "USDT", "amount": str(amount)}
        async with session.post(f"{CRYPTO_API_URL}/createCheck", headers=headers, json=data) as resp:
            if resp.status == 200:
                result = await resp.json()
                if result.get("ok"):
                    return result["result"]
            logger.error(f"Crypto Bot check error: {await resp.text()}")
            return None

# ========== ИНИЦИАЛИЗАЦИЯ ==========
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

user_bets = {}
user_invoices = {}

# ========== КОМАНДЫ ==========
@router.message(Command("start"))
async def cmd_start(message: Message):
    update_user_info(message.from_user.id, message.from_user.username, message.from_user.first_name)
    user = get_user(message.from_user.id)
    welcome_text = f"""
{e(EMOJI['home'])} <b>Vest Casino</b>

{e(EMOJI['dice'])} <b>Кубик</b>
{e(EMOJI['basketball'])} <b>Баскетбол</b>
{e(EMOJI['football'])} <b>Футбол</b>

{e(EMOJI['wallet'])} Твой баланс: <b>{user['balance']:.2f} USDT</b>

{e(EMOJI['support'])} Поддержка: @{SUPPORT_USERNAME}
"""
    await message.answer(welcome_text, parse_mode=ParseMode.HTML, reply_markup=main_menu_keyboard())

@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer(f"{e(EMOJI['cross'])} У вас нет доступа", parse_mode=ParseMode.HTML)
        return
    await message.answer(f"{e(EMOJI['settings'])} <b>Админ-панель</b>\n\nВыберите действие:", parse_mode=ParseMode.HTML, reply_markup=admin_panel_keyboard())

@router.message(Command("support"))
async def cmd_support(message: Message):
    text = f"""
{e(EMOJI['support'])} <b>ПОДДЕРЖКА</b>

Если у вас возникли вопросы или проблемы, напишите нам:

{e(EMOJI['link'])} <b><a href='https://t.me/{SUPPORT_USERNAME}'>@{SUPPORT_USERNAME}</a></b>

Мы ответим в ближайшее время!
"""
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=support_keyboard())

@router.message(Command("id"))
async def cmd_id(message: Message):
    await message.answer(f"Ваш ID: <code>{message.from_user.id}</code>", parse_mode=ParseMode.HTML)

@router.message(F.text == "Профиль")
async def profile(message: Message):
    user = get_user(message.from_user.id)
    stats = user["stats"]
    text = f"""
{e(EMOJI['profile'])} <b>ПРОФИЛЬ</b>

{e(EMOJI['wallet'])} <b>Баланс:</b> {user['balance']:.2f} USDT

{e(EMOJI['stats'])} <b>СТАТИСТИКА:</b>

{e(EMOJI['dice'])} <b>Кубик:</b>
  {e(EMOJI['check'])} Побед: {stats['dice']['wins']}
  {e(EMOJI['cross'])} Поражений: {stats['dice']['losses']}

{e(EMOJI['basketball'])} <b>Баскетбол:</b>
  {e(EMOJI['check'])} Побед: {stats['basketball']['wins']}
  {e(EMOJI['cross'])} Поражений: {stats['basketball']['losses']}

{e(EMOJI['football'])} <b>Футбол:</b>
  {e(EMOJI['check'])} Побед: {stats['football']['wins']}
  {e(EMOJI['cross'])} Поражений: {stats['football']['losses']}

{e(EMOJI['graph'])} <b>ВСЕГО:</b>
  {e(EMOJI['money'])} Выиграно: {stats['total_won']:.2f} USDT
  {e(EMOJI['cross'])} Проиграно: {stats['total_lost']:.2f} USDT
"""
    await message.answer(text, parse_mode=ParseMode.HTML)

@router.message(F.text == "Игры")
async def games_menu(message: Message):
    text = f"""
{e(EMOJI['games'])} <b>ВЫБЕРИТЕ ИГРУ</b>

🎲 <b>Кубик</b>
🏀 <b>Баскетбол</b>
⚽ <b>Футбол</b>

Минимальная ставка: <b>0.1 USDT</b>
"""
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=games_menu_keyboard())

@router.message(F.text == "Поддержка")
async def support_button(message: Message):
    text = f"""
{e(EMOJI['support'])} <b>ПОДДЕРЖКА</b>

Если у вас возникли вопросы или проблемы, напишите нам:

{e(EMOJI['link'])} <b><a href='https://t.me/{SUPPORT_USERNAME}'>@{SUPPORT_USERNAME}</a></b>

Мы ответим в ближайшее время!
"""
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=support_keyboard())

@router.message(F.text == "Пополнить")
async def deposit_start(message: Message, state: FSMContext):
    await message.answer(
        f"{e(EMOJI['wallet'])} <b>ПОПОЛНЕНИЕ БАЛАНСА</b>\n\nВведите сумму в USDT (мин. 0.1):",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_keyboard()
    )
    await state.set_state(DepositState.waiting_for_amount)

@router.message(DepositState.waiting_for_amount)
async def deposit_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.replace(",", "."))
        if amount < 0.1:
            await message.answer(f"{e(EMOJI['cross'])} Минимум 0.1 USDT", parse_mode=ParseMode.HTML)
            return
        
        invoice = await create_invoice(amount)
        if invoice:
            user_invoices[message.from_user.id] = invoice["invoice_id"]
            pay_url = invoice['pay_url']
            
            text = f"""
{e(EMOJI['crypto'])} <b>СЧЁТ НА ОПЛАТУ</b>

{e(EMOJI['money'])} Сумма: <b>{amount:.2f} USDT</b>

{e(EMOJI['link'])} <b><a href='{pay_url}'>НАЖМИТЕ ДЛЯ ОПЛАТЫ</a></b>

После оплаты нажмите кнопку ниже:
"""
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text="Проверить оплату", callback_data=f"check_payment_{invoice['invoice_id']}", icon_custom_emoji_id=EMOJI["loading"]))
            builder.row(InlineKeyboardButton(text="Отмена", callback_data="cancel_action", icon_custom_emoji_id=EMOJI["cross"]))
            
            await message.answer(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True, reply_markup=builder.as_markup())
        else:
            await message.answer(f"{e(EMOJI['cross'])} Ошибка создания счёта", parse_mode=ParseMode.HTML)
    except ValueError:
        await message.answer(f"{e(EMOJI['cross'])} Введите число", parse_mode=ParseMode.HTML)
    await state.clear()

@router.message(F.text == "Вывод")
async def withdraw_start(message: Message, state: FSMContext):
    user = get_user(message.from_user.id)
    if user['balance'] < 0.5:
        await message.answer(f"{e(EMOJI['cross'])} Минимальная сумма вывода 0.5 USDT", parse_mode=ParseMode.HTML)
        return
    await message.answer(
        f"{e(EMOJI['withdraw'])} <b>ВЫВОД СРЕДСТВ</b>\n\n{e(EMOJI['wallet'])} Ваш баланс: <b>{user['balance']:.2f} USDT</b>\n\nВведите сумму вывода (мин. 0.5):",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_keyboard()
    )
    await state.set_state(WithdrawState.waiting_for_amount)

@router.message(WithdrawState.waiting_for_amount)
async def withdraw_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.replace(",", "."))
        user = get_user(message.from_user.id)
        
        if amount < 0.5:
            await message.answer(f"{e(EMOJI['cross'])} Минимум 0.5 USDT", parse_mode=ParseMode.HTML)
            return
        if amount > user['balance']:
            await message.answer(f"{e(EMOJI['cross'])} Недостаточно средств", parse_mode=ParseMode.HTML)
            return
        
        check_data = await create_check(amount)
        if check_data:
            update_balance(message.from_user.id, -amount)
            new_balance = get_user(message.from_user.id)['balance']
            check_link = f"https://t.me/send?start={check_data['check_id']}"
            
            await message.answer(
                f"{e(EMOJI['check'])} <b>ЧЕК НА ВЫВОД СОЗДАН!</b>\n\n{e(EMOJI['money'])} Сумма: <b>{amount:.2f} USDT</b>\n{e(EMOJI['link'])} <b><a href='{check_link}'>НАЖМИТЕ ДЛЯ ПОЛУЧЕНИЯ</a></b>\n\n{e(EMOJI['wallet'])} Новый баланс: <b>{new_balance:.2f} USDT</b>",
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=main_menu_keyboard()
            )
        else:
            await message.answer(f"{e(EMOJI['cross'])} <b>ОШИБКА ВЫВОДА</b>\n\nПопробуйте позже.", parse_mode=ParseMode.HTML, reply_markup=main_menu_keyboard())
        await state.clear()
    except ValueError:
        await message.answer(f"{e(EMOJI['cross'])} Введите число", parse_mode=ParseMode.HTML)

@router.message(F.text == "Помощь")
async def help_cmd(message: Message):
    text = f"""
{e(EMOJI['info'])} <b>ПОМОЩЬ</b>

{e(EMOJI['wallet'])} <b>ФИНАНСЫ:</b>
• Пополнение от 0.1 USDT
• Вывод от 0.5 USDT (через чек Crypto Bot)
• Ставки от 0.1 USDT

{e(EMOJI['settings'])} <b>Команды:</b>
/admin — админ-панель
/id — узнать свой ID
/support — поддержка
"""
    await message.answer(text, parse_mode=ParseMode.HTML)

# ========== ОБРАБОТЧИКИ ИГР ==========
@router.callback_query(F.data == "back_to_games")
async def back_to_games(callback: CallbackQuery):
    text = f"""
{e(EMOJI['games'])} <b>ВЫБЕРИТЕ ИГРУ</b>

🎲 <b>Кубик</b>
🏀 <b>Баскетбол</b>
⚽ <b>Футбол</b>

Минимальная ставка: <b>0.1 USDT</b>
"""
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=games_menu_keyboard())
    await callback.answer()

@router.callback_query(F.data == "back_to_menu_msg")
async def back_to_menu_msg(callback: CallbackQuery):
    await callback.message.delete()
    user = get_user(callback.from_user.id)
    await callback.message.answer(
        f"{e(EMOJI['home'])} <b>Vest Casino</b>\n\nВыберите действие:",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard()
    )
    await callback.answer()

# ========== КУБИК ==========
@router.callback_query(F.data == "game_dice")
async def game_dice(callback: CallbackQuery):
    user = get_user(callback.from_user.id)
    text = f"""
{e(EMOJI['dice'])} <b>КУБИК</b>

{e(EMOJI['wallet'])} Баланс: <b>{user['balance']:.2f} USDT</b>

Выберите режим:
"""
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=dice_mode_keyboard())
    await callback.answer()

@router.callback_query(F.data.startswith("dice_mode_"))
async def dice_mode_selected(callback: CallbackQuery, state: FSMContext):
    mode = callback.data.replace("dice_mode_", "")
    await state.update_data(dice_mode=mode)
    user = get_user(callback.from_user.id)
    
    multipliers = {"highlow": 1.85, "evenodd": 1.85, "number": 5, "twodice": 4, "threedice": 3, "lucky": 2.5}
    mode_names = {"highlow": "1-3 / 4-6", "evenodd": "Чёт / Нечет", "number": "Угадать число", "twodice": "Два кубика: сумма 7", "threedice": "Три кубика: 10-11", "lucky": "Счастливое: 1 или 6"}
    
    text = f"""
{e(EMOJI['dice'])} <b>КУБИК</b>

Режим: <b>{mode_names[mode]}</b>
Множитель: <b>x{multipliers[mode]}</b>

{e(EMOJI['wallet'])} Баланс: <b>{user['balance']:.2f} USDT</b>

Выберите ставку:
"""
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=dice_bet_keyboard())
    await callback.answer()

@router.callback_query(F.data == "dice_back_modes")
async def dice_back_modes(callback: CallbackQuery):
    user = get_user(callback.from_user.id)
    text = f"""
{e(EMOJI['dice'])} <b>КУБИК</b>

{e(EMOJI['wallet'])} Баланс: <b>{user['balance']:.2f} USDT</b>

Выберите режим:
"""
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=dice_mode_keyboard())
    await callback.answer()

@router.callback_query(F.data.startswith("dice_bet_"))
async def dice_set_bet(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    bet_type = parts[2]
    user_id = callback.from_user.id
    user = get_user(user_id)
    data = await state.get_data()
    mode = data.get("dice_mode", "highlow")
    
    if bet_type == "custom":
        await callback.message.edit_text(
            f"{e(EMOJI['dice'])} <b>КУБИК</b>\n\n{e(EMOJI['wallet'])} Баланс: {user['balance']:.2f} USDT\n\nВведите сумму ставки (мин. 0.1):",
            parse_mode=ParseMode.HTML
        )
        user_bets[user_id] = {"game": "dice", "mode": mode, "awaiting_custom": True}
        await callback.answer()
        return
    
    bet = float(bet_type)
    if user["balance"] < bet:
        await callback.answer("Недостаточно средств!", show_alert=True)
        return
    if bet < 0.1:
        await callback.answer("Минимальная ставка 0.1 USDT", show_alert=True)
        return
    
    user_bets[user_id] = {"game": "dice", "mode": mode, "bet": bet, "awaiting_custom": False}
    
    if mode in ["twodice", "threedice"]:
        await dice_play_auto(callback, user_id, bet, mode)
    else:
        text = f"""
{e(EMOJI['dice'])} <b>КУБИК</b>

{e(EMOJI['money'])} Ставка: <b>{bet:.2f} USDT</b>
{e(EMOJI['wallet'])} Баланс: <b>{user['balance']:.2f} USDT</b>

Выберите вариант:
"""
        await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=dice_choice_keyboard(mode))
    await callback.answer()

async def dice_play_auto(callback: CallbackQuery, user_id: int, bet: float, mode: str):
    user = get_user(user_id)
    if mode == "twodice":
        d1, d2 = random.randint(1, 6), random.randint(1, 6)
        is_win = (d1 + d2) == 7
        mult = 4
        result = f"Кубики: {d1} + {d2} = {d1+d2}"
    else:
        d1, d2, d3 = random.randint(1, 6), random.randint(1, 6), random.randint(1, 6)
        total = d1 + d2 + d3
        is_win = 10 <= total <= 11
        mult = 3
        result = f"Кубики: {d1} + {d2} + {d3} = {total}"
    
    if is_win:
        win_amount = bet * mult
        update_balance(user_id, win_amount - bet)
        add_game_stat(user_id, "dice", True)
        text = f"{e(EMOJI['dice'])} <b>КУБИК</b>\n\n{result}\n\n{e(EMOJI['check'])} <b>ПОБЕДА!</b>\n\n{e(EMOJI['money'])} +{win_amount - bet:.2f} USDT"
    else:
        update_balance(user_id, -bet)
        add_game_stat(user_id, "dice", False)
        text = f"{e(EMOJI['dice'])} <b>КУБИК</b>\n\n{result}\n\n{e(EMOJI['cross'])} <b>ПОРАЖЕНИЕ</b>\n\n{e(EMOJI['money'])} -{bet:.2f} USDT"
    
    new_balance = get_user(user_id)["balance"]
    text += f"\n{e(EMOJI['wallet'])} Баланс: <b>{new_balance:.2f} USDT</b>"
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=play_again_keyboard("dice"))

@router.callback_query(F.data == "dice_change_bet")
async def dice_change_bet(callback: CallbackQuery, state: FSMContext):
    user = get_user(callback.from_user.id)
    data = await state.get_data()
    mode = data.get("dice_mode", "highlow")
    multipliers = {"highlow": 1.85, "evenodd": 1.85, "number": 5, "twodice": 4, "threedice": 3, "lucky": 2.5}
    mode_names = {"highlow": "1-3 / 4-6", "evenodd": "Чёт / Нечет", "number": "Угадать число", "twodice": "Два кубика: сумма 7", "threedice": "Три кубика: 10-11", "lucky": "Счастливое: 1 или 6"}
    
    text = f"""
{e(EMOJI['dice'])} <b>КУБИК</b>

Режим: <b>{mode_names[mode]}</b>
Множитель: <b>x{multipliers[mode]}</b>

{e(EMOJI['wallet'])} Баланс: <b>{user['balance']:.2f} USDT</b>

Выберите ставку:
"""
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=dice_bet_keyboard())
    await callback.answer()

@router.callback_query(F.data.startswith("dice_"))
async def dice_play(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if user_id not in user_bets:
        await callback.answer("Сначала выберите ставку!", show_alert=True)
        return
    
    bet_data = user_bets[user_id]
    bet = bet_data["bet"]
    mode = bet_data["mode"]
    user = get_user(user_id)
    
    if user["balance"] < bet:
        await callback.answer("Недостаточно средств!", show_alert=True)
        return
    
    choice = callback.data
    roll = random.randint(1, 6)
    is_win = False
    mult = {"highlow": 1.85, "evenodd": 1.85, "number": 5, "lucky": 2.5}[mode]
    
    if mode == "highlow":
        is_win = (choice == "dice_low" and roll <= 3) or (choice == "dice_high" and roll >= 4)
        choice_text = "Меньше (1-3)" if choice == "dice_low" else "Больше (4-6)"
    elif mode == "evenodd":
        is_win = (choice == "dice_even" and roll % 2 == 0) or (choice == "dice_odd" and roll % 2 != 0)
        choice_text = "Чётное" if choice == "dice_even" else "Нечётное"
    elif mode == "number":
        num = int(choice.split("_")[-1])
        is_win = roll == num
        choice_text = f"Число {num}"
    elif mode == "lucky":
        num = int(choice.split("_")[-1])
        is_win = roll == num
        choice_text = f"Число {num}"
    
    if is_win:
        win_amount = bet * mult
        update_balance(user_id, win_amount - bet)
        add_game_stat(user_id, "dice", True)
        text = f"{e(EMOJI['dice'])} <b>КУБИК</b>\n\nВаш выбор: <b>{choice_text}</b>\n{e(EMOJI['dice'])} Выпало: <b>{roll}</b>\n\n{e(EMOJI['check'])} <b>ПОБЕДА!</b>\n\n{e(EMOJI['money'])} +{win_amount - bet:.2f} USDT"
    else:
        update_balance(user_id, -bet)
        add_game_stat(user_id, "dice", False)
        text = f"{e(EMOJI['dice'])} <b>КУБИК</b>\n\nВаш выбор: <b>{choice_text}</b>\n{e(EMOJI['dice'])} Выпало: <b>{roll}</b>\n\n{e(EMOJI['cross'])} <b>ПОРАЖЕНИЕ</b>\n\n{e(EMOJI['money'])} -{bet:.2f} USDT"
    
    new_balance = get_user(user_id)["balance"]
    text += f"\n{e(EMOJI['wallet'])} Баланс: <b>{new_balance:.2f} USDT</b>"
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=play_again_keyboard("dice"))
    await callback.answer()

# ========== БАСКЕТБОЛ ==========
@router.callback_query(F.data == "game_basketball")
async def game_basketball(callback: CallbackQuery):
    user = get_user(callback.from_user.id)
    text = f"""
{e(EMOJI['basketball'])} <b>БАСКЕТБОЛ</b>

{e(EMOJI['wallet'])} Баланс: <b>{user['balance']:.2f} USDT</b>

{e(EMOJI['money'])} Множители:
• Попадание: <b>x1.85</b>
• Промах: <b>x1.5</b>
• Попадание 2 раза: <b>x3</b>

Выберите ставку:
"""
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=basketball_bet_keyboard())
    await callback.answer()

@router.callback_query(F.data.startswith("basketball_bet_"))
async def basketball_set_bet(callback: CallbackQuery):
    parts = callback.data.split("_")
    bet_type = parts[2]
    user_id = callback.from_user.id
    user = get_user(user_id)
    
    if bet_type == "custom":
        await callback.message.edit_text(
            f"{e(EMOJI['basketball'])} <b>БАСКЕТБОЛ</b>\n\n{e(EMOJI['wallet'])} Баланс: {user['balance']:.2f} USDT\n\nВведите сумму ставки (мин. 0.1):",
            parse_mode=ParseMode.HTML
        )
        user_bets[user_id] = {"game": "basketball", "awaiting_custom": True}
        await callback.answer()
        return
    
    bet = float(bet_type)
    if user["balance"] < bet:
        await callback.answer("Недостаточно средств!", show_alert=True)
        return
    if bet < 0.1:
        await callback.answer("Минимальная ставка 0.1 USDT", show_alert=True)
        return
    
    user_bets[user_id] = {"game": "basketball", "bet": bet, "awaiting_custom": False}
    
    text = f"""
{e(EMOJI['basketball'])} <b>БАСКЕТБОЛ</b>

{e(EMOJI['money'])} Ставка: <b>{bet:.2f} USDT</b>
{e(EMOJI['wallet'])} Баланс: <b>{user['balance']:.2f} USDT</b>

Выберите исход:
"""
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=basketball_choice_keyboard())
    await callback.answer()

@router.callback_query(F.data == "basketball_change_bet")
async def basketball_change_bet(callback: CallbackQuery):
    user = get_user(callback.from_user.id)
    text = f"""
{e(EMOJI['basketball'])} <b>БАСКЕТБОЛ</b>

{e(EMOJI['wallet'])} Баланс: <b>{user['balance']:.2f} USDT</b>

{e(EMOJI['money'])} Множители:
• Попадание: <b>x1.85</b>
• Промах: <b>x1.5</b>
• Попадание 2 раза: <b>x3</b>

Выберите ставку:
"""
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=basketball_bet_keyboard())
    await callback.answer()

@router.callback_query(F.data.in_(["basketball_hit", "basketball_miss", "basketball_double"]))
async def basketball_play(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in user_bets:
        await callback.answer("Сначала выберите ставку!", show_alert=True)
        return
    
    bet = user_bets[user_id]["bet"]
    user = get_user(user_id)
    if user["balance"] < bet:
        await callback.answer("Недостаточно средств!", show_alert=True)
        return
    
    choice = callback.data
    is_win = random.random() < 0.2
    
    if choice == "basketball_hit":
        choice_text, mult = "Попадание", 1.85
        result_desc = "Попадание!" if is_win else "Промах!"
    elif choice == "basketball_miss":
        choice_text, mult = "Промах", 1.5
        result_desc = "Промах!" if is_win else "Попадание!"
    else:
        choice_text, mult = "Попадание 2 раза", 3
        is_win = random.random() < 0.04
        result_desc = "Попадание 2 раза подряд!" if is_win else "Промах!"
    
    if is_win:
        win_amount = bet * mult
        update_balance(user_id, win_amount - bet)
        add_game_stat(user_id, "basketball", True)
        text = f"{e(EMOJI['basketball'])} <b>БАСКЕТБОЛ</b>\n\nВаш выбор: <b>{choice_text}</b>\nРезультат: <b>{result_desc}</b>\n\n{e(EMOJI['check'])} <b>ПОБЕДА!</b>\n\n{e(EMOJI['money'])} +{win_amount - bet:.2f} USDT"
    else:
        update_balance(user_id, -bet)
        add_game_stat(user_id, "basketball", False)
        text = f"{e(EMOJI['basketball'])} <b>БАСКЕТБОЛ</b>\n\nВаш выбор: <b>{choice_text}</b>\nРезультат: <b>{result_desc}</b>\n\n{e(EMOJI['cross'])} <b>ПОРАЖЕНИЕ</b>\n\n{e(EMOJI['money'])} -{bet:.2f} USDT"
    
    new_balance = get_user(user_id)["balance"]
    text += f"\n{e(EMOJI['wallet'])} Баланс: <b>{new_balance:.2f} USDT</b>"
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=play_again_keyboard("basketball"))
    await callback.answer()

# ========== ФУТБОЛ ==========
@router.callback_query(F.data == "game_football")
async def game_football(callback: CallbackQuery):
    user = get_user(callback.from_user.id)
    text = f"""
{e(EMOJI['football'])} <b>ФУТБОЛ</b>

{e(EMOJI['wallet'])} Баланс: <b>{user['balance']:.2f} USDT</b>

Выберите режим:
"""
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=football_mode_keyboard())
    await callback.answer()

@router.callback_query(F.data.startswith("football_mode_"))
async def football_mode_selected(callback: CallbackQuery, state: FSMContext):
    mode = callback.data.replace("football_mode_", "")
    await state.update_data(football_mode=mode)
    user = get_user(callback.from_user.id)
    
    mults = {"goal": 1.85, "miss": 1.5, "penalty": 2, "post": 4}
    names = {"goal": "Гол", "miss": "Промах", "penalty": "Пенальти", "post": "Штанга/Перекладина"}
    
    text = f"""
{e(EMOJI['football'])} <b>ФУТБОЛ</b>

Режим: <b>{names[mode]}</b>
Множитель: <b>x{mults[mode]}</b>

{e(EMOJI['wallet'])} Баланс: <b>{user['balance']:.2f} USDT</b>

Выберите ставку:
"""
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=football_bet_keyboard())
    await callback.answer()

@router.callback_query(F.data.startswith("football_bet_"))
async def football_set_bet(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    bet_type = parts[2]
    user_id = callback.from_user.id
    user = get_user(user_id)
    data = await state.get_data()
    mode = data.get("football_mode", "goal")
    
    if bet_type == "custom":
        await callback.message.edit_text(
            f"{e(EMOJI['football'])} <b>ФУТБОЛ</b>\n\n{e(EMOJI['wallet'])} Баланс: {user['balance']:.2f} USDT\n\nВведите сумму ставки (мин. 0.1):",
            parse_mode=ParseMode.HTML
        )
        user_bets[user_id] = {"game": "football", "mode": mode, "awaiting_custom": True}
        await callback.answer()
        return
    
    bet = float(bet_type)
    if user["balance"] < bet:
        await callback.answer("Недостаточно средств!", show_alert=True)
        return
    if bet < 0.1:
        await callback.answer("Минимальная ставка 0.1 USDT", show_alert=True)
        return
    
    user_bets[user_id] = {"game": "football", "mode": mode, "bet": bet, "awaiting_custom": False}
    await football_play_auto(callback, user_id, bet, mode)
    await callback.answer()

async def football_play_auto(callback: CallbackQuery, user_id: int, bet: float, mode: str):
    user = get_user(user_id)
    mults = {"goal": 1.85, "miss": 1.5, "penalty": 2, "post": 4}
    mult = mults[mode]
    names = {"goal": "Гол", "miss": "Промах", "penalty": "Пенальти", "post": "Штанга/Перекладина"}
    
    if mode == "post":
        is_win = random.random() < 0.1
        result = "Штанга/Перекладина!" if is_win else "Мимо!"
    elif mode == "penalty":
        is_win = random.random() < 0.3
        result = "ГОЛ с пенальти!" if is_win else "Вратарь взял!"
    else:
        is_win = random.random() < 0.2
        if mode == "goal":
            result = "ГОЛ!" if is_win else "Промах!"
        else:
            result = "Промах!" if is_win else "ГОЛ!"
    
    if is_win:
        win_amount = bet * mult
        update_balance(user_id, win_amount - bet)
        add_game_stat(user_id, "football", True)
        text = f"{e(EMOJI['football'])} <b>ФУТБОЛ</b>\n\nРежим: <b>{names[mode]}</b>\nРезультат: <b>{result}</b>\n\n{e(EMOJI['check'])} <b>ПОБЕДА!</b>\n\n{e(EMOJI['money'])} +{win_amount - bet:.2f} USDT"
    else:
        update_balance(user_id, -bet)
        add_game_stat(user_id, "football", False)
        text = f"{e(EMOJI['football'])} <b>ФУТБОЛ</b>\n\nРежим: <b>{names[mode]}</b>\nРезультат: <b>{result}</b>\n\n{e(EMOJI['cross'])} <b>ПОРАЖЕНИЕ</b>\n\n{e(EMOJI['money'])} -{bet:.2f} USDT"
    
    new_balance = get_user(user_id)["balance"]
    text += f"\n{e(EMOJI['wallet'])} Баланс: <b>{new_balance:.2f} USDT</b>"
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=play_again_keyboard("football"))

@router.callback_query(F.data == "football_change_bet")
async def football_change_bet(callback: CallbackQuery, state: FSMContext):
    user = get_user(callback.from_user.id)
    data = await state.get_data()
    mode = data.get("football_mode", "goal")
    mults = {"goal": 1.85, "miss": 1.5, "penalty": 2, "post": 4}
    names = {"goal": "Гол", "miss": "Промах", "penalty": "Пенальти", "post": "Штанга/Перекладина"}
    
    text = f"""
{e(EMOJI['football'])} <b>ФУТБОЛ</b>

Режим: <b>{names[mode]}</b>
Множитель: <b>x{mults[mode]}</b>

{e(EMOJI['wallet'])} Баланс: <b>{user['balance']:.2f} USDT</b>

Выберите ставку:
"""
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=football_bet_keyboard())
    await callback.answer()

# ========== СВОЯ СТАВКА ==========
@router.message(F.text.regexp(r"^\d+(\.\d+)?$"))
async def handle_custom_bet(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if user_id not in user_bets or not user_bets[user_id].get("awaiting_custom"):
        return
    
    try:
        bet = float(message.text.replace(",", "."))
        game = user_bets[user_id]["game"]
        user = get_user(user_id)
        
        if bet < 0.1:
            await message.answer(f"{e(EMOJI['cross'])} Минимум 0.1 USDT", parse_mode=ParseMode.HTML)
            return
        if user["balance"] < bet:
            await message.answer(f"{e(EMOJI['cross'])} Недостаточно средств!", parse_mode=ParseMode.HTML)
            return
        
        user_bets[user_id]["bet"] = bet
        user_bets[user_id]["awaiting_custom"] = False
        
        if game == "dice":
            mode = user_bets[user_id]["mode"]
            if mode in ["twodice", "threedice"]:
                await dice_play_auto_msg(message, user_id, bet, mode)
            else:
                text = f"{e(EMOJI['dice'])} <b>КУБИК</b>\n\n{e(EMOJI['money'])} Ставка: <b>{bet:.2f} USDT</b>\n{e(EMOJI['wallet'])} Баланс: <b>{user['balance']:.2f} USDT</b>\n\nВыберите вариант:"
                await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=dice_choice_keyboard(mode))
        elif game == "basketball":
            text = f"{e(EMOJI['basketball'])} <b>БАСКЕТБОЛ</b>\n\n{e(EMOJI['money'])} Ставка: <b>{bet:.2f} USDT</b>\n{e(EMOJI['wallet'])} Баланс: <b>{user['balance']:.2f} USDT</b>\n\nВыберите исход:"
            await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=basketball_choice_keyboard())
        elif game == "football":
            mode = user_bets[user_id]["mode"]
            await football_play_auto_msg(message, user_id, bet, mode)
    except ValueError:
        await message.answer(f"{e(EMOJI['cross'])} Введите число", parse_mode=ParseMode.HTML)

async def dice_play_auto_msg(message: Message, user_id: int, bet: float, mode: str):
    user = get_user(user_id)
    if mode == "twodice":
        d1, d2 = random.randint(1, 6), random.randint(1, 6)
        is_win = (d1 + d2) == 7
        mult = 4
        result = f"Кубики: {d1} + {d2} = {d1+d2}"
    else:
        d1, d2, d3 = random.randint(1, 6), random.randint(1, 6), random.randint(1, 6)
        total = d1 + d2 + d3
        is_win = 10 <= total <= 11
        mult = 3
        result = f"Кубики: {d1} + {d2} + {d3} = {total}"
    
    if is_win:
        win_amount = bet * mult
        update_balance(user_id, win_amount - bet)
        add_game_stat(user_id, "dice", True)
        text = f"{e(EMOJI['dice'])} <b>КУБИК</b>\n\n{result}\n\n{e(EMOJI['check'])} <b>ПОБЕДА!</b>\n\n{e(EMOJI['money'])} +{win_amount - bet:.2f} USDT"
    else:
        update_balance(user_id, -bet)
        add_game_stat(user_id, "dice", False)
        text = f"{e(EMOJI['dice'])} <b>КУБИК</b>\n\n{result}\n\n{e(EMOJI['cross'])} <b>ПОРАЖЕНИЕ</b>\n\n{e(EMOJI['money'])} -{bet:.2f} USDT"
    
    new_balance = get_user(user_id)["balance"]
    text += f"\n{e(EMOJI['wallet'])} Баланс: <b>{new_balance:.2f} USDT</b>"
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=play_again_keyboard("dice"))

async def football_play_auto_msg(message: Message, user_id: int, bet: float, mode: str):
    user = get_user(user_id)
    mults = {"goal": 1.85, "miss": 1.5, "penalty": 2, "post": 4}
    mult = mults[mode]
    names = {"goal": "Гол", "miss": "Промах", "penalty": "Пенальти", "post": "Штанга/Перекладина"}
    
    if mode == "post":
        is_win = random.random() < 0.1
        result = "Штанга/Перекладина!" if is_win else "Мимо!"
    elif mode == "penalty":
        is_win = random.random() < 0.3
        result = "ГОЛ с пенальти!" if is_win else "Вратарь взял!"
    else:
        is_win = random.random() < 0.2
        if mode == "goal":
            result = "ГОЛ!" if is_win else "Промах!"
        else:
            result = "Промах!" if is_win else "ГОЛ!"
    
    if is_win:
        win_amount = bet * mult
        update_balance(user_id, win_amount - bet)
        add_game_stat(user_id, "football", True)
        text = f"{e(EMOJI['football'])} <b>ФУТБОЛ</b>\n\nРежим: <b>{names[mode]}</b>\nРезультат: <b>{result}</b>\n\n{e(EMOJI['check'])} <b>ПОБЕДА!</b>\n\n{e(EMOJI['money'])} +{win_amount - bet:.2f} USDT"
    else:
        update_balance(user_id, -bet)
        add_game_stat(user_id, "football", False)
        text = f"{e(EMOJI['football'])} <b>ФУТБОЛ</b>\n\nРежим: <b>{names[mode]}</b>\nРезультат: <b>{result}</b>\n\n{e(EMOJI['cross'])} <b>ПОРАЖЕНИЕ</b>\n\n{e(EMOJI['money'])} -{bet:.2f} USDT"
    
    new_balance = get_user(user_id)["balance"]
    text += f"\n{e(EMOJI['wallet'])} Баланс: <b>{new_balance:.2f} USDT</b>"
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=play_again_keyboard("football"))

# ========== АДМИН-ПАНЕЛЬ ==========
@router.callback_query(F.data == "admin_panel")
async def admin_panel(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(f"{e(EMOJI['settings'])} <b>Админ-панель</b>\n\nВыберите действие:", parse_mode=ParseMode.HTML, reply_markup=admin_panel_keyboard())
    await callback.answer()

@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    db = get_all_users()
    total_users = len(db)
    total_balance = sum(u["balance"] for u in db.values())
    total_bets = sum(u["stats"][g]["wins"] + u["stats"][g]["losses"] for u in db.values() for g in ["dice", "basketball", "football"])
    total_wins = sum(u["stats"][g]["wins"] for u in db.values() for g in ["dice", "basketball", "football"])
    
    text = f"""
{e(EMOJI['stats'])} <b>СТАТИСТИКА</b>

{e(EMOJI['users'])} Пользователей: <b>{total_users}</b>
{e(EMOJI['wallet'])} Общий баланс: <b>{total_balance:.2f} USDT</b>

{e(EMOJI['dice'])} Ставок: <b>{total_bets}</b>
{e(EMOJI['check'])} Побед: <b>{total_wins}</b>
"""
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=back_to_admin_keyboard())
    await callback.answer()

@router.callback_query(F.data == "admin_users_list")
async def admin_users_list(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    db = get_all_users()
    if not db:
        text = f"{e(EMOJI['users'])} <b>Пользователей нет</b>"
    else:
        text = f"{e(EMOJI['users'])} <b>ПОЛЬЗОВАТЕЛИ:</b>\n\n"
        for uid, data in list(db.items())[:20]:
            name = data.get("first_name", "") or data.get("username", "") or uid
            text += f"• <code>{uid}</code> — {name} — {data['balance']:.2f} USDT\n"
        if len(db) > 20:
            text += f"\n... ещё {len(db) - 20}"
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=back_to_admin_keyboard())
    await callback.answer()

@router.callback_query(F.data == "admin_edit_balance")
async def admin_edit_balance_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        f"{e(EMOJI['edit'])} <b>ИЗМЕНЕНИЕ БАЛАНСА</b>\n\n{e(EMOJI['info'])} Отправьте в формате:\n<code>ID СУММА</code>\n\n<i>Пример: 123456789 100</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_keyboard()
    )
    await state.set_state(AdminStates.waiting_for_edit_balance)
    await callback.answer()

@router.message(AdminStates.waiting_for_edit_balance)
async def admin_edit_balance_process(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await state.clear()
        return
    try:
        parts = message.text.strip().split()
        if len(parts) != 2:
            raise ValueError("Неверный формат")
        
        user_id = int(parts[0])
        new_balance = float(parts[1].replace(",", "."))
        if new_balance < 0:
            raise ValueError("Отрицательный баланс")
        
        db = get_all_users()
        uid = str(user_id)
        if uid not in db:
            await message.answer(f"{e(EMOJI['cross'])} Пользователь с ID <code>{user_id}</code> не найден", parse_mode=ParseMode.HTML, reply_markup=cancel_keyboard())
            return
        
        old_balance, new_balance = set_balance(user_id, new_balance)
        name = db[uid].get("first_name", "") or db[uid].get("username", "") or user_id
        
        try:
            await bot.send_message(user_id, f"{e(EMOJI['wallet'])} <b>Баланс изменён!</b>\n\n{e(EMOJI['edit'])} Новый баланс: <b>{new_balance:.2f} USDT</b>", parse_mode=ParseMode.HTML)
        except:
            pass
        
        await message.answer(
            f"{e(EMOJI['check'])} <b>Баланс изменён!</b>\n\n{e(EMOJI['profile'])} {name}\n{e(EMOJI['info'])} ID: <code>{user_id}</code>\n{e(EMOJI['wallet'])} {old_balance:.2f} → <b>{new_balance:.2f} USDT</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_panel_keyboard()
        )
        await state.clear()
    except ValueError:
        await message.answer(f"{e(EMOJI['cross'])} Неверный формат. Используйте: <code>ID СУММА</code>", parse_mode=ParseMode.HTML, reply_markup=cancel_keyboard())

@router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(f"{e(EMOJI['broadcast'])} <b>РАССЫЛКА</b>\n\n{e(EMOJI['info'])} Отправьте сообщение для рассылки:", parse_mode=ParseMode.HTML, reply_markup=cancel_keyboard())
    await state.set_state(AdminStates.waiting_for_broadcast)
    await callback.answer()

@router.message(AdminStates.waiting_for_broadcast)
async def admin_broadcast_send(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await state.clear()
        return
    
    db = get_all_users()
    success = 0
    failed = 0
    await message.answer(f"{e(EMOJI['loading'])} <b>Рассылка...</b>", parse_mode=ParseMode.HTML)
    
    for user_id in db.keys():
        try:
            await bot.send_message(int(user_id), message.html_text or message.text, parse_mode=ParseMode.HTML)
            success += 1
            await asyncio.sleep(0.05)
        except:
            failed += 1
    
    await message.answer(f"{e(EMOJI['check'])} <b>Готово!</b>\n\n✅ {success}\n❌ {failed}", parse_mode=ParseMode.HTML, reply_markup=admin_panel_keyboard())
    await state.clear()

@router.callback_query(F.data == "close_admin")
async def close_admin(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.delete()
    await callback.answer()

# ========== ОБЩИЕ КОЛБЭКИ ==========
@router.callback_query(F.data == "cancel_action")
async def cancel_action(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    if callback.from_user.id in ADMIN_IDS:
        await callback.message.edit_text(f"{e(EMOJI['cross'])} <b>Отменено</b>", parse_mode=ParseMode.HTML, reply_markup=admin_panel_keyboard())
    else:
        await callback.message.delete()
        await callback.message.answer(f"{e(EMOJI['home'])} <b>Vest Casino</b>", parse_mode=ParseMode.HTML, reply_markup=main_menu_keyboard())
    await callback.answer()

@router.callback_query(F.data.startswith("check_payment_"))
async def check_payment(callback: CallbackQuery):
    invoice_id = int(callback.data.split("_")[2])
    invoice = await check_invoice(invoice_id)
    
    if invoice and invoice["status"] == "paid":
        amount = float(invoice["amount"])
        update_balance(callback.from_user.id, amount)
        new_balance = get_user(callback.from_user.id)["balance"]
        await callback.message.edit_text(
            f"{e(EMOJI['check'])} <b>ОПЛАЧЕНО!</b>\n\n{e(EMOJI['money'])} +{amount:.2f} USDT\n{e(EMOJI['wallet'])} Баланс: <b>{new_balance:.2f} USDT</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_to_menu_keyboard()
        )
    elif invoice and invoice["status"] == "active":
        await callback.answer("Счёт не оплачен", show_alert=True)
    else:
        await callback.answer("Счёт не найден", show_alert=True)

# ========== ЗАПУСК ==========
async def main():
    logger.info("Vest Casino bot started!")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
