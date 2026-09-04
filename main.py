# main.py

import os
import logging
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from telegram_bot import TelegramBot
from database import init_db, clean_old_posts, init_stats_table
from config import Config
from orchestrator import Orchestrator

# Настройка логирования
logging.basicConfig(
    level=logging.ERROR,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Загружаем переменные окружения
load_dotenv()

async def main_async():
    """Асинхронная основная функция"""
    try:
        # Проверяем конфигурацию
        Config.validate()
        
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
        
        # 4. Создаём бота
        bot = TelegramBot(token)
        
        # 5. Настраиваем бота
        await bot.setup()
        
        # 6. Создаём оркестратор с ботом
        orchestrator = Orchestrator(bot=bot.app.bot)
        
        # 7. Запускаем планировщик (автоматическая публикация)
        print("⏰ Запуск планировщика публикаций...")
        scheduler_task = asyncio.create_task(orchestrator.run_scheduler())
        
        # 8. Запускаем бота
        print("🚀 Telegram-бот готов к работе!")
        
        # Инициализируем и запускаем бота в том же event loop
        await bot.app.initialize()
        await bot.app.start()
        
        # Запускаем polling в том же event loop
        await bot.app.updater.start_polling()
        
        # Держим бота в живых (бесконечный цикл)
        while True:
            await asyncio.sleep(3600)  # Спим 1 час
        
    except KeyboardInterrupt:
        print("\n👋 Бот остановлен пользователем")
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        logging.error(f"Критическая ошибка: {e}", exc_info=True)

def main():
    """Основная функция"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main_async())
    except KeyboardInterrupt:
        print("\n👋 Бот остановлен пользователем")
    finally:
        loop.close()

if __name__ == "__main__":
    main()