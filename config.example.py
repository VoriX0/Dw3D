# config.example.py
# Скопируйте этот файл в config.py и заполните своими данными

TELEGRAM_BOT_TOKEN = "ваш_токен_бота"
DEEPSEEK_API_KEY = "ваш_api_ключ_deepseek"

# Прокси (если нужен) – пример для SOCKS5:
# PROXY_URL = "socks5://логин:пароль@ip:порт"
# Для HTTP: "http://user:pass@ip:port"
# Если прокси не используется, оставьте PROXY_URL = None
PROXY_URL = None

# Дополнительные настройки (опционально, можно оставить как есть)
MAX_TOKENS = 50000
ANTI_SPAM_RESET_DELAY = 5
SAVE_FOLDER = "user_histories"
LOG_PATH = "bot.log"