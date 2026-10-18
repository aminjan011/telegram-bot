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

# ==================== SOZLAMA (SETTINGS) ====================
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
                    CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                ''')
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS promocodes (
                        code TEXT PRIMARY KEY,
                        reward INT
                    )
                ''')
                await conn.execute("INSERT INTO settings (key, value) VALUES ('channel_1', '@kinozhuldyzkz') ON CONFLICT (key) DO NOTHING")
                await conn.execute("INSERT INTO settings (key, value) VALUES ('channel_2', '') ON CONFLICT (key) DO NOTHING")
                await conn.execute("INSERT INTO settings (key, value) VALUES ('chat_link', '') ON CONFLICT (key) DO NOTHING")
                await conn.execute("INSERT INTO settings (key, value) VALUES ('preview_file_id', '') ON CONFLICT (key) DO NOTHING")
                await conn.execute("INSERT INTO settings (key, value) VALUES ('preview_type', '') ON CONFLICT (key) DO NOTHING")
                await conn.execute("INSERT INTO settings (key, value) VALUES ('preview_caption', '') ON CONFLICT (key) DO NOTHING")
            logging.info("Ma'lumotlar bazasi muvaffaqiyatli ishga tushdi!")
        except Exception as e:
            logging.error(f"Baza ulanishida xatolik: {e}")

async def get_setting(key: str) -> str:
    if not db_pool:
        return ""
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT value FROM settings WHERE key = $1", key)
        return row['value'] if row else ""

async def set_setting(key: str, value: str):
    if not db_pool:
        return
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO settings (key, value) VALUES ($1, $2) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            key, value
        )

async def get_user(user_id: int):
    if not db_pool:
        return None
    async with db_pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)

async def add_user(user_id: int, referrer_id: int = None):
    if not db_pool:
        return
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (user_id, referrer_id, joined_at) VALUES ($1, $2, $3) ON CONFLICT (user_id) DO NOTHING",
            user_id, referrer_id, now_str
        )

async def add_point(user_id: int, amount: int = 1):
    if not db_pool:
        return
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET points = points + $1 WHERE user_id = $2", amount, user_id)

async def get_stats():
    if not db_pool:
        return 0, 0
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT COUNT(*), COALESCE(SUM(points), 0) FROM users")
        return row[0], row[1]

async def get_all_users():
    if not db_pool:
        return []
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT user_id FROM users")
        return [r['user_id'] for r in rows]

async def check_subscription(user_id: int) -> bool:
    ch1 = await get_setting('channel_1')
    ch2 = await get_setting('channel_2')
    channels_to_check = [c for c in [ch1, ch2] if c and c.strip()]
    
    for ch in channels_to_check:
        try:
            member = await bot.get_chat_member(chat_id=ch, user_id=user_id)
            if member.status not in ["creator", "administrator", "member"]:
                return False
        except Exception:
            return False
    return True

def is_2x_active(joined_at_str: str):
    if not joined_at_str:
        return False, 0
    try:
        joined_dt = datetime.strptime(joined_at_str, "%Y-%m-%d %H:%M:%S")
        diff = (joined_dt + timedelta(hours=1)) - datetime.now()
        if diff.total_seconds() > 0:
            return True, int(diff.total_seconds() // 60)
    except Exception:
        pass
    return False, 0

async def kick_after_trial(user_id: int, delay: int = TRIAL_SECONDS):
    await asyncio.sleep(delay)
    try:
        user = await get_user(user_id)
        if user and user['points'] >= REQUIRED_REFERRALS:
            return

        member = await bot.get_chat_member(chat_id=PRIVATE_CHANNEL_ID, user_id=user_id)
        if member.status in ["member", "restricted"]:
            await bot.ban_chat_member(chat_id=PRIVATE_CHANNEL_ID, user_id=user_id)
            await bot.unban_chat_member(chat_id=PRIVATE_CHANNEL_ID, user_id=user_id)

        if db_pool:
            async with db_pool.acquire() as conn:
                await conn.execute("UPDATE users SET expire_date = NULL WHERE user_id = $1", user_id)

        bot_info = await bot.get_me()
        ref_link = f"https://t.me/{bot_info.username}?start={user_id}"
        share_text = "🔥 Заходи в закрытый VIP-архив! За старт сразу дают +2 бонусных балла 🎁"
        share_url = f"https://t.me/share/url?url={urllib.parse.quote(ref_link)}&text={urllib.parse.quote(share_text)}"

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📤 Поделиться с друзьями (в 1 клик)", url=share_url)],
            [InlineKeyboardButton(text="⚡ Открыть VIP на 10 дней", callback_data="free_channel")]
        ])

        await bot.send_message(
            user_id,
            "⏱ <b>Время вашего 1-минутного Тест-драйва истекло!</b>\n"
            "┏━━━━━━━━━━━━━━━━━━━━━\n"
            "┃ Вы лично убедились, что внутри закрытого\n"
            "┃ канала находится эксклюзивный контент.\n"
            "┗━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🔓 Чтобы вернуться обратно и получить полный доступ на <b>{SUB_DAYS} дней</b>, "
            f"пригласите друзей (у вас уже есть бонусные баллы на счету)!",
            parse_mode=ParseMode.HTML,
            reply_markup=kb
        )
    except Exception as e:
        logging.error(f"Trial kick xatosi ({user_id}): {e}")

async def get_main_keyboard(user_id: int):
    user = await get_user(user_id)
    points = user['points'] if user else 0
    trial_used = user['trial_used'] if user else 0
    vip_text = "🟢 Забрать VIP Доступ" if points >= REQUIRED_REFERRALS else "⚡ Получить VIP Доступ"

    keyboard = [
        [InlineKeyboardButton(text="🪪 Личный кабинет", callback_data="profile"), InlineKeyboardButton(text="🎁 Ежедневный бонус", callback_data="daily_bonus")],
        [InlineKeyboardButton(text="📂 Премиум Архив", callback_data="channels"), InlineKeyboardButton(text=vip_text, callback_data="free_channel")],
    ]

    if trial_used == 0:
        keyboard.append([
            InlineKeyboardButton(text="⏱ Тест-драйв (1 мин)", callback_data="trial_access"),
            InlineKeyboardButton(text="🔥 Превью (18+)", callback_data="content_preview")
        ])
    else:
        keyboard.append([InlineKeyboardButton(text="🔥 Превью контента (18+)", callback_data="content_preview")])

    keyboard.extend([
        [InlineKeyboardButton(text="🔑 Промокод", callback_data="enter_promocode"), InlineKeyboardButton(text="📖 Справка", callback_data="help")],
        [InlineKeyboardButton(text="💬 Поддержка", url=f"https://t.me/{ADMIN_USERNAME}")]
    ])

    chat_url = await get_setting('chat_link')
    if chat_url and chat_url.strip():
        keyboard.append([InlineKeyboardButton(text="👥 Закрытый Чат Сообщества", url=chat_url)])
        
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

async def get_sub_keyboard():
    ch1 = await get_setting('channel_1')
    ch2 = await get_setting('channel_2')
    
    buttons = []
    if ch1 and ch1.strip():
        buttons.append([InlineKeyboardButton(text=f"📢 Подписаться ➔ {ch1}", url=f"https://t.me/{ch1.replace('@', '')}")])
    if ch2 and ch2.strip():
        buttons.append([InlineKeyboardButton(text=f"📢 Подписаться ➔ {ch2}", url=f"https://t.me/{ch2.replace('@', '')}")])
        
    buttons.append([InlineKeyboardButton(text=f"✨ Проверить и забрать +{WELCOME_BONUS} балла", callback_data="check_sub")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

async def get_admin_keyboard():
    ch1 = await get_setting('channel_1') or "Не задан"
    ch2 = await get_setting('channel_2') or "Не задан"
    chat = await get_setting('chat_link') or "Не задан"
    prev = "✅ Загружено" if await get_setting('preview_file_id') else "❌ Нет"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Аналитика", callback_data="admin_stats"), InlineKeyboardButton(text="📥 Скачать базу (CSV)", callback_data="admin_backup")],
            [InlineKeyboardButton(text="📢 Массовая рассылка", callback_data="admin_broadcast")],
            [InlineKeyboardButton(text="➕ Создать промокод", callback_data="admin_create_promo"), InlineKeyboardButton(text=f"🎬 Превью: {prev}", callback_data="admin_set_preview")],
            [InlineKeyboardButton(text=f"⚙️ Канал 1: {ch1}", callback_data="admin_set_ch1"), InlineKeyboardButton(text=f"⚙️ Канал 2: {ch2}", callback_data="admin_set_ch2")],
            [InlineKeyboardButton(text=f"💬 Чат: {chat}", callback_data="admin_set_chat")],
            [InlineKeyboardButton(text="❌ Закрыть панель", callback_data="admin_close")]
        ]
    )

async def process_welcome_and_referral(user_id: int):
    user = await get_user(user_id)
    if not user or not db_pool:
        return False

    got_welcome = False
    async with db_pool.acquire() as conn:
        if user['welcome_claimed'] == 0:
            await conn.execute(
                "UPDATE users SET points = points + $1, welcome_claimed = 1 WHERE user_id = $2",
                WELCOME_BONUS, user_id
            )
            got_welcome = True

        ref_id = user['referrer_id']
        if ref_id:
            ref_user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", ref_id)
            reward = 1
            boost_text = ""
            if ref_user and ref_user['joined_at']:
                active_2x, _ = is_2x_active(ref_user['joined_at'])
                if active_2x:
                    reward = 2
                    boost_text = " <i>(🔥 Сработал 2X Турбо-бонус!)</i>"

            await conn.execute("UPDATE users SET points = points + $1 WHERE user_id = $2", reward, ref_id)
            await conn.execute("UPDATE users SET referrer_id = NULL WHERE user_id = $1", user_id)

            try:
                await bot.send_message(
                    ref_id,
                    f"🎉 <b>Новое подключение по вашей ссылке!</b>\n"
                    f"Партнер успешно прошел верификацию. Вам начислено: <b>+{reward} балла</b> ⭐{boost_text}",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass

    return got_welcome

def build_welcome_text(first_name: str, user_row, got_welcome: bool = False) -> str:
    boost_line = ""
    if user_row and user_row['joined_at']:
        active_2x, mins_left = is_2x_active(user_row['joined_at'])
        if active_2x:
            boost_line = f"\n🔥 <b>ТУРБО-РЕЖИМ 2X АКТИВЕН ({mins_left} мин):</b>\n<i>Сейчас вы получаете по <b>+2 балла</b> за каждого приглашенного друга!</i>\n"

    welcome_gift = f"\n🎁 <b>На ваш баланс зачислен подарок: +{WELCOME_BONUS} балла!</b>\n" if got_welcome else ""

    return (
        f"⚡ <b>Добро пожаловать в Nexus Core, {first_name}</b>\n"
        f"┏━━━━━━━━━━━━━━━━━━━━━\n"
        f"┃ 🛡 Статус аккаунта: <code>Верифицирован</code>\n"
        f"┃ 🚀 Уровень доступа: <code>Standard Hub</code>\n"
        f"┗━━━━━━━━━━━━━━━━━━━━━"
        f"{welcome_gift}{boost_line}\n"
        f"✨ <b>Возможности обновленной системы:</b>\n"
        f"• ⏱ <b>Тест-драйв (1 мин)</b> — разовый бесплатный вход в VIP-канал на 60 секунд.\n"
        f"• 🎁 <b>Стартовый бонус</b> — <b>+{WELCOME_BONUS} балла</b> каждому новому участнику.\n"
        f"• 🔥 <b>Турбо 2X</b> — двойные баллы за друзей в первый час после старта.\n"
        f"• 🎬 <b>Превью & Бонусы</b> — демонстрация контента, промокоды и ежедневные награды.\n\n"
        f"👇 <i>Выберите нужный инструмент в панели управления:</i>"
    )

@dp.message(CommandStart())
async def start_handler(message: types.Message, command: CommandObject):
    user_id = message.from_user.id
    args = command.args
    
    referrer_id = int(args) if args and args.isdigit() and int(args) != user_id else None
    
    if not await get_user(user_id):
        await add_user(user_id, referrer_id)
    
    if not await check_subscription(user_id):
        sub_text = (
            "🔐 <b>Система Безопасности & Верификация</b>\n"
            "┏━━━━━━━━━━━━━━━━━━━━━\n"
            "┃ ⚠️ Для разблокировки экосистемы\n"
            "┃ подпишитесь на каналы партнеров.\n"
            f"┃ 🎁 Бонус за подписку: <b>+{WELCOME_BONUS} балла + 1 мин VIP!</b>\n"
            "┗━━━━━━━━━━━━━━━━━━━━━\n\n"
            "👇 <i>Подпишитесь и заберите стартовый пакет:</i>"
        )
        try:
            await message.answer(sub_text, reply_markup=await get_sub_keyboard(), parse_mode=ParseMode.HTML)
        except TelegramForbiddenError:
            pass
        return

    got_welcome = await process_welcome_and_referral(user_id)
    user = await get_user(user_id)
    first_name = html.escape(message.from_user.first_name)
    welcome_text = build_welcome_text(first_name, user, got_welcome)
    
    try:
        await message.answer(welcome_text, reply_markup=await get_main_keyboard(user_id), parse_mode=ParseMode.HTML)
    except TelegramForbiddenError:
        pass

@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    
    if await check_subscription(user_id):
        got_welcome = await process_welcome_and_referral(user_id)
        if got_welcome:
            await callback.answer(f"🎉 Верификация пройдена! Вам начислено +{WELCOME_BONUS} приветственных балла!", show_alert=True)
        else:
            await callback.answer("✅ Подписка подтверждена!")

        try:
            await callback.message.delete()
        except Exception:
            pass
            
        user = await get_user(user_id)
        first_name = html.escape(callback.from_user.first_name)
        welcome_text = build_welcome_text(first_name, user, got_welcome)
        await callback.message.answer(welcome_text, reply_markup=await get_main_keyboard(user_id), parse_mode=ParseMode.HTML)
    else:
        await callback.answer("⚠️ Вы подписались еще не на все обязательные каналы!", show_alert=True)

@dp.callback_query(F.data == "trial_access")
async def trial_access_handler(callback: CallbackQuery):
    user_id = callback.from_user.id
    if not await check_subscription(user_id):
        await callback.answer("⚠️ Сначала подпишитесь на обязательные каналы!", show_alert=True)
        return

    user = await get_user(user_id)
    if not user:
        return

    if user['trial_used'] == 1:
        await callback.answer("❌ Вы уже использовали свой разовый 1-минутный тест-драйв!", show_alert=True)
        return

    await callback.answer()
    try:
        invite_link = await bot.create_chat_invite_link(
            chat_id=PRIVATE_CHANNEL_ID,
            creates_join_request=True,
            expire_date=datetime.now() + timedelta(minutes=10)
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Войти на 1 минуту (Авто-одобрение)", url=invite_link.invite_link)],
            [InlineKeyboardButton(text="◀️ Назад в меню", callback_data="back_main")]
        ])
        text = (
            "⏱ <b>Тест-драйв закрытого VIP-канала (60 секунд)</b>\n"
            "┏━━━━━━━━━━━━━━━━━━━━━\n"
            "┃ 🔓 Режим: <code>Разовый пробный доступ</code>\n"
            "┃ ⏳ Длительность: <code>Ровно 1 минута</code>\n"
            "┗━━━━━━━━━━━━━━━━━━━━━\n\n"
            "⚠️ <b>Как это работает:</b>\n"
            "1. Нажмите кнопку ниже и подайте заявку на вступление.\n"
            "2. Бот <b>мгновенно одобрит</b> её и запустит таймер на <b>60 секунд</b>.\n"
            "3. По истечении минуты система автоматически исключит вас из канала.\n\n"
            "👇 <i>Приготовьтесь и нажимайте кнопку входа:</i>"
        )
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception as e:
        logging.error(f"Trial invite link xatosi: {e}")
        await callback.message.edit_text(
            "⚠️ Ошибка генерации тестовой ссылки. Убедитесь, что бот является администратором закрытого канала.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")]])
        )

@dp.callback_query(F.data == "profile")
async def profile_handler(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    user = await get_user(user_id)
    
    points = user['points'] if user else 0
    expire_str = user['expire_date'] if user else "Не активна"
    trial_status = "✅ Доступен (1 мин)" if (user and user['trial_used'] == 0) else "❌ Использован"
    
    is_in_private = False
    try:
        member = await bot.get_chat_member(chat_id=PRIVATE_CHANNEL_ID, user_id=user_id)
        if member.status in ["creator", "administrator", "member"]:
            is_in_private = True
    except Exception:
        pass

    status_badge = "🟢 Активен (VIP)" if is_in_private else "🔴 Неактивен"
    active_2x, mins = is_2x_active(user['joined_at']) if user else (False, 0)
    boost_status = f"🔥 Активен ({mins} мин)" if active_2x else "⚪ Завершен (1X)"

    profile_text = (
        f"🪪 <b>Персональный профиль пользователя</b>\n"
        f"┏━━━━━━━━━━━━━━━━━━━━━\n"
        f"┃ 🆔 ID аккаунта: <code>{user_id}</code>\n"
        f"┃ ⭐ Баланс баллов: <b>{points} / {REQUIRED_REFERRALS}</b>\n"
        f"┃ ⏱ Тест-драйв: <b>{trial_status}</b>\n"
        f"┃ ⚡ Турбо-бонус 2X: <b>{boost_status}</b>\n"
        f"┃ 🔐 Доступ в VIP: <b>{status_badge}</b>\n"
        f"┃ ⏳ Истекает: <code>{expire_str if is_in_private else 'Отсутствует'}</code>\n"
        f"┗━━━━━━━━━━━━━━━━━━━━━"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ На главную панель", callback_data="back_main")]])
    await callback.message.edit_text(profile_text, reply_markup=kb, parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "content_preview")
async def content_preview_handler(callback: CallbackQuery):
    user_id = callback.from_user.id
    if not await check_subscription(user_id):
        await callback.answer("⚠️ Сначала подпишитесь на обязательные каналы!", show_alert=True)
        return

    file_id = await get_setting("preview_file_id")
    p_type = await get_setting("preview_type")
    caption = await get_setting("preview_caption") or (
        "🔥 <b>Эксклюзивное превью закрытого архива!</b>\n\n"
        "В приватном VIP-канале вас ждут тысячи полных материалов без цензуры.\n"
        "⚡ <i>Пригласите друзей и получите полный доступ абсолютно бесплатно!</i>"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ Получить полный VIP доступ", callback_data="free_channel")],
        [InlineKeyboardButton(text="🗑 Скрыть превью", callback_data="delete_msg")]
    ])

    await callback.answer()
    if file_id and p_type:
        try:
            if p_type == "photo":
                await bot.send_photo(chat_id=user_id, photo=file_id, caption=caption, parse_mode=ParseMode.HTML, protect_content=True, reply_markup=kb)
            elif p_type == "video":
                await bot.send_video(chat_id=user_id, video=file_id, caption=caption, parse_mode=ParseMode.HTML, protect_content=True, reply_markup=kb)
            return
        except Exception as e:
            logging.error(f"Preview yuborishda xato: {e}")

    fallback_text = (
        "🔥 <b>Превью закрытого VIP-канала (18+)</b>\n"
        "┏━━━━━━━━━━━━━━━━━━━━━\n"
        "┃ 🎬 Внутри: <b>Более 2,500+ эксклюзивных видео</b>\n"
        "┃ 📸 Фото-архивы: <b>Полные сливы без цензуры</b>\n"
        "┃ 🔄 Обновления: <b>Ежедневно в HD качестве</b>\n"
        "┃ 🛡 Защита: <b>Приватный доступ без рекламы</b>\n"
        "┗━━━━━━━━━━━━━━━━━━━━━\n\n"
        "💡 <i>Используйте кнопку «⏱ Тест-драйв (1 мин)» в меню, чтобы зайти и увидеть всё своими глазами!</i>"
    )
    await callback.message.answer(fallback_text, parse_mode=ParseMode.HTML, protect_content=True, reply_markup=kb)

@dp.callback_query(F.data == "delete_msg")
async def delete_msg_handler(callback: CallbackQuery):
    await callback.answer()
    try:
        await callback.message.delete()
    except Exception:
        pass

@dp.callback_query(F.data == "daily_bonus")
async def daily_bonus_handler(callback: CallbackQuery):
    user_id = callback.from_user.id
    user = await get_user(user_id)
    today_str = datetime.now().strftime("%Y-%m-%d")

    if user and user['last_bonus'] == today_str:
        await callback.answer("⏳ Вы уже получили бонус сегодня! Возвращайтесь завтра.", show_alert=True)
        return

    if db_pool:
        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE users SET points = points + 1, last_bonus = $1 WHERE user_id = $2", today_str, user_id)

    await callback.answer("🎉 Успешно! Вам начислен ежедневный бонус: +1 балл ⭐", show_alert=True)
    
    try:
        user = await get_user(user_id)
        first_name = html.escape(callback.from_user.first_name)
        welcome_text = build_welcome_text(first_name, user, False)
        await callback.message.edit_text(welcome_text, reply_markup=await get_main_keyboard(user_id), parse_mode=ParseMode.HTML)
    except Exception:
        pass

@dp.callback_query(F.data == "enter_promocode")
async def enter_promocode_handler(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(UserStates.waiting_for_promo)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Отмена", callback_data="back_main")]])
    await callback.message.edit_text(
        "🔑 <b>Активация промокода</b>\n"
        "┏━━━━━━━━━━━━━━━━━━━━━\n"
        "┃ Введите секретный промокод в чат,\n"
        "┃ чтобы мгновенно получить баллы.\n"
        "┗━━━━━━━━━━━━━━━━━━━━━",
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )

@dp.message(UserStates.waiting_for_promo)
async def process_user_promocode(message: types.Message, state: FSMContext):
    await state.clear()
    code = message.text.strip()
    user_id = message.from_user.id

    if not db_pool:
        return

    async with db_pool.acquire() as conn:
        promo = await conn.fetchrow("SELECT reward FROM promocodes WHERE code = $1", code)
        if promo:
            reward = promo['reward']
            await conn.execute("UPDATE users SET points = points + $1 WHERE user_id = $2", reward, user_id)
            await conn.execute("DELETE FROM promocodes WHERE code = $1", code)
            await message.answer(
                f"🎉 <b>Промокод активирован!</b>\nНачислено на баланс: <b>+{reward} баллов</b> ⭐",
                parse_mode=ParseMode.HTML,
                reply_markup=await get_main_keyboard(user_id)
            )
        else:
            await message.answer(
                "❌ <b>Ошибка:</b> Неверный или уже использованный промокод.",
                parse_mode=ParseMode.HTML,
                reply_markup=await get_main_keyboard(user_id)
            )

@dp.callback_query(F.data == "channels")
async def channels_handler(callback: CallbackQuery):
    await callback.answer()
    if not await check_subscription(callback.from_user.id):
        await callback.answer("⚠️ Требуется обязательная подписка!", show_alert=True)
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Открыть защищенный архив", url=CHANNELS_SECTION_LINK)],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")]
        ]
    )
    await callback.message.edit_text(
        "📂 <b>Премиум Архивы Данных</b>\n"
        "┏━━━━━━━━━━━━━━━━━━━━━\n"
        "┃ Все эксклюзивные подборки, медиафайлы\n"
        "┃ и материалы собраны в защищенном канале.\n"
        "┗━━━━━━━━━━━━━━━━━━━━━\n\n"
        "👇 <i>Нажмите для перехода:</i>",
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )

@dp.callback_query(F.data == "free_channel")
async def free_channel_handler(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    if not await check_subscription(user_id):
        await callback.answer("⚠️ Сначала подпишитесь на обязательные каналы!", show_alert=True)
        return

    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={user_id}"
    
    share_text = "🔥 Заходи в закрытый VIP-архив! За старт сразу дают +2 бонусных балла и 1 минуту бесплатного тест-драйва 🎁"
    share_url = f"https://t.me/share/url?url={urllib.parse.quote(ref_link)}&text={urllib.parse.quote(share_text)}"

    user = await get_user(user_id)
    points = user['points'] if user else 0
    expire_str = user['expire_date'] if user else None

    is_in_private = False
    try:
        member = await bot.get_chat_member(chat_id=PRIVATE_CHANNEL_ID, user_id=user_id)
        if member.status in ["creator", "administrator", "member"]:
            is_in_private = True
    except Exception:
        pass

    filled = min(points, REQUIRED_REFERRALS)
    empty = REQUIRED_REFERRALS - filled
    progress_bar = "▰" * filled + "▱" * empty

    active_2x, mins = is_2x_active(user['joined_at']) if user else (False, 0)
    boost_notice = f"🔥 <b>2X ТУРБО АКТИВЕН ({mins} мин):</b> +2 балла за друга!\n\n" if active_2x else ""

    text = (
        f"⚡ <b>VIP Доступ по реферальной сети</b>\n"
        f"┏━━━━━━━━━━━━━━━━━━━━━\n"
        f"┃ Приглашайте друзей одной кнопкой,\n"
        f"┃ заполняйте шкалу и забирайте VIP-доступ.\n"
        f"┗━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{boost_notice}"
        f"📊 Прогресс: [{progress_bar}] <b>{points}/{REQUIRED_REFERRALS}</b>\n\n"
        f"🔗 <b>Ваша персональная ссылка (нажмите для копирования):</b>\n"
        f"<code>{ref_link}</code>\n\n"
    )
    
    kb_share_back = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Поделиться с друзьями (в 1 клик)", url=share_url)],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")]
    ])

    if is_in_private and expire_str:
        try:
            if datetime.now() < datetime.strptime(expire_str, "%Y-%m-%d %H:%M:%S"):
                text += f"🟢 <b>Статус:</b> VIP доступ активен до <b>{expire_str}</b>"
                await callback.message.edit_text(text, reply_markup=kb_share_back, parse_mode=ParseMode.HTML)
                return
        except Exception:
            pass

    if points >= REQUIRED_REFERRALS:
        expire_dt = datetime.now() + timedelta(days=SUB_DAYS)
        expire_date_str = expire_dt.strftime("%Y-%m-%d %H:%M:%S")
        
        if db_pool:
            async with db_pool.acquire() as conn:
                await conn.execute("UPDATE users SET expire_date = $1, warned_expire = 0 WHERE user_id = $2", expire_date_str, user_id)

        try:
            invite_link = await bot.create_chat_invite_link(
                chat_id=PRIVATE_CHANNEL_ID,
                creates_join_request=True,
                expire_date=datetime.now() + timedelta(minutes=15)
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔓 Подать заявку (Авто-принятие за 1 сек)", url=invite_link.invite_link)],
                [InlineKeyboardButton(text="📤 Поделиться с друзьями", url=share_url)],
                [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")]
            ])
            text += (
                f"🎉 <b>Цель достигнута!</b> Вам открыт доступ на <b>{SUB_DAYS} дней</b> (до {expire_date_str}).\n\n"
                f"🛡 <i>Нажмите кнопку ниже и подайте заявку — бот автоматически одобрит ваш аккаунт за 1 секунду!</i>"
            )
        except Exception as e:
            logging.error(f"Invite link xatosi: {e}")
            text += "⚠️ Ошибка генерации ссылки. Убедитесь, что у бота есть права на управление пригласительными ссылками."
            kb = kb_share_back
    else:
        needed = REQUIRED_REFERRALS - points
        text += f"💡 До разблокировки осталось набрать: <b>{needed} баллов</b>.\n👇 <i>Нажмите кнопку «Поделиться», чтобы быстро отправить ссылку друзьям!</i>"
        kb = kb_share_back

    await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)

@dp.chat_join_request(F.chat.id == PRIVATE_CHANNEL_ID)
async def handle_private_join_request(request: ChatJoinRequest):
    user_id = request.from_user.id
    user = await get_user(user_id)

    if not user:
        try:
            await request.decline()
        except Exception:
            pass
        return

    if user['points'] >= REQUIRED_REFERRALS:
        try:
            await request.approve()
            expire_dt = datetime.now() + timedelta(days=SUB_DAYS)
            expire_str = expire_dt.strftime("%Y-%m-%d %H:%M:%S")
            if db_pool:
                async with db_pool.acquire() as conn:
                    await conn.execute("UPDATE users SET expire_date = $1, warned_expire = 0 WHERE user_id = $2", expire_str, user_id)

            await bot.send_message(
                user_id,
                f"✅ <b>Ваша VIP-заявка автоматически одобрена!</b>\n"
                f"Добро пожаловать в закрытый канал на <b>{SUB_DAYS} дней</b> (до <code>{expire_str}</code>).",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logging.error(f"Join request full approve xatosi: {e}")
        return

    if user['trial_used'] == 0:
        try:
            await request.approve()
            expire_dt = datetime.now() + timedelta(seconds=TRIAL_SECONDS)
            expire_str = expire_dt.strftime("%Y-%m-%d %H:%M:%S")
            
            if db_pool:
                async with db_pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE users SET trial_used = 1, expire_date = $1, warned_expire = 1 WHERE user_id = $2",
                        expire_str, user_id
                    )

            asyncio.create_task(kick_after_trial(user_id, TRIAL_SECONDS))

            await bot.send_message(
                user_id,
                "⏱ <b>Тест-драйв запущен! У вас есть ровно 60 секунд!</b>\n\n"
                "Быстрее переходите в канал и оцените эксклюзивный контент!",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logging.error(f"Join request trial approve xatosi: {e}")
        return

    try:
        await request.decline()
        await bot.send_message(
            user_id,
            "❌ <b>Доступ отклонен!</b>\nВы уже использовали пробную минуту. Для полного входа наберите 10 баллов внутри бота.",
            parse_mode=ParseMode.HTML
        )
    except Exception:
        pass

@dp.callback_query(F.data == "help")
async def help_handler(callback: CallbackQuery):
    await callback.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")]])
    help_text = (
        "📖 <b>Справочный центр Nexus</b>\n"
        "┏━━━━━━━━━━━━━━━━━━━━━\n"
        "┃ • <b>Тест-драйв (1 мин)</b> — разовый вход в VIP-канал на 60 сек.\n"
        f"┃ • <b>Стартовый подарок</b> — +{WELCOME_BONUS} балла сразу за старт.\n"
        "┃ • <b>Турбо 2X</b> — в первый час +2 балла за друга.\n"
        "┃ • <b>Превью</b> — демонстрация контента закрытого канала.\n"
        "┃ • <b>Ежедневный бонус</b> — стабильно по +1 баллу в день.\n"
        "┃ • <b>Промокоды</b> — секретные коды для поощрения.\n"
        "┗━━━━━━━━━━━━━━━━━━━━━\n\n"
        "💬 Возникли вопросы? Свяжитесь с поддержкой в главном меню."
    )
    await callback.message.edit_text(help_text, reply_markup=kb, parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "back_main")
async def back_main_handler(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    user_id = callback.from_user.id
    user = await get_user(user_id)
    first_name = html.escape(callback.from_user.first_name)
    welcome_text = build_welcome_text(first_name, user, False)
    await callback.message.edit_text(welcome_text, reply_markup=await get_main_keyboard(user_id), parse_mode=ParseMode.HTML)

# ==================== ADMIN PANEL ====================
@dp.message(Command("admin"))
async def admin_start(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("👑 <b>Панель администратора (Nexus Control)</b>", reply_markup=await get_admin_keyboard(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "admin_stats")
async def admin_stats_handler(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    await callback.answer("⏳ Сканирование базы данных...", show_alert=False)
    
    total_users, total_points = await get_stats()
    users = await get_all_users()
    
    semaphore = asyncio.Semaphore(30)
    async def check_u(uid):
        async with semaphore:
            try:
                await bot.send_chat_action(chat_id=uid, action="typing")
                return True
            except Exception:
                return False

    results = await asyncio.gather(*[check_u(uid) for uid in users])
    active = sum(1 for r in results if r)

    text = (
        f"📊 <b>Аналитический отчет системы:</b>\n"
        f"┏━━━━━━━━━━━━━━━━━━━━━\n"
        f"┃ 👤 Всего пользователей: <b>{total_users}</b>\n"
        f"┃ 🟢 Активных онлайн: <b>{active}</b>\n"
        f"┃ 🔴 Заблокировали бота: <b>{total_users - active}</b>\n"
        f"┃ ⭐ Суммарно баллов: <b>{total_points}</b>\n"
        f"┗━━━━━━━━━━━━━━━━━━━━━"
    )
    try:
        await callback.message.edit_text(text, reply_markup=await get_admin_keyboard(), parse_mode=ParseMode.HTML)
    except TelegramBadRequest:
        pass

@dp.callback_query(F.data == "admin_backup")
async def admin_backup_handler(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    await callback.answer("📥 Формирование резервной копии...")

    if not db_pool:
        return

    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT user_id, referrer_id, points, trial_used, expire_date, joined_at FROM users ORDER BY points DESC")

    lines = ["user_id,referrer_id,points,trial_used,expire_date,joined_at"]
    for r in rows:
        lines.append(f"{r['user_id']},{r['referrer_id'] or ''},{r['points']},{r['trial_used']},{r['expire_date'] or ''},{r['joined_at'] or ''}")

    csv_bytes = "\n".join(lines).encode("utf-8")
    filename = f"nexus_backup_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    file = BufferedInputFile(csv_bytes, filename=filename)

    await bot.send_document(
        chat_id=ADMIN_ID,
        document=file,
        caption=f"📥 <b>Полный бэкап базы пользователей</b>\n👥 Всего записей: <b>{len(rows)}</b>",
        parse_mode=ParseMode.HTML
    )

@dp.callback_query(F.data == "admin_set_preview")
async def admin_set_preview(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    await callback.answer()
    await state.set_state(AdminStates.waiting_for_preview)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Удалить текущее превью", callback_data="admin_remove_preview")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel_settings")]
    ])
    await callback.message.edit_text(
        "🎬 <b>Настройка превью контента</b>\n\n"
        "Отправьте сюда <b>Фото</b> или <b>Короткое Видео</b> (можно с текстом в описании), "
        "которое будет показываться пользователям при нажатии кнопки «🔥 Превью контента»:",
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )

@dp.callback_query(F.data == "admin_remove_preview")
async def admin_remove_preview(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    await callback.answer()
    await set_setting("preview_file_id", "")
    await set_setting("preview_type", "")
    await set_setting("preview_caption", "")
    await state.clear()
    await callback.message.edit_text("✅ Превью успешно сброшено на текстовый режим!", reply_markup=await get_admin_keyboard())

@dp.message(AdminStates.waiting_for_preview)
async def process_admin_preview(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return

    file_id = ""
    p_type = ""
    if message.photo:
        file_id = message.photo[-1].file_id
        p_type = "photo"
    elif message.video:
        file_id = message.video.file_id
        p_type = "video"
    else:
        await message.answer("❌ Пожалуйста, отправьте именно <b>фото</b> или <b>видео</b>!", parse_mode=ParseMode.HTML)
        return

    caption = message.html_text if message.caption else ""
    await set_setting("preview_file_id", file_id)
    await set_setting("preview_type", p_type)
    await set_setting("preview_caption", caption)
    await state.clear()
    await message.answer("✅ <b>Медиа-превью успешно сохранено!</b>\nТеперь оно защищено от копирования и доступно в главном меню.", parse_mode=ParseMode.HTML, reply_markup=await get_admin_keyboard())

@dp.callback_query(F.data == "admin_create_promo")
async def admin_create_promo(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    await callback.answer()
    await state.set_state(AdminStates.waiting_for_promo)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel_settings")]])
    await callback.message.edit_text("✏️ Отправьте промокод и награду через пробел (например: <code>NEXUS 5</code>):", reply_markup=kb, parse_mode=ParseMode.HTML)

@dp.message(AdminStates.waiting_for_promo)
async def process_create_promo(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    parts = message.text.strip().split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("❌ <b>Неверный формат!</b> Пример: <code>NEXUS 5</code>", parse_mode=ParseMode.HTML, reply_markup=await get_admin_keyboard())
        return

    code, reward = parts[0], int(parts[1])
    if db_pool:
        async with db_pool.acquire() as conn:
            await conn.execute("INSERT INTO promocodes (code, reward) VALUES ($1, $2) ON CONFLICT (code) DO UPDATE SET reward = EXCLUDED.reward", code, reward)

    await message.answer(f"✅ <b>Промокод успешно создан!</b>\nКод: <code>{code}</code>\nНаграда: <b>+{reward} баллов</b>", parse_mode=ParseMode.HTML, reply_markup=await get_admin_keyboard())

@dp.callback_query(F.data == "admin_set_ch1")
async def admin_set_ch1(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    await callback.answer()
    await state.set_state(AdminStates.waiting_for_ch1)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel_settings")]])
    await callback.message.edit_text("✏️ Отправьте юзернейм 1-го канала (например: <code>@channel</code>):", reply_markup=kb, parse_mode=ParseMode.HTML)

@dp.message(AdminStates.waiting_for_ch1)
async def process_ch1(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    ch = message.text.strip()
    if not ch.startswith("@"): ch = "@" + ch
    await set_setting("channel_1", ch)
    await state.clear()
    await message.answer(f"✅ Канал 1 изменен на: <b>{ch}</b>", reply_markup=await get_admin_keyboard(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "admin_set_ch2")
async def admin_set_ch2(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    await callback.answer()
    await state.set_state(AdminStates.waiting_for_ch2)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Удалить 2-й канал", callback_data="admin_remove_ch2")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel_settings")]
        ]
    )
    await callback.message.edit_text("✏️ Отправьте юзернейм 2-го канала или удалите его:", reply_markup=kb, parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "admin_remove_ch2")
async def admin_remove_ch2(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    await callback.answer()
    await set_setting("channel_2", "")
    await state.clear()
    await callback.message.edit_text("✅ 2-й канал успешно удален!", reply_markup=await get_admin_keyboard())

@dp.message(AdminStates.waiting_for_ch2)
async def process_ch2(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    ch = message.text.strip()
    if not ch.startswith("@"): ch = "@" + ch
    await set_setting("channel_2", ch)
    await state.clear()
    await message.answer(f"✅ Канал 2 установлен: <b>{ch}</b>", reply_markup=await get_admin_keyboard(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "admin_set_chat")
async def admin_set_chat(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    await callback.answer()
    await state.set_state(AdminStates.waiting_for_chat)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Удалить ссылку на чат", callback_data="admin_remove_chat")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel_settings")]
        ]
    )
    await callback.message.edit_text("✏️ Отправьте полную ссылку на чат (например: <code>https://t.me/...</code>):", reply_markup=kb, parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "admin_remove_chat")
async def admin_remove_chat(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    await callback.answer()
    await set_setting("chat_link", "")
    await state.clear()
    await callback.message.edit_text("✅ Ссылка на чат удалена!", reply_markup=await get_admin_keyboard())

@dp.message(AdminStates.waiting_for_chat)
async def process_chat(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    chat_url = message.text.strip()
    await set_setting("chat_link", chat_url)
    await state.clear()
    await message.answer(f"✅ Ссылка на чат сохранена:\n<b>{chat_url}</b>", reply_markup=await get_admin_keyboard(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_handler(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    await callback.answer()
    await state.set_state(AdminStates.waiting_for_broadcast)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel_settings")]])
    await callback.message.edit_text("📢 Отправьте сообщение (текст, фото или видео) для массовой рассылки:", reply_markup=kb)

@dp.message(AdminStates.waiting_for_broadcast)
async def process_broadcast(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    users = await get_all_users()
    await message.answer(f"⏳ Рассылка запущена для {len(users)} пользователей...")
    
    success, failed = 0, 0
    for uid in users:
        try:
            await message.copy_to(chat_id=uid)
            success += 1
            await asyncio.sleep(0.04)
        except (TelegramForbiddenError, TelegramAPIError, Exception):
            failed += 1
            
    await message.answer(f"✅ <b>Рассылка завершена!</b>\n\n🎉 Успешно: {success}\n❌ Ошибок: {failed}", reply_markup=await get_admin_keyboard(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "admin_cancel_settings")
async def cancel_settings(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.edit_text("👑 <b>Панель администратора (Nexus Control)</b>", reply_markup=await get_admin_keyboard(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "admin_close")
async def admin_close(callback: CallbackQuery):
    await callback.answer()
    await callback.message.delete()

# --- BACKGROUND TASKS ---
async def auto_kick_expired_users():
    while True:
        try:
            if db_pool:
                async with db_pool.acquire() as conn:
                    now = datetime.now()
                    users = await conn.fetch("SELECT user_id, points, expire_date, warned_expire FROM users WHERE expire_date IS NOT NULL AND expire_date != ''")

                    for r in users:
                        u_id = r['user_id']
                        pts = r['points']
                        warned = r['warned_expire']
                        try:
                            expire_dt = datetime.strptime(r['expire_date'], "%Y-%m-%d %H:%M:%S")
                            
                            if 0 < (expire_dt - now).total_seconds() <= 10800 and warned == 0:
                                try:
                                    await bot.send_message(
                                        u_id,
                                        "⚠️ <b>Внимание! Срок вашей VIP-подписки истекает через 3 часа!</b>\n"
                                        "Пригласите друзей заранее, чтобы не потерять доступ к закрытому архиву.",
                                        parse_mode=ParseMode.HTML
                                    )
                                    await conn.execute("UPDATE users SET warned_expire = 1 WHERE user_id = $1", u_id)
                                except Exception:
                                    pass

                            if now >= expire_dt:
                                is_full_sub = pts >= REQUIRED_REFERRALS
                                try:
                                    member = await bot.get_chat_member(chat_id=PRIVATE_CHANNEL_ID, user_id=u_id)
                                    if member.status in ["member", "restricted"]:
                                        await bot.ban_chat_member(chat_id=PRIVATE_CHANNEL_ID, user_id=u_id)
                                        await bot.unban_chat_member(chat_id=PRIVATE_CHANNEL_ID, user_id=u_id)
                                        if is_full_sub:
                                            try:
                                                await bot.send_message(
                                                    u_id,
                                                    f"⏰ <b>Срок вашей VIP-подписки ({SUB_DAYS} дней) истек!</b>\n"
                                                    f"Вы были автоматически исключены из закрытого канала.\n\n"
                                                    f"🎁 <i>Мы оставили вам <b>+{WELCOME_BONUS} бонусных балла</b> на счету, чтобы вы могли быстрее вернуться!</i>",
                                                    parse_mode=ParseMode.HTML
                                                )
                                            except Exception:
                                                pass
                                except Exception:
                                    pass

                                if is_full_sub:
                                    await conn.execute("UPDATE users SET expire_date = NULL, points = $1, warned_expire = 0 WHERE user_id = $2", WELCOME_BONUS, u_id)
                                else:
                                    await conn.execute("UPDATE users SET expire_date = NULL, warned_expire = 0 WHERE user_id = $1", u_id)
                        except Exception:
                            pass
        except Exception:
            pass
        await asyncio.sleep(15)

async def on_startup(app):
    await init_db_pool()
    asyncio.create_task(auto_kick_expired_users())
    if RENDER_EXTERNAL_URL:
        webhook_url = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}"
        await bot.set_webhook(
            webhook_url, 
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query", "chat_join_request"]
        )
        logging.info(f"Webhook o'rnatildi: {webhook_url}")

def main():
    app = web.Application()
    app.router.add_get("/", lambda req: web.Response(text="Nexus Core Active", status=200))

    webhook_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_handler.register(app, path=WEBHOOK_PATH)

    setup_application(app, dp, bot=bot)
    app.on_startup.append(on_startup)

    web.run_app(app, host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    main()
