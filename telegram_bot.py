# telegram_bot.py

"""
Telegram-интерфейс бота.

Здесь только UI: команды, кнопки и запуск цикла. Вся логика — в агентах
и оркестраторе.
"""

import asyncio
import time
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from orchestrator import Orchestrator
from database import get_posts_by_status, clean_old_posts
from memory_bank import MemoryBank
from console_utils import setup_utf8_console
from config import Config
from channels import get_profile, active_profiles, NEWS

# UI может использоваться и в отдельном процессе — консоль настраиваем и здесь
setup_utf8_console()


class TelegramBot:
    # Сколько секунд отводится ручному циклу, прежде чем публикуем без картинки.
    # Хостинг может убить выполнение по своему таймауту, а результат уже готов.
    MANUAL_CYCLE_TIMEOUT = 300  # 5 минут
    
    def __init__(self, token: str):
        self.token = token
        self.app = None
        self.orchestrator = None
        self.scheduler_task = None
        self.scheduler_tasks = []
        self.memory = MemoryBank()
        self._post_lock = asyncio.Lock()
        # Проверка связи с Telegram на старте: нужна, чтобы main.py отличил
        # «нет сети/блокировка» от ошибок конфигурации
        self.network_ready = False
    
    # ==========================================
    # НАСТРОЙКА
    # ==========================================
    
    async def setup(self):
        """Настраивает бота: команды, кнопки, защита от посторонних"""
        self.app = ApplicationBuilder().token(self.token).build()
        
        # Обработчик ошибок: без него любое исключение в команде pтb просто
        # логирует трейсбек и молчит, а пользователь не понимает, что произошло.
        self.app.add_error_handler(self._on_error)
        
        # Доступ только для администратора канала: иначе любой человек сможет
        # запускать платный цикл и смотреть аналитику. В группах команды должны
        # начинаться со слэша, в личке можем отвечать и на текст.
        private = filters.ChatType.PRIVATE
        self.app.add_handler(CommandHandler("start", self.start, filters=private))
        self.app.add_handler(CommandHandler("help", self.help_cmd, filters=private))
        
        admin_only = self._admin_filter()
        self.app.add_handler(CommandHandler("post", self.post_now, admin_only))
        self.app.add_handler(CommandHandler("status", self.status, admin_only))
        self.app.add_handler(CommandHandler("analytics", self.analytics_cmd, admin_only))
        self.app.add_handler(CommandHandler("growth", self.growth_cmd, admin_only))
        self.app.add_handler(CommandHandler("pin", self.pin_cmd, admin_only))
        self.app.add_handler(CommandHandler("admin", self.admin_menu, filters=private & admin_only))
        
        self.app.add_handler(CallbackQueryHandler(self.button_handler, admin_only))
        
        # Кнопки внизу экрана. Это единственный сетевой вызов на старте:
        # если Telegram недоступен (нет сети, блокировка), бот всё равно должен
        # запуститься — кнопки не критичны, а в лог попадёт понятная причина.
        commands = [
            ("start", "Главное меню"),
            ("help", "Справка"),
            ("status", "Статус бота"),
            ("analytics", "Аналитика"),
            ("post", "Создать пост сейчас"),
            ("admin", "Панель управления"),
        ]
        try:
            await self.app.bot.set_my_commands(commands)
            self.network_ready = True
        except Exception as e:
            print(f"⚠️ Не удалось обновить меню команд ({self._describe_network_error(e)})")
            print("   Бот продолжит работу: команды /start, /help, /status доступны и без меню.")
        
        return self.app
    
    @staticmethod
    def _describe_network_error(error: Exception) -> str:
        """Превращает сетевую ошибку telegram в понятное объяснение."""
        text = f"{type(error).__name__}: {error}".strip()
        lowered = text.lower()
        if "timed out" in lowered or "timeout" in lowered:
            return (
                f"{text}. Нет соединения с api.telegram.org. "
                "Проверьте интернет, VPN/прокси или блокировку Telegram."
            )
        if "unauthorized" in lowered or "401" in lowered:
            return f"{text}. Похоже, TELEGRAM_TOKEN неверный или отозван."
        if "not found" in lowered or "404" in lowered:
            return f"{text}. Похоже, TELEGRAM_TOKEN указан с ошибкой."
        return text
    
    async def _on_error(self, update, context):
        """Логирует ошибку обработчика и сообщает пользователю, что запрос упал."""
        error = context.error
        print(f"❌ Ошибка обработки запроса: {type(error).__name__}: {error}")
        
        chat_id = None
        if update is not None:
            chat_id = getattr(getattr(update, "effective_chat", None), "id", None)
        if chat_id is None:
            return
        
        try:
            await self.app.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ Не удалось выполнить команду: {type(error).__name__}. Подробности в логе."
            )
        except Exception:
            # Сообщить не получилось (например, та же сетевая проблема) — уже залогировано
            pass
    
    # ==========================================
    # КОМАНДЫ
    # ==========================================
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._send_main_menu(update.effective_chat.id)
    
    def _main_menu_keyboard(self) -> InlineKeyboardMarkup:
        """
        Клавиатура главного меню.

        Кнопки публикации строятся по активным каналам: если CHANNEL_TIPS_ID
        не задан, кнопки лайфхаков не будет — интерфейс не должен предлагать
        действие, которое заведомо не сработает.
        """
        rows = []
        for p in active_profiles():
            rows.append([InlineKeyboardButton(
                f"{p.emoji} Пост: {p.title}", callback_data=f"post_now:{p.key}"
            )])
        rows.append([InlineKeyboardButton("📊 Статус", callback_data="status")])
        rows.append([InlineKeyboardButton("📈 Воронка", callback_data="growth")])
        rows.append([InlineKeyboardButton("📈 Аналитика", callback_data="analytics")])
        rows.append([InlineKeyboardButton("🔧 Админ-панель", callback_data="admin_panel")])
        return InlineKeyboardMarkup(rows)
    
    async def growth_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Показывает отчёт по воронке: подписчики, прирост, конверсия."""
        await self._send(update.message.reply_text, "📈 Собираю метрики воронки...")
        # Сначала снимаем свежий замер, потом строим отчёт
        try:
            orchestrator = Orchestrator(bot=self.app.bot)
            await orchestrator.run_growth({})
            from agents.growth_agent import GrowthAgent
            report = GrowthAgent(bot=self.app.bot).build_report(hours=24)
        except Exception as e:
            report = f"⚠️ Не удалось собрать метрики: {e}"
        await self._send(update.message.reply_text, report)
    
    async def pin_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        /pin [news|tips] — поставить или обновить закреп-навигацию канала.

        Без аргумента — для всех активных каналов: это разовая настройка,
        и удобнее сделать её одной командой, чем по каналу отдельно.
        """
        args = getattr(context, "args", None) or []
        from agents.growth_agent import GrowthAgent

        if args:
            try:
                profiles = [get_profile(args[0].strip().lower())]
            except ValueError:
                await update.message.reply_text(
                    f"❓ Неизвестный канал: {args[0]}\n"
                    f"Доступны: {', '.join(p.key for p in active_profiles())}"
                )
                return
        else:
            profiles = active_profiles()

        agent = GrowthAgent(bot=self.app.bot)
        results = []
        for p in profiles:
            ok = await agent.update_pin(p)
            results.append(f"{'✅' if ok else '❌'} {p.title}")

        await update.message.reply_text(
            "📌 Закреп-навигация:\n" + "\n".join(results)
        )
    
    async def _send_main_menu(self, chat_id: int):
        """Отправляет главное меню с кнопками."""
        await self.app.bot.send_message(
            chat_id=chat_id,
            text="🤖 *Панель управления ботом*\n\nВыбери действие:",
            reply_markup=self._main_menu_keyboard(),
            parse_mode="Markdown"
        )
    
    async def help_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "🤖 *Новостной бот-канал*\n\n"
            "Бот сам собирает новости из RSS, выбирает лучшую, пишет по ней пост, "
            "рисует картинку и публикует в канал по расписанию.\n\n"
            "*Команды:*\n"
            "• /post — собрать и опубликовать пост (выбор канала)\n"
            "• /growth — рост подписчиков и конверсия воронки\n"
            "• /status — сколько постов опубликовано и ждёт отправки\n"
            "• /analytics — статистика и лучшее время публикации\n"
            "• /admin — очистка базы и перезапуск планировщика\n\n"
            "Автопубликация идёт в рабочее время (07:00–22:00) "
            f"с интервалом не чаще {Config.POST_INTERVAL // 3600} ч.",
            parse_mode="Markdown"
        )
    
    async def post_now(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        /post — принудительная публикация.

        Без аргумента предлагает выбрать канал кнопками, если каналов несколько.
        С аргументом (/post tips) публикует сразу в указанный канал.
        """
        args = getattr(context, "args", None) or []
        if args:
            key = args[0].strip().lower()
            try:
                profile = get_profile(key)
            except ValueError:
                await update.message.reply_text(
                    f"❓ Неизвестный канал: {key}\n"
                    f"Доступны: {', '.join(p.key for p in active_profiles())}"
                )
                return
            await self._run_manual_cycle(update.message.reply_text, profile)
            return
        
        profiles = active_profiles()
        if len(profiles) == 1:
            await self._run_manual_cycle(update.message.reply_text, profiles[0])
            return
        
        # Каналов несколько — спрашиваем, куда публиковать
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"{p.emoji} {p.title}", callback_data=f"post_now:{p.key}")]
            for p in profiles
        ])
        await update.message.reply_text(
            "📤 Куда публиковать принудительно?",
            reply_markup=keyboard
        )
    
    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Статистика по каналам раздельно: у каждого свой продукт и метрики."""
        from database import get_all_posts_with_stats, get_subscriber_growth

        lines = ["📊 *Статистика по каналам*", ""]
        for p in active_profiles():
            lines.append(f"{p.emoji} *{p.title}*")

            published = len(get_posts_by_status('published', channel=p.key))
            pending = len(get_posts_by_status('pending', channel=p.key))
            lines.append(f"   📝 постов: {published} (в очереди: {pending})")

            # Подписчики и прирост
            growth = get_subscriber_growth(p.key, hours=24)
            if growth.get("current") is not None:
                delta = growth.get("delta")
                delta_text = "—" if delta is None else (f"+{delta}" if delta > 0 else str(delta))
                lines.append(f"   👥 подписчиков: {growth['current']} ({delta_text} за сутки)")

            # Просмотры и средний отклик по каналу
            posts = get_all_posts_with_stats(limit=30, channel=p.key)
            viewed = [x for x in posts if (x.get("views") or 0) > 0]
            if viewed:
                avg_views = sum(x["views"] for x in viewed) / len(viewed)
                avg_score = sum(x.get("score") or 0 for x in viewed) / len(viewed)
                lines.append(f"   👁️ средние просмотры: {avg_views:.0f}")
                lines.append(f"   ⭐ средняя оценка: {avg_score:.1f}/10")
            else:
                lines.append("   👁️ статистика ещё не собрана")
            lines.append("")

        last = self.memory.get(f"last_publish_at:{NEWS}")
        if last:
            try:
                lines.append(f"🕐 Последняя публикация (новости): "
                             f"{datetime.fromisoformat(str(last)).strftime('%d.%m %H:%M')}")
            except ValueError:
                pass
        lines.append(f"⏰ Планировщики: {'работают' if self._scheduler_alive() else 'не запущены'}")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    
    async def analytics_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        summary = await self._collect_analytics(context.bot)
        await self._send(update.message.reply_text, "📊 Собираю аналитику...")
        await self._send(update.message.reply_text, summary)
    
    async def admin_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "🔧 *Панель управления*",
            reply_markup=self._admin_keyboard(),
            parse_mode="Markdown"
        )
    
    # ==========================================
    # КНОПКИ
    # ==========================================
    
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        data = query.data or ""
        
        # Кнопки публикации несут канал в callback_data: "post_now:news" / "post_now:tips"
        if data.startswith("post_now"):
            key = data.split(":", 1)[1] if ":" in data else NEWS
            try:
                profile = get_profile(key)
            except ValueError:
                await query.message.reply_text(f"❓ Неизвестный канал: {key}")
                return
            await self._run_manual_cycle(query.message.reply_text, profile)
        
        elif data == "status":
            lines = ["📊 *Статистика по каналам*", ""]
            for p in active_profiles():
                published = len(get_posts_by_status('published', channel=p.key))
                pending = len(get_posts_by_status('pending', channel=p.key))
                lines.append(f"{p.emoji} *{p.title}*")
                lines.append(f"   ✅ опубликовано: {published}")
                lines.append(f"   ⏳ ожидают: {pending}")
            await query.message.reply_text("\n".join(lines), parse_mode="Markdown")
        
        elif data == "growth":
            await self._send(query.message.reply_text, "📈 Собираю метрики воронки...")
            try:
                orchestrator = Orchestrator(bot=self.app.bot)
                await orchestrator.run_growth({})
                from agents.growth_agent import GrowthAgent
                report = GrowthAgent(bot=self.app.bot).build_report(hours=24)
            except Exception as e:
                report = f"⚠️ Не удалось собрать метрики: {e}"
            await self._send(query.message.reply_text, report)
        
        elif data == "analytics":
            await self._send(query.message.reply_text, "📊 Собираю аналитику...")
            await self._send(query.message.reply_text, await self._collect_analytics(context.bot))
        
        elif query.data == "admin_panel":
            await query.message.edit_text(
                "🔧 *Панель управления*",
                reply_markup=self._admin_keyboard(),
                parse_mode="Markdown"
            )
        
        elif query.data == "menu":
            await query.message.delete()
            await self._send_main_menu(query.message.chat_id)
        
        elif query.data == "clean_posts":
            deleted = clean_old_posts(7)
            await query.message.reply_text(f"🧹 Удалено {deleted} старых постов (старше 7 дней)")
        
        elif query.data == "restart_scheduler":
            await query.message.reply_text("🔄 Перезапускаю планировщик...")
            self.restart_scheduler(context.bot)
            await query.message.reply_text("✅ Планировщик перезапущен!")
    
    def restart_scheduler(self, bot):
        """
        Перезапускает планировщики (общая логика для кнопки и внешних вызовов).

        Планировщик создаётся на КАЖДЫЙ активный канал: у них разные интервалы,
        разные метки последней публикации и сдвиг расписания. Один общий
        планировщик смешивал бы каналы и ломал расписание.
        """
        self.stop_schedulers()
        
        self.scheduler_tasks = []
        for profile in active_profiles():
            orchestrator = Orchestrator(bot=bot, profile=profile)
            if profile.key == NEWS:
                # Основной оркестратор канала новостей — для команд и аналитики
                self.orchestrator = orchestrator
            task = asyncio.create_task(
                orchestrator.run_scheduler(), name=f"scheduler-{profile.key}"
            )
            self.scheduler_tasks.append(task)
        
        if not self.scheduler_tasks:
            # Каналов нет — создаём хотя бы новостной, чтобы команды работали
            self.orchestrator = Orchestrator(bot=bot, profile=get_profile(NEWS))
        
        return self.scheduler_tasks
    
    def stop_schedulers(self):
        """Останавливает все запущенные планировщики."""
        for task in getattr(self, "scheduler_tasks", []):
            if task and not task.done():
                task.cancel()
        self.scheduler_tasks = []
    
    # ==========================================
    # ВНУТРЕННЕЕ
    # ==========================================
    
    def _admin_filter(self):
        """
        Фильтр доступа к управляющим командам.
        
        Пускает ADMIN_ID и всех из ADMIN_IDS (через запятую). Если администратор
        не задан вовсе, команды открыты — об этом main.py предупреждает при старте.
        """
        admin_ids = [i for i in [Config.ADMIN_ID, Config.OWNER_CHAT_ID, *Config.EXTRA_ADMIN_IDS] if i]
        if not admin_ids:
            return filters.ALL
        if len(admin_ids) == 1:
            return filters.User(user_id=admin_ids[0])
        return filters.User(user_id=admin_ids)
    
    async def _run_manual_cycle(self, reply, profile=None):
        """
        Ручной запуск цикла из Telegram.

        Профиль определяет канал и его стиль. Кулдаун у каждого канала свой:
        иначе запуск в новостной блокировал бы проверку лайфхаков.
        """
        profile = profile or get_profile(NEWS)
        
        if self._post_lock.locked():
            await reply("⏳ Цикл уже выполняется, дождись окончания.")
            return
        
        if not self._cooldown_passed(profile):
            await reply(
                f"⏳ Слишком часто для «{profile.title}»: следующий запуск через "
                f"{self._cooldown_left(profile) // 60} мин."
            )
            return
        
        async with self._post_lock:
            self.memory.set(self._manual_run_key(profile),
                            datetime.now().astimezone().isoformat())
            await reply(f"📡 Запускаю цикл подготовки поста для «{profile.title}»...")
            
            self.orchestrator = Orchestrator(bot=self.app.bot, profile=profile)
            # Картинку генерируем и здесь: раньше стоял skip_image_generation=True,
            # из-за чего ручной пост всегда уходил без картинки.
            # Бюджет времени страхует публикацию: если генерация не уложится,
            # ImageAgent пропустит картинку, но пост всё равно выйдет.
            context = {
                "deadline": time.time() + self.MANUAL_CYCLE_TIMEOUT,
                "channel_profile": profile,
            }
            result = await self.orchestrator.run(context)
            
            if result.get("published_post_id"):
                # Планировщик этого канала отсчитывает интервал от своей метки,
                # иначе он опубликует ещё один пост сразу после ручного.
                self.memory.set(profile.memory_key("last_publish_at"),
                                datetime.now().astimezone().isoformat())
                await reply(
                    f"✅ Пост опубликован в «{profile.title}»! ID: {result['published_post_id']}"
                )
            elif result.get("duplicate_skipped"):
                await reply("⚠️ Эта новость уже публиковалась за сутки. Попробуй позже.")
            else:
                await reply("⚠️ Пост не опубликован. Проверь логи.")
    
    @staticmethod
    def _manual_run_key(profile) -> str:
        """Ключ метки ручного запуска, изолированный по каналу."""
        return profile.memory_key("last_manual_run_at")
    
    def _cooldown_passed(self, profile=None) -> bool:
        profile = profile or get_profile(NEWS)
        if Config.POST_COOLDOWN <= 0:
            return True
        last = self.memory.get(self._manual_run_key(profile))
        if not last:
            return True
        last_dt = self._parse_last_run(last)
        if last_dt is None:
            return True
        return (datetime.now().astimezone() - last_dt).total_seconds() >= Config.POST_COOLDOWN
    
    def _cooldown_left(self, profile=None) -> int:
        profile = profile or get_profile(NEWS)
        last = self.memory.get(self._manual_run_key(profile))
        last_dt = self._parse_last_run(last)
        if last_dt is None:
            return 0
        delta = (datetime.now().astimezone() - last_dt).total_seconds()
        return max(0, int(Config.POST_COOLDOWN - delta))
    
    @staticmethod
    def _parse_last_run(value) -> datetime | None:
        """
        Разбирает метку последнего ручного запуска.
        
        Раньше она писалась через datetime.now().isoformat() — без часового пояса,
        а сравнивалась с aware-временем. Вычитание naive из aware даёт TypeError
        (не ValueError!), и команда /post падала целиком, не оставляя даже записи
        в логе. Теперь наивную метку считаем локальной и приводим к aware.
        """
        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()
        return parsed
    
    def _scheduler_alive(self) -> bool:
        return bool(self.scheduler_tasks) and any(
            t for t in self.scheduler_tasks if not t.done()
        )
    
    def _admin_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🧹 Очистить старые посты", callback_data="clean_posts")],
            [InlineKeyboardButton("🔄 Перезапустить планировщик", callback_data="restart_scheduler")],
            [InlineKeyboardButton("🔙 Назад", callback_data="menu")],
        ])
    
    async def _collect_analytics(self, bot) -> str:
        from agents.analytics_agent import AnalyticsAgent
        analytics = AnalyticsAgent(bot=bot)
        await analytics.execute({})
        return analytics.get_summary()
    
    @staticmethod
    async def _send(reply, text: str):
        """
        Отправляет сообщение, отключая Markdown при ошибке разметки.
        Сводка аналитики содержит подчёркивания из имён файлов, и Telegram
        отвечает 400 на непарный Markdown — сообщение просто не доходило.
        """
        try:
            await reply(text, parse_mode="Markdown")
        except Exception as e:
            print(f"⚠️ Не удалось отправить с Markdown ({e}), отправляю как текст")
            await reply(text)
    
    def run(self):
        """Запускает бота отдельным процессом (для отладки)."""
        print("🚀 Telegram-бот запущен!")
        print("📌 Кнопки управления всегда в меню (/start) и внизу экрана")
        self.app.run_polling()
