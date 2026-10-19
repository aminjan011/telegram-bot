import os
import logging
import html
import asyncio
import urllib.parse
from datetime import datetime, timedelta
import asyncpg
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, CommandObject, Command
from aiogram.types import (
    InlineKeyboardButton, 
    InlineKeyboardMarkup, 
    CallbackQuery, 
    ChatJoinRequest, 
    BufferedInputFile
)
from aiogram.enums import ParseMode
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiogram.exceptions import TelegramForbiddenError, TelegramAPIError, TelegramBadRequest
from aiohttp import web

logging.basicConfig(level=logging.INFO)

# ==================== СОЗЛАМА (SETTINGS) ====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "8932013152:AAEPVbmHe0zuH_zGyhFSxi4eomRCIez6QSA")
PRIVATE_CHANNEL_ID = -1004324882879
CHANNELS_SECTION_LINK = "https://t.me/+j2RWAdcz2FwwMGVi"
REQUIRED_REFERRALS = 10
SUB_DAYS = 10
WELCOME_BONUS = 2        
TRIAL_SECONDS = 60       
ADMIN_USERNAME = "zyroxcv"
ADMIN_ID = 1112793157

WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
DATABASE_URL = os.getenv("DATABASE_URL")
PORT = int(os.getenv("PORT", 8080))
# ============================================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

class UserStates(StatesGroup):
    waiting_for_promo = State()

class AdminStates(StatesGroup):
    waiting_for_broadcast = State()
    waiting_for_ch1 = State()
    waiting_for_ch2 = State()
    waiting_for_chat = State()
    waiting_for_trial_channel = State()
    waiting_for_promo = State()
    waiting_for_preview = State()
    waiting_for_daily_reward = State()

# --- БАЗА ДАННЫХ ---
db_pool: asyncpg.Pool = None

async def init_db_pool():
    global db_pool
    if DATABASE_URL:
        try:
            db_pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=1, max_size=10)
            async with db_pool.acquire() as conn:
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        user_id BIGINT PRIMARY KEY,
                        referrer_id BIGINT,
                        points REAL DEFAULT 0,
                        has_access INT DEFAULT 0,
                        expire_date TEXT,
                        last_bonus TEXT,
                        warned_expire INT DEFAULT 0,
                        joined_at TEXT,
                        welcome_claimed INT DEFAULT 0,
                        trial_used INT DEFAULT 0
                    )
                ''')
                await conn.execute("ALTER TABLE users ALTER COLUMN points TYPE REAL USING points::REAL")
                await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_bonus TEXT")
                await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS warned_expire INT DEFAULT 0")
                await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS joined_at TEXT")
                await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS welcome_claimed INT DEFAULT 0")
                await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS trial_used INT DEFAULT 0")

                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                ''')
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS promocodes (
                        code TEXT PRIMARY KEY,
                        reward REAL
                    )
                ''')
                await conn.execute("INSERT INTO settings (key, value) VALUES ('channel_1', '@kinozhuldyzkz') ON CONFLICT (key) DO NOTHING")
                await conn.execute("INSERT INTO settings (key, value) VALUES ('channel_2', '') ON CONFLICT (key) DO NOTHING")
                await conn.execute("INSERT INTO settings (key, value) VALUES ('chat_link', '') ON CONFLICT (key) DO NOTHING")
                await conn.execute(f"INSERT INTO settings (key, value) VALUES ('trial_channel_id', '{PRIVATE_CHANNEL_ID}') ON CONFLICT (key) DO NOTHING")
                await conn.execute("INSERT INTO settings (key, value) VALUES ('preview_file_id', '') ON CONFLICT (key) DO NOTHING")
                await conn.execute("INSERT INTO settings (key, value) VALUES ('preview_type', '') ON CONFLICT (key) DO NOTHING")
                await conn.execute("INSERT INTO settings (key, value) VALUES ('preview_caption', '') ON CONFLICT (key) DO NOTHING")
                await conn.execute("INSERT INTO settings (key, value) VALUES ('daily_reward', '1') ON CONFLICT (key) DO NOTHING")
            logging.info("База данных успешно инициализирована!")
        except Exception as e:
            logging.error(f"Ошибка БД: {e}")

async def get_setting(key: str) -> str:
    if not db_pool: return ""
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT value FROM settings WHERE key = $1", key)
        return row['value'] if row else ""

async def set_setting(key: str, value: str):
    if not db_pool: return
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO settings (key, value) VALUES ($1, $2) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            key, value
        )

async def get_trial_channel_id() -> int:
    val = await get_setting('trial_channel_id')
    if val and val.strip():
        try:
            return int(val.strip())
        except ValueError:
            pass
    return PRIVATE_CHANNEL_ID

async def get_user(user_id: int):
    if not db_pool: return None
    async with db_pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)

async def add_user(user_id: int, referrer_id: int = None):
    if not db_pool: return
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (user_id, referrer_id, joined_at) VALUES ($1, $2, $3) ON CONFLICT (user_id) DO NOTHING",
            user_id, referrer_id, now_str
        )

async def get_stats():
    if not db_pool: return 0, 0
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT COUNT(*), COALESCE(SUM(points), 0) FROM users")
        return row[0], row[1]

async def get_all_users():
    if not db_pool: return []
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT user_id FROM users")
        return [r['user_id'] for r in rows]

async def check_subscription(user_id: int) -> bool:
    ch1 = await get_setting('channel_1')
    ch2 = await get_setting('channel_2')
    for ch in [c for c in [ch1, ch2] if c and c.strip()]:
        try:
            m = await bot.get_chat_member(chat_id=ch, user_id=user_id)
            if m.status not in ["creator", "administrator", "member"]: return False
        except Exception:
            return False
    return True

def is_2x_active(joined_at_str: str):
    if not joined_at_str: return False, 0
    try:
        diff = (datetime.strptime(joined_at_str, "%Y-%m-%d %H:%M:%S") + timedelta(hours=1)) - datetime.now()
        if diff.total_seconds() > 0: return True, int(diff.total_seconds() // 60)
    except Exception:
        pass
    return False, 0

async def kick_after_trial(user_id: int, delay: int = TRIAL_SECONDS):
    await asyncio.sleep(delay)
    try:
        user = await get_user(user_id)
        if user and user['points'] >= REQUIRED_REFERRALS: return
        trial_ch = await get_trial_channel_id()
        m = await bot.get_chat_member(chat_id=trial_ch, user_id=user_id)
        if m.status in ["member", "restricted"]:
            await bot.ban_chat_member(chat_id=trial_ch, user_id=user_id)
            await bot.unban_chat_member(chat_id=trial_ch, user_id=user_id)
        if db_pool:
            async with db_pool.acquire() as conn:
                await conn.execute("UPDATE users SET expire_date = NULL WHERE user_id = $1", user_id)
        
        bot_info = await bot.get_me()
        ref_link = f"https://t.me/{bot_info.username}?start={user_id}"
        share_url = f"https://t.me/share/url?url={urllib.parse.quote(ref_link)}&text={urllib.parse.quote('🔥 Доступ к 1,500+ видео в VIP-архиве!')}"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📤 Поделиться с друзьями", url=share_url)],
            [InlineKeyboardButton(text="⚡ Забрать VIP навсегда", callback_data="free_channel")]
        ])
        await bot.send_message(user_id, "⏳ <b>Время тест-драйва исчерпано!</b>\n\nЭмоции только начинаются... Хотите получить полный доступ ко всем <b>1,500+ видео</b> без цензуры?", parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception:
        pass

# --- ПРЕМИУМ КЛАВИАТУРЫ ---
async def get_main_keyboard(user_id: int):
    user = await get_user(user_id)
    points = user['points'] if user else 0
    trial_used = user['trial_used'] if user else 0
    vip_text = "🟢 Забрать VIP Доступ" if points >= REQUIRED_REFERRALS else "⚡ Получить VIP Доступ"

    kb = [
        [InlineKeyboardButton(text="🪪 Личный Кабинет", callback_data="profile"), InlineKeyboardButton(text="🎁 Ежедневный Бонус", callback_data="daily_bonus")],
        [InlineKeyboardButton(text="🔥 PREMIUM ARCHIVE (1,500+ ВИДЕО)", callback_data="channels")],
        [InlineKeyboardButton(text=vip_text, callback_data="free_channel")],
    ]
    if trial_used == 0:
        kb.append([InlineKeyboardButton(text="⏱ Тест-драйв (1 мин)", callback_data="trial_access"), InlineKeyboardButton(text="🔞 Превью 18+", callback_data="content_preview")])
    else:
        kb.append([InlineKeyboardButton(text="🔞 Превью контента 18+", callback_data="content_preview")])

    kb.extend([
        [InlineKeyboardButton(text="🔑 Промокод", callback_data="enter_promocode"), InlineKeyboardButton(text="📖 Справка", callback_data="help")],
        [InlineKeyboardButton(text="💬 Поддержка 24/7", url=f"https://t.me/{ADMIN_USERNAME}")]
    ])
    chat_url = await get_setting('chat_link')
    if chat_url and chat_url.strip():
        kb.append([InlineKeyboardButton(text="👥 Закрытый Чат Клуба", url=chat_url)])
    return InlineKeyboardMarkup(inline_keyboard=kb)

async def get_sub_keyboard():
    ch1 = await get_setting('channel_1')
    ch2 = await get_setting('channel_2')
    buttons = []
    if ch1 and ch1.strip(): buttons.append([InlineKeyboardButton(text=f"📢 Подписаться ➔ {ch1}", url=f"https://t.me/{ch1.replace('@', '')}")])
    if ch2 and ch2.strip(): buttons.append([InlineKeyboardButton(text=f"📢 Подписаться ➔ {ch2}", url=f"https://t.me/{ch2.replace('@', '')}")])
    buttons.append([InlineKeyboardButton(text=f"✨ Проверить и забрать +{WELCOME_BONUS} балла", callback_data="check_sub")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

async def get_admin_keyboard():
    ch1 = await get_setting('channel_1') or "Не задан"
    ch2 = await get_setting('channel_2') or "Не задан"
    chat = await get_setting('chat_link') or "Не задан"
    trial_ch = await get_setting('trial_channel_id') or str(PRIVATE_CHANNEL_ID)
    prev = "✅ Есть" if await get_setting('preview_file_id') else "❌ Нет"
    daily_val = await get_setting('daily_reward') or "1"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"), InlineKeyboardButton(text="📥 Бэкап (CSV)", callback_data="admin_backup")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text=f"🎁 Бонус за день: +{daily_val}", callback_data="admin_set_daily"), InlineKeyboardButton(text="➕ Промокод", callback_data="admin_create_promo")],
        [InlineKeyboardButton(text=f"🎬 Превью: {prev}", callback_data="admin_set_preview")],
        [InlineKeyboardButton(text=f"⚙️ Канал 1: {ch1}", callback_data="admin_set_ch1")],
        [InlineKeyboardButton(text=f"⚙️ Канал 2: {ch2}", callback_data="admin_set_ch2")],
        [InlineKeyboardButton(text=f"⏱ Test Kanal: {trial_ch}", callback_data="admin_set_trial_ch")],
        [InlineKeyboardButton(text=f"💬 Чат: {chat}", callback_data="admin_set_chat")],
        [InlineKeyboardButton(text="❌ Закрыть", callback_data="admin_close")]
    ])

async def process_welcome_and_referral(user_id: int):
    user = await get_user(user_id)
    if not user or not db_pool: return False
    got_wel = False
    async with db_pool.acquire() as conn:
        if user['welcome_claimed'] == 0:
            await conn.execute("UPDATE users SET points = points + $1, welcome_claimed = 1 WHERE user_id = $2", float(WELCOME_BONUS), user_id)
            got_wel = True
        ref_id = user['referrer_id']
        if ref_id:
            ref_user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", ref_id)
            reward, boost = 1.0, ""
            if ref_user and ref_user['joined_at']:
                if is_2x_active(ref_user['joined_at'])[0]: reward, boost = 2.0, " 🔥 (2X Турбо-бонус!)"
            await conn.execute("UPDATE users SET points = points + $1 WHERE user_id = $2", reward, ref_id)
            await conn.execute("UPDATE users SET referrer_id = NULL WHERE user_id = $1", user_id)
            try:
                await bot.send_message(ref_id, f"🎉 <b>Успешное приглашение!</b>\nВам начислено: <b>+{reward} балл</b>{boost} ⭐", parse_mode=ParseMode.HTML)
            except Exception:
                pass
    return got_wel

def build_welcome_text(first_name: str, user_row, got_welcome: bool = False) -> str:
    boost_line = ""
    if user_row and user_row['joined_at']:
        act, mins = is_2x_active(user_row['joined_at'])
        if act: boost_line = f"\n🔥 <b>ТУРБО-РЕЖИМ 2X АКТИВЕН ({mins} мин):</b> х2 балла за каждого друга!\n"
    gift = f"\n🎁 <b>Вам начислен стартовый бонус: +{WELCOME_BONUS} балла!</b>\n" if got_welcome else ""
    return (
        f"🌌 <b>Добро пожаловать в Nexus Core, {first_name}!</b>\n"
        f"┏━━━━━━━━━━━━━━━━━━━━━\n"
        f"┃ 🛡 Статус: <code>VIP Верифицирован</code>\n"
        f"┃ 💎 Доступ: <code>Эксклюзив 18+</code>\n"
        f"┗━━━━━━━━━━━━━━━━━━━━━"
        f"{gift}{boost_line}\n"
        f"🔥 <b>Что вас ждет внутри системы:</b>\n"
        f"• 📂 <b>Премиум Архив</b> — более <b>1,500+ горячих видео</b> без цензуры!\n"
        f"• ⏱ <b>Тест-драйв</b> — 1 минута полного погружения в VIP.\n"
        f"• 🎁 <b>Награды</b> — ежедневные бонусы и секретные промокоды.\n\n"
        f"👇 <i>Выберите нужный раздел в меню ниже:</i>"
    )

# --- ХЕНДЛЕРЫ ПОЛЬЗОВАТЕЛЯ ---
@dp.message(CommandStart())
async def start_handler(message: types.Message, command: CommandObject):
    user_id = message.from_user.id
    ref_id = int(command.args) if command.args and command.args.isdigit() and int(command.args) != user_id else None
    if not await get_user(user_id): await add_user(user_id, ref_id)
    if not await check_subscription(user_id):
        sub_text = (
            "🔐 <b>Система Безопасности & Доступ</b>\n"
            "┏━━━━━━━━━━━━━━━━━━━━━\n"
            "┃ ⚠️ Для входа в закрытую экосистему\n"
            "┃ подпишитесь на наши каналы-партнеры.\n"
            f"┃ 🎁 Бонус за подписку: <b>+{WELCOME_BONUS} балла сразу!</b>\n"
            "┗━━━━━━━━━━━━━━━━━━━━━\n\n"
            "👇 <i>Подпишитесь и нажмите кнопку проверки:</i>"
        )
        try:
            await message.answer(sub_text, reply_markup=await get_sub_keyboard(), parse_mode=ParseMode.HTML)
        except TelegramForbiddenError:
            pass
        return

    got_wel = await process_welcome_and_referral(user_id)
    user = await get_user(user_id)
    await message.answer(build_welcome_text(html.escape(message.from_user.first_name), user, got_wel), reply_markup=await get_main_keyboard(user_id), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    if await check_subscription(user_id):
        got_wel = await process_welcome_and_referral(user_id)
        await callback.answer("✅ Верификация успешно пройдена!", show_alert=True)
        try: await callback.message.delete()
        except Exception: pass
        user = await get_user(user_id)
        await callback.message.answer(build_welcome_text(html.escape(callback.from_user.first_name), user, got_wel), reply_markup=await get_main_keyboard(user_id), parse_mode=ParseMode.HTML)
    else:
        await callback.answer("⚠️ Вы подписались еще не на все каналы!", show_alert=True)

@dp.callback_query(F.data == "trial_access")
async def trial_access_handler(callback: CallbackQuery):
    user_id = callback.from_user.id
    if not await check_subscription(user_id):
        await callback.answer("⚠️ Сначала подпишитесь на каналы!", show_alert=True)
        return
    user = await get_user(user_id)
    if user and user['trial_used'] == 1:
        await callback.answer("❌ Вы уже использовали свой тест-драйв!", show_alert=True)
        return
    await callback.answer()
    try:
        trial_ch = await get_trial_channel_id()
        link = await bot.create_chat_invite_link(chat_id=trial_ch, creates_join_request=True, expire_date=datetime.now() + timedelta(minutes=10))
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Войти на 1 минуту (VIP)", url=link.invite_link)],
            [InlineKeyboardButton(text="◀️ На главную", callback_data="back_main")]
        ])
        text = (
            "⏱ <b>Тест-драйв закрытого VIP-канала (60 секунд)</b>\n"
            "┏━━━━━━━━━━━━━━━━━━━━━\n"
            "┃ 🔓 Статус: <code>Пробный доступ 18+</code>\n"
            "┃ ⏳ Таймер: <code>60 секунд пламени</code>\n"
            "┗━━━━━━━━━━━━━━━━━━━━━\n\n"
            "⚠️ Нажмите кнопку ниже. Заявка будет одобрена <b>мгновенно</b>, и начнется отсчет времени вау-эффекта!"
        )
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception:
        await callback.message.edit_text("⚠️ Ошибка генерации ссылки.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ На главную", callback_data="back_main")]]))

@dp.callback_query(F.data == "profile")
async def profile_handler(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    user = await get_user(user_id)
    points = user['points'] if user else 0
    pts_str = f"{int(points)}" if points.is_integer() else f"{points}"
    trial = "✅ Доступен" if (user and user['trial_used'] == 0) else "❌ Использован"
    is_vip = False
    try:
        trial_ch = await get_trial_channel_id()
        m = await bot.get_chat_member(chat_id=trial_ch, user_id=user_id)
        if m.status in ["creator", "administrator", "member"]: is_vip = True
    except Exception: pass

    text = (
        f"🪪 <b>Персональный кабинет</b>\n"
        f"┏━━━━━━━━━━━━━━━━━━━━━\n"
        f"┃ 🆔 ID: <code>{user_id}</code>\n"
        f"┃ ⭐ Баллы: <b>{pts_str}/{REQUIRED_REFERRALS}</b>\n"
        f"┃ ⏱ Тест-драйв: <b>{trial}</b>\n"
        f"┃ 🔐 VIP статус: <b>{'🟢 Активен (1,500+ видео)' if is_vip else '🔴 Неактивен'}</b>\n"
        f"┗━━━━━━━━━━━━━━━━━━━━━"
    )
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ На главную", callback_data="back_main")]]), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "content_preview")
async def content_preview_handler(callback: CallbackQuery):
    if not await check_subscription(callback.from_user.id):
        await callback.answer("⚠️ Требуется подписка!", show_alert=True)
        return
    file_id, p_type = await get_setting("preview_file_id"), await get_setting("preview_type")
    caption = await get_setting("preview_caption") or "🔥 <b>ЭКСКЛЮЗИВНОЕ ПРЕВЬЮ 18+</b>\nСамые горячие кадры из закрытой базы на 1,500+ видео!"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ Получить полный доступ к 1,500+ видео", callback_data="free_channel")],
        [InlineKeyboardButton(text="🗑 Скрыть", callback_data="delete_msg")]
    ])
    await callback.answer()
    if file_id and p_type:
        try:
            if p_type == "photo": await bot.send_photo(callback.from_user.id, photo=file_id, caption=caption, parse_mode=ParseMode.HTML, protect_content=True, reply_markup=kb)
            elif p_type == "video": await bot.send_video(callback.from_user.id, video=file_id, caption=caption, parse_mode=ParseMode.HTML, protect_content=True, reply_markup=kb)
            return
        except Exception: pass
    await callback.message.answer("🔥 <b>Превью VIP-архива (18+)</b>\nТысячи откровенных и элитных материалов без цензуры ждут вас прямо сейчас!", parse_mode=ParseMode.HTML, protect_content=True, reply_markup=kb)

@dp.callback_query(F.data == "delete_msg")
async def delete_msg(c: CallbackQuery):
    await c.answer()
    try: await c.message.delete()
    except Exception: pass

@dp.callback_query(F.data == "daily_bonus")
async def daily_bonus(c: CallbackQuery):
    uid = c.from_user.id
    user = await get_user(uid)
    today = datetime.now().strftime("%Y-%m-%d")
    if user and user['last_bonus'] == today:
        await c.answer("⏳ Вы уже забрали бонус сегодня! Ждем вас завтра.", show_alert=True)
        return
    
    reward_val = float(await get_setting("daily_reward") or "1")
    if db_pool:
        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE users SET points = points + $1, last_bonus = $2 WHERE user_id = $3", reward_val, today, uid)
    
    rew_str = f"{int(reward_val)}" if reward_val.is_integer() else f"{reward_val}"
    await c.answer(f"🎉 Ежедневный бонус зачислен: +{rew_str} балл ⭐", show_alert=True)
    user = await get_user(uid)
    await c.message.edit_text(build_welcome_text(html.escape(c.from_user.first_name), user, False), reply_markup=await get_main_keyboard(uid), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "enter_promocode")
async def enter_promo(c: CallbackQuery, state: FSMContext):
    await c.answer()
    await state.set_state(UserStates.waiting_for_promo)
    await c.message.edit_text("🔑 <b>Активация промокода</b>\n\nОтправьте секретный код в чат:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ На главную", callback_data="back_main")]]), parse_mode=ParseMode.HTML)

@dp.message(UserStates.waiting_for_promo)
async def process_promo(message: types.Message, state: FSMContext):
    await state.clear()
    code, uid = message.text.strip(), message.from_user.id
    if not db_pool: return
    async with db_pool.acquire() as conn:
        promo = await conn.fetchrow("SELECT reward FROM promocodes WHERE code = $1", code)
        if promo:
            rew = promo['reward']
            await conn.execute("UPDATE users SET points = points + $1 WHERE user_id = $2", rew, uid)
            await conn.execute("DELETE FROM promocodes WHERE code = $1", code)
            rew_str = f"{int(rew)}" if rew.is_integer() else f"{rew}"
            await message.answer(f"🎉 <b>Промокод успешно активирован!</b>\nНачислено: <b>+{rew_str} баллов</b> ⭐", reply_markup=await get_main_keyboard(uid), parse_mode=ParseMode.HTML)
        else:
            await message.answer("❌ <b>Ошибка:</b> Неверный или уже использованный промокод.", reply_markup=await get_main_keyboard(uid), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "channels")
async def channels(c: CallbackQuery):
    if not await check_subscription(c.from_user.id):
        await c.answer("⚠️ Требуется подписка на каналы!", show_alert=True)
        return
    await c.answer()
    
    archive_text = (
        "🔥 <b>ЭЛИТНЫЙ VIP-АРХИВ: 1,500+ ВИДЕО БЕЗ ЦЕНЗУРЫ!</b> 🔥\n\n"
        "Вы устали от однообразного контента и пустых обещаний? Добро пожаловать в эпицентр страсти и откровенности! "
        "Мы собрали для вас самую горячую, элитную и закрытую коллекцию видео в Telegram.\n\n"
        "✨ <b>Почему этот архив выбирают тысячи ценителей:</b>\n"
        "• 🎬 <b>Более 1,500+ эксклюзивных видео</b> в безупречном HD качестве — обновления прилетают КАЖДЫЙ ДЕНЬ!\n"
        "• 🔞 Никакой цензуры, рекламы и скучных пауз. Только чистые эмоции и эротическое наслаждение.\n"
        "• 🔒 Полностью закрытая база, защищенная от слива — контент доступен только истинным участникам клуба.\n\n"
        "💥 <i>Не отказывайте себе в удовольствии. Нажмите кнопку ниже и окунитесь в мир абсолютного кайфа прямо сейчас! 👇</i>"
    )
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Войти в премиум архив (1,500+ видео)", url=CHANNELS_SECTION_LINK)],
        [InlineKeyboardButton(text="◀️ На главную", callback_data="back_main")]
    ])
    await c.message.edit_text(archive_text, reply_markup=kb, parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "free_channel")
async def free_channel(c: CallbackQuery):
    uid = c.from_user.id
    if not await check_subscription(uid):
        await c.answer("⚠️ Нужна подписка на каналы!", show_alert=True)
        return
    await c.answer()
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={uid}"
    share_text = "🔥 1,500+ горячих видео без цензуры в закрытом VIP-архиве! Заходи и забирай бонус 🎁"
    share_url = f"https://t.me/share/url?url={urllib.parse.quote(ref_link)}&text={urllib.parse.quote(share_text)}"
    
    user = await get_user(uid)
    points = user['points'] if user else 0
    expire_str = user['expire_date'] if user else None

    is_vip = False
    try:
        trial_ch = await get_trial_channel_id()
        m = await bot.get_chat_member(chat_id=trial_ch, user_id=uid)
        if m.status in ["creator", "administrator", "member"]: is_vip = True
    except Exception: pass

    bar = "▰" * min(int(points), REQUIRED_REFERRALS) + "▱" * (REQUIRED_REFERRALS - min(int(points), REQUIRED_REFERRALS))
    pts_str = f"{int(points)}" if points.is_integer() else f"{points}"
    
    text = (
        f"⚡ <b>Бесплатный доступ к 1,500+ видео</b>\n"
        f"┏━━━━━━━━━━━━━━━━━━━━━\n"
        f"┃ Приглашайте друзей и заполняйте шкалу\n"
        f"┃ прогресса для получения VIP-статуса.\n"
        f"┗━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📊 Шкала: [{bar}] <b>{pts_str}/{REQUIRED_REFERRALS}</b>\n\n"
        f"🔗 <b>Ваша личная ссылка:</b>\n"
        f"<code>{ref_link}</code>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Поделиться с друзьями (1 клик)", url=share_url)],
        [InlineKeyboardButton(text="◀️ На главную", callback_data="back_main")]
    ])

    if is_vip and expire_str:
        await c.message.edit_text(text + f"\n\n🟢 <b>VIP доступ активен до:</b> {expire_str}", reply_markup=kb, parse_mode=ParseMode.HTML)
        return

    if points >= REQUIRED_REFERRALS:
        exp_str = (datetime.now() + timedelta(days=SUB_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
        if db_pool:
            async with db_pool.acquire() as conn:
                await conn.execute("UPDATE users SET expire_date = $1, warned_expire = 0 WHERE user_id = $2", exp_str, uid)
        trial_ch = await get_trial_channel_id()
        link = await bot.create_chat_invite_link(chat_id=trial_ch, creates_join_request=True, expire_date=datetime.now() + timedelta(minutes=15))
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔓 Подать заявку в VIP", url=link.invite_link)],
            [InlineKeyboardButton(text="📤 Поделиться", url=share_url)],
            [InlineKeyboardButton(text="◀️ На главную", callback_data="back_main")]
        ])
        text += f"\n\n🎉 <b>Цель достигнута!</b> Доступ ко всем 1,500+ видео открыт на {SUB_DAYS} дней."
    else:
        needed = REQUIRED_REFERRALS - points
        needed_str = f"{int(needed)}" if needed.is_integer() else f"{needed}"
        text += f"\n\n💡 <b>Осталось пригласить:</b> {needed_str} друзей"

    await c.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)

@dp.chat_join_request()
async def join_req(req: ChatJoinRequest):
    trial_ch = await get_trial_channel_id()
    if req.chat.id != trial_ch:
        return
        
    uid = req.from_user.id
    user = await get_user(uid)
    if not user:
        try: await req.decline()
        except Exception: pass
        return

    if user['points'] >= REQUIRED_REFERRALS:
        try:
            await req.approve()
            exp_str = (datetime.now() + timedelta(days=SUB_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
            if db_pool:
                async with db_pool.acquire() as conn:
                    await conn.execute("UPDATE users SET expire_date = $1, warned_expire = 0 WHERE user_id = $2", exp_str, uid)
            await bot.send_message(uid, f"✅ <b>Заявка одобрена!</b> Добро пожаловать к 1,500+ видео. VIP активен до {exp_str}", parse_mode=ParseMode.HTML)
        except Exception: pass
        return

    if user['trial_used'] == 0:
        try:
            await req.approve()
            exp_str = (datetime.now() + timedelta(seconds=TRIAL_SECONDS)).strftime("%Y-%m-%d %H:%M:%S")
            if db_pool:
                async with db_pool.acquire() as conn:
                    await conn.execute("UPDATE users SET trial_used = 1, expire_date = $1, warned_expire = 1 WHERE user_id = $2", exp_str, uid)
            asyncio.create_task(kick_after_trial(uid, TRIAL_SECONDS))
            await bot.send_message(uid, "⏱ <b>Тест-драйв запущен!</b> У вас есть 60 секунд насладиться 1,500+ видео!", parse_mode=ParseMode.HTML)
        except Exception: pass
        return

    try:
        await req.decline()
        await bot.send_message(uid, "❌ <b>Доступ ограничен.</b> Вы уже использовали тест-драйв. Наберите 10 баллов для полного доступа к 1,500+ видео.", parse_mode=ParseMode.HTML)
    except Exception: pass

@dp.callback_query(F.data == "help")
async def help_h(c: CallbackQuery):
    await c.answer()
    help_text = (
        "📖 <b>Справочный центр Nexus</b>\n"
        "┏━━━━━━━━━━━━━━━━━━━━━\n"
        "┃ • 📂 <b>Премиум Архив</b> — база из 1,500+ видео.\n"
        "┃ • ⏱ <b>Тест-драйв</b> — 1 минута бесплатного VIP.\n"
        "┃ • 🎁 <b>Старт</b> — бонусные баллы при входе.\n"
        "┃ • 🔥 <b>Турбо 2X</b> — х2 баллы в первый час.\n"
        "┗━━━━━━━━━━━━━━━━━━━━━"
    )
    await c.message.edit_text(help_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ На главную", callback_data="back_main")]]), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "back_main")
async def back_main(c: CallbackQuery, state: FSMContext):
    await c.answer()
    await state.clear()
    uid = c.from_user.id
    user = await get_user(uid)
    await c.message.edit_text(build_welcome_text(html.escape(c.from_user.first_name), user, False), reply_markup=await get_main_keyboard(uid), parse_mode=ParseMode.HTML)

# --- АДМИН ПАНЕЛЬ ---
@dp.message(Command("admin"))
async def admin_start(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    await m.answer("👑 Панель администратора", reply_markup=await get_admin_keyboard(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(c: CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    await c.answer("⏳ Сбор статистики...")
    tot, pts = await get_stats()
    pts_str = f"{int(pts)}" if pts.is_integer() else f"{pts}"
    text = f"📊 <b>Статистика:</b>\n👥 Юзеров: <b>{tot}</b>\n⭐ Суммарно баллов: <b>{pts_str}</b>"
    try: await c.message.edit_text(text, reply_markup=await get_admin_keyboard(), parse_mode=ParseMode.HTML)
    except Exception: pass

@dp.callback_query(F.data == "admin_backup")
async def admin_backup(c: CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    await c.answer("📥 Создание бэкапа...")
    if not db_pool: return
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT user_id, referrer_id, points, trial_used, expire_date, joined_at FROM users")
    lines = ["user_id,referrer_id,points,trial_used,expire_date,joined_at"]
    for r in rows:
        lines.append(f"{r['user_id']},{r['referrer_id'] or ''},{r['points']},{r['trial_used']},{r['expire_date'] or ''},{r['joined_at'] or ''}")
    file = BufferedInputFile("\n".join(lines).encode("utf-8"), filename="backup.csv")
    await bot.send_document(ADMIN_ID, document=file, caption="📥 Бэкап базы данных")

@dp.callback_query(F.data == "admin_set_daily")
async def set_daily_start(c: CallbackQuery, state: FSMContext):
    if c.from_user.id != ADMIN_ID: return
    await c.answer()
    await state.set_state(AdminStates.waiting_for_daily_reward)
    await c.message.edit_text("✏️ Отправьте новое количество баллов для ежедневного бонуса (например: <code>0.5</code> или <code>1</code>):", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel_settings")]]))

@dp.message(AdminStates.waiting_for_daily_reward)
async def set_daily_save(m: types.Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID: return
    await state.clear()
    val = m.text.strip().replace(",", ".")
    try:
        float_val = float(val)
        if float_val < 0: raise ValueError()
    except ValueError:
        await m.answer("❌ Ошибка! Введите корректное число (например: <code>0.5</code> или <code>1</code>).", reply_markup=await get_admin_keyboard())
        return
    
    await set_setting("daily_reward", val)
    await m.answer(f"✅ Награда за ежедневный бонус успешно изменена на: <b>+{val} балл(а)</b>", reply_markup=await get_admin_keyboard(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "admin_set_preview")
async def set_prev(c: CallbackQuery, state: FSMContext):
    if c.from_user.id != ADMIN_ID: return
    await c.answer()
    await state.set_state(AdminStates.waiting_for_preview)
    await c.message.edit_text("Отправьте фото или видео для превью:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel_settings")]]))

@dp.message(AdminStates.waiting_for_preview)
async def save_prev(m: types.Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID: return
    fid, ptype = "", ""
    if m.photo: fid, ptype = m.photo[-1].file_id, "photo"
    elif m.video: fid, ptype = m.video.file_id, "video"
    else:
        await m.answer("Отправьте фото или видео!")
        return
    await set_setting("preview_file_id", fid)
    await set_setting("preview_type", ptype)
    await set_setting("preview_caption", m.html_text if m.caption else "")
    await state.clear()
    await m.answer("✅ Превью сохранено!", reply_markup=await get_admin_keyboard())

@dp.callback_query(F.data == "admin_create_promo")
async def cr_promo(c: CallbackQuery, state: FSMContext):
    if c.from_user.id != ADMIN_ID: return
    await c.answer()
    await state.set_state(AdminStates.waiting_for_promo)
    await c.message.edit_text("Отправьте код и награду (например: <code>CODE 0.5</code>):", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel_settings")]]))

@dp.message(AdminStates.waiting_for_promo)
async def save_promo(m: types.Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID: return
    await state.clear()
    parts = m.text.strip().split()
    if len(parts) != 2:
        await m.answer("Формат: <code>CODE 0.5</code>", reply_markup=await get_admin_keyboard())
        return
    try:
        rew_val = float(parts[1].replace(",", "."))
    except ValueError:
        await m.answer("❌ Награда должна быть числом (например: <code>CODE 0.5</code>)", reply_markup=await get_admin_keyboard())
        return

    if db_pool:
        async with db_pool.acquire() as conn:
            await conn.execute("ALTER TABLE promocodes ALTER COLUMN reward TYPE REAL USING reward::REAL")
            await conn.execute("INSERT INTO promocodes (code, reward) VALUES ($1, $2) ON CONFLICT (code) DO UPDATE SET reward = EXCLUDED.reward", parts[0], rew_val)
    await m.answer(f"✅ Промокод {parts[0]} создан (+{parts[1]})", reply_markup=await get_admin_keyboard())

@dp.callback_query(F.data == "admin_set_ch1")
async def set_ch1(c: CallbackQuery, state: FSMContext):
    if c.from_user.id != ADMIN_ID: return
    await c.answer()
    await state.set_state(AdminStates.waiting_for_ch1)
    await c.message.edit_text("Отправьте @channel_1:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel_settings")]]))

@dp.message(AdminStates.waiting_for_ch1)
async def save_ch1(m: types.Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID: return
    ch = m.text.strip()
    if not ch.startswith("@"): ch = "@" + ch
    await set_setting("channel_1", ch)
    await state.clear()
    await m.answer(f"✅ Канал 1: {ch}", reply_markup=await get_admin_keyboard())

@dp.callback_query(F.data == "admin_set_trial_ch")
async def set_trial_ch(c: CallbackQuery, state: FSMContext):
    if c.from_user.id != ADMIN_ID: return
    await c.answer()
    await state.set_state(AdminStates.waiting_for_trial_channel)
    await c.message.edit_text("Отправьте ID тест-канала (masalan: <code>-100xxxxxxxxxx</code>) yoki o'chirish uchun <code>0</code> yuboring:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel_settings")]]))

@dp.message(AdminStates.waiting_for_trial_channel)
async def save_trial_ch(m: types.Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID: return
    val = m.text.strip()
    try:
        chat_id = int(val)
        if chat_id == 0:
            await set_setting("trial_channel_id", str(PRIVATE_CHANNEL_ID))
            await state.clear()
            await m.answer(f"✅ Test kanal ID standart qiymatga ({PRIVATE_CHANNEL_ID}) qaytarildi.", reply_markup=await get_admin_keyboard())
            return
    except ValueError:
        await m.answer("❌ Xatolik! ID raqam bo'lishi kerak (masalan: <code>-100123456789</code>). Qaytadan yuboring:", reply_markup=get_admin_keyboard())
        return

    await set_setting("trial_channel_id", str(chat_id))
    await state.clear()
    await m.answer(f"✅ Test kanal ID muvaffaqiyatli saqlandi: <code>{chat_id}</code>", reply_markup=await get_admin_keyboard(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "admin_broadcast")
async def broadcast(c: CallbackQuery, state: FSMContext):
    if c.from_user.id != ADMIN_ID: return
    await c.answer()
    await state.set_state(AdminStates.waiting_for_broadcast)
    await c.message.edit_text("Отправьте пост для рассылки:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel_settings")]]))

@dp.message(AdminStates.waiting_for_broadcast)
async def send_broadcast(m: types.Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID: return
    await state.clear()
    users = await get_all_users()
    await m.answer(f"⏳ Рассылка для {len(users)} юзеров...")
    succ = 0
    for uid in users:
        try:
            await m.copy_to(chat_id=uid)
            succ += 1
            await asyncio.sleep(0.04)
        except Exception: pass
    await m.answer(f"✅ Успешно отправлено: {succ}", reply_markup=await get_admin_keyboard())

@dp.callback_query(F.data == "admin_cancel_settings")
async def cancel(c: CallbackQuery, state: FSMContext):
    await c.answer()
    await state.clear()
    await c.message.edit_text("👑 Админ-панель", reply_markup=await get_admin_keyboard())

@dp.callback_query(F.data == "admin_close")
async def close(c: CallbackQuery):
    await c.answer()
    try: await c.message.delete()
    except Exception: pass

async def auto_kick_expired_users():
    while True:
        try:
            if db_pool:
                async with db_pool.acquire() as conn:
                    now = datetime.now()
                    rows = await conn.fetch("SELECT user_id, points, expire_date, warned_expire FROM users WHERE expire_date IS NOT NULL AND expire_date != ''")
                    for r in rows:
                        u_id, pts, warned = r['user_id'], r['points'], r['warned_expire']
                        exp = datetime.strptime(r['expire_date'], "%Y-%m-%d %H:%M:%S")
                        if 0 < (exp - now).total_seconds() <= 10800 and warned == 0:
                            try:
                                await bot.send_message(u_id, "⚠️ Ваша VIP-подписка на 1,500+ видео истекает через 3 часа!", parse_mode=ParseMode.HTML)
                                await conn.execute("UPDATE users SET warned_expire = 1 WHERE user_id = $1", u_id)
                            except Exception: pass
                        if now >= exp:
                            is_full = pts >= REQUIRED_REFERRALS
                            try:
                                trial_ch = await get_trial_channel_id()
                                m = await bot.get_chat_member(chat_id=trial_ch, user_id=u_id)
                                if m.status in ["member", "restricted"]:
                                    await bot.ban_chat_member(chat_id=trial_ch, user_id=u_id)
                                    await bot.unban_chat_member(chat_id=trial_ch, user_id=u_id)
                                    if is_full:
                                        await bot.send_message(u_id, "⏰ Срок доступа к архиву 1,500+ видео истек!", parse_mode=ParseMode.HTML)
                            except Exception: pass
                            if is_full:
                                await conn.execute("UPDATE users SET expire_date = NULL, points = $1, warned_expire = 0 WHERE user_id = $2", float(WELCOME_BONUS), u_id)
                            else:
                                await conn.execute("UPDATE users SET expire_date = NULL, warned_expire = 0 WHERE user_id = $1", u_id)
        except Exception: pass
        await asyncio.sleep(15)

async def on_startup(app):
    await init_db_pool()
    asyncio.create_task(auto_kick_expired_users())
    if RENDER_EXTERNAL_URL:
        await bot.set_webhook(f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}", drop_pending_updates=True, allowed_updates=["message", "callback_query", "chat_join_request"])

def main():
    app = web.Application()
    app.router.add_get("/", lambda req: web.Response(text="OK", status=200))
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    app.on_startup.append(on_startup)
    web.run_app(app, host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    main()
