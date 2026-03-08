FROM python:3.11-slim

WORKDIR /app

# Встановлюємо FFmpeg
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

# Копіюємо файли
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Запускаємо обидва процеси
CMD ["sh", "-c", "python main.py & python app.py"]
