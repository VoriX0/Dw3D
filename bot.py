import asyncio
import json
import logging
import sys
import aiofiles
import aiofiles.os as async_os
from pathlib import Path
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import F
from openai import AsyncOpenAI
import tiktoken
from typing import Optional
import httpx
from datetime import datetime, timezone
from aiogram.client.session.aiohttp import AiohttpSession

from config import *

# Проверка, что токены заданы (на случай, если config.py пустой)
if not TELEGRAM_BOT_TOKEN or not DEEPSEEK_API_KEY:
    print("Ошибка: TELEGRAM_BOT_TOKEN и DEEPSEEK_API_KEY должны быть заданы в config.py")
    sys.exit(1)

# Если PROXY_URL задан пустой строкой, превращаем в None
if PROXY_URL == "":
    PROXY_URL = None


LOG_PATH = Path(LOG_PATH).absolute()
SAVE_FOLDER = Path(SAVE_FOLDER).absolute()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Конфигурация
API_TOKEN = "7886096966:AAH6VBJjwi82KYtJUXx2hm_2wpZrNKAu6zY"
DEEPSEEK_API_KEY = "sk-05f9e78cc1d649469e4afdcf9343cc7c"
SAVE_FOLDER = Path("user_histories").absolute()
MAX_TOKENS = 50000
ANTI_SPAM_RESET_DELAY = 5

# Промпты
MainRole = '''Роль: Мастер D&D. Создаешь уникальные локации, битвы, сюжет и прокачку. Жестко следи за правилами. 
Персонаж: При создании (имя/класс/предыстория) → уровень 0, инвентарь пуст. Начинай сюжет сразу.

Инвентарь:  
- Деньги — валюта (с монстров/квестов). Храни как отдельный предмет.  
- Предметы: из сундуков/монстров/квестов/крафта (если ресурсы+навык)/покупки.  
- Покупка: При нехватке денег → отказ (иногда мини-квест за предмет).  
- Изучение предмета: Выдай характеристики + навык/опыт.  
- Колдованные предметы: Не в инвентарь, исчезают через 2-4 хода.  
→ ВСЕГДА выводи инвентарь при описании ситуации!  

КАТЕГОРИЧЕСКИЕ ЗАПРЕТЫ (проверяй каждый ввод):  
1. Предметы: Использовать только при наличии в инвентаре.  
2. Заклинания/способности: Только изученные.  
3. Уровень: Запрещай предметы/заклинания не по уровню.  
→ При нарушении: объясни ошибку, требуй новый ввод.  -
4. Анализируй действия игрока по отдельности, если у игрока не получается первое действие, значит все последующие, связанные действия сразу провалены.
5. Если действия игрока маловероятно или тяжело выполнимо, то скорее всего оно будет неудачным. Не бойся обрывать действия игрока, если этого требует логика

Прогресс:  
- Уровень: Даёшь ТОЛЬКО ты (за победы/квесты/сюжет). Определяет доступ к экипировке/заклинаниям.  
- Смерть: Возврат на контрольную точку. Штраф: -50% уровней/предметов, -1 навык. Причины: смертельный урон/казнь (нарушение законов).
- Здесь не кубики решают, удалось ли действие, а ты. Решение ты принимаешь исходя из умений, уровня, предыстории и других аспектов персонажа. Если у игрока не получается сложное действие это нормально. Вот пример хорошего ответа: Игрок-волшебник: Я взламываю замок и забираю все вещи из сундука. Мастер: У тебя не получилось взломать замок, а скрежетом ты привлёк внимание охраны.

Контекст: Действия игрока должны строго соответствовать ситуации (пример: нельзя отравить короля при атаке орков).  

Сюжет:  
- Основная задача → выполнение = конец игры.  
- Локации уникальны: существа (добрые/злые), деревни, вражда (класс/репутация/атака игрока).  

Формат ответов:  
- Не более 400-600 токенов (~800-1000 символов).  
- Для парсинга использовать HTML-теги
- Запрещено: тег code, br и s, спецсимволы, *, #.  
- Разрешено: b, i, u, pre (для парсинга в боте).'''

Plot = 'Все действия этого запроса нужно сделать 1 раз. Ты мастер игры D&D, основываясь на правилах, создай начало для сюжета для одного игрока. Принцип: Введение, предупреждение о пустом инвентаре и 1 уровне, просьба создания пресонажа. В конце своего ответа попроси пользователя создать персонажа по принципу: Имя, класс, предыстория.'

def create_bot_with_proxy():
    
    if PROXY_URL:
        logger.info(f"Бот использует этот прокси: {PROXY_URL.split('://')[0]}://***")
        session = AiohttpSession(proxy=PROXY_URL)
        return Bot(token=TELEGRAM_BOT_TOKEN, session=session)
    else:
        logger.info(" ^= ^`     ^a           ^a       ^l   ^c   ^b ^a ^o")
        return Bot(token=TELEGRAM_BOT_TOKEN)


# Сессии и блокировки
user_sessions: dict[int, "UserSession"] = {}
user_locks: dict[int, asyncio.Lock] = {}

class UserSession:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.history = []
        self._initialized = asyncio.Event()
        self.last_reset_time: Optional[datetime] = None
        self.encoder = tiktoken.encoding_for_model("gpt-3.5-turbo")
        asyncio.create_task(self._init_history())

    async def _init_history(self):
        try:
            await async_os.makedirs(SAVE_FOLDER, exist_ok=True)
            file_path = SAVE_FOLDER / f"{self.user_id}.json"
            if await async_os.path.exists(file_path):
                async with aiofiles.open(file_path, "r", encoding='utf-8') as f:
                    self.history = json.loads(await f.read())
            else:
                self.history = [
                    {"role": "system", "content": MainRole},
                    {"role": "user", "content": Plot}
                ]
                await self._save_history()
        except Exception as e:
            logger.error(f"Session init error: {e}")
            self.history = [
                {"role": "system", "content": MainRole},
                {"role": "user", "content": Plot}
            ]
        finally:
            self._initialized.set()

    async def clean_history(self):
        def sync_clean(history):
            encoder = tiktoken.encoding_for_model("gpt-3.5-turbo")
            total = sum(len(encoder.encode(m["content"])) for m in history)
            if total <= MAX_TOKENS:
                return history
            new_history = history[:2]
            current_total = sum(len(encoder.encode(m["content"])) for m in new_history)
            for m in reversed(history[2:]):
                token_count = len(encoder.encode(m["content"]))
                if current_total + token_count > MAX_TOKENS:
                    break
                new_history.append(m)
                current_total += token_count
            return new_history

        self.history = await asyncio.to_thread(sync_clean, self.history)

    async def reset(self):
        file_path = SAVE_FOLDER / f"{self.user_id}.json"
        if await async_os.path.exists(file_path):
            await async_os.remove(file_path)
        self.history = [
            {"role": "system", "content": MainRole},
            {"role": "user", "content": Plot}
        ]
        await self._save_history()

    async def add_user_message(self, text: str):
        await self.wait_initialized()
        self.history.append({"role": "user", "content": text})

    async def add_bot_message(self, text: str):
        self.history.append({"role": "assistant", "content": text})
        await self._save_history()

    async def _save_history(self):
        try:
            file_path = SAVE_FOLDER / f"{self.user_id}.json"
            async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
                await f.write(json.dumps(self.history, ensure_ascii=False, indent=2))
        except Exception as e:
            logger.error(f"Save history error: {e}")

    async def wait_initialized(self):
        await self._initialized.wait()

# Инициализация бота и диспетчера
bot = create_bot_with_proxy()
dp = Dispatcher()
# Клавиатуры

def start_keyboard():
    return types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="⚔️Начать приключение!⚔️")]
        ],
        resize_keyboard=True
    )


def main_keyboard():
    return types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text="⭕️Сбросить прогресс⭕️")]],
        resize_keyboard=True,
        is_persistent=True
    )


def confirmation_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="♻️Да", callback_data="reset_yes"),
        InlineKeyboardButton(text="❌Нет", callback_data="reset_no")
    ]])

# Индикатор заполнения
async def show_typing_indicator(chat_id: int) -> tuple[Optional[int], Optional[int]]:
    try:
        e = await bot.send_message(chat_id, "📝")
        await asyncio.sleep(0.1)
        t = await bot.send_message(chat_id, "Прописываю сюжет...")
        return e.message_id, t.message_id
    except Exception as e:
        logger.error(f"Ошибка индикатора: {e}")
        return None, None

def fix_telegram_html(text: str) -> str:
    """Быстрое исправление HTML для Telegram"""
    import re
    # Исправляем самозакрывающиеся br
    text = re.sub(r'<br\s*/?\s*>', '\n', text, flags=re.IGNORECASE)
    # Удаляем другие проблемные теги
    text = re.sub(r'</?(div|p|span|code|s)[^>]*>', '', text, flags=re.IGNORECASE)
    return text

# Общая логика обработки
async def process_user_message_directly(message: types.Message, task_type: str):
    uid = message.from_user.id
    session = user_sessions.setdefault(uid, UserSession(uid))
    await session.wait_initialized()
    
    now = datetime.now(timezone.utc)
    if task_type == 'regular' and session.last_reset_time:
        d = (now - session.last_reset_time).total_seconds()
        if d < ANTI_SPAM_RESET_DELAY:
            return f"⌛️ Подождите ещё {ANTI_SPAM_RESET_DELAY - int(d)} сек"
    
    await session.clean_history()
    await session.add_user_message(message.text)
    
    async with httpx.AsyncClient() as hc:
        aclient = AsyncOpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url="https://api.deepseek.com/v1/",
            http_client=hc
        )
        resp = await aclient.chat.completions.create(
            model="deepseek-chat",
            messages=session.history,
            max_tokens=800 if task_type == "start" else 1000
        )
    
    answer = resp.choices[0].message.content
    answer = fix_telegram_html(answer)
    await session.add_bot_message(answer)
    return answer

# Хэндлеры и анти-лока
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    uid = message.from_user.id
    if uid in user_sessions:
        return await message.answer("⚠️ Сессия уже создана.")
    user_sessions[uid] = UserSession(uid)
    user_locks[uid] = asyncio.Lock()
    await message.answer("🎲 Добро пожаловать в мир D&D!", reply_markup=start_keyboard())

@dp.message(F.text == "⚔️Начать приключение!⚔️")
async def handle_start_adventure(msg: types.Message):
    uid = msg.from_user.id
    lock = user_locks.setdefault(uid, asyncio.Lock())
    if lock.locked():
        return await msg.answer("⌛️ Запрос уже обрабатывается, пожалуйста, подождите...")
    async with lock:
        emoji_id, text_id = await show_typing_indicator(msg.chat.id)
        ans = await process_user_message_directly(msg, "start")
        if emoji_id: await bot.delete_message(msg.chat.id, emoji_id)
        if text_id: await bot.delete_message(msg.chat.id, text_id)
        return await msg.answer(ans, reply_markup=main_keyboard(), parse_mode='HTML')

@dp.message(F.text == "⭕️Сбросить прогресс⭕️")
async def handle_reset_request(msg: types.Message):
    uid = msg.from_user.id
    lock = user_locks.setdefault(uid, asyncio.Lock())
    if lock.locked():
        return await msg.answer("⌛️ Запрос уже обрабатывается, нельзя сбросить прогресс сейчас.")
    session = user_sessions.get(uid)
    now = datetime.now(timezone.utc)
    if session and session.last_reset_time:
        elapsed = (now - session.last_reset_time).total_seconds()
        if elapsed < ANTI_SPAM_RESET_DELAY:
            return await msg.answer(f"⌛️ Подождите ещё {ANTI_SPAM_RESET_DELAY - int(elapsed)} сек перед повторным сбросом")
    async with lock:
        if session:
            session.last_reset_time = now
        return await msg.answer("⚠️ Вы уверены, что хотите полностью сбросить прогресс?", reply_markup=confirmation_keyboard())

@dp.callback_query(lambda c: c.data in ["reset_yes", "reset_no"])
async def on_reset_confirm(c: types.CallbackQuery):
    uid = c.from_user.id
    lock = user_locks.setdefault(uid, asyncio.Lock())
    if lock.locked():
        await c.answer()
        return await c.message.answer("⌛️ Дождитесь окончания обработки перед сбросом")
    session = user_sessions.get(uid)
    if c.data == "reset_yes" and session:
        session.last_reset_time = datetime.now(timezone.utc)
        await session.reset()
        await c.message.edit_reply_markup(None)
        return await c.message.answer("✅ Прогресс сброшен! Новая сессия создана!", reply_markup=start_keyboard())
    await c.message.edit_reply_markup(None)
    return await c.message.answer("❌ Сброс отменен. Продолжаем приключение!", reply_markup=main_keyboard())

@dp.message(F.text)
async def handle_message(msg: types.Message):
    uid = msg.from_user.id
    lock = user_locks.setdefault(uid, asyncio.Lock())
    if lock.locked():
        return await msg.answer("⌛️ Запрос уже обрабатывается, пожалуйста, подождите...")
    async with lock:
        emoji_id, text_id = await show_typing_indicator(msg.chat.id)
        ans = await process_user_message_directly(msg, "regular")
        if emoji_id: await bot.delete_message(msg.chat.id, emoji_id)
        if text_id: await bot.delete_message(msg.chat.id, text_id)
        return await msg.answer(ans, reply_markup=main_keyboard(), parse_mode='HTML')

@dp.message()
async def handle_non_text(message: types.Message):
    uid = message.from_user.id
    lock = user_locks.setdefault(uid, asyncio.Lock())
    if lock.locked():
        return await message.answer("⌛️ Запрос уже обрабатывается, пожалуйста, подождите...")
    return await message.answer("⚠️ Бот принимает только текстовые сообщения!")

async def main():
    await async_os.makedirs(SAVE_FOLDER, exist_ok=True)
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot, skip_updates=True)

if __name__ == "__main__":
    asyncio.run(main())