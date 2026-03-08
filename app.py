from flask import Flask, render_template, request, jsonify, session, send_file
from math import ceil
import os
import json
import re
import asyncio
import tempfile
import shutil
import yt_dlp
import requests
from datetime import datetime, timedelta
from functools import wraps
import hashlib
import hmac

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-this-please'

# ================= КОНФІГУРАЦІЯ =================
BOT_TOKEN = "8213254007:AAFQkGiQqi1YirAvF4VuGcF3CL6WpqFVSGA"
DATA_FILE = 'data/bot_data.json'
PORT = int(os.environ.get('PORT', 5000))

SHOP_PRICES = {
    "vip_1_day": 200,
    "vip_7_days": 1000,
    "vip_30_days": 3500,
    "unlimited_24h": 500,
    "priority_pass": 50
}

COSTS = {
    "audio": {"128": 10, "192": 15, "256": 20, "base_find": 15, "base_random": 10},
    "video": {"360": 25, "480": 35, "720": 50, "1080": 70}
}

# ================= ДАНІ =================
def ensure_data_dir():
    os.makedirs('data', exist_ok=True)

def load_data():
    ensure_data_dir()
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {'user_data': {}, 'promocodes': {}}
    return {'user_data': {}, 'promocodes': {}}

def save_data(data):
    ensure_data_dir()
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_user_stats(data, user_id):
    user_id = str(user_id)
    if user_id not in data['user_data']:
        data['user_data'][user_id] = {
            "downloads": 0, "tracks": 0, "videos": 0,
            "source": "N/A", "stars": 50, "is_vip": False,
            "vip_expiration": None, "used_promos": [],
            "unlimited_dl_expires": None, "priority_passes": 0
        }
    return data['user_data'][user_id]

def is_vip_active(stats):
    if stats.get("is_vip", False):
        return True
    vip_exp = stats.get("vip_expiration")
    if vip_exp:
        if isinstance(vip_exp, str):
            vip_exp = datetime.fromisoformat(vip_exp)
        if datetime.now() < vip_exp:
            return True
    return False

def is_unlimited_active(stats):
    unlim_exp = stats.get("unlimited_dl_expires")
    if unlim_exp:
        if isinstance(unlim_exp, str):
            unlim_exp = datetime.fromisoformat(unlim_exp)
        if datetime.now() < unlim_exp:
            return True
    return False

def get_final_cost(user_id, base_cost, data):
    stats = get_user_stats(data, user_id)
    if is_unlimited_active(stats):
        return 0
    if is_vip_active(stats):
        from math import ceil
        return ceil(base_cost * 0.5)
    return base_cost

# ================= TELEGRAM AUTH =================
def verify_telegram_auth(data, bot_token):
    if 'hash' not in data:
        return False
    
    check_hash = data.pop('hash')
    data_check_arr = []
    for key, value in sorted(data.items()):
        data_check_arr.append(f'{key}={value}')
    data_check_string = '\n'.join(data_check_arr)
    
    secret_key = hashlib.sha256(bot_token.encode()).digest()
    hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    
    return hmac.compare_digest(hash, check_hash)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Not authenticated'}), 401
        return f(*args, **kwargs)
    return decorated_function

# ================= ЗАВАНТАЖЕННЯ =================
def clean_filename(name):
    return re.sub(r'[\/*?:"<>|]', '', name)

async def download_media(query, audio=True, quality="best"):
    tmpdir = tempfile.mkdtemp()
    try:
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
            info = ydl.extract_info(query, download=True)
            
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
            
            file = files[0]
            safe_name = clean_filename(file)
            safe_path = os.path.join(tmpdir, safe_name)
            
            if safe_name != file:
                os.rename(os.path.join(tmpdir, file), safe_path)
            
            title = clean_filename(entry.get("title", "Без назви"))
            return safe_path, title, tmpdir
    except Exception as e:
        print(f"Download error: {e}")
        shutil.rmtree(tmpdir)
        return None, None, None

# ================= РОУТИ =================
@app.route('/')
def index():
    if 'user_id' in session:
        return render_template('index.html', logged_in=True, user=session)
    return render_template('index.html', logged_in=False)

@app.route('/auth', methods=['POST'])
def auth():
    data = request.form.to_dict()
    if verify_telegram_auth(data, BOT_TOKEN):
        user_id = data.get('id')
        session['user_id'] = user_id
        session['username'] = data.get('username', 'User')
        session['first_name'] = data.get('first_name', '')
        session['photo_url'] = data.get('photo_url', '')
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': 'Invalid auth'})

@app.route('/logout')
def logout():
    session.clear()
    return jsonify({'success': True})

@app.route('/api/stats')
@login_required
def api_stats():
    data = load_data()
    user_id = session['user_id']
    stats = get_user_stats(data, user_id)
    
    return jsonify({
        'stars': stats['stars'],
        'downloads': stats['downloads'],
        'tracks': stats['tracks'],
        'videos': stats['videos'],
        'vip': is_vip_active(stats),
        'unlimited': is_unlimited_active(stats)
    })

@app.route('/api/download', methods=['POST'])
@login_required
def api_download():
    data = load_data()
    user_id = session['user_id']
    stats = get_user_stats(data, user_id)
    
    query = request.form.get('query', '').strip()
    media_type = request.form.get('type', 'audio')
    quality = request.form.get('quality', '192')
    
    if not query:
        return jsonify({'success': False, 'error': 'Введіть назву або посилання'})
    
    # Додаємо пошук якщо не URL
    url_pattern = re.compile(r'https?://[^\s/$.?#].[^\s]*')
    if not url_pattern.match(query):
        query = f"ytsearch1:{query}"
    
    base_cost = COSTS[media_type].get(quality, COSTS[media_type].get('192', 15))
    cost = get_final_cost(user_id, base_cost, data)
    
    if stats['stars'] < cost:
        return jsonify({'success': False, 'error': f'Недостатньо зірок. Потрібно: {cost}, Є: {stats["stars"]}'})
    
    stats['stars'] -= cost
    
    try:
        filepath, title, tmpdir = asyncio.run(download_media(query, audio=(media_type == 'audio'), quality=quality))
        
        if not filepath:
            return jsonify({'success': False, 'error': 'Нічого не знайдено'})
        
        stats['downloads'] += 1
        if media_type == 'audio':
            stats['tracks'] += 1
        else:
            stats['videos'] += 1
        
        save_data(data)
        
        # Зберігаємо шлях у сесії для завантаження
        session['download_file'] = filepath
        session['download_title'] = title
        
        return jsonify({
            'success': True, 
            'title': title,
            'download_url': '/download/file'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/download/file')
@login_required
def download_file():
    if 'download_file' not in session:
        return 'File not found', 404
    
    filepath = session['download_file']
    title = session.get('download_title', 'download')
    
    if os.path.exists(filepath):
        return send_file(filepath, as_attachment=True, download_name=f"{title}.mp3")
    return 'File not found', 404

@app.route('/api/shop/buy', methods=['POST'])
@login_required
def shop_buy():
    data = load_data()
    user_id = session['user_id']
    stats = get_user_stats(data, user_id)
    
    item = request.form.get('item')
    prices = {
        "vip_1_day": (SHOP_PRICES["vip_1_day"], 1, 'days'),
        "vip_7_days": (SHOP_PRICES["vip_7_days"], 7, 'days'),
        "vip_30_days": (SHOP_PRICES["vip_30_days"], 30, 'days'),
        "unlimited_24h": (SHOP_PRICES["unlimited_24h"], 24, 'hours'),
        "priority_pass": (SHOP_PRICES["priority_pass"], 1, 'pass')
    }
    
    if item not in prices:
        return jsonify({'success': False, 'error': 'Невірний товар'})
    
    cost, duration, dtype = prices[item]
    
    if stats['stars'] < cost:
        return jsonify({'success': False, 'error': f'Недостатньо зірок. Потрібно: {cost}'})
    
    stats['stars'] -= cost
    
    if 'vip' in item:
        curr = stats.get("vip_expiration")
        if curr:
            if isinstance(curr, str):
                curr = datetime.fromisoformat(curr)
            if curr < datetime.now():
                curr = datetime.now()
        else:
            curr = datetime.now()
        stats["vip_expiration"] = (curr + timedelta(days=duration)).isoformat()
    elif item == "unlimited_24h":
        curr = stats.get("unlimited_dl_expires")
        if curr:
            if isinstance(curr, str):
                curr = datetime.fromisoformat(curr)
            if curr < datetime.now():
                curr = datetime.now()
        else:
            curr = datetime.now()
        stats["unlimited_dl_expires"] = (curr + timedelta(hours=duration)).isoformat()
    elif item == "priority_pass":
        stats["priority_passes"] = stats.get("priority_passes", 0) + 1
    
    save_data(data)
    return jsonify({'success': True, 'message': f'Куплено: {item}'})

@app.route('/api/promo', methods=['POST'])
@login_required
def promo():
    data = load_data()
    user_id = session['user_id']
    stats = get_user_stats(data, user_id)
    
    code = request.form.get('code', '').upper()
    promo = data.get('promocodes', {}).get(code)
    
    if not promo:
        return jsonify({'success': False, 'error': 'Промокод не знайдено'})
    
    if datetime.now() > datetime.fromisoformat(promo['expires']):
        return jsonify({'success': False, 'error': 'Промокод закінчився'})
    
    if promo['uses'] <= 0:
        return jsonify({'success': False, 'error': 'Промокод використано'})
    
    if code in stats.get('used_promos', []):
        return jsonify({'success': False, 'error': 'Ви вже використовували цей промокод'})
    
    reward = promo['reward']
    stats['stars'] += reward
    stats['used_promos'] = stats.get('used_promos', []) + [code]
    promo['uses'] -= 1
    
    save_data(data)
    return jsonify({'success': True, 'message': f'+{reward} ⭐'})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"🌐 Веб-сайт запущено на порту {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
