import asyncio
import json
import random
import string
import re
import logging
import os
import uuid
from datetime import datetime
from typing import Optional, Dict, Any

import httpx
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Настройки логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Константы
ARTILLECT_REGISTER_URL = "https://app.artillect.pro/api/auth/register/"
ARTILLECT_VERIFY_URL = "https://app.artillect.pro/api/auth/verify-email"
MAIL_API_BASE = "https://www.1secmail.com/api/v1/"

REFERRAL_CODE = "B3B7C1C4"

# Глобальные переменные
config: Dict[str, Any] = {}
auto_register_task: Optional[asyncio.Task] = None
is_auto_registering = False
http_client: Optional[httpx.AsyncClient] = None


def get_system_proxy() -> Optional[str]:
    """Получение системного прокси из переменных окружения"""
    proxy_vars = ['https_proxy', 'http_proxy', 'all_proxy', 'HTTPS_PROXY', 'HTTP_PROXY', 'ALL_PROXY']
    
    for var in proxy_vars:
        proxy_url = os.environ.get(var)
        if proxy_url:
            logger.info(f"Найден прокси в переменной {var}: {proxy_url}")
            return proxy_url
    
    # Проверка реестра Windows
    if os.name == 'nt':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Internet Settings') as key:
                proxy_enable = winreg.QueryValueEx(key, 'ProxyEnable')[0]
                if proxy_enable:
                    proxy_server = winreg.QueryValueEx(key, 'ProxyServer')[0]
                    # Определяем тип прокси
                    if ':' in proxy_server:
                        host, port = proxy_server.split(':')
                        # Пробуем определить тип (обычно HTTP прокси на портах 8080, 3128 и т.д.)
                        proxy_url = f"http://{proxy_server}"
                        logger.info(f"Найден прокси в реестре Windows: {proxy_url}")
                        return proxy_url
        except Exception as e:
            logger.debug(f"Не удалось получить прокси из реестра: {e}")
    
    return None


def create_http_client(proxy_url: Optional[str] = None) -> httpx.AsyncClient:
    """Создание HTTP клиента с прокси"""
    proxies = None
    if proxy_url:
        proxies = {"http://": proxy_url, "https://": proxy_url}
        logger.info(f"HTTP клиент настроен на использование прокси: {proxy_url}")
    
    client = httpx.AsyncClient(
        proxies=proxies,
        timeout=httpx.Timeout(30.0, connect=10.0),
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:151.0) Gecko/20100101 Firefox/151.0",
            "Accept": "*/*",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        }
    )
    return client


# Загрузка конфигурации
def load_config() -> dict:
    """Загрузка конфигурации из config.json"""
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    
    if not os.path.exists(config_path):
        logger.error("Файл config.json не найден!")
        return None
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        if not config.get('telegram_bot_token') or config['telegram_bot_token'] == 'ВАШ_ТОКЕН_БОТА':
            logger.error("Укажите ваш токен бота в config.json")
            return None
        
        if not config.get('telegram_chat_id'):
            logger.error("Укажите ваш Chat ID в config.json")
            return None
        
        logger.info("Конфигурация успешно загружена")
        return config
    
    except Exception as e:
        logger.error(f"Ошибка загрузки конфигурации: {e}")
        return None


def generate_random_name() -> str:
    """Генерация случайного имени"""
    names = ["Alex", "Max", "John", "Dmitry", "Ivan", "Sergey", "Pavel", "Andrey", "Victor", "Roman"]
    return random.choice(names) + str(random.randint(100, 999))


def generate_password(length: int = 12) -> str:
    """Генерация надежного пароля"""
    chars = string.ascii_letters + string.digits + "!@#$%^&*()"
    return ''.join(random.choice(chars) for _ in range(length))


async def create_temp_email() -> tuple[str, str]:
    """Создание временной почты через 1secmail"""
    global http_client
    
    try:
        # Получаем список доступных доменов
        response = await http_client.get(f"{MAIL_API_BASE}?action=getDomainList")
        response.raise_for_status()
        domains = response.text.strip().split('\n')
        
        if not domains or domains[0] == '':
            raise Exception("Не удалось получить список доменов")
        
        domain = random.choice(domains)
        login = ''.join(random.choices(string.ascii_lowercase + string.digits, k=random.randint(8, 12)))
        email = f"{login}@{domain}"
        
        logger.info(f"Создана временная почта: {email}")
        return email, login
    
    except Exception as e:
        logger.error(f"Ошибка создания почты: {e}")
        raise


async def check_email_messages(login: str, domain: str) -> list:
    """Проверка сообщений на почте"""
    global http_client
    
    try:
        response = await http_client.get(
            f"{MAIL_API_BASE}?action=getMessages&login={login}&domain={domain}"
        )
        response.raise_for_status()
        messages = response.json()
        return messages if isinstance(messages, list) else []
    
    except Exception as e:
        logger.error(f"Ошибка проверки почты: {e}")
        return []


async def read_message(login: str, domain: str, msg_id: str) -> dict:
    """Чтение сообщения"""
    global http_client
    
    try:
        response = await http_client.get(
            f"{MAIL_API_BASE}?action=readMessage&login={login}&domain={domain}&id={msg_id}"
        )
        response.raise_for_status()
        return response.json()
    
    except Exception as e:
        logger.error(f"Ошибка чтения сообщения: {e}")
        return {}


async def register_account(email: str, name: str, password: str) -> bool:
    """Регистрация аккаунта на Artillect"""
    global http_client
    
    try:
        # Создаем новую сессию для регистрации с cookies
        register_client = httpx.AsyncClient(
            proxies=http_client.proxies,
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:151.0) Gecko/20100101 Firefox/151.0",
                "Accept": "*/*",
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                "Content-Type": "application/json",
                "Origin": "https://app.artillect.pro",
                "Referer": f"https://app.artillect.pro/register/?ref={REFERRAL_CODE}",
            },
            cookies={
                "_ym_uid": str(uuid.uuid4()),
                "_ym_d": str(int(datetime.now().timestamp())),
                "cid": str(uuid.uuid4()),
            }
        )
        
        data = {
            "name": name,
            "email": email,
            "password": password,
            "policy": True,
            "subscribe": False,
            "locale": "ru",
            "referralCode": REFERRAL_CODE
        }
        
        response = await register_client.post(ARTILLECT_REGISTER_URL, json=data)
        response.raise_for_status()
        
        result = response.json()
        logger.info(f"Регистрация успешна: {result}")
        
        await register_client.aclose()
        return True
    
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP ошибка при регистрации: {e.response.status_code} - {e.response.text}")
        return False
    except Exception as e:
        logger.error(f"Ошибка регистрации: {e}")
        return False


async def verify_email(token: str) -> bool:
    """Подтверждение email"""
    global http_client
    
    try:
        verify_url = f"{ARTILLECT_VERIFY_URL}?token={token}"
        
        # Создаем сессию для верификации
        verify_client = httpx.AsyncClient(
            proxies=http_client.proxies,
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:151.0) Gecko/20100101 Firefox/151.0",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            }
        )
        
        response = await verify_client.get(verify_url, follow_redirects=True)
        response.raise_for_status()
        
        logger.info(f"Email подтвержден: {response.status_code}")
        
        await verify_client.aclose()
        return True
    
    except Exception as e:
        logger.error(f"Ошибка подтверждения email: {e}")
        return False


def extract_verification_link(text: str) -> Optional[str]:
    """Извлечение ссылки подтверждения из текста письма"""
    # Паттерн для поиска ссылки вида https://app.artillect.pro/api/auth/verify-email?token=...
    pattern = r'https://app\.artillect\.pro/api/auth/verify-email\?token=[a-f0-9\-]+'
    match = re.search(pattern, text)
    
    if match:
        link = match.group(0)
        token_match = re.search(r'token=([a-f0-9\-]+)', link)
        if token_match:
            return token_match.group(1)
    
    return None


async def send_telegram_message(chat_id: int, message: str):
    """Отправка сообщения в Telegram"""
    bot_token = config['telegram_bot_token']
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    
    async with httpx.AsyncClient(proxies=http_client.proxies if http_client else None) as client:
        try:
            response = await client.post(
                url,
                json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
                timeout=30.0
            )
            response.raise_for_status()
            logger.info("Сообщение отправлено в Telegram")
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения: {e}")


async def full_registration_process(chat_id: int):
    """Полный процесс регистрации"""
    global http_client
    
    try:
        await send_telegram_message(chat_id, "🔄 Начинаю регистрацию...")
        
        # Шаг 1: Создание временной почты
        await send_telegram_message(chat_id, "📧 Создаю временную почту...")
        email, login = await create_temp_email()
        domain = email.split('@')[1]
        await send_telegram_message(chat_id, f"✅ Почта создана: <code>{email}</code>")
        
        # Шаг 2: Генерация данных
        name = generate_random_name()
        password = generate_password(12)
        await send_telegram_message(chat_id, f"👤 Имя: <code>{name}</code>\n🔑 Пароль: <code>{password}</code>")
        
        # Шаг 3: Регистрация
        await send_telegram_message(chat_id, "📝 Регистрирую аккаунт...")
        success = await register_account(email, name, password)
        
        if not success:
            await send_telegram_message(chat_id, "❌ Ошибка при регистрации!")
            return
        
        await send_telegram_message(chat_id, "✅ Аккаунт зарегистрирован! Ожидаю письмо...")
        
        # Шаг 4: Ожидание и чтение письма
        max_attempts = 30
        for attempt in range(max_attempts):
            await asyncio.sleep(2)
            messages = await check_email_messages(login, domain)
            
            if messages:
                msg_id = messages[0]['id']
                message_data = await read_message(login, domain, str(msg_id))
                
                if message_data and 'body' in message_data:
                    body = message_data.get('body', '')
                    html_body = message_data.get('htmlBody', '')
                    
                    token = extract_verification_link(body) or extract_verification_link(html_body)
                    
                    if token:
                        await send_telegram_message(chat_id, "✉️ Письмо получено! Подтверждаю email...")
                        
                        # Шаг 5: Подтверждение
                        verify_success = await verify_email(token)
                        
                        if verify_success:
                            await send_telegram_message(
                                chat_id, 
                                f"🎉 <b>Регистрация завершена успешно!</b>\n\n"
                                f"📧 Email: <code>{email}</code>\n"
                                f"🔑 Пароль: <code>{password}</code>\n"
                                f"👤 Имя: <code>{name}</code>\n\n"
                                f"✅ Email подтвержден!"
                            )
                        else:
                            await send_telegram_message(chat_id, "❌ Ошибка при подтверждении email!")
                        
                        return
                    else:
                        logger.warning("Ссылка подтверждения не найдена в письме")
        
        await send_telegram_message(chat_id, "⏰ Превышено время ожидания письма!")
        
    except Exception as e:
        logger.error(f"Ошибка в процессе регистрации: {e}")
        await send_telegram_message(chat_id, f"❌ Произошла ошибка: {str(e)}")


async def auto_register_loop(chat_id: int, interval: int = 60):
    """Цикл автоматической регистрации"""
    global is_auto_registering
    
    is_auto_registering = True
    logger.info(f"Запущена авто-регистрация каждые {interval} секунд")
    
    try:
        while is_auto_registering:
            await full_registration_process(chat_id)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("Авто-регистрация остановлена")
    finally:
        is_auto_registering = False


# Обработчики команд
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    chat_id = update.effective_chat.id
    
    welcome_text = (
        "🤖 <b>Бот для автоматической регистрации на Artillect.pro</b>\n\n"
        "Доступные команды:\n"
        "/register - Создать один аккаунт\n"
        "/start_auto [минуты] - Запустить авто-регистрацию (по умолчанию 1 минута)\n"
        "/stop_auto - Остановить авто-регистрацию\n"
        "/status - Проверить статус\n"
        "/help - Помощь"
    )
    
    await update.message.reply_text(welcome_text, parse_mode="HTML")


async def register_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /register"""
    chat_id = update.effective_chat.id
    
    if is_auto_registering:
        await update.message.reply_text("⚠️ Сначала остановите авто-регистрацию командой /stop_auto")
        return
    
    await full_registration_process(chat_id)


async def start_auto_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start_auto"""
    global auto_register_task, is_auto_registering
    
    chat_id = update.effective_chat.id
    
    if is_auto_registering:
        await update.message.reply_text("⚠️ Авто-регистрация уже запущена!")
        return
    
    # Получаем интервал из аргументов (в минутах)
    interval = 60  # по умолчанию 1 минута
    if context.args and context.args[0].isdigit():
        interval = int(context.args[0]) * 60
    
    await update.message.reply_text(f"🚀 Запускаю авто-регистрацию каждые {interval // 60} мин...")
    
    auto_register_task = asyncio.create_task(auto_register_loop(chat_id, interval))


async def stop_auto_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /stop_auto"""
    global auto_register_task, is_auto_registering
    
    if not is_auto_registering:
        await update.message.reply_text("ℹ️ Авто-регистрация не запущена")
        return
    
    is_auto_registering = False
    if auto_register_task:
        auto_register_task.cancel()
        try:
            await auto_register_task
        except asyncio.CancelledError:
            pass
    
    await update.message.reply_text("⏹️ Авто-регистрация остановлена")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /status"""
    status = "🟢 Авто-регистрация запущена" if is_auto_registering else "🔴 Авто-регистрация остановлена"
    await update.message.reply_text(f"📊 Статус: {status}")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    help_text = (
        "📖 <b>Помощь</b>\n\n"
        "Этот бот автоматически регистрирует аккаунты на Artillect.pro с использованием временной почты.\n\n"
        "<b>Команды:</b>\n"
        "/register - Создать один аккаунт\n"
        "/start_auto [минуты] - Запустить авто-регистрацию\n"
        "/stop_auto - Остановить авто-регистрацию\n"
        "/status - Проверить статус\n\n"
        "<b>Примеры:</b>\n"
        "/start_auto 1 - регистрация каждую минуту\n"
        "/start_auto 5 - регистрация каждые 5 минут"
    )
    await update.message.reply_text(help_text, parse_mode="HTML")


async def post_init(application):
    """Инициализация после запуска"""
    logger.info("Application initialized")


def main():
    """Основная функция"""
    global config, http_client
    
    # Загрузка конфигурации
    config = load_config()
    if not config:
        return
    
    # Получение прокси
    proxy_url = config.get('proxy_url') or get_system_proxy()
    
    # Создание HTTP клиента
    http_client = create_http_client(proxy_url)
    
    # Создание приложения Telegram
    logger.info(f"Бот использует прокси: {proxy_url if proxy_url else 'нет'}")
    
    application = Application.builder().token(config['telegram_bot_token']).build()
    
    # Добавление обработчиков
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("register", register_command))
    application.add_handler(CommandHandler("start_auto", start_auto_command))
    application.add_handler(CommandHandler("stop_auto", stop_auto_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("help", help_command))
    
    # Запуск бота
    logger.info("Бот запущен и ожидает команды")
    print("🤖 Бот запущен...")
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
