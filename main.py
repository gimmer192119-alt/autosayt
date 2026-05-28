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

import requests
from telegram import Update, Bot
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
# Используем 1secmail API (бесплатный, не требует авторизации)
MAIL_API_BASE = "https://www.1secmail.com/api/v1/"

REFERRAL_CODE = "B3B7C1C4"

# Загрузка конфигурации
def load_config() -> dict:
    """Загрузка конфигурации из config.json"""
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    
    if not os.path.exists(config_path):
        logger.error("Файл config.json не найден! Создайте его по шаблону.")
        return None
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        if not config.get('telegram_bot_token') or config['telegram_bot_token'] == 'ВАШ_ТОКЕН_БОТА':
            logger.error("Укажите ваш токен бота в config.json")
            return None
        
        if not config.get('telegram_chat_id') or config['telegram_chat_id'] == 'ВАШ_CHAT_ID':
            logger.error("Укажите ваш Chat ID в config.json")
            return None
        
        logger.info("Конфигурация успешно загружена")
        return config
    
    except json.JSONDecodeError as e:
        logger.error(f"Ошибка чтения config.json: {e}")
        return None
    except Exception as e:
        logger.error(f"Ошибка загрузки конфигурации: {e}")
        return None

# Словари для генерации имен
FIRST_NAMES = ["Alex", "Max", "John", "Mike", "Dmitry", "Ivan", "Peter", "Anna", "Maria", "Elena", "Olga", "Kate"]
LAST_NAMES = ["Smith", "Johnson", "Brown", "Wilson", "Davis", "Miller", "Anderson", "Thomas", "Jackson", "White"]


class AccountCreator:
    def __init__(self, telegram_bot_token: str, telegram_chat_id: int):
        self.bot_token = telegram_bot_token
        self.chat_id = telegram_chat_id
        self.session = requests.Session()
        self._setup_session()
    
    def _setup_session(self):
        """Настройка сессии с заголовками"""
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:151.0) Gecko/20100101 Firefox/151.0",
            "Accept": "*/*",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Content-Type": "application/json",
            "Origin": "https://app.artillect.pro",
            "Connection": "keep-alive",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "Pragma": "no-cache",
            "Cache-Control": "no-cache",
        })
    
    def generate_random_name(self) -> str:
        """Генерация случайного имени"""
        return f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
    
    def generate_random_password(self, length: int = 12) -> str:
        """Генерация случайного пароля минимум 9 символов"""
        if length < 9:
            length = 9
        
        # Гарантируем наличие разных типов символов
        chars = (
            string.ascii_uppercase +
            string.ascii_lowercase +
            string.digits +
            "!@#$%^&*()-_=+[]{}|;:,.<>?"
        )
        
        password = [
            random.choice(string.ascii_uppercase),
            random.choice(string.ascii_lowercase),
            random.choice(string.digits),
            random.choice("!@#$%^&*()-_=+[]{}|;:,.<>?")
        ]
        
        # Остальные символы случайные
        password += [random.choice(chars) for _ in range(length - 4)]
        
        # Перемешиваем
        random.shuffle(password)
        return ''.join(password)
    
    def create_temp_email(self) -> Optional[Dict[str, Any]]:
        """Создание временной почты через 1secmail"""
        try:
            # Генерируем случайный логин
            login = ''.join(random.choices(string.ascii_lowercase + string.digits, k=10))
            
            # Получаем список доступных доменов
            response = self.session.get(f"{MAIL_API_BASE}?action=getDomainList")
            response.raise_for_status()
            domains = response.json()
            
            if not domains:
                logger.error("Не удалось получить список доменов")
                return None
            
            domain = random.choice(domains)
            email = f"{login}@{domain}"
            
            email_data = {
                "login": login,
                "domain": domain,
                "email": email
            }
            
            logger.info(f"Создана временная почта: {email}")
            return email_data
            
        except Exception as e:
            logger.error(f"Ошибка создания временной почты: {e}")
            return None
    
    def get_messages(self, login: str, domain: str) -> list:
        """Получение списка сообщений для почты"""
        try:
            response = self.session.get(
                f"{MAIL_API_BASE}?action=getMessages&login={login}&domain={domain}"
            )
            response.raise_for_status()
            messages = response.json()
            return messages if messages else []
        except Exception as e:
            logger.error(f"Ошибка получения сообщений: {e}")
            return []
    
    def read_message(self, login: str, domain: str, msg_id: int) -> Optional[Dict]:
        """Чтение конкретного сообщения"""
        try:
            response = self.session.get(
                f"{MAIL_API_BASE}?action=readMessage&login={login}&domain={domain}&id={msg_id}"
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Ошибка чтения сообщения: {e}")
            return None
    
    def extract_verification_link(self, html_content: str) -> Optional[str]:
        """Извлечение ссылки подтверждения из HTML письма"""
        # Ищем ссылку вида https://app.artillect.pro/api/auth/verify-email?token=...
        pattern = r'https://app\.artillect\.pro/api/auth/verify-email\?token=[a-f0-9\-]+'
        match = re.search(pattern, html_content)
        if match:
            return match.group(0)
        return None
    
    def register_account(self, email: str, password: str, name: str) -> bool:
        """Регистрация аккаунта на Artillect"""
        try:
            payload = {
                "name": name,
                "email": email,
                "password": password,
                "policy": True,
                "subscribe": False,
                "locale": "ru",
                "referralCode": REFERRAL_CODE
            }
            
            # Обновляем заголовки для конкретного запроса
            headers = self.session.headers.copy()
            headers["Referer"] = f"https://app.artillect.pro/register/?ref={REFERRAL_CODE}"
            
            # Добавляем cookies для сессии
            cookies = {
                "_ym_uid": str(random.randint(1000000000000000000, 9999999999999999999)),
                "_ym_d": str(int(datetime.now().timestamp())),
                "cid": str(uuid.uuid4()),
            }
            
            response = self.session.post(
                ARTILLECT_REGISTER_URL,
                headers=headers,
                json=payload,
                cookies=cookies
            )
            
            logger.info(f"Статус регистрации: {response.status_code}")
            logger.info(f"Ответ сервера: {response.text[:500]}")
            
            if response.status_code in [200, 201]:
                logger.info("Регистрация успешна!")
                return True
            else:
                logger.warning(f"Регистрация не удалась: {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Ошибка регистрации: {e}")
            return False
        return None
    
    def verify_email(self, verification_link: str) -> bool:
        """Подтверждение email по ссылке"""
        try:
            # Используем ту же сессию для сохранения cookies
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:151.0) Gecko/20100101 Firefox/151.0",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            }
            
            response = self.session.get(verification_link, headers=headers)
            
            logger.info(f"Статус подтверждения: {response.status_code}")
            
            # Проверяем успешность по статусу или содержимому
            if response.status_code in [200, 301, 302]:
                logger.info("Email подтвержден успешно!")
                return True
            
            # Проверяем содержимое на наличие признаков успеха
            if "подтверждена" in response.text.lower() or "verified" in response.text.lower():
                logger.info("Email подтвержден (определено по содержимому)!")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Ошибка подтверждения email: {e}")
            return False
    
    async def send_telegram_message(self, message: str):
        """Отправка сообщения в Telegram"""
        try:
            bot = Bot(token=self.bot_token)
            await bot.send_message(chat_id=self.chat_id, text=message, parse_mode='HTML')
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения в Telegram: {e}")
    
    async def create_account_flow(self) -> Dict[str, Any]:
        """Полный процесс создания и подтверждения аккаунта"""
        result = {
            "success": False,
            "email": "",
            "password": "",
            "name": "",
            "message": ""
        }
        
        await self.send_telegram_message("🔄 Начинаю процесс регистрации...")
        
        # Шаг 1: Создание временной почты
        await self.send_telegram_message("📧 Создаю временную почту...")
        email_data = self.create_temp_email()
        
        if not email_data or not email_data.get("email"):
            error_msg = "❌ Не удалось создать временную почту"
            result["message"] = error_msg
            await self.send_telegram_message(error_msg)
            return result
        
        result["email"] = email_data["email"]
        login = email_data.get("login", "")
        domain = email_data.get("domain", "")
        
        # Шаг 2: Генерация данных аккаунта
        name = self.generate_random_name()
        password = self.generate_random_password()
        
        result["name"] = name
        result["password"] = password
        
        await self.send_telegram_message(
            f"📝 Данные аккаунта:\n"
            f"👤 Имя: <code>{name}</code>\n"
            f"📧 Email: <code>{email_data['email']}</code>\n"
            f"🔒 Пароль: <code>{password}</code>"
        )
        
        # Шаг 3: Регистрация
        await self.send_telegram_message("🔐 Регистрирую аккаунт...")
        if not self.register_account(email_data["email"], password, name):
            error_msg = "❌ Не удалось зарегистрировать аккаунт"
            result["message"] = error_msg
            await self.send_telegram_message(error_msg)
            return result
        
        await self.send_telegram_message("✅ Регистрация успешна! Ожидаю письмо с подтверждением...")
        
        # Шаг 4: Ожидание и получение письма
        max_attempts = 30  # Максимум 30 попыток (5 минут)
        attempt = 0
        verification_link = None
        
        while attempt < max_attempts:
            attempt += 1
            await asyncio.sleep(10)  # Ждем 10 секунд между проверками
            
            messages = self.get_messages(login, domain)
            
            for message in messages:
                msg_id = message.get("id", 0)
                
                # Читаем полное сообщение
                full_message = self.read_message(login, domain, msg_id)
                if full_message:
                    body = full_message.get("body", "") or full_message.get("text", "")
                    
                    verification_link = self.extract_verification_link(body)
                    if verification_link:
                        break
            
            if verification_link:
                break
            
            if attempt % 6 == 0:  # Каждую минуту отчет
                await self.send_telegram_message(f"⏳ Ожидаю письмо... Попытка {attempt}/{max_attempts}")
        
        if not verification_link:
            error_msg = "❌ Письмо с подтверждением не получено"
            result["message"] = error_msg
            await self.send_telegram_message(error_msg)
            return result
        
        # Шаг 5: Подтверждение email
        await self.send_telegram_message("🔗 Ссылка найдена! Подтверждаю email...")
        
        if self.verify_email(verification_link):
            result["success"] = True
            result["message"] = "✅ Аккаунт успешно создан и подтвержден!"
            
            success_msg = (
                "🎉 <b>Аккаунт успешно создан и подтвержден!</b>\n\n"
                f"👤 Имя: <code>{name}</code>\n"
                f"📧 Email: <code>{email_data['email']}</code>\n"
                f"🔒 Пароль: <code>{password}</code>\n"
                f"🔗 Реферальный код: <code>{REFERRAL_CODE}</code>\n\n"
                f"💾 <b>Сохраните эти данные!</b>"
            )
            await self.send_telegram_message(success_msg)
        else:
            error_msg = "❌ Не удалось подтвердить email"
            result["message"] = error_msg
            await self.send_telegram_message(error_msg)
        
        return result


# Глобальные переменные для управления
creator: Optional[AccountCreator] = None
registration_task: Optional[asyncio.Task] = None
is_running = False


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    await update.message.reply_text(
        "🤖 Бот для автоматической регистрации аккаунтов Artillect\n\n"
        "Доступные команды:\n"
        "/register - Создать один аккаунт\n"
        "/start_auto <минуты> - Запустить авто-регистрацию каждые N минут\n"
        "/stop_auto - Остановить авто-регистрацию\n"
        "/status - Показать текущий статус",
        parse_mode=None
    )


async def register_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /register - разовая регистрация"""
    global creator
    
    if creator is None:
        await update.message.reply_text(
            "❌ Бот не настроен. Запустите с параметрами:\n"
            "python main.py <BOT_TOKEN> <CHAT_ID>"
        )
        return
    
    await update.message.reply_text("🔄 Начинаю регистрацию...")
    
    result = await creator.create_account_flow()
    
    if result["success"]:
        await update.message.reply_text(
            f"✅ <b>Успешно!</b>\n\n"
            f"👤 Имя: <code>{result['name']}</code>\n"
            f"📧 Email: <code>{result['email']}</code>\n"
            f"🔒 Пароль: <code>{result['password']}</code>",
            parse_mode='HTML'
        )
    else:
        await update.message.reply_text(f"❌ {result['message']}")


async def start_auto_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start_auto - авто-регистрация"""
    global creator, registration_task, is_running
    
    if creator is None:
        await update.message.reply_text("❌ Бот не настроен.")
        return
    
    if is_running:
        await update.message.reply_text("⚠️ Авто-регистрация уже запущена.")
        return
    
    try:
        interval_minutes = int(context.args[0])
        if interval_minutes < 1:
            raise ValueError()
    except (IndexError, ValueError):
        await update.message.reply_text(
            "❌ Неверный формат. Используйте: /start_auto <минуты>\n"
            "Пример: /start_auto 5"
        )
        return
    
    is_running = True
    
    async def auto_register_loop():
        while is_running:
            try:
                logger.info(f"Запуск авто-регистрации (интервал: {interval_minutes} мин)")
                result = await creator.create_account_flow()
                
                if result["success"]:
                    logger.info(f"Аккаунт создан: {result['email']}")
                
                # Ждем следующий интервал
                for _ in range(interval_minutes * 6):  # Разбиваем на 10-секундные интервалы
                    if not is_running:
                        break
                    await asyncio.sleep(10)
                    
            except Exception as e:
                logger.error(f"Ошибка в цикле авто-регистрации: {e}")
                if creator:
                    await creator.send_telegram_message(f"❌ Ошибка: {e}")
                await asyncio.sleep(60)
    
    registration_task = asyncio.create_task(auto_register_loop())
    
    await update.message.reply_text(
        f"✅ Авто-регистрация запущена!\n"
        f"⏰ Интервал: каждые {interval_minutes} мин.\n"
        f"Для остановки используйте /stop_auto"
    )


async def stop_auto_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /stop_auto"""
    global is_running, registration_task
    
    is_running = False
    
    if registration_task:
        registration_task.cancel()
        try:
            await registration_task
        except asyncio.CancelledError:
            pass
        registration_task = None
    
    await update.message.reply_text("⏹️ Авто-регистрация остановлена.")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /status"""
    global is_running
    
    status = "🟢 Запущена" if is_running else "🔴 Остановлена"
    
    await update.message.reply_text(
        f"<b>Статус бота:</b>\n"
        f"Авто-регистрация: {status}\n"
        f"Реферальный код: <code>{REFERRAL_CODE}</code>",
        parse_mode='HTML'
    )


def main():
    """Основная функция"""
    # Загружаем конфигурацию из файла
    config = load_config()
    
    if not config:
        print("\n❌ Ошибка: Не удалось загрузить конфигурацию!")
        print("\n📝 Инструкция:")
        print("1. Откройте файл config.json")
        print("2. Замените 'ВАШ_ТОКЕН_БОТА' на токен вашего Telegram бота")
        print("3. Замените 'ВАШ_CHAT_ID' на ваш числовой Chat ID")
        print("\nПример config.json:")
        print('{')
        print('    "telegram_bot_token": "123456:ABCdefGHIjklMNOpqrsTUVwxyz",')
        print('    "telegram_chat_id": 987654321')
        print('}')
        print("\nКак получить Chat ID:")
        print("- Напишите боту @userinfobot в Telegram")
        print("- Или отправьте сообщение созданному боту и используйте код get_id.py")
        return
    
    bot_token = config['telegram_bot_token']
    chat_id = int(config['telegram_chat_id'])
    
    global creator
    creator = AccountCreator(bot_token, chat_id)
    
    # Создаем приложение Telegram
    application = Application.builder().token(bot_token).build()
    
    # Добавляем обработчики команд
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("register", register_command))
    application.add_handler(CommandHandler("start_auto", start_auto_command))
    application.add_handler(CommandHandler("stop_auto", stop_auto_command))
    application.add_handler(CommandHandler("status", status_command))
    
    print("🤖 Бот запущен...")
    logger.info("Бот запущен и ожидает команды")
    
    # Запускаем бота (синхронный вызов для Python 3.13+)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
