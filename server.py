import os
import json
from flask import Flask, render_template, jsonify

app = Flask(__name__)

# Шлях до твого файлу (переконайся, що він такий же, як у main.py)
DATA_FILE = 'data/bot_data.json'

def get_user_data(user_id):
    if not os.path.exists(DATA_FILE):
        return {"stars": 0, "is_vip": False}
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
        # Повертаємо дані конкретного юзера
        return data.get('users', {}).get(str(user_id), {"stars": 0, "is_vip": False})

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/user/<int:user_id>')
def api_user(user_id):
    user_info = get_user_data(user_id)
    return jsonify(user_info)

if __name__ == '__main__':
    # Railway сам призначить порт через змінну оточення
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)