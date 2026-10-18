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

BOT_TOKEN = os.getenv("BOT_TOKEN", "8932013152:AAFbV_8mNECLC8Ctk7rkcKCg3Od60CUpZ8o")
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

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

class UserStates(StatesGroup):
    waiting_for_promo = State()

class AdminStates(StatesGroup):
    waiting_for_broadcast = State()
    waiting_for_ch1 = State()
    waiting_for_ch2 = State()
    waiting_for_chat = State()
    waiting_for_promo = State()
    waiting_for_preview = State()

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
                        points INT DEFAULT 0,
                        has_access INT DEFAULT 0,
                        expire_date TEXT,
                        last_bonus TEXT,
                        warned_expire INT DEFAULT 0,
                        joined_at TEXT,
                        welcome_claimed INT DEFAULT 0,
                        trial_used INT DEFAULT 0
                    )
                ''')
                await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_bonus TEXT")
                await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS warned_expire INT DEFAULT 0")
                await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS joined_at TEXT")
                await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS welcome_claimed INT DEFAULT 0")
                await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS trial_used INT DEFAULT 0")

                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)
                ''')
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS promocodes (code TEXT PRIMARY KEY, reward INT)
                ''')
                await conn.execute("INSERT INTO settings (key, value) VALUES ('channel_1', '@kinozhuldyzkz') ON CONFLICT (key) DO NOTHING")
                await conn.execute("INSERT INTO settings (key, value) VALUES ('channel_2', '') ON CONFLICT (key) DO NOTHING")
                await conn.execute("INSERT INTO settings (key, value) VALUES ('chat_link', '') ON CONFLICT (key) DO NOTHING")
                await conn.execute("INSERT INTO settings (key, value) VALUES ('preview_file_id', '') ON CONFLICT (key) DO NOTHING")
                await conn.execute("INSERT INTO settings (key, value) VALUES ('preview_type', '') ON CONFLICT (key) DO NOTHING")
                await conn.execute("INSERT INTO settings (key, value) VALUES ('preview_caption', '') ON CONFLICT (key) DO NOTHING")
            logging.info("Baza muvaffaqiyatli ishga tushdi!")
        except Exception as e:
            logging.error(f"Baza xatosi: {e}")

async def get_setting(key: str) -> str:
    if not db_pool: return ""
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT value FROM settings WHERE key = $1", key)
        return row['value'] if row else ""

async def set_setting(key: str, value: str):
    if not db_pool: return
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO settings (key, value) VALUES ($1, $2) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", key, value)

async def get_user(user_id: int):
    if not db_pool: return None
    async with db_pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)

async def add_user(user_id: int, referrer_id: int = None):
    if not db_pool: return
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO users (user_id, referrer_id, joined_at) VALUES ($1, $2, $3) ON CONFLICT (user_id) DO NOTHING", user_id, referrer_id, now_str)

async def add_point(user_id: int, amount: int = 1):
    if not db_pool: return
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET points = points + $1 WHERE user_id = $2", amount, user_id)

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
        m = await bot.get_chat_member(chat_id=PRIVATE_CHANNEL_ID, user_id=user_id)
        if m.status in ["member", "restricted"]:
            await bot.ban_chat_member(chat_id=PRIVATE_CHANNEL_ID, user_id=user_id)
            await bot.unban_chat_member(chat_id=PRIVATE_CHANNEL_ID, user_id=user_id)
        if db_pool:
            async with db_pool.acquire() as conn:
                await conn.execute("UPDATE users SET expire_date = NULL WHERE user_id = $1", user_id)
        
        bot_info = await bot.get_me()
        ref_link = f"https://t.me/{bot_info.username}?start={user_id}"
        share_url = f"https://t.me/share/url?url={urllib.parse.quote(ref_link)}&text={urllib.parse.quote('🔥 Заходи в VIP-архив!')}"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📤 Поделиться", url=share_url)],
            [InlineKeyboardButton(text="⚡ VIP на 10 дней", callback_data="free_channel")]
        ])
        await bot.send_message(user_id, "⏱ <b>Тест-драйв завершен!</b>\nХотите полный доступ?", parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception:
        pass

async def get_main_keyboard(user_id: int):
    user = await get_user(user_id)
    points = user['points'] if user else 0
    trial_used = user['trial_used'] if user else 0
    vip_text = "🟢 Забрать VIP" if points >= REQUIRED_REFERRALS else "⚡ Получить VIP"

    kb = [
        [InlineKeyboardButton(text="🪪 Профиль", callback_data="profile"), InlineKeyboardButton(text="🎁 Бонус", callback_data="daily_bonus")],
        [InlineKeyboardButton(text="📂 Архив", callback_data="channels"), InlineKeyboardButton(text=vip_text, callback_data="free_channel")],
    ]
    if trial_used == 0:
        kb.append([InlineKeyboardButton(text="⏱ Тест-драйв (1 мин)", callback_data="trial_access"), InlineKeyboardButton(text="🔥 Превью", callback_data="content_preview")])
    else:
        kb.append([InlineKeyboardButton(text="🔥 Превью контента", callback_data="content_preview")])

    kb.extend([
        [InlineKeyboardButton(text="🔑 Промокод", callback_data="enter_promocode"), InlineKeyboardButton(text="📖 Справка", callback_data="help")],
        [InlineKeyboardButton(text="💬 Поддержка", url=f"https://t.me/{ADMIN_USERNAME}")]
    ])
    chat_url = await get_setting('chat_link')
    if chat_url and chat_url.strip():
        kb.append([InlineKeyboardButton(text="👥 Чат", url=chat_url)])
    return InlineKeyboardMarkup(inline_keyboard=kb)

async def get_sub_keyboard():
    ch1 = await get_setting('channel_1')
    ch2 = await get_setting('channel_2')
    buttons = []
    if ch1 and ch1.strip(): buttons.append([InlineKeyboardButton(text=f"📢 Подписаться ➔ {ch1}", url=f"https://t.me/{ch1.replace('@', '')}")])
    if ch2 and ch2.strip(): buttons.append([InlineKeyboardButton(text=f"📢 Подписаться ➔ {ch2}", url=f"https://t.me/{ch2.replace('@', '')}")])
    buttons.append([InlineKeyboardButton(text=f"✨ Проверить (+{WELCOME_BONUS} балла)", callback_data="check_sub")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

async def get_admin_keyboard():
    ch1 = await get_setting('channel_1') or "Не задан"
    ch2 = await get_setting('channel_2') or "Не задан"
    chat = await get_setting('chat_link') or "Не задан"
    prev = "✅ Есть" if await get_setting('preview_file_id') else "❌ Нет"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"), InlineKeyboardButton(text="📥 Бэкап (CSV)", callback_data="admin_backup")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="➕ Промокод", callback_data="admin_create_promo"), InlineKeyboardButton(text=f"🎬 Превью: {prev}", callback_data="admin_set_preview")],
        [InlineKeyboardButton(text=f"⚙️ Канал 1: {ch1}", callback_data="admin_set_ch1")],
        [InlineKeyboardButton(text=f"⚙️ Канал 2: {ch2}", callback_data="admin_set_ch2")],
        [InlineKeyboardButton(text=f"💬 Чат: {chat}", callback_data="admin_set_chat")],
        [InlineKeyboardButton(text="❌ Закрыть", callback_data="admin_close")]
    ])

async def process_welcome_and_referral(user_id: int):
    user = await get_user(user_id)
    if not user or not db_pool: return False
    got_wel = False
    async with db_pool.acquire() as conn:
        if user['welcome_claimed'] == 0:
            await conn.execute("UPDATE users SET points = points + $1, welcome_claimed = 1 WHERE user_id = $2", WELCOME_BONUS, user_id)
            got_wel = True
        ref_id = user['referrer_id']
        if ref_id:
            ref_user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", ref_id)
            reward, boost = 1, ""
            if ref_user and ref_user['joined_at']:
                if is_2x_active(ref_user['joined_at'])[0]: reward, boost = 2, " (2X Бонус!)"
            await conn.execute("UPDATE users SET points = points + $1 WHERE user_id = $2", reward, ref_id)
            await conn.execute("UPDATE users SET referrer_id = NULL WHERE user_id = $1", user_id)
            try:
                await bot.send_message(ref_id, f"🎉 <b>Реферал подписался!</b> +{reward} балл{boost}", parse_mode=ParseMode.HTML)
            except Exception:
                pass
    return got_wel

def build_welcome_text(first_name: str, user_row, got_welcome: bool = False) -> str:
    boost_line = ""
    if user_row and user_row['joined_at']:
        act, mins = is_2x_active(user_row['joined_at'])
        if act: boost_line = f"\n🔥 <b>2X Турбо-режим ({mins} мин):</b> х2 балла за друзей!\n"
    gift = f"\n🎁 <b>Вам начислено: +{WELCOME_BONUS} балла!</b>\n" if got_welcome else ""
    return (
        f"⚡ <b>Nexus Core, {first_name}</b>\n"
        f"┏━━━━━━━━━━━━━━━━━━━━━\n"
        f"┃ 🛡 Статус: <code>Верифицирован</code>\n"
        f"┗━━━━━━━━━━━━━━━━━━━━━"
        f"{gift}{boost_line}\n"
        f"✨ <b>Возможности:</b>\n"
        f"• ⏱ <b>Тест-драйв</b> — 1 минута в VIP.\n"
        f"• 🎁 <b>Старт</b> — +{WELCOME_BONUS} балла сразу.\n"
        f"• 🔥 <b>Турбо 2X</b> — х2 балла в первый час.\n"
        f"• 🎬 <b>Превью & Бонусы</b> — контент и коды.\n\n"
        f"👇 <i>Выберите раздел:</i>"
    )

@dp.message(CommandStart())
async def start_handler(message: types.Message, command: CommandObject):
    user_id = message.from_user.id
    ref_id = int(command.args) if command.args and command.args.isdigit() and int(command.args) != user_id else None
    if not await get_user(user_id): await add_user(user_id, ref_id)
    if not await check_subscription(user_id):
        await message.answer("🔐 Подпишитесь на каналы для доступа:", reply_markup=await get_sub_keyboard(), parse_mode=ParseMode.HTML)
        return
    got_wel = await process_welcome_and_referral(user_id)
    user = await get_user(user_id)
    await message.answer(build_welcome_text(html.escape(message.from_user.first_name), user, got_wel), reply_markup=await get_main_keyboard(user_id), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    if await check_subscription(user_id):
        got_wel = await process_welcome_and_referral(user_id)
        await callback.answer("✅ Успешно верифицировано!", show_alert=True)
        try: await callback.message.delete()
        except Exception: pass
        user = await get_user(user_id)
        await callback.message.answer(build_welcome_text(html.escape(callback.from_user.first_name), user, got_wel), reply_markup=await get_main_keyboard(user_id), parse_mode=ParseMode.HTML)
    else:
        await callback.answer("⚠️ Вы подписались не на все каналы!", show_alert=True)

@dp.callback_query(F.data == "trial_access")
async def trial_access_handler(callback: CallbackQuery):
    user_id = callback.from_user.id
    if not await check_subscription(user_id):
        await callback.answer("⚠️ Подпишитесь на каналы!", show_alert=True)
        return
    user = await get_user(user_id)
    if user and user['trial_used'] == 1:
        await callback.answer("❌ Тест-драйв уже использован!", show_alert=True)
        return
    await callback.answer()
    try:
        link = await bot.create_chat_invite_link(chat_id=PRIVATE_CHANNEL_ID, creates_join_request=True, expire_date=datetime.now() + timedelta(minutes=10))
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Войти на 1 минуту", url=link.invite_link)],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")]
        ])
        await callback.message.edit_text("⏱ <b>Тест-драйв VIP-канала (60 секунд)</b>\nНажмите кнопку ниже, заявка одобрится автоматически:", reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception:
        await callback.message.edit_text("⚠️ Ошибка ссылки.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")]]))

@dp.callback_query(F.data == "profile")
async def profile_handler(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    user = await get_user(user_id)
    points = user['points'] if user else 0
    expire = user['expire_date'] if user else "Нет"
    trial = "✅ Доступен" if (user and user['trial_used'] == 0) else "❌ Использован"
    is_vip = False
    try:
        m = await bot.get_chat_member(chat_id=PRIVATE_CHANNEL_ID, user_id=user_id)
        if m.status in ["creator", "administrator", "member"]: is_vip = True
    except Exception: pass

    text = f"🪪 <b>Профиль</b>\nID: <code>{user_id}</code>\n⭐ Баллы: <b>{points}/{REQUIRED_REFERRALS}</b>\n⏱ Тест-драйв: <b>{trial}</b>\n🔐 VIP: <b>{'🟢 Активен' if is_vip else '🔴 Неактивен'}</b>"
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")]]), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "content_preview")
async def content_preview_handler(callback: CallbackQuery):
    if not await check_subscription(callback.from_user.id):
        await callback.answer("⚠️ Нужна подписка!", show_alert=True)
        return
    file_id, p_type = await get_setting("preview_file_id"), await get_setting("preview_type")
    caption = await get_setting("preview_caption") or "🔥 <b>Превью VIP-канала!</b>"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ Получить VIP", callback_data="free_channel")],
        [InlineKeyboardButton(text="🗑 Скрыть", callback_data="delete_msg")]
    ])
    await callback.answer()
    if file_id and p_type:
        try:
            if p_type == "photo": await bot.send_photo(callback.from_user.id, photo=file_id, caption=caption, parse_mode=ParseMode.HTML, protect_content=True, reply_markup=kb)
            elif p_type == "video": await bot.send_video(callback.from_user.id, video=file_id, caption=caption, parse_mode=ParseMode.HTML, protect_content=True, reply_markup=kb)
            return
        except Exception: pass
    await callback.message.answer("🔥 <b>Превью VIP-канала (18+)</b>\nТысячи эксклюзивных файлов без цензуры жду вас!", parse_mode=ParseMode.HTML, protect_content=True, reply_markup=kb)

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
        await c.answer("⏳ Бонус уже получен сегодня!", show_alert=True)
        return
    if db_pool:
        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE users SET points = points + 1, last_bonus = $1 WHERE user_id = $2", today, uid)
    await c.answer("🎉 Получен ежедневный бонус: +1 балл ⭐", show_alert=True)
    user = await get_user(uid)
    await c.message.edit_text(build_welcome_text(html.escape(c.from_user.first_name), user, False), reply_markup=await get_main_keyboard(uid), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "enter_promocode")
async def enter_promo(c: CallbackQuery, state: FSMContext):
    await c.answer()
    await state.set_state(UserStates.waiting_for_promo)
    await c.message.edit_text("🔑 Введите промокод:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Отмена", callback_data="back_main")]]))

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
            await message.answer(f"🎉 Промокод активирован! +{rew} баллов ⭐", reply_markup=await get_main_keyboard(uid), parse_mode=ParseMode.HTML)
        else:
            await message.answer("❌ Неверный или использованный код.", reply_markup=await get_main_keyboard(uid), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "channels")
async def channels(c: CallbackQuery):
    if not await check_subscription(c.from_user.id):
        await c.answer("⚠️ Нужна подписка!", show_alert=True)
        return
    await c.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Открыть архив", url=CHANNELS_SECTION_LINK)],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")]
    ])
    await c.message.edit_text("📂 <b>Архивы данных</b>", reply_markup=kb, parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "free_channel")
async def free_channel(c: CallbackQuery):
    uid = c.from_user.id
    if not await check_subscription(uid):
        await c.answer("⚠️ Нужна подписка!", show_alert=True)
        return
    await c.answer()
    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={uid}"
    share_url = f"https://t.me/share/url?url={urllib.parse.quote(ref_link)}&text={urllib.parse.quote('🔥 Заходи!')}"
    user = await get_user(uid)
    points = user['points'] if user else 0
    expire_str = user['expire_date'] if user else None

    is_vip = False
    try:
        m = await bot.get_chat_member(chat_id=PRIVATE_CHANNEL_ID, user_id=uid)
        if m.status in ["creator", "administrator", "member"]: is_vip = True
    except Exception: pass

    bar = "▰" * min(points, REQUIRED_REFERRALS) + "▱" * (REQUIRED_REFERRALS - min(points, REQUIRED_REFERRALS))
    text = f"⚡ <b>VIP Доступ</b>\nПрогресс: [{bar}] <b>{points}/{REQUIRED_REFERRALS}</b>\n\n🔗 <code>{ref_link}</code>"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Поделиться", url=share_url)],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")]
    ])

    if is_vip and expire_str:
        await c.message.edit_text(text + f"\n\n🟢 VIP активен до: {expire_str}", reply_markup=kb, parse_mode=ParseMode.HTML)
        return

    if points >= REQUIRED_REFERRALS:
        exp_str = (datetime.now() + timedelta(days=SUB_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
        if db_pool:
            async with db_pool.acquire() as conn:
                await conn.execute("UPDATE users SET expire_date = $1, warned_expire = 0 WHERE user_id = $2", exp_str, uid)
        link = await bot.create_chat_invite_link(chat_id=PRIVATE_CHANNEL_ID, creates_join_request=True, expire_date=datetime.now() + timedelta(minutes=15))
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔓 Подать заявку", url=link.invite_link)],
            [InlineKeyboardButton(text="📤 Поделиться", url=share_url)],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")]
        ])
        text += f"\n\n🎉 Готово! Доступ открыт на {SUB_DAYS} дней."
    else:
        text += f"\n\n💡 Осталось пригласить: <b>{REQUIRED_REFERRALS - points}</b>"

    await c.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)

@dp.chat_join_request(F.chat.id == PRIVATE_CHANNEL_ID)
async def join_req(req: ChatJoinRequest):
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
            await bot.send_message(uid, f"✅ Заявка одобрена! VIP активен до {exp_str}", parse_mode=ParseMode.HTML)
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
            await bot.send_message(uid, "⏱ Тест-драйв запущен на 60 секунд!", parse_mode=ParseMode.HTML)
        except Exception: pass
        return

    try:
        await req.decline()
        await bot.send_message(uid, "❌ Тест уже использован. Наберите 10 баллов.", parse_mode=ParseMode.HTML)
    except Exception: pass

@dp.callback_query(F.data == "help")
async def help_h(c: CallbackQuery):
    await c.answer()
    await c.message.edit_text("📖 <b>Справка Nexus</b>\nИспользуйте меню для получения доступа.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")]]), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "back_main")
async def back_main(c: CallbackQuery, state: FSMContext):
    await c.answer()
    await state.clear()
    uid = c.from_user.id
    user = await get_user(uid)
    await c.message.edit_text(build_welcome_text(html.escape(c.from_user.first_name), user, False), reply_markup=await get_main_keyboard(uid), parse_mode=ParseMode.HTML)

@dp.message(Command("admin"))
async def admin_start(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    await m.answer("👑 Админ-панель", reply_markup=await get_admin_keyboard(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(c: CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    await c.answer("⏳ Сбор статистики...")
    tot, pts = await get_stats()
    text = f"📊 <b>Статистика:</b>\n👥 Юзеров: <b>{tot}</b>\n⭐ Баллов: <b>{pts}</b>"
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
    await bot.send_document(ADMIN_ID, document=file, caption="📥 Бэкап базы")

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
    await c.message.edit_text("Отправьте код и награду (например: <code>CODE 5</code>):", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel_settings")]]))

@dp.message(AdminStates.waiting_for_promo)
async def save_promo(m: types.Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID: return
    await state.clear()
    parts = m.text.strip().split()
    if len(parts) != 2 or not parts[1].isdigit():
        await m.answer("Формат: <code>CODE 5</code>", reply_markup=await get_admin_keyboard())
        return
    if db_pool:
        async with db_pool.acquire() as conn:
            await conn.execute("INSERT INTO promocodes (code, reward) VALUES ($1, $2) ON CONFLICT (code) DO UPDATE SET reward = EXCLUDED.reward", parts[0], int(parts[1]))
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
                                await bot.send_message(u_id, "⚠️ Подписка истекает через 3 часа!", parse_mode=ParseMode.HTML)
                                await conn.execute("UPDATE users SET warned_expire = 1 WHERE user_id = $1", u_id)
                            except Exception: pass
                        if now >= exp:
                            is_full = pts >= REQUIRED_REFERRALS
                            try:
                                m = await bot.get_chat_member(chat_id=PRIVATE_CHANNEL_ID, user_id=u_id)
                                if m.status in ["member", "restricted"]:
                                    await bot.ban_chat_member(chat_id=PRIVATE_CHANNEL_ID, user_id=u_id)
                                    await bot.unban_chat_member(chat_id=PRIVATE_CHANNEL_ID, user_id=u_id)
                                    if is_full:
                                        await bot.send_message(u_id, "⏰ Срок подписки истек!", parse_mode=ParseMode.HTML)
                            except Exception: pass
                            if is_full:
                                await conn.execute("UPDATE users SET expire_date = NULL, points = $1, warned_expire = 0 WHERE user_id = $2", WELCOME_BONUS, u_id)
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
