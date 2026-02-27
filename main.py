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
    CallbackQueryHandler, ConversationHandler, ContextTypes, filters, InlineQueryHandler, ChosenInlineResultHandler
)
import yt_dlp
import nest_asyncio
from telegram.error import TimedOut, BadRequest

nest_asyncio.apply()

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
required_channels = []  # Тепер список каналів
last_activity = {}

# --- Ціни ---
SHOP_PRICES = {
    "vip_1_day": 200,
    "vip_7_days": 1000,
    "vip_30_days": 3500,
    "unlimited_24h": 500,
    "priority_pass": 50
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
        
        # Конвертуємо user_data
        for uid, stats in user_data.items():
            data_to_save['user_data'][uid] = stats.copy()
            if stats.get('vip_expiration') and isinstance(stats['vip_expiration'], datetime):
                data_to_save['user_data'][uid]['vip_expiration'] = stats['vip_expiration'].isoformat()
            if stats.get('unlimited_dl_expires') and isinstance(stats['unlimited_dl_expires'], datetime):
                data_to_save['user_data'][uid]['unlimited_dl_expires'] = stats['unlimited_dl_expires'].isoformat()
        
        # Конвертуємо promocodes
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
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            global user_data, promocodes, required_channels
            user_data = data.get('user_data', {})
            required_channels = data.get('required_channels', [])
            
            # Конвертуємо promocodes
            raw_promos = data.get('promocodes', {})
            for code, pdata in raw_promos.items():
                if pdata.get('expires') and isinstance(pdata['expires'], str):
                    pdata['expires'] = datetime.fromisoformat(pdata['expires'])
                promocodes[code] = pdata
            
            # Конвертуємо user_data
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

def get_text(context: ContextTypes.DEFAULT_TYPE, key: str) -> str:
    lang = context.user_data.get("lang", "ua")
    return LANGUAGES.get(lang, LANGUAGES["ua"]).get(key, f"_{key}_")

def log_action(user, action: str):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    name = user.username or user.full_name or "Unknown"
    print(f"🕒 {now} | 👤 {name} | 🆔 {user.id} | 📌 {action}")

def clean_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", name)

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
    
    # Міграція даних
    for key, default in [("is_vip", False), ("vip_expiration", None), ("used_promos", []),
                         ("has_channel_reward", False), ("stars", 50), 
                         ("unlimited_dl_expires", None), ("priority_passes", 0)]:
        if key not in stats:
            stats[key] = default
    
    return stats

def is_vip_active(user_id):
    stats = get_user_stats(user_id)
    if stats.get("is_vip", False):
        return True
    
    vip_exp = stats.get("vip_expiration")
    if vip_exp:
        # Конвертуємо якщо це строка
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
        except Exception:
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
        # Видаємо нагороду якщо ще не отримували
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
                text=f"✅ Дякуємо за підписку! Ви отримали:\n➕ {reward} зірок ⭐\n VIP-статус на 1 день!"
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
            await update.message.reply_text(f"🎉 *Нове досягнення: {achievement_name}!* 🎉", parse_mode="Markdown")
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

# ================= СИСТЕМА МОВ =================
LANGUAGES = {
    "ua": {
        "start_greeting": "Привіт! Я допоможу тобі завантажити 🎵 музику або 🎬 відео з YouTube, SoundCloud або TikTok.\n📌 Натисни кнопку нижче, щоб почати:",
        "start_button_audio": "🎵 Музика",
        "start_button_video": "🎬 Відео",
        "help_text": "📖 *Довідка*\n*Підтримувані джерела:*\n- YouTube (аудіо, відео)\n- SoundCloud (аудіо)\n- TikTok (відео)\n*Основні команди:*\n`start` — запуск\n`help` — допомога\n`shop` — магазин\n`cancel` — скасувати\n`restart` — перезапуск\n`ping` — перевірка\n`stats` — статистика\n`lang` — мова\n`find` — пошук\n`support` — підтримка\n`level` — рівень\n`achievements` — досягнення\n`topusers` — топ\n`random` — випадковий трек\n`promo <code>` — промокод\n`balance` — баланс\n`dice <ставка>` — кубик\n`flipcoin <ставка> <вибір>` — монетка\n`duel <ID> <ставка>` — дуель",
        "ping_success": "✅ Бот активний!",
        "stats_text": "📊 *Твоя статистика:*\n👑 Статус: {vip_status}\n🎵 Треків: {tracks}\n🎬 Відео: {videos}\n📌 Джерело: {source}",
        "lang_select": "🌐 Обери мову:",
        "support_text": "💬 Підтримка: https://t.me/MyDownloaderSupport",
        "level_text": "🌟 *Твій рівень: {level}*\nЗавантажено: {downloads}\nДо наступного: {needed}",
        "topusers_empty": "📊 Ще немає статистики!",
        "topusers_text": "🏆 *Топ-5:*\n",
        "genre_empty": "❓ Вкажіть жанр. Приклад: `/genre рок`",
        "genre_set": "✅ Жанр встановлено: *{genre}*",
        "random_track_searching": "🎧 Знаходжу трек...",
        "random_track_caption": "🎵 *Випадковий трек:*\n{title}",
        "error_downloading": "❌ Помилка: {e}",
        "find_empty": "❓ Напишіть опис. Приклад: `/find пісня з фільму`",
        "find_searching": "🔍 Шукаю: {query}",
        "find_caption": "🎵 {title}",
        "find_error": "❌ Помилка пошуку: {e}",
        "select_source_text": "🔍 Обери джерело:",
        "select_quality_text": "🎚 Обери якість:",
        "ask_query_text": "📥 Надішли посилання або назву:",
        "download_started": "🔄 Завантаження...",
        "file_too_large": "⚠️ Файл занадто великий (>50MB)",
        "download_complete": "✅ Готово!",
        "sent_audio_caption": "🎵 {title}",
        "sent_video_caption": "🎬 {title}",
        "sent_doc_caption": "📎 {title}",
        "download_error": "❌ Помилка: {e}",
        "cancelled": "Скасовано",
        "restart_message": "Перезапуск. Введіть /start",
        "achievements_text": "🏆 *Досягнення:*\n",
        "achievement_unlocked": "🎉 *Нове досягнення: {name}!*",
        "achievement_no_achievements": "😕 Поки немає досягнень",
        "lang_changed": "🌐 Мова: {lang}",
        "inline_downloading": "📥 Завантаження...",
        "inline_sent": "✅ Відправлено!",
        "inline_error": "❌ Помилка",
        "inline_no_results": "⚠️ Нічого не знайдено",
        "group_search_started": "🔍 Шукаю: {query}...",
        "no_results_found": "😕 Нічого не знайдено для '{query}'",
        "balance_text": "💰 *Баланс:* {stars} ⭐\n *Статус:* {vip_status}",
        "dice_roll": "🎲 Випало: {value}!",
        "dice_win": "🎉 Виграв {win_amount} ⭐! Баланс: {stars}",
        "dice_lose": "💔 Програв {lost_amount} ⭐. Баланс: {stars}",
        "dice_neutral": "⚖️ Випало {value}. Ставка повернута. Баланс: {stars}",
        "dice_no_money": "❌ Недостатньо зірок. Баланс: {stars}",
        "dice_invalid_bet": "❗️ Ставка має бути > 0",
        "queue_add": "🔄 В черзі. Позиція: {pos}. Пріоритет: {priority}",
        "queue_start": "🚀 Починаю...",
        "not_enough_stars_find": "❌ Потрібно {cost} ⭐. Баланс: {stars}",
        "not_enough_stars_random": "❌ Потрібно {cost} ⭐. Баланс: {stars}",
        "not_enough_stars_download": "❌ Потрібно {cost} ⭐. Баланс: {stars}",
        "blocked_user_message": "❌ Акаунт заблоковано",
        "vip_status_active": "👑 VIP",
        "vip_status_inactive": "Звичайний",
        "spam_warning": "⏳ Зачекайте трохи",
        "shop_title": "🛒 *Магазин*\nБаланс: {stars} ⭐",
        "shop_vip_1": "👑 VIP 1 день ({cost}⭐)",
        "shop_vip_7": "👑 VIP 7 днів ({cost}⭐)",
        "shop_vip_30": "👑 VIP 30 днів ({cost}⭐)",
        "shop_unlimited": "♾ Безліміт 24г ({cost}⭐)",
        "shop_priority": "🚀 Пріоритет ({cost}⭐)",
        "shop_success": "✅ Куплено: {item}!",
        "shop_fail": "❌ Недостатньо ⭐. Потрібно: {cost}, Є: {stars}",
        "shop_priority_desc": "Ваш запит буде першим у черзі",
        "must_subscribe": "❗️ Підпишіться на канали",
        "subscribe_button": "➡️ Підписатись",
        "subscription_verified": "✅ Отримано: {reward} ⭐ + VIP на 1 день!",
        "promo_enter": "❓ Приклад: `/promo CODE`",
        "promo_activated": "✅ Промокод {code}! +{reward} ⭐",
        "promo_not_found": "❌ Промокод {code} не знайдено",
        "promo_expired": "❌ Промокод {code} закінчився",
        "promo_no_uses": "❌ Промокод {code} використано",
        "promo_already_used": "❌ Ви вже використовували {code}",
        "flipcoin_empty": "❓ Приклад: `/flipcoin 20 орел`",
        "flipcoin_invalid_bet": "❗️ Ставка > 0",
        "flipcoin_invalid_choice": "❗️ орел або решка",
        "flipcoin_no_money": "❌ Недостатньо ⭐. Баланс: {stars}",
        "flipcoin_result": "🎲 Випало: *{result}*!",
        "flipcoin_win": "🎉 Виграв {win_amount} ⭐! Баланс: {stars}",
        "flipcoin_lose": "💔 Програв {lost_amount} ⭐. Баланс: {stars}",
        "duel_empty": "❓ Приклад: `/duel 123456 50`",
        "duel_invalid_bet": "❗️ Ставка > 0",
        "duel_self": "❌ Не можна грати з собою",
        "duel_no_money": "❌ Недостатньо ⭐. Баланс: {stars}",
        "duel_opponent_no_money": "❌ У @{username} недостатньо ⭐",
        "duel_invite_text": "⚔️ @{challenger} викликає на дуель! Ставка: {bet} ⭐",
        "duel_invite_buttons": "Прийняти,Відхилити",
        "duel_accepted_challenger": "✅ @{opponent} прийняв!",
        "duel_accepted_opponent": "✅ Прийнято!",
        "duel_declined_challenger": "❌ @{opponent} відмовився",
        "duel_declined_opponent": "❌ Відхилено",
        "duel_start": "🔥 Дуель! @{c} vs @{o}, ставка: {bet} ⭐",
        "duel_result": "🎲 @{username}: {roll}!",
        "duel_win": "🏆 Переможець: @{winner}! +{win_amount} ⭐",
        "duel_draw": "🤝 Нічия!",
        "duel_expired": "❌ Дуель неактуальна",
        "admin_help_text": "👑 *Адмін*\n`/add_stars <ID> <кількість>`\n`/remove_stars <ID> <кількість>`\n`/set_downloads <ID> <кількість>`\n`/user_stats <ID>`\n`/block <ID>`\n`/unblock <ID>`\n`/grant_vip <ID>`\n`/revoke_vip <ID>`\n`/send_to <ID> <текст>`\n`/broadcast <текст>`\n`/bot_stats`\n`/create_promo <код> <зірки> <рази> <дні>`\n`/delete_promo <код>`\n`/list_promos`\n`/set_channel @username`\n`/remove_channel @username`\n`/list_channels`\n`/unset_channel`",
        "stars_added": "✅ +{amount} ⭐ користувачу {user_id}. Баланс: {stars}",
        "stars_removed": "✅ -{amount} ⭐ у {user_id}. Баланс: {stars}",
        "user_not_found": "❌ Користувач {user_id} не знайдено",
        "message_sent": "✅ Надіслано {user_id}",
        "broadcast_started": "✅ Початок розсилки...",
        "user_blocked": "✅ Заблоковано {user_id}",
        "user_unblocked": "✅ Розблоковано {user_id}",
        "bot_stats_text": "📊 *Статистика:*\nКористувачів: {total_users}\nЗавантажень: {total_downloads}\nТреків: {total_tracks}\nВідео: {total_videos}\nДжерело: {most_popular_source}",
        "downloads_set": "✅ Встановлено {count} завантажень для {user_id}",
        "admin_menu_title": "👑 *Адмін-меню*",
        "admin_button_add_stars": "➕ Додати ⭐",
        "admin_button_remove_stars": "➖ Забрати ⭐",
        "admin_button_set_downloads": "📊 Завантаження",
        "admin_button_user_stats": "👤 Статистика",
        "admin_button_help": "📖 Допомога",
        "admin_button_exit": "⬅️ Вихід",
        "admin_prompt_add_stars": "Введіть: `ID кількість`",
        "admin_prompt_remove_stars": "Введіть: `ID кількість`",
        "admin_prompt_user_stats": "Введіть ID",
        "admin_prompt_set_downloads_id": "Введіть ID",
        "admin_prompt_set_downloads_count": "Введіть кількість для {user_id}",
        "admin_invalid_input": "❌ Некоректно",
        "admin_action_cancelled": "Скасовано",
        "vip_granted": "✅ VIP надано {user_id}",
        "vip_revoked": "✅ VIP забрано у {user_id}",
        "promo_created": "✅ Промокод {code}: {reward}⭐, {uses} раз(и), до {expires}",
        "promo_create_format": "❌ Формат: `/create_promo код зірки рази дні`",
        "promo_deleted": "✅ Промокод {code} видалено",
        "promo_delete_format": "❌ Формат: `/delete_promo код`",
        "promo_list_empty": "😕 Немає промокодів",
        "promo_list_header": "📜 *Промокоди:*\n",
        "channel_set": "✅ Канал {username} додано",
        "channel_removed": "✅ Канал {username} видалено",
        "channel_list": "📋 *Канали:*\n{channels}",
        "channel_set_error": "❌ Не знайдено канал {username}",
        "channel_set_format": "❌ Формат: `/set_channel @username`",
        "channel_unset": "✅ Підписку вимкнено"
    }
}
LANGUAGES["en"] = {**LANGUAGES["ua"]}

COSTS = {
    "audio": {"128": 10, "192": 15, "256": 20, "base_find": 15, "base_random": 10},
    "video": {"360": 25, "480": 35, "720": 50, "1080": 70}
}

# ================= КОМАНДИ КОРИСТУВАЧА =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id): return
    if await check_blocked(update, context): return ConversationHandler.END
    if not await is_user_subscribed(update, context): return ConversationHandler.END
    
    user = update.effective_user
    log_action(user, "Запустив /start")
    stats = get_user_stats(user.id)
    context.user_data["lang"] = stats.get("lang", "ua")
    
    keyboard = [
        [InlineKeyboardButton("🎵 Музика", callback_data="audio")],
        [InlineKeyboardButton("🎬 Відео", callback_data="video")]
    ]
    await update.message.reply_text("Привіт! Обери тип:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECTING

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id): return
    if await check_blocked(update, context): return
    if not await is_user_subscribed(update, context): return
    await update.message.reply_markdown(get_text(context, "help_text"))

async def achievements_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id): return
    if await check_blocked(update, context): return
    if not await is_user_subscribed(update, context): return
    
    stats = get_user_stats(update.effective_user.id)
    if not stats["achievements"]:
        await update.message.reply_text("😕 Поки немає досягнень")
        return
    
    response = "🏆 *Досягнення:*\n"
    for achievement in stats["achievements"]:
        response += f"- {achievement}\n"
    await update.message.reply_text(response, parse_mode="Markdown")

async def lang_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id): return
    if await check_blocked(update, context): return
    
    keyboard = [
        [InlineKeyboardButton("🇺 Українська", callback_data="lang_ua")],
        [InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")]
    ]
    await update.message.reply_text("🌐 Обери мову:", reply_markup=InlineKeyboardMarkup(keyboard))

async def set_lang_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang_code = query.data.split("_")[1]
    context.user_data["lang"] = lang_code
    get_user_stats(query.from_user.id)["lang"] = lang_code
    await query.edit_message_text(f"🌐 Мова: {lang_code.upper()}")
    save_data()

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id): return
    if await check_blocked(update, context): return
    await update.message.reply_text("✅ Бот активний!")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id): return
    if await check_blocked(update, context): return
    if not await is_user_subscribed(update, context): return
    
    user = update.effective_user
    stats = get_user_stats(user.id)
    vip_status = "👑 VIP" if is_vip_active(user.id) else "Звичайний"
    
    text = f"📊 *Статистика:*\n"
    text += f"👑 Статус: {vip_status}\n"
    text += f"🎵 Треків: {stats['tracks']}\n"
    text += f"🎬 Відео: {stats['videos']}\n"
    text += f"📌 Джерело: {stats['source']}"
    await update.message.reply_markdown(text)

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id): return
    if await check_blocked(update, context): return
    await update.message.reply_text("💬 Підтримка: https://t.me/MyDownloaderSupport")

async def level_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id): return
    if await check_blocked(update, context): return
    if not await is_user_subscribed(update, context): return
    
    stats = get_user_stats(update.effective_user.id)
    level = calculate_level(stats['downloads'])
    needed = (level * 10) - stats['downloads']
    
    await update.message.reply_markdown(
        f"🌟 *Рівень: {level}*\n"
        f"Завантажено: {stats['downloads']}\n"
        f"До наступного: {needed}"
    )

async def top_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id): return
    if await check_blocked(update, context): return
    if not await is_user_subscribed(update, context): return
    
    if not user_data:
        await update.message.reply_text("📊 Ще немає статистики!")
        return
    
    sorted_users = sorted(user_data.items(), key=lambda x: x[1]['downloads'], reverse=True)[:5]
    response = "🏆 *Топ-5:*\n"
    
    for i, (uid, stats) in enumerate(sorted_users, 1):
        try:
            user_info = await context.bot.get_chat(uid)
            name = user_info.username or user_info.first_name
        except:
            name = f"ID {uid}"
        response += f"{i}. @{name} — {stats['downloads']} завантажень\n"
    
    await update.message.reply_markdown(response)

async def genre_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id): return
    if await check_blocked(update, context): return
    if not await is_user_subscribed(update, context): return
    
    if not context.args:
        await update.message.reply_markdown("❓ Вкажіть жанр. Приклад: `/genre рок`")
        return
    
    genre = " ".join(context.args).capitalize()
    get_user_stats(update.effective_user.id)["genre"] = genre
    await update.message.reply_markdown(f"✅ Жанр: *{genre}*")
    save_data()

async def random_track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id): return
    if await check_blocked(update, context): return
    if not await is_user_subscribed(update, context): return
    
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
        filepath, title, tmpdir = await download_media(query, audio=True, quality="best")
        if not filepath:
            await update.message.reply_text("😕 Нічого не знайдено")
            return
        
        with open(filepath, "rb") as f:
            await update.message.reply_audio(f, caption=f"🎵 *{title}*", parse_mode="Markdown")
        
        stats["downloads"] += 1
        stats["tracks"] += 1
        await check_achievements(update, context)
        save_data()
    except Exception as e:
        await update.message.reply_text(f"❌ Помилка: {e}")
    finally:
        if tmpdir and os.path.isdir(tmpdir):
            shutil.rmtree(tmpdir)

async def find(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id): return
    if await check_blocked(update, context): return
    if not await is_user_subscribed(update, context): return
    
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
        filepath, title, tmpdir = await download_media(query, audio=True, quality="best")
        if not filepath:
            await update.message.reply_text("😕 Нічого не знайдено")
            return
        
        with open(filepath, "rb") as f:
            await update.message.reply_audio(f, caption=f"🎵 {title}")
        
        stats["downloads"] += 1
        stats["tracks"] += 1
        await check_achievements(update, context)
        save_data()
    except Exception as e:
        await update.message.reply_text(f"❌ Помилка: {e}")
    finally:
        if tmpdir and os.path.isdir(tmpdir):
            shutil.rmtree(tmpdir)

# ================= МАГАЗИН =================
async def shop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id): return
    if await check_blocked(update, context): return
    if not await is_user_subscribed(update, context): return
    
    stats = get_user_stats(update.effective_user.id)
    keyboard = [
        [InlineKeyboardButton(f"👑 VIP 1 день ({SHOP_PRICES['vip_1_day']}⭐)", callback_data="shop_buy_vip_1")],
        [InlineKeyboardButton(f"👑 VIP 7 днів ({SHOP_PRICES['vip_7_days']}⭐)", callback_data="shop_buy_vip_7")],
        [InlineKeyboardButton(f"👑 VIP 30 днів ({SHOP_PRICES['vip_30_days']}⭐)", callback_data="shop_buy_vip_30")],
        [InlineKeyboardButton(f"♾ Безліміт 24г ({SHOP_PRICES['unlimited_24h']}⭐)", callback_data="shop_buy_unlimited")],
        [InlineKeyboardButton(f"🚀 Пріоритет ({SHOP_PRICES['priority_pass']}⭐)", callback_data="shop_buy_priority")]
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
    
    items = {
        "shop_buy_vip_1": (SHOP_PRICES["vip_1_day"], "VIP (1 день)", lambda: extend_vip(stats, days=1)),
        "shop_buy_vip_7": (SHOP_PRICES["vip_7_days"], "VIP (7 днів)", lambda: extend_vip(stats, days=7)),
        "shop_buy_vip_30": (SHOP_PRICES["vip_30_days"], "VIP (30 днів)", lambda: extend_vip(stats, days=30)),
        "shop_buy_unlimited": (SHOP_PRICES["unlimited_24h"], "Безліміт 24г", lambda: extend_unlimited(stats)),
        "shop_buy_priority": (SHOP_PRICES["priority_pass"], "Пріоритет", lambda: add_priority(stats))
    }
    
    if data in items:
        cost, name, action = items[data]
        if stats["stars"] >= cost:
            stats["stars"] -= cost
            action()
            await query.message.reply_text(f"✅ Куплено: {name}!")
            save_data()
        else:
            await query.message.reply_text(f"❌ Недостатньо ⭐. Потрібно: {cost}, Є: {stats['stars']}")

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
    if await check_blocked(update, context): return ConversationHandler.END
    
    query = update.callback_query
    await query.answer()
    context.user_data["type"] = query.data
    
    if query.data == "audio":
        keyboard = [[InlineKeyboardButton("YouTube", callback_data="yt"), InlineKeyboardButton("SoundCloud", callback_data="sc")]]
    else:
        keyboard = [[InlineKeyboardButton("YouTube", callback_data="yt"), InlineKeyboardButton("TikTok", callback_data="tt")]]
    
    await query.edit_message_text("🔍 Обери джерело:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_SOURCE

async def select_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_blocked(update, context): return ConversationHandler.END
    
    query = update.callback_query
    await query.answer()
    context.user_data["source"] = query.data
    
    media_type = context.user_data["type"]
    user_id = query.from_user.id
    
    if media_type == "audio":
        keyboard = [[InlineKeyboardButton(f"{kb}kbps ({get_final_cost(user_id, COSTS['audio'][kb])}⭐)", callback_data=kb)] for kb in ["128", "192", "256"]]
    else:
        keyboard = [[InlineKeyboardButton(f"{p}p ({get_final_cost(user_id, COSTS['video'][p])}⭐)", callback_data=p)] for p in ["360", "480", "720", "1080"]]
    
    await query.edit_message_text("🎚 Обери якість:", reply_markup=InlineKeyboardMarkup(keyboard))
    return ASK_QUERY

async def select_quality(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_blocked(update, context): return ConversationHandler.END
    
    query = update.callback_query
    await query.answer()
    
    quality = query.data
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
    
    if await check_blocked(update, context): return ConversationHandler.END
    if not await is_user_subscribed(update, context): return ConversationHandler.END
    
    user_query = update.message.text.strip()
    media_type = context.user_data.get("type", "audio")
    user = update.effective_user
    stats = get_user_stats(user.id)
    
    # Додаємо жанр якщо встановлено
    if stats.get("genre"):
        user_query = f"{user_query} {stats['genre']} genre"
        stats["genre"] = None
    
    # Якщо не URL, додаємо пошук
    url_pattern = re.compile(r'https?://[^\s/$.?#].[^\s]*')
    if not url_pattern.match(user_query):
        user_query = f"ytsearch1:{user_query}"
    
    quality = context.user_data.get("quality", "128")
    base_cost = COSTS[media_type][quality]
    cost = get_final_cost(user.id, base_cost)
    
    # Визначаємо пріоритет
    priority = 10
    if is_vip_active(user.id):
        priority = 1
    elif stats.get("priority_passes", 0) > 0:
        priority = 5
        stats["priority_passes"] -= 1
        await update.message.reply_text("🚀 Використано Priority Pass!")
    
    # Додаємо в чергу
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
                
                log_action(user_info, f"Завантаження (Пріоритет {priority}): {user_query}")
                
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
                        
                        await check_achievements_from_queue(temp_context, user_id)
                        log_action(user_info, f"✅ Завантажено: {title}")
                        save_data()
                
                except Exception as e:
                    error_text = f"❌ Помилка: {e}"
                    if inline_message_id:
                        try:
                            await application.bot.edit_message_text(inline_message_id=inline_message_id, text=error_text)
                        except:
                            pass
                    else:
                        await application.bot.send_message(chat_id=chat_id, text=error_text)
                    log_action(user_info, f"❌ Помилка: {e}")
                
                finally:
                    if tmpdir and os.path.isdir(tmpdir):
                        shutil.rmtree(tmpdir)
                
                download_queue.task_done()
        
        except Exception as e:
            print(f"Критична помилка в черзі: {e}")
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

async def download_media(query, audio=True, quality="best"):
    tmpdir = tempfile.mkdtemp()
    
    if audio:
        if quality == "best":
            fmt = "bestaudio/best"
        else:
            fmt = f"bestaudio[abr<={quality}]/bestaudio/best"
        opts = {
            "format": fmt,
            "outtmpl": os.path.join(tmpdir, "%(title)s.%(ext)s"),
            "quiet": True,
            "noplaylist": True,
            "ignoreerrors": True
        }
    else:
        if quality == "best":
            fmt = "bestvideo+bestaudio/best"
        else:
            fmt = f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best"
        opts = {
            "format": fmt,
            "outtmpl": os.path.join(tmpdir, "%(title)s.%(ext)s"),
            "quiet": True,
            "noplaylist": True,
            "ignoreerrors": True,
            "merge_output_format": "mp4",
            "postprocessors": [{'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'}]
        }
    
    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            info = await asyncio.to_thread(ydl.extract_info, query, download=True)
            
            if not info or ('entries' in info and not info['entries']):
                shutil.rmtree(tmpdir)
                return None, None, None
            
            if 'entries' in info and info['entries']:
                entry = info['entries'][0]
            else:
                entry = info
            
            files = os.listdir(tmpdir)
            if not files:
                shutil.rmtree(tmpdir)
                return None, None, None
        
        except Exception:
            shutil.rmtree(tmpdir)
            return None, None, None
    
    file = files[0]
    safe_name = clean_filename(file)
    safe_path = os.path.join(tmpdir, safe_name)
    
    if safe_name != file:
        os.rename(os.path.join(tmpdir, file), safe_path)
    
    title = clean_filename(entry.get("title", "Без назви"))
    return safe_path, title, tmpdir

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_blocked(update, context): return ConversationHandler.END
    await update.message.reply_text("Скасовано")
    return ConversationHandler.END

async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_blocked(update, context): return ConversationHandler.END
    context.user_data.clear()
    await update.message.reply_text("Перезапуск. Введіть /start")
    return ConversationHandler.END

# ================= ІГРИ ТА ЕКОНОМІКА =================
async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id): return
    if await check_blocked(update, context): return
    if not await is_user_subscribed(update, context): return
    
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
    if check_spam(update.effective_user.id): return
    if await check_blocked(update, context): return
    if not await is_user_subscribed(update, context): return
    
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
    if check_spam(update.effective_user.id): return
    if await check_blocked(update, context): return
    if not await is_user_subscribed(update, context): return
    
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
    if check_spam(update.effective_user.id): return
    if await check_blocked(update, context): return
    if not await is_user_subscribed(update, context): return
    
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
    if check_spam(update.effective_user.id): return
    if await check_blocked(update, context): return
    if not await is_user_subscribed(update, context): return
    
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

# ================= INLINE =================
async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query
    if not query:
        return
    
    results = []
    try:
        ydl_opts = {
            'format': 'bestaudio/best',
            'extract_flat': 'True',
            'quiet': True,
            'noplaylist': True
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, f"ytsearch5:{query}", download=False)
            
            if 'entries' in info:
                for entry in info['entries']:
                    title = entry.get('title', 'Без назви')
                    url = entry.get('webpage_url', '')
                    if not url:
                        continue
                    
                    unique_id = base64.urlsafe_b64encode(url.encode()).decode()
                    results.append(
                        InlineQueryResultArticle(
                            id=unique_id,
                            title=title,
                            description=f"🎵 {entry.get('channel', 'Невідомий')}",
                            thumb_url=entry.get('thumbnail'),
                            input_message_content=InputTextMessageContent(message_text="📥 Завантаження...")
                        )
                    )
    except Exception as e:
        print(f"Помилка inline: {e}")
    
    await update.inline_query.answer(results, cache_time=300)

async def chosen_inline_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.chosen_inline_result.from_user
    result_id = update.chosen_inline_result.result_id
    inline_message_id = update.chosen_inline_result.inline_message_id
    
    try:
        url = base64.urlsafe_b64decode(result_id).decode()
    except:
        if inline_message_id:
            try:
                await context.bot.edit_message_text(inline_message_id=inline_message_id, text="❌ Помилка")
            except:
                pass
        return
    
    media_type = "audio"
    quality = "192"
    base_cost = COSTS[media_type][quality]
    cost = get_final_cost(user.id, base_cost)
    stats = get_user_stats(user.id)
    
    if stats["stars"] < cost:
        if inline_message_id:
            try:
                await context.bot.edit_message_text(
                    inline_message_id=inline_message_id,
                    text=f"❌ Потрібно {cost} ⭐. Баланс: {stats['stars']}"
                )
            except:
                pass
        return
    
    prio = 1 if is_vip_active(user.id) else 10
    if not is_vip_active(user.id) and stats.get("priority_passes", 0) > 0:
        prio = 5
        stats["priority_passes"] -= 1
    
    await download_queue.put((prio, time.time(), user.id, url, media_type, quality, cost, context.user_data.copy(), user.id, inline_message_id))
    
    if inline_message_id:
        try:
            await context.bot.edit_message_text(
                inline_message_id=inline_message_id,
                text=f"🔄 В черзі. Пріоритет: {'VIP' if prio == 1 else 'Високий' if prio == 5 else 'Звичайний'}"
            )
        except:
            pass

async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id): return
    if await check_blocked(update, context): return
    if not await is_user_subscribed(update, context): return
    if update.effective_user.is_bot:
        return
    
    bot_name = context.bot.username
    query = update.message.text
    
    if f'@{bot_name}' in query:
        user_query = query.replace(f'@{bot_name}', '').strip()
        if not user_query or user_query.startswith('/'):
            await update.message.reply_text("Я готовий! Надішліть назву пісні")
            return
        
        user = update.effective_user
        search_query = f"ytsearch1:{user_query}"
        base_cost = COSTS["audio"]["192"]
        cost = get_final_cost(user.id, base_cost)
        stats = get_user_stats(user.id)
        
        if stats["stars"] < cost:
            await update.message.reply_markdown(f"❌ Потрібно {cost} ⭐. Баланс: {stats['stars']}")
            return
        
        prio = 1 if is_vip_active(user.id) else 10
        
        try:
            await update.message.reply_text(f"🔍 Шукаю: {user_query}...")
            await download_queue.put((prio, time.time(), user.id, search_query, "audio", "192", cost, context.user_data.copy(), update.message.chat_id, None))
        except Exception as e:
            await update.message.reply_text(f"❌ Помилка: {e}")

# ================= АДМІН КОМАНДИ =================
async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    await update.message.reply_markdown(get_text(context, "admin_help_text"))

async def add_stars(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
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
    if not is_admin(update.effective_user.id): return
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
    if not is_admin(update.effective_user.id): return
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
    if not is_admin(update.effective_user.id): return
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
    if not is_admin(update.effective_user.id): return
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
    if not is_admin(update.effective_user.id): return
    
    total_users = len(user_data)
    total_downloads = sum(s.get("downloads", 0) for s in user_data.values())
    total_tracks = sum(s.get("tracks", 0) for s in user_data.values())
    total_videos = sum(s.get("videos", 0) for s in user_data.values())
    
    all_sources = {}
    for stats in user_data.values():
        for source, count in stats.get("source_counts", {}).items():
            all_sources[source] = all_sources.get(source, 0) + count
    
    most_popular = max(all_sources, key=all_sources.get).upper() if all_sources else "N/A"
    
    text = f"📊 *Статистика:*\n"
    text += f"Користувачів: {total_users}\n"
    text += f"Завантажень: {total_downloads}\n"
    text += f"Треків: {total_tracks}\n"
    text += f"Відео: {total_videos}\n"
    text += f"Джерело: {most_popular}"
    
    await update.message.reply_markdown(text)

async def user_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
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
    if not is_admin(update.effective_user.id): return
    try:
        user_id = int(context.args[0])
    except:
        await update.message.reply_markdown("❌ Формат: `/block <ID>`")
        return
    
    get_user_stats(user_id)["is_blocked"] = True
    await update.message.reply_text(f"✅ Заблоковано {user_id}")
    save_data()

async def unblock_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    try:
        user_id = int(context.args[0])
    except:
        await update.message.reply_markdown("❌ Формат: `/unblock <ID>`")
        return
    
    get_user_stats(user_id)["is_blocked"] = False
    await update.message.reply_text(f"✅ Розблоковано {user_id}")
    save_data()

async def grant_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
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
    if not is_admin(update.effective_user.id): return
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
    if not is_admin(update.effective_user.id): return
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
    if not is_admin(update.effective_user.id): return
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
    if not is_admin(update.effective_user.id): return
    
    active_promos = {k: v for k, v in promocodes.items() if v['expires'] > datetime.now() and v['uses'] > 0}
    
    if not active_promos:
        await update.message.reply_text("😕 Немає промокодів")
        return
    
    response = "📜 *Промокоди:*\n"
    for code, data in active_promos.items():
        expires_str = data['expires'].strftime('%Y-%m-%d %H:%M')
        response += f"`{code}`: {data['reward']}⭐, {data['uses']} раз(и), до {expires_str}\n"
    
    await update.message.reply_markdown(response)

async def set_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    try:
        username = context.args[0]
        if not username.startswith('@'):
            raise IndexError
    except:
        await update.message.reply_markdown("❌ Формат: `/set_channel @username`")
        return
    
    try:
        chat = await context.bot.get_chat(chat_id=username)
        
        # Перевірка чи немає вже такого каналу
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
    if not is_admin(update.effective_user.id): return
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
    if not is_admin(update.effective_user.id): return
    
    if not required_channels:
        await update.message.reply_text("📋 Каналів немає")
        return
    
    channels_list = "\n".join([f"- {ch['username']}" for ch in required_channels])
    await update.message.reply_markdown(f"📋 *Канали:*\n{channels_list}")

async def unset_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    
    global required_channels
    required_channels.clear()
    await update.message.reply_text("✅ Підписку вимкнено")
    save_data()

# ================= АДМІН МЕНЮ =================
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    
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
        await query.message.reply_markdown(get_text(context, "admin_help_text"))
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
        await asyncio.sleep(60)  # Зберігаємо кожну хвилину
        save_data()

# ================= MAIN =================
application = None

async def main():
    global application
    
    # Завантажуємо дані
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
    
    # Other handlers
    application.add_handler(InlineQueryHandler(inline_query))
    application.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUPS & ~filters.COMMAND, text_message_handler))
    
    print("🤖 Бот активний!")
    
    # Запускаємо задачі
    asyncio.create_task(process_queue())
    asyncio.create_task(periodic_save())
    
    await application.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
