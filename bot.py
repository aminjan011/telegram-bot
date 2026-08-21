import os
import logging
import psycopg2
from psycopg2 import pool
import html
import asyncio
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, CommandObject, Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from aiogram.enums import ParseMode
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

logging.basicConfig(level=logging.INFO)

# ==================== SOZLAMA (SETTINGS) ====================
BOT_TOKEN = "8932013152:AAHm6khUTUG4DexDCxrRXoxLyFP7sxAAZJ8"
PRIVATE_CHANNEL_ID = -1004324882879
CHANNELS_SECTION_LINK = "https://t.me/+_AxorsmPVYE2M2Ji"
REQUIRED_REFERRALS = 10
SUB_DAYS = 10
ADMIN_USERNAME = "softic00"
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

# --- DATABASE CONNECTION POOL ---
db_pool = None
if DATABASE_URL:
    try:
        db_pool = psycopg2.pool.SimpleConnectionPool(1, 20, DATABASE_URL, sslmode='require')
    except Exception as e:
        logging.error(f"Pool yaratishda xato: {e}")

def get_db():
    if db_pool:
        return db_pool.getconn()
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def release_db(conn):
    if db_pool and conn:
        db_pool.putconn(conn)
    elif conn:
        conn.close()

def init_db():
    if not DATABASE_URL:
        logging.error("DATABASE_URL topilmadi!")
        return
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                referrer_id BIGINT,
                points INT DEFAULT 0,
                has_access INT DEFAULT 0,
                expire_date TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        cursor.execute("INSERT INTO settings (key, value) VALUES ('channel_1', '@kinozhuldyzkz') ON CONFLICT (key) DO NOTHING")
        cursor.execute("INSERT INTO settings (key, value) VALUES ('channel_2', '') ON CONFLICT (key) DO NOTHING")
        conn.commit()
        cursor.close()
    finally:
        release_db(conn)

init_db()

def get_setting(key: str) -> str:
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = %s", (key,))
        row = cursor.fetchone()
        cursor.close()
        return row[0] if row else ""
    finally:
        release_db(conn)

def set_setting(key: str, value: str):
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (key, value))
        conn.commit()
        cursor.close()
    finally:
        release_db(conn)

def get_user(user_id: int):
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, referrer_id, points, has_access, expire_date FROM users WHERE user_id = %s", (user_id,))
        row = cursor.fetchone()
        cursor.close()
        return row
    finally:
        release_db(conn)

def add_user(user_id: int, referrer_id: int = None):
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO users (user_id, referrer_id) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING", (user_id, referrer_id))
        conn.commit()
        cursor.close()
    finally:
        release_db(conn)

def add_point(user_id: int):
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET points = points + 1 WHERE user_id = %s", (user_id,))
        conn.commit()
        cursor.close()
    finally:
        release_db(conn)

# ==================== TAKOMILLASHTIRILGAN STATISTIKA ====================
def get_stats():
    conn = get_db()
    try:
        cursor = conn.cursor()
        
        # 1. Umumiy va ballari bor foydalanuvchilar
        cursor.execute("SELECT COUNT(*), COALESCE(SUM(points), 0), COALESCE(AVG(points), 0) FROM users")
        total_users, total_points, avg_points = cursor.fetchone()
        
        # 2. Kamida 1 ta ball to'plagan faol taklifchilar
        cursor.execute("SELECT COUNT(*) FROM users WHERE points > 0")
        active_referrers = cursor.fetchone()[0]
        
        # 3. Bugun va oxirgi 7 kunda qo'shilganlar
        cursor.execute("SELECT COUNT(*) FROM users WHERE created_at >= CURRENT_DATE")
        today_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM users WHERE created_at >= CURRENT_DATE - INTERVAL '7 days'")
        week_users = cursor.fetchone()[0]
        
        # 4. Hozirda faol obunaga (yopiq kanalga kirishga) ega bo'lganlar
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("SELECT COUNT(*) FROM users WHERE expire_date IS NOT NULL AND expire_date > %s", (now_str,))
        active_subscribers = cursor.fetchone()[0]

        cursor.close()
        return {
            "total_users": total_users or 0,
            "total_points": total_points or 0,
            "avg_points": round(avg_points or 0, 1),
            "active_referrers": active_referrers or 0,
            "today_users": today_users or 0,
            "week_users": week_users or 0,
            "active_subscribers": active_subscribers or 0
        }
    finally:
        release_db(conn)

def get_all_users():
    conn = get_db()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM users")
        rows = cursor.fetchall()
        cursor.close()
        return [r[0] for r in rows]
    finally:
        release_db(conn)

async def check_subscription(user_id: int) -> bool:
    ch1 = get_setting('channel_1')
    ch2 = get_setting('channel_2')
    
    channels_to_check = [c for c in [ch1, ch2] if c.strip()]
    
    for ch in channels_to_check:
        try:
            member = await bot.get_chat_member(chat_id=ch, user_id=user_id)
            if member.status not in ["creator", "administrator", "member"]:
                return False
        except Exception as e:
            logging.error(f"Ошибка проверки подписки {ch}: {e}")
            return False
    return True

def get_main_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📁 Каналы", callback_data="channels")],
            [InlineKeyboardButton(text="⚡ Бесплатный канал", callback_data="free_channel")],
            [InlineKeyboardButton(text="🤖 Помощник", callback_data="help")],
            [InlineKeyboardButton(text="📝 Написать администратору", url=f"https://t.me/{ADMIN_USERNAME}")]
        ]
    )

def get_sub_keyboard():
    ch1 = get_setting('channel_1')
    ch2 = get_setting('channel_2')
    
    buttons = []
    if ch1.strip():
        clean1 = ch1.replace("@", "")
        buttons.append([InlineKeyboardButton(text="📢 Канал 1", url=f"https://t.me/{clean1}")])
    if ch2.strip():
        clean2 = ch2.replace("@", "")
        buttons.append([InlineKeyboardButton(text="📢 Канал 2", url=f"https://t.me/{clean2}")])
        
    buttons.append([InlineKeyboardButton(text="🔄 Проверить подписку", callback_data="check_sub")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_admin_keyboard():
    ch1 = get_setting('channel_1') or "Не настроен"
    ch2 = get_setting('channel_2') or "Не настроен"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
            [InlineKeyboardButton(text=f"⚙️ Канал 1: {ch1}", callback_data="admin_set_ch1")],
            [InlineKeyboardButton(text=f"⚙️ Канал 2: {ch2}", callback_data="admin_set_ch2")],
            [InlineKeyboardButton(text="❌ Закрыть", callback_data="admin_close")]
        ]
    )

@dp.message(CommandStart())
async def start_handler(message: types.Message, command: CommandObject):
    user_id = message.from_user.id
    args = command.args
    
    referrer_id = None
    if args and args.isdigit():
        ref_candidate = int(args)
        if ref_candidate != user_id:
            referrer_id = ref_candidate

    user = get_user(user_id)
    if not user:
        add_user(user_id, referrer_id)
    
    is_sub = await check_subscription(user_id)
    if not is_sub:
        sub_text = "⚠️ <b>Для использования бота необходимо подписаться на наши каналы!</b>\n\nПосле подписки нажмите кнопку «Проверить подписку»."
        await message.answer(sub_text, reply_markup=get_sub_keyboard(), parse_mode=ParseMode.HTML)
        return

    first_name = html.escape(message.from_user.first_name)
    welcome_text = f"💥 <b>Добро пожаловать, {first_name}!</b>\n‹━━━━━━━━━━━━━━━━━━›\n\n🔥 Приватный архив 18+\n— эксклюзивный контент\n— доступ только для участников\n\n👇 <b>Выбери раздел</b> 👇"
    await message.answer(welcome_text, reply_markup=get_main_keyboard(), parse_mode=ParseMode.HTML)

# ==================== ADMIN PANEL ====================
@dp.message(Command("admin"))
async def admin_start(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    await message.answer("👑 <b>Панель администратора</b>", reply_markup=get_admin_keyboard(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "admin_stats")
async def admin_stats_handler(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    stats = get_stats()
    text = (
        f"📊 <b>Расширенная статистика бота:</b>\n\n"
        f"👤 Всего пользователей: <b>{stats['total_users']}</b>\n"
        f"☀️ Новых за сегодня: <b>+{stats['today_users']}</b>\n"
        f"📅 Новых за 7 дней: <b>+{stats['week_users']}</b>\n\n"
        f"⭐ Всего набрано баллов: <b>{stats['total_points']}</b>\n"
        f"📈 В среднем баллов у юзера: <b>{stats['avg_points']}</b>\n"
        f"👥 Рефералов привели: <b>{stats['active_referrers']} чел.</b>\n\n"
        f"🔓 Активных подписок в привате: <b>{stats['active_subscribers']}</b>"
    )
    await callback.message.edit_text(text, reply_markup=get_admin_keyboard(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "admin_set_ch1")
async def admin_set_ch1(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    await state.set_state(AdminStates.waiting_for_ch1)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel_settings")]])
    await callback.message.edit_text("✏️ Отправьте username первого обязательного канала (например: <code>@mychannel</code>):", reply_markup=kb, parse_mode=ParseMode.HTML)

@dp.message(AdminStates.waiting_for_ch1)
async def process_ch1(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    channel_username = message.text.strip()
    if not channel_username.startswith("@"):
        channel_username = "@" + channel_username
    set_setting("channel_1", channel_username)
    await state.clear()
    await message.answer(f"✅ Канал 1 успешно обновлен: <b>{channel_username}</b>", reply_markup=get_admin_keyboard(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "admin_set_ch2")
async def admin_set_ch2(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    await state.set_state(AdminStates.waiting_for_ch2)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Удалить 2-канал", callback_data="admin_remove_ch2")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel_settings")]
        ]
    )
    await callback.message.edit_text("✏️ Отправьте username второго обязательного канала (например: <code>@mychannel2</code>) или нажмите «Удалить»:", reply_markup=kb, parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "admin_remove_ch2")
async def admin_remove_ch2(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    set_setting("channel_2", "")
    await state.clear()
    await callback.message.edit_text("✅ Второй обязательный канал успешно удален!", reply_markup=get_admin_keyboard())

@dp.message(AdminStates.waiting_for_ch2)
async def process_ch2(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    channel_username = message.text.strip()
    if not channel_username.startswith("@"):
        channel_username = "@" + channel_username
    set_setting("channel_2", channel_username)
    await state.clear()
    await message.answer(f"✅ Канал 2 успешно добавлен/обновлен: <b>{channel_username}</b>", reply_markup=get_admin_keyboard(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "admin_cancel_settings")
async def admin_cancel_settings(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("👑 <b>Панель администратора</b>", reply_markup=get_admin_keyboard(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_handler(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    await state.set_state(AdminStates.waiting_for_broadcast)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel_settings")]])
    await callback.message.edit_text("📢 Отправьте сообщение, которое будет разослано всем пользователям:", reply_markup=kb)

@dp.message(AdminStates.waiting_for_broadcast)
async def process_broadcast(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    await state.clear()
    
    users = get_all_users()
    await message.answer(f"⏳ Начинаем рассылку для {len(users)} пользователей...")
    
    success = 0
    failed = 0
    
    for uid in users:
        try:
            await message.copy_to(chat_id=uid)
            success += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
            
    await message.answer(
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"🎉 Успешно отправлено: <b>{success}</b>\n"
        f"❌ Не доставлено: <b>{failed}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=get_admin_keyboard()
    )

@dp.callback_query(F.data == "admin_close")
async def admin_close_handler(callback: CallbackQuery):
    await callback.message.delete()

# ======================================================

@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(callback: CallbackQuery):
    user_id = callback.from_user.id
    is_sub = await check_subscription(user_id)
    
    if is_sub:
        user = get_user(user_id)
        if user and user[1]:
            add_point(user[1])
            try:
                await bot.send_message(
                    user[1], 
                    "🎉 Пользователь, которого вы пригласили, подписался на канал! Вам начислен <b>+1 балл</b>.",
                    parse_mode=ParseMode.HTML
                )
            except Exception:
                pass
            
            conn = get_db()
            try:
                cursor = conn.cursor()
                cursor.execute("UPDATE users SET referrer_id = NULL WHERE user_id = %s", (user_id,))
                conn.commit()
                cursor.close()
            finally:
                release_db(conn)

        await callback.message.delete()
        first_name = html.escape(callback.from_user.first_name)
        welcome_text = f"💥 <b>Добро пожаловать, {first_name}!</b>\n‹━━━━━━━━━━━━━━›\n\n🔥 Приватный архив 18+\n— эксклюзивный контент\n— доступ только для участников\n\n👇 <b>Выбери раздел</b> 👇"
        await callback.message.answer(welcome_text, reply_markup=get_main_keyboard(), parse_mode=ParseMode.HTML)
    else:
        await callback.answer("❌ Вы еще не подписались на все каналы!", show_alert=True)

@dp.callback_query(F.data == "channels")
async def channels_handler(callback: CallbackQuery):
    is_sub = await check_subscription(callback.from_user.id)
    if not is_sub:
        await callback.answer("⚠️ Сначала подпишитесь на все обязательные каналы!", show_alert=True)
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📢 Перейти в канал", url=CHANNELS_SECTION_LINK)],
            [InlineKeyboardButton(text="👈 Назад", callback_data="back_main")]
        ]
    )
    await callback.message.edit_text("👇 Вы можете перейти в наш приватный канал по кнопке ниже:", reply_markup=kb)

@dp.callback_query(F.data == "free_channel")
async def free_channel_handler(callback: CallbackQuery):
    user_id = callback.from_user.id
    is_sub = await check_subscription(user_id)
    if not is_sub:
        await callback.answer("⚠️ Сначала подпишитесь на все обязательные каналы!", show_alert=True)
        return

    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={user_id}"
    
    user = get_user(user_id)
    points = user[2] if user else 0
    expire_str = user[4] if user else None

    # Foydalanuvchi hozirda yopiq kanalda bor-yo'qligini tekshirish
    is_in_private_channel = False
    try:
        member = await bot.get_chat_member(chat_id=PRIVATE_CHANNEL_ID, user_id=user_id)
        if member.status in ["creator", "administrator", "member"]:
            is_in_private_channel = True
    except Exception:
        is_in_private_channel = False

    text = (
        f"⚡ <b>Бесплатный канал (Реферальная система)</b>\n\n"
        f"Приглашайте друзей и накапливайте баллы, чтобы получить доступ к закрытому каналу!\n"
        f"💡 <i>За каждого приглашенного друга, подписавшегося на канал, вы получаете +1 балл.</i>\n\n"
        f"👤 Ваши баллы: <b>{points} / {REQUIRED_REFERRALS}</b>\n"
        f"🔗 Ваша пригласительная ссылка:\n<code>{ref_link}</code>\n\n"
    )

    kb_back = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="👈 Назад", callback_data="back_main")]])

    # 1-HOLAT: Foydalanuvchi allaqachon kanalda bor va muddati tugamagan
    if is_in_private_channel and expire_str:
        try:
            exp_dt = datetime.strptime(expire_str, "%Y-%m-%d %H:%M:%S")
            if datetime.now() < exp_dt:
                text += (
                    f"✅ <b>Вы уже состоите в закрытом канале!</b>\n\n"
                    f"⏳ Срок действия вашего текущего доступа: до <b>{expire_str}</b>.\n"
                    f"По истечении этого времени вы сможете активировать доступ снова."
                )
                await callback.message.edit_text(text, reply_markup=kb_back, parse_mode=ParseMode.HTML)
                return
        except Exception:
            pass

    # 2-HOLAT: 10 ball yig'ilgan va kanalda yo'q (yoki muddati tugagan)
    if points >= REQUIRED_REFERRALS:
        expire_dt = datetime.now() + timedelta(days=SUB_DAYS)
        expire_date_str = expire_dt.strftime("%Y-%m-%d %H:%M:%S")
        
        conn = get_db()
        try:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET expire_date = %s WHERE user_id = %s", (expire_date_str, user_id))
            conn.commit()
            cursor.close()
        finally:
            release_db(conn)

        try:
            expire_time = datetime.now() + timedelta(minutes=10)
            invite_link = await bot.create_chat_invite_link(
                chat_id=PRIVATE_CHANNEL_ID,
                member_limit=1,
                expire_date=expire_time
            )

            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🔓 Войти в канал (Временная ссылка)", url=invite_link.invite_link)],
                    [InlineKeyboardButton(text="👈 Назад", callback_data="back_main")]
                ]
            )
            text += (
                f"🎉 <b>Вы успешно собрали 10 баллов!</b>\n\n"
                f"⚠️ <b>ВНИМАНИЕ:</b> Вам предоставляется доступ к закрытому каналу ровно на <b>{SUB_DAYS} дней</b>!\n"
                f"⏳ Срок действия доступа: до <b>{expire_date_str}</b>.\n"
                f"По истечении {SUB_DAYS} дней система автоматически исключит вас из канала.\n\n"
                f"<i>Ссылка ниже одноразовая и действительна в течение 10 минут только для 1 человека!</i>"
            )
        except Exception as e:
            logging.error(f"Ошибка создания ссылки: {e}")
            text += "⚠️ Произошла ошибка при создании ссылки. Убедитесь, что бот является администратором закрытого канала с правом приглашения пользователей."
            kb = kb_back
    else:
        text += f"💡 Для получения доступа вам осталось набрать ещё <b>{REQUIRED_REFERRALS - points}</b> баллов."
        kb = kb_back

    await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "help")
async def help_handler(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👈 Назад", callback_data="back_main")]
        ]
    )
    help_text = "🤖 <b>Помощник / Информация</b>\n\n1. <b>Каналы</b> — Список наших основных ресурсов.\n2. <b>Бесплатный канал</b> — Приглашайте друзей по своей ссылке, копите баллы и получайте бесплатный доступ к закрытому каналу!\n\nЕсли у вас возникли вопросы, свяжитесь с администратором."
    await callback.message.edit_text(help_text, reply_markup=kb, parse_mode=ParseMode.HTML)

@dp.callback_query(F.data == "back_main")
async def back_main_handler(callback: CallbackQuery):
    first_name = html.escape(callback.from_user.first_name)
    welcome_text = f"💥 <b>Добро пожаловать, {first_name}!</b>\n‹━━━━━━━━━━━━━━━━›\n\n🔥 Приватный архив 18+\n— эксклюзивный контент\n— доступ только для участников\n\n👇 <b>Выбери раздел</b> 👇"
    await callback.message.edit_text(welcome_text, reply_markup=get_main_keyboard(), parse_mode=ParseMode.HTML)

# --- AVTOMATIK KANALO'DAN CHIQARISH (XAVFSIZ TEKSHIRUV) ---
async def auto_kick_expired_users():
    while True:
        try:
            conn = get_db()
            try:
                cursor = conn.cursor()
                now = datetime.now()
                
                cursor.execute("SELECT user_id, expire_date FROM users WHERE expire_date IS NOT NULL AND expire_date != ''")
                users = cursor.fetchall()

                for u_id, expire_str in users:
                    try:
                        expire_dt = datetime.strptime(expire_str, "%Y-%m-%d %H:%M:%S")
                        if now >= expire_dt:
                            # Telegram'da haqiqatan ham kanalda bormi, tekshiramiz
                            try:
                                member = await bot.get_chat_member(chat_id=PRIVATE_CHANNEL_ID, user_id=u_id)
                                if member.status in ["member", "restricted"]:
                                    await bot.ban_chat_member(chat_id=PRIVATE_CHANNEL_ID, user_id=u_id)
                                    await bot.unban_chat_member(chat_id=PRIVATE_CHANNEL_ID, user_id=u_id)
                                    await bot.send_message(
                                        u_id,
                                        f"⏰ <b>Срок вашей бесплатной подписки ({SUB_DAYS} дней) истек!</b>\n\n"
                                        f"Вы были автоматически исключены из закрытого канала. "
                                        f"Чтобы войти снова, вам необходимо повторно набрать {REQUIRED_REFERRALS} баллов.",
                                        parse_mode=ParseMode.HTML
                                    )
                                    logging.info(f"Foydalanuvchi {u_id} kanaldan chiqarildi (muddati tugagan).")
                            except Exception as e:
                                logging.error(f"Kanal a'zosini kick qilishda xato ({u_id}): {e}")

                            # Muddati tugagach, bazani tozalaymiz
                            cursor.execute("UPDATE users SET expire_date = NULL, points = 0 WHERE user_id = %s", (u_id,))
                            conn.commit()
                    except Exception as ex:
                        logging.error(f"Sana parse qilishda xato: {ex}")

                cursor.close()
            finally:
                release_db(conn)
        except Exception as e:
            logging.error(f"Ошибка в auto_kick_expired_users: {e}")

        await asyncio.sleep(60)

# --- WEBHOOK ISHGA TUSHMASI ---
async def on_startup(app):
    asyncio.create_task(auto_kick_expired_users())
    if RENDER_EXTERNAL_URL:
        webhook_url = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}"
        await bot.set_webhook(webhook_url)
        logging.info(f"Webhook o'rnatildi: {webhook_url}")
    else:
        logging.warning("RENDER_EXTERNAL_URL topilmadi. Webhook o'rnatilmadi.")

def main():
    app = web.Application()
    
    async def health_check(request):
        return web.Response(text="Bot ishlayapti!", status=200)

    app.router.add_get("/", health_check)

    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
    )
    webhook_requests_handler.register(app, path=WEBHOOK_PATH)

    setup_application(app, dp, bot=bot)
    app.on_startup.append(on_startup)

    web.run_app(app, host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    main()
