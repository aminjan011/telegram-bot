import os
import logging
import sqlite3
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

# ==================== НАСТРОЙКИ ====================
BOT_TOKEN = "8932013152:AAHm6khUTUG4DexDCxrRXoxLyFP7sxAAZJ8"      # Tokeningiz[span_0](start_span)[span_0](end_span)
PRIVATE_CHANNEL_ID = -1004324882879                    # Referal orqali kiriladigan yopiq kanal ID[span_1](start_span)[span_1](end_span)
CHANNELS_SECTION_LINK = "https://t.me/+_AxorsmPVYE2M2Ji"    # "📁 Каналы" bo'limi uchun havola[span_2](start_span)[span_2](end_span)
REQUIRED_REFERRALS = 10                               # Referal soni (10 ball)[span_3](start_span)[span_3](end_span)
SUB_DAYS = 10                                         # Podpiska muddati (10 kun)[span_4](start_span)[span_4](end_span)
ADMIN_USERNAME = "softic00"                    # Admin username (sans @)[span_5](start_span)[span_5](end_span)
ADMIN_ID = 1112793157                                  # Admin Telegram ID raqami[span_6](start_span)[span_6](end_span)

# Webhook sozlamalari (Render uchun)
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")  # Render avtomatik taqdim etadigan URL
PORT = int(os.getenv("PORT", 8080))
# ===================================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

class AdminStates(StatesGroup):
    waiting_for_broadcast = State()[span_7](start_span)[span_7](end_span)
    waiting_for_ch1 = State()[span_8](start_span)[span_8](end_span)
    waiting_for_ch2 = State()[span_9](start_span)[span_9](end_span)

def init_db():
    conn = sqlite3.connect("bot_database.db")[span_10](start_span)[span_10](end_span)
    cursor = conn.cursor()[span_11](start_span)[span_11](end_span)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            referrer_id INTEGER,
            points INTEGER DEFAULT 0,
            has_access INTEGER DEFAULT 0,
            expire_date TEXT
        )
    ''')[span_12](start_span)[span_12](end_span)
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN expire_date TEXT")[span_13](start_span)[span_13](end_span)
    except sqlite3.OperationalError:[span_14](start_span)[span_14](end_span)
        pass[span_15](start_span)[span_15](end_span)

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')[span_16](start_span)[span_16](end_span)
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('channel_1', '@kinozhuldyzkz')")[span_17](start_span)[span_17](end_span)
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('channel_2', '')")[span_18](start_span)[span_18](end_span)
    conn.commit()[span_19](start_span)[span_19](end_span)
    conn.close()[span_20](start_span)[span_20](end_span)

init_db()[span_21](start_span)[span_21](end_span)

def get_setting(key: str) -> str:
    conn = sqlite3.connect("bot_database.db")[span_22](start_span)[span_22](end_span)
    cursor = conn.cursor()[span_23](start_span)[span_23](end_span)
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))[span_24](start_span)[span_24](end_span)
    row = cursor.fetchone()[span_25](start_span)[span_25](end_span)
    conn.close()[span_26](start_span)[span_26](end_span)
    return row[0] if row else "[span_27](start_span)"[span_27](end_span)

def set_setting(key: str, value: str):
    conn = sqlite3.connect("bot_database.db")[span_28](start_span)[span_28](end_span)
    cursor = conn.cursor()[span_29](start_span)[span_29](end_span)
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))[span_30](start_span)[span_30](end_span)
    conn.commit()[span_31](start_span)[span_31](end_span)
    conn.close()[span_32](start_span)[span_32](end_span)

def get_user(user_id: int):
    conn = sqlite3.connect("bot_database.db")[span_33](start_span)[span_33](end_span)
    cursor = conn.cursor()[span_34](start_span)[span_34](end_span)
    cursor.execute("SELECT user_id, referrer_id, points, has_access, expire_date FROM users WHERE user_id = ?", (user_id,))[span_35](start_span)[span_35](end_span)
    row = cursor.fetchone()[span_36](start_span)[span_36](end_span)
    conn.close()[span_37](start_span)[span_37](end_span)
    return row[span_38](start_span)[span_38](end_span)

def add_user(user_id: int, referrer_id: int = None):
    conn = sqlite3.connect("bot_database.db")[span_39](start_span)[span_39](end_span)
    cursor = conn.cursor()[span_40](start_span)[span_40](end_span)
    cursor.execute("INSERT OR IGNORE INTO users (user_id, referrer_id) VALUES (?, ?)", (user_id, referrer_id))[span_41](start_span)[span_41](end_span)
    conn.commit()[span_42](start_span)[span_42](end_span)
    conn.close()[span_43](start_span)[span_43](end_span)

def add_point(user_id: int):
    conn = sqlite3.connect("bot_database.db")[span_44](start_span)[span_44](end_span)
    cursor = conn.cursor()[span_45](start_span)[span_45](end_span)
    cursor.execute("UPDATE users SET points = points + 1 WHERE user_id = ?", (user_id,))[span_46](start_span)[span_46](end_span)
    conn.commit()[span_47](start_span)[span_47](end_span)
    conn.close()[span_48](start_span)[span_48](end_span)

def get_stats():
    conn = sqlite3.connect("bot_database.db")[span_49](start_span)[span_49](end_span)
    cursor = conn.cursor()[span_50](start_span)[span_50](end_span)
    cursor.execute("SELECT COUNT(*), SUM(points) FROM users")[span_51](start_span)[span_51](end_span)
    stats = cursor.fetchone()[span_52](start_span)[span_52](end_span)
    conn.close()[span_53](start_span)[span_53](end_span)
    total_users = stats[0] if stats[0] else 0[span_54](start_span)[span_54](end_span)
    total_points = stats[1] if stats[1] else 0[span_55](start_span)[span_55](end_span)
    return total_users, total_points[span_56](start_span)[span_56](end_span)

def get_all_users():
    conn = sqlite3.connect("bot_database.db")[span_57](start_span)[span_57](end_span)
    cursor = conn.cursor()[span_58](start_span)[span_58](end_span)
    cursor.execute("SELECT user_id FROM users")[span_59](start_span)[span_59](end_span)
    rows = cursor.fetchall()[span_60](start_span)[span_60](end_span)
    conn.close()[span_61](start_span)[span_61](end_span)
    return [r[0] for r in rows][span_62](start_span)[span_62](end_span)

async def check_subscription(user_id: int) -> bool:
    ch1 = get_setting('channel_1')[span_63](start_span)[span_63](end_span)
    ch2 = get_setting('channel_2')[span_64](start_span)[span_64](end_span)
    
    channels_to_check = [c for c in [ch1, ch2] if c.strip()][span_65](start_span)[span_65](end_span)
    
    for ch in channels_to_check:[span_66](start_span)[span_66](end_span)
        try:
            member = await bot.get_chat_member(chat_id=ch, user_id=user_id)[span_67](start_span)[span_67](end_span)
            if member.status not in ["creator", "administrator", "member"]:[span_68](start_span)[span_68](end_span)
                return False[span_69](start_span)[span_69](end_span)
        except Exception as e:[span_70](start_span)[span_70](end_span)
            logging.error(f"Ошибка проверки подписки {ch}: {e}")[span_71](start_span)[span_71](end_span)
            return False[span_72](start_span)[span_72](end_span)
    return True[span_73](start_span)[span_73](end_span)

def get_main_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📁 Каналы", callback_data="channels")],[span_74](start_span)[span_74](end_span)
            [InlineKeyboardButton(text="⚡ Бесплатный канал", callback_data="free_channel")],[span_75](start_span)[span_75](end_span)
            [InlineKeyboardButton(text="🤖 Помощник", callback_data="help")],[span_76](start_span)[span_76](end_span)
            [InlineKeyboardButton(text="📝 Написать администратору", url=f"https://t.me/{ADMIN_USERNAME}")][span_77](start_span)[span_77](end_span)
        ]
    )

def get_sub_keyboard():
    ch1 = get_setting('channel_1')[span_78](start_span)[span_78](end_span)
    ch2 = get_setting('channel_2')[span_79](start_span)[span_79](end_span)
    
    buttons = [][span_80](start_span)[span_80](end_span)
    if ch1.strip():[span_81](start_span)[span_81](end_span)
        clean1 = ch1.replace("@", "")[span_82](start_span)[span_82](end_span)
        buttons.append([InlineKeyboardButton(text="📢 Канал 1", url=f"https://t.me/{clean1}")])[span_83](start_span)[span_83](end_span)
    if ch2.strip():[span_84](start_span)[span_84](end_span)
        clean2 = ch2.replace("@", "")[span_85](start_span)[span_85](end_span)
        buttons.append([InlineKeyboardButton(text="📢 Канал 2", url=f"https://t.me/{clean2}")])[span_86](start_span)[span_86](end_span)
        
    buttons.append([InlineKeyboardButton(text="🔄 Проверить подписку", callback_data="check_sub")])[span_87](start_span)[span_87](end_span)
    return InlineKeyboardMarkup(inline_keyboard=buttons)[span_88](start_span)[span_88](end_span)

def get_admin_keyboard():
    ch1 = get_setting('channel_1') or "Не настроен[span_89](start_span)"[span_89](end_span)
    ch2 = get_setting('channel_2') or "Не настроен[span_90](start_span)"[span_90](end_span)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],[span_91](start_span)[span_91](end_span)
            [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],[span_92](start_span)[span_92](end_span)
            [InlineKeyboardButton(text=f"⚙️ Канал 1: {ch1}", callback_data="admin_set_ch1")],[span_93](start_span)[span_93](end_span)
            [InlineKeyboardButton(text=f"⚙️ Канал 2: {ch2}", callback_data="admin_set_ch2")],[span_94](start_span)[span_94](end_span)
            [InlineKeyboardButton(text="❌ Закрыть", callback_data="admin_close")][span_95](start_span)[span_95](end_span)
        ]
    )

@dp.message(CommandStart())
async def start_handler(message: types.Message, command: CommandObject):
    user_id = message.from_user.id[span_96](start_span)[span_96](end_span)
    args = command.args[span_97](start_span)[span_97](end_span)
    
    referrer_id = None[span_98](start_span)[span_98](end_span)
    if args and args.isdigit():[span_99](start_span)[span_99](end_span)
        ref_candidate = int(args)[span_100](start_span)[span_100](end_span)
        if ref_candidate != user_id:[span_101](start_span)[span_101](end_span)
            referrer_id = ref_candidate[span_102](start_span)[span_102](end_span)

    user = get_user(user_id)[span_103](start_span)[span_103](end_span)
    if not user:[span_104](start_span)[span_104](end_span)
        add_user(user_id, referrer_id)[span_105](start_span)[span_105](end_span)
    
    is_sub = await check_subscription(user_id)[span_106](start_span)[span_106](end_span)
    if not is_sub:[span_107](start_span)[span_107](end_span)
        sub_text = "⚠️ <b>Для использования бота необходимо подписаться на наши каналы!</b>\n\nПосле подписки нажмите кнопку «Проверить подписку».[span_108](start_span)"[span_108](end_span)
        await message.answer(sub_text, reply_markup=get_sub_keyboard(), parse_mode=ParseMode.HTML)[span_109](start_span)[span_109](end_span)
        return[span_110](start_span)[span_110](end_span)

    first_name = html.escape(message.from_user.first_name)[span_111](start_span)[span_111](end_span)
    welcome_text = f"💥 <b>Добро пожаловать, {first_name}!</b>\n‹━━━━━━━━━━━━━━━━━━›\n\n🔥 Приватный архив 18+\n— эксклюзивный контент\n— доступ только для участников\n\n👇 <b>Выбери раздел</b> 👇[span_112](start_span)"[span_112](end_span)
    await message.answer(welcome_text, reply_markup=get_main_keyboard(), parse_mode=ParseMode.HTML)[span_113](start_span)[span_113](end_span)

# ==================== ADMIN PANEL ====================
@dp.message(Command("admin"))
async def admin_start(message: types.Message):
    if message.from_user.id != ADMIN_ID:[span_114](start_span)[span_114](end_span)
        return[span_115](start_span)[span_115](end_span)
    await message.answer("👑 <b>Панель администратора</b>", reply_markup=get_admin_keyboard(), parse_mode=ParseMode.HTML)[span_116](start_span)[span_116](end_span)

@dp.callback_query(F.data == "admin_stats")
async def admin_stats_handler(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:[span_117](start_span)[span_117](end_span)
        return[span_118](start_span)[span_118](end_span)
    total_users, total_points = get_stats()[span_119](start_span)[span_119](end_span)
    text = (
        f"📊 <b>Статистика бота:</b>\n\n"
        f"👤 Всего пользователей: <b>{total_users}</b>\n"
        f"⭐ Всего набрано баллов: <b>{total_points}</b>"
    )[span_120](start_span)[span_120](end_span)
    await callback.message.edit_text(text, reply_markup=get_admin_keyboard(), parse_mode=ParseMode.HTML)[span_121](start_span)[span_121](end_span)

@dp.callback_query(F.data == "admin_set_ch1")
async def admin_set_ch1(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:[span_122](start_span)[span_122](end_span)
        return[span_123](start_span)[span_123](end_span)
    await state.set_state(AdminStates.waiting_for_ch1)[span_124](start_span)[span_124](end_span)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel_settings")]])[span_125](start_span)[span_125](end_span)
    await callback.message.edit_text("✏️ Отправьте username первого обязательного канала (например: <code>@mychannel</code>):", reply_markup=kb, parse_mode=ParseMode.HTML)[span_126](start_span)[span_126](end_span)

@dp.message(AdminStates.waiting_for_ch1)
async def process_ch1(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:[span_127](start_span)[span_127](end_span)
        return[span_128](start_span)[span_128](end_span)
    channel_username = message.text.strip()[span_129](start_span)[span_129](end_span)
    if not channel_username.startswith("@"):[span_130](start_span)[span_130](end_span)
        channel_username = "@" + channel_username[span_131](start_span)[span_131](end_span)
    set_setting("channel_1", channel_username)[span_132](start_span)[span_132](end_span)
    await state.clear()[span_133](start_span)[span_133](end_span)
    await message.answer(f"✅ Канал 1 успешно обновлен: <b>{channel_username}</b>", reply_markup=get_admin_keyboard(), parse_mode=ParseMode.HTML)[span_134](start_span)[span_134](end_span)

@dp.callback_query(F.data == "admin_set_ch2")
async def admin_set_ch2(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:[span_135](start_span)[span_135](end_span)
        return[span_136](start_span)[span_136](end_span)
    await state.set_state(AdminStates.waiting_for_ch2)[span_137](start_span)[span_137](end_span)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Удалить 2-канал", callback_data="admin_remove_ch2")],[span_138](start_span)[span_138](end_span)
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel_settings")][span_139](start_span)[span_139](end_span)
        ]
    )[span_140](start_span)[span_140](end_span)
    await callback.message.edit_text("✏️ Отправьте username второго обязательного канала (например: <code>@mychannel2</code>) или нажмите «Удалить»:", reply_markup=kb, parse_mode=ParseMode.HTML)[span_141](start_span)[span_141](end_span)

@dp.callback_query(F.data == "admin_remove_ch2")
async def admin_remove_ch2(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:[span_142](start_span)[span_142](end_span)
        return[span_143](start_span)[span_143](end_span)
    set_setting("channel_2", "")[span_144](start_span)[span_144](end_span)
    await state.clear()[span_145](start_span)[span_145](end_span)
    await callback.message.edit_text("✅ Второй обязательный канал успешно удален!", reply_markup=get_admin_keyboard())[span_146](start_span)[span_146](end_span)

@dp.message(AdminStates.waiting_for_ch2)
async def process_ch2(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:[span_147](start_span)[span_147](end_span)
        return[span_148](start_span)[span_148](end_span)
    channel_username = message.text.strip()[span_149](start_span)[span_149](end_span)
    if not channel_username.startswith("@"):[span_150](start_span)[span_150](end_span)
        channel_username = "@" + channel_username[span_151](start_span)[span_151](end_span)
    set_setting("channel_2", channel_username)[span_152](start_span)[span_152](end_span)
    await state.clear()[span_153](start_span)[span_153](end_span)
    await message.answer(f"✅ Канал 2 успешно добавлен/обновлен: <b>{channel_username}</b>", reply_markup=get_admin_keyboard(), parse_mode=ParseMode.HTML)[span_154](start_span)[span_154](end_span)

@dp.callback_query(F.data == "admin_cancel_settings")
async def admin_cancel_settings(callback: CallbackQuery, state: FSMContext):
    await state.clear()[span_155](start_span)[span_155](end_span)
    await callback.message.edit_text("👑 <b>Панель администратора</b>", reply_markup=get_admin_keyboard(), parse_mode=ParseMode.HTML)[span_156](start_span)[span_156](end_span)

@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_handler(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:[span_157](start_span)[span_157](end_span)
        return[span_158](start_span)[span_158](end_span)
    await state.set_state(AdminStates.waiting_for_broadcast)[span_159](start_span)[span_159](end_span)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel_settings")]])[span_160](start_span)[span_160](end_span)
    await callback.message.edit_text("📢 Отправьте сообщение, которое будет разослано всем пользователям:", reply_markup=kb)[span_161](start_span)[span_161](end_span)

@dp.message(AdminStates.waiting_for_broadcast)
async def process_broadcast(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:[span_162](start_span)[span_162](end_span)
        return[span_163](start_span)[span_163](end_span)
    await state.clear()[span_164](start_span)[span_164](end_span)
    
    users = get_all_users()[span_165](start_span)[span_165](end_span)
    await message.answer(f"⏳ Начинаем рассылку для {len(users)} пользователей...")[span_166](start_span)[span_166](end_span)
    
    success = 0[span_167](start_span)[span_167](end_span)
    failed = 0[span_168](start_span)[span_168](end_span)
    
    for uid in users:[span_169](start_span)[span_169](end_span)
        try:
            await message.copy_to(chat_id=uid)[span_170](start_span)[span_170](end_span)
            success += 1[span_171](start_span)[span_171](end_span)
            await asyncio.sleep(0.05)[span_172](start_span)[span_172](end_span)
        except Exception:
            failed += 1[span_173](start_span)[span_173](end_span)
            
    await message.answer(
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"🎉 Успешно отправлено: <b>{success}</b>\n"
        f"❌ Не доставлено: <b>{failed}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=get_admin_keyboard()
    )[span_174](start_span)[span_174](end_span)

@dp.callback_query(F.data == "admin_close")
async def admin_close_handler(callback: CallbackQuery):
    await callback.message.delete()[span_175](start_span)[span_175](end_span)

# ======================================================

@dp.callback_query(F.data == "check_sub")
async def check_sub_callback(callback: CallbackQuery):
    user_id = callback.from_user.id[span_176](start_span)[span_176](end_span)
    is_sub = await check_subscription(user_id)[span_177](start_span)[span_177](end_span)
    
    if is_sub:[span_178](start_span)[span_178](end_span)
        user = get_user(user_id)[span_179](start_span)[span_179](end_span)
        if user and user[1]:[span_180](start_span)[span_180](end_span)
            add_point(user[1])[span_181](start_span)[span_181](end_span)
            try:
                await bot.send_message(
                    user[1], 
                    "🎉 Пользователь, которого вы пригласили, подписался на канал! Вам начислен <b>+1 балл</b>.",
                    parse_mode=ParseMode.HTML
                )[span_182](start_span)[span_182](end_span)
            except:
                pass[span_183](start_span)[span_183](end_span)
            
            conn = sqlite3.connect("bot_database.db")[span_184](start_span)[span_184](end_span)
            cursor = conn.cursor()[span_185](start_span)[span_185](end_span)
            cursor.execute("UPDATE users SET referrer_id = NULL WHERE user_id = ?", (user_id,))[span_186](start_span)[span_186](end_span)
            conn.commit()[span_187](start_span)[span_187](end_span)
            conn.close()[span_188](start_span)[span_188](end_span)

        await callback.message.delete()[span_189](start_span)[span_189](end_span)
        first_name = html.escape(callback.from_user.first_name)[span_190](start_span)[span_190](end_span)
        welcome_text = f"💥 <b>Добро пожаловать, {first_name}!</b>\n‹━━━━━━━━━━━━━━›\n\n🔥 Приватный архив 18+\n— эксклюзивный контент\n— доступ только для участников\n\n👇 <b>Выбери раздел</b> 👇[span_191](start_span)"[span_191](end_span)
        await callback.message.answer(welcome_text, reply_markup=get_main_keyboard(), parse_mode=ParseMode.HTML)[span_192](start_span)[span_192](end_span)
    else:
        await callback.answer("❌ Вы еще не подписались на все каналы!", show_alert=True)[span_193](start_span)[span_193](end_span)

@dp.callback_query(F.data == "channels")
async def channels_handler(callback: CallbackQuery):
    is_sub = await check_subscription(callback.from_user.id)[span_194](start_span)[span_194](end_span)
    if not is_sub:[span_195](start_span)[span_195](end_span)
        await callback.answer("⚠️ Сначала подпишитесь на все обязательные каналы!", show_alert=True)[span_196](start_span)[span_196](end_span)
        return[span_197](start_span)[span_197](end_span)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📢 Перейти в канал", url=CHANNELS_SECTION_LINK)],[span_198](start_span)[span_198](end_span)
            [InlineKeyboardButton(text="👈 Назад", callback_data="back_main")][span_199](start_span)[span_199](end_span)
        ]
    )[span_200](start_span)[span_200](end_span)
    await callback.message.edit_text("👇 Вы можете перейти в наш приватный канал по кнопке ниже:", reply_markup=kb)[span_201](start_span)[span_201](end_span)

@dp.callback_query(F.data == "free_channel")
async def free_channel_handler(callback: CallbackQuery):
    user_id = callback.from_user.id[span_202](start_span)[span_202](end_span)
    is_sub = await check_subscription(user_id)[span_203](start_span)[span_203](end_span)
    if not is_sub:[span_204](start_span)[span_204](end_span)
        await callback.answer("⚠️ Сначала подпишитесь на все обязательные каналы!", show_alert=True)[span_205](start_span)[span_205](end_span)
        return[span_206](start_span)[span_206](end_span)

    bot_info = await bot.get_me()[span_207](start_span)[span_207](end_span)
    ref_link = f"https://t.me/{bot_info.username}?start={user_id}[span_208](start_span)"[span_208](end_span)
    
    user = get_user(user_id)[span_209](start_span)[span_209](end_span)
    points = user[2] if user else 0[span_210](start_span)[span_210](end_span)
    expire_date_str = user[4] if user and len(user) > 4 else None[span_211](start_span)[span_211](end_span)

    text = (
        f"⚡ <b>Бесплатный канал (Реферальная система)</b>\n\n"
        f"Приглашайте друзей и накапливайте баллы, чтобы получить доступ к закрытому каналу!\n"
        f"💡 <i>За каждого приглашенного друга, подписавшегося на канал, вы получаете +1 балл.</i>\n\n"
        f"👤 Ваши баллы: <b>{points} / {REQUIRED_REFERRALS}</b>\n"
        f"🔗 Ваша пригласительная ссылка:\n<code>{ref_link}</code>\n\n"
    )[span_212](start_span)[span_212](end_span)

    if points >= REQUIRED_REFERRALS:[span_213](start_span)[span_213](end_span)
        if not expire_date_str:[span_214](start_span)[span_214](end_span)
            expire_dt = datetime.now() + timedelta(days=SUB_DAYS)[span_215](start_span)[span_215](end_span)
            expire_date_str = expire_dt.strftime("%Y-%m-%d %H:%M:%S")[span_216](start_span)[span_216](end_span)
            conn = sqlite3.connect("bot_database.db")[span_217](start_span)[span_217](end_span)
            cursor = conn.cursor()[span_218](start_span)[span_218](end_span)
            cursor.execute("UPDATE users SET expire_date = ? WHERE user_id = ?", (expire_date_str, user_id))[span_219](start_span)[span_219](end_span)
            conn.commit()[span_220](start_span)[span_220](end_span)
            conn.close()[span_221](start_span)[span_221](end_span)

        try:
            expire_time = datetime.now() + timedelta(minutes=10)[span_222](start_span)[span_222](end_span)
            invite_link = await bot.create_chat_invite_link(
                chat_id=PRIVATE_CHANNEL_ID,
                member_limit=1,
                expire_date=expire_time
            )[span_223](start_span)[span_223](end_span)
            
            try:
                await bot.unban_chat_member(chat_id=PRIVATE_CHANNEL_ID, user_id=user_id)[span_224](start_span)[span_224](end_span)
            except Exception:
                pass[span_225](start_span)[span_225](end_span)

            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🔓 Войти в канал (Временная ссылка)", url=invite_link.invite_link)],[span_226](start_span)[span_226](end_span)
                    [InlineKeyboardButton(text="👈 Назад", callback_data="back_main")][span_227](start_span)[span_227](end_span)
                ]
            )[span_228](start_span)[span_228](end_span)
            text += (
                f"🎉 <b>Вы успешно собрали 10 баллов!</b>\n\n"
                f"⚠️ <b>ВНИМАНИЕ:</b> Вам предоставляется доступ к закрытому каналу ровно на <b>{SUB_DAYS} дней</b>!\n"
                f"⏳ Срок действия доступа: до <b>{expire_date_str}</b>.\n"
                f"По истечении {SUB_DAYS} дней система автоматически исключит вас из канала.\n\n"
                f"<i>Ссылка ниже одноразовая и действительна в течение 10 минут только для 1 человека!</i>"
            )[span_229](start_span)[span_229](end_span)
        except Exception as e:[span_230](start_span)[span_230](end_span)
            logging.error(f"Ошибка создания ссылки: {e}")[span_231](start_span)[span_231](end_span)
            kb = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="👈 Назад", callback_data="back_main")]][span_232](start_span)[span_232](end_span)
            )
            text += "⚠️ Произошла ошибка при создании ссылки. Убедитесь, что бот является администратором закрытого канала с правом приглашения пользователей.[span_233](start_span)"[span_233](end_span)
    else:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="👈 Назад", callback_data="back_main")][span_234](start_span)[span_234](end_span)
            ]
        )
        text += f"💡 Для получения доступа вам осталось набрать ещё <b>{REQUIRED_REFERRALS - points}</b> баллов.[span_235](start_span)"[span_235](end_span)

    await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)[span_236](start_span)[span_236](end_span)

@dp.callback_query(F.data == "help")
async def help_handler(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👈 Назад", callback_data="back_main")][span_237](start_span)[span_237](end_span)
        ]
    )[span_238](start_span)[span_238](end_span)
    help_text = "🤖 <b>Помощник / Информация</b>\n\n1. <b>Каналы</b> — Список наших основных ресурсов.\n2. <b>Бесплатный канал</b> — Приглашайте друзей по своей ссылке, копите баллы и получайте бесплатный доступ к закрытому каналу!\n\nЕсли у вас возникли вопросы, свяжитесь с администратором.[span_239](start_span)"[span_239](end_span)
    await callback.message.edit_text(help_text, reply_markup=kb, parse_mode=ParseMode.HTML)[span_240](start_span)[span_240](end_span)

@dp.callback_query(F.data == "back_main")
async def back_main_handler(callback: CallbackQuery):
    first_name = html.escape(callback.from_user.first_name)[span_241](start_span)[span_241](end_span)
    welcome_text = f"💥 <b>Добро пожаловать, {first_name}!</b>\n‹━━━━━━━━━━━━━━━━›\n\n🔥 Приватный архив 18+\n— эксклюзивный контент\n— доступ только для участников\n\n👇 <b>Выбери раздел</b> 👇[span_242](start_span)"[span_242](end_span)
    await callback.message.edit_text(welcome_text, reply_markup=get_main_keyboard(), parse_mode=ParseMode.HTML)[span_243](start_span)[span_243](end_span)

# --- AVTOMATIK KANALO'DAN CHIQARISH (10 KUNDAN SO'NG) ---
async def auto_kick_expired_users():
    while True:
        try:
            conn = sqlite3.connect("bot_database.db")[span_244](start_span)[span_244](end_span)
            cursor = conn.cursor()[span_245](start_span)[span_245](end_span)
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")[span_246](start_span)[span_246](end_span)
            cursor.execute("SELECT user_id FROM users WHERE expire_date IS NOT NULL AND expire_date <= ?", (now_str,))[span_247](start_span)[span_247](end_span)
            expired_users = cursor.fetchall()[span_248](start_span)[span_248](end_span)

            for (u_id,) in expired_users:[span_249](start_span)[span_249](end_span)
                try:
                    await bot.ban_chat_member(chat_id=PRIVATE_CHANNEL_ID, user_id=u_id)[span_250](start_span)[span_250](end_span)
                    await bot.unban_chat_member(chat_id=PRIVATE_CHANNEL_ID, user_id=u_id)[span_251](start_span)[span_251](end_span)
                    await bot.send_message(
                        u_id,
                        f"⏰ <b>Срок вашей бесплатной подписки ({SUB_DAYS} дней) истек!</b>\n\n"
                        f"Вы были автоматически исключены из закрытого канала. "
                        f"Чтобы войти снова, вам необходимо повторно набрать {REQUIRED_REFERRALS} баллов.",
                        parse_mode=ParseMode.HTML
                    )[span_252](start_span)[span_252](end_span)
                except Exception as e:[span_253](start_span)[span_253](end_span)
                    logging.error(f"Ошибка при исключении пользователя {u_id}: {e}")[span_254](start_span)[span_254](end_span)

                cursor.execute("UPDATE users SET expire_date = NULL, points = 0 WHERE user_id = ?", (u_id,))[span_255](start_span)[span_255](end_span)
                conn.commit()[span_256](start_span)[span_256](end_span)

            conn.close()[span_257](start_span)[span_257](end_span)
        except Exception as e:[span_258](start_span)[span_258](end_span)
            logging.error(f"Ошибка в auto_kick_expired_users: {e}")[span_259](start_span)[span_259](end_span)

        await asyncio.sleep(3600)[span_260](start_span)[span_260](end_span)

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
    
    # Render o'zining "Health Check"i uchun salomatlik marshruti
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
