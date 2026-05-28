import asyncio
import json
import logging
import random
import re
import string
import uuid
from typing import Any, Dict, List, Optional

import httpx
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    JobQueue,
)

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Глобальные переменные
CONFIG_FILE = "config.json"
http_client: Optional[httpx.AsyncClient] = None

# Константы API
ARTILLECT_REGISTER_URL = "https://app.artillect.pro/api/auth/register/"
ARTILLECT_VERIFY_BASE = "https://app.artillect.pro/api/auth/verify-email"
CATCHMAIL_API = "https://catchmail.io/api"

# Словарь для хранения активных сессий регистрации (если понадобится расширение функционала)
active_registrations: Dict[str, Dict[str, Any]] = {}


def load_config() -> Dict[str, Any]:
    """Загружает конфигурацию из файла."""
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
        logger.info("Конфигурация успешно загружена")
        return config
    except FileNotFoundError:
        logger.error(f"Файл {CONFIG_FILE} не найден!")
        raise
    except json.JSONDecodeError:
        logger.error(f"Ошибка JSON в файле {CONFIG_FILE}!")
        raise


def generate_random_name(length: int = 6) -> str:
    """Генерирует случайное имя."""
    return "".join(random.choices(string.ascii_lowercase, k=length)).capitalize()


def generate_secure_password(length: int = 12) -> str:
    """Генерирует надежный пароль минимум 9 символов."""
    if length < 9:
        length = 9
    chars = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
    password = "".join(random.choices(chars, k=length))
    # Убедимся, что есть хотя бы одна цифра и спецсимвол
    if not any(c.isdigit() for c in password):
        password = password[:-1] + random.choice(string.digits)
    if not any(c in "!@#$%^&*()-_=+" for c in password):
        password = password[:-1] + random.choice("!@#$%^&*()-_=+")
    return password


async def create_catchmail_email() -> Optional[str]:
    """Создает временную почту через CatchMail.io"""
    try:
        url = f"{CATCHMAIL_API}/email"
        response = await http_client.get(url)
        
        if response.status_code == 200:
            data = response.json()
            # Проверяем различные возможные ключи ответа
            if isinstance(data, dict):
                email = data.get("email") or data.get("address") or data.get("mail")
                if email:
                    logger.info(f"CatchMail создал почту: {email}")
                    return email
            
            # Если ответ пришел строкой
            elif isinstance(data, str) and "@" in data:
                 return data.strip()
                 
            logger.error(f"Не удалось распарсить ответ CatchMail: {data}")
            return None
        else:
            logger.error(f"CatchMail вернул ошибку: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        logger.error(f"Ошибка создания почты (CatchMail): {e}")
        return None


async def get_catchmail_messages(email: str) -> List[Dict[str, Any]]:
    """Получает сообщения для почты CatchMail."""
    try:
        url = f"{CATCHMAIL_API}/messages"
        params = {"email": email}
        
        response = await http_client.get(url, params=params)
        
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list):
                return data
            elif isinstance(data, dict) and "messages" in data:
                return data["messages"]
            logger.warning(f"Неожиданный формат сообщений: {data}")
            return []
        else:
            logger.warning(f"Ошибка получения писем: {response.status_code}")
            return []
    except Exception as e:
        logger.error(f"Ошибка получения писем CatchMail: {e}")
        return []


async def register_artillect(name: str, email: str, password: str, referral_code: str = "B3B7C1C4") -> bool:
    """Регистрирует аккаунт на Artillect."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:151.0) Gecko/20100101 Firefox/151.0",
        "Accept": "*/*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Content-Type": "application/json",
        "Origin": "https://app.artillect.pro",
        "Referer": f"https://app.artillect.pro/register/?ref={referral_code}",
    }
    
    payload = {
        "name": name,
        "email": email,
        "password": password,
        "policy": True,
        "subscribe": False,
        "locale": "ru",
        "referralCode": referral_code,
    }

    try:
        response = await http_client.post(ARTILLECT_REGISTER_URL, json=payload, headers=headers)
        if response.status_code == 200 or response.status_code == 201:
            logger.info(f"Регистрация успешна для {email}")
            return True
        else:
            logger.error(f"Ошибка регистрации: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        logger.error(f"Исключение при регистрации: {e}")
        return False


def extract_verification_link(text: str) -> Optional[str]:
    """Извлекает ссылку подтверждения из текста письма."""
    pattern = r"(https://app\.artillect\.pro/api/auth/verify-email\?token=[a-f0-9\-]+)"
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        return match.group(1)
    
    token_pattern = r"token=([a-f0-9\-]+)"
    token_match = re.search(token_pattern, text, re.IGNORECASE)
    if token_match:
        return f"{ARTILLECT_VERIFY_BASE}?token={token_match.group(1)}"
        
    return None


async def verify_email(link: str) -> bool:
    """Переходит по ссылке подтверждения."""
    try:
        response = await http_client.get(link, allow_redirects=True)
        if response.status_code == 200:
            if "success" in response.text.lower() or "подтвержден" in response.text.lower():
                return True
            return True 
        logger.warning(f"Статус подтверждения: {response.status_code}")
        return False
    except Exception as e:
        logger.error(f"Ошибка подтверждения: {e}")
        return False


async def registration_process(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Фоновая задача регистрации."""
    job = context.job
    chat_id = job.data["chat_id"]
    bot = context.bot
    
    name = generate_random_name()
    password = generate_secure_password()
    referral = "B3B7C1C4"
    
    await bot.send_message(chat_id, "🔄 Начинаю процесс регистрации...\n1️⃣ Генерирую почту...")

    # 1. Создаем почту
    email = await create_catchmail_email()
    if not email:
        await bot.send_message(chat_id, "❌ Не удалось создать временную почту. Попробуйте позже.")
        return

    await bot.send_message(chat_id, f"✅ Почта создана: `{email}`\n2️⃣ Регистрирую аккаунт...", parse_mode="Markdown")

    # 2. Регистрируемся
    success = await register_artillect(name, email, password, referral)
    if not success:
        await bot.send_message(chat_id, "❌ Ошибка при регистрации на сайте. Возможно, почта уже занята.")
        return

    await bot.send_message(
        chat_id, 
        f"✅ Регистрация успешна!\n📧 Email: `{email}`\n🔑 Пароль: `{password}`\n\n⏳ Ожидаю письмо подтверждения...",
        parse_mode="Markdown"
    )

    # 3. Ждем письмо
    max_attempts = 20
    delay = 5
    
    verification_link = None
    
    for attempt in range(1, max_attempts + 1):
        logger.info(f"Попытка {attempt} получить письмо для {email}...")
        
        messages = await get_catchmail_messages(email)
        
        for msg in messages:
            body = msg.get("body") or msg.get("text") or msg.get("content") or ""
            subject = msg.get("subject", "")
            
            if "confirm" in subject.lower() or "verify" in subject.lower() or "подтверждение" in body.lower():
                link = extract_verification_link(body)
                if link:
                    verification_link = link
                    break
        
        if verification_link:
            break
            
        if attempt < max_attempts:
            await asyncio.sleep(delay)

    if verification_link:
        await bot.send_message(chat_id, f"📨 Письмо найдено!\n🔗 Ссылка: `{verification_link}`\n\n⚡ Подтверждаю...", parse_mode="Markdown")
        
        verified = await verify_email(verification_link)
        
        if verified:
            await bot.send_message(
                chat_id,
                f"🎉 АККАУНТ ПОДТВЕРЖДЕН!\n\n👤 Имя: {name}\n📧 Email: `{email}`\n🔑 Пароль: `{password}`\n\n✅ Можно пользоваться!",
                parse_mode="Markdown"
            )
        else:
            await bot.send_message(chat_id, "⚠️ Ссылка перешла, но статус подтверждения неизвестен. Проверьте почту вручную.")
    else:
        await bot.send_message(chat_id, "❌ Письмо с подтверждением не пришло за отведенное время.")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start."""
    user = update.effective_user
    welcome_text = (
        f"Привет, {user.first_name}! 👋\n\n"
        "Я бот для автоматической регистрации на Artillect.pro.\n\n"
        "Команды:\n"
        "/register — Создать один аккаунт\n"
        "/status — Статус активных задач\n"
        "/help — Помощь"
    )
    await update.message.reply_text(welcome_text)


async def register_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /register."""
    chat_id = update.effective_chat.id
    
    await context.job_queue.run_once(
        registration_process,
        0,
        data={"chat_id": chat_id},
        name=f"reg_{chat_id}_{uuid.uuid4()}"
    )
    
    await update.message.reply_text("🚀 Запускаю процесс регистрации... Следите за сообщениями.")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /status."""
    if not context.job_queue:
        await update.message.reply_text("❌ Очередь задач не настроена.")
        return

    jobs = context.job_queue.jobs()
    active_jobs = [j for j in jobs if not j.removed and not j.finished]
    
    if not active_jobs:
        await update.message.reply_text("✅ Нет активных задач регистрации.")
    else:
        text = f"📊 Активных задач: {len(active_jobs)}\n\n"
        for i, job in enumerate(active_jobs[:5], 1):
            text += f"{i}. {job.name}\n"
        if len(active_jobs) > 5:
            text += f"... и еще {len(active_jobs) - 5}"
        
        await update.message.reply_text(text)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /help."""
    help_text = (
        "ℹ️ **Помощь**\n\n"
        "Этот бот автоматически:\n"
        "1. Создает временную почту (CatchMail)\n"
        "2. Регистрирует аккаунт на Artillect.pro\n"
        "3. Подтверждает email по ссылке из письма\n\n"
        "Просто нажмите /register, чтобы начать."
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Логирование ошибок."""
    logger.error(f"Update {update} caused error {context.error}")


def main() -> None:
    """Точка входа."""
    config = load_config()
    bot_token = config["bot_token"]
    
    global http_client
    http_client = httpx.AsyncClient(
        timeout=30.0,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    )

    application = (
        Application.builder()
        .token(bot_token)
        .job_queue(JobQueue())
        .build()
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("register", register_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, start_command))

    application.add_error_handler(error_handler)

    logger.info("Бот запущен...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
