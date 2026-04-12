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
import aiohttp

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Переменная окружения BOT_TOKEN не установлена!")

CRYPTO_BOT_API = "465788:AAOxwPgMIPTheqZpyAyN2JotJ9U8fREP7rl"
CRYPTO_API_URL = "https://pay.crypt.bot/api"

ADMIN_IDS = [7973988177]

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
}

def e(emoji_id):
    return f'<tg-emoji emoji-id="{emoji_id}">⚡</tg-emoji>'

# ========== FSM ==========
class DepositState(StatesGroup):
    waiting_for_amount = State()

class WithdrawState(StatesGroup):
    waiting_for_amount = State()
    waiting_for_wallet = State()

class AdminStates(StatesGroup):
    waiting_for_broadcast = State()
    waiting_for_edit_balance = State()

# ========== КЛАВИАТУРЫ ==========
def main_menu_keyboard():
    """Главное меню с премиум-эмодзи в кнопках"""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Профиль", icon_custom_emoji_id=EMOJI["profile"])],
            [KeyboardButton(text="Игры", icon_custom_emoji_id=EMOJI["games"])],
            [
                KeyboardButton(text="Пополнить", icon_custom_emoji_id=EMOJI["wallet"]),
                KeyboardButton(text="Вывод", icon_custom_emoji_id=EMOJI["withdraw"])
            ],
            [KeyboardButton(text="Помощь", icon_custom_emoji_id=EMOJI["info"])],
        ],
        resize_keyboard=True
    )

def games_menu_keyboard():
    """Меню выбора игр"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Кубик",
            callback_data="game_dice",
            icon_custom_emoji_id=EMOJI["dice"]
        )],
        [InlineKeyboardButton(
            text="Баскетбол",
            callback_data="game_basketball",
            icon_custom_emoji_id=EMOJI["basketball"]
        )],
        [InlineKeyboardButton(
            text="Футбол",
            callback_data="game_football",
            icon_custom_emoji_id=EMOJI["football"]
        )],
        [InlineKeyboardButton(
            text="Назад",
            callback_data="back_to_menu",
            icon_custom_emoji_id=EMOJI["back"]
        )]
    ])

def back_to_menu_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Назад в меню",
            callback_data="back_to_menu",
            icon_custom_emoji_id=EMOJI["back"]
        )]
    ])

def back_to_admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Назад",
            callback_data="admin_panel",
            icon_custom_emoji_id=EMOJI["back"]
        )]
    ])

def cancel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Отмена",
            callback_data="cancel_action",
            icon_custom_emoji_id=EMOJI["cross"]
        )]
    ])

def admin_panel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Рассылка",
            callback_data="admin_broadcast",
            icon_custom_emoji_id=EMOJI["broadcast"]
        )],
        [InlineKeyboardButton(
            text="Изменить баланс",
            callback_data="admin_edit_balance",
            icon_custom_emoji_id=EMOJI["edit"]
        )],
        [InlineKeyboardButton(
            text="Статистика",
            callback_data="admin_stats",
            icon_custom_emoji_id=EMOJI["stats"]
        )],
        [InlineKeyboardButton(
            text="Пользователи",
            callback_data="admin_users_list",
            icon_custom_emoji_id=EMOJI["users"]
        )],
        [InlineKeyboardButton(
            text="Закрыть",
            callback_data="close_admin",
            icon_custom_emoji_id=EMOJI["cross"]
        )]
    ])

def dice_bet_keyboard():
    """Клавиатура выбора ставки для кубика"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="0.1", callback_data="dice_bet_0.1", icon_custom_emoji_id=EMOJI["money"]),
            InlineKeyboardButton(text="0.5", callback_data="dice_bet_0.5", icon_custom_emoji_id=EMOJI["money"]),
            InlineKeyboardButton(text="1", callback_data="dice_bet_1", icon_custom_emoji_id=EMOJI["money"])
        ],
        [
            InlineKeyboardButton(text="5", callback_data="dice_bet_5", icon_custom_emoji_id=EMOJI["money"]),
            InlineKeyboardButton(text="10", callback_data="dice_bet_10", icon_custom_emoji_id=EMOJI["money"]),
            InlineKeyboardButton(text="50", callback_data="dice_bet_50", icon_custom_emoji_id=EMOJI["money"])
        ],
        [
            InlineKeyboardButton(text="Своя сумма", callback_data="dice_bet_custom", icon_custom_emoji_id=EMOJI["edit"])
        ],
        [
            InlineKeyboardButton(text="Назад", callback_data="back_to_games", icon_custom_emoji_id=EMOJI["back"])
        ]
    ])

def dice_choice_keyboard():
    """Выбор меньше/больше для кубика"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="1-3 (Меньше)", callback_data="dice_low", icon_custom_emoji_id="5373141891321699086"),
            InlineKeyboardButton(text="4-6 (Больше)", callback_data="dice_high", icon_custom_emoji_id="5373141891321699086")
        ],
        [
            InlineKeyboardButton(text="Изменить ставку", callback_data="dice_change_bet", icon_custom_emoji_id=EMOJI["edit"])
        ],
        [
            InlineKeyboardButton(text="Назад", callback_data="back_to_games", icon_custom_emoji_id=EMOJI["back"])
        ]
    ])

def game_bet_keyboard(game: str):
    """Клавиатура выбора ставки для баскетбола и футбола"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="0.1", callback_data=f"{game}_bet_0.1", icon_custom_emoji_id=EMOJI["money"]),
            InlineKeyboardButton(text="0.5", callback_data=f"{game}_bet_0.5", icon_custom_emoji_id=EMOJI["money"]),
            InlineKeyboardButton(text="1", callback_data=f"{game}_bet_1", icon_custom_emoji_id=EMOJI["money"])
        ],
        [
            InlineKeyboardButton(text="5", callback_data=f"{game}_bet_5", icon_custom_emoji_id=EMOJI["money"]),
            InlineKeyboardButton(text="10", callback_data=f"{game}_bet_10", icon_custom_emoji_id=EMOJI["money"]),
            InlineKeyboardButton(text="50", callback_data=f"{game}_bet_50", icon_custom_emoji_id=EMOJI["money"])
        ],
        [
            InlineKeyboardButton(text="Своя сумма", callback_data=f"{game}_bet_custom", icon_custom_emoji_id=EMOJI["edit"])
        ],
        [
            InlineKeyboardButton(text="Назад", callback_data="back_to_games", icon_custom_emoji_id=EMOJI["back"])
        ]
    ])

def game_action_keyboard(game: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Играть",
            callback_data=f"{game}_play",
            icon_custom_emoji_id=EMOJI[game]
        )],
        [InlineKeyboardButton(
            text="Изменить ставку",
            callback_data=f"{game}_change_bet",
            icon_custom_emoji_id=EMOJI["edit"]
        )],
        [InlineKeyboardButton(
            text="Назад",
            callback_data="back_to_games",
            icon_custom_emoji_id=EMOJI["back"]
        )]
    ])

def play_again_keyboard(game: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="Играть ещё",
            callback_data=f"{game}_change_bet" if game != "dice" else "dice_change_bet",
            icon_custom_emoji_id=EMOJI[game]
        )],
        [InlineKeyboardButton(
            text="В меню",
            callback_data="back_to_menu",
            icon_custom_emoji_id=EMOJI["home"]
        )]
    ])

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

async def create_transfer(user_id: int, amount: float, wallet: str):
    """Создание вывода средств"""
    async with aiohttp.ClientSession() as session:
        headers = {"Crypto-Pay-API-Token": CRYPTO_BOT_API}
        data = {
            "user_id": user_id,
            "asset": "USDT",
            "amount": str(amount),
            "spend_id": f"withdraw_{user_id}_{random.randint(1000, 9999)}",
            "comment": f"Вывод с Vest Casino для {wallet}"
        }
        async with session.post(f"{CRYPTO_API_URL}/transfer", headers=headers, json=data) as resp:
            if resp.status == 200:
                result = await resp.json()
                return result.get("ok", False)
            logger.error(f"Crypto Bot transfer error: {await resp.text()}")
            return False

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

{e(EMOJI['games'])} <b>Доступные игры:</b>
{e(EMOJI['dice'])} <b>Кубик</b> — угадай меньше (1-3) или больше (4-6), выигрыш x1.85
{e(EMOJI['basketball'])} <b>Баскетбол</b> — попади в кольцо (50+), выигрыш x1.5
{e(EMOJI['football'])} <b>Футбол</b> — забей гол (70+), выигрыш x3

{e(EMOJI['wallet'])} Твой баланс: <b>{user['balance']:.2f} USDT</b>
{e(EMOJI['info'])} Используй кнопки ниже для навигации
"""
    await message.answer(welcome_text, parse_mode=ParseMode.HTML, reply_markup=main_menu_keyboard())

@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer(f"{e(EMOJI['cross'])} У вас нет доступа", parse_mode=ParseMode.HTML)
        return
    
    await message.answer(
        f"{e(EMOJI['settings'])} <b>Админ-панель</b>\n\nВыберите действие:",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_panel_keyboard()
    )

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

{e(EMOJI['dice'])} <b>Кубик</b> — меньше (1-3) / больше (4-6) | x1.85
{e(EMOJI['basketball'])} <b>Баскетбол</b> — бросок 50+ | x1.5
{e(EMOJI['football'])} <b>Футбол</b> — удар 70+ | x3

Минимальная ставка: <b>0.1 USDT</b>
"""
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=games_menu_keyboard())

@router.message(F.text == "Пополнить")
async def deposit_start(message: Message, state: FSMContext):
    await message.answer(
        f"{e(EMOJI['wallet'])} <b>ПОПОЛНЕНИЕ БАЛАНСА</b>\n\n"
        f"Введите сумму в USDT (мин. 0.1):",
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
            await message.answer(
                text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text="Проверить оплату",
                        callback_data=f"check_payment_{invoice['invoice_id']}",
                        icon_custom_emoji_id=EMOJI["loading"]
                    )],
                    [InlineKeyboardButton(
                        text="Отмена",
                        callback_data="cancel_action",
                        icon_custom_emoji_id=EMOJI["cross"]
                    )]
                ])
            )
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
        f"{e(EMOJI['withdraw'])} <b>ВЫВОД СРЕДСТВ</b>\n\n"
        f"{e(EMOJI['wallet'])} Ваш баланс: <b>{user['balance']:.2f} USDT</b>\n\n"
        f"Введите сумму вывода (мин. 0.5):",
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
        
        await state.update_data(withdraw_amount=amount)
        await message.answer(
            f"{e(EMOJI['wallet'])} <b>Введите адрес кошелька USDT (TRC20):</b>\n\n"
            f"<i>Пример: TXxxxxxxxxxxxxxxxxxxxxxxxxxxxxx</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_keyboard()
        )
        await state.set_state(WithdrawState.waiting_for_wallet)
    except ValueError:
        await message.answer(f"{e(EMOJI['cross'])} Введите число", parse_mode=ParseMode.HTML)

@router.message(WithdrawState.waiting_for_wallet)
async def withdraw_wallet(message: Message, state: FSMContext):
    wallet = message.text.strip()
    
    if len(wallet) < 30:
        await message.answer(f"{e(EMOJI['cross'])} Некорректный адрес кошелька", parse_mode=ParseMode.HTML)
        return
    
    data = await state.get_data()
    amount = data['withdraw_amount']
    user_id = message.from_user.id
    
    success = await create_transfer(user_id, amount, wallet)
    
    if success:
        update_balance(user_id, -amount)
        new_balance = get_user(user_id)['balance']
        
        await message.answer(
            f"{e(EMOJI['check'])} <b>ВЫВОД УСПЕШЕН!</b>\n\n"
            f"{e(EMOJI['money'])} Сумма: <b>{amount:.2f} USDT</b>\n"
            f"{e(EMOJI['wallet'])} Кошелёк: <code>{wallet}</code>\n"
            f"{e(EMOJI['wallet'])} Новый баланс: <b>{new_balance:.2f} USDT</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_keyboard()
        )
    else:
        await message.answer(
            f"{e(EMOJI['cross'])} <b>ОШИБКА ВЫВОДА</b>\n\n"
            f"Попробуйте позже или обратитесь к администратору.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_keyboard()
        )
    
    await state.clear()

@router.message(F.text == "Помощь")
async def help_cmd(message: Message):
    text = f"""
{e(EMOJI['info'])} <b>ПОМОЩЬ</b>

{e(EMOJI['games'])} <b>ИГРЫ:</b>
{e(EMOJI['dice'])} <b>Кубик</b> — меньше (1-3) / больше (4-6) | x1.85
{e(EMOJI['basketball'])} <b>Баскетбол</b> — бросок 50+ | x1.5
{e(EMOJI['football'])} <b>Футбол</b> — удар 70+ | x3

{e(EMOJI['wallet'])} <b>ФИНАНСЫ:</b>
• Пополнение от 0.1 USDT
• Вывод от 0.5 USDT
• Ставки от 0.1 USDT

{e(EMOJI['settings'])} <b>Админ-панель:</b> /admin
"""
    await message.answer(text, parse_mode=ParseMode.HTML)

# ========== ОБРАБОТЧИКИ ИГР ==========
@router.callback_query(F.data == "back_to_games")
async def back_to_games(callback: CallbackQuery):
    text = f"""
{e(EMOJI['games'])} <b>ВЫБЕРИТЕ ИГРУ</b>

{e(EMOJI['dice'])} <b>Кубик</b> — меньше (1-3) / больше (4-6) | x1.85
{e(EMOJI['basketball'])} <b>Баскетбол</b> — бросок 50+ | x1.5
{e(EMOJI['football'])} <b>Футбол</b> — удар 70+ | x3

Минимальная ставка: <b>0.1 USDT</b>
"""
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=games_menu_keyboard())
    await callback.answer()

@router.callback_query(F.data == "game_dice")
async def game_dice(callback: CallbackQuery):
    user = get_user(callback.from_user.id)
    text = f"""
{e(EMOJI['dice'])} <b>КУБИК</b>

{e(EMOJI['wallet'])} Баланс: <b>{user['balance']:.2f} USDT</b>
{e(EMOJI['money'])} Множитель: <b>x1.85</b>

Угадайте: <b>1-3 (Меньше)</b> или <b>4-6 (Больше)</b>

Выберите ставку:
"""
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=dice_bet_keyboard())
    await callback.answer()

@router.callback_query(F.data.startswith("dice_bet_"))
async def dice_set_bet(callback: CallbackQuery):
    parts = callback.data.split("_")
    bet_type = parts[2]
    user_id = callback.from_user.id
    user = get_user(user_id)
    
    if bet_type == "custom":
        await callback.message.edit_text(
            f"{e(EMOJI['dice'])} <b>КУБИК</b>\n\n"
            f"{e(EMOJI['wallet'])} Баланс: {user['balance']:.2f} USDT\n\n"
            f"Введите сумму ставки (мин. 0.1):",
            parse_mode=ParseMode.HTML
        )
        user_bets[user_id] = {"game": "dice", "awaiting_custom": True}
        await callback.answer()
        return
    
    bet = float(bet_type)
    
    if user["balance"] < bet:
        await callback.answer("Недостаточно средств!", show_alert=True)
        return
    
    if bet < 0.1:
        await callback.answer("Минимальная ставка 0.1 USDT", show_alert=True)
        return
    
    user_bets[user_id] = {"game": "dice", "bet": bet, "awaiting_custom": False}
    
    text = f"""
{e(EMOJI['dice'])} <b>КУБИК</b>

{e(EMOJI['money'])} Ставка: <b>{bet:.2f} USDT</b>
{e(EMOJI['wallet'])} Баланс: <b>{user['balance']:.2f} USDT</b>

Выберите вариант:
"""
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=dice_choice_keyboard())
    await callback.answer()

@router.callback_query(F.data == "dice_change_bet")
async def dice_change_bet(callback: CallbackQuery):
    user = get_user(callback.from_user.id)
    text = f"""
{e(EMOJI['dice'])} <b>КУБИК</b>

{e(EMOJI['wallet'])} Баланс: <b>{user['balance']:.2f} USDT</b>
{e(EMOJI['money'])} Множитель: <b>x1.85</b>

Выберите ставку:
"""
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=dice_bet_keyboard())
    await callback.answer()

@router.callback_query(F.data.in_(["dice_low", "dice_high"]))
async def dice_play(callback: CallbackQuery):
    user_id = callback.from_user.id
    
    if user_id not in user_bets:
        await callback.answer("Сначала выберите ставку!", show_alert=True)
        return
    
    bet_data = user_bets[user_id]
    if bet_data["game"] != "dice":
        await callback.answer("Ошибка", show_alert=True)
        return
    
    bet = bet_data["bet"]
    user = get_user(user_id)
    
    if user["balance"] < bet:
        await callback.answer("Недостаточно средств!", show_alert=True)
        return
    
    choice = callback.data  # dice_low или dice_high
    roll = random.randint(1, 6)
    
    if choice == "dice_low":
        is_win = roll <= 3
        choice_text = "Меньше (1-3)"
    else:
        is_win = roll >= 4
        choice_text = "Больше (4-6)"
    
    win_amount = bet * 1.85 if is_win else 0
    
    result_text = f"""
{e(EMOJI['dice'])} <b>КУБИК</b>

Ваш выбор: <b>{choice_text}</b>
{e(EMOJI['dice'])} Выпало: <b>{roll}</b>

"""
    
    if is_win:
        update_balance(user_id, win_amount)
        add_game_stat(user_id, "dice", True)
        result_text += f"""
{e(EMOJI['check'])} <b>ПОБЕДА!</b>

{e(EMOJI['money'])} +{win_amount:.2f} USDT
"""
    else:
        update_balance(user_id, -bet)
        add_game_stat(user_id, "dice", False)
        result_text += f"""
{e(EMOJI['cross'])} <b>ПОРАЖЕНИЕ</b>

{e(EMOJI['money'])} -{bet:.2f} USDT
"""
    
    new_balance = get_user(user_id)["balance"]
    result_text += f"\n{e(EMOJI['wallet'])} Баланс: <b>{new_balance:.2f} USDT</b>"
    
    await callback.message.edit_text(result_text, parse_mode=ParseMode.HTML, reply_markup=play_again_keyboard("dice"))
    await callback.answer()

@router.callback_query(F.data.startswith("game_"))
async def game_selected(callback: CallbackQuery):
    game = callback.data.split("_")[1]  # basketball или football
    user = get_user(callback.from_user.id)
    
    game_names = {
        "basketball": f"{e(EMOJI['basketball'])} Баскетбол",
        "football": f"{e(EMOJI['football'])} Футбол"
    }
    
    multipliers = {"basketball": 1.5, "football": 3}
    
    text = f"""
{game_names[game]} 

{e(EMOJI['wallet'])} Баланс: <b>{user['balance']:.2f} USDT</b>
{e(EMOJI['money'])} Множитель: <b>x{multipliers[game]}</b>

Выберите ставку (мин. 0.1):
"""
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=game_bet_keyboard(game))
    await callback.answer()

@router.callback_query(F.data.startswith("basketball_bet_"))
@router.callback_query(F.data.startswith("football_bet_"))
async def set_bet(callback: CallbackQuery):
    parts = callback.data.split("_")
    game = parts[0]
    bet_type = parts[2]
    
    user_id = callback.from_user.id
    user = get_user(user_id)
    
    game_names = {
        "basketball": f"{e(EMOJI['basketball'])} Баскетбол",
        "football": f"{e(EMOJI['football'])} Футбол"
    }
    
    if bet_type == "custom":
        await callback.message.edit_text(
            f"{game_names[game]}\n\n{e(EMOJI['wallet'])} Баланс: {user['balance']:.2f} USDT\n\nВведите сумму ставки (мин. 0.1):",
            parse_mode=ParseMode.HTML
        )
        user_bets[user_id] = {"game": game, "awaiting_custom": True}
        await callback.answer()
        return
    
    bet = float(bet_type)
    
    if user["balance"] < bet:
        await callback.answer("Недостаточно средств!", show_alert=True)
        return
    
    if bet < 0.1:
        await callback.answer("Минимальная ставка 0.1 USDT", show_alert=True)
        return
    
    user_bets[user_id] = {"game": game, "bet": bet, "awaiting_custom": False}
    
    text = f"""
{game_names[game]}

{e(EMOJI['money'])} Ставка: <b>{bet:.2f} USDT</b>
{e(EMOJI['wallet'])} Баланс: <b>{user['balance']:.2f} USDT</b>

Готовы играть?
"""
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=game_action_keyboard(game))
    await callback.answer()

@router.message(F.text.regexp(r"^\d+(\.\d+)?$"))
async def handle_custom_bet(message: Message):
    user_id = message.from_user.id
    
    if user_id not in user_bets or not user_bets[user_id].get("awaiting_custom"):
        return
    
    try:
        bet = float(message.text.replace(",", "."))
        game = user_bets[user_id]["game"]
        user = get_user(user_id)
        
        if game == "dice":
            game_names = {"dice": f"{e(EMOJI['dice'])} Кубик"}
        else:
            game_names = {
                "basketball": f"{e(EMOJI['basketball'])} Баскетбол",
                "football": f"{e(EMOJI['football'])} Футбол"
            }
        
        if bet < 0.1:
            await message.answer(f"{e(EMOJI['cross'])} Минимум 0.1 USDT", parse_mode=ParseMode.HTML)
            return
        
        if user["balance"] < bet:
            await message.answer(f"{e(EMOJI['cross'])} Недостаточно средств!", parse_mode=ParseMode.HTML)
            return
        
        user_bets[user_id] = {"game": game, "bet": bet, "awaiting_custom": False}
        
        if game == "dice":
            text = f"""
{e(EMOJI['dice'])} <b>КУБИК</b>

{e(EMOJI['money'])} Ставка: <b>{bet:.2f} USDT</b>
{e(EMOJI['wallet'])} Баланс: <b>{user['balance']:.2f} USDT</b>

Выберите вариант:
"""
            await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=dice_choice_keyboard())
        else:
            text = f"""
{game_names[game]}

{e(EMOJI['money'])} Ставка: <b>{bet:.2f} USDT</b>
{e(EMOJI['wallet'])} Баланс: <b>{user['balance']:.2f} USDT</b>

Готовы играть?
"""
            await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=game_action_keyboard(game))
        
    except ValueError:
        await message.answer(f"{e(EMOJI['cross'])} Введите число", parse_mode=ParseMode.HTML)

@router.callback_query(F.data.endswith("_play"))
async def play_game(callback: CallbackQuery):
    parts = callback.data.split("_")
    game = parts[0]
    user_id = callback.from_user.id
    
    if user_id not in user_bets:
        await callback.answer("Сначала выберите ставку!", show_alert=True)
        return
    
    bet_data = user_bets[user_id]
    if bet_data["game"] != game:
        await callback.answer("Ошибка", show_alert=True)
        return
    
    bet = bet_data["bet"]
    user = get_user(user_id)
    
    if user["balance"] < bet:
        await callback.answer("Недостаточно средств!", show_alert=True)
        return
    
    game_names = {
        "basketball": f"{e(EMOJI['basketball'])} Баскетбол",
        "football": f"{e(EMOJI['football'])} Футбол"
    }
    
    if game == "basketball":
        score = random.randint(0, 100)
        is_win = score >= 50
        win_amount = bet * 1.5 if is_win else 0
        result_text = f"""
{game_names[game]}

{e(EMOJI['basketball'])} Бросок: <b>{score}</b>
{e(EMOJI['info'])} Нужно: 50+

"""
    else:
        score = random.randint(0, 100)
        is_win = score >= 70
        win_amount = bet * 3 if is_win else 0
        result_text = f"""
{game_names[game]}

{e(EMOJI['football'])} Удар: <b>{score}</b>
{e(EMOJI['info'])} Нужно: 70+

"""
    
    if is_win:
        update_balance(user_id, win_amount)
        add_game_stat(user_id, game, True)
        result_text += f"""
{e(EMOJI['check'])} <b>ПОБЕДА!</b>

{e(EMOJI['money'])} +{win_amount:.2f} USDT
"""
    else:
        update_balance(user_id, -bet)
        add_game_stat(user_id, game, False)
        result_text += f"""
{e(EMOJI['cross'])} <b>ПОРАЖЕНИЕ</b>

{e(EMOJI['money'])} -{bet:.2f} USDT
"""
    
    new_balance = get_user(user_id)["balance"]
    result_text += f"\n{e(EMOJI['wallet'])} Баланс: <b>{new_balance:.2f} USDT</b>"
    
    await callback.message.edit_text(result_text, parse_mode=ParseMode.HTML, reply_markup=play_again_keyboard(game))
    await callback.answer()

@router.callback_query(F.data.endswith("_change_bet"))
async def change_bet(callback: CallbackQuery):
    game = callback.data.split("_")[0]
    user = get_user(callback.from_user.id)
    
    game_names = {
        "basketball": f"{e(EMOJI['basketball'])} Баскетбол",
        "football": f"{e(EMOJI['football'])} Футбол"
    }
    
    multipliers = {"basketball": 1.5, "football": 3}
    
    text = f"""
{game_names[game]} 

{e(EMOJI['wallet'])} Баланс: <b>{user['balance']:.2f} USDT</b>
{e(EMOJI['money'])} Множитель: <b>x{multipliers[game]}</b>

Выберите ставку (мин. 0.1):
"""
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=game_bet_keyboard(game))
    await callback.answer()

# ========== АДМИН-ПАНЕЛЬ ==========
@router.callback_query(F.data == "admin_panel")
async def admin_panel(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    
    await callback.message.edit_text(
        f"{e(EMOJI['settings'])} <b>Админ-панель</b>\n\nВыберите действие:",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_panel_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    
    db = get_all_users()
    total_users = len(db)
    total_balance = sum(u["balance"] for u in db.values())
    
    total_bets = 0
    total_wins = 0
    for u in db.values():
        for game in ["dice", "basketball", "football"]:
            total_bets += u["stats"][game]["wins"] + u["stats"][game]["losses"]
            total_wins += u["stats"][game]["wins"]
    
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
        f"{e(EMOJI['edit'])} <b>ИЗМЕНЕНИЕ БАЛАНСА</b>\n\n"
        f"{e(EMOJI['info'])} Отправьте в формате:\n"
        f"<code>ID СУММА</code>\n\n"
        f"<i>Пример: 123456789 100</i>",
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
            await message.answer(
                f"{e(EMOJI['cross'])} Баланс не может быть отрицательным",
                parse_mode=ParseMode.HTML,
                reply_markup=cancel_keyboard()
            )
            return
        
        db = get_all_users()
        uid = str(user_id)
        
        if uid not in db:
            await message.answer(
                f"{e(EMOJI['cross'])} Пользователь с ID <code>{user_id}</code> не найден",
                parse_mode=ParseMode.HTML,
                reply_markup=cancel_keyboard()
            )
            return
        
        old_balance, new_balance = set_balance(user_id, new_balance)
        user_data = db[uid]
        name = user_data.get("first_name", "") or user_data.get("username", "") or user_id
        
        try:
            await bot.send_message(
                user_id,
                f"{e(EMOJI['wallet'])} <b>Баланс изменён!</b>\n\n"
                f"{e(EMOJI['edit'])} Новый баланс: <b>{new_balance:.2f} USDT</b>",
                parse_mode=ParseMode.HTML
            )
        except:
            pass
        
        await message.answer(
            f"{e(EMOJI['check'])} <b>Баланс изменён!</b>\n\n"
            f"{e(EMOJI['profile'])} {name}\n"
            f"{e(EMOJI['info'])} ID: <code>{user_id}</code>\n"
            f"{e(EMOJI['wallet'])} {old_balance:.2f} → <b>{new_balance:.2f} USDT</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_panel_keyboard()
        )
        await state.clear()
        
    except ValueError:
        await message.answer(
            f"{e(EMOJI['cross'])} Неверный формат. Используйте: <code>ID СУММА</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_keyboard()
        )

@router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    
    await callback.message.edit_text(
        f"{e(EMOJI['broadcast'])} <b>РАССЫЛКА</b>\n\n"
        f"{e(EMOJI['info'])} Отправьте сообщение для рассылки:",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_keyboard()
    )
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
            await bot.send_message(
                int(user_id),
                message.html_text if message.html_text else message.text,
                parse_mode=ParseMode.HTML
            )
            success += 1
            await asyncio.sleep(0.05)
        except:
            failed += 1
    
    await message.answer(
        f"{e(EMOJI['check'])} <b>Готово!</b>\n\n✅ {success}\n❌ {failed}",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_panel_keyboard()
    )
    await state.clear()

@router.callback_query(F.data == "close_admin")
async def close_admin(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    
    await callback.message.delete()
    await callback.answer()

# ========== ОБЩИЕ КОЛБЭКИ ==========
@router.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery):
    await callback.message.delete()
    await callback.message.answer(
        f"{e(EMOJI['home'])} <b>Vest Casino</b>\n\nВыберите действие:",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "cancel_action")
async def cancel_action(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    if callback.from_user.id in ADMIN_IDS:
        await callback.message.edit_text(
            f"{e(EMOJI['cross'])} <b>Отменено</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_panel_keyboard()
        )
    else:
        await callback.message.delete()
        await callback.message.answer(
            f"{e(EMOJI['home'])} <b>Vest Casino</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_keyboard()
        )
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
            f"{e(EMOJI['check'])} <b>ОПЛАЧЕНО!</b>\n\n"
            f"{e(EMOJI['money'])} +{amount:.2f} USDT\n"
            f"{e(EMOJI['wallet'])} Баланс: <b>{new_balance:.2f} USDT</b>",
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
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
