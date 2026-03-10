import os
import json
from flask import Flask, render_template, jsonify

app = Flask(__name__)

# Твій файл бази даних, який лежить в корені
DATA_FILE = 'bot_data.json'

def get_user_data(user_id):
    if not os.path.exists(DATA_FILE):
        return {"stars": 0, "is_vip": False}
    
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # Шукаємо юзера в секції 'users'
            users = data.get('users', {})
            user_info = users.get(str(user_id), {"stars": 0, "is_vip": False})
            return user_info
    except Exception as e:
        print(f"Помилка читання файлу: {e}")
        return {"stars": 0, "is_vip": False}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/user/<int:user_id>')
def api_user(user_id):
    data = get_user_data(user_id)
    return jsonify(data)

if __name__ == '__main__':
    # Railway автоматично підхопить цей порт
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
