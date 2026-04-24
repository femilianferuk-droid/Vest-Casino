import os
import json
import asyncio
import logging
import random
import uuid
import re
from datetime import datetime
import asyncpg
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, InputMediaPhoto, InputMediaVideo,
    ContentType
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

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("Переменная окружения DATABASE_URL не установлена!")

CRYPTO_BOT_API = os.getenv("CRYPTO_BOT_API", "465788:AAOxwPgMIPTheqZpyAyN2JotJ9U8fREP7rl")
CRYPTO_API_URL = "https://pay.crypt.bot/api"

ADMIN_IDS = [7973988177]
SUPPORT_USERNAME = "VestSupport"
BOT_USERNAME = "vestCasinoBot"
PRIVACY_URL = "https://telegra.ph/-04-23-2406"

USDT_RUB_RATE = 90

# Реквизиты для пополнения рублями
RUB_REQUISITES = {
    "phone": "+79818376180",
    "bank": "ЮМАНИ",
    "recipient": "Иван Б"
}

# ========== ЛОГИРОВАНИЕ ==========
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ========== БАЗА ДАННЫХ POSTGRESQL ==========
class Database:
    def __init__(self):
        self.pool = None
    
    async def connect(self):
        if not self.pool:
            self.pool = await asyncpg.create_pool(DATABASE_URL)
            await self._init_tables()
    
    async def _init_tables(self):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    balance NUMERIC(20, 2) DEFAULT 0,
                    username TEXT DEFAULT '',
                    first_name TEXT DEFAULT '',
                    privacy_accepted BOOLEAN DEFAULT FALSE
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS stats (
                    user_id BIGINT PRIMARY KEY REFERENCES users(user_id),
                    dice_wins INT DEFAULT 0,
                    dice_losses INT DEFAULT 0,
                    basketball_wins INT DEFAULT 0,
                    basketball_losses INT DEFAULT 0,
                    football_wins INT DEFAULT 0,
                    football_losses INT DEFAULT 0,
                    blackjack_wins INT DEFAULT 0,
                    blackjack_losses INT DEFAULT 0,
                    bowling_wins INT DEFAULT 0,
                    bowling_losses INT DEFAULT 0,
                    slots_wins INT DEFAULT 0,
                    slots_losses INT DEFAULT 0,
                    total_won NUMERIC(20, 2) DEFAULT 0,
                    total_lost NUMERIC(20, 2) DEFAULT 0
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS media (
                    section TEXT PRIMARY KEY,
                    type TEXT,
                    file_id TEXT
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS rub_payments (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    amount_rub NUMERIC(20, 2),
                    amount_usdt NUMERIC(20, 2),
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            sections = ["profile", "games", "deposit", "withdraw", "support", "help"]
            for section in sections:
                await conn.execute("""
                    INSERT INTO media (section, type, file_id) 
                    VALUES ($1, NULL, NULL) 
                    ON CONFLICT (section) DO NOTHING
                """, section)
    
    async def get_user(self, user_id: int):
        async with self.pool.acquire() as conn:
            user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
            if not user:
                await conn.execute("INSERT INTO users (user_id) VALUES ($1)", user_id)
                await conn.execute("INSERT INTO stats (user_id) VALUES ($1)", user_id)
                user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
            stats = await conn.fetchrow("SELECT * FROM stats WHERE user_id = $1", user_id)
            return self._format_user(user, stats)
    
    def _format_user(self, user, stats):
        if not user:
            return None
        return {
            "balance": float(user["balance"]),
            "username": user["username"] or "",
            "first_name": user["first_name"] or "",
            "privacy_accepted": user["privacy_accepted"],
            "stats": {
                "dice": {"wins": stats["dice_wins"], "losses": stats["dice_losses"]},
                "basketball": {"wins": stats["basketball_wins"], "losses": stats["basketball_losses"]},
                "football": {"wins": stats["football_wins"], "losses": stats["football_losses"]},
                "blackjack": {"wins": stats["blackjack_wins"], "losses": stats["blackjack_losses"]},
                "bowling": {"wins": stats["bowling_wins"], "losses": stats["bowling_losses"]},
                "slots": {"wins": stats["slots_wins"], "losses": stats["slots_losses"]},
                "total_won": float(stats["total_won"]),
                "total_lost": float(stats["total_lost"])
            }
        }
    
    async def update_balance(self, user_id: int, amount: float):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET balance = balance + $1 WHERE user_id = $2",
                amount, user_id
            )
            if amount > 0:
                await conn.execute(
                    "UPDATE stats SET total_won = total_won + $1 WHERE user_id = $2",
                    amount, user_id
                )
            else:
                await conn.execute(
                    "UPDATE stats SET total_lost = total_lost + $1 WHERE user_id = $2",
                    abs(amount), user_id
                )
            balance = await conn.fetchval("SELECT balance FROM users WHERE user_id = $1", user_id)
            return float(balance)
    
    async def set_balance(self, user_id: int, amount: float):
        async with self.pool.acquire() as conn:
            old = await conn.fetchval("SELECT balance FROM users WHERE user_id = $1", user_id)
            await conn.execute("UPDATE users SET balance = $1 WHERE user_id = $2", amount, user_id)
            return float(old), amount
    
    async def accept_privacy(self, user_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE users SET privacy_accepted = TRUE WHERE user_id = $1", user_id)
    
    async def add_game_stat(self, user_id: int, game: str, is_win: bool):
        async with self.pool.acquire() as conn:
            game_map = {
                "dice": "dice", "basketball": "basketball", "football": "football",
                "blackjack": "blackjack", "bowling": "bowling", "slots": "slots"
            }
            if game in game_map:
                col = f"{game_map[game]}_{'wins' if is_win else 'losses'}"
                await conn.execute(f"UPDATE stats SET {col} = {col} + 1 WHERE user_id = $1", user_id)
    
    async def get_all_users(self):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM users")
            return {str(r["user_id"]): {
                "balance": float(r["balance"]),
                "username": r["username"] or "",
                "first_name": r["first_name"] or ""
            } for r in rows}
    
    async def update_user_info(self, user_id: int, username: str, first_name: str):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET username = $1, first_name = $2 WHERE user_id = $3",
                username or "", first_name or "", user_id
            )
    
    async def get_media(self, section: str):
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM media WHERE section = $1", section)
    
    async def set_media(self, section: str, media_type: str, file_id: str):
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE media SET type = $1, file_id = $2 WHERE section = $3",
                media_type, file_id, section
            )
    
    async def clear_all_media(self):
        async with self.pool.acquire() as conn:
            await conn.execute("UPDATE media SET type = NULL, file_id = NULL")
    
    async def create_rub_payment(self, user_id: int, amount_rub: float, amount_usdt: float):
        async with self.pool.acquire() as conn:
            payment_id = await conn.fetchval(
                "INSERT INTO rub_payments (user_id, amount_rub, amount_usdt) VALUES ($1, $2, $3) RETURNING id",
                user_id, amount_rub, amount_usdt
            )
            return payment_id
    
    async def approve_rub_payment(self, payment_id: int):
        async with self.pool.acquire() as conn:
            payment = await conn.fetchrow("SELECT * FROM rub_payments WHERE id = $1", payment_id)
            if payment and payment["status"] == "pending":
                await conn.execute("UPDATE rub_payments SET status = 'approved' WHERE id = $1", payment_id)
                return payment
            return None
    
    async def reject_rub_payment(self, payment_id: int):
        async with self.pool.acquire() as conn:
            payment = await conn.fetchrow("SELECT * FROM rub_payments WHERE id = $1", payment_id)
            if payment and payment["status"] == "pending":
                await conn.execute("UPDATE rub_payments SET status = 'rejected' WHERE id = $1", payment_id)
                return payment
            return None

db = Database()

# ========== ПРЕМИУМ ЭМОДЗИ ID ==========
EMOJI = {
    "settings": "5904258298764334001",
    "profile": "5884366771913233289",
    "wallet": "5769126056262898415",
    "dice": "5778479949572738874",
    "basketball": "5778672437122045013",
    "football": "5775896410780079073",
    "blackjack": "5836907383292436018",
    "bowling": "5837069325034331827",
    "slots": "5805553606635559688",
    "money": "5904359114531675993",
    "check": "6041919344995209164",
    "cross": "6030757850274336631",
    "back": "6037249452824072506",
    "info": "5891120964468480450",
    "stats": "5870921681735781843",
    "crypto": "5260752406890711732",
    "graph": "5870930636742595124",
    "home": "5873147866364514353",
    "edit": "5771847914477326786",
    "users": "5870772616305839506",
    "broadcast": "6039422865189638057",
    "loading": "5345906554510012647",
    "link": "5769289093221454192",
    "gift": "5805298713211447980",
    "send": "6039573425268201570",
    "games": "5778672437122045013",
    "withdraw": "5890848474563352982",
    "support": "5983580310292402968",
    "rub": "5904359114531675993",
    "star": "5805331990618053402",
    "bot": "5983580310292402968",
    "clock": "5983150113483134607",
    "media": "5944753741512052670",
    "folder": "5805550320985578625",
    "king": "5805553606635559688",
    "diamond": "5836907383292436018",
}

def e(emoji_id):
    return f'<tg-emoji emoji-id="{emoji_id}">⚡</tg-emoji>'

# ========== FSM ==========
class DepositState(StatesGroup):
    waiting_for_amount_crypto = State()
    waiting_for_amount_rub = State()
    waiting_for_rub_screenshot = State()

class WithdrawState(StatesGroup):
    waiting_for_amount = State()

class AdminStates(StatesGroup):
    waiting_for_broadcast = State()
    waiting_for_edit_balance = State()
    waiting_for_media_profile = State()
    waiting_for_media_games = State()
    waiting_for_media_deposit = State()
    waiting_for_media_withdraw = State()
    waiting_for_media_support = State()
    waiting_for_media_help = State()

# ========== КЛАВИАТУРЫ ==========
def main_menu_keyboard():
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

def deposit_method_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="Crypto Bot (USDT)", callback_data="deposit_crypto",
        icon_custom_emoji_id=EMOJI["crypto"], style="primary"
    ))
    builder.row(InlineKeyboardButton(
        text="Рубли (ЮМАНИ)", callback_data="deposit_rub",
        icon_custom_emoji_id=EMOJI["rub"], style="default"
    ))
    builder.row(InlineKeyboardButton(
        text="Звёзды Telegram", callback_data="deposit_stars",
        icon_custom_emoji_id=EMOJI["star"], style="default"
    ))
    builder.row(InlineKeyboardButton(
        text="Назад", callback_data="back_to_menu_msg",
        icon_custom_emoji_id=EMOJI["back"], style="default"
    ))
    return builder.as_markup()

def privacy_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="Политика конфиденциальности", url=PRIVACY_URL,
        icon_custom_emoji_id=EMOJI["info"], style="primary"
    ))
    builder.row(InlineKeyboardButton(
        text="Я ознакомился", callback_data="accept_privacy",
        icon_custom_emoji_id=EMOJI["check"], style="success"
    ))
    return builder.as_markup()

def games_menu_keyboard():
    builder = InlineKeyboardBuilder()
    games_list = [
        ("Кубик", "game_dice", EMOJI["dice"]),
        ("Баскетбол", "game_basketball", EMOJI["basketball"]),
        ("Футбол", "game_football", EMOJI["football"]),
        ("Блэкджек", "game_blackjack", EMOJI["blackjack"]),
        ("Боулинг", "game_bowling", EMOJI["bowling"]),
        ("Слоты", "game_slots", EMOJI["slots"]),
    ]
    for name, cb, emoji in games_list:
        builder.row(InlineKeyboardButton(
            text=name, callback_data=cb,
            icon_custom_emoji_id=emoji, style="primary"
        ))
    builder.row(InlineKeyboardButton(
        text="Назад", callback_data="back_to_menu_msg",
        icon_custom_emoji_id=EMOJI["back"], style="default"
    ))
    return builder.as_markup()

def back_to_menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="Назад в меню", callback_data="back_to_menu_msg",
        icon_custom_emoji_id=EMOJI["back"], style="default"
    ))
    return builder.as_markup()

def back_to_admin_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="Назад", callback_data="admin_panel",
        icon_custom_emoji_id=EMOJI["back"], style="default"
    ))
    return builder.as_markup()

def cancel_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="Отмена", callback_data="cancel_action",
        icon_custom_emoji_id=EMOJI["cross"], style="danger"
    ))
    return builder.as_markup()

def support_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="Написать в поддержку", url=f"https://t.me/{SUPPORT_USERNAME}",
        icon_custom_emoji_id=EMOJI["support"], style="primary"
    ))
    builder.row(InlineKeyboardButton(
        text="Назад", callback_data="back_to_menu_msg",
        icon_custom_emoji_id=EMOJI["back"], style="default"
    ))
    return builder.as_markup()

def admin_panel_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="Рассылка", callback_data="admin_broadcast",
        icon_custom_emoji_id=EMOJI["broadcast"], style="primary"
    ))
    builder.row(InlineKeyboardButton(
        text="Изменить баланс", callback_data="admin_edit_balance",
        icon_custom_emoji_id=EMOJI["edit"], style="primary"
    ))
    builder.row(InlineKeyboardButton(
        text="Статистика", callback_data="admin_stats",
        icon_custom_emoji_id=EMOJI["stats"], style="default"
    ))
    builder.row(InlineKeyboardButton(
        text="Пользователи", callback_data="admin_users_list",
        icon_custom_emoji_id=EMOJI["users"], style="default"
    ))
    builder.row(InlineKeyboardButton(
        text="Медиа", callback_data="admin_media",
        icon_custom_emoji_id=EMOJI["media"], style="default"
    ))
    builder.row(InlineKeyboardButton(
        text="Закрыть", callback_data="close_admin",
        icon_custom_emoji_id=EMOJI["cross"], style="danger"
    ))
    return builder.as_markup()

def admin_media_keyboard():
    builder = InlineKeyboardBuilder()
    sections = [
        ("Медиа в Профиль", "admin_media_profile", EMOJI["profile"]),
        ("Медиа в Игры", "admin_media_games", EMOJI["games"]),
        ("Медиа в Пополнение", "admin_media_deposit", EMOJI["wallet"]),
        ("Медиа в Вывод", "admin_media_withdraw", EMOJI["withdraw"]),
        ("Медиа в Поддержку", "admin_media_support", EMOJI["support"]),
        ("Медиа в Помощь", "admin_media_help", EMOJI["info"]),
    ]
    for i, (name, cb, emoji) in enumerate(sections):
        style = "primary" if i < 4 else "default"
        builder.row(InlineKeyboardButton(
            text=name, callback_data=cb,
            icon_custom_emoji_id=emoji, style=style
        ))
    builder.row(InlineKeyboardButton(
        text="Удалить все медиа", callback_data="admin_media_clear",
        icon_custom_emoji_id=EMOJI["cross"], style="danger"
    ))
    builder.row(InlineKeyboardButton(
        text="Назад", callback_data="admin_panel",
        icon_custom_emoji_id=EMOJI["back"], style="default"
    ))
    return builder.as_markup()

def blackjack_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="Взять", callback_data="bj_hit",
            icon_custom_emoji_id=EMOJI["blackjack"], style="primary"
        ),
        InlineKeyboardButton(
            text="Пас", callback_data="bj_stand",
            icon_custom_emoji_id=EMOJI["cross"], style="danger"
        )
    )
    return builder.as_markup()

def slots_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="Крутить", callback_data="slots_spin",
        icon_custom_emoji_id=EMOJI["slots"], style="primary"
    ))
    builder.row(InlineKeyboardButton(
        text="Изменить ставку", callback_data="slots_change_bet",
        icon_custom_emoji_id=EMOJI["edit"], style="default"
    ))
    builder.row(InlineKeyboardButton(
        text="Назад", callback_data="back_to_games",
        icon_custom_emoji_id=EMOJI["back"], style="default"
    ))
    return builder.as_markup()

def slots_bet_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="0.1", callback_data="slots_bet_0.1", icon_custom_emoji_id=EMOJI["money"], style="default"),
        InlineKeyboardButton(text="0.5", callback_data="slots_bet_0.5", icon_custom_emoji_id=EMOJI["money"], style="default"),
        InlineKeyboardButton(text="1", callback_data="slots_bet_1", icon_custom_emoji_id=EMOJI["money"], style="default")
    )
    builder.row(
        InlineKeyboardButton(text="5", callback_data="slots_bet_5", icon_custom_emoji_id=EMOJI["money"], style="default"),
        InlineKeyboardButton(text="10", callback_data="slots_bet_10", icon_custom_emoji_id=EMOJI["money"], style="default"),
        InlineKeyboardButton(text="50", callback_data="slots_bet_50", icon_custom_emoji_id=EMOJI["money"], style="default")
    )
    builder.row(InlineKeyboardButton(
        text="Своя сумма", callback_data="slots_bet_custom",
        icon_custom_emoji_id=EMOJI["edit"], style="primary"
    ))
    builder.row(InlineKeyboardButton(
        text="Назад", callback_data="back_to_games",
        icon_custom_emoji_id=EMOJI["back"], style="default"
    ))
    return builder.as_markup()

def dice_mode_keyboard():
    builder = InlineKeyboardBuilder()
    modes = [
        ("1-3 / 4-6 (x1.85)", "dice_mode_highlow"),
        ("Чёт / Нечет (x1.85)", "dice_mode_evenodd"),
        ("Угадать число (x5)", "dice_mode_number"),
        ("Два кубика: сумма 7 (x4)", "dice_mode_twodice"),
        ("Три кубика: 10-11 (x3)", "dice_mode_threedice"),
        ("Счастливое: 1 или 6 (x2.5)", "dice_mode_lucky"),
    ]
    for text, cb in modes:
        builder.row(InlineKeyboardButton(
            text=text, callback_data=cb,
            icon_custom_emoji_id=EMOJI["dice"], style="primary"
        ))
    builder.row(InlineKeyboardButton(
        text="Назад", callback_data="back_to_games",
        icon_custom_emoji_id=EMOJI["back"], style="default"
    ))
    return builder.as_markup()

def dice_choice_keyboard(mode: str):
    builder = InlineKeyboardBuilder()
    if mode == "highlow":
        builder.row(
            InlineKeyboardButton(text="1-3", callback_data="dice_low", icon_custom_emoji_id=EMOJI["dice"], style="success"),
            InlineKeyboardButton(text="4-6", callback_data="dice_high", icon_custom_emoji_id=EMOJI["dice"], style="danger")
        )
    elif mode == "evenodd":
        builder.row(
            InlineKeyboardButton(text="Чётное", callback_data="dice_even", icon_custom_emoji_id=EMOJI["dice"], style="primary"),
            InlineKeyboardButton(text="Нечётное", callback_data="dice_odd", icon_custom_emoji_id=EMOJI["dice"], style="primary")
        )
    elif mode == "number":
        for i in range(1, 7):
            builder.add(InlineKeyboardButton(
                text=str(i), callback_data=f"dice_num_{i}",
                icon_custom_emoji_id=EMOJI["dice"], style="primary"
            ))
        builder.adjust(3)
    elif mode == "lucky":
        builder.row(
            InlineKeyboardButton(text="1", callback_data="dice_lucky_1", icon_custom_emoji_id=EMOJI["dice"], style="primary"),
            InlineKeyboardButton(text="6", callback_data="dice_lucky_6", icon_custom_emoji_id=EMOJI["dice"], style="primary")
        )
    builder.row(InlineKeyboardButton(
        text="Изменить ставку", callback_data="dice_change_bet",
        icon_custom_emoji_id=EMOJI["edit"], style="default"
    ))
    builder.row(InlineKeyboardButton(
        text="Назад к режимам", callback_data="dice_back_modes",
        icon_custom_emoji_id=EMOJI["back"], style="default"
    ))
    return builder.as_markup()

def dice_bet_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="0.1", callback_data="dice_bet_0.1", icon_custom_emoji_id=EMOJI["money"], style="default"),
        InlineKeyboardButton(text="0.5", callback_data="dice_bet_0.5", icon_custom_emoji_id=EMOJI["money"], style="default"),
        InlineKeyboardButton(text="1", callback_data="dice_bet_1", icon_custom_emoji_id=EMOJI["money"], style="default")
    )
    builder.row(
        InlineKeyboardButton(text="5", callback_data="dice_bet_5", icon_custom_emoji_id=EMOJI["money"], style="default"),
        InlineKeyboardButton(text="10", callback_data="dice_bet_10", icon_custom_emoji_id=EMOJI["money"], style="default"),
        InlineKeyboardButton(text="50", callback_data="dice_bet_50", icon_custom_emoji_id=EMOJI["money"], style="default")
    )
    builder.row(InlineKeyboardButton(
        text="Своя сумма", callback_data="dice_bet_custom",
        icon_custom_emoji_id=EMOJI["edit"], style="primary"
    ))
    builder.row(InlineKeyboardButton(
        text="Назад", callback_data="back_to_games",
        icon_custom_emoji_id=EMOJI["back"], style="default"
    ))
    return builder.as_markup()

def blackjack_bet_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="0.1", callback_data="blackjack_bet_0.1", icon_custom_emoji_id=EMOJI["money"], style="default"),
        InlineKeyboardButton(text="0.5", callback_data="blackjack_bet_0.5", icon_custom_emoji_id=EMOJI["money"], style="default"),
        InlineKeyboardButton(text="1", callback_data="blackjack_bet_1", icon_custom_emoji_id=EMOJI["money"], style="default")
    )
    builder.row(
        InlineKeyboardButton(text="5", callback_data="blackjack_bet_5", icon_custom_emoji_id=EMOJI["money"], style="default"),
        InlineKeyboardButton(text="10", callback_data="blackjack_bet_10", icon_custom_emoji_id=EMOJI["money"], style="default"),
        InlineKeyboardButton(text="50", callback_data="blackjack_bet_50", icon_custom_emoji_id=EMOJI["money"], style="default")
    )
    builder.row(InlineKeyboardButton(
        text="Своя сумма", callback_data="blackjack_bet_custom",
        icon_custom_emoji_id=EMOJI["edit"], style="primary"
    ))
    builder.row(InlineKeyboardButton(
        text="Назад", callback_data="back_to_games",
        icon_custom_emoji_id=EMOJI["back"], style="default"
    ))
    return builder.as_markup()

def basketball_bet_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="0.1", callback_data="basketball_bet_0.1", icon_custom_emoji_id=EMOJI["money"], style="default"),
        InlineKeyboardButton(text="0.5", callback_data="basketball_bet_0.5", icon_custom_emoji_id=EMOJI["money"], style="default"),
        InlineKeyboardButton(text="1", callback_data="basketball_bet_1", icon_custom_emoji_id=EMOJI["money"], style="default")
    )
    builder.row(
        InlineKeyboardButton(text="5", callback_data="basketball_bet_5", icon_custom_emoji_id=EMOJI["money"], style="default"),
        InlineKeyboardButton(text="10", callback_data="basketball_bet_10", icon_custom_emoji_id=EMOJI["money"], style="default"),
        InlineKeyboardButton(text="50", callback_data="basketball_bet_50", icon_custom_emoji_id=EMOJI["money"], style="default")
    )
    builder.row(InlineKeyboardButton(
        text="Своя сумма", callback_data="basketball_bet_custom",
        icon_custom_emoji_id=EMOJI["edit"], style="primary"
    ))
    builder.row(InlineKeyboardButton(
        text="Назад", callback_data="back_to_games",
        icon_custom_emoji_id=EMOJI["back"], style="default"
    ))
    return builder.as_markup()

def basketball_choice_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="Попадание (1.85x)", callback_data="basketball_hit",
        icon_custom_emoji_id=EMOJI["basketball"], style="success"
    ))
    builder.row(InlineKeyboardButton(
        text="Промах (1.5x)", callback_data="basketball_miss",
        icon_custom_emoji_id=EMOJI["basketball"], style="danger"
    ))
    builder.row(InlineKeyboardButton(
        text="Попадание 2 раза (3x)", callback_data="basketball_double",
        icon_custom_emoji_id=EMOJI["basketball"], style="primary"
    ))
    builder.row(InlineKeyboardButton(
        text="Изменить ставку", callback_data="basketball_change_bet",
        icon_custom_emoji_id=EMOJI["edit"], style="default"
    ))
    builder.row(InlineKeyboardButton(
        text="Назад", callback_data="back_to_games",
        icon_custom_emoji_id=EMOJI["back"], style="default"
    ))
    return builder.as_markup()

def football_mode_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="Гол (x1.85)", callback_data="football_mode_goal",
        icon_custom_emoji_id=EMOJI["football"], style="success"
    ))
    builder.row(InlineKeyboardButton(
        text="Промах (x1.5)", callback_data="football_mode_miss",
        icon_custom_emoji_id=EMOJI["football"], style="danger"
    ))
    builder.row(InlineKeyboardButton(
        text="Пенальти (x2)", callback_data="football_mode_penalty",
        icon_custom_emoji_id=EMOJI["football"], style="primary"
    ))
    builder.row(InlineKeyboardButton(
        text="Штанга/Перекладина (x4)", callback_data="football_mode_post",
        icon_custom_emoji_id=EMOJI["football"], style="default"
    ))
    builder.row(InlineKeyboardButton(
        text="Назад", callback_data="back_to_games",
        icon_custom_emoji_id=EMOJI["back"], style="default"
    ))
    return builder.as_markup()

def football_bet_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="0.1", callback_data="football_bet_0.1", icon_custom_emoji_id=EMOJI["money"], style="default"),
        InlineKeyboardButton(text="0.5", callback_data="football_bet_0.5", icon_custom_emoji_id=EMOJI["money"], style="default"),
        InlineKeyboardButton(text="1", callback_data="football_bet_1", icon_custom_emoji_id=EMOJI["money"], style="default")
    )
    builder.row(
        InlineKeyboardButton(text="5", callback_data="football_bet_5", icon_custom_emoji_id=EMOJI["money"], style="default"),
        InlineKeyboardButton(text="10", callback_data="football_bet_10", icon_custom_emoji_id=EMOJI["money"], style="default"),
        InlineKeyboardButton(text="50", callback_data="football_bet_50", icon_custom_emoji_id=EMOJI["money"], style="default")
    )
    builder.row(InlineKeyboardButton(
        text="Своя сумма", callback_data="football_bet_custom",
        icon_custom_emoji_id=EMOJI["edit"], style="primary"
    ))
    builder.row(InlineKeyboardButton(
        text="Назад", callback_data="back_to_games",
        icon_custom_emoji_id=EMOJI["back"], style="default"
    ))
    return builder.as_markup()

def bowling_bet_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="0.1", callback_data="bowling_bet_0.1", icon_custom_emoji_id=EMOJI["money"], style="default"),
        InlineKeyboardButton(text="0.5", callback_data="bowling_bet_0.5", icon_custom_emoji_id=EMOJI["money"], style="default"),
        InlineKeyboardButton(text="1", callback_data="bowling_bet_1", icon_custom_emoji_id=EMOJI["money"], style="default")
    )
    builder.row(
        InlineKeyboardButton(text="5", callback_data="bowling_bet_5", icon_custom_emoji_id=EMOJI["money"], style="default"),
        InlineKeyboardButton(text="10", callback_data="bowling_bet_10", icon_custom_emoji_id=EMOJI["money"], style="default"),
        InlineKeyboardButton(text="50", callback_data="bowling_bet_50", icon_custom_emoji_id=EMOJI["money"], style="default")
    )
    builder.row(InlineKeyboardButton(
        text="Своя сумма", callback_data="bowling_bet_custom",
        icon_custom_emoji_id=EMOJI["edit"], style="primary"
    ))
    builder.row(InlineKeyboardButton(
        text="Назад", callback_data="back_to_games",
        icon_custom_emoji_id=EMOJI["back"], style="default"
    ))
    return builder.as_markup()

def bowling_choice_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(
        text="Страйк (x10)", callback_data="bowling_strike",
        icon_custom_emoji_id=EMOJI["bowling"], style="success"
    ))
    builder.row(InlineKeyboardButton(
        text="Спэр (x5)", callback_data="bowling_spare",
        icon_custom_emoji_id=EMOJI["bowling"], style="primary"
    ))
    builder.row(InlineKeyboardButton(
        text="7+ кеглей (x2)", callback_data="bowling_seven",
        icon_custom_emoji_id=EMOJI["bowling"], style="default"
    ))
    builder.row(InlineKeyboardButton(
        text="Изменить ставку", callback_data="bowling_change_bet",
        icon_custom_emoji_id=EMOJI["edit"], style="default"
    ))
    builder.row(InlineKeyboardButton(
        text="Назад", callback_data="back_to_games",
        icon_custom_emoji_id=EMOJI["back"], style="default"
    ))
    return builder.as_markup()

def play_again_keyboard(game: str):
    builder = InlineKeyboardBuilder()
    cb_map = {
        "dice": "dice_change_bet",
        "basketball": "basketball_change_bet",
        "football": "football_change_bet",
        "blackjack": "blackjack_change_bet",
        "bowling": "bowling_change_bet",
        "slots": "slots_change_bet"
    }
    cb = cb_map.get(game, "back_to_games")
    builder.row(InlineKeyboardButton(
        text="Играть ещё", callback_data=cb,
        icon_custom_emoji_id=EMOJI.get(game, EMOJI["games"]), style="success"
    ))
    builder.row(InlineKeyboardButton(
        text="В меню", callback_data="back_to_menu_msg",
        icon_custom_emoji_id=EMOJI["home"], style="default"
    ))
    return builder.as_markup()

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
async def send_with_media(message_or_callback, section: str, text: str, reply_markup=None, parse_mode=ParseMode.HTML):
    media = await db.get_media(section)
    file_id = media["file_id"] if media else None
    media_type = media["type"] if media else None
    
    if file_id and media_type:
        try:
            if media_type == "photo":
                if isinstance(message_or_callback, Message):
                    await message_or_callback.answer_photo(
                        photo=file_id, caption=text,
                        parse_mode=parse_mode, reply_markup=reply_markup
                    )
                else:
                    await message_or_callback.message.delete()
                    await message_or_callback.message.answer_photo(
                        photo=file_id, caption=text,
                        parse_mode=parse_mode, reply_markup=reply_markup
                    )
            elif media_type == "video":
                if isinstance(message_or_callback, Message):
                    await message_or_callback.answer_video(
                        video=file_id, caption=text,
                        parse_mode=parse_mode, reply_markup=reply_markup
                    )
                else:
                    await message_or_callback.message.delete()
                    await message_or_callback.message.answer_video(
                        video=file_id, caption=text,
                        parse_mode=parse_mode, reply_markup=reply_markup
                    )
            return True
        except Exception as e:
            logger.error(f"Error sending media: {e}")
    
    if isinstance(message_or_callback, CallbackQuery):
        await message_or_callback.message.edit_text(
            text, parse_mode=parse_mode, reply_markup=reply_markup
        )
    else:
        await message_or_callback.answer(
            text, parse_mode=parse_mode, reply_markup=reply_markup
        )
    return False

# ========== CRYPTO BOT API ==========
async def create_crypto_invoice(amount: float):
    async with aiohttp.ClientSession() as session:
        headers = {"Crypto-Pay-API-Token": CRYPTO_BOT_API}
        data = {
            "asset": "USDT",
            "amount": str(amount),
            "description": "Vest Casino - пополнение баланса",
            "paid_btn_name": "callback",
            "paid_btn_url": f"https://t.me/{BOT_USERNAME}"
        }
        async with session.post(f"{CRYPTO_API_URL}/createInvoice", headers=headers, json=data) as resp:
            if resp.status == 200:
                result = await resp.json()
                if result.get("ok"):
                    return result["result"]
            logger.error(f"Crypto Bot API error: {await resp.text()}")
            return None

async def check_crypto_invoice(invoice_id: int):
    async with aiohttp.ClientSession() as session:
        headers = {"Crypto-Pay-API-Token": CRYPTO_BOT_API}
        data = {"invoice_ids": [invoice_id]}
        async with session.post(f"{CRYPTO_API_URL}/getInvoices", headers=headers, json=data) as resp:
            if resp.status == 200:
                result = await resp.json()
                if result.get("ok") and result["result"]["items"]:
                    return result["result"]["items"][0]
            return None

async def crypto_payment_check_loop(user_id: int, message: Message, invoice_id: int):
    for i in range(120):
        await asyncio.sleep(5)
        invoice = await check_crypto_invoice(invoice_id)
        if invoice and invoice["status"] == "paid":
            amount = float(invoice["amount"])
            await db.update_balance(user_id, amount)
            new_balance = (await db.get_user(user_id))["balance"]
            try:
                await message.edit_text(
                    f"{e(EMOJI['check'])} <b>ОПЛАЧЕНО!</b>\n\n"
                    f"{e(EMOJI['money'])} +{amount:.2f} USDT\n"
                    f"{e(EMOJI['wallet'])} Баланс: <b>{new_balance:.2f} USDT</b>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=back_to_menu_keyboard()
                )
            except:
                pass
            return

# ========== БЛЭКДЖЕК ==========
def get_card_value(card):
    if card in ['J', 'Q', 'K']:
        return 10
    elif card == 'A':
        return 11
    return int(card)

def get_hand_value(hand):
    value = sum(get_card_value(c) for c in hand)
    aces = hand.count('A')
    while value > 21 and aces > 0:
        value -= 10
        aces -= 1
    return value

def create_deck():
    cards = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A'] * 4
    random.shuffle(cards)
    return cards

def format_hand(hand):
    return ' '.join(hand)

# ========== СЛОТЫ ==========
SLOTS_SYMBOLS = ["🍒", "🍋", "🍊", "🍇", "💎", "👑", "7️⃣"]
SLOTS_MULTIPLIERS = {
    ("👑", "👑", "👑"): 10,
    ("💎", "💎", "💎"): 8,
    ("7️⃣", "7️⃣", "7️⃣"): 5,
    ("🍒", "🍒", "🍒"): 3,
    ("🍋", "🍋", "🍋"): 3,
    ("🍊", "🍊", "🍊"): 3,
    ("🍇", "🍇", "🍇"): 3,
}

def spin_slots():
    return [random.choice(SLOTS_SYMBOLS) for _ in range(3)]

def get_slots_win(result):
    t = tuple(result)
    if t in SLOTS_MULTIPLIERS:
        return SLOTS_MULTIPLIERS[t]
    if result[0] == result[1] or result[1] == result[2] or result[0] == result[2]:
        return 1.5
    return 0

# ========== ИНИЦИАЛИЗАЦИЯ ==========
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

user_bets = {}

# ========== КОМАНДЫ ==========
@router.message(Command("start"))
async def cmd_start(message: Message):
    await db.update_user_info(message.from_user.id, message.from_user.username, message.from_user.first_name)
    user = await db.get_user(message.from_user.id)
    welcome_text = f"""
{e(EMOJI['home'])} <b>Vest Casino</b>

{e(EMOJI['dice'])} <b>Кубик</b>
{e(EMOJI['basketball'])} <b>Баскетбол</b>
{e(EMOJI['football'])} <b>Футбол</b>
{e(EMOJI['blackjack'])} <b>Блэкджек</b>
{e(EMOJI['bowling'])} <b>Боулинг</b>
{e(EMOJI['slots'])} <b>Слоты</b>

{e(EMOJI['wallet'])} Твой баланс: <b>{user['balance']:.2f} USDT</b>

{e(EMOJI['support'])} Поддержка: @{SUPPORT_USERNAME}
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

@router.message(Command("approve"))
async def cmd_approve(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        payment_id = int(message.text.split()[1])
        payment = await db.approve_rub_payment(payment_id)
        if payment:
            await db.update_balance(payment["user_id"], payment["amount_usdt"])
            try:
                await bot.send_message(
                    payment["user_id"],
                    f"{e(EMOJI['check'])} <b>Платёж одобрен!</b>\n\n"
                    f"{e(EMOJI['money'])} +{payment['amount_usdt']:.2f} USDT",
                    parse_mode=ParseMode.HTML
                )
            except:
                pass
            await message.answer(
                f"{e(EMOJI['check'])} Платёж #{payment_id} одобрен. "
                f"{payment['amount_usdt']:.2f} USDT начислено пользователю {payment['user_id']}."
            )
        else:
            await message.answer(f"{e(EMOJI['cross'])} Платёж #{payment_id} не найден или уже обработан.")
    except (IndexError, ValueError):
        await message.answer("Используйте: /approve ID_платежа")

@router.message(Command("reject"))
async def cmd_reject(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        payment_id = int(message.text.split()[1])
        payment = await db.reject_rub_payment(payment_id)
        if payment:
            try:
                await bot.send_message(
                    payment["user_id"],
                    f"{e(EMOJI['cross'])} <b>Платёж отклонён.</b>\n\n"
                    f"Свяжитесь с поддержкой: @{SUPPORT_USERNAME}",
                    parse_mode=ParseMode.HTML
                )
            except:
                pass
            await message.answer(
                f"{e(EMOJI['check'])} Платёж #{payment_id} отклонён. "
                f"Пользователь {payment['user_id']} уведомлён."
            )
        else:
            await message.answer(f"{e(EMOJI['cross'])} Платёж #{payment_id} не найден или уже обработан.")
    except (IndexError, ValueError):
        await message.answer("Используйте: /reject ID_платежа")

@router.message(Command("id"))
async def cmd_id(message: Message):
    await message.answer(f"Ваш ID: <code>{message.from_user.id}</code>", parse_mode=ParseMode.HTML)

@router.message(Command("support"))
async def cmd_support(message: Message):
    text = f"""
{e(EMOJI['support'])} <b>ПОДДЕРЖКА</b>

{e(EMOJI['link'])} <b><a href='https://t.me/{SUPPORT_USERNAME}'>@{SUPPORT_USERNAME}</a></b>
"""
    await send_with_media(message, "support", text, support_keyboard())

# ========== ОСНОВНЫЕ КНОПКИ ==========
@router.message(F.text == "Профиль")
async def profile(message: Message):
    user = await db.get_user(message.from_user.id)
    stats = user["stats"]
    text = f"""
{e(EMOJI['profile'])} <b>ПРОФИЛЬ</b>

{e(EMOJI['wallet'])} <b>Баланс:</b> {user['balance']:.2f} USDT

{e(EMOJI['stats'])} <b>СТАТИСТИКА:</b>

{e(EMOJI['dice'])} <b>Кубик:</b> {e(EMOJI['check'])} Побед: {stats['dice']['wins']} | {e(EMOJI['cross'])} Поражений: {stats['dice']['losses']}
{e(EMOJI['basketball'])} <b>Баскетбол:</b> {e(EMOJI['check'])} Побед: {stats['basketball']['wins']} | {e(EMOJI['cross'])} Поражений: {stats['basketball']['losses']}
{e(EMOJI['football'])} <b>Футбол:</b> {e(EMOJI['check'])} Побед: {stats['football']['wins']} | {e(EMOJI['cross'])} Поражений: {stats['football']['losses']}
{e(EMOJI['blackjack'])} <b>Блэкджек:</b> {e(EMOJI['check'])} Побед: {stats['blackjack']['wins']} | {e(EMOJI['cross'])} Поражений: {stats['blackjack']['losses']}
{e(EMOJI['bowling'])} <b>Боулинг:</b> {e(EMOJI['check'])} Побед: {stats['bowling']['wins']} | {e(EMOJI['cross'])} Поражений: {stats['bowling']['losses']}
{e(EMOJI['slots'])} <b>Слоты:</b> {e(EMOJI['check'])} Побед: {stats['slots']['wins']} | {e(EMOJI['cross'])} Поражений: {stats['slots']['losses']}

{e(EMOJI['graph'])} <b>ВСЕГО:</b>
{e(EMOJI['money'])} Выиграно: {stats['total_won']:.2f} USDT
{e(EMOJI['cross'])} Проиграно: {stats['total_lost']:.2f} USDT
"""
    await send_with_media(message, "profile", text)

@router.message(F.text == "Игры")
async def games_menu(message: Message):
    text = f"""
{e(EMOJI['games'])} <b>ВЫБЕРИТЕ ИГРУ</b>

Минимальная ставка: <b>0.1 USDT</b>
"""
    await send_with_media(message, "games", text, games_menu_keyboard())

@router.message(F.text == "Поддержка")
async def support_button(message: Message):
    text = f"""
{e(EMOJI['support'])} <b>ПОДДЕРЖКА</b>

{e(EMOJI['link'])} <b><a href='https://t.me/{SUPPORT_USERNAME}'>@{SUPPORT_USERNAME}</a></b>
"""
    await send_with_media(message, "support", text, support_keyboard())

@router.message(F.text == "Пополнить")
async def deposit_start(message: Message):
    user = await db.get_user(message.from_user.id)
    
    if not user.get("privacy_accepted"):
        text = f"""
{e(EMOJI['info'])} <b>ПОЛИТИКА КОНФИДЕНЦИАЛЬНОСТИ</b>

Перед пополнением баланса необходимо ознакомиться с политикой конфиденциальности.

{e(EMOJI['link'])} <b><a href='{PRIVACY_URL}'>Ознакомиться</a></b>
"""
        await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=privacy_keyboard())
        return
    
    text = f"""
{e(EMOJI['wallet'])} <b>ПОПОЛНЕНИЕ БАЛАНСА</b>

Выберите способ пополнения:

{e(EMOJI['crypto'])} <b>Crypto Bot</b> — пополнение в USDT
{e(EMOJI['rub'])} <b>Рубли (ЮМАНИ)</b> — от 10₽
{e(EMOJI['star'])} <b>Звёзды Telegram</b> — написать в поддержку
"""
    await send_with_media(message, "deposit", text, deposit_method_keyboard())

@router.callback_query(F.data == "accept_privacy")
async def accept_privacy_callback(callback: CallbackQuery):
    await db.accept_privacy(callback.from_user.id)
    await callback.message.delete()
    await callback.message.answer(
        f"{e(EMOJI['check'])} <b>Спасибо!</b>\n\n"
        f"Вы ознакомились с политикой конфиденциальности.\n"
        f"Теперь вы можете пополнить баланс.",
        parse_mode=ParseMode.HTML
    )
    text = f"""
{e(EMOJI['wallet'])} <b>ПОПОЛНЕНИЕ БАЛАНСА</b>

Выберите способ пополнения:

{e(EMOJI['crypto'])} <b>Crypto Bot</b> — пополнение в USDT
{e(EMOJI['rub'])} <b>Рубли (ЮМАНИ)</b> — от 10₽
{e(EMOJI['star'])} <b>Звёзды Telegram</b> — написать в поддержку
"""
    await send_with_media(callback.message, "deposit", text, deposit_method_keyboard())
    await callback.answer()

@router.callback_query(F.data == "deposit_crypto")
async def deposit_crypto_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        f"{e(EMOJI['crypto'])} <b>ПОПОЛНЕНИЕ CRYPTO BOT</b>\n\n"
        f"Введите сумму в USDT (мин. 0.1):",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_keyboard()
    )
    await state.set_state(DepositState.waiting_for_amount_crypto)
    await callback.answer()

@router.message(DepositState.waiting_for_amount_crypto)
async def deposit_crypto_amount(message: Message, state: FSMContext):
    try:
        amount = float(message.text.replace(",", "."))
        if amount < 0.1:
            await message.answer(f"{e(EMOJI['cross'])} Минимум 0.1 USDT", parse_mode=ParseMode.HTML)
            return
        
        invoice = await create_crypto_invoice(amount)
        if invoice:
            pay_url = invoice['pay_url']
            text = f"""
{e(EMOJI['crypto'])} <b>СЧЁТ НА ОПЛАТУ</b>

{e(EMOJI['money'])} Сумма: <b>{amount:.2f} USDT</b>

{e(EMOJI['link'])} <b><a href='{pay_url}'>НАЖМИТЕ ДЛЯ ОПЛАТЫ</a></b>

Оплата проверяется автоматически...
"""
            msg = await message.answer(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
            asyncio.create_task(crypto_payment_check_loop(message.from_user.id, msg, invoice["invoice_id"]))
        else:
            await message.answer(f"{e(EMOJI['cross'])} Ошибка создания счёта", parse_mode=ParseMode.HTML)
    except ValueError:
        await message.answer(f"{e(EMOJI['cross'])} Введите число", parse_mode=ParseMode.HTML)
    await state.clear()

@router.callback_query(F.data == "deposit_rub")
async def deposit_rub_start(callback: CallbackQuery, state: FSMContext):
    text = f"""
{e(EMOJI['rub'])} <b>ПОПОЛНЕНИЕ РУБЛЯМИ</b>

{e(EMOJI['info'])} <b>Реквизиты:</b>
• Телефон: <code>{RUB_REQUISITES['phone']}</code>
• Банк: <b>{RUB_REQUISITES['bank']}</b>
• Получатель: <b>{RUB_REQUISITES['recipient']}</b>

{e(EMOJI['money'])} Курс: 1 USDT = {USDT_RUB_RATE}₽

Введите сумму в <b>рублях</b> (мин. 10₽):
"""
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=cancel_keyboard())
    await state.set_state(DepositState.waiting_for_amount_rub)
    await callback.answer()

@router.message(DepositState.waiting_for_amount_rub)
async def deposit_rub_amount(message: Message, state: FSMContext):
    try:
        amount_rub = float(message.text.replace(",", "."))
        if amount_rub < 10:
            await message.answer(f"{e(EMOJI['cross'])} Минимум 10₽", parse_mode=ParseMode.HTML)
            return
        
        amount_usdt = round(amount_rub / USDT_RUB_RATE, 2)
        await state.update_data(rub_amount=amount_rub, rub_usdt=amount_usdt)
        
        text = f"""
{e(EMOJI['rub'])} <b>ПОДТВЕРЖДЕНИЕ</b>

Сумма: <b>{amount_rub}₽</b> ({amount_usdt:.2f} USDT)

Отправьте <b>скриншот</b> успешного перевода.
"""
        await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=cancel_keyboard())
        await state.set_state(DepositState.waiting_for_rub_screenshot)
    except ValueError:
        await message.answer(f"{e(EMOJI['cross'])} Введите число", parse_mode=ParseMode.HTML)

@router.message(DepositState.waiting_for_rub_screenshot, F.photo)
async def deposit_rub_screenshot(message: Message, state: FSMContext):
    data = await state.get_data()
    amount_rub = data.get("rub_amount")
    amount_usdt = data.get("rub_usdt")
    
    if not amount_rub or not amount_usdt:
        await message.answer(
            f"{e(EMOJI['cross'])} Ошибка, попробуйте заново.",
            parse_mode=ParseMode.HTML,
            reply_markup=deposit_method_keyboard()
        )
        await state.clear()
        return
    
    payment_id = await db.create_rub_payment(message.from_user.id, amount_rub, amount_usdt)
    
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_photo(
                admin_id,
                photo=message.photo[-1].file_id,
                caption=(
                    f"{e(EMOJI['rub'])} <b>ПОПОЛНЕНИЕ РУБЛЯМИ</b>\n\n"
                    f"ID платежа: <code>{payment_id}</code>\n"
                    f"User ID: <code>{message.from_user.id}</code>\n"
                    f"Сумма: <b>{amount_rub}₽</b> ({amount_usdt:.2f} USDT)\n\n"
                    f"<b>/approve {payment_id}</b> — одобрить\n"
                    f"<b>/reject {payment_id}</b> — отклонить"
                ),
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Failed to send to admin {admin_id}: {e}")
    
    await message.answer(
        f"{e(EMOJI['check'])} <b>Скриншот отправлен на проверку!</b>\n\n"
        f"ID платежа: <code>{payment_id}</code>\n"
        f"После проверки средства будут зачислены.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard()
    )
    await state.clear()

@router.message(DepositState.waiting_for_rub_screenshot)
async def deposit_rub_screenshot_wrong(message: Message):
    await message.answer(
        f"{e(EMOJI['cross'])} Отправьте скриншот (фото).",
        parse_mode=ParseMode.HTML,
        reply_markup=cancel_keyboard()
    )

@router.callback_query(F.data == "deposit_stars")
async def deposit_stars_callback(callback: CallbackQuery):
    text = f"""
{e(EMOJI['star'])} <b>ПОПОЛНЕНИЕ ЗВЁЗДАМИ</b>

Для пополнения баланса звёздами Telegram, напишите в поддержку:

{e(EMOJI['link'])} <b><a href='https://t.me/{SUPPORT_USERNAME}'>@{SUPPORT_USERNAME}</a></b>

Укажите:
• Ваш ID: <code>{callback.from_user.id}</code>
• Сумму пополнения в USDT
• Количество звёзд для списания
"""
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=back_to_menu_keyboard())
    await callback.answer()

@router.message(F.text == "Вывод")
async def withdraw_start(message: Message):
    text = f"""
{e(EMOJI['withdraw'])} <b>ВЫВОД СРЕДСТВ</b>

Для вывода средств напишите в поддержку:

{e(EMOJI['link'])} <b><a href='https://t.me/{SUPPORT_USERNAME}'>@{SUPPORT_USERNAME}</a></b>

Укажите:
• Ваш ID: <code>{message.from_user.id}</code>
• Сумму вывода в USDT
"""
    await send_with_media(message, "withdraw", text, support_keyboard())

@router.message(F.text == "Помощь")
async def help_cmd(message: Message):
    text = f"""
{e(EMOJI['info'])} <b>ПОМОЩЬ</b>

{e(EMOJI['wallet'])} <b>ФИНАНСЫ:</b>
• Пополнение от 0.1 USDT (крипто) / 10₽ (рубли)
• Crypto Bot / Рубли (ЮМАНИ) / Звёзды
• Курс RUB: 1 USDT = {USDT_RUB_RATE}₽
• Вывод через поддержку

{e(EMOJI['games'])} <b>ИГРЫ:</b>
• Кубик (6 режимов)
• Баскетбол (3 режима)
• Футбол (4 режима)
• Блэкджек (x2)
• Боулинг (3 режима)
• Слоты (3 линии)

{e(EMOJI['link'])} <b><a href='{PRIVACY_URL}'>Политика конфиденциальности</a></b>

{e(EMOJI['settings'])} <b>Команды:</b>
/admin — админ-панель
/id — узнать свой ID
/support — поддержка
"""
    await send_with_media(message, "help", text)

# ========== СЛОТЫ ==========
@router.callback_query(F.data == "game_slots")
async def game_slots(callback: CallbackQuery):
    user = await db.get_user(callback.from_user.id)
    text = f"""
{e(EMOJI['slots'])} <b>СЛОТЫ</b>

{e(EMOJI['wallet'])} Баланс: <b>{user['balance']:.2f} USDT</b>

{e(EMOJI['money'])} Множители:
• 👑👑👑 — x10 | 💎💎💎 — x8
• 7️⃣7️⃣7️⃣ — x5 | 🍒🍒🍒 и др. — x3
• Два одинаковых — x1.5

Выберите ставку:
"""
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=slots_bet_keyboard())
    await callback.answer()

@router.callback_query(F.data.startswith("slots_bet_"))
async def slots_set_bet(callback: CallbackQuery):
    parts = callback.data.split("_")
    bet_type = parts[2]
    user_id = callback.from_user.id
    user = await db.get_user(user_id)
    
    if bet_type == "custom":
        await callback.message.edit_text(
            f"{e(EMOJI['slots'])} <b>СЛОТЫ</b>\n\n"
            f"{e(EMOJI['wallet'])} Баланс: {user['balance']:.2f} USDT\n\n"
            f"Введите сумму ставки (мин. 0.1):",
            parse_mode=ParseMode.HTML
        )
        user_bets[user_id] = {"game": "slots", "awaiting_custom": True}
        await callback.answer()
        return
    
    bet = float(bet_type)
    if user["balance"] < bet:
        await callback.answer("Недостаточно средств!", show_alert=True)
        return
    if bet < 0.1:
        await callback.answer("Минимальная ставка 0.1 USDT", show_alert=True)
        return
    
    user_bets[user_id] = {"game": "slots", "bet": bet, "awaiting_custom": False}
    
    result = spin_slots()
    win_mult = get_slots_win(result)
    
    if win_mult > 0:
        win_amount = bet * win_mult
        await db.update_balance(user_id, win_amount - bet)
        await db.add_game_stat(user_id, "slots", True)
        text = (
            f"{e(EMOJI['slots'])} <b>СЛОТЫ</b>\n\n"
            f"{' '.join(result)}\n\n"
            f"{e(EMOJI['check'])} <b>ПОБЕДА! x{win_mult}</b>\n"
            f"{e(EMOJI['money'])} +{win_amount - bet:.2f} USDT"
        )
    else:
        await db.update_balance(user_id, -bet)
        await db.add_game_stat(user_id, "slots", False)
        text = (
            f"{e(EMOJI['slots'])} <b>СЛОТЫ</b>\n\n"
            f"{' '.join(result)}\n\n"
            f"{e(EMOJI['cross'])} <b>ПОРАЖЕНИЕ</b>\n"
            f"{e(EMOJI['money'])} -{bet:.2f} USDT"
        )
    
    new_balance = (await db.get_user(user_id))["balance"]
    text += f"\n{e(EMOJI['wallet'])} Баланс: <b>{new_balance:.2f} USDT</b>"
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=play_again_keyboard("slots"))
    await callback.answer()

@router.callback_query(F.data == "slots_change_bet")
async def slots_change_bet(callback: CallbackQuery):
    user = await db.get_user(callback.from_user.id)
    text = f"""
{e(EMOJI['slots'])} <b>СЛОТЫ</b>

{e(EMOJI['wallet'])} Баланс: <b>{user['balance']:.2f} USDT</b>

Выберите ставку:
"""
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=slots_bet_keyboard())
    await callback.answer()

@router.callback_query(F.data == "slots_spin")
async def slots_spin(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in user_bets or user_bets[user_id].get("game") != "slots":
        await callback.answer("Сначала выберите ставку!", show_alert=True)
        return
    
    bet = user_bets[user_id]["bet"]
    user = await db.get_user(user_id)
    if user["balance"] < bet:
        await callback.answer("Недостаточно средств!", show_alert=True)
        return
    
    result = spin_slots()
    win_mult = get_slots_win(result)
    
    if win_mult > 0:
        win_amount = bet * win_mult
        await db.update_balance(user_id, win_amount - bet)
        await db.add_game_stat(user_id, "slots", True)
        text = (
            f"{e(EMOJI['slots'])} <b>СЛОТЫ</b>\n\n"
            f"{' '.join(result)}\n\n"
            f"{e(EMOJI['check'])} <b>ПОБЕДА! x{win_mult}</b>\n"
            f"{e(EMOJI['money'])} +{win_amount - bet:.2f} USDT"
        )
    else:
        await db.update_balance(user_id, -bet)
        await db.add_game_stat(user_id, "slots", False)
        text = (
            f"{e(EMOJI['slots'])} <b>СЛОТЫ</b>\n\n"
            f"{' '.join(result)}\n\n"
            f"{e(EMOJI['cross'])} <b>ПОРАЖЕНИЕ</b>\n"
            f"{e(EMOJI['money'])} -{bet:.2f} USDT"
        )
    
    new_balance = (await db.get_user(user_id))["balance"]
    text += f"\n{e(EMOJI['wallet'])} Баланс: <b>{new_balance:.2f} USDT</b>"
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=play_again_keyboard("slots"))
    await callback.answer()

# ========== ОБРАБОТЧИКИ ИГР ==========
@router.callback_query(F.data == "back_to_games")
async def back_to_games(callback: CallbackQuery):
    text = f"""
{e(EMOJI['games'])} <b>ВЫБЕРИТЕ ИГРУ</b>

Минимальная ставка: <b>0.1 USDT</b>
"""
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=games_menu_keyboard())
    await callback.answer()

@router.callback_query(F.data == "back_to_menu_msg")
async def back_to_menu_msg(callback: CallbackQuery):
    await callback.message.delete()
    await callback.message.answer(
        f"{e(EMOJI['home'])} <b>Vest Casino</b>\n\nВыберите действие:",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard()
    )
    await callback.answer()

# ========== КУБИК ==========
@router.callback_query(F.data == "game_dice")
async def game_dice(callback: CallbackQuery):
    user = await db.get_user(callback.from_user.id)
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
    user = await db.get_user(callback.from_user.id)
    
    multipliers = {"highlow": 1.85, "evenodd": 1.85, "number": 5, "twodice": 4, "threedice": 3, "lucky": 2.5}
    mode_names = {
        "highlow": "1-3 / 4-6", "evenodd": "Чёт / Нечет", "number": "Угадать число",
        "twodice": "Два кубика: сумма 7", "threedice": "Три кубика: 10-11", "lucky": "Счастливое: 1 или 6"
    }
    
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
    user = await db.get_user(callback.from_user.id)
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
    user = await db.get_user(user_id)
    data = await state.get_data()
    mode = data.get("dice_mode", "highlow")
    
    if bet_type == "custom":
        await callback.message.edit_text(
            f"{e(EMOJI['dice'])} <b>КУБИК</b>\n\n"
            f"{e(EMOJI['wallet'])} Баланс: {user['balance']:.2f} USDT\n\n"
            f"Введите сумму ставки (мин. 0.1):",
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
    user = await db.get_user(user_id)
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
        await db.update_balance(user_id, win_amount - bet)
        await db.add_game_stat(user_id, "dice", True)
        text = (
            f"{e(EMOJI['dice'])} <b>КУБИК</b>\n\n{result}\n\n"
            f"{e(EMOJI['check'])} <b>ПОБЕДА!</b>\n"
            f"{e(EMOJI['money'])} +{win_amount - bet:.2f} USDT"
        )
    else:
        await db.update_balance(user_id, -bet)
        await db.add_game_stat(user_id, "dice", False)
        text = (
            f"{e(EMOJI['dice'])} <b>КУБИК</b>\n\n{result}\n\n"
            f"{e(EMOJI['cross'])} <b>ПОРАЖЕНИЕ</b>\n"
            f"{e(EMOJI['money'])} -{bet:.2f} USDT"
        )
    
    new_balance = (await db.get_user(user_id))["balance"]
    text += f"\n{e(EMOJI['wallet'])} Баланс: <b>{new_balance:.2f} USDT</b>"
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=play_again_keyboard("dice"))

@router.callback_query(F.data == "dice_change_bet")
async def dice_change_bet(callback: CallbackQuery, state: FSMContext):
    user = await db.get_user(callback.from_user.id)
    data = await state.get_data()
    mode = data.get("dice_mode", "highlow")
    multipliers = {"highlow": 1.85, "evenodd": 1.85, "number": 5, "twodice": 4, "threedice": 3, "lucky": 2.5}
    mode_names = {
        "highlow": "1-3 / 4-6", "evenodd": "Чёт / Нечет", "number": "Угадать число",
        "twodice": "Два кубика: сумма 7", "threedice": "Три кубика: 10-11", "lucky": "Счастливое: 1 или 6"
    }
    
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
    user = await db.get_user(user_id)
    
    if user["balance"] < bet:
        await callback.answer("Недостаточно средств!", show_alert=True)
        return
    
    choice = callback.data
    roll = random.randint(1, 6)
    is_win = False
    mult = {"highlow": 1.85, "evenodd": 1.85, "number": 5, "lucky": 2.5}[mode]
    
    if mode == "highlow":
        is_win = (choice == "dice_low" and roll <= 3) or (choice == "dice_high" and roll >= 4)
        choice_text = "1-3" if choice == "dice_low" else "4-6"
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
        await db.update_balance(user_id, win_amount - bet)
        await db.add_game_stat(user_id, "dice", True)
        text = (
            f"{e(EMOJI['dice'])} <b>КУБИК</b>\n\n"
            f"Ваш выбор: <b>{choice_text}</b>\n"
            f"{e(EMOJI['dice'])} Выпало: <b>{roll}</b>\n\n"
            f"{e(EMOJI['check'])} <b>ПОБЕДА!</b>\n"
            f"{e(EMOJI['money'])} +{win_amount - bet:.2f} USDT"
        )
    else:
        await db.update_balance(user_id, -bet)
        await db.add_game_stat(user_id, "dice", False)
        text = (
            f"{e(EMOJI['dice'])} <b>КУБИК</b>\n\n"
            f"Ваш выбор: <b>{choice_text}</b>\n"
            f"{e(EMOJI['dice'])} Выпало: <b>{roll}</b>\n\n"
            f"{e(EMOJI['cross'])} <b>ПОРАЖЕНИЕ</b>\n"
            f"{e(EMOJI['money'])} -{bet:.2f} USDT"
        )
    
    new_balance = (await db.get_user(user_id))["balance"]
    text += f"\n{e(EMOJI['wallet'])} Баланс: <b>{new_balance:.2f} USDT</b>"
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=play_again_keyboard("dice"))
    await callback.answer()

# ========== БАСКЕТБОЛ ==========
@router.callback_query(F.data == "game_basketball")
async def game_basketball(callback: CallbackQuery):
    user = await db.get_user(callback.from_user.id)
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
    user = await db.get_user(user_id)
    
    if bet_type == "custom":
        await callback.message.edit_text(
            f"{e(EMOJI['basketball'])} <b>БАСКЕТБОЛ</b>\n\n"
            f"{e(EMOJI['wallet'])} Баланс: {user['balance']:.2f} USDT\n\n"
            f"Введите сумму ставки (мин. 0.1):",
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
    user = await db.get_user(callback.from_user.id)
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
    user = await db.get_user(user_id)
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
        await db.update_balance(user_id, win_amount - bet)
        await db.add_game_stat(user_id, "basketball", True)
        text = (
            f"{e(EMOJI['basketball'])} <b>БАСКЕТБОЛ</b>\n\n"
            f"Ваш выбор: <b>{choice_text}</b>\nРезультат: <b>{result_desc}</b>\n\n"
            f"{e(EMOJI['check'])} <b>ПОБЕДА!</b>\n"
            f"{e(EMOJI['money'])} +{win_amount - bet:.2f} USDT"
        )
    else:
        await db.update_balance(user_id, -bet)
        await db.add_game_stat(user_id, "basketball", False)
        text = (
            f"{e(EMOJI['basketball'])} <b>БАСКЕТБОЛ</b>\n\n"
            f"Ваш выбор: <b>{choice_text}</b>\nРезультат: <b>{result_desc}</b>\n\n"
            f"{e(EMOJI['cross'])} <b>ПОРАЖЕНИЕ</b>\n"
            f"{e(EMOJI['money'])} -{bet:.2f} USDT"
        )
    
    new_balance = (await db.get_user(user_id))["balance"]
    text += f"\n{e(EMOJI['wallet'])} Баланс: <b>{new_balance:.2f} USDT</b>"
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=play_again_keyboard("basketball"))
    await callback.answer()

# ========== ФУТБОЛ ==========
@router.callback_query(F.data == "game_football")
async def game_football(callback: CallbackQuery):
    user = await db.get_user(callback.from_user.id)
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
    user = await db.get_user(callback.from_user.id)
    
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
    user = await db.get_user(user_id)
    data = await state.get_data()
    mode = data.get("football_mode", "goal")
    
    if bet_type == "custom":
        await callback.message.edit_text(
            f"{e(EMOJI['football'])} <b>ФУТБОЛ</b>\n\n"
            f"{e(EMOJI['wallet'])} Баланс: {user['balance']:.2f} USDT\n\n"
            f"Введите сумму ставки (мин. 0.1):",
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
    user = await db.get_user(user_id)
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
        await db.update_balance(user_id, win_amount - bet)
        await db.add_game_stat(user_id, "football", True)
        text = (
            f"{e(EMOJI['football'])} <b>ФУТБОЛ</b>\n\n"
            f"Режим: <b>{names[mode]}</b>\nРезультат: <b>{result}</b>\n\n"
            f"{e(EMOJI['check'])} <b>ПОБЕДА!</b>\n"
            f"{e(EMOJI['money'])} +{win_amount - bet:.2f} USDT"
        )
    else:
        await db.update_balance(user_id, -bet)
        await db.add_game_stat(user_id, "football", False)
        text = (
            f"{e(EMOJI['football'])} <b>ФУТБОЛ</b>\n\n"
            f"Режим: <b>{names[mode]}</b>\nРезультат: <b>{result}</b>\n\n"
            f"{e(EMOJI['cross'])} <b>ПОРАЖЕНИЕ</b>\n"
            f"{e(EMOJI['money'])} -{bet:.2f} USDT"
        )
    
    new_balance = (await db.get_user(user_id))["balance"]
    text += f"\n{e(EMOJI['wallet'])} Баланс: <b>{new_balance:.2f} USDT</b>"
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=play_again_keyboard("football"))

@router.callback_query(F.data == "football_change_bet")
async def football_change_bet(callback: CallbackQuery, state: FSMContext):
    user = await db.get_user(callback.from_user.id)
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

# ========== БЛЭКДЖЕК ==========
@router.callback_query(F.data == "game_blackjack")
async def game_blackjack(callback: CallbackQuery):
    user = await db.get_user(callback.from_user.id)
    text = f"""
{e(EMOJI['blackjack'])} <b>БЛЭКДЖЕК</b>

{e(EMOJI['wallet'])} Баланс: <b>{user['balance']:.2f} USDT</b>
{e(EMOJI['money'])} Множитель: <b>x2</b>

Выберите ставку:
"""
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=blackjack_bet_keyboard())
    await callback.answer()

@router.callback_query(F.data.startswith("blackjack_bet_"))
async def blackjack_set_bet(callback: CallbackQuery):
    parts = callback.data.split("_")
    bet_type = parts[2]
    user_id = callback.from_user.id
    user = await db.get_user(user_id)
    
    if bet_type == "custom":
        await callback.message.edit_text(
            f"{e(EMOJI['blackjack'])} <b>БЛЭКДЖЕК</b>\n\n"
            f"{e(EMOJI['wallet'])} Баланс: {user['balance']:.2f} USDT\n\n"
            f"Введите сумму ставки (мин. 0.1):",
            parse_mode=ParseMode.HTML
        )
        user_bets[user_id] = {"game": "blackjack", "awaiting_custom": True}
        await callback.answer()
        return
    
    bet = float(bet_type)
    if user["balance"] < bet:
        await callback.answer("Недостаточно средств!", show_alert=True)
        return
    if bet < 0.1:
        await callback.answer("Минимальная ставка 0.1 USDT", show_alert=True)
        return
    
    deck = create_deck()
    player_hand = [deck.pop(), deck.pop()]
    dealer_hand = [deck.pop(), deck.pop()]
    
    user_bets[user_id] = {
        "game": "blackjack", "bet": bet, "deck": deck,
        "player_hand": player_hand, "dealer_hand": dealer_hand, "awaiting_custom": False
    }
    
    player_value = get_hand_value(player_hand)
    dealer_visible = dealer_hand[0]
    
    text = f"""
{e(EMOJI['blackjack'])} <b>БЛЭКДЖЕК</b>

{e(EMOJI['money'])} Ставка: <b>{bet:.2f} USDT</b>

Ваши карты: <b>{format_hand(player_hand)}</b> ({player_value})
Карта дилера: <b>{dealer_visible}</b> + ?

Выберите действие:
"""
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=blackjack_keyboard())
    await callback.answer()

@router.callback_query(F.data == "blackjack_change_bet")
async def blackjack_change_bet(callback: CallbackQuery):
    user = await db.get_user(callback.from_user.id)
    text = f"""
{e(EMOJI['blackjack'])} <b>БЛЭКДЖЕК</b>

{e(EMOJI['wallet'])} Баланс: <b>{user['balance']:.2f} USDT</b>
{e(EMOJI['money'])} Множитель: <b>x2</b>

Выберите ставку:
"""
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=blackjack_bet_keyboard())
    await callback.answer()

@router.callback_query(F.data.in_(["bj_hit", "bj_stand"]))
async def blackjack_play(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in user_bets or user_bets[user_id].get("game") != "blackjack":
        await callback.answer("Игра не найдена!", show_alert=True)
        return
    
    game_data = user_bets[user_id]
    bet = game_data["bet"]
    deck = game_data["deck"]
    player_hand = game_data["player_hand"]
    dealer_hand = game_data["dealer_hand"]
    
    choice = callback.data
    
    if choice == "bj_hit":
        player_hand.append(deck.pop())
        player_value = get_hand_value(player_hand)
        
        if player_value > 21:
            await db.update_balance(user_id, -bet)
            await db.add_game_stat(user_id, "blackjack", False)
            dealer_value = get_hand_value(dealer_hand)
            
            text = (
                f"{e(EMOJI['blackjack'])} <b>БЛЭКДЖЕК</b>\n\n"
                f"Ваши карты: <b>{format_hand(player_hand)}</b> ({player_value}) — <b>ПЕРЕБОР!</b>\n"
                f"Карты дилера: <b>{format_hand(dealer_hand)}</b> ({dealer_value})\n\n"
                f"{e(EMOJI['cross'])} <b>ПОРАЖЕНИЕ</b>\n"
                f"{e(EMOJI['money'])} -{bet:.2f} USDT"
            )
            new_balance = (await db.get_user(user_id))["balance"]
            text += f"\n{e(EMOJI['wallet'])} Баланс: <b>{new_balance:.2f} USDT</b>"
            await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=play_again_keyboard("blackjack"))
            del user_bets[user_id]
            await callback.answer()
            return
        
        dealer_visible = dealer_hand[0]
        text = f"""
{e(EMOJI['blackjack'])} <b>БЛЭКДЖЕК</b>

{e(EMOJI['money'])} Ставка: <b>{bet:.2f} USDT</b>

Ваши карты: <b>{format_hand(player_hand)}</b> ({player_value})
Карта дилера: <b>{dealer_visible}</b> + ?

Выберите действие:
"""
        await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=blackjack_keyboard())
        await callback.answer()
        return
    
    while get_hand_value(dealer_hand) < 17:
        dealer_hand.append(deck.pop())
    
    player_value = get_hand_value(player_hand)
    dealer_value = get_hand_value(dealer_hand)
    
    if dealer_value > 21 or player_value > dealer_value:
        win_amount = bet * 2
        await db.update_balance(user_id, win_amount - bet)
        await db.add_game_stat(user_id, "blackjack", True)
        text = (
            f"{e(EMOJI['blackjack'])} <b>БЛЭКДЖЕК</b>\n\n"
            f"Ваши карты: <b>{format_hand(player_hand)}</b> ({player_value})\n"
            f"Карты дилера: <b>{format_hand(dealer_hand)}</b> ({dealer_value})\n\n"
            f"{e(EMOJI['check'])} <b>ПОБЕДА!</b>\n"
            f"{e(EMOJI['money'])} +{win_amount - bet:.2f} USDT"
        )
    elif player_value == dealer_value:
        text = (
            f"{e(EMOJI['blackjack'])} <b>БЛЭКДЖЕК</b>\n\n"
            f"Ваши карты: <b>{format_hand(player_hand)}</b> ({player_value})\n"
            f"Карты дилера: <b>{format_hand(dealer_hand)}</b> ({dealer_value})\n\n"
            f"<b>НИЧЬЯ!</b>\nСтавка возвращена."
        )
    else:
        await db.update_balance(user_id, -bet)
        await db.add_game_stat(user_id, "blackjack", False)
        text = (
            f"{e(EMOJI['blackjack'])} <b>БЛЭКДЖЕК</b>\n\n"
            f"Ваши карты: <b>{format_hand(player_hand)}</b> ({player_value})\n"
            f"Карты дилера: <b>{format_hand(dealer_hand)}</b> ({dealer_value})\n\n"
            f"{e(EMOJI['cross'])} <b>ПОРАЖЕНИЕ</b>\n"
            f"{e(EMOJI['money'])} -{bet:.2f} USDT"
        )
    
    new_balance = (await db.get_user(user_id))["balance"]
    text += f"\n{e(EMOJI['wallet'])} Баланс: <b>{new_balance:.2f} USDT</b>"
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=play_again_keyboard("blackjack"))
    del user_bets[user_id]
    await callback.answer()

# ========== БОУЛИНГ ==========
@router.callback_query(F.data == "game_bowling")
async def game_bowling(callback: CallbackQuery):
    user = await db.get_user(callback.from_user.id)
    text = f"""
{e(EMOJI['bowling'])} <b>БОУЛИНГ</b>

{e(EMOJI['wallet'])} Баланс: <b>{user['balance']:.2f} USDT</b>

{e(EMOJI['money'])} Множители:
• Страйк (все 10): <b>x10</b>
• Спэр (с двух): <b>x5</b>
• 7+ кеглей: <b>x2</b>

Выберите ставку:
"""
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=bowling_bet_keyboard())
    await callback.answer()

@router.callback_query(F.data.startswith("bowling_bet_"))
async def bowling_set_bet(callback: CallbackQuery):
    parts = callback.data.split("_")
    bet_type = parts[2]
    user_id = callback.from_user.id
    user = await db.get_user(user_id)
    
    if bet_type == "custom":
        await callback.message.edit_text(
            f"{e(EMOJI['bowling'])} <b>БОУЛИНГ</b>\n\n"
            f"{e(EMOJI['wallet'])} Баланс: {user['balance']:.2f} USDT\n\n"
            f"Введите сумму ставки (мин. 0.1):",
            parse_mode=ParseMode.HTML
        )
        user_bets[user_id] = {"game": "bowling", "awaiting_custom": True}
        await callback.answer()
        return
    
    bet = float(bet_type)
    if user["balance"] < bet:
        await callback.answer("Недостаточно средств!", show_alert=True)
        return
    if bet < 0.1:
        await callback.answer("Минимальная ставка 0.1 USDT", show_alert=True)
        return
    
    user_bets[user_id] = {"game": "bowling", "bet": bet, "awaiting_custom": False}
    
    text = f"""
{e(EMOJI['bowling'])} <b>БОУЛИНГ</b>

{e(EMOJI['money'])} Ставка: <b>{bet:.2f} USDT</b>
{e(EMOJI['wallet'])} Баланс: <b>{user['balance']:.2f} USDT</b>

Выберите исход:
"""
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=bowling_choice_keyboard())
    await callback.answer()

@router.callback_query(F.data == "bowling_change_bet")
async def bowling_change_bet(callback: CallbackQuery):
    user = await db.get_user(callback.from_user.id)
    text = f"""
{e(EMOJI['bowling'])} <b>БОУЛИНГ</b>

{e(EMOJI['wallet'])} Баланс: <b>{user['balance']:.2f} USDT</b>

{e(EMOJI['money'])} Множители:
• Страйк (все 10): <b>x10</b>
• Спэр (с двух): <b>x5</b>
• 7+ кеглей: <b>x2</b>

Выберите ставку:
"""
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=bowling_bet_keyboard())
    await callback.answer()

@router.callback_query(F.data.in_(["bowling_strike", "bowling_spare", "bowling_seven"]))
async def bowling_play(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in user_bets:
        await callback.answer("Сначала выберите ставку!", show_alert=True)
        return
    
    bet = user_bets[user_id]["bet"]
    user = await db.get_user(user_id)
    if user["balance"] < bet:
        await callback.answer("Недостаточно средств!", show_alert=True)
        return
    
    choice = callback.data
    roll1 = random.randint(0, 10)
    if roll1 < 10:
        roll2 = random.randint(0, 10 - roll1)
    else:
        roll2 = 0
    total = roll1 + roll2
    
    is_strike = roll1 == 10
    is_spare = not is_strike and total == 10
    
    if choice == "bowling_strike":
        is_win = is_strike
        mult = 10
        choice_text = "Страйк"
        result_desc = f"Страйк! ({roll1})" if is_strike else f"Не страйк ({roll1}, {roll2})"
    elif choice == "bowling_spare":
        is_win = is_spare
        mult = 5
        choice_text = "Спэр"
        result_desc = f"Спэр! ({roll1} + {roll2})" if is_spare else f"Не спэр ({roll1} + {roll2} = {total})"
    else:
        is_win = total >= 7
        mult = 2
        choice_text = "7+ кеглей"
        result_desc = f"{total} кеглей ({roll1} + {roll2})" if is_win else f"Только {total} кеглей ({roll1} + {roll2})"
    
    if is_win:
        win_amount = bet * mult
        await db.update_balance(user_id, win_amount - bet)
        await db.add_game_stat(user_id, "bowling", True)
        text = (
            f"{e(EMOJI['bowling'])} <b>БОУЛИНГ</b>\n\n"
            f"Ваш выбор: <b>{choice_text}</b>\nРезультат: <b>{result_desc}</b>\n\n"
            f"{e(EMOJI['check'])} <b>ПОБЕДА!</b>\n"
            f"{e(EMOJI['money'])} +{win_amount - bet:.2f} USDT"
        )
    else:
        await db.update_balance(user_id, -bet)
        await db.add_game_stat(user_id, "bowling", False)
        text = (
            f"{e(EMOJI['bowling'])} <b>БОУЛИНГ</b>\n\n"
            f"Ваш выбор: <b>{choice_text}</b>\nРезультат: <b>{result_desc}</b>\n\n"
            f"{e(EMOJI['cross'])} <b>ПОРАЖЕНИЕ</b>\n"
            f"{e(EMOJI['money'])} -{bet:.2f} USDT"
        )
    
    new_balance = (await db.get_user(user_id))["balance"]
    text += f"\n{e(EMOJI['wallet'])} Баланс: <b>{new_balance:.2f} USDT</b>"
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=play_again_keyboard("bowling"))
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
        user = await db.get_user(user_id)
        
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
                user_data = await db.get_user(user_id)
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
                    await db.update_balance(user_id, win_amount - bet)
                    await db.add_game_stat(user_id, "dice", True)
                    text = f"{e(EMOJI['dice'])} <b>КУБИК</b>\n\n{result}\n\n{e(EMOJI['check'])} <b>ПОБЕДА!</b>\n{e(EMOJI['money'])} +{win_amount - bet:.2f} USDT"
                else:
                    await db.update_balance(user_id, -bet)
                    await db.add_game_stat(user_id, "dice", False)
                    text = f"{e(EMOJI['dice'])} <b>КУБИК</b>\n\n{result}\n\n{e(EMOJI['cross'])} <b>ПОРАЖЕНИЕ</b>\n{e(EMOJI['money'])} -{bet:.2f} USDT"
                
                new_balance = (await db.get_user(user_id))["balance"]
                text += f"\n{e(EMOJI['wallet'])} Баланс: <b>{new_balance:.2f} USDT</b>"
                await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=play_again_keyboard("dice"))
            else:
                text = f"{e(EMOJI['dice'])} <b>КУБИК</b>\n\n{e(EMOJI['money'])} Ставка: <b>{bet:.2f} USDT</b>\n{e(EMOJI['wallet'])} Баланс: <b>{user['balance']:.2f} USDT</b>\n\nВыберите вариант:"
                await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=dice_choice_keyboard(mode))
        elif game == "basketball":
            text = f"{e(EMOJI['basketball'])} <b>БАСКЕТБОЛ</b>\n\n{e(EMOJI['money'])} Ставка: <b>{bet:.2f} USDT</b>\n{e(EMOJI['wallet'])} Баланс: <b>{user['balance']:.2f} USDT</b>\n\nВыберите исход:"
            await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=basketball_choice_keyboard())
        elif game == "football":
            mode = user_bets[user_id]["mode"]
            mults = {"goal": 1.85, "miss": 1.5, "penalty": 2, "post": 4}
            mult_val = mults[mode]
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
                win_amount = bet * mult_val
                await db.update_balance(user_id, win_amount - bet)
                await db.add_game_stat(user_id, "football", True)
                text = f"{e(EMOJI['football'])} <b>ФУТБОЛ</b>\n\nРежим: <b>{names[mode]}</b>\nРезультат: <b>{result}</b>\n\n{e(EMOJI['check'])} <b>ПОБЕДА!</b>\n{e(EMOJI['money'])} +{win_amount - bet:.2f} USDT"
            else:
                await db.update_balance(user_id, -bet)
                await db.add_game_stat(user_id, "football", False)
                text = f"{e(EMOJI['football'])} <b>ФУТБОЛ</b>\n\nРежим: <b>{names[mode]}</b>\nРезультат: <b>{result}</b>\n\n{e(EMOJI['cross'])} <b>ПОРАЖЕНИЕ</b>\n{e(EMOJI['money'])} -{bet:.2f} USDT"
            
            new_balance = (await db.get_user(user_id))["balance"]
            text += f"\n{e(EMOJI['wallet'])} Баланс: <b>{new_balance:.2f} USDT</b>"
            await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=play_again_keyboard("football"))
        elif game == "blackjack":
            deck = create_deck()
            player_hand = [deck.pop(), deck.pop()]
            dealer_hand = [deck.pop(), deck.pop()]
            
            user_bets[user_id] = {
                "game": "blackjack", "bet": bet, "deck": deck,
                "player_hand": player_hand, "dealer_hand": dealer_hand, "awaiting_custom": False
            }
            
            player_value = get_hand_value(player_hand)
            dealer_visible = dealer_hand[0]
            
            text = f"""
{e(EMOJI['blackjack'])} <b>БЛЭКДЖЕК</b>

{e(EMOJI['money'])} Ставка: <b>{bet:.2f} USDT</b>

Ваши карты: <b>{format_hand(player_hand)}</b> ({player_value})
Карта дилера: <b>{dealer_visible}</b> + ?

Выберите действие:
"""
            await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=blackjack_keyboard())
        elif game == "bowling":
            text = f"""
{e(EMOJI['bowling'])} <b>БОУЛИНГ</b>

{e(EMOJI['money'])} Ставка: <b>{bet:.2f} USDT</b>
{e(EMOJI['wallet'])} Баланс: <b>{user['balance']:.2f} USDT</b>

Выберите исход:
"""
            await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=bowling_choice_keyboard())
        elif game == "slots":
            result = spin_slots()
            win_mult = get_slots_win(result)
            
            if win_mult > 0:
                win_amount = bet * win_mult
                await db.update_balance(user_id, win_amount - bet)
                await db.add_game_stat(user_id, "slots", True)
                text = f"{e(EMOJI['slots'])} <b>СЛОТЫ</b>\n\n{' '.join(result)}\n\n{e(EMOJI['check'])} <b>ПОБЕДА! x{win_mult}</b>\n{e(EMOJI['money'])} +{win_amount - bet:.2f} USDT"
            else:
                await db.update_balance(user_id, -bet)
                await db.add_game_stat(user_id, "slots", False)
                text = f"{e(EMOJI['slots'])} <b>СЛОТЫ</b>\n\n{' '.join(result)}\n\n{e(EMOJI['cross'])} <b>ПОРАЖЕНИЕ</b>\n{e(EMOJI['money'])} -{bet:.2f} USDT"
            
            new_balance = (await db.get_user(user_id))["balance"]
            text += f"\n{e(EMOJI['wallet'])} Баланс: <b>{new_balance:.2f} USDT</b>"
            await message.answer(text, parse_mode=ParseMode.HTML, reply_markup=play_again_keyboard("slots"))
    except ValueError:
        await message.answer(f"{e(EMOJI['cross'])} Введите число", parse_mode=ParseMode.HTML)

# ========== АДМИН-ПАНЕЛЬ ==========
@router.callback_query(F.data == "admin_panel")
async def admin_panel(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        f"{e(EMOJI['settings'])} <b>Админ-панель</b>\n\nВыберите действие:",
        parse_mode=ParseMode.HTML, reply_markup=admin_panel_keyboard()
    )
    await callback.answer()

@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    db_users = await db.get_all_users()
    total_users = len(db_users)
    total_balance = sum(u["balance"] for u in db_users.values())
    text = f"""
{e(EMOJI['stats'])} <b>СТАТИСТИКА</b>

{e(EMOJI['users'])} Пользователей: <b>{total_users}</b>
{e(EMOJI['wallet'])} Общий баланс: <b>{total_balance:.2f} USDT</b>
"""
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=back_to_admin_keyboard())
    await callback.answer()

@router.callback_query(F.data == "admin_users_list")
async def admin_users_list(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    db_users = await db.get_all_users()
    if not db_users:
        text = f"{e(EMOJI['users'])} <b>Пользователей нет</b>"
    else:
        text = f"{e(EMOJI['users'])} <b>ПОЛЬЗОВАТЕЛИ:</b>\n\n"
        for uid, data in list(db_users.items())[:20]:
            name = data.get("first_name") or data.get("username") or uid
            text += f"• <code>{uid}</code> — {name} — {data['balance']:.2f} USDT\n"
        if len(db_users) > 20:
            text += f"\n... ещё {len(db_users) - 20}"
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=back_to_admin_keyboard())
    await callback.answer()

@router.callback_query(F.data == "admin_edit_balance")
async def admin_edit_balance_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        f"{e(EMOJI['edit'])} <b>ИЗМЕНЕНИЕ БАЛАНСА</b>\n\n"
        f"{e(EMOJI['info'])} Отправьте в формате:\n<code>ID СУММА</code>\n\n"
        f"<i>Пример: 123456789 100</i>",
        parse_mode=ParseMode.HTML, reply_markup=cancel_keyboard()
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
        
        db_users = await db.get_all_users()
        uid = str(user_id)
        if uid not in db_users:
            await message.answer(
                f"{e(EMOJI['cross'])} Пользователь с ID <code>{user_id}</code> не найден",
                parse_mode=ParseMode.HTML, reply_markup=cancel_keyboard()
            )
            return
        
        old_balance, new_balance = await db.set_balance(user_id, new_balance)
        name = db_users[uid].get("first_name") or db_users[uid].get("username") or user_id
        
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
            parse_mode=ParseMode.HTML, reply_markup=admin_panel_keyboard()
        )
        await state.clear()
    except ValueError:
        await message.answer(
            f"{e(EMOJI['cross'])} Неверный формат. Используйте: <code>ID СУММА</code>",
            parse_mode=ParseMode.HTML, reply_markup=cancel_keyboard()
        )

@router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        f"{e(EMOJI['broadcast'])} <b>РАССЫЛКА</b>\n\n"
        f"{e(EMOJI['info'])} Отправьте сообщение для рассылки:",
        parse_mode=ParseMode.HTML, reply_markup=cancel_keyboard()
    )
    await state.set_state(AdminStates.waiting_for_broadcast)
    await callback.answer()

@router.message(AdminStates.waiting_for_broadcast)
async def admin_broadcast_send(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await state.clear()
        return
    
    db_users = await db.get_all_users()
    success = 0
    failed = 0
    await message.answer(f"{e(EMOJI['loading'])} <b>Рассылка...</b>", parse_mode=ParseMode.HTML)
    
    for user_id in db_users.keys():
        try:
            await bot.send_message(int(user_id), message.html_text or message.text, parse_mode=ParseMode.HTML)
            success += 1
            await asyncio.sleep(0.05)
        except:
            failed += 1
    
    await message.answer(
        f"{e(EMOJI['check'])} <b>Готово!</b>\n\n✅ {success}\n❌ {failed}",
        parse_mode=ParseMode.HTML, reply_markup=admin_panel_keyboard()
    )
    await state.clear()

# ========== АДМИН МЕДИА ==========
@router.callback_query(F.data == "admin_media")
async def admin_media_menu(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    
    sections = {
        "profile": "Профиль", "games": "Игры", "deposit": "Пополнение",
        "withdraw": "Вывод", "support": "Поддержка", "help": "Помощь"
    }
    
    text = f"{e(EMOJI['media'])} <b>УПРАВЛЕНИЕ МЕДИА</b>\n\n"
    for key, name in sections.items():
        media = await db.get_media(key)
        if media and media["file_id"]:
            status = f"{e(EMOJI['check'])} {media['type']}"
        else:
            status = f"{e(EMOJI['cross'])} Нет"
        text += f"• <b>{name}</b>: {status}\n"
    
    text += f"\nВыберите раздел для изменения:"
    await callback.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=admin_media_keyboard())
    await callback.answer()

@router.callback_query(F.data.startswith("admin_media_"))
async def admin_media_set(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    
    action = callback.data.replace("admin_media_", "")
    
    if action == "clear":
        await db.clear_all_media()
        await callback.message.edit_text(
            f"{e(EMOJI['check'])} <b>Все медиа удалены!</b>",
            parse_mode=ParseMode.HTML, reply_markup=admin_panel_keyboard()
        )
        await callback.answer("Все медиа удалены!")
        return
    
    sections_names = {
        "profile": "Профиль", "games": "Игры", "deposit": "Пополнение",
        "withdraw": "Вывод", "support": "Поддержка", "help": "Помощь"
    }
    section_name = sections_names.get(action, action)
    
    state_map = {
        "profile": AdminStates.waiting_for_media_profile,
        "games": AdminStates.waiting_for_media_games,
        "deposit": AdminStates.waiting_for_media_deposit,
        "withdraw": AdminStates.waiting_for_media_withdraw,
        "support": AdminStates.waiting_for_media_support,
        "help": AdminStates.waiting_for_media_help,
    }
    
    if action in state_map:
        await state.set_state(state_map[action])
    
    await state.update_data(media_section=action)
    
    await callback.message.edit_text(
        f"{e(EMOJI['media'])} <b>МЕДИА ДЛЯ: {section_name}</b>\n\n"
        f"Отправьте фото или видео (без сжатия).\n"
        f"Для удаления отправьте любое текстовое сообщение.",
        parse_mode=ParseMode.HTML, reply_markup=cancel_keyboard()
    )
    await callback.answer()

@router.message(AdminStates.waiting_for_media_profile)
@router.message(AdminStates.waiting_for_media_games)
@router.message(AdminStates.waiting_for_media_deposit)
@router.message(AdminStates.waiting_for_media_withdraw)
@router.message(AdminStates.waiting_for_media_support)
@router.message(AdminStates.waiting_for_media_help)
async def admin_media_receive(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await state.clear()
        return
    
    data = await state.get_data()
    section = data.get("media_section")
    
    sections_names = {
        "profile": "Профиль", "games": "Игры", "deposit": "Пополнение",
        "withdraw": "Вывод", "support": "Поддержка", "help": "Помощь"
    }
    section_name = sections_names.get(section, section)
    
    if message.photo:
        file_id = message.photo[-1].file_id
        await db.set_media(section, "photo", file_id)
        await message.answer(
            f"{e(EMOJI['check'])} <b>Фото добавлено в раздел «{section_name}»!</b>",
            parse_mode=ParseMode.HTML, reply_markup=admin_media_keyboard()
        )
    elif message.video:
        file_id = message.video.file_id
        await db.set_media(section, "video", file_id)
        await message.answer(
            f"{e(EMOJI['check'])} <b>Видео добавлено в раздел «{section_name}»!</b>",
            parse_mode=ParseMode.HTML, reply_markup=admin_media_keyboard()
        )
    else:
        await db.set_media(section, None, None)
        await message.answer(
            f"{e(EMOJI['check'])} <b>Медиа удалено из раздела «{section_name}»!</b>",
            parse_mode=ParseMode.HTML, reply_markup=admin_media_keyboard()
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
@router.callback_query(F.data == "cancel_action")
async def cancel_action(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    if callback.from_user.id in ADMIN_IDS:
        await callback.message.edit_text(
            f"{e(EMOJI['cross'])} <b>Отменено</b>",
            parse_mode=ParseMode.HTML, reply_markup=admin_panel_keyboard()
        )
    else:
        await callback.message.delete()
        await callback.message.answer(
            f"{e(EMOJI['home'])} <b>Vest Casino</b>",
            parse_mode=ParseMode.HTML, reply_markup=main_menu_keyboard()
        )
    await callback.answer()

# ========== ЗАПУСК ==========
async def main():
    await db.connect()
    logger.info("Vest Casino bot started!")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
