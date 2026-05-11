FROM python:3.10-slim

WORKDIR /app

# Копируем только requirements.txt сначала (для кэширования слоя)
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь код, кроме того, что в .dockerignore
COPY . .

# Команда запуска
CMD ["python", "bot.py"]