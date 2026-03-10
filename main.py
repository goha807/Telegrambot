import os
import re
import asyncio
import shutil
import tempfile
import base64
import time
import json
from datetime import datetime, timedelta
from math import floor, ceil
import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, ContextTypes, filters, InlineQueryHandler
)
import yt_dlp
from telegram.error import TimedOut, BadRequest

# ================= КОНФІГУРАЦІЯ =================
BOT_TOKEN = "8213254007:AAFQkGiQqi1YirAvF4VuGcF3CL6WpqFVSGA"
ADMINS_IDS = [1813590984]
MAX_SIZE = 50 * 1024 * 1024
SPAM_DELAY = 2.0
DATA_FILE = 'data/bot_data.json'

# --- Стани ---
SELECTING, SELECT_SOURCE, ASK_QUERY, DOWNLOAD = range(4)
ADMIN_MENU, AWAIT_ADD_STARS, AWAIT_REMOVE_STARS, AWAIT_USER_STATS, AWAIT_SET_DOWNLOADS_ID, AWAIT_SET_DOWNLOADS_COUNT = range(4, 10)

# --- Глобальні дані ---
user_data = {}
download_queue = asyncio.PriorityQueue()
download_in_progress = asyncio.Lock()
duel_data = {}
promocodes = {}
required_channels = []
last_activity = {}
application = None

# --- Ціни ---
SHOP_PRICES = {
    "vip_1_day": 200,
    "vip_7_days": 1000,
    "vip_30_days": 3500,
    "unlimited_24h": 500,
    "priority_pass": 50,
    "case_bronze": 100,
    "case_silver": 250,
    "case_gold": 500
}

# ================= ЗБЕРЕЖЕННЯ ДАНИХ =================
def ensure_data_dir():
    os.makedirs('data', exist_ok=True)

def save_data():
    ensure_data_dir()
    try:
        data_to_save = {
            'user_data': {},
            'promocodes': {},
            'required_channels': required_channels
        }
        for uid, stats in user_data.items():
            data_to_save['user_data'][uid] = stats.copy()
            if stats.get('vip_expiration') and isinstance(stats['vip_expiration'], datetime):
                data_to_save['user_data'][uid]['vip_expiration'] = stats['vip_expiration'].isoformat()
            if stats.get('unlimited_dl_expires') and isinstance(stats['unlimited_dl_expires'], datetime):
                data_to_save['user_data'][uid]['unlimited_dl_expires'] = stats['unlimited_dl_expires'].isoformat()
        
        for code, pdata in promocodes.items():
            data_to_save['promocodes'][code] = pdata.copy()
            if pdata.get('expires') and isinstance(pdata['expires'], datetime):
                data_to_save['promocodes'][code]['expires'] = pdata['expires'].isoformat()
        
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data_to_save, f, ensure_ascii=False, indent=2)
        print("💾 Дані збережено")
    except Exception as e:
        print(f"❌ Помилка збереження: {e}")

def load_data():
    ensure_data_dir()
    global user_data, promocodes, required_channels
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            user_data = data.get('user_data', {})
            required_channels = data.get('required_channels', [])
            raw_promos = data.get('promocodes', {})
            for code, pdata in raw_promos.items():
                if pdata.get('expires') and isinstance(pdata['expires'], str):
                    pdata['expires'] = datetime.fromisoformat(pdata['expires'])
                promocodes[code] = pdata
            for uid, stats in user_data.items():
                if stats.get('vip_expiration') and isinstance(stats['vip_expiration'], str):
                    stats['vip_expiration'] = datetime.fromisoformat(stats['vip_expiration'])
                if stats.get('unlimited_dl_expires') and isinstance(stats['unlimited_dl_expires'], str):
                    stats['unlimited_dl_expires'] = datetime.fromisoformat(stats['unlimited_dl_expires'])
            print("📂 Дані завантажено")
        except Exception as e:
            print(f"❌ Помилка завантаження: {e}")
    else:
        print("📂 Файл даних не знайдено")

# ================= ДОПОМІЖНІ ФУНКЦІЇ =================
def is_admin(user_id):
    return user_id in ADMINS_IDS

def log_action(user, action: str):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    name = user.username or user.full_name or "Unknown"
    print(f"🕒 {now} | 👤 {name} | 🆔 {user.id} | 📌 {action}")

def clean_filename(name: str) -> str:
    return re.sub(r'[\/*?:"<>|]', "", name).strip()

def check_spam(user_id):
    now = time.time()
    last_time = last_activity.get(user_id, 0)
    if now - last_time < SPAM_DELAY:
        return True
    last_activity[user_id] = now
    return False

def get_user_stats(user_id):
    stats = user_data.setdefault(int(user_id), {
        "downloads": 0, "tracks": 0, "videos": 0,
        "source": "N/A", "genre": None, "achievements": [],
        "lang": "ua", "stars": 50, "last_download_hour": None,
        "source_counts": {"yt": 0, "sc": 0, "tt": 0}, "is_blocked": False,
        "is_vip": False, "vip_expiration": None,
        "used_promos": [], "has_channel_reward": False,
        "unlimited_dl_expires": None, "priority_passes": 0
    })
    for key, default in [("is_vip", False), ("vip_expiration", None), ("used_promos", []),
                         ("has_channel_reward", False), ("stars", 50),
                         ("unlimited_dl_expires", None), ("priority_passes", 0), ("downloads", 0)]:
        if key not in stats:
            stats[key] = default
    return stats

def is_vip_active(user_id):
    stats = get_user_stats(user_id)
    if stats.get("is_vip", False):
        return True
    vip_exp = stats.get("vip_expiration")
    if vip_exp:
        if isinstance(vip_exp, str):
            vip_exp = datetime.fromisoformat(vip_exp)
            stats["vip_expiration"] = vip_exp
        if datetime.now() < vip_exp:
            return True
    return False

def is_unlimited_active(user_id):
    stats = get_user_stats(user_id)
    unlim_exp = stats.get("unlimited_dl_expires")
    if unlim_exp:
        if isinstance(unlim_exp, str):
            unlim_exp = datetime.fromisoformat(unlim_exp)
            stats["unlimited_dl_expires"] = unlim_exp
        if datetime.now() < unlim_exp:
            return True
    return False

def get_final_cost(user_id, base_cost):
    if is_unlimited_active(user_id):
        return 0
    if is_vip_active(user_id):
        return ceil(base_cost * 0.5)
    return base_cost

async def is_user_subscribed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not required_channels or not update.effective_user:
        return True
    user_id = update.effective_user.id
    missing_channels = []
    for channel in required_channels:
        try:
            member = await context.bot.get_chat_member(chat_id=channel['id'], user_id=user_id)
            if member.status not in ['member', 'administrator', 'creator']:
                missing_channels.append(channel)
        except Exception as e:
            log_action(update.effective_user, f"Помилка перевірки підписки: {e}")
            missing_channels.append(channel)
    
    if missing_channels:
        keyboard = []
        for channel in missing_channels:
            keyboard.append([InlineKeyboardButton(
                "➡️ Підписатись",
                url=f"https://t.me/{channel['username'].lstrip('@')}"
            )])
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❗️ Для використання бота, будь ласка, підпишіться на наші канали.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return False
    else:
        stats = get_user_stats(user_id)
        if not stats.get('has_channel_reward', False):
            reward = 100
            stats['stars'] += reward
            current_expiry = stats.get("vip_expiration") or datetime.now()
            if current_expiry < datetime.now():
                current_expiry = datetime.now()
            stats["vip_expiration"] = current_expiry + timedelta(days=1)
            stats['has_channel_reward'] = True
            log_action(update.effective_user, f"Отримав бонус {reward}⭐ та VIP за підписку")
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"✅ Дякуємо за підписку! Ви отримали:\n➕ {reward} зірок ⭐\n⭐ VIP-статус на 1 день!"
            )
            save_data()
        return True

def calculate_level(downloads):
    return floor(downloads / 10) + 1

async def check_achievements(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stats = get_user_stats(user_id)
    current_downloads = stats["downloads"]
    for downloads_needed, achievement_name in [(1, "Новачок"), (10, "Аматор"), (50, "Меломан"), (100, "Майстер музики")]:
        if current_downloads >= downloads_needed and achievement_name not in stats["achievements"]:
            stats["achievements"].append(achievement_name)
            log_action(update.effective_user, f"Розблокував досягнення: {achievement_name}")
            try:
                await update.message.reply_text(f"🎉 Нове досягнення: {achievement_name}! 🎉", parse_mode="Markdown")
            except:
                pass
            save_data()

async def check_blocked(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not update.effective_user:
        return False
    user_id = update.effective_user.id
    if get_user_stats(user_id).get("is_blocked", False):
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Ваш акаунт заблоковано адміністратором."
            )
        except Exception as e:
            log_action(update.effective_user, f"Failed to send blocked message: {e}")
        return True
    return False

COSTS = {
    "audio": {"128": 10, "192": 15, "256": 20, "base_find": 15, "base_random": 10},
    "video": {"360": 25, "480": 35, "720": 50, "1080": 70}
}

# ================= КОМАНДИ КОРИСТУВАЧА =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id):
        log_action(update.effective_user, "Спам /start")
        return ConversationHandler.END
    if await check_blocked(update, context):
        return ConversationHandler.END
    if not await is_user_subscribed(update, context):
        return ConversationHandler.END
    
    user = update.effective_user
    log_action(user, "Запустив /start")
    stats = get_user_stats(user.id)
    context.user_data["lang"] = stats.get("lang", "ua")
    
    keyboard = [
        [InlineKeyboardButton("🎵 Музика", callback_data="audio")],
        [InlineKeyboardButton("🎬 Відео", callback_data="video")]
    ]
    
    await update.message.reply_text(
        "Привіт! Я допоможу тобі завантажити 🎵 музику або 🎬 відео з YouTube, SoundCloud або TikTok.\n📌 Натисни кнопку нижче, щоб почати:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return SELECTING

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id):
        return
    if await check_blocked(update, context):
        return
    if not await is_user_subscribed(update, context):
        return
    log_action(update.effective_user, "Використав /help")
    text = """📖 Довідка
Підтримувані джерела:
- YouTube (аудіо, відео)
- SoundCloud (аудіо)
- TikTok (відео)
Основні команди:
`start` — запуск
`help` — допомога
`shop` — магазин
`cancel` — скасувати
`restart` — перезапуск
`ping` — перевірка
`stats` — статистика
`lang` — мова
`find` — пошук
`support` — підтримка
`level` — рівень
`achievements` — досягнення
`topusers` — топ
`random` — випадковий трек
`promo <code>` — промокод
`balance` — баланс
`dice <ставка>` — кубик
`flipcoin <ставка> <вибір>` — монетка
`duel <ID> <ставка>` — дуель"""
    await update.message.reply_markdown(text)

async def achievements_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id):
        return
    if await check_blocked(update, context):
        return
    if not await is_user_subscribed(update, context):
        return
    log_action(update.effective_user, "Перевірив /achievements")
    stats = get_user_stats(update.effective_user.id)
    if not stats["achievements"]:
        await update.message.reply_text("😕 Поки немає досягнень")
        return
    response = "🏆 *Досягнення*:\n"
    for achievement in stats["achievements"]:
        response += f"- {achievement}\n"
    await update.message.reply_text(response, parse_mode="Markdown")

async def lang_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id):
        return
    if await check_blocked(update, context):
        return
    log_action(update.effective_user, "Використав /lang")
    keyboard = [
        [InlineKeyboardButton("🇺🇦 Українська", callback_data="lang_ua")],
        [InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")]
    ]
    await update.message.reply_text("🌐 Обери мову:", reply_markup=InlineKeyboardMarkup(keyboard))

async def set_lang_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang_code = query.data.split("_")[1]
    context.user_data["lang"] = lang_code
    get_user_stats(query.from_user.id)["lang"] = lang_code
    log_action(query.from_user, f"Змінив мову на {lang_code}")
    await query.edit_message_text(f"🌐 Мова: {lang_code.upper()}")
    save_data()

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id):
        return
    if await check_blocked(update, context):
        return
    log_action(update.effective_user, "Використав /ping")
    await update.message.reply_text("✅ Бот активний!")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id):
        return
    if await check_blocked(update, context):
        return
    if not await is_user_subscribed(update, context):
        return
    log_action(update.effective_user, "Перевірив /stats")
    user = update.effective_user
    stats = get_user_stats(user.id)
    vip_status = "👑 VIP" if is_vip_active(user.id) else "Звичайний"
    text = f"📊 *Статистика*:\n"
    text += f"👑 Статус: {vip_status}\n"
    text += f"🎵 Треків: {stats['tracks']}\n"
    text += f"🎬 Відео: {stats['videos']}\n"
    text += f"📌 Джерело: {stats['source']}"
    await update.message.reply_markdown(text)

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id):
        return
    if await check_blocked(update, context):
        return
    log_action(update.effective_user, "Використав /support")
    await update.message.reply_text("💬 Підтримка: https://t.me/MyDownloaderSupport")

async def level_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id):
        return
    if await check_blocked(update, context):
        return
    if not await is_user_subscribed(update, context):
        return
    log_action(update.effective_user, "Перевірив /level")
    stats = get_user_stats(update.effective_user.id)
    level = calculate_level(stats['downloads'])
    needed = (level * 10) - stats['downloads']
    await update.message.reply_markdown(
        f"🌟 *Рівень: {level}*\n"
        f"Завантажено: {stats['downloads']}\n"
        f"До наступного: {needed}"
    )

async def top_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id):
        return
    if await check_blocked(update, context):
        return
    if not await is_user_subscribed(update, context):
        return
    log_action(update.effective_user, "Перевірив /topusers")
    
    if not user_data:
        await update.message.reply_text("📊 Ще немає статистики!")
        return
    
    sorted_users = sorted(
        user_data.items(),
        key=lambda x: x[1].get('downloads', 0),
        reverse=True
    )[:5]
    
    if not sorted_users:
        await update.message.reply_text("📊 Ще немає статистики!")
        return

    response = "🏆 *Топ-5*:\n"
    for i, (uid, stats) in enumerate(sorted_users, 1):
        try:
            user_info = await context.bot.get_chat(uid)
            name = user_info.username or user_info.first_name
            if not name:
                name = f"ID {uid}"
        except:
            name = f"ID {uid}"
        downloads = stats.get('downloads', 0)
        response += f"{i}. @{name} — {downloads} завантажень\n"
    
    await update.message.reply_markdown(response)

async def genre_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id):
        return
    if await check_blocked(update, context):
        return
    if not await is_user_subscribed(update, context):
        return
    log_action(update.effective_user, f"Встановив жанр: {context.args}")
    if not context.args:
        await update.message.reply_markdown("❓ Вкажіть жанр. Приклад: `/genre рок`")
        return
    genre = " ".join(context.args).capitalize()
    get_user_stats(update.effective_user.id)["genre"] = genre
    await update.message.reply_markdown(f"✅ Жанр: *{genre}*")
    save_data()

async def random_track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id):
        return
    if await check_blocked(update, context):
        return
    if not await is_user_subscribed(update, context):
        return
    log_action(update.effective_user, "Використав /random")
    user = update.effective_user
    cost = get_final_cost(user.id, COSTS["audio"]["base_random"])
    stats = get_user_stats(user.id)
    if stats["stars"] < cost:
        await update.message.reply_markdown(f"❌ Потрібно {cost} ⭐. Баланс: {stats['stars']}")
        return
    stats["stars"] -= cost
    await update.message.reply_text("🎧 Знаходжу трек...")
    tracks = [
        "ytsearch:Imagine Dragons Believer",
        "ytsearch:Queen Bohemian Rhapsody",
        "ytsearch:Dua Lipa Don't Start Now"
    ]
    query = random.choice(tracks)
    tmpdir = None
    try:
        filepath, title, tmpdir = await download_media(query, audio=True, quality="192")
        if not filepath:
            await update.message.reply_text("😕 Нічого не знайдено")
            return
        with open(filepath, "rb") as f:
            await update.message.reply_audio(f, caption=f"🎵 *{title}*", parse_mode="Markdown")
        stats["downloads"] += 1
        stats["tracks"] += 1
        log_action(update.effective_user, f"Завантажив трек: {title}")
        await check_achievements(update, context)
        save_data()
    except Exception as e:
        log_action(update.effective_user, f"Помилка завантаження: {e}")
        await update.message.reply_text(f"❌ Помилка: {e}")
    finally:
        if tmpdir and os.path.isdir(tmpdir):
            shutil.rmtree(tmpdir)

async def find(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id):
        return
    if await check_blocked(update, context):
        return
    if not await is_user_subscribed(update, context):
        return
    log_action(update.effective_user, f"Використав /find: {context.args}")
    if not context.args:
        await update.message.reply_markdown("❓ Напишіть опис. Приклад: `/find пісня`")
        return
    user = update.effective_user
    cost = get_final_cost(user.id, COSTS["audio"]["base_find"])
    stats = get_user_stats(user.id)
    if stats["stars"] < cost:
        await update.message.reply_markdown(f"❌ Потрібно {cost} ⭐. Баланс: {stats['stars']}")
        return
    stats["stars"] -= cost
    query = "ytsearch1:" + " ".join(context.args)
    await update.message.reply_text(f"🔍 Шукаю: {' '.join(context.args)}")
    tmpdir = None
    try:
        filepath, title, tmpdir = await download_media(query, audio=True, quality="192")
        if not filepath:
            await update.message.reply_text("😕 Нічого не знайдено")
            return
        with open(filepath, "rb") as f:
            await update.message.reply_audio(f, caption=f"🎵 {title}")
        stats["downloads"] += 1
        stats["tracks"] += 1
        log_action(update.effective_user, f"Завантажив через /find: {title}")
        await check_achievements(update, context)
        save_data()
    except Exception as e:
        log_action(update.effective_user, f"Помилка /find: {e}")
        await update.message.reply_text(f"❌ Помилка: {e}")
    finally:
        if tmpdir and os.path.isdir(tmpdir):
            shutil.rmtree(tmpdir)

# ================= МАГАЗИН (3 КЕЙСИ) =================
async def shop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id):
        return
    if await check_blocked(update, context):
        return
    if not await is_user_subscribed(update, context):
        return
    log_action(update.effective_user, "Відкрив /shop")
    stats = get_user_stats(update.effective_user.id)
    keyboard = [
        [InlineKeyboardButton(f"👑 VIP 1 день ({SHOP_PRICES['vip_1_day']}⭐)", callback_data="shop_buy_vip_1")],
        [InlineKeyboardButton(f"👑 VIP 7 днів ({SHOP_PRICES['vip_7_days']}⭐)", callback_data="shop_buy_vip_7")],
        [InlineKeyboardButton(f"👑 VIP 30 днів ({SHOP_PRICES['vip_30_days']}⭐)", callback_data="shop_buy_vip_30")],
        [InlineKeyboardButton(f"♾ Безліміт 24г ({SHOP_PRICES['unlimited_24h']}⭐)", callback_data="shop_buy_unlimited")],
        [InlineKeyboardButton(f"🚀 Пріоритет ({SHOP_PRICES['priority_pass']}⭐)", callback_data="shop_buy_priority")],
        [InlineKeyboardButton(f"🥉 Бронзовий кейс ({SHOP_PRICES['case_bronze']}⭐)", callback_data="shop_case_bronze")],
        [InlineKeyboardButton(f"🥈 Срібний кейс ({SHOP_PRICES['case_silver']}⭐)", callback_data="shop_case_silver")],
        [InlineKeyboardButton(f"🥇 Золотий кейс ({SHOP_PRICES['case_gold']}⭐)", callback_data="shop_case_gold")]
    ]
    await update.message.reply_markdown(
        f"🛒 *Магазин*\nБаланс: {stats['stars']} ⭐",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def shop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    stats = get_user_stats(user_id)
    data = query.data
    log_action(query.from_user, f"Натиснув в магазині: {data}")
    
    items = {
        "shop_buy_vip_1": (SHOP_PRICES["vip_1_day"], "VIP (1 день)", lambda: extend_vip(stats, days=1)),
        "shop_buy_vip_7": (SHOP_PRICES["vip_7_days"], "VIP (7 днів)", lambda: extend_vip(stats, days=7)),
        "shop_buy_vip_30": (SHOP_PRICES["vip_30_days"], "VIP (30 днів)", lambda: extend_vip(stats, days=30)),
        "shop_buy_unlimited": (SHOP_PRICES["unlimited_24h"], "Безліміт 24г", lambda: extend_unlimited(stats)),
        "shop_buy_priority": (SHOP_PRICES["priority_pass"], "Пріоритет", lambda: add_priority(stats))
    }
    
    # Логіка для 3 кейсів
    if data == "shop_case_bronze":
        await open_case(query, stats, "bronze")
        return
    elif data == "shop_case_silver":
        await open_case(query, stats, "silver")
        return
    elif data == "shop_case_gold":
        await open_case(query, stats, "gold")
        return
    
    if data in items:
        cost, name, action = items[data]
        if stats["stars"] >= cost:
            stats["stars"] -= cost
            action()
            log_action(query.from_user, f"Купив: {name}")
            await query.message.reply_text(f"✅ Куплено: {name}!")
            save_data()
        else:
            await query.message.reply_text(f"❌ Недостатньо ⭐. Потрібно: {cost}, Є: {stats['stars']}")

async def open_case(query, stats, case_type):
    prices = {
        "bronze": SHOP_PRICES["case_bronze"],
        "silver": SHOP_PRICES["case_silver"],
        "gold": SHOP_PRICES["case_gold"]
    }
    
    rewards = {
        "bronze": [
            (0.40, "stars", 20),
            (0.30, "stars", 50),
            (0.20, "stars", 100),
            (0.10, "vip", 1)
        ],
        "silver": [
            (0.30, "stars", 100),
            (0.30, "stars", 200),
            (0.25, "stars", 300),
            (0.10, "vip", 3),
            (0.05, "priority", 5)
        ],
        "gold": [
            (0.20, "stars", 300),
            (0.25, "stars", 500),
            (0.20, "stars", 750),
            (0.15, "vip", 7),
            (0.10, "vip", 30),
            (0.10, "priority", 10)
        ]
    }
    
    cost = prices[case_type]
    case_names = {"bronze": "🥉 Бронзовий", "silver": "🥈 Срібний", "gold": "🥇 Золотий"}
    
    if stats["stars"] < cost:
        await query.message.reply_text(f"❌ Недостатньо ⭐. Потрібно: {cost}, Є: {stats['stars']}")
        return
    
    stats["stars"] -= cost
    
    # Розіграш нагороди
    chance = random.random()
    cumulative = 0
    reward_type = "stars"
    reward_value = 0
    
    for prob, rtype, value in rewards[case_type]:
        cumulative += prob
        if chance <= cumulative:
            reward_type = rtype
            reward_value = value
            break
    
    log_action(query.from_user, f"Відкрив {case_names[case_type]} кейс, випало: {reward_type} {reward_value}")
    
    if reward_type == "stars":
        stats["stars"] += reward_value
        await query.message.reply_text(f"🎉 {case_names[case_type]} кейс!\nВипало: {reward_value} ⭐!")
    elif reward_type == "vip":
        extend_vip(stats, days=reward_value)
        await query.message.reply_text(f"🎉 {case_names[case_type]} кейс!\nВипало: VIP на {reward_value} днів!")
    elif reward_type == "priority":
        stats["priority_passes"] += reward_value
        await query.message.reply_text(f"🎉 {case_names[case_type]} кейс!\nВипало: {reward_value} Priority Pass!")
    
    save_data()

def extend_vip(stats, days=1):
    curr = stats.get("vip_expiration") or datetime.now()
    if curr < datetime.now():
        curr = datetime.now()
    stats["vip_expiration"] = curr + timedelta(days=days)

def extend_unlimited(stats):
    curr = stats.get("unlimited_dl_expires") or datetime.now()
    if curr < datetime.now():
        curr = datetime.now()
    stats["unlimited_dl_expires"] = curr + timedelta(hours=24)

def add_priority(stats):
    stats["priority_passes"] += 1

# ================= ЗАВАНТАЖЕННЯ =================
async def select_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_blocked(update, context):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    context.user_data["type"] = query.data
    log_action(query.from_user, f"Обрав тип: {query.data}")
    if query.data == "audio":
        keyboard = [[InlineKeyboardButton("YouTube", callback_data="yt"), InlineKeyboardButton("SoundCloud", callback_data="sc")]]
    else:
        keyboard = [[InlineKeyboardButton("YouTube", callback_data="yt"), InlineKeyboardButton("TikTok", callback_data="tt")]]
    await query.edit_message_text("🔍 Обери джерело:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_SOURCE

async def select_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_blocked(update, context):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    context.user_data["source"] = query.data
    log_action(query.from_user, f"Обрав джерело: {query.data}")
    media_type = context.user_data["type"]
    user_id = query.from_user.id
    if media_type == "audio":
        keyboard = [[InlineKeyboardButton(f"{kb}kbps ({get_final_cost(user_id, COSTS['audio'][kb])}⭐)", callback_data=kb)] for kb in ["128", "192", "256"]]
    else:
        keyboard = [[InlineKeyboardButton(f"{p}p ({get_final_cost(user_id, COSTS['video'][p])}⭐)", callback_data=p)] for p in ["360", "480", "720", "1080"]]
    await query.edit_message_text("🎚 Обери якість:", reply_markup=InlineKeyboardMarkup(keyboard))
    return ASK_QUERY

async def select_quality(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_blocked(update, context):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    quality = query.data
    log_action(query.from_user, f"Обрав якість: {quality}")
    media_type = context.user_data.get("type", "audio")
    base_cost = COSTS[media_type][quality]
    cost = get_final_cost(query.from_user.id, base_cost)
    stats = get_user_stats(query.from_user.id)
    if stats["stars"] < cost:
        await query.edit_message_text(f"❌ Потрібно {cost} ⭐. Баланс: {stats['stars']}")
        return ConversationHandler.END
    context.user_data["quality"] = quality
    await query.edit_message_text("📥 Надішли посилання або назву:")
    return DOWNLOAD

async def handle_download(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id):
        await update.message.reply_text("⏳ Зачекайте трохи")
        return ConversationHandler.END
    if await check_blocked(update, context):
        return ConversationHandler.END
    if not await is_user_subscribed(update, context):
        return ConversationHandler.END
    
    log_action(update.effective_user, f"Надіслав запит: {update.message.text[:50]}")
    user_query = update.message.text.strip()
    media_type = context.user_data.get("type", "audio")
    user = update.effective_user
    stats = get_user_stats(user.id)
    
    if stats.get("genre"):
        user_query = f"{user_query} {stats['genre']} genre"
        stats["genre"] = None
    
    url_pattern = re.compile(r'https?://[^\s/$.?#].[^\s]*')
    if not url_pattern.match(user_query):
        user_query = f"ytsearch1:{user_query}"
    
    quality = context.user_data.get("quality", "128")
    base_cost = COSTS[media_type][quality]
    cost = get_final_cost(user.id, base_cost)
    
    priority = 10
    if is_vip_active(user.id):
        priority = 1
    elif stats.get("priority_passes", 0) > 0:
        priority = 5
        stats["priority_passes"] -= 1
        log_action(update.effective_user, "Використано Priority Pass!")
        await update.message.reply_text("🚀 Використано Priority Pass!")
    
    await download_queue.put((priority, time.time(), user.id, user_query, media_type, quality, cost, context.user_data.copy(), update.message.chat_id, None))
    position = download_queue.qsize()
    prio_text = "VIP" if priority == 1 else ("Високий" if priority == 5 else "Звичайний")
    await update.message.reply_text(f"🔄 В черзі. Позиція: {position}. Пріоритет: {prio_text}")
    return ConversationHandler.END

# ================= ОБРОБКА ЧЕРГИ =================
async def process_queue():
    while True:
        try:
            priority, timestamp, user_id, user_query, media_type, quality, cost, u_data, chat_id, inline_message_id = await download_queue.get()
            temp_context = type('obj', (object,), {'user_data': u_data})()
            async with download_in_progress:
                try:
                    user_info = await application.bot.get_chat(user_id)
                except:
                    user_info = type('obj', (object,), {'username': 'unknown', 'id': user_id})()
                
                log_action(user_info, f"Завантаження (Пріоритет {priority}): {user_query[:50]}")
                
                if inline_message_id:
                    try:
                        await application.bot.edit_message_text(inline_message_id=inline_message_id, text="🔄 Завантаження...")
                    except:
                        pass
                else:
                    await application.bot.send_message(chat_id=chat_id, text="🚀 Починаю...")
                
                stats = get_user_stats(user_id)
                real_cost = get_final_cost(user_id, cost) if cost > 0 else 0
                
                if stats["stars"] < real_cost:
                    error_text = f"❌ Потрібно {real_cost} ⭐. Баланс: {stats['stars']}"
                    if inline_message_id:
                        try:
                            await application.bot.edit_message_text(inline_message_id=inline_message_id, text=error_text)
                        except:
                            pass
                    else:
                        await application.bot.send_message(chat_id=chat_id, text=error_text)
                    download_queue.task_done()
                    continue
                
                stats["stars"] -= real_cost
                tmpdir = None
                try:
                    filepath, title, tmpdir = await download_media(user_query, audio=(media_type == "audio"), quality=quality)
                    if not filepath:
                        error_text = f"😕 Нічого не знайдено"
                        log_action(user_info, f"Нічого не знайдено для: {user_query[:50]}")
                        if inline_message_id:
                            try:
                                await application.bot.edit_message_text(inline_message_id=inline_message_id, text=error_text)
                            except:
                                pass
                        else:
                            await application.bot.send_message(chat_id=chat_id, text=error_text)
                        download_queue.task_done()
                        continue
                    
                    size = os.path.getsize(filepath)
                    if size > MAX_SIZE:
                        error_text = "⚠️ Файл занадто великий (>50MB)"
                        if inline_message_id:
                            try:
                                await application.bot.edit_message_text(inline_message_id=inline_message_id, text=error_text)
                            except:
                                pass
                        else:
                            await application.bot.send_message(chat_id=chat_id, text=error_text)
                    else:
                        with open(filepath, "rb") as f:
                            try:
                                if media_type == "audio":
                                    if inline_message_id:
                                        await application.bot.send_audio(chat_id=user_id, audio=f, caption=f"🎵 {title}")
                                        try:
                                            await application.bot.edit_message_text(inline_message_id=inline_message_id, text="✅ Відправлено!")
                                        except:
                                            pass
                                    else:
                                        await application.bot.send_audio(chat_id=chat_id, audio=f, caption=f"🎵 {title}")
                                else:
                                    if inline_message_id:
                                        await application.bot.send_video(chat_id=user_id, video=f, caption=f"🎬 {title}")
                                        try:
                                            await application.bot.edit_message_text(inline_message_id=inline_message_id, text="✅ Відправлено!")
                                        except:
                                            pass
                                    else:
                                        await application.bot.send_video(chat_id=chat_id, video=f, caption=f"🎬 {title}")
                            except TimedOut:
                                f.seek(0)
                                if inline_message_id:
                                    await application.bot.send_document(chat_id=user_id, document=f, filename=os.path.basename(filepath), caption=f"📎 {title}")
                                    try:
                                        await application.bot.edit_message_text(inline_message_id=inline_message_id, text="✅ Відправлено!")
                                    except:
                                        pass
                                else:
                                    await application.bot.send_document(chat_id=chat_id, document=f, filename=os.path.basename(filepath), caption=f"📎 {title}")
                        
                        stats["downloads"] += 1
                        stats["source"] = u_data.get("source", "N/A")
                        stats["source_counts"][stats["source"]] = stats["source_counts"].get(stats["source"], 0) + 1
                        if media_type == "audio":
                            stats["tracks"] += 1
                        else:
                            stats["videos"] += 1
                        log_action(user_info, f"✅ Успішно завантажено: {title[:50]}")
                        await check_achievements_from_queue(temp_context, user_id)
                        save_data()
                except Exception as e:
                    error_text = f"❌ Помилка: {e}"
                    log_action(user_info, f"❌ Помилка завантаження: {e}")
                    if inline_message_id:
                        try:
                            await application.bot.edit_message_text(inline_message_id=inline_message_id, text=error_text)
                        except:
                            pass
                    else:
                        await application.bot.send_message(chat_id=chat_id, text=error_text)
                finally:
                    if tmpdir and os.path.isdir(tmpdir):
                        shutil.rmtree(tmpdir)
                download_queue.task_done()
        except Exception as e:
            print(f"Критична помилка в черзі: {e}")
            log_action(type('obj', (object,), {'username': 'system', 'id': 0})(), f"Критична помилка в черзі: {e}")
            try:
                download_queue.task_done()
            except:
                pass

async def check_achievements_from_queue(context, user_id):
    stats = get_user_stats(user_id)
    for downloads_needed, achievement_name in [(1, "Новачок"), (10, "Аматор"), (50, "Меломан"), (100, "Майстер музики")]:
        if stats["downloads"] >= downloads_needed and achievement_name not in stats["achievements"]:
            stats["achievements"].append(achievement_name)
            try:
                await application.bot.send_message(chat_id=user_id, text=f"🎉 *Нове досягнення: {achievement_name}!*", parse_mode="Markdown")
            except:
                pass

# ================= 🔧 ВИПРАВЛЕНА ФУНКЦІЯ ЗАВАНТАЖЕННЯ =================
async def download_media(query, audio=True, quality="192"):
    tmpdir = tempfile.mkdtemp()
    log_action(type('obj', (object,), {'username': 'system', 'id': 0})(), f"Початок завантаження: {query[:50]}")
    
    base_opts = {
        "outtmpl": os.path.join(tmpdir, "%(title)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "ignoreerrors": False,
        "extractor_retries": 5,
        "fragment_retries": 5,
        "retry_sleep_functions": {"extractor": lambda n: 2 ** n},
        "extractor_args": {
            "youtube": {
                "player_client": ["ios", "web", "tv_embedded", "android"],
                "player_skip": ["webpage", "meta"],
                "initial_data": ["*"]
            }
        },
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,uk;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive"
        },
        "no_check_certificate": True,
        "socket_timeout": 60,
        "retries": 5
    }
    
    if audio:
        if quality == "best":
            fmt = "bestaudio/best"
        else:
            fmt = f"bestaudio[abr<={quality}]/bestaudio/best"
        opts = {
            **base_opts,
            "format": fmt,
            "postprocessors": [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': quality if quality != "best" else "192"
            }]
        }
    else:
        if quality == "best":
            fmt = "bestvideo+bestaudio/best"
        else:
            fmt = f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best"
        opts = {
            **base_opts,
            "format": fmt,
            "merge_output_format": "mp4",
            "postprocessors": [{'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'}]
        }
    
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, query, download=True)
            if not info or ('entries' in info and not info['entries']):
                log_action(type('obj', (object,), {'username': 'system', 'id': 0})(), f"Нічого не знайдено для: {query[:50]}")
                shutil.rmtree(tmpdir)
                return None, None, None
            entry = info['entries'][0] if 'entries' in info and info['entries'] else info
            files = os.listdir(tmpdir)
            if not files:
                log_action(type('obj', (object,), {'username': 'system', 'id': 0})(), f"Файли не створені для: {query[:50]}")
                shutil.rmtree(tmpdir)
                return None, None, None
            
            file = files[0]
            safe_name = clean_filename(file)
            safe_path = os.path.join(tmpdir, safe_name)
            if safe_name != file and os.path.exists(os.path.join(tmpdir, file)):
                os.rename(os.path.join(tmpdir, file), safe_path)
            
            title = clean_filename(entry.get("title", "Без назви"))
            log_action(type('obj', (object,), {'username': 'system', 'id': 0})(), f"Успішно завантажено: {title[:50]}")
            return safe_path, title, tmpdir
    except Exception as e:
        log_action(type('obj', (object,), {'username': 'system', 'id': 0})(), f"❌ Помилка yt_dlp: {e}")
        print(f"❌ Download error: {e}")
        if tmpdir and os.path.isdir(tmpdir):
            shutil.rmtree(tmpdir)
        return None, None, None

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_blocked(update, context):
        return ConversationHandler.END
    log_action(update.effective_user, "Скасував операцію")
    await update.message.reply_text("Скасовано")
    return ConversationHandler.END

async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_blocked(update, context):
        return ConversationHandler.END
    log_action(update.effective_user, "Перезапустив бота")
    context.user_data.clear()
    await update.message.reply_text("Перезапуск. Введіть /start")
    return ConversationHandler.END

# ================= ІГРИ ТА ЕКОНОМІКА =================
async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id):
        return
    if await check_blocked(update, context):
        return
    if not await is_user_subscribed(update, context):
        return
    log_action(update.effective_user, "Перевірив /balance")
    stats = get_user_stats(update.effective_user.id)
    vip_status = "👑 VIP" if is_vip_active(update.effective_user.id) else "Звичайний"
    text = f"💰 *Баланс:* {stats['stars']} ⭐\n"
    text += f"👑 *Статус:* {vip_status}"
    if stats.get("vip_expiration") and datetime.now() < (datetime.fromisoformat(stats["vip_expiration"]) if isinstance(stats["vip_expiration"], str) else stats["vip_expiration"]):
        exp = datetime.fromisoformat(stats["vip_expiration"]) if isinstance(stats["vip_expiration"], str) else stats["vip_expiration"]
        text += f" (до {exp.strftime('%d.%m %H:%M')})"
    if is_unlimited_active(update.effective_user.id):
        unlim = stats["unlimited_dl_expires"]
        if isinstance(unlim, str):
            unlim = datetime.fromisoformat(unlim)
        text += f"\n♾ Безліміт до: {unlim.strftime('%d.%m %H:%M')}"
    await update.message.reply_markdown(text)

async def promo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id):
        return
    if await check_blocked(update, context):
        return
    if not await is_user_subscribed(update, context):
        return
    log_action(update.effective_user, f"Використав промокод: {context.args}")
    if not context.args:
        await update.message.reply_text("❓ Приклад: `/promo CODE`")
        return
    code = context.args[0].upper()
    promo = promocodes.get(code)
    stats = get_user_stats(update.effective_user.id)
    if not promo:
        await update.message.reply_text(f"❌ Промокод {code} не знайдено")
        return
    if datetime.now() > promo["expires"]:
        await update.message.reply_text(f"❌ Промокод {code} закінчився")
        del promocodes[code]
        save_data()
        return
    if promo["uses"] <= 0:
        await update.message.reply_text(f"❌ Промокод {code} використано")
        return
    if code in stats.get("used_promos", []):
        await update.message.reply_text(f"❌ Ви вже використовували {code}")
        return
    reward = promo["reward"]
    stats["stars"] += reward
    stats["used_promos"].append(code)
    promo["uses"] -= 1
    await update.message.reply_text(f"✅ Промокод {code}! +{reward} ⭐")
    save_data()

async def dice_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id):
        return
    if await check_blocked(update, context):
        return
    if not await is_user_subscribed(update, context):
        return
    log_action(update.effective_user, f"Грав в /dice: {context.args}")
    stats = get_user_stats(update.effective_user.id)
    current_stars = stats.get("stars", 50)
    if current_stars == 0:
        await update.message.reply_text("❌ У тебе немає зірок!")
        return
    bet = 10
    if context.args:
        try:
            bet = int(context.args[0])
            if bet <= 0:
                await update.message.reply_text("❗️ Ставка > 0")
                return
        except:
            await update.message.reply_text("❗️ Ставка > 0")
            return
    if current_stars < bet:
        await update.message.reply_markdown(f"❌ Недостатньо ⭐. Баланс: {current_stars}")
        return
    sent_dice = await update.message.reply_dice(emoji="🎲")
    dice_value = sent_dice.dice.value
    response = f"🎲 Випало: {dice_value}!"
    if dice_value == 6:
        win_amount = bet * 2
        stats["stars"] += win_amount
        response += f"\n🎉 Виграв {win_amount} ⭐! Баланс: {stats['stars']}"
    elif dice_value == 1:
        stats["stars"] -= bet
        response += f"\n💔 Програв {bet} ⭐. Баланс: {stats['stars']}"
    else:
        response += f"\n⚖️ Ставка повернута. Баланс: {stats['stars']}"
    await asyncio.sleep(4)
    await update.message.reply_markdown(response)
    save_data()

async def flipcoin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id):
        return
    if await check_blocked(update, context):
        return
    if not await is_user_subscribed(update, context):
        return
    log_action(update.effective_user, f"Грав в /flipcoin: {context.args}")
    if len(context.args) < 2:
        await update.message.reply_markdown("❓ Приклад: `/flipcoin 20 орел`")
        return
    try:
        bet = int(context.args[0])
        choice = context.args[1].lower()
    except:
        await update.message.reply_markdown("❗️ Ставка > 0")
        return
    if bet <= 0:
        await update.message.reply_markdown("❗️ Ставка > 0")
        return
    if choice not in ['орел', 'решка', 'heads', 'tails']:
        await update.message.reply_markdown("❗️ орел або решка")
        return
    stats = get_user_stats(update.effective_user.id)
    if stats["stars"] < bet:
        await update.message.reply_markdown(f"❌ Недостатньо ⭐. Баланс: {stats['stars']}")
        return
    result = random.choice(['орел', 'решка'])
    is_win = (choice in ['орел', 'heads'] and result == 'орел') or (choice in ['решка', 'tails'] and result == 'решка')
    await update.message.reply_markdown(f"🎲 Випало: *{result}*!")
    await asyncio.sleep(1)
    if is_win:
        stats["stars"] += bet
        await update.message.reply_markdown(f"🎉 Виграв {bet} ⭐! Баланс: {stats['stars']}")
    else:
        stats["stars"] -= bet
        await update.message.reply_markdown(f"💔 Програв {bet} ⭐. Баланс: {stats['stars']}")
    save_data()

# ================= DUEL =================
async def duel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id):
        return
    if await check_blocked(update, context):
        return
    if not await is_user_subscribed(update, context):
        return
    log_action(update.effective_user, f"Створив дуель: {context.args}")
    if len(context.args) < 2:
        await update.message.reply_markdown("❓ Приклад: `/duel 123456 50`")
        return
    try:
        opponent_id = int(context.args[0].replace('@', ''))
        bet = int(context.args[1])
    except:
        await update.message.reply_markdown("❗️ Ставка > 0")
        return
    if bet <= 0:
        await update.message.reply_markdown("❗️ Ставка > 0")
        return
    user = update.effective_user
    if user.id == opponent_id:
        await update.message.reply_markdown("❌ Не можна грати з собою")
        return
    challenger_stats = get_user_stats(user.id)
    if challenger_stats["stars"] < bet:
        await update.message.reply_markdown(f"❌ Недостатньо ⭐. Баланс: {challenger_stats['stars']}")
        return
    try:
        opponent_user = await context.bot.get_chat(opponent_id)
        opponent_stats = get_user_stats(opponent_id)
    except:
        await update.message.reply_markdown(f"❌ Користувач не знайдено")
        return
    if opponent_stats["stars"] < bet:
        username = opponent_user.username or opponent_user.first_name
        await update.message.reply_markdown(f"❌ У @{username} недостатньо ⭐")
        return
    duel_id = base64.urlsafe_b64encode(os.urandom(6)).decode('utf-8')
    duel_data[duel_id] = {
        'challenger_id': user.id,
        'opponent_id': opponent_id,
        'bet': bet,
        'challenger_chat_id': update.message.chat_id
    }
    try:
        keyboard = [[
            InlineKeyboardButton("Прийняти", callback_data=f"duel_accept_{duel_id}"),
            InlineKeyboardButton("Відхилити", callback_data=f"duel_decline_{duel_id}")
        ]]
        challenger_name = user.username or user.first_name
        await context.bot.send_message(
            chat_id=opponent_id,
            text=f"⚔️ @{challenger_name} викликає на дуель! Ставка: {bet} ⭐",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        await update.message.reply_text(f"⚔️ Запрошення надіслано")
    except Exception as e:
        log_action(update.effective_user, f"Помилка створення дуелі: {e}")
        await update.message.reply_text(f"❌ Помилка: {e}")
        if duel_id in duel_data:
            del duel_data[duel_id]

async def duel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split('_')
    action = parts[1]
    duel_id = parts[2]
    user_id = query.from_user.id
    log_action(query.from_user, f"Дуель {action}: {duel_id}")
    if duel_id not in duel_data:
        await query.edit_message_text("❌ Дуель неактуальна")
        return
    duel = duel_data[duel_id]
    if user_id != duel['opponent_id']:
        await query.answer("Це не ваш виклик!", show_alert=True)
        return
    challenger_id = duel['challenger_id']
    opponent_id = duel['opponent_id']
    bet = duel['bet']
    challenger_chat_id = duel['challenger_chat_id']
    try:
        challenger_user = await context.bot.get_chat(challenger_id)
        opponent_user = await context.bot.get_chat(opponent_id)
    except:
        await query.edit_message_text("❌ Помилка")
        if duel_id in duel_data:
            del duel_data[duel_id]
        return
    if action == "accept":
        challenger_stats = get_user_stats(challenger_id)
        opponent_stats = get_user_stats(opponent_id)
        if challenger_stats["stars"] < bet or opponent_stats["stars"] < bet:
            await query.edit_message_text("❌ Недостатньо ⭐")
            if duel_id in duel_data:
                del duel_data[duel_id]
            return
        await query.edit_message_text("✅ Прийнято!")
        await context.bot.send_message(chat_id=challenger_chat_id, text="✅ Суперник прийняв!")
        await asyncio.sleep(1)
        challenger_name = challenger_user.username or challenger_user.first_name
        opponent_name = opponent_user.username or opponent_user.first_name
        await context.bot.send_message(
            chat_id=challenger_chat_id,
            text=f"🔥 Дуель! @{challenger_name} vs @{opponent_name}, ставка: {bet} ⭐"
        )
        await asyncio.sleep(1)
        challenger_roll = random.randint(1, 6)
        opponent_roll = random.randint(1, 6)
        await context.bot.send_message(chat_id=challenger_chat_id, text=f"🎲 @{challenger_name}: {challenger_roll}!")
        await asyncio.sleep(1)
        await context.bot.send_message(chat_id=challenger_chat_id, text=f"🎲 @{opponent_name}: {opponent_roll}!")
        await asyncio.sleep(1)
        if challenger_roll > opponent_roll:
            winner_id, winner_name = challenger_id, challenger_name
            loser_id = opponent_id
        elif opponent_roll > challenger_roll:
            winner_id, winner_name = opponent_id, opponent_name
            loser_id = challenger_id
        else:
            winner_id = None
        if winner_id:
            get_user_stats(winner_id)["stars"] += bet
            get_user_stats(loser_id)["stars"] -= bet
            await context.bot.send_message(chat_id=challenger_chat_id, text=f"🏆 Переможець: @{winner_name}! +{bet} ⭐")
        else:
            await context.bot.send_message(chat_id=challenger_chat_id, text="🤝 Нічия!")
        save_data()
    elif action == "decline":
        await query.edit_message_text("❌ Відхилено")
        challenger_name = challenger_user.username or challenger_user.first_name
        await context.bot.send_message(chat_id=challenger_chat_id, text=f"❌ @{challenger_name} відмовився")
        if duel_id in duel_data:
            del duel_data[duel_id]

# ================= АДМІН КОМАНДИ =================
async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    log_action(update.effective_user, "Використав /adminhelp")
    text = """👑 Адмін-панель:
`/add_stars <ID> <кількість>`
`/remove_stars <ID> <кількість>`
`/set_downloads <ID> <кількість>`
`/user_stats <ID>`
`/block <ID>`
`/unblock <ID>`
`/grant_vip <ID>`
`/revoke_vip <ID>`
`/send_to <ID> <текст>`
`/broadcast <текст>`
`/bot_stats`
`/create_promo <код> <зірки> <рази> <дні>`
`/delete_promo <код>`
`/list_promos`
`/set_channel @username`
`/remove_channel @username`
`/list_channels`
`/unset_channel`"""
    await update.message.reply_markdown(text)

async def add_stars(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    log_action(update.effective_user, f"Додав зірки: {context.args}")
    try:
        user_id = int(context.args[0])
        amount = int(context.args[1])
    except:
        await update.message.reply_markdown("❌ Формат: `/add_stars <ID> <кількість>`")
        return
    stats = get_user_stats(user_id)
    stats["stars"] += amount
    await update.message.reply_markdown(f"✅ +{amount} ⭐ користувачу {user_id}. Баланс: {stats['stars']}")
    save_data()

async def remove_stars(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    log_action(update.effective_user, f"Забрав зірки: {context.args}")
    try:
        user_id = int(context.args[0])
        amount = int(context.args[1])
    except:
        await update.message.reply_markdown("❌ Формат: `/remove_stars <ID> <кількість>`")
        return
    if user_id not in user_data:
        await update.message.reply_markdown(f"❌ Користувач {user_id} не знайдено")
        return
    stats = get_user_stats(user_id)
    stats["stars"] = max(0, stats["stars"] - amount)
    await update.message.reply_markdown(f"✅ -{amount} ⭐ у {user_id}. Баланс: {stats['stars']}")
    save_data()

async def set_downloads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    log_action(update.effective_user, f"Встановив завантаження: {context.args}")
    try:
        user_id = int(context.args[0])
        count = int(context.args[1])
    except:
        await update.message.reply_markdown("❌ Формат: `/set_downloads <ID> <кількість>`")
        return
    stats = get_user_stats(user_id)
    stats["downloads"] = count
    await update.message.reply_text(f"✅ Встановлено {count} завантажень для {user_id}")
    save_data()

async def send_to(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    log_action(update.effective_user, f"Надіслав повідомлення: {context.args[0]}")
    try:
        user_id = int(context.args[0])
        message_text = " ".join(context.args[1:])
        if not message_text:
            raise IndexError
    except:
        await update.message.reply_markdown("❌ Формат: `/send_to <ID> <текст>`")
        return
    try:
        await context.bot.send_message(chat_id=user_id, text=message_text)
        await update.message.reply_text(f"✅ Надіслано {user_id}")
    except Exception as e:
        await update.message.reply_text(f"❌ Помилка: {e}")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    log_action(update.effective_user, "Почав розсилку")
    if not context.args:
        await update.message.reply_markdown("❓ Використання: `/broadcast <текст>`")
        return
    message_text = " ".join(context.args)
    await update.message.reply_text("✅ Початок розсилки...")
    success_count = 0
    fail_count = 0
    for user_id in list(user_data.keys()):
        try:
            await context.bot.send_message(chat_id=user_id, text=message_text)
            success_count += 1
            await asyncio.sleep(0.1)
        except:
            fail_count += 1
    await update.message.reply_text(f"✅ Завершено\nНадіслано: {success_count}\nНе вдалося: {fail_count}")

async def bot_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    log_action(update.effective_user, "Перевірив /bot_stats")
    total_users = len(user_data)
    total_downloads = sum(s.get("downloads", 0) for s in user_data.values())
    total_tracks = sum(s.get("tracks", 0) for s in user_data.values())
    total_videos = sum(s.get("videos", 0) for s in user_data.values())
    all_sources = {}
    for stats in user_data.values():
        for source, count in stats.get("source_counts", {}).items():
            all_sources[source] = all_sources.get(source, 0) + count
    most_popular = max(all_sources, key=all_sources.get).upper() if all_sources else "N/A"
    text = f"📊 *Статистика*:\n"
    text += f"Користувачів: {total_users}\n"
    text += f"Завантажень: {total_downloads}\n"
    text += f"Треків: {total_tracks}\n"
    text += f"Відео: {total_videos}\n"
    text += f"Джерело: {most_popular}"
    await update.message.reply_markdown(text)

async def user_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    log_action(update.effective_user, f"Перевірив статистику: {context.args}")
    try:
        user_id = int(context.args[0])
    except:
        await update.message.reply_markdown("❌ Формат: `/user_stats <ID>`")
        return
    await display_user_stats(update.message, context, user_id)

async def display_user_stats(message, context, user_id):
    if user_id not in user_data:
        await message.reply_markdown(f"❌ Користувач {user_id} не знайдено")
        return
    stats = get_user_stats(user_id)
    try:
        user_info = await context.bot.get_chat(user_id)
        username = user_info.username or user_info.first_name
    except:
        username = f"ID {user_id}"
    vip = "Так" if is_vip_active(user_id) else "Ні"
    level = calculate_level(stats['downloads'])
    text = f"📊 *Статистика @{username} (ID: {user_id}):*\n"
    text += f"👑 VIP: {vip}\n"
    text += f"🌟 Рівень: {level}\n"
    text += f"💰 Баланс: {stats['stars']} ⭐\n"
    text += f"⬇️ Завантажень: {stats['downloads']}\n"
    text += f"🎵 Треків: {stats['tracks']}\n"
    text += f"🎬 Відео: {stats['videos']}\n"
    text += f"📌 Джерело: {stats['source'].upper() if stats['source'] != 'N/A' else 'N/A'}\n"
    text += f"🚫 Заблокований: {'Так' if stats['is_blocked'] else 'Ні'}"
    await message.reply_markdown(text)

async def block_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    log_action(update.effective_user, f"Заблокував: {context.args}")
    try:
        user_id = int(context.args[0])
    except:
        await update.message.reply_markdown("❌ Формат: `/block <ID>`")
        return
    get_user_stats(user_id)["is_blocked"] = True
    await update.message.reply_text(f"✅ Заблоковано {user_id}")
    save_data()

async def unblock_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    log_action(update.effective_user, f"Розблокував: {context.args}")
    try:
        user_id = int(context.args[0])
    except:
        await update.message.reply_markdown("❌ Формат: `/unblock <ID>`")
        return
    get_user_stats(user_id)["is_blocked"] = False
    await update.message.reply_text(f"✅ Розблоковано {user_id}")
    save_data()

async def grant_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    log_action(update.effective_user, f"Надав VIP: {context.args}")
    try:
        user_id = int(context.args[0])
    except:
        await update.message.reply_markdown("❌ Формат: `/grant_vip <ID>`")
        return
    stats = get_user_stats(user_id)
    stats["is_vip"] = True
    await update.message.reply_text(f"✅ VIP надано {user_id}")
    save_data()

async def revoke_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    log_action(update.effective_user, f"Забрав VIP: {context.args}")
    try:
        user_id = int(context.args[0])
    except:
        await update.message.reply_markdown("❌ Формат: `/revoke_vip <ID>`")
        return
    stats = get_user_stats(user_id)
    stats["is_vip"] = False
    stats["vip_expiration"] = None
    await update.message.reply_text(f"✅ VIP забрано у {user_id}")
    save_data()

async def create_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    log_action(update.effective_user, f"Створив промокод: {context.args}")
    try:
        code = context.args[0].upper()
        reward = int(context.args[1])
        uses = int(context.args[2])
        days = int(context.args[3])
    except:
        await update.message.reply_markdown("❌ Формат: `/create_promo код зірки рази дні`")
        return
    expires = datetime.now() + timedelta(days=days)
    promocodes[code] = {"reward": reward, "uses": uses, "expires": expires}
    await update.message.reply_text(f"✅ Промокод {code}: {reward}⭐, {uses} раз(и), до {expires.strftime('%Y-%m-%d %H:%M')}")
    save_data()

async def delete_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    log_action(update.effective_user, f"Видалив промокод: {context.args}")
    try:
        code = context.args[0].upper()
    except:
        await update.message.reply_markdown("❌ Формат: `/delete_promo код`")
        return
    if code in promocodes:
        del promocodes[code]
        await update.message.reply_text(f"✅ Промокод {code} видалено")
        save_data()
    else:
        await update.message.reply_text(f"❌ Промокод {code} не знайдено")

async def list_promos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    log_action(update.effective_user, "Перевірив список промокодів")
    active_promos = {k: v for k, v in promocodes.items() if v['expires'] > datetime.now() and v['uses'] > 0}
    if not active_promos:
        await update.message.reply_text("😕 Немає промокодів")
        return
    response = "📜 *Промокоди*:\n"
    for code, data in active_promos.items():
        expires_str = data['expires'].strftime('%Y-%m-%d %H:%M')
        response += f"`{code}`: {data['reward']}⭐, {data['uses']} раз(и), до {expires_str}\n"
    await update.message.reply_markdown(response)

async def set_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    log_action(update.effective_user, f"Додав канал: {context.args}")
    try:
        username = context.args[0]
        if not username.startswith('@'):
            raise IndexError
    except:
        await update.message.reply_markdown("❌ Формат: `/set_channel @username`")
        return
    try:
        chat = await context.bot.get_chat(chat_id=username)
        for ch in required_channels:
            if ch['id'] == chat.id:
                await update.message.reply_text("⚠️ Канал вже додано")
                return
        required_channels.append({'id': chat.id, 'username': username})
        await update.message.reply_text(f"✅ Канал {username} додано")
        save_data()
    except Exception:
        await update.message.reply_text(f"❌ Не знайдено канал {username}")

async def remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    log_action(update.effective_user, f"Видалив канал: {context.args}")
    try:
        username = context.args[0]
        if not username.startswith('@'):
            raise IndexError
    except:
        await update.message.reply_markdown("❌ Формат: `/remove_channel @username`")
        return
    global required_channels
    initial_len = len(required_channels)
    required_channels = [ch for ch in required_channels if ch['username'] != username]
    if len(required_channels) < initial_len:
        await update.message.reply_text(f"✅ Канал {username} видалено")
        save_data()
    else:
        await update.message.reply_text("❌ Канал не знайдено")

async def list_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    log_action(update.effective_user, "Перевірив список каналів")
    if not required_channels:
        await update.message.reply_text("📋 Каналів немає")
        return
    channels_list = "\n".join([f"- {ch['username']}" for ch in required_channels])
    await update.message.reply_markdown(f"📋 *Канали*:\n{channels_list}")

async def unset_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    log_action(update.effective_user, "Вимкнув підписку")
    global required_channels
    required_channels.clear()
    await update.message.reply_text("✅ Підписку вимкнено")
    save_data()

# ================= АДМІН МЕНЮ =================
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    log_action(update.effective_user, "Відкрив адмін меню")
    keyboard = [
        [InlineKeyboardButton("➕ Додати ⭐", callback_data="admin_add_stars")],
        [InlineKeyboardButton("➖ Забрати ⭐", callback_data="admin_remove_stars")],
        [InlineKeyboardButton("📊 Завантаження", callback_data="admin_set_downloads")],
        [InlineKeyboardButton("👤 Статистика", callback_data="admin_user_stats")],
        [InlineKeyboardButton("📖 Допомога", callback_data="admin_help")],
        [InlineKeyboardButton("⬅️ Вихід", callback_data="admin_exit")]
    ]
    await update.message.reply_markdown("👑 *Адмін-меню*", reply_markup=InlineKeyboardMarkup(keyboard))
    return ADMIN_MENU

async def admin_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data
    log_action(query.from_user, f"Адмін меню: {action}")
    if action == "admin_add_stars":
        await query.message.reply_text("Введіть: `ID кількість`")
        return AWAIT_ADD_STARS
    elif action == "admin_remove_stars":
        await query.message.reply_text("Введіть: `ID кількість`")
        return AWAIT_REMOVE_STARS
    elif action == "admin_set_downloads":
        await query.message.reply_text("Введіть ID")
        return AWAIT_SET_DOWNLOADS_ID
    elif action == "admin_user_stats":
        await query.message.reply_text("Введіть ID")
        return AWAIT_USER_STATS
    elif action == "admin_help":
        await query.message.reply_markdown("""👑 Адмін-панель:
`/add_stars <ID> <кількість>`
`/remove_stars <ID> <кількість>`
`/set_downloads <ID> <кількість>`
`/user_stats <ID>`
`/block <ID>`
`/unblock <ID>`
`/grant_vip <ID>`
`/revoke_vip <ID>`
`/send_to <ID> <текст>`
`/broadcast <текст>`
`/bot_stats`
`/create_promo <код> <зірки> <рази> <дні>`
`/delete_promo <код>`
`/list_promos`
`/set_channel @username`
`/remove_channel @username`
`/list_channels`
`/unset_channel`""")
        return ADMIN_MENU
    elif action == "admin_exit":
        await query.message.edit_text("Скасовано")
        return ConversationHandler.END

async def admin_add_stars_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id, amount = map(int, update.message.text.split())
        context.args = [user_id, amount]
        await add_stars(update, context)
    except:
        await update.message.reply_text("❌ Некоректно")
    return ConversationHandler.END

async def admin_remove_stars_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id, amount = map(int, update.message.text.split())
        context.args = [user_id, amount]
        await remove_stars(update, context)
    except:
        await update.message.reply_text("❌ Некоректно")
    return ConversationHandler.END

async def admin_user_stats_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = int(update.message.text)
        await display_user_stats(update.message, context, user_id)
    except:
        await update.message.reply_text("❌ Некоректно")
    return ConversationHandler.END

async def admin_set_downloads_id_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = int(update.message.text)
        get_user_stats(user_id)
        context.user_data['admin_target_user'] = user_id
        await update.message.reply_text(f"Введіть кількість для {user_id}")
        return AWAIT_SET_DOWNLOADS_COUNT
    except:
        await update.message.reply_text("❌ Некоректно")
        return ConversationHandler.END

async def admin_set_downloads_count_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        count = int(update.message.text)
        user_id = context.user_data.pop('admin_target_user', None)
        if not user_id:
            await update.message.reply_text("Сталася помилка")
            return ConversationHandler.END
        context.args = [user_id, count]
        await set_downloads(update, context)
    except:
        await update.message.reply_text("❌ Некоректно")
    return ConversationHandler.END

async def admin_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Скасовано")
    return ConversationHandler.END

# ================= PERIODIC SAVE =================
async def periodic_save():
    while True:
        await asyncio.sleep(60)
        save_data()

# ================= MAIN =================
async def main():
    global application
    load_data()
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Handlers
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("ping", ping))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("lang", lang_command))
    application.add_handler(CallbackQueryHandler(set_lang_callback, pattern=r"^lang_"))
    application.add_handler(CommandHandler("find", find))
    application.add_handler(CommandHandler("support", support))
    application.add_handler(CommandHandler("level", level_command))
    application.add_handler(CommandHandler("topusers", top_users))
    application.add_handler(CommandHandler("genre", genre_filter))
    application.add_handler(CommandHandler("random", random_track))
    application.add_handler(CommandHandler("achievements", achievements_command))
    application.add_handler(CommandHandler("balance", balance_command))
    application.add_handler(CommandHandler("dice", dice_command))
    application.add_handler(CommandHandler("flipcoin", flipcoin_command))
    application.add_handler(CommandHandler("duel", duel_command))
    application.add_handler(CallbackQueryHandler(duel_callback, pattern=r"^duel_"))
    application.add_handler(CommandHandler("promo", promo_command))
    application.add_handler(CommandHandler("shop", shop_command))
    application.add_handler(CallbackQueryHandler(shop_callback, pattern=r"^shop_"))
    
    # Admin commands
    application.add_handler(CommandHandler("adminhelp", admin_help))
    application.add_handler(CommandHandler("add_stars", add_stars))
    application.add_handler(CommandHandler("remove_stars", remove_stars))
    application.add_handler(CommandHandler("set_downloads", set_downloads))
    application.add_handler(CommandHandler("send_to", send_to))
    application.add_handler(CommandHandler("broadcast", broadcast))
    application.add_handler(CommandHandler("bot_stats", bot_stats))
    application.add_handler(CommandHandler("user_stats", user_stats_command))
    application.add_handler(CommandHandler("block", block_user))
    application.add_handler(CommandHandler("unblock", unblock_user))
    application.add_handler(CommandHandler("grant_vip", grant_vip))
    application.add_handler(CommandHandler("revoke_vip", revoke_vip))
    application.add_handler(CommandHandler("create_promo", create_promo))
    application.add_handler(CommandHandler("delete_promo", delete_promo))
    application.add_handler(CommandHandler("list_promos", list_promos))
    application.add_handler(CommandHandler("set_channel", set_channel))
    application.add_handler(CommandHandler("remove_channel", remove_channel))
    application.add_handler(CommandHandler("list_channels", list_channels))
    application.add_handler(CommandHandler("unset_channel", unset_channel))
    
    # Conversation handlers
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SELECTING: [CallbackQueryHandler(select_type, pattern=r'^(audio|video)$')],
            SELECT_SOURCE: [CallbackQueryHandler(select_source, pattern=r'^(yt|sc|tt)$')],
            ASK_QUERY: [CallbackQueryHandler(select_quality, pattern=r'^\d{3,4}$')],
            DOWNLOAD: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_download)]
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("restart", restart)],
        per_message=False
    )
    
    admin_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("admin", admin_command)],
        states={
            ADMIN_MENU: [CallbackQueryHandler(admin_menu_callback, pattern=r'^admin_')],
            AWAIT_ADD_STARS: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_stars_input)],
            AWAIT_REMOVE_STARS: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_remove_stars_input)],
            AWAIT_USER_STATS: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_user_stats_input)],
            AWAIT_SET_DOWNLOADS_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_set_downloads_id_input)],
            AWAIT_SET_DOWNLOADS_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_set_downloads_count_input)]
        },
        fallbacks=[CommandHandler("cancel", admin_cancel)],
        per_message=False
    )
    
    application.add_handler(conv_handler)
    application.add_handler(admin_conv_handler)
    
    print("🤖 Бот активний!")
    log_action(type('obj', (object,), {'username': 'system', 'id': 0})(), "Бот запущено")
    asyncio.create_task(process_queue())
    asyncio.create_task(periodic_save())
    await application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    asyncio.run(main())
