# main.py

import os
import logging
from dotenv import load_dotenv
from telegram_bot import TelegramBot
from database import init_db, clean_old_posts, init_stats_table

# Настройка логирования
logging.basicConfig(
    level=logging.ERROR,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Загружаем переменные окружения
load_dotenv()

def main():
    try:
        # 1. Инициализируем базу данных
        print("📦 Инициализация базы данных...")
        init_db()
        init_stats_table()
        
        # 2. Очищаем старые посты
        clean_old_posts(30)
        
        # 3. Получаем токен
        token = os.getenv("TELEGRAM_TOKEN")
        if not token:
            print("❌ TELEGRAM_TOKEN не задан в .env!")
            return
        
        # 4. Запускаем бота
        bot = TelegramBot(token)
        bot.run()
        
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        logging.error(f"Критическая ошибка: {e}")

if __name__ == "__main__":
    main()