from flask import Flask, render_template_string, jsonify, request
import json
import os
from datetime import datetime

app = Flask(__name__)
DATA_FILE = 'data/bot_data.json'

def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r') as f:
            return json.load(f)
    return {'user_data': {}}

@app.route('/')
def index():
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>🎵 Music Bot</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body { 
                background: linear-gradient(135deg, #1a1a2e, #16213e); 
                color: white; 
                font-family: Arial, sans-serif;
                margin: 0;
                padding: 20px;
                text-align: center;
            }
            .container { max-width: 800px; margin: 0 auto; }
            .card {
                background: rgba(255,255,255,0.1);
                padding: 30px;
                border-radius: 20px;
                margin: 20px 0;
                backdrop-filter: blur(10px);
            }
            h1 { font-size: 2.5em; margin-bottom: 10px; }
            .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 15px; margin: 20px 0; }
            .stat { background: rgba(255,255,255,0.05); padding: 20px; border-radius: 12px; }
            .stat-value { font-size: 2em; font-weight: bold; color: #00d9ff; }
            .stat-label { font-size: 0.9em; opacity: 0.8; }
            .btn {
                background: linear-gradient(135deg, #667eea, #764ba2);
                color: white;
                padding: 15px 30px;
                border: none;
                border-radius: 12px;
                font-size: 16px;
                cursor: pointer;
                margin: 10px;
                text-decoration: none;
                display: inline-block;
            }
            .btn:hover { transform: translateY(-2px); box-shadow: 0 10px 20px rgba(0,0,0,0.3); }
            #telegram-login { margin: 20px 0; }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="card">
                <h1>🎵 Music Downloader</h1>
                <p>Завантажуй музику з Telegram бота</p>
                <div id="telegram-login"></div>
                <div id="user-panel" style="display:none;">
                    <div class="stats" id="stats"></div>
                </div>
                <a href="https://t.me/YourBotUsername" class="btn">🚀 Відкрити бота</a>
            </div>
        </div>
        
        <script src="https://telegram.org/js/telegram-widget.js?22" 
                data-telegram-login="YourBotUsername" 
                data-size="large" 
                data-auth-url="/auth" 
                data-request-access="write"></script>
        
        <script>
            // Перевірка авторизації
            fetch('/api/stats')
                .then(r => r.json())
                .then(data => {
                    if (data.success) {
                        document.getElementById('telegram-login').style.display = 'none';
                        document.getElementById('user-panel').style.display = 'block';
                        document.getElementById('stats').innerHTML = `
                            <div class="stat">
                                <div class="stat-value">${data.stars || 0}</div>
                                <div class="stat-label">⭐ Зірки</div>
                            </div>
                            <div class="stat">
                                <div class="stat-value">${data.downloads || 0}</div>
                                <div class="stat-label">📥 Завантажень</div>
                            </div>
                            <div class="stat">
                                <div class="stat-value">${data.tracks || 0}</div>
                                <div class="stat-label">🎵 Треків</div>
                            </div>
                        `;
                    }
                });
        </script>
    </body>
    </html>
    '''

@app.route('/auth')
def auth():
    # Проста авторизація через Telegram
    user_id = request.args.get('id')
    if user_id:
        return f'''
        <script>
            localStorage.setItem('user_id', '{user_id}');
            window.location.href = '/';
        </script>
        '''
    return '/'

@app.route('/api/stats')
def api_stats():
    user_id = request.args.get('user_id') or request.headers.get('X-User-Id')
    if not user_id:
        return jsonify({'success': False})
    
    data = load_data()
    user_data = data.get('user_data', {}).get(str(user_id), {})
    
    return jsonify({
        'success': True,
        'stars': user_data.get('stars', 0),
        'downloads': user_data.get('downloads', 0),
        'tracks': user_data.get('tracks', 0),
        'videos': user_data.get('videos', 0)
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"🌐 Сайт запущено на порту {port}")
    app.run(host='0.0.0.0', port=port, debug=True)
