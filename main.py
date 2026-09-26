# main.py

import os
import sys
import atexit
import logging
import asyncio
from dotenv import load_dotenv

from telegram_bot import TelegramBot
from database import init_db, clean_old_posts
from config import Config
from orchestrator import Orchestrator
from console_utils import setup_utf8_console
from channels import active_profiles

# Без этого первый же print с эмодзи падает на cp1251-консоли Windows
setup_utf8_console()

# Настройка логирования
logging.basicConfig(
    level=logging.ERROR,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Загружаем переменные окружения
load_dotenv()

# Держим ссылки на задачи, чтобы их не собрал сборщик мусора
_tasks = []


def acquire_single_instance_lock():
    """
    Не даёт запустить второй экземпляр бота.
    
    Два процесса читали бы одну БД и один Memory Bank, и каждый планировщик
    публиковал бы свои посты — то есть дубли в канале. Блокировка снимается
    сама при завершении процесса.
    
    Файл блокировки кладём рядом с базой, а не в системный %TEMP%: временная
    папка может быть недоступна для записи (например, под ограничениями
    хостинга или политиками безопасности), и бот тогда не стартует вовсе.
    
    Возвращает открытый файл блокировки или None, если уже запущен другой экземпляр.
    """
    lock_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".bot.lock")
    lock_file = open(lock_path, "a+", encoding="utf-8")
    
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_file.close()
        return None
    
    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    
    return lock_file


async def shutdown(bot, scheduler_task):
    """Аккуратно останавливает планировщик и бота."""
    if scheduler_task and not scheduler_task.done():
        print("\n⏹️ Останавливаю планировщик...")
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass
    
    if bot and bot.app:
        print("⏹️ Останавливаю бота...")
        try:
            if bot.app.updater and bot.app.updater.running:
                await bot.app.updater.stop()
            if bot.app.running:
                await bot.app.stop()
            await bot.app.shutdown()
        except Exception as e:
            print(f"⚠️ Ошибка при остановке бота: {e}")


async def main_async():
    """Асинхронная основная функция"""
    bot = None
    scheduler_task = None
    
    try:
        # Проверяем конфигурацию: критичные переменные окружения + предупреждения
        print("🔧 Проверяю переменные окружения...")
        try:
            warnings = Config.validate()
        except ValueError as e:
            print(str(e))
            return 1
        
        for warning in warnings:
            print(f"⚠️  {warning}")
        
        if not warnings:
            print("✅ Конфигурация в порядке")
        
        # 1. Инициализируем базу данных (init_db сам создаёт и таблицу статистики)
        print(f"📦 Инициализация базы данных ({Config.DB_PATH})...")
        init_db()
        
        # 2. Очищаем старые посты
        clean_old_posts(30)
        
        # 3. Получаем токен
        token = Config.TELEGRAM_TOKEN
        if not token:
            print("❌ TELEGRAM_TOKEN не задан в переменных окружения!")
            return 1
        
        # 4. Создаём бота
        bot = TelegramBot(token)
        
        # 5. Настраиваем бота
        await bot.setup()
        
        if not bot.network_ready:
            print("❌ Telegram недоступен. Проверьте интернет, VPN/прокси или блокировку api.telegram.org.")
            return 1
        
        # 6. Создаём оркестратор с ботом
        orchestrator = Orchestrator(bot=bot.app.bot)
        
        # 7. Запускаем бота в текущем event loop
        print("🚀 Запуск Telegram-бота...")
        try:
            await bot.app.initialize()
            await bot.app.start()
            await bot.app.updater.start_polling()
        except Exception as e:
            print(f"❌ Не удалось подключиться к Telegram: {bot._describe_network_error(e)}")
            print("   Проверьте доступ в интернет и правильность TELEGRAM_TOKEN.")
            return 1
        
        # 8. Запускаем планировщики (автоматическая публикация) — по одному на канал.
        # Только после старта бота: планировщик публикует через него.
        # Регистрируем их в самом боте, чтобы кнопка «Перезапустить планировщик»
        # управляла теми же задачами, а не создавала вторые.
        print("⏰ Запуск планировщиков публикаций...")
        scheduler_tasks = bot.restart_scheduler(bot.app.bot)
        _tasks.extend(scheduler_tasks)
        for profile in active_profiles():
            print(f"   • «{profile.title}» — интервал {profile.interval // 3600} ч, "
                  f"сдвиг {profile.schedule_offset // 3600} ч")
        
        print("🚀 Telegram-бот готов к работе!")
        
        # Держим бота в живых: ждём сигнала остановки или падения планировщиков
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        
        for signal_name in ("SIGINT", "SIGTERM"):
            sig = getattr(__import__("signal"), signal_name, None)
            if sig is None:
                continue
            try:
                loop.add_signal_handler(sig, stop.set)
            except (NotImplementedError, RuntimeError):
                # Windows: Ctrl+C всё равно поднимет KeyboardInterrupt
                pass
        
        async def watch_scheduler(task, name):
            try:
                await task
                print(f"⚠️ Планировщик «{name}» завершился")
                stop.set()
            except asyncio.CancelledError:
                pass
            except Exception as e:
                print(f"❌ Планировщик «{name}» упал: {e}")
                logging.error(f"Планировщик «{name}» упал: {e}", exc_info=True)
                stop.set()
        
        watchers = []
        for task in scheduler_tasks:
            watchers.append(asyncio.create_task(watch_scheduler(task, task.get_name())))
        _tasks.extend(watchers)
        
        try:
            await stop.wait()
        finally:
            for w in watchers:
                w.cancel()
        
        return 0
        
    except KeyboardInterrupt:
        print("\n👋 Бот остановлен пользователем")
        return 0
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        logging.error(f"Критическая ошибка: {e}", exc_info=True)
        return 1
    finally:
        await shutdown(bot, scheduler_task)


def main():
    """Основная функция"""
    lock_file = acquire_single_instance_lock()
    if lock_file is None:
        print("❌ Бот уже запущен в другом процессе. Второй экземпляр не нужен: он даст дубли постов.")
        return 1
    
    atexit.register(lock_file.close)
    
    exit_code = 1
    try:
        exit_code = asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\n👋 Бот остановлен пользователем")
        exit_code = 0
    
    return exit_code


if __name__ == "__main__":
    sys.exit(main())