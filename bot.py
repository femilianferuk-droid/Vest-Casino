import os
import json
import asyncio
import logging
from datetime import datetime
from typing import Optional
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.filters import Command, StateFilter
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

ADMIN_IDS = [7973988177]  # Твой ID

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

def add_game_stat(user_id, game, is_win, amount):
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
    "send_money": "5890848474563352982",
    "receive_money": "5879814368572478751",
    "check": "5870633910337015697",
    "cross": "5870657884844462243",
    "back": "6037249452824072506",
    "info": "6028435952299413210",
    "gift": "6032644646587338669",
    "stats": "5870921681735781843",
    "crypto": "5260752406890711732",
    "graph": "5870930636742595124",
    "home": "5873147866364514353",
    "edit": "5870676941614354370",
    "trash": "5870875489362513438",
    "users": "5870772616305839506",
    "plus": "5891207662678317861",
    "minus": "5893192487324880883",
}

def e(id): return f'<tg-emoji emoji-id="{id}">⚡</tg-emoji>'

# ========== FSM ==========
class DepositState(StatesGroup):
    waiting_for_amount = State()

class AdminStates(StatesGroup):
    waiting_for_broadcast = State()
    waiting_for_edit_balance_user = State()
    waiting_for_edit_balance_amount = State()

# ========== КЛАВИАТУРЫ ==========
def main_menu_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=f"{e(EMOJI['profile'])} Профиль")],
            [KeyboardButton(text=f"{e(EMOJI['dice'])} Кубик"), 
             KeyboardButton(text=f"{e(EMOJI['basketball'])} Баскетбол")],
            [KeyboardButton(text=f"{e(EMOJI['football'])} Футбол")],
            [KeyboardButton(text=f"{e(EMOJI['wallet'])} Пополнить"), 
             KeyboardButton(text=f"{e(EMOJI['info'])} Помощь")],
        ],
        resize_keyboard=True
    )

def back_to_menu_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="◁ Назад в меню",
            callback_data="back_to_menu",
            icon_custom_emoji_id=EMOJI["back"]
        )]
    ])

def back_to_admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="◁ Назад",
            callback_data="admin_panel",
            icon_custom_emoji_id=EMOJI["back"]
        )]
    ])

def cancel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="❌ Отмена",
            callback_data="cancel_action",
            icon_custom_emoji_id=EMOJI["cross"]
        )]
    ])

def admin_panel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📢 Рассылка",
            callback_data="admin_broadcast",
            icon_custom_emoji_id="5370599459661045441"
        )],
        [InlineKeyboardButton(
            text=f"{e(EMOJI['edit'])} Изменить баланс",
            callback_data="admin_edit_balance",
            icon_custom_emoji_id=EMOJI["edit"]
        )],
        [InlineKeyboardButton(
            text=f"{e(EMOJI['stats'])} Статистика",
            callback_data="admin_stats",
            icon_custom_emoji_id=EMOJI["stats"]
        )],
        [InlineKeyboardButton(
            text=f"{e(EMOJI['users'])} Все пользователи",
            callback_data="admin_users_list",
            icon_custom_emoji_id=EMOJI["users"]
        )],
        [InlineKeyboardButton(
            text="◁ Закрыть",
            callback_data="close_admin",
            icon_custom_emoji_id=EMOJI["cross"]
        )]
    ])

def game_bet_keyboard(game: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="10", callback_data=f"{game}_bet_10"),
         InlineKeyboardButton(text="50", callback_data=f"{game}_bet_50"),
         InlineKeyboardButton(text="100", callback_data=f"{game}_bet_100")],
        [InlineKeyboardButton(text="250", callback_data=f"{game}_bet_250"),
         InlineKeyboardButton(text="500", callback_data=f"{game}_bet_500"),
         InlineKeyboardButton(text="1000", callback_data=f"{game}_bet_1000")],
        [InlineKeyboardButton(text="Своя сумма", callback_data=f"{game}_bet_custom")],
        [InlineKeyboardButton(
            text="◁ Назад",
            callback_data="back_to_menu",
            icon_custom_emoji_id=EMOJI["back"]
        )]
    ])

def game_action_keyboard(game: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🎲 Играть",
            callback_data=f"{game}_play"
        )],
        [InlineKeyboardButton(
            text="💰 Изменить ставку",
            callback_data=f"{game}_change_bet"
        )],
        [InlineKeyboardButton(
            text="◁ Назад к играм",
            callback_data="back_to_menu"
        )]
    ])

# ========== CRYPTO BOT API ==========
async def create_invoice(amount: float):
    async with aiohttp.ClientSession() as session:
        headers = {"Crypto-Pay-API-Token": CRYPTO_BOT_API}
        data = {
            "asset": "USDT",
            "amount": str(amount),
            "description": "Пополнение баланса в казино",
            "paid_btn_name": "callback",
            "paid_btn_url": "https://t.me/your_bot"
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

# ========== ИНИЦИАЛИЗАЦИЯ ==========
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

# ========== ХРАНЕНИЕ ВРЕМЕННЫХ ДАННЫХ ==========
user_bets = {}  # user_id: {"game": str, "bet": float}
user_invoices = {}  # user_id: invoice_id

# ========== КОМАНДЫ ==========
@router.message(Command("start"))
async def cmd_start(message: Message):
    update_user_info(message.from_user.id, message.from_user.username, message.from_user.first_name)
    welcome_text = f"""
{e(EMOJI['home'])} <b>Добро пожаловать в Crypto Casino!</b>

{e(EMOJI['dice'])} <b>Кубик</b> — угадай число от 1 до 6, выигрыш x2
{e(EMOJI['basketball'])} <b>Баскетбол</b> — попади в кольцо, выигрыш x1.5
{e(EMOJI['football'])} <b>Футбол</b> — забей гол, выигрыш x3

{e(EMOJI['wallet'])} Твой баланс: <b>0.00 USDT</b>
{e(EMOJI['info'])} Используй кнопки ниже для навигации
"""
    await message.answer(welcome_text, parse_mode=ParseMode.HTML, reply_markup=main_menu_keyboard())

@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer(f"{e(EMOJI['cross'])} У вас нет доступа к админ-панели", parse_mode=ParseMode.HTML)
        return
    
    await message.answer(
        f"{e(EMOJI['settings'])} <b>Админ-панель</b>\n\nВыберите действие:",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_panel_keyboard()
    )

@router.message(F.text == f"{e(EMOJI['profile'])} Профиль")
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

@router.message(F.text == f"{e(EMOJI['wallet'])} Пополнить")
async def deposit_start(message: Message, state: FSMContext):
    await message.answer(
        f"{e(EMOJI['wallet'])} <b>ПОПОЛНЕНИЕ БАЛАНСА</b>\n\n"
        f"Введите сумму пополнения в USDT (минимум 1 USDT):",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_keyboard()
    )
    await state.set_state(DepositState.waiting_for_amount)

@router.message(DepositState.waiting_for_amount)
async def deposit_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.replace(",", "."))
        if amount < 1:
            await message.answer(f"{e(EMOJI['cross'])} Минимальная сумма пополнения — 1 USDT", parse_mode=ParseMode.HTML)
            return
        
        invoice = await create_invoice(amount)
        if invoice:
            user_invoices[message.from_user.id] = invoice["invoice_id"]
            
            text = f"""
{e(EMOJI['crypto'])} <b>СЧЁТ НА ОПЛАТУ</b>

{e(EMOJI['money'])} Сумма: <b>{amount:.2f} USDT</b>
{e(EMOJI['info'])} Статус: ожидает оплаты

{e(EMOJI['send_money'])} <b>Ссылка для оплаты:</b>
<code>{invoice['pay_url']}</code>

Нажмите кнопку ниже, чтобы проверить оплату:
"""
            await message.answer(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text="🔄 Проверить оплату",
                        callback_data=f"check_payment_{invoice['invoice_id']}",
                        icon_custom_emoji_id="5345906554510012647"
                    )],
                    [InlineKeyboardButton(
                        text="◁ Отмена",
                        callback_data="cancel_action",
                        icon_custom_emoji_id=EMOJI["cross"]
                    )]
                ])
            )
        else:
            await message.answer(f"{e(EMOJI['cross'])} Ошибка создания счёта", parse_mode=ParseMode.HTML)
    except ValueError:
        await message.answer(f"{e(EMOJI['cross'])} Введите корректное число", parse_mode=ParseMode.HTML)
    
    await state.clear()

@router.message(F.text == f"{e(EMOJI['info'])} Помощь")
async def help_cmd(message: Message):
    text = f"""
{e(EMOJI['info'])} <b>ПОМОЩЬ</b>

{e(EMOJI['dice'])} <b>Кубик</b>
Угадайте число от 1 до 6. При выигрыше ставка умножается на 2.

{e(EMOJI['basketball'])} <b>Баскетбол</b>
Попадите в кольцо (нужно 50+ очков). Выигрыш x1.5.

{e(EMOJI['football'])} <b>Футбол</b>
Забейте гол (нужно 70+ очков). Выигрыш x3.

{e(EMOJI['wallet'])} <b>Пополнение</b>
Через Crypto Bot, минимальная сумма 1 USDT.

{e(EMOJI['settings'])} <b>Админ-панель:</b> /admin
"""
    await message.answer(text, parse_mode=ParseMode.HTML)

# ========== ИГРЫ ==========
@router.message(F.text.in_([
    f"{e(EMOJI['dice'])} Кубик",
    f"{e(EMOJI['basketball'])} Баскетбол",
    f"{e(EMOJI['football'])} Футбол"
]))
async def game_selected(message: Message):
    game_map = {
        f"{e(EMOJI['dice'])} Кубик": "dice",
        f"{e(EMOJI['basketball'])} Баскетбол": "basketball",
        f"{e(EMOJI['football'])} Футбол": "football"
    }
    game = game_map[message.text]
    
    game_names = {
        "dice": f"{e(EMOJI['dice'])} Кубик",
        "basketball": f"{e(EMOJI['basketball'])} Баскетбол",
        "football": f"{e(EMOJI['football'])} Футбол"
    }
    
    multipliers = {"dice": 2, "basketball": 1.5, "football": 3}
    
    user = get_user(message.from_user.id)
    
    text = f"""
{game_names[game]} 

{e(EMOJI['wallet'])} Ваш баланс: <b>{user['balance']:.2f} USDT</b>
{e(EMOJI['money'])} Множитель выигрыша: <b>x{multipliers[game]}</b>

Выберите сумму ставки:
"""
    await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=game_bet_keyboard(game))

@router.callback_query(F.data.startswith("dice_bet_"))
@router.callback_query(F.data.startswith("basketball_bet_"))
@router.callback_query(F.data.startswith("football_bet_"))
async def set_bet(callback: CallbackQuery):
    parts = callback.data.split("_")
    game = parts[0]
    bet_type = parts[2]
    
    user_id = callback.from_user.id
    user = get_user(user_id)
    
    game_names = {
        "dice": f"{e(EMOJI['dice'])} Кубик",
        "basketball": f"{e(EMOJI['basketball'])} Баскетбол",
        "football": f"{e(EMOJI['football'])} Футбол"
    }
    
    if bet_type == "custom":
        await callback.message.edit_text(
            f"{game_names[game]}\n\n{e(EMOJI['wallet'])} Ваш баланс: {user['balance']:.2f} USDT\n\nВведите сумму ставки:",
            parse_mode=ParseMode.HTML
        )
        user_bets[user_id] = {"game": game, "awaiting_custom": True}
        await callback.answer()
        return
    
    bet = float(bet_type)
    
    if user["balance"] < bet:
        await callback.answer("❌ Недостаточно средств!", show_alert=True)
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
        
        game_names = {
            "dice": f"{e(EMOJI['dice'])} Кубик",
            "basketball": f"{e(EMOJI['basketball'])} Баскетбол",
            "football": f"{e(EMOJI['football'])} Футбол"
        }
        
        if bet < 1:
            await message.answer(f"{e(EMOJI['cross'])} Минимальная ставка — 1 USDT", parse_mode=ParseMode.HTML)
            return
        
        if user["balance"] < bet:
            await message.answer(f"{e(EMOJI['cross'])} Недостаточно средств!", parse_mode=ParseMode.HTML)
            return
        
        user_bets[user_id] = {"game": game, "bet": bet, "awaiting_custom": False}
        
        text = f"""
{game_names[game]}

{e(EMOJI['money'])} Ставка: <b>{bet:.2f} USDT</b>
{e(EMOJI['wallet'])} Баланс: <b>{user['balance']:.2f} USDT</b>

Готовы играть?
"""
        await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=game_action_keyboard(game))
        
    except ValueError:
        await message.answer(f"{e(EMOJI['cross'])} Введите корректное число", parse_mode=ParseMode.HTML)

@router.callback_query(F.data.endswith("_play"))
async def play_game(callback: CallbackQuery):
    parts = callback.data.split("_")
    game = parts[0]
    user_id = callback.from_user.id
    
    if user_id not in user_bets:
        await callback.answer("❌ Сначала выберите ставку!", show_alert=True)
        return
    
    bet_data = user_bets[user_id]
    if bet_data["game"] != game:
        await callback.answer("❌ Ошибка данных игры", show_alert=True)
        return
    
    bet = bet_data["bet"]
    user = get_user(user_id)
    
    if user["balance"] < bet:
        await callback.answer("❌ Недостаточно средств!", show_alert=True)
        return
    
    game_names = {
        "dice": f"{e(EMOJI['dice'])} Кубик",
        "basketball": f"{e(EMOJI['basketball'])} Баскетбол",
        "football": f"{e(EMOJI['football'])} Футбол"
    }
    
    if game == "dice":
        # Кубик: угадать число 1-6
        import random
        player_roll = random.randint(1, 6)
        bot_roll = random.randint(1, 6)
        
        is_win = player_roll == bot_roll
        win_amount = bet * 2 if is_win else 0
        
        result_text = f"""
{game_names[game]}

{e(EMOJI['dice'])} Ваше число: <b>{player_roll}</b>
{e(EMOJI['dice'])} Выпало: <b>{bot_roll}</b>

"""
        
    elif game == "basketball":
        # Баскетбол: 50+ очков = победа
        import random
        score = random.randint(0, 100)
        is_win = score >= 50
        win_amount = bet * 1.5 if is_win else 0
        
        result_text = f"""
{game_names[game]}

{e(EMOJI['basketball'])} Ваш бросок: <b>{score} очков</b>
{e(EMOJI['info'])} Нужно: 50+ очков

"""
        
    else:  # football
        # Футбол: 70+ очков = гол
        import random
        score = random.randint(0, 100)
        is_win = score >= 70
        win_amount = bet * 3 if is_win else 0
        
        result_text = f"""
{game_names[game]}

{e(EMOJI['football'])} Ваш удар: <b>{score} очков</b>
{e(EMOJI['info'])} Нужно: 70+ очков

"""
    
    if is_win:
        update_balance(user_id, win_amount)
        add_game_stat(user_id, game, True, win_amount)
        result_text += f"""
{e(EMOJI['check'])} <b>ПОБЕДА!</b>

{e(EMOJI['money'])} Выигрыш: <b>+{win_amount:.2f} USDT</b>
"""
    else:
        update_balance(user_id, -bet)
        add_game_stat(user_id, game, False, bet)
        result_text += f"""
{e(EMOJI['cross'])} <b>ПОРАЖЕНИЕ</b>

{e(EMOJI['money'])} Потеряно: <b>-{bet:.2f} USDT</b>
"""
    
    new_balance = get_user(user_id)["balance"]
    result_text += f"\n{e(EMOJI['wallet'])} Новый баланс: <b>{new_balance:.2f} USDT</b>"
    
    # Клавиатура после игры
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🎲 Играть ещё",
            callback_data=f"{game}_change_bet"
        )],
        [InlineKeyboardButton(
            text="◁ В меню",
            callback_data="back_to_menu"
        )]
    ])
    
    await callback.message.edit_text(result_text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    await callback.answer()

@router.callback_query(F.data.endswith("_change_bet"))
async def change_bet(callback: CallbackQuery):
    game = callback.data.split("_")[0]
    
    game_names = {
        "dice": f"{e(EMOJI['dice'])} Кубик",
        "basketball": f"{e(EMOJI['basketball'])} Баскетбол",
        "football": f"{e(EMOJI['football'])} Футбол"
    }
    
    multipliers = {"dice": 2, "basketball": 1.5, "football": 3}
    
    user = get_user(callback.from_user.id)
    
    text = f"""
{game_names[game]} 

{e(EMOJI['wallet'])} Ваш баланс: <b>{user['balance']:.2f} USDT</b>
{e(EMOJI['money'])} Множитель выигрыша: <b>x{multipliers[game]}</b>

Выберите сумму ставки:
"""
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=game_bet_keyboard(game))
    await callback.answer()

# ========== АДМИН-ПАНЕЛЬ ==========
@router.callback_query(F.data == "admin_panel")
async def admin_panel(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
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
        await callback.answer("❌ Нет доступа", show_alert=True)
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
{e(EMOJI['stats'])} <b>СТАТИСТИКА БОТА</b>

{e(EMOJI['users'])} Всего пользователей: <b>{total_users}</b>
{e(EMOJI['wallet'])} Общий баланс: <b>{total_balance:.2f} USDT</b>

{e(EMOJI['dice'])} Всего ставок: <b>{total_bets}</b>
{e(EMOJI['check'])} Всего побед: <b>{total_wins}</b>
"""
    await callback.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=back_to_admin_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "admin_users_list")
async def admin_users_list(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    db = get_all_users()
    
    if not db:
        text = f"{e(EMOJI['users'])} <b>Пользователей пока нет</b>"
    else:
        text = f"{e(EMOJI['users'])} <b>СПИСОК ПОЛЬЗОВАТЕЛЕЙ:</b>\n\n"
        for uid, data in list(db.items())[:20]:  # Первые 20
            name = data.get("first_name", "") or data.get("username", "") or uid
            text += f"• <code>{uid}</code> — {name} — {data['balance']:.2f} USDT\n"
        
        if len(db) > 20:
            text += f"\n... и ещё {len(db) - 20} пользователей"
    
    await callback.message.edit_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=back_to_admin_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "admin_edit_balance")
async def admin_edit_balance_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    await callback.message.edit_text(
        f"{e(EMOJI['edit'])} <b>ИЗМЕНЕНИЕ БАЛАНСА</b>\n\n"
        f"{e(EMOJI['info'])} Отправьте ID пользователя:\n"
        f"<i>Можно отправить /id в чате с пользователем</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_keyboard()
    )
    await state.set_state(AdminStates.waiting_for_edit_balance_user)
    await callback.answer()

@router.message(AdminStates.waiting_for_edit_balance_user)
async def admin_edit_balance_user(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    try:
        user_id = int(message.text.strip())
    except ValueError:
        await message.answer(
            f"{e(EMOJI['cross'])} Некорректный ID. Отправьте число.",
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_keyboard()
        )
        return
    
    db = get_all_users()
    uid = str(user_id)
    
    if uid not in db:
        await message.answer(
            f"{e(EMOJI['cross'])} Пользователь с ID <code>{user_id}</code> не найден в базе.",
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_keyboard()
        )
        return
    
    user_data = db[uid]
    name = user_data.get("first_name", "") or user_data.get("username", "") or user_id
    current_balance = user_data["balance"]
    
    await state.update_data(edit_user_id=user_id)
    
    await message.answer(
        f"{e(EMOJI['edit'])} <b>ИЗМЕНЕНИЕ БАЛАНСА</b>\n\n"
        f"{e(EMOJI['profile'])} Пользователь: <b>{name}</b>\n"
        f"{e(EMOJI['info'])} ID: <code>{user_id}</code>\n"
        f"{e(EMOJI['wallet'])} Текущий баланс: <b>{current_balance:.2f} USDT</b>\n\n"
        f"{e(EMOJI['money'])} Введите <b>новый баланс</b> (число):",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_keyboard()
    )
    await state.set_state(AdminStates.waiting_for_edit_balance_amount)

@router.message(AdminStates.waiting_for_edit_balance_amount)
async def admin_edit_balance_amount(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    try:
        new_balance = float(message.text.replace(",", "."))
        if new_balance < 0:
            await message.answer(
                f"{e(EMOJI['cross'])} Баланс не может быть отрицательным.",
                parse_mode=ParseMode.HTML,
                reply_markup=cancel_keyboard()
            )
            return
    except ValueError:
        await message.answer(
            f"{e(EMOJI['cross'])} Введите корректное число.",
            parse_mode=ParseMode.HTML,
            reply_markup=cancel_keyboard()
        )
        return
    
    data = await state.get_data()
    user_id = data["edit_user_id"]
    
    old_balance, new_balance = set_balance(user_id, new_balance)
    
    db = get_all_users()
    user_data = db[str(user_id)]
    name = user_data.get("first_name", "") or user_data.get("username", "") or user_id
    
    # Оповещение пользователю
    try:
        await bot.send_message(
            user_id,
            f"{e(EMOJI['wallet'])} <b>Ваш баланс изменён!</b>\n\n"
            f"{e(EMOJI['edit'])} Администратор установил новый баланс:\n"
            f"<b>{old_balance:.2f}</b> → <b>{new_balance:.2f} USDT</b>",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.warning(f"Не удалось отправить уведомление пользователю {user_id}: {e}")
    
    await message.answer(
        f"{e(EMOJI['check'])} <b>Баланс успешно изменён!</b>\n\n"
        f"{e(EMOJI['profile'])} Пользователь: <b>{name}</b>\n"
        f"{e(EMOJI['info'])} ID: <code>{user_id}</code>\n"
        f"{e(EMOJI['wallet'])} Старый баланс: {old_balance:.2f} USDT\n"
        f"{e(EMOJI['wallet'])} Новый баланс: <b>{new_balance:.2f} USDT</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_panel_keyboard()
    )
    await state.clear()

@router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    await callback.message.edit_text(
        f"{e('5370599459661045441')} <b>РАССЫЛКА</b>\n\n"
        f"{e(EMOJI['info'])} Отправьте сообщение для рассылки всем пользователям.\n"
        f"<i>Поддерживается HTML и премиум-эмодзи</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_keyboard()
    )
    await state.set_state(AdminStates.waiting_for_broadcast)
    await callback.answer()

@router.message(AdminStates.waiting_for_broadcast)
async def admin_broadcast_send(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    db = get_all_users()
    success = 0
    failed = 0
    
    await message.answer(f"{e('5345906554510012647')} <b>Начинаю рассылку...</b>", parse_mode=ParseMode.HTML)
    
    for user_id in db.keys():
        try:
            await bot.send_message(
                int(user_id),
                message.html_text if message.html_text else message.text,
                parse_mode=ParseMode.HTML
            )
            success += 1
            await asyncio.sleep(0.05)  # Защита от флуда
        except Exception as e:
            logger.error(f"Failed to send to {user_id}: {e}")
            failed += 1
    
    await message.answer(
        f"{e(EMOJI['check'])} <b>Рассылка завершена!</b>\n\n"
        f"✅ Успешно: {success}\n"
        f"❌ Ошибок: {failed}",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_panel_keyboard()
    )
    await state.clear()

@router.callback_query(F.data == "close_admin")
async def close_admin(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    
    await callback.message.delete()
    await callback.answer()

# ========== ОБЩИЕ КОЛБЭКИ ==========
@router.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery):
    await callback.message.delete()
    await callback.message.answer(
        f"{e(EMOJI['home'])} <b>Главное меню</b>\n\nВыберите действие:",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "cancel_action")
async def cancel_action(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    if callback.from_user.id in ADMIN_IDS and callback.message.chat.type == "private":
        await callback.message.edit_text(
            f"{e(EMOJI['cross'])} <b>Действие отменено</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_panel_keyboard()
        )
    else:
        await callback.message.edit_text(
            f"{e(EMOJI['cross'])} <b>Действие отменено</b>",
            parse_mode=ParseMode.HTML
        )
        await callback.message.answer(
            f"{e(EMOJI['home'])} <b>Главное меню</b>",
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
            f"{e(EMOJI['check'])} <b>ОПЛАТА УСПЕШНА!</b>\n\n"
            f"{e(EMOJI['money'])} Пополнено: <b>+{amount:.2f} USDT</b>\n"
            f"{e(EMOJI['wallet'])} Новый баланс: <b>{new_balance:.2f} USDT</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_to_menu_keyboard()
        )
    elif invoice and invoice["status"] == "active":
        await callback.answer("⏳ Счёт ещё не оплачен", show_alert=True)
    else:
        await callback.answer("❌ Счёт не найден или отменён", show_alert=True)

# ========== ЗАПУСК ==========
async def main():
    logger.info("Starting bot...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
