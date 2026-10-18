import os
import logging
import html
import asyncio
from datetime import datetime, timedelta
import asyncpg
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, CommandObject, Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
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
ADMIN_USERNAME = "zyroxcv"
ADMIN_ID = 1112793157

WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
DATABASE_URL = os.getenv("DATABASE_URL")
PORT = int(os.getenv("PORT", 8080))
# ============================================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

class AdminStates(StatesGroup):
    waiting_for_broadcast = State()
    waiting_for_ch1 = State()
    waiting_for_ch2 = State()
    waiting_for_chat = State()

# --- ASYNC DATABASE CONNECTION POOL ---
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
                        expire_date TEXT
                    )
                ''')
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                ''')
                await conn.execute("INSERT INTO settings (key, value) VALUES ('channel_1', '@kinozhuldyzkz') ON CONFLICT (key) DO NOTHING")
                await conn.execute("INSERT INTO settings (key, value) VALUES ('channel_2', '') ON CONFLICT (key) DO NOTHING")
                await conn.execute("INSERT INTO settings (key, value) VALUES ('chat_link', '') ON CONFLICT (key) DO NOTHING")
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
        row = await conn.fetchrow("SELECT user_id, referrer_id, points, has_access, expire_date FROM users WHERE user_id = $1", user_id)
        return row

async def add_user(user_id: int, referrer_id: int = None):
    if not db_pool:
        return
    async with db_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO users (user_id, referrer_id) VALUES ($1, $2) ON CONFLICT (user_id) DO NOTHING",
            user_id, referrer_id
        )

async def add_point(user_id: int):
    if not db_pool:
        return
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET points = points + 1 WHERE user_id = $1", user_id)

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

# --- PREMIUM UI KEYBOARDS ---
async def get_main_keyboard():
    keyboard = [
        [InlineKeyboardButton(text="💎 Личный кабинет", callback_data="profile")],
        [InlineKeyboardButton(text="📁 Премиум Архив", callback_data="channels"), InlineKeyboardButton(text="⚡ VIP Доступ", callback_data="free_channel")],
        [InlineKeyboardButton(text="📖 Справка", callback_data="help"), InlineKeyboardButton(text="💬 Поддержка", url=f"https://t.me/{ADMIN_USERNAME}")]
    ]
    chat_url = await get_setting('chat_link')
    if chat_url and chat_url.strip():
        keyboard.append([InlineKeyboardButton(text="👥 Сообщество / Чат", url=chat_url)])
        
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

async def get_sub_keyboard():
    ch1 = await get_setting('channel_1')
    ch2 = await get_setting('channel_2')
    
    buttons = []
    if ch1 and ch1.strip():
        buttons.append([InlineKeyboardButton(text=f"📌 Подписаться на {ch1}", url=f"https://t.me/{ch1.replace('@', '')}")])
    if ch2 and ch2.strip():
        buttons.append([InlineKeyboardButton(text=f"📌 Подписаться на {ch2}", url=f"https://t.me/{ch2.replace('@', '')}")])
        
    buttons.append([InlineKeyboardButton(text="🔄 Проверить подписку", callback_data="check_sub")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

async def get_admin_keyboard():
    ch1 = await get_setting('channel_1') or "Не задан"
    ch2 = await get_setting('channel_2') or "Не задан"
    chat = await get_setting('chat_link') or "Не задан"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Аналитика бота", callback_data="admin_stats")],
            [InlineKeyboardButton(text="📢 Массовая рассылка", callback_data="admin_broadcast")],
            [InlineKeyboardButton(text=f"⚙️ Канал 1: {ch1}", callback_data="admin_set_ch1")],
            [InlineKeyboardButton(text=f"⚙️ Канал 2: {ch2}", callback_data="admin_set_ch2")],
            [InlineKeyboardButton(text=f"💬 Чат: {chat}", callback_data="admin_set_chat")],
            [InlineKeyboardButton(text="❌ Закрыть панель", callback_data="admin_close")]
        ]
    )

# --- HANDLERS ---

@dp.message(CommandStart())
async def start_handler(message: types.Message, command: CommandObject):
    user_id = message.from_user.id
    args = command.args
    
    referrer_id = int(args) if args and args.isdigit() and int(args) != user_id else None
    
    if not await get_user(user_id):
        await add_user(user_id, referrer_id)
    
    if not await check_subscription(user_id):
        sub_text = (
            "🔐 <b>Требуется верификация подписки</b>\n"
            "┌────────────────────────\n"
            "│ Для доступа к закрытой экосистеме\n"
            "│ необходимо подписаться на спонсорские каналы.\n"
            "└────────────────────────\n\n"
            "👇 <i>Выполните подписку и нажмите кнопку проверки:</i>"
        )
        try:
            await message.answer(sub_text, reply_markup=await get_sub_keyboard(), parse_mode=ParseMode.HTML)
        except TelegramForbiddenError:
            pass
        return

    first_name = html.escape(message.from_user.first_name)
    welcome_text = (
        f"🌌 <b>Добро пожаловать в Nexus Hub, {first_name}</b>\n"
        f"┌────────────────────────\n"
        f"│ ⚡ Статус: <code>Авторизован</code>\n"
        f"│ 📂 Доступ: <code>Активен</code>\n"
        f"└────────────────────────\n\n"
        f"💎 Эксклюзивные материалы, закрытые архивы и регулярные обновления уже жутдут вас в системе.\n\n"
        f"👇 <i>Выберите нужный инструмент в меню:</i>"
    )
    try:
        await message.answer(welcome_text, reply_markup=await get_main_keyboard(), parse_mode=ParseMode.HTML)
    except TelegramForbiddenError:
        pass

@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    
    if await check_subscription(user_id):
        user = await get_user(user_id)
        if user and user['referrer_id']:
            await add_point(user['referrer_id'])
            try:
                await bot.send_message(
                    user['referrer_id'], 
                    "🎉 <b>Реферальное уведомление:</b>\nВаш партнер успешно верифицирован в системе. Баланс пополнен на <b>+1 балл</b> ⭐",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
            
            if db_pool:
                async with db_pool.acquire() as conn:
                    await conn.execute("UPDATE users SET referrer_id = NULL WHERE user_id = $1", user_id)

        try:
            await callback.message.delete()
        except Exception:
            pass
            
        first_name = html.escape(callback.from_user.first_name)
        welcome_text = (
            f"🌌 <b>Добро пожаловать в Nexus Hub, {first_name}</b>\n"
            f"┌────────────────────────\n"
            f"│ ⚡ Статус: <code>Авторизован</code>\n"
            f"│ 📂 Доступ: <code>Активен</code>\n"
            f"└────────────────────────\n\n"
            f"💎 Эксклюзивные материалы, закрытые архивы и регулярные обновления уже ждут вас в системе.\n\n"
            f"👇 <i>Выберите нужный инструмент в меню:</i>"
        )
        await callback.message.answer(welcome_text, reply_markup=await get_main_keyboard(), parse_mode=ParseMode.HTML)
    else:
        await callback.answer("⚠️ Вы не выполнили все условия подписки!", show_alert=True)

@dp.callback_query(F.data == "profile")
async def profile_handler(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    user = await get_user(user_id)
    
    points = user['points'] if user else 0
    expire_str = user['expire_date'] if user else "Не активна"
    
    is_in_private = False
    try:
        member = await bot.get_chat_member(chat_id=PRIVATE_CHANNEL_ID, user_id=user_id)
        if member.status in ["creator", "administrator", "member"]:
            is_in_private = True
    except Exception:
        pass

    status_badge = "🟢 Активен" if is_in_private else "🔴 Неактивен"

    profile_text = (
        f"🪪 <b>Ваш персональный профиль</b>\n"
        f"┌────────────────────────\n"
        f"👤 ID: <code>{user_id}</code>\n"
        f"⭐ Баланс баллов: <b>{points} / {REQUIRED_REFERRALS}</b>\n"
        f"🔐 Доступ в VIP: <b>{status_badge}</b>\n"
        f"⏳ Срок действия: <code>{expire_str if is_in_private else 'Отсутствует'}</code>\n"
        f"└────────────────────────"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Вернуться в меню", callback_data="back_main")]])
    await callback.message.edit_text(profile_text, reply_markup=kb, parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "channels")
async def channels_handler(callback: CallbackQuery):
    await callback.answer()
    if not await check_subscription(callback.from_user.id):
        await callback.answer("⚠️ Требуется подписка!", show_alert=True)
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Открыть архивный канал", url=CHANNELS_SECTION_LINK)],
            [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")]
        ]
    )
    await callback.message.edit_text(
        "📂 <b>Премиум Архив Каналов</b>\n"
        "┌────────────────────────\n"
        "│ Все медиафайлы, подборки и каталоги\n"
        "│ структурированы в защищенном канале.\n"
        "└────────────────────────\n\n"
        "👇 <i>Нажмите для перехода:</i>",
        reply_markup=kb,
        parse_mode=ParseMode.HTML
    )

@dp.callback_query(F.data == "free_channel")
async def free_channel_handler(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    if not await check_subscription(user_id):
        await callback.answer("⚠️ Сначала подпишитесь на каналы!", show_alert=True)
        return

    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={user_id}"
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

    # Progress bar yaratish
    filled = min(points, REQUIRED_REFERRALS)
    empty = REQUIRED_REFERRALS - filled
    progress_bar = "▰" * filled + "▱" * empty

    text = (
        f"⚡ <b>VIP Доступ по реферальной системе</b>\n"
        f"┌────────────────────────\n"
        f"│ Приглашайте пользователей по ссылке,\n"
        f"│ собирайте баллы и активируйте доступ.\n"
        f"└────────────────────────\n\n"
        f"📊 Прогресс: [{progress_bar}] <b>{points}/{REQUIRED_REFERRALS}</b>\n\n"
        f"🔗 <b>Ваша персональная ссылка:</b>\n"
        f"<code>{ref_link}</code>\n\n"
    )
    kb_back = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")]])

    if is_in_private and expire_str:
        try:
            if datetime.now() < datetime.strptime(expire_str, "%Y-%m-%d %H:%M:%S"):
                text += f"🟢 <b>Статус:</b> VIP доступ активен до <b>{expire_str}</b>"
                await callback.message.edit_text(text, reply_markup=kb_back, parse_mode=ParseMode.HTML)
                return
        except Exception:
            pass

    if points >= REQUIRED_REFERRALS:
        expire_dt = datetime.now() + timedelta(days=SUB_DAYS)
        expire_date_str = expire_dt.strftime("%Y-%m-%d %H:%M:%S")
        
        if db_pool:
            async with db_pool.acquire() as conn:
                await conn.execute("UPDATE users SET expire_date = $1 WHERE user_id = $2", expire_date_str, user_id)

        try:
            invite_link = await bot.create_chat_invite_link(
                chat_id=PRIVATE_CHANNEL_ID,
                member_limit=1,
                expire_date=datetime.now() + timedelta(minutes=10)
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔓 Войти в закрытый канал", url=invite_link.invite_link)],
                [InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")]
            ])
            text += f"🎉 <b>Цель достигнута!</b> Вам открыт доступ на <b>{SUB_DAYS} дней</b> (до {expire_date_str})."
        except Exception:
            text += "⚠️ Ошибка генерации ссылки. Обратитесь к администратору."
            kb = kb_back
    else:
        needed = REQUIRED_REFERRALS - points
        text += f"💡 Осталось пригласить: <b>{needed} пользователей</b>."
        kb = kb_back

    await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "help")
async def help_handler(callback: CallbackQuery):
    await callback.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")]])
    help_text = (
        "📖 <b>Справочный центр бота</b>\n"
        "┌────────────────────────\n"
        "│ • <b>Архив</b> — каталог всех доступных ресурсов.\n"
        "│ • <b>VIP Доступ</b> — бесплатное получение подписки через инвайты.\n"
        "└────────────────────────\n\n"
        "💬 Возникли проблемы? Напишите нашему администратору через главное меню."
    )
    await callback.message.edit_text(help_text, reply_markup=kb, parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "back_main")
async def back_main_handler(callback: CallbackQuery):
    await callback.answer()
    first_name = html.escape(callback.from_user.first_name)
    welcome_text = (
        f"🌌 <b>Добро пожаловать в Nexus Hub, {first_name}</b>\n"
        f"┌────────────────────────\n"
        f"│ ⚡ Статус: <code>Авторизован</code>\n"
        f"│ 📂 Доступ: <code>Активен</code>\n"
        f"└────────────────────────\n\n"
        f"💎 Эксклюзивные материалы, закрытые архивы и регулярные обновления уже ждут вас в системе.\n\n"
        f"👇 <i>Выберите нужный инструмент в меню:</i>"
    )
    await callback.message.edit_text(welcome_text, reply_markup=await get_main_keyboard(), parse_mode=ParseMode.HTML)

# ==================== ADMIN PANEL ====================
@dp.message(Command("admin"))
async def admin_start(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("👑 <b>Панель администратора (Nexus Control)</b>", reply_markup=await get_admin_keyboard(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "admin_stats")
async def admin_stats_handler(callback: CallbackQuery):
    await callback.answer()
    if callback.from_user.id != ADMIN_ID:
        return
    await callback.answer("⏳ Сканирование базы данных...", show_alert=False)
    
    total_users, total_points = await get_stats()
    users = await get_all_users()
    active = 0
    for uid in users:
        try:
            await bot.send_chat_action(chat_id=uid, action="typing")
            active += 1
        except Exception:
            pass

    text = (
        f"📊 <b>Аналитический отчет:</b>\n"
        f"┌────────────────────────\n"
        f"👤 Всего пользователей: <b>{total_users}</b>\n"
        f"🟢 Активных в сети: <b>{active}</b>\n"
        f"🔴 Заблокировали бота: <b>{total_users - active}</b>\n"
        f"⭐ Суммарно баллов: <b>{total_points}</b>\n"
        f"└────────────────────────"
    )
    await callback.message.edit_text(text, reply_markup=await get_admin_keyboard(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "admin_set_ch1")
async def admin_set_ch1(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
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

@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_handler(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    await state.set_state(AdminStates.waiting_for_broadcast)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel_settings")]])
    await callback.message.edit_text("📢 Отправьте пост для массовой рассылки:", reply_markup=kb)

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
        except Exception:
            failed += 1
            
    await message.answer(f"✅ <b>Рассылка завершена!</b>\n\n🎉 Успешно: {success}\n❌ Ошибок: {failed}", reply_markup=await get_admin_keyboard(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "admin_cancel_settings")
async def cancel_settings(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("👑 <b>Панель администратора</b>", reply_markup=await get_admin_keyboard(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "admin_close")
async def admin_close(callback: CallbackQuery):
    await callback.message.delete()

# --- BACKGROUND TASKS ---
async def auto_kick_expired_users():
    while True:
        try:
            if db_pool:
                async with db_pool.acquire() as conn:
                    now = datetime.now()
                    users = await conn.fetch("SELECT user_id, expire_date FROM users WHERE expire_date IS NOT NULL AND expire_date != ''")

                    for r in users:
                        u_id = r['user_id']
                        try:
                            expire_dt = datetime.strptime(r['expire_date'], "%Y-%m-%d %H:%M:%S")
                            if now >= expire_dt:
                                try:
                                    member = await bot.get_chat_member(chat_id=PRIVATE_CHANNEL_ID, user_id=u_id)
                                    if member.status in ["member", "restricted"]:
                                        await bot.ban_chat_member(chat_id=PRIVATE_CHANNEL_ID, user_id=u_id)
                                        await bot.unban_chat_member(chat_id=PRIVATE_CHANNEL_ID, user_id=u_id)
                                        try:
                                            await bot.send_message(
                                                u_id,
                                                "⏰ <b>Срок действия вашей подписки истек!</b>\nВы были автоматически исключены из закрытого канала.",
                                                parse_mode=ParseMode.HTML
                                            )
                                        except Exception:
                                            pass
                                except Exception:
                                    pass
                                await conn.execute("UPDATE users SET expire_date = NULL, points = 0 WHERE user_id = $1", u_id)
                        except Exception:
                            pass
        except Exception:
            pass
        await asyncio.sleep(60)

async def on_startup(app):
    await init_db_pool()
    asyncio.create_task(auto_kick_expired_users())
    if RENDER_EXTERNAL_URL:
        webhook_url = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}"
        await bot.set_webhook(webhook_url, drop_pending_updates=True)

def main():
    app = web.Application()
    app.router.add_get("/", lambda req: web.Response(text="Nexus Bot Active", status=200))

    webhook_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_handler.register(app, path=WEBHOOK_PATH)

    setup_application(app, dp, bot=bot)
    app.on_startup.append(on_startup)

    web.run_app(app, host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    main()
