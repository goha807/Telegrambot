import os
import re
import asyncio
import shutil
import tempfile
import base64
import time
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
BOT_TOKEN = "8213254007:AAFQkGiQqi1YirAvF4VuGcF3CL6WpqFVSGA" # ВАШ ТОКЕН
ADMINS_IDS = [1813590984] # ВАШ ID
MAX_SIZE = 50 * 1024 * 1024  # 50 MB
SPAM_DELAY = 2.0 # Затримка анти-спаму (секунди)

# --- Стани для ConversationHandler ---
# Основний діалог
SELECTING, SELECT_SOURCE, ASK_QUERY, DOWNLOAD = range(4)
# Адмін-діалог
ADMIN_MENU, AWAIT_ADD_STARS, AWAIT_REMOVE_STARS, AWAIT_USER_STATS, AWAIT_SET_DOWNLOADS_ID, AWAIT_SET_DOWNLOADS_COUNT = range(4, 10)

# --- Глобальні дані та конфігурація ---
user_data = {}
# PriorityQueue: (priority, timestamp, data...)
# 1 = VIP, 5 = Priority Pass, 10 = Regular
download_queue = asyncio.PriorityQueue()
download_in_progress = asyncio.Lock()
duel_data = {} 
promocodes = {} 
required_channel = {} 
last_activity = {} # Для анти-спаму

# --- Ціни в магазині ---
SHOP_PRICES = {
    "vip_1_day": 200,
    "vip_7_days": 1000,
    "vip_30_days": 3500,
    "unlimited_24h": 500,
    "priority_pass": 50
}

# --- Система мов ---
LANGUAGES = {
    "ua": {
        # --- GENERAL USER TEXTS ---
        "start_greeting": "Привіт, {}!\n\nЯ допоможу тобі завантажити 🎵 музику або 🎬 відео з YouTube, SoundCloud або TikTok.\n\n📌 Натисни кнопку нижче, щоб почати:",
        "start_button_audio": "🎵 Музика",
        "start_button_video": "🎬 Відео",
        "help_text": "📖 *Довідка*\n\n*Підтримувані джерела:*\n- YouTube (аудіо, відео)\n- SoundCloud (аудіо)\n- TikTok (відео)\n\n*Основні команди:*\n`start` — запуск\n`help` — допомога\n`shop` — магазин послуг 🛒\n`cancel` — скасувати\n`restart` — перезапуск\n`ping` — чи бот активний\n`stats` — статистика завантажень\n`lang` — зміна мови\n`find` — AI-пошук музики за описом\n`support` — підтримка\n`level` — перевірити свій прогрес\n`achievements` — твої досягнення\n`topusers` — топ користувачів\n`random` — випадковий трек дня\n`promo <code>` — активувати промокод\n\n*Магазин:*\nВи можете придбати VIP-статус, безліміт на завантаження або 'Пріоритетний пропуск', щоб стати першим у черзі.\n\n*Ігрові команди:*\n`balance` — перевірити баланс зірок\n`dice <ставка>` — кинути кубик і виграти зірки\n`flipcoin <ставка> <вибір>` — гра Орел і Решка\n`duel <ID> <ставка>` — викликати користувача на дуель\n\n*Інструкція:*\n1. Введи /start\n2. Обери тип файлу\n3. Обери джерело\n4. Обери якість\n5. Встав посилання або назву пісні/відео\n\n_Файли зберігаються без перекодування (webm, m4a, mp4)_",
        "ping_success": "✅ Бот активний!",
        "stats_text": "📊 *Твоя статистика:*\n\n👑 Статус: {vip_status}\n🎵 Треків: {tracks}\n🎬 Відео: {videos}\n📌 Найпопулярніше джерело: {source}",
        "lang_select": "🌐 Обери мову:",
        "support_text": "💬 Звʼязок із розробником: https://t.me/MyDownloaderSupport",
        "level_text": "🌟 *Твій рівень: {level}*\nЗавантажено файлів: {downloads}\nЗалишилось до {next_level} рівня: {needed} завантажень.",
        "topusers_empty": "📊 Ще немає статистики для відображення!",
        "topusers_text": "🏆 *Топ-5 користувачів за завантаженнями:*\n",
        "genre_empty": "❓ Вкажіть жанр після команди.\nПриклад: `/genre рок`",
        "genre_set": "✅ Пошук музики тепер обмежено жанром: *{genre}*.\nНадішліть свій запит.",
        "random_track_searching": "🎧 Знаходжу випадковий трек для тебе...",
        "random_track_caption": "🎵 *Випадковий трек дня:*\n{title}",
        "error_downloading": "❌ Помилка при завантаженні треку: {e}",
        "find_empty": "❓ Напиши опис пісні після команди `/find`.\nПриклад: `/find та пісня з фільму Хенкок`",
        "find_searching": "🔍 Знаходжу пісню: {query}",
        "find_caption": "🎵 {title}",
        "find_error": "❌ Помилка при пошуку: {e}",
        "select_source_text": "🔍 Обери джерело:",
        "select_quality_text": "🎚 Обери якість:",
        "ask_query_text": "📥 Надішли посилання або назву пісні/відео:",
        "download_started": "🔄 Завантаження...",
        "file_too_large": "⚠️ Файл занадто великий для надсилання (понад 50MB).",
        "download_complete": "✅ Завантаження завершено! Надсилаю файл...",
        "sent_audio_caption": "🎵 {title}",
        "sent_video_caption": "🎬 {title}",
        "sent_doc_caption": "📎 {title}",
        "download_error": "❌ Помилка: {e}",
        "cancelled": "Операція скасована.",
        "restart_message": "Операція скасована. Введіть /start, щоб почати знову.",
        "achievements_text": "🏆 *Твої досягнення:*\n",
        "achievement_unlocked": "🎉 *Нове досягнення: {name}!* 🎉",
        "achievement_no_achievements": "😕 У тебе поки немає досягнень.",
        "lang_changed": "🌐 Мову змінено на {lang}.",
        "inline_downloading": "📥 Завантаження файлу...",
        "inline_sent": "✅ Файл відправлено!",
        "inline_error": "❌ Не вдалося завантажити файл.",
        "inline_no_results": "⚠️ За вашим запитом нічого не знайдено.",
        "group_search_started": "🔍 Шукаю: {query}...",
        "no_results_found": "😕 За вашим запитом *'{query}'* нічого не знайдено.",
        "balance_text": "💰 *Твій баланс:* {stars} зірок ⭐\n👑 *Статус:* {vip_status}",
        "dice_roll": "🎲 Кидаю кубик... Випало: {value}!",
        "dice_win": "🎉 Вітаємо! Випало 6! Ти виграв {win_amount} зірок! Твій новий баланс: {stars} ⭐",
        "dice_lose": "💔 На жаль, випало 1! Ти втратив {lost_amount} зірок! Твій новий баланс: {stars} ⭐",
        "dice_neutral": "⚖️ Випало {value}! Твоя ставка {bet} повертається. Поточний баланс: {stars} ⭐",
        "dice_no_money": "❌ У тебе недостатньо зірок для такої ставки! Твій баланс: {stars} ⭐",
        "dice_invalid_bet": "❗️ Ставка має бути числом більше 0. Приклад: `/dice 20`",
        "queue_add": "🔄 Завантаження додано в чергу.\nВаша позиція: {pos}.\nПріоритет: {priority}",
        "queue_start": "🚀 Починаю завантаження вашого запиту.",
        "not_enough_stars_find": "❌ Для пошуку `/find` потрібно {cost} зірок. Твій баланс: {stars} ⭐",
        "not_enough_stars_random": "❌ Для випадкового треку `/random` потрібно {cost} зірок. Твій баланс: {stars} ⭐",
        "not_enough_stars_download": "❌ Для завантаження обраної якості потрібно {cost} зірок. Твій баланс: {stars} ⭐",
        "blocked_user_message": "❌ Ваш акаунт заблоковано адміністратором. Зв'яжіться з підтримкою для деталей.",
        "vip_status_active": "👑 VIP (Активний)",
        "vip_status_inactive": "Звичайний",
        "spam_warning": "⏳ Будь ласка, не флудіть! Зачекайте трохи.",
        
        # --- SHOP TEXTS ---
        "shop_title": "🛒 *Магазин*\nТвій баланс: {stars} ⭐\nОбери товар:",
        "shop_vip_1": "👑 VIP 1 день ({cost}⭐)",
        "shop_vip_7": "👑 VIP 7 днів ({cost}⭐)",
        "shop_vip_30": "👑 VIP 30 днів ({cost}⭐)",
        "shop_unlimited": "♾ Безліміт скачування 24г ({cost}⭐)",
        "shop_priority": "🚀 Перший в черзі (1 раз) ({cost}⭐)",
        "shop_success": "✅ Успішно придбано: {item}!",
        "shop_fail": "❌ Недостатньо зірок. Потрібно {cost}, у вас {stars}.",
        "shop_priority_desc": "Ваш наступний запит буде оброблено позачергово (після VIP).",
        
        # --- SUBSCRIPTION TEXTS ---
        "must_subscribe": "❗️ Для використання бота, будь ласка, підпишіться на наш канал. Це дасть вам бонус: 100 зірок та VIP на 24 години!",
        "subscribe_button": "➡️ Підписатись",
        "subscription_verified": "✅ Дякуємо за підписку! Ви отримали:\n➕ {reward} зірок ⭐\n👑 VIP-статус на 1 день!\nТепер ви можете користуватися ботом.",

        # --- PROMOCODE TEXTS ---
        "promo_enter": "❓ Вкажіть промокод після команди. Приклад: `/promo NEWYEAR`",
        "promo_activated": "✅ Промокод `{code}` активовано! Вам нараховано {reward} зірок! ⭐",
        "promo_not_found": "❌ Промокод `{code}` не знайдено.",
        "promo_expired": "❌ Термін дії промокоду `{code}` закінчився.",
        "promo_no_uses": "❌ Промокод `{code}` вже використано максимальну кількість разів.",
        "promo_already_used": "❌ Ви вже використовували промокод `{code}`.",
        
        # --- DUEL TEXTS ---
        "flipcoin_empty": "❓ Вкажіть ставку та вибір (орел/решка).\nПриклад: `/flipcoin 20 орел`",
        "flipcoin_invalid_bet": "❗️ Ставка має бути числом більше 0.",
        "flipcoin_invalid_choice": "❗️ Ваш вибір має бути 'орел' або 'решка'.",
        "flipcoin_no_money": "❌ У вас недостатньо зірок. Ваш баланс: {stars} ⭐",
        "flipcoin_result": "🎲 Монета підкинута... Випало: *{result}*!",
        "flipcoin_win": "🎉 Вітаємо! Ти виграв {win_amount} зірок! Новий баланс: {stars} ⭐",
        "flipcoin_lose": "💔 На жаль, ти програв {lost_amount} зірок. Новий баланс: {stars} ⭐",
        "duel_empty": "❓ Вкажіть ID користувача та ставку.\nПриклад: `/duel 123456789 50`",
        "duel_invalid_bet": "❗️ Ставка має бути числом більше 0.",
        "duel_self": "❌ Не можна викликати на дуель самого себе.",
        "duel_no_money": "❌ У вас недостатньо зірок для такої ставки. Ваш баланс: {stars} ⭐",
        "duel_opponent_no_money": "❌ У користувача @{username} недостатньо зірок для цієї ставки.",
        "duel_invite_text": "⚔️ Користувач @{challenger_username} викликає тебе на дуель зі ставкою {bet} зірок! У тебе є {opponent_stars} зірок. Ти приймаєш виклик?",
        "duel_invite_buttons": "Прийняти,Відхилити",
        "duel_accepted_challenger": "✅ @{opponent_username} прийняв твій виклик! Кидаємо кубики...",
        "duel_accepted_opponent": "✅ Ви прийняли виклик від @{challenger_username}!",
        "duel_declined_challenger": "❌ @{opponent_username} відмовився від дуелі.",
        "duel_declined_opponent": "❌ Ви відхилили виклик на дуель.",
        "duel_start": "🔥 Початок дуелі між @{challenger_username} та @{opponent_username} зі ставкою {bet} зірок!",
        "duel_result": "🎲 Кубик @{username}: {roll}!",
        "duel_win": "🏆 Переможець: @{winner_username}! Він/вона виграв(ла) {win_amount} зірок!",
        "duel_draw": "🤝 Нічия! Ставка повертається.",
        "duel_expired": "❌ Ця дуель вже неактуальна.",

        # --- ADMIN TEXTS ---
        "admin_help_text": "👑 *Адмін-довідка*\n\n*Керування користувачами:*\n`/add_stars <ID> <кількість>` — додати зірки\n`/remove_stars <ID> <кількість>` — забрати зірки\n`/set_downloads <ID> <кількість>` — встановити к-ть завантажень\n`/user_stats <ID>` — статистика користувача\n`/block <ID>` — заблокувати користувача\n`/unblock <ID>` — розблокувати\n`/grant_vip <ID>` — видати VIP-статус\n`/revoke_vip <ID>` — забрати VIP-статус\n\n*Керування ботом:*\n`/send_to <ID> <повідомлення>` — надіслати повідомлення\n`/broadcast <повідомлення>` — розсилка всім\n`/bot_stats` — загальна статистика\n\n*Промокоди:*\n`/create_promo <назва> <зірки> <використання> <дні>`\n`/delete_promo <назва>` — видалити промокод\n`/list_promos` — список активних промокодів\n\n*Канал підписки:*\n`/set_channel @username` — встановити канал\n`/unset_channel` — видалити канал",
        "stars_added": "✅ Додано {amount} зірок користувачу {user_id}. Новий баланс: {stars} ⭐",
        "stars_removed": "✅ Забрано {amount} зірок у користувача {user_id}. Новий баланс: {stars} ⭐",
        "user_not_found": "❌ Користувача з ID {user_id} не знайдено.",
        "message_sent": "✅ Повідомлення надіслано користувачу {user_id}.",
        "broadcast_started": "✅ Початок розсилки повідомлень. Це може зайняти деякий час.",
        "user_blocked": "✅ Користувача {user_id} заблоковано.",
        "user_unblocked": "✅ Користувача {user_id} розблоковано.",
        "bot_stats_text": "📊 *Загальна статистика бота:*\n\n👤 Користувачів: {total_users}\n⬇️ Завантажено файлів: {total_downloads}\n🎵 Треків: {total_tracks}\n🎬 Відео: {total_videos}\n📌 Найпопулярніше джерело: {most_popular_source}\n",
        "downloads_set": "✅ Користувачу {user_id} встановлено {count} завантажень.",
        "admin_menu_title": "👑 *Адмін-меню*\n\nОберіть дію:",
        "admin_button_add_stars": "➕ Видати зірки",
        "admin_button_remove_stars": "➖ Забрати зірки",
        "admin_button_set_downloads": "📊 Встановити завантаження",
        "admin_button_user_stats": "👤 Статистика гравця",
        "admin_button_help": "📖 Інструкція",
        "admin_button_exit": "⬅️ Вийти",
        "admin_prompt_add_stars": "Введіть ID користувача та кількість зірок через пробіл (напр. `12345 500`).",
        "admin_prompt_remove_stars": "Введіть ID користувача та кількість зірок для зняття (напр. `12345 100`).",
        "admin_prompt_user_stats": "Введіть ID користувача для перегляду статистики.",
        "admin_prompt_set_downloads_id": "Введіть ID користувача, якому потрібно встановити кількість завантажень.",
        "admin_prompt_set_downloads_count": "Тепер введіть нову кількість завантажень для користувача {user_id}.",
        "admin_invalid_input": "❌ Некоректний ввід. Спробуйте ще раз.",
        "admin_action_cancelled": "Дію скасовано.",
        "vip_granted": "✅ Користувачу {user_id} надано VIP-статус.",
        "vip_revoked": "✅ У користувача {user_id} забрано VIP-статус.",
        "promo_created": "✅ Промокод `{code}` створено: {reward}⭐, {uses} використань, дійсний до {expires}.",
        "promo_create_format": "❌ Формат: `/create_promo <назва> <зірки> <використання> <дні>`",
        "promo_deleted": "✅ Промокод `{code}` видалено.",
        "promo_delete_format": "❌ Формат: `/delete_promo <назва>`",
        "promo_list_empty": "😕 Активних промокодів немає.",
        "promo_list_header": "📜 *Активні промокоди:*\n\n",
        "channel_set": "✅ Канал для підписки встановлено: {username}",
        "channel_set_error": "❌ Не вдалося знайти канал {username}. Переконайтесь, що він публічний і бот є адміністратором каналу.",
        "channel_set_format": "❌ Формат: `/set_channel @username`",
        "channel_unset": "✅ Обов'язкову підписку на канал вимкнено.",
    },
    "en": {
        # --- English translations can be added here ---
    }
}
# Only UA is fully defined, so we'll use it as a fallback for EN
LANGUAGES["en"] = {**LANGUAGES["ua"], **LANGUAGES.get("en", {})}

# --- Ціни (підвищені) ---
COSTS = {
    "audio": {
        "128": 10, "192": 15, "256": 20, "base_find": 15, "base_random": 10
    },
    "video": {
        "360": 25, "480": 35, "720": 50, "1080": 70
    }
}

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
    """Перевірка на флуд (анти-спам)."""
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
        "is_vip": False,
        "vip_expiration": None, # Дата закінчення VIP
        "used_promos": [],
        "has_channel_reward": False,
        "unlimited_dl_expires": None, # Дата закінчення безліміту
        "priority_passes": 0 # Кількість пропусків у початок черги
    })
    # Міграція даних
    if "is_vip" not in stats: stats["is_vip"] = False
    if "vip_expiration" not in stats: stats["vip_expiration"] = None
    if "used_promos" not in stats: stats["used_promos"] = []
    if "has_channel_reward" not in stats: stats["has_channel_reward"] = False
    if "stars" not in stats: stats["stars"] = 50
    if "unlimited_dl_expires" not in stats: stats["unlimited_dl_expires"] = None
    if "priority_passes" not in stats: stats["priority_passes"] = 0
    return stats

def is_vip_active(user_id):
    """Перевіряє, чи активний VIP (постійний або тимчасовий)."""
    stats = get_user_stats(user_id)
    if stats.get("is_vip", False): # Постійний VIP від адміна
        return True
    if stats.get("vip_expiration") and datetime.now() < stats["vip_expiration"]: # Тимчасовий VIP
        return True
    return False

def is_unlimited_active(user_id):
    """Перевіряє, чи активний безліміт на завантаження."""
    stats = get_user_stats(user_id)
    if stats.get("unlimited_dl_expires") and datetime.now() < stats["unlimited_dl_expires"]:
        return True
    return False

def get_final_cost(user_id, base_cost):
    """Розраховує вартість з урахуванням VIP та Безліміту."""
    if is_unlimited_active(user_id):
        return 0
    if is_vip_active(user_id):
        return ceil(base_cost * 0.5)
    return base_cost

async def is_user_subscribed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Перевіряє, чи підписаний користувач на обов'язковий канал."""
    if not required_channel or not update.effective_user:
        return True

    user_id = update.effective_user.id
    try:
        member = await context.bot.get_chat_member(chat_id=required_channel['id'], user_id=user_id)
        if member.status in ['member', 'administrator', 'creator']:
            stats = get_user_stats(user_id)
            if not stats.get('has_channel_reward', False):
                reward = 100 # Бонус за підписку
                stats['stars'] += reward
                
                # --- ДОДАЄМО VIP НА 1 ДЕНЬ ---
                current_expiry = stats.get("vip_expiration") or datetime.now()
                if current_expiry < datetime.now():
                    current_expiry = datetime.now()
                stats["vip_expiration"] = current_expiry + timedelta(days=1)
                
                stats['has_channel_reward'] = True
                log_action(update.effective_user, f"Отримав бонус {reward}⭐ та VIP за підписку")
                
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=get_text(context, "subscription_verified").format(reward=reward)
                )
            return True
        else:
            raise Exception("User is not a member")
    except Exception:
        keyboard = [[InlineKeyboardButton(
            get_text(context, "subscribe_button"),
            url=f"https://t.me/{required_channel['username'].lstrip('@')}"
        )]]
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=get_text(context, "must_subscribe"),
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return False

def calculate_level(downloads):
    return floor(downloads / 10) + 1

async def check_achievements(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stats = get_user_stats(user_id)
    current_downloads = stats["downloads"]
   
    for downloads_needed, achievement_name in [(1, "Новачок"), (10, "Аматор"), (50, "Меломан"), (100, "Майстер музики")]:
        if current_downloads >= downloads_needed and achievement_name not in stats["achievements"]:
            stats["achievements"].append(achievement_name)
            await update.message.reply_text(get_text(context, "achievement_unlocked").format(name=achievement_name), parse_mode="Markdown")

async def check_blocked(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not update.effective_user:
        return False 
    user_id = update.effective_user.id
    if get_user_stats(user_id).get("is_blocked", False):
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=get_text(context, "blocked_user_message")
            )
        except Exception as e:
            log_action(update.effective_user, f"Failed to send blocked message: {e}")
        return True
    return False

# --- USER COMMANDS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id): return
    if await check_blocked(update, context): return ConversationHandler.END
    if not await is_user_subscribed(update, context): return ConversationHandler.END
    
    user = update.effective_user
    log_action(user, "Запустив /start")
    stats = get_user_stats(user.id)
    lang = stats.get("lang", "ua")
    context.user_data["lang"] = lang

    greeting = get_text(context, "start_greeting").format(user.first_name)
    keyboard = [
        [InlineKeyboardButton(get_text(context, "start_button_audio"), callback_data="audio")],
        [InlineKeyboardButton(get_text(context, "start_button_video"), callback_data="video")]
    ]
    await update.message.reply_text(greeting, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return SELECTING

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id): return
    if await check_blocked(update, context): return
    if not await is_user_subscribed(update, context): return
    user = update.effective_user
    log_action(user, "Запустив /help")
    await update.message.reply_markdown(get_text(context, "help_text"))

async def achievements_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id): return
    if await check_blocked(update, context): return
    if not await is_user_subscribed(update, context): return
    user = update.effective_user
    log_action(user, "Запустив /achievements")
    stats = get_user_stats(user.id)
   
    if not stats["achievements"]:
        await update.message.reply_text(get_text(context, "achievement_no_achievements"))
        return
       
    response = get_text(context, "achievements_text")
    for achievement in stats["achievements"]:
        response += f"- {achievement}\n"
    await update.message.reply_text(response, parse_mode="Markdown")

async def lang_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id): return
    if await check_blocked(update, context): return
    user = update.effective_user
    log_action(user, "Запустив /lang")
    keyboard = [
        [InlineKeyboardButton("🇺🇦 Українська", callback_data="lang_ua")],
        [InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")]
    ]
    await update.message.reply_text(get_text(context, "lang_select"), reply_markup=InlineKeyboardMarkup(keyboard))

async def set_lang_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
   
    lang_code = query.data.split("_")[1]
    context.user_data["lang"] = lang_code
    get_user_stats(query.from_user.id)["lang"] = lang_code
   
    await query.edit_message_text(get_text(context, "lang_changed").format(lang=lang_code.upper()))

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id): return
    if await check_blocked(update, context): return
    user = update.effective_user
    log_action(user, "Запустив /ping")
    await update.message.reply_text(get_text(context, "ping_success"))

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id): return
    if await check_blocked(update, context): return
    if not await is_user_subscribed(update, context): return
    user = update.effective_user
    log_action(user, "Запустив /stats")
    stats = get_user_stats(user.id)
    vip_status_key = "vip_status_active" if is_vip_active(user.id) else "vip_status_inactive"
    await update.message.reply_markdown(
        get_text(context, "stats_text").format(
            tracks=stats['tracks'],
            videos=stats['videos'],
            source=stats['source'],
            vip_status=get_text(context, vip_status_key)
        )
    )

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id): return
    if await check_blocked(update, context): return
    user = update.effective_user
    log_action(user, "Запустив /support")
    await update.message.reply_text(get_text(context, "support_text"))

async def level_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id): return
    if await check_blocked(update, context): return
    if not await is_user_subscribed(update, context): return
    user = update.effective_user
    log_action(user, "Запустив /level")
    stats = get_user_stats(user.id)
    level = calculate_level(stats['downloads'])
    downloads_needed_for_next_level = (level * 10) - stats['downloads']
    await update.message.reply_text(
        get_text(context, "level_text").format(
            level=level,
            downloads=stats['downloads'],
            next_level=level + 1,
            needed=downloads_needed_for_next_level
        ),
        parse_mode="Markdown"
    )

async def top_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id): return
    if await check_blocked(update, context): return
    if not await is_user_subscribed(update, context): return
    user = update.effective_user
    log_action(user, "Запустив /topusers")
    if not user_data:
        await update.message.reply_text(get_text(context, "topusers_empty"))
        return

    sorted_users = sorted(user_data.items(), key=lambda item: item[1]['downloads'], reverse=True)
    top_5 = sorted_users[:5]

    response = get_text(context, "topusers_text")
    for i, (user_id, stats) in enumerate(top_5):
        try:
            user_info = await context.bot.get_chat(user_id)
            username = user_info.username or user_info.first_name
            response += f"{i + 1}. @{username} — {stats['downloads']} завантажень (Рівень {calculate_level(stats['downloads'])})\n"
        except Exception:
            response += f"{i + 1}. Користувач {user_id} — {stats['downloads']} завантажень (Рівень {calculate_level(stats['downloads'])})\n"
    await update.message.reply_text(response, parse_mode="Markdown")

async def genre_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id): return
    if await check_blocked(update, context): return
    if not await is_user_subscribed(update, context): return
    user = update.effective_user
    log_action(user, "Запустив /genre")
    args = context.args
    if not args:
        await update.message.reply_text(get_text(context, "genre_empty"), parse_mode="Markdown")
        return
   
    genre = " ".join(args).capitalize()
    get_user_stats(user.id)["genre"] = genre
    log_action(user, f"Встановив жанр: {genre}")
    await update.message.reply_text(get_text(context, "genre_set").format(genre=genre), parse_mode="Markdown")

async def random_track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id): return
    if await check_blocked(update, context): return
    if not await is_user_subscribed(update, context): return
    user = update.effective_user
   
    cost = get_final_cost(user.id, COSTS["audio"]["base_random"])
    stats = get_user_stats(user.id)
    if stats["stars"] < cost:
        await update.message.reply_text(get_text(context, "not_enough_stars_random").format(cost=cost, stars=stats["stars"]), parse_mode="Markdown")
        return

    stats["stars"] -= cost
    log_action(user, f"Запустив /random за {cost}⭐")
   
    tracks = [
        "ytsearch:Imagine Dragons Believer",
        "ytsearch:Queen Bohemian Rhapsody",
        "ytsearch:Dua Lipa Don't Start Now",
        "ytsearch:The Weeknd Blinding Lights",
        "ytsearch:AC/DC Thunderstruck"
    ]
   
    random_query = random.choice(tracks)
   
    await update.message.reply_text(get_text(context, "random_track_searching"))
   
    tmpdir = None
    try:
        filepath, title, tmpdir = await download_media(random_query, audio=True, quality="best")
        if not filepath:
            await update.message.reply_text(get_text(context, "no_results_found").format(query=random_query))
            return
           
        with open(filepath, "rb") as f:
            await update.message.reply_audio(f, caption=get_text(context, "random_track_caption").format(title=title), parse_mode="Markdown")
        log_action(user, f"Відправлено випадковий трек: {title}")
    except Exception as e:
        await update.message.reply_text(get_text(context, "error_downloading").format(e=e))
        log_action(user, f"Помилка при завантаженні випадкового треку: {e}")
    finally:
        if tmpdir and os.path.isdir(tmpdir):
            shutil.rmtree(tmpdir)

async def find(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id): return
    if await check_blocked(update, context): return
    if not await is_user_subscribed(update, context): return
    user = update.effective_user
    args = context.args
    if not args:
        log_action(user, "Запустив /find без аргументів")
        await update.message.reply_text(get_text(context, "find_empty"), parse_mode="Markdown")
        return

    cost = get_final_cost(user.id, COSTS["audio"]["base_find"])
    stats = get_user_stats(user.id)
    if stats["stars"] < cost:
        await update.message.reply_text(get_text(context, "not_enough_stars_find").format(cost=cost, stars=stats["stars"]), parse_mode="Markdown")
        return
       
    stats["stars"] -= cost
    log_action(user, f"Запустив /find за {cost}⭐")

    query = "ytsearch1:" + " ".join(args)
    context.user_data["type"] = "audio"
    await update.message.reply_text(get_text(context, "find_searching").format(query=" ".join(args)))
   
    tmpdir = None
    try:
        filepath, title, tmpdir = await download_media(query, audio=True, quality="best")
        if not filepath:
            await update.message.reply_text(get_text(context, "no_results_found").format(query=query.replace("ytsearch1:", "")))
            return
           
        with open(filepath, "rb") as f:
            await update.message.reply_audio(f, caption=get_text(context, "find_caption").format(title=title))
        log_action(user, f"✅ Знайдено та відправлено за запитом /find: {title}")
    except Exception as e:
        await update.message.reply_text(get_text(context, "find_error").format(e=e))
        log_action(user, f"Помилка при пошуку через /find: {e}")
    finally:
        if tmpdir and os.path.isdir(tmpdir):
            shutil.rmtree(tmpdir)

# --- SHOP COMMANDS ---

async def shop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id): return
    if await check_blocked(update, context): return
    if not await is_user_subscribed(update, context): return
    
    user = update.effective_user
    stats = get_user_stats(user.id)
    log_action(user, "Відкрив /shop")
    
    keyboard = [
        [InlineKeyboardButton(get_text(context, "shop_vip_1").format(cost=SHOP_PRICES["vip_1_day"]), callback_data="shop_buy_vip_1")],
        [InlineKeyboardButton(get_text(context, "shop_vip_7").format(cost=SHOP_PRICES["vip_7_days"]), callback_data="shop_buy_vip_7")],
        [InlineKeyboardButton(get_text(context, "shop_vip_30").format(cost=SHOP_PRICES["vip_30_days"]), callback_data="shop_buy_vip_30")],
        [InlineKeyboardButton(get_text(context, "shop_unlimited").format(cost=SHOP_PRICES["unlimited_24h"]), callback_data="shop_buy_unlimited")],
        [InlineKeyboardButton(get_text(context, "shop_priority").format(cost=SHOP_PRICES["priority_pass"]), callback_data="shop_buy_priority")],
    ]
    
    await update.message.reply_text(
        get_text(context, "shop_title").format(stars=stats["stars"]),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def shop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    stats = get_user_stats(user_id)
    data = query.data
    
    cost = 0
    item_name = ""
    action_func = None
    
    if data == "shop_buy_vip_1":
        cost = SHOP_PRICES["vip_1_day"]
        item_name = "VIP (1 день)"
        def action():
            curr = stats.get("vip_expiration") or datetime.now()
            if curr < datetime.now(): curr = datetime.now()
            stats["vip_expiration"] = curr + timedelta(days=1)
            
    elif data == "shop_buy_vip_7":
        cost = SHOP_PRICES["vip_7_days"]
        item_name = "VIP (7 днів)"
        def action():
            curr = stats.get("vip_expiration") or datetime.now()
            if curr < datetime.now(): curr = datetime.now()
            stats["vip_expiration"] = curr + timedelta(days=7)

    elif data == "shop_buy_vip_30":
        cost = SHOP_PRICES["vip_30_days"]
        item_name = "VIP (30 днів)"
        def action():
            curr = stats.get("vip_expiration") or datetime.now()
            if curr < datetime.now(): curr = datetime.now()
            stats["vip_expiration"] = curr + timedelta(days=30)

    elif data == "shop_buy_unlimited":
        cost = SHOP_PRICES["unlimited_24h"]
        item_name = "Безліміт на 24г"
        def action():
            curr = stats.get("unlimited_dl_expires") or datetime.now()
            if curr < datetime.now(): curr = datetime.now()
            stats["unlimited_dl_expires"] = curr + timedelta(hours=24)
            
    elif data == "shop_buy_priority":
        cost = SHOP_PRICES["priority_pass"]
        item_name = "Пріоритет в черзі (1 раз)"
        def action():
            stats["priority_passes"] += 1

    # Процес купівлі
    if stats["stars"] >= cost:
        stats["stars"] -= cost
        if action_func: action_func()
        
        log_action(query.from_user, f"Купив {item_name} за {cost}")
        msg = get_text(context, "shop_success").format(item=item_name, cost=cost)
        if data == "shop_buy_priority":
            msg += "\n" + get_text(context, "shop_priority_desc")
            
        await query.message.reply_text(msg, parse_mode="Markdown")
    else:
        await query.message.reply_text(get_text(context, "shop_fail").format(cost=cost, stars=stats["stars"]))

# --- DOWNLOAD CONVERSATION ---

async def select_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_blocked(update, context): return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    context.user_data["type"] = query.data
    log_action(query.from_user, f"Обрав тип: {query.data}")
   
    if query.data == "audio":
        keyboard = [[InlineKeyboardButton("YouTube", callback_data="yt"),
                     InlineKeyboardButton("SoundCloud", callback_data="sc")]]
    else:
        keyboard = [[InlineKeyboardButton("YouTube", callback_data="yt"),
                     InlineKeyboardButton("TikTok", callback_data="tt")]]
                     
    await query.edit_message_text(get_text(context, "select_source_text"), reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_SOURCE

async def select_source(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_blocked(update, context): return ConversationHandler.END
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
       
    await query.edit_message_text(get_text(context, "select_quality_text"), reply_markup=InlineKeyboardMarkup(keyboard))
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
        await query.edit_message_text(get_text(context, "not_enough_stars_download").format(cost=cost, stars=stats["stars"]), parse_mode="Markdown")
        return ConversationHandler.END

    context.user_data["quality"] = quality
    log_action(query.from_user, f"Обрав якість: {quality} за {cost} зірок")
    await query.edit_message_text(get_text(context, "ask_query_text"), parse_mode="Markdown")
    return DOWNLOAD

async def handle_download(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id):
        await update.message.reply_text(get_text(context, "spam_warning"))
        return ConversationHandler.END
    if await check_blocked(update, context): return ConversationHandler.END
    if not await is_user_subscribed(update, context): return ConversationHandler.END
   
    user_query = update.message.text.strip()
    media_type = context.user_data.get("type", "audio")
    user = update.effective_user
    
    stats = get_user_stats(user.id)
    if stats.get("genre"):
        user_query = f"{user_query} {stats['genre']} genre"
        stats["genre"] = None # Скидаємо жанр після використання
   
    url_pattern = re.compile(r'https?://[^\s/$.?#].[^\s]*')
    if not url_pattern.match(user_query):
        user_query = f"ytsearch1:{user_query}"
       
    quality = context.user_data.get("quality", "128")
    base_cost = COSTS[media_type][quality]
    cost = get_final_cost(user.id, base_cost)
    
    # --- ВИЗНАЧЕННЯ ПРІОРИТЕТУ ---
    # 1 = VIP (найвищий)
    # 5 = Priority Pass (середній)
    # 10 = Звичайний (низький)
    priority = 10
    if is_vip_active(user.id):
        priority = 1
    elif stats.get("priority_passes", 0) > 0:
        priority = 5
        stats["priority_passes"] -= 1
        await update.message.reply_text("🚀 Використано Priority Pass! Ви піднялися в черзі.")

    # Додаємо в PriorityQueue. Елементи сортуються по першому значенню (priority).
    # timestamp додається, щоб зберігати порядок FIFO в межах одного пріоритету.
    await download_queue.put((priority, time.time(), user.id, user_query, media_type, quality, cost, context.user_data.copy(), update.message.chat_id, None))
   
    position = download_queue.qsize()
    prio_text = "VIP" if priority == 1 else ("Високий" if priority == 5 else "Звичайний")
    await update.message.reply_text(get_text(context, "queue_add").format(pos=position, priority=prio_text))
    return ConversationHandler.END

# --- DOWNLOAD QUEUE & CORE LOGIC ---

async def process_queue():
    while True:
        try:
            # Розпаковка з PriorityQueue
            priority, timestamp, user_id, user_query, media_type, quality, cost, u_data, chat_id, inline_message_id = await download_queue.get()
           
            temp_context = type('obj', (object,), {'user_data': u_data})()
            def get_q_text(key): return LANGUAGES.get(u_data.get("lang", "ua"), LANGUAGES["ua"]).get(key, f"_{key}_")

            async with download_in_progress:
                user_info = await application.bot.get_chat(user_id)
                log_action(user_info, f"Починаю завантаження (Пріоритет {priority}): {user_query}")
               
                if inline_message_id:
                    await application.bot.edit_message_text(inline_message_id=inline_message_id, text=get_q_text("download_started"))
                else:
                    await application.bot.send_message(chat_id=chat_id, text=get_q_text("queue_start"))
               
                stats = get_user_stats(user_id)

                # Перевірка коштів (на випадок, якщо зірки зникли поки чекав у черзі)
                real_cost = get_final_cost(user_id, cost) if cost > 0 else 0
                
                if stats["stars"] < real_cost:
                    error_text = get_q_text("not_enough_stars_download").format(cost=real_cost, stars=stats["stars"])
                    if inline_message_id:
                        await application.bot.edit_message_text(inline_message_id=inline_message_id, text=error_text, parse_mode="Markdown")
                    else:
                        await application.bot.send_message(chat_id=chat_id, text=error_text, parse_mode="Markdown")
                    download_queue.task_done()
                    continue

                stats["stars"] -= real_cost
               
                tmpdir = None
                try:
                    filepath, title, tmpdir = await download_media(user_query, audio=(media_type == "audio"), quality=quality)
                   
                    if not filepath:
                        error_text = get_q_text("no_results_found").format(query=user_query.replace("ytsearch1:", ""))
                        if inline_message_id:
                            await application.bot.edit_message_text(inline_message_id=inline_message_id, text=error_text)
                        else:
                            await application.bot.send_message(chat_id=chat_id, text=error_text, parse_mode="Markdown")
                        download_queue.task_done()
                        continue
                       
                    size = os.path.getsize(filepath)

                    if size > MAX_SIZE:
                        error_text = get_q_text("file_too_large")
                        if inline_message_id:
                            await application.bot.edit_message_text(inline_message_id=inline_message_id, text=error_text)
                        else:
                            await application.bot.send_message(chat_id=chat_id, text=error_text)
                        log_action(user_info, f"Файл {title} занадто великий для відправки.")
                    else:
                        with open(filepath, "rb") as f:
                            try:
                                if media_type == "audio":
                                    caption = get_q_text("sent_audio_caption").format(title=title)
                                    if inline_message_id:
                                        await application.bot.send_audio(chat_id=user_id, audio=f, caption=caption)
                                        await application.bot.edit_message_text(inline_message_id=inline_message_id, text=get_q_text("inline_sent"))
                                    else:
                                        await application.bot.send_audio(chat_id=chat_id, audio=f, caption=caption)
                                else:
                                    caption = get_q_text("sent_video_caption").format(title=title)
                                    if inline_message_id:
                                        await application.bot.send_video(chat_id=user_id, video=f, caption=caption)
                                        await application.bot.edit_message_text(inline_message_id=inline_message_id, text=get_q_text("inline_sent"))
                                    else:
                                        await application.bot.send_video(chat_id=chat_id, video=f, caption=caption)
                            except TimedOut:
                                f.seek(0)
                                caption = get_q_text("sent_doc_caption").format(title=title)
                                if inline_message_id:
                                    await application.bot.send_document(chat_id=user.id, document=f, filename=os.path.basename(filepath), caption=caption)
                                    await application.bot.edit_message_text(inline_message_id=inline_message_id, text=get_q_text("inline_sent"))
                                else:
                                    await application.bot.send_document(chat_id=chat_id, document=f, filename=os.path.basename(filepath), caption=caption)
                       
                        stats["downloads"] += 1
                        stats["source"] = u_data.get("source", "N/A")
                        stats["source_counts"][stats["source"]] = stats["source_counts"].get(stats["source"], 0) + 1
                       
                        if media_type == "audio":
                            stats["tracks"] += 1
                        else:
                            stats["videos"] += 1
                       
                        await check_achievements_from_queue(temp_context, user_id)
                        log_action(user_info, f"✅ Завантажено: {title}")
                   
                except Exception as e:
                    error_text = get_q_text("download_error").format(e=e)
                    if inline_message_id:
                        await application.bot.edit_message_text(inline_message_id=inline_message_id, text=error_text)
                    else:
                        await application.bot.send_message(chat_id=chat_id, text=error_text, parse_mode="Markdown")
                    log_action(user_info, f"❌ Помилка: {e}")
                finally:
                    if tmpdir and os.path.isdir(tmpdir):
                        shutil.rmtree(tmpdir)
            download_queue.task_done()
        except Exception as e:
            print(f"Критична помилка в черзі: {e}")
            try: download_queue.task_done()
            except: pass


async def check_achievements_from_queue(context: ContextTypes.DEFAULT_TYPE, user_id):
    stats = get_user_stats(user_id)
   
    def get_q_text(key): return LANGUAGES.get(stats.get("lang", "ua"), LANGUAGES["ua"]).get(key, f"_{key}_")

    for downloads_needed, achievement_name_ua in [(1, "Новачок"), (10, "Аматор"), (50, "Меломан"), (100, "Майстер музики")]:
        if stats["downloads"] >= downloads_needed and achievement_name_ua not in stats["achievements"]:
            stats["achievements"].append(achievement_name_ua)
            await application.bot.send_message(chat_id=user_id, text=get_q_text("achievement_unlocked").format(name=achievement_name_ua), parse_mode="Markdown")

    current_hour = datetime.now().hour
    if 6 <= current_hour < 8 and get_q_text("achievement_early_bird") not in stats["achievements"]:
        stats["achievements"].append(get_q_text("achievement_early_bird"))
        await application.bot.send_message(chat_id=user_id, text=get_q_text("achievement_unlocked").format(name=get_q_text("achievement_early_bird")), parse_mode="Markdown")
   
    if 2 <= current_hour < 4 and get_q_text("achievement_night_owl") not in stats["achievements"]:
        stats["achievements"].append(get_q_text("achievement_night_owl"))
        await application.bot.send_message(chat_id=user_id, text=get_q_text("achievement_unlocked").format(name=get_q_text("achievement_night_owl")), parse_mode="Markdown")


async def download_media(query, audio=True, quality="best"):
    tmpdir = tempfile.mkdtemp()
   
    # --- КОНФІГУРАЦІЯ YT-DLP ДЛЯ MERGING ---
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
            "ignoreerrors": True,
            "merge_output_format": "mp4" if not audio else None,
        }
    else:
        # Для відео ми змушуємо yt-dlp з'єднувати найкраще відео і найкраще аудіо
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
            "merge_output_format": "mp4", # Важливо для TikTok/YouTube
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
    log_action(update.effective_user, "❌ Скасовано")
    await update.message.reply_text(get_text(context, "cancelled"))
    return ConversationHandler.END

async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await check_blocked(update, context): return ConversationHandler.END
    user = update.effective_user
    log_action(user, "🔄 Перезапуск")
    context.user_data.clear()
    await update.message.reply_text(get_text(context, "restart_message"))
    return ConversationHandler.END

# --- GAMES & ECONOMY ---

async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id): return
    if await check_blocked(update, context): return
    if not await is_user_subscribed(update, context): return
    user = update.effective_user
    log_action(user, "Запустив /balance")
    stats = get_user_stats(user.id)
    stars = stats.get("stars", 50)
    
    is_vip = is_vip_active(user.id)
    vip_status_key = "vip_status_active" if is_vip else "vip_status_inactive"
    status_text = get_text(context, vip_status_key)
    
    if stats.get("vip_expiration") and datetime.now() < stats["vip_expiration"]:
        status_text += f" (до {stats['vip_expiration'].strftime('%d.%m %H:%M')})"
    
    unlim_text = ""
    if is_unlimited_active(user.id):
        unlim_text = f"\n♾ Безліміт до: {stats['unlimited_dl_expires'].strftime('%d.%m %H:%M')}"

    await update.message.reply_markdown(
        get_text(context, "balance_text").format(
            stars=stars, 
            vip_status=status_text
        ) + unlim_text
    )
    
async def promo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id): return
    if await check_blocked(update, context): return
    if not await is_user_subscribed(update, context): return

    user = update.effective_user
    if not context.args:
        await update.message.reply_text(get_text(context, "promo_enter"))
        return

    code = context.args[0].upper()
    log_action(user, f"Спробував активувати промокод: {code}")
    
    promo = promocodes.get(code)
    stats = get_user_stats(user.id)

    if not promo:
        await update.message.reply_text(get_text(context, "promo_not_found").format(code=code))
        return
    
    if datetime.now() > promo["expires"]:
        await update.message.reply_text(get_text(context, "promo_expired").format(code=code))
        del promocodes[code] # Clean up expired code
        return

    if promo["uses"] <= 0:
        await update.message.reply_text(get_text(context, "promo_no_uses").format(code=code))
        return

    if code in stats.get("used_promos", []):
        await update.message.reply_text(get_text(context, "promo_already_used").format(code=code))
        return

    # Activate promo
    reward = promo["reward"]
    stats["stars"] += reward
    stats["used_promos"].append(code)
    promo["uses"] -= 1
    
    log_action(user, f"Активував промокод {code} та отримав {reward}⭐")
    await update.message.reply_text(get_text(context, "promo_activated").format(code=code, reward=reward))


async def dice_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id): return
    if await check_blocked(update, context): return
    if not await is_user_subscribed(update, context): return
    user = update.effective_user
    log_action(user, "Запустив /dice")
   
    stats = get_user_stats(user.id)
    current_stars = stats.get("stars", 50)

    if current_stars == 0:
        await update.message.reply_text("❌ У тебе немає зірок! Завантажуй файли, щоб отримати більше.")
        return

    bet = 10
    if context.args:
        try:
            bet = int(context.args[0])
            if bet <= 0:
                await update.message.reply_text(get_text(context, "dice_invalid_bet"), parse_mode="Markdown")
                return
        except (ValueError, IndexError):
            await update.message.reply_text(get_text(context, "dice_invalid_bet"), parse_mode="Markdown")
            return

    if current_stars < bet:
        await update.message.reply_text(get_text(context, "dice_no_money").format(stars=current_stars), parse_mode="Markdown")
        return

    sent_dice = await update.message.reply_dice(emoji="🎲")
    dice_value = sent_dice.dice.value
   
    response = get_text(context, "dice_roll").format(value=dice_value)
   
    if dice_value == 6:
        win_amount = bet * 2
        stats["stars"] += win_amount
        response += "\n" + get_text(context, "dice_win").format(win_amount=win_amount, stars=stats["stars"])
    elif dice_value == 1:
        stats["stars"] -= bet
        response += "\n" + get_text(context, "dice_lose").format(lost_amount=bet, stars=stats["stars"])
    else:
        response += "\n" + get_text(context, "dice_neutral").format(value=dice_value, bet=bet, stars=stats["stars"])
   
    await asyncio.sleep(4)
    await update.message.reply_text(response, parse_mode="Markdown")

async def flipcoin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id): return
    if await check_blocked(update, context): return
    if not await is_user_subscribed(update, context): return
    user = update.effective_user
    log_action(user, "Запустив /flipcoin")

    if len(context.args) < 2:
        await update.message.reply_text(get_text(context, "flipcoin_empty"), parse_mode="Markdown")
        return

    try:
        bet = int(context.args[0])
        choice = context.args[1].lower()
    except (ValueError, IndexError):
        await update.message.reply_text(get_text(context, "flipcoin_invalid_bet"), parse_mode="Markdown")
        return

    if bet <= 0:
        await update.message.reply_text(get_text(context, "flipcoin_invalid_bet"), parse_mode="Markdown")
        return

    if choice not in ['орел', 'решка', 'heads', 'tails']:
        await update.message.reply_text(get_text(context, "flipcoin_invalid_choice"), parse_mode="Markdown")
        return

    stats = get_user_stats(user.id)
    if stats["stars"] < bet:
        await update.message.reply_text(get_text(context, "flipcoin_no_money").format(stars=stats["stars"]), parse_mode="Markdown")
        return

    result = random.choice(['орел', 'решка'])
   
    is_win = (choice in ['орел', 'heads'] and result == 'орел') or \
             (choice in ['решка', 'tails'] and result == 'решка')

    if is_win:
        stats["stars"] += bet
        response = get_text(context, "flipcoin_win").format(win_amount=bet, stars=stats["stars"])
    else:
        stats["stars"] -= bet
        response = get_text(context, "flipcoin_lose").format(lost_amount=bet, stars=stats["stars"])

    await update.message.reply_text(get_text(context, "flipcoin_result").format(result=result), parse_mode="Markdown")
    await asyncio.sleep(1)
    await update.message.reply_text(response, parse_mode="Markdown")

# --- DUEL LOGIC ---

async def duel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_spam(update.effective_user.id): return
    if await check_blocked(update, context): return
    if not await is_user_subscribed(update, context): return
    user = update.effective_user
    log_action(user, "Запустив /duel")

    if len(context.args) < 2:
        await update.message.reply_text(get_text(context, "duel_empty"), parse_mode="Markdown")
        return

    try:
        opponent_id_str = context.args[0].replace('@', '')
        opponent_id = int(opponent_id_str)
        bet = int(context.args[1])
    except (ValueError, IndexError):
        await update.message.reply_text(get_text(context, "duel_invalid_bet"), parse_mode="Markdown")
        return

    if bet <= 0:
        await update.message.reply_text(get_text(context, "duel_invalid_bet"), parse_mode="Markdown")
        return

    if user.id == opponent_id:
        await update.message.reply_text(get_text(context, "duel_self"), parse_mode="Markdown")
        return

    challenger_stats = get_user_stats(user.id)
   
    if challenger_stats["stars"] < bet:
        await update.message.reply_text(get_text(context, "duel_no_money").format(stars=challenger_stats["stars"]), parse_mode="Markdown")
        return
   
    try:
        opponent_user = await context.bot.get_chat(opponent_id)
        opponent_stats = get_user_stats(opponent_id)
    except (BadRequest, TimedOut):
        await update.message.reply_text(get_text(context, "user_not_found").format(user_id=opponent_id), parse_mode="Markdown")
        return
   
    if opponent_stats["stars"] < bet:
        opponent_username = opponent_user.username or opponent_user.first_name
        await update.message.reply_text(get_text(context, "duel_opponent_no_money").format(username=opponent_username), parse_mode="Markdown")
        return

    duel_id = base64.urlsafe_b64encode(os.urandom(6)).decode('utf-8')
    duel_data[duel_id] = {
        'challenger_id': user.id,
        'opponent_id': opponent_id,
        'bet': bet,
        'challenger_chat_id': update.message.chat_id
    }
   
    try:
        buttons_text = get_text(context, "duel_invite_buttons").split(',')
        keyboard = [[
            InlineKeyboardButton(buttons_text[0], callback_data=f"duel_accept_{duel_id}"),
            InlineKeyboardButton(buttons_text[1], callback_data=f"duel_decline_{duel_id}")
        ]]
        await context.bot.send_message(
            chat_id=opponent_id,
            text=get_text(context, "duel_invite_text").format(
                challenger_username=user.username or user.first_name,
                bet=bet,
                opponent_stars=opponent_stats["stars"]
            ),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        await update.message.reply_text(f"⚔️ Запрошення на дуель надіслано користувачу @{opponent_user.username or opponent_user.first_name}.")
    except Exception as e:
        await update.message.reply_text(f"❌ Не вдалося надіслати запрошення. Можливо, користувач заблокував бота. Помилка: {e}")
        del duel_data[duel_id]

async def duel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = query.data.split('_')
    action = parts[1]
    duel_id = parts[2]
   
    user_id = query.from_user.id

    if duel_id not in duel_data:
        await query.edit_message_text(get_text(context, "duel_expired"))
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
    except Exception as e:
        await query.edit_message_text("❌ Помилка: не вдалося отримати дані одного з гравців.")
        del duel_data[duel_id]
        return

    if action == "accept":
        challenger_stats = get_user_stats(challenger_id)
        opponent_stats = get_user_stats(opponent_id)

        if challenger_stats["stars"] < bet or opponent_stats["stars"] < bet:
            await query.edit_message_text("❌ У одного з вас недостатньо зірок для цієї дуелі.")
            del duel_data[duel_id]
            return

        await query.edit_message_text(get_text(context, "duel_accepted_opponent").format(challenger_username=challenger_user.username or challenger_user.first_name))
        await context.bot.send_message(
            chat_id=challenger_chat_id,
            text=get_text(context, "duel_accepted_challenger").format(opponent_username=opponent_user.username or opponent_user.first_name)
        )
        await asyncio.sleep(1)

        duel_message_chat_id = challenger_chat_id
        await context.bot.send_message(
            chat_id=duel_message_chat_id,
            text=get_text(context, "duel_start").format(
                challenger_username=challenger_user.username or challenger_user.first_name,
                opponent_username=opponent_user.username or opponent_user.first_name,
                bet=bet
            ),
            parse_mode="Markdown"
        )
        await asyncio.sleep(1)

        challenger_roll = random.randint(1, 6)
        opponent_roll = random.randint(1, 6)
       
        await context.bot.send_message(
            chat_id=duel_message_chat_id,
            text=get_text(context, "duel_result").format(username=challenger_user.username or challenger_user.first_name, roll=challenger_roll)
        )
        await asyncio.sleep(1)
        await context.bot.send_message(
            chat_id=duel_message_chat_id,
            text=get_text(context, "duel_result").format(username=opponent_user.username or opponent_user.first_name, roll=opponent_roll)
        )
        await asyncio.sleep(1)

        if challenger_roll > opponent_roll:
            winner_id, winner_username = challenger_id, challenger_user.username or challenger_user.first_name
            loser_id = opponent_id
        elif opponent_roll > challenger_roll:
            winner_id, winner_username = opponent_id, opponent_user.username or opponent_user.first_name
            loser_id = challenger_id
        else:
            winner_id = None

        if winner_id:
            get_user_stats(winner_id)["stars"] += bet
            get_user_stats(loser_id)["stars"] -= bet
            await context.bot.send_message(
                chat_id=duel_message_chat_id,
                text=get_text(context, "duel_win").format(winner_username=winner_username, win_amount=bet),
                parse_mode="Markdown"
            )
        else:
            await context.bot.send_message(
                chat_id=duel_message_chat_id,
                text=get_text(context, "duel_draw"),
                parse_mode="Markdown"
            )

    elif action == "decline":
        await query.edit_message_text(get_text(context, "duel_declined_opponent"))
        await context.bot.send_message(
            chat_id=challenger_chat_id,
            text=get_text(context, "duel_declined_challenger").format(opponent_username=opponent_user.username or opponent_user.first_name)
        )

    if duel_id in duel_data:
        del duel_data[duel_id]


# --- INLINE & GROUP LOGIC ---

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
            'noplaylist': True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = await asyncio.to_thread(ydl.extract_info, f"ytsearch5:{query}", download=False)
           
            if 'entries' in info:
                for i, entry in enumerate(info['entries']):
                    title = entry.get('title', 'Без назви')
                    url = entry.get('webpage_url', '')
                    if not url: continue
                    unique_id = base64.urlsafe_b64encode(url.encode()).decode()
                   
                    results.append(
                        InlineQueryResultArticle(
                            id=unique_id,
                            title=title,
                            description=f"🎵 {entry.get('channel', 'Невідомий виконавець')}",
                            thumb_url=entry.get('thumbnail'),
                            input_message_content=InputTextMessageContent(
                                message_text=get_text(context, "inline_downloading")
                            )
                        )
                    )
    except Exception as e:
        print(f"Помилка при inline-пошуку: {e}")
       
    await update.inline_query.answer(results, cache_time=300)

async def chosen_inline_result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.chosen_inline_result.from_user
    result_id = update.chosen_inline_result.result_id
    inline_message_id = update.chosen_inline_result.inline_message_id

    try:
        url = base64.urlsafe_b64decode(result_id).decode()
    except Exception as e:
        log_action(user, f"Помилка декодування inline ID: {e}")
        if inline_message_id:
            await context.bot.edit_message_text(inline_message_id=inline_message_id, text="❌ Помилка: невірний ID результату.")
        return

    log_action(user, f"Вибрав inline-результат: {url}")
   
    media_type = "audio"
    quality = "192"
    base_cost = COSTS[media_type][quality]
    cost = get_final_cost(user.id, base_cost)
   
    stats = get_user_stats(user.id)
    if stats["stars"] < cost:
        if inline_message_id:
            await context.bot.edit_message_text(
                inline_message_id=inline_message_id,
                text=get_text(context, "not_enough_stars_download").format(cost=cost, stars=stats["stars"]),
                parse_mode="Markdown"
            )
        return
    
    prio = 1 if is_vip_active(user.id) else 10
    if not is_vip_active(user.id) and stats.get("priority_passes", 0) > 0:
        prio = 5
        stats["priority_passes"] -= 1

    # Додаємо в Priority Queue
    await download_queue.put((prio, time.time(), user.id, url, media_type, quality, cost, context.user_data.copy(), user.id, inline_message_id))
   
    if inline_message_id:
        await context.bot.edit_message_text(
            inline_message_id=inline_message_id,
            text=get_text(context, "queue_add").format(pos=download_queue.qsize(), priority=prio)
        )

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
            await update.message.reply_text("Я готовий! Надішліть мені назву пісні, і я її знайду.")
            return

        user = update.effective_user
        log_action(user, f"Згадали в групі: {user_query}")
       
        search_query = f"ytsearch1:{user_query}"
       
        base_cost = COSTS["audio"]["192"]
        cost = get_final_cost(user.id, base_cost)
        stats = get_user_stats(user.id)
        if stats["stars"] < cost:
            await update.message.reply_text(get_text(context, "not_enough_stars_download").format(cost=cost, stars=stats["stars"]), parse_mode="Markdown")
            return
        
        prio = 1 if is_vip_active(user.id) else 10

        try:
            await update.message.reply_text(get_text(context, "group_search_started").format(query=user_query))
            await download_queue.put((prio, time.time(), user.id, search_query, "audio", "192", cost, context.user_data.copy(), update.message.chat_id, None))
        except Exception as e:
            await update.message.reply_text(get_text(context, "download_error").format(e=e))
            log_action(user, f"❌ Помилка завантаження в групі: {e}")

# --- ADMIN COMMANDS ---

async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    log_action(update.effective_user, "Запустив /adminhelp")
    await update.message.reply_text(get_text(context, "admin_help_text"), parse_mode="Markdown")

async def add_stars(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
   
    try:
        user_id = int(context.args[0])
        amount = int(context.args[1])
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Формат: `/add_stars <ID> <кількість>`.", parse_mode="Markdown")
        return

    log_action(update.effective_user, f"Запустив /add_stars для {user_id} ({amount} зірок)")
   
    stats = get_user_stats(user_id)
    stats["stars"] += amount
    await update.message.reply_text(get_text(context, "stars_added").format(amount=amount, user_id=user_id, stars=stats["stars"]), parse_mode="Markdown")

async def remove_stars(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
   
    try:
        user_id = int(context.args[0])
        amount = int(context.args[1])
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Формат: `/remove_stars <ID> <кількість>`.", parse_mode="Markdown")
        return

    log_action(update.effective_user, f"Запустив /remove_stars для {user_id} ({amount} зірок)")
   
    if user_id not in user_data:
        await update.message.reply_text(get_text(context, "user_not_found").format(user_id=user_id))
        return
       
    stats = get_user_stats(user_id)
    stats["stars"] = max(0, stats["stars"] - amount)
    await update.message.reply_text(get_text(context, "stars_removed").format(amount=amount, user_id=user_id, stars=stats["stars"]), parse_mode="Markdown")

async def set_downloads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
   
    try:
        user_id = int(context.args[0])
        count = int(context.args[1])
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Формат: `/set_downloads <ID> <кількість>`.", parse_mode="Markdown")
        return

    log_action(update.effective_user, f"Запустив /set_downloads для {user_id} (кількість: {count})")
   
    stats = get_user_stats(user_id)
    stats["downloads"] = count
    await update.message.reply_text(get_text(context, "downloads_set").format(user_id=user_id, count=count))

async def send_to(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return

    try:
        user_id = int(context.args[0])
        message_text = " ".join(context.args[1:])
        if not message_text: raise IndexError
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Формат: `/send_to <ID> <повідомлення>`.", parse_mode="Markdown")
        return

    log_action(update.effective_user, f"Запустив /send_to для {user_id}")

    try:
        await context.bot.send_message(chat_id=user_id, text=message_text)
        await update.message.reply_text(get_text(context, "message_sent").format(user_id=user_id))
    except Exception as e:
        await update.message.reply_text(f"❌ Помилка при відправці: {e}")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return

    if not context.args:
        await update.message.reply_text("❓ Використання: `/broadcast <повідомлення>`", parse_mode="Markdown")
        return
   
    message_text = " ".join(context.args)
    log_action(update.effective_user, "Запустив /broadcast")

    await update.message.reply_text(get_text(context, "broadcast_started"))
   
    success_count = 0
    fail_count = 0
    for user_id in list(user_data.keys()):
        try:
            await context.bot.send_message(chat_id=user_id, text=message_text)
            success_count += 1
            await asyncio.sleep(0.1)
        except Exception:
            fail_count += 1
            pass
    await update.message.reply_text(f"✅ Розсилка завершена.\nНадіслано: {success_count}\nНе вдалося: {fail_count}")

async def bot_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
   
    log_action(update.effective_user, "Запустив /bot_stats")
   
    total_users = len(user_data)
    total_downloads = sum(stats.get("downloads", 0) for stats in user_data.values())
    total_tracks = sum(stats.get("tracks", 0) for stats in user_data.values())
    total_videos = sum(stats.get("videos", 0) for stats in user_data.values())
   
    all_sources = {}
    for stats in user_data.values():
        for source, count in stats.get("source_counts", {}).items():
            all_sources[source] = all_sources.get(source, 0) + count
   
    most_popular_source = "N/A"
    if all_sources:
        most_popular_source = max(all_sources, key=all_sources.get).upper()

    await update.message.reply_text(
        get_text(context, "bot_stats_text").format(
            total_users=total_users,
            total_downloads=total_downloads,
            total_tracks=total_tracks,
            total_videos=total_videos,
            most_popular_source=most_popular_source
        ),
        parse_mode="Markdown"
    )

async def user_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
   
    try:
        user_id = int(context.args[0])
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Формат: `/user_stats <ID>`.", parse_mode="Markdown")
        return
   
    await display_user_stats(update.message, context, user_id)

async def display_user_stats(message, context, user_id):
    log_action(message.from_user, f"Запустив /user_stats для {user_id}")
   
    if user_id not in user_data:
        await message.reply_text(get_text(context, "user_not_found").format(user_id=user_id))
        return
   
    stats = get_user_stats(user_id)
    try:
        user_info = await context.bot.get_chat(user_id)
        username = user_info.username or user_info.first_name
    except Exception:
        username = f"ID {user_id}"

    response = f"📊 *Статистика користувача @{username} (ID: {user_id}):*\n"
    response += f"👑 VIP: {'Так' if is_vip_active(user_id) else 'Ні'}\n"
    response += f"🌟 Рівень: {calculate_level(stats['downloads'])}\n"
    response += f"💰 Баланс зірок: {stats['stars']} ⭐\n"
    response += f"⬇️ Завантажено файлів: {stats['downloads']}\n"
    response += f"🎵 Треків: {stats['tracks']}\n"
    response += f"🎬 Відео: {stats['videos']}\n"
    response += f"📌 Останнє джерело: {stats['source'].upper() if stats['source'] != 'N/A' else 'N/A'}\n"
    response += f"🚫 Заблокований: {'Так' if stats['is_blocked'] else 'Ні'}\n"
   
    await message.reply_text(response, parse_mode="Markdown")

async def block_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return

    try:
        user_id = int(context.args[0])
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Формат: `/block <ID>`.", parse_mode="Markdown")
        return

    log_action(update.effective_user, f"Запустив /block для {user_id}")
   
    get_user_stats(user_id)["is_blocked"] = True
    await update.message.reply_text(get_text(context, "user_blocked").format(user_id=user_id))

async def unblock_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
   
    try:
        user_id = int(context.args[0])
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Формат: `/unblock <ID>`.", parse_mode="Markdown")
        return

    log_action(update.effective_user, f"Запустив /unblock для {user_id}")

    get_user_stats(user_id)["is_blocked"] = False
    await update.message.reply_text(get_text(context, "user_unblocked").format(user_id=user_id))
    
# --- НОВІ АДМІН-КОМАНДИ ---
async def grant_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    try:
        user_id = int(context.args[0])
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Формат: `/grant_vip <ID>`")
        return
    
    log_action(update.effective_user, f"Надає VIP для {user_id}")
    stats = get_user_stats(user_id)
    stats["is_vip"] = True
    await update.message.reply_text(get_text(context, "vip_granted").format(user_id=user_id))

async def revoke_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    try:
        user_id = int(context.args[0])
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Формат: `/revoke_vip <ID>`")
        return
    
    log_action(update.effective_user, f"Забирає VIP у {user_id}")
    stats = get_user_stats(user_id)
    stats["is_vip"] = False
    stats["vip_expiration"] = None # Скидаємо і тимчасовий VIP
    await update.message.reply_text(get_text(context, "vip_revoked").format(user_id=user_id))

async def create_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    try:
        code = context.args[0].upper()
        reward = int(context.args[1])
        uses = int(context.args[2])
        days = int(context.args[3])
    except (ValueError, IndexError):
        await update.message.reply_text(get_text(context, "promo_create_format"), parse_mode="Markdown")
        return
    
    expires = datetime.now() + timedelta(days=days)
    promocodes[code] = {"reward": reward, "uses": uses, "expires": expires}
    
    log_action(update.effective_user, f"Створив промокод {code}")
    await update.message.reply_text(
        get_text(context, "promo_created").format(
            code=code, reward=reward, uses=uses, expires=expires.strftime('%Y-%m-%d %H:%M')
        )
    )

async def delete_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    try:
        code = context.args[0].upper()
    except IndexError:
        await update.message.reply_text(get_text(context, "promo_delete_format"), parse_mode="Markdown")
        return
    
    if code in promocodes:
        del promocodes[code]
        log_action(update.effective_user, f"Видалив промокод {code}")
        await update.message.reply_text(get_text(context, "promo_deleted").format(code=code))
    else:
        await update.message.reply_text(get_text(context, "promo_not_found").format(code=code))

async def list_promos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    log_action(update.effective_user, "Запросив список промокодів")
    
    active_promos = {k: v for k, v in promocodes.items() if v['expires'] > datetime.now() and v['uses'] > 0}

    if not active_promos:
        await update.message.reply_text(get_text(context, "promo_list_empty"))
        return
    
    response = get_text(context, "promo_list_header")
    for code, data in active_promos.items():
        expires_str = data['expires'].strftime('%Y-%m-%d %H:%M')
        response += f"`{code}`: {data['reward']}⭐, {data['uses']} використань, до {expires_str}\n"
    
    await update.message.reply_text(response, parse_mode="Markdown")

async def set_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    try:
        username = context.args[0]
        if not username.startswith('@'):
             raise IndexError
    except IndexError:
        await update.message.reply_text(get_text(context, "channel_set_format"), parse_mode="Markdown")
        return
        
    try:
        chat = await context.bot.get_chat(chat_id=username)
        required_channel['id'] = chat.id
        required_channel['username'] = username
        log_action(update.effective_user, f"Встановив канал для підписки: {username}")
        await update.message.reply_text(get_text(context, "channel_set").format(username=username))
    except Exception:
        await update.message.reply_text(get_text(context, "channel_set_error").format(username=username))

async def unset_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    required_channel.clear()
    log_action(update.effective_user, "Вимкнув обов'язкову підписку")
    await update.message.reply_text(get_text(context, "channel_unset"))


# --- ADMIN CONVERSATION HANDLER ---

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
   
    keyboard = [
        [InlineKeyboardButton(get_text(context, "admin_button_add_stars"), callback_data="admin_add_stars")],
        [InlineKeyboardButton(get_text(context, "admin_button_remove_stars"), callback_data="admin_remove_stars")],
        [InlineKeyboardButton(get_text(context, "admin_button_set_downloads"), callback_data="admin_set_downloads")],
        [InlineKeyboardButton(get_text(context, "admin_button_user_stats"), callback_data="admin_user_stats")],
        [InlineKeyboardButton(get_text(context, "admin_button_help"), callback_data="admin_help")],
        [InlineKeyboardButton(get_text(context, "admin_button_exit"), callback_data="admin_exit")]
    ]
    await update.message.reply_text(
        get_text(context, "admin_menu_title"),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return ADMIN_MENU

async def admin_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data

    if action == "admin_add_stars":
        await query.message.reply_text(get_text(context, "admin_prompt_add_stars"))
        return AWAIT_ADD_STARS
    elif action == "admin_remove_stars":
        await query.message.reply_text(get_text(context, "admin_prompt_remove_stars"))
        return AWAIT_REMOVE_STARS
    elif action == "admin_set_downloads":
        await query.message.reply_text(get_text(context, "admin_prompt_set_downloads_id"))
        return AWAIT_SET_DOWNLOADS_ID
    elif action == "admin_user_stats":
        await query.message.reply_text(get_text(context, "admin_prompt_user_stats"))
        return AWAIT_USER_STATS
    elif action == "admin_help":
        await query.message.reply_text(get_text(context, "admin_help_text"), parse_mode="Markdown")
        # Stay in the menu
        return ADMIN_MENU
    elif action == "admin_exit":
        await query.message.edit_text(get_text(context, "admin_action_cancelled"))
        return ConversationHandler.END

async def admin_add_stars_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id, amount = map(int, update.message.text.split())
        context.args = [user_id, amount]
        await add_stars(update, context)
    except (ValueError, IndexError):
        await update.message.reply_text(get_text(context, "admin_invalid_input"))
    return ConversationHandler.END

async def admin_remove_stars_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id, amount = map(int, update.message.text.split())
        context.args = [user_id, amount]
        await remove_stars(update, context)
    except (ValueError, IndexError):
        await update.message.reply_text(get_text(context, "admin_invalid_input"))
    return ConversationHandler.END

async def admin_user_stats_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = int(update.message.text)
        await display_user_stats(update.message, context, user_id)
    except ValueError:
        await update.message.reply_text(get_text(context, "admin_invalid_input"))
    return ConversationHandler.END

async def admin_set_downloads_id_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = int(update.message.text)
        get_user_stats(user_id) # Create if not exists
       
        context.user_data['admin_target_user'] = user_id
        await update.message.reply_text(get_text(context, "admin_prompt_set_downloads_count").format(user_id=user_id))
        return AWAIT_SET_DOWNLOADS_COUNT
    except ValueError:
        await update.message.reply_text(get_text(context, "admin_invalid_input"))
        return ConversationHandler.END

async def admin_set_downloads_count_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        count = int(update.message.text)
        user_id = context.user_data.pop('admin_target_user', None)
        if not user_id:
             await update.message.reply_text("Сталася помилка. Спробуйте знову.")
             return ConversationHandler.END
       
        context.args = [user_id, count]
        await set_downloads(update, context)
    except ValueError:
        await update.message.reply_text(get_text(context, "admin_invalid_input"))
    return ConversationHandler.END

async def admin_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(get_text(context, "admin_action_cancelled"))
    return ConversationHandler.END

# --- MAIN APP ---
application = None 

async def main():
    global application
    application = ApplicationBuilder().token(BOT_TOKEN).build()
   
    # --- User handlers ---
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

    # --- SHOP ---
    application.add_handler(CommandHandler("shop", shop_command))
    application.add_handler(CallbackQueryHandler(shop_callback, pattern=r"^shop_"))
   
    # --- Admin direct command handlers ---
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
    application.add_handler(CommandHandler("unset_channel", unset_channel))
   
    # --- Conversation handlers ---
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SELECTING: [CallbackQueryHandler(select_type, pattern=r'^(audio|video)$')],
            SELECT_SOURCE: [CallbackQueryHandler(select_source, pattern=r'^(yt|sc|tt)$')],
            ASK_QUERY: [CallbackQueryHandler(select_quality, pattern=r'^\d{3,4}$')],
            DOWNLOAD: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_download)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("restart", restart),
        ],
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
            AWAIT_SET_DOWNLOADS_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_set_downloads_count_input)],
        },
        fallbacks=[CommandHandler("cancel", admin_cancel)],
        per_message=False
    )

    application.add_handler(conv_handler)
    application.add_handler(admin_conv_handler)
   
    # --- Inline and other handlers ---
    application.add_handler(InlineQueryHandler(inline_query))
    application.add_handler(ChosenInlineResultHandler(chosen_inline_result))
    application.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUPS & ~filters.COMMAND, text_message_handler))

    print("🤖 Бот активний!")
    asyncio.create_task(process_queue())
    await application.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
