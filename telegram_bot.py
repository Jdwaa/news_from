# telegram_bot.py

import os
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

from orchestrator import Orchestrator
from database import init_db, get_posts_by_status, clean_old_posts


class TelegramBot:
    def __init__(self, token: str):
        self.token = token
        self.app = None
        self.orchestrator = None
    
    async def setup(self):
        self.app = ApplicationBuilder().token(self.token).build()
        
        # 👇 Меню команд (кнопки внизу)
        commands = [
            ("start", "Главное меню"),
            ("post", "Создать пост сейчас"),
            ("status", "Статус бота"),
            ("analytics", "Аналитика постов"),
            ("admin", "Панель управления"),
        ]
        await self.app.bot.set_my_commands(commands)
        
        # Команды
        self.app.add_handler(CommandHandler("start", self.start))
        self.app.add_handler(CommandHandler("post", self.post_now))
        self.app.add_handler(CommandHandler("status", self.status))
        self.app.add_handler(CommandHandler("analytics", self.analytics_cmd))
        self.app.add_handler(CommandHandler("admin", self.admin_menu))
        
        # Кнопки
        self.app.add_handler(CallbackQueryHandler(self.button_handler))
        
        return self.app
    
    async def send_main_menu(self, chat_id: int):
        """Отправляет сообщение с кнопками управления"""
        keyboard = [
            [InlineKeyboardButton("📰 Создать пост", callback_data="post_now")],
            [InlineKeyboardButton("📊 Статус", callback_data="status")],
            [InlineKeyboardButton("📈 Аналитика", callback_data="analytics")],
            [InlineKeyboardButton("🔧 Админ-панель", callback_data="admin_panel")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await self.app.bot.send_message(
            chat_id=chat_id,
            text="🤖 **Панель управления ботом**\n\nВыбери действие:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.send_main_menu(update.effective_chat.id)
    
    async def post_now(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("📡 Запускаю цикл подготовки поста...")
        self.orchestrator = Orchestrator(bot=context.bot)
        result = await self.orchestrator.run()
        if result.get("published_post_id"):
            await update.message.reply_text(f"✅ Пост опубликован! ID: {result['published_post_id']}")
        else:
            await update.message.reply_text("⚠️ Пост не опубликован. Проверь логи.")
    
    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        published = get_posts_by_status('published')
        pending = get_posts_by_status('pending')
        await update.message.reply_text(
            f"📊 **Статистика бота**\n\n✅ Опубликовано: {len(published)}\n⏳ Ожидают: {len(pending)}",
            parse_mode='Markdown'
        )
    
    async def analytics_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        from agents.analytics_agent import AnalyticsAgent
        await update.message.reply_text("📊 Собираю аналитику...")
        analytics = AnalyticsAgent()
        await analytics.execute({})
        summary = analytics.get_summary()
        await update.message.reply_text(summary, parse_mode='Markdown')
    
    async def admin_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [
            [InlineKeyboardButton("🧹 Очистить старые посты", callback_data="clean_posts")],
            [InlineKeyboardButton("🔄 Перезапустить цикл", callback_data="restart_cycle")],
            [InlineKeyboardButton("🔙 Назад", callback_data="menu")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("🔧 **Панель управления**", reply_markup=reply_markup, parse_mode='Markdown')
    
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        if query.data == "post_now":
            await query.message.reply_text("📡 Запускаю цикл...")
            self.orchestrator = Orchestrator(bot=context.bot)
            result = await self.orchestrator.run()
            if result.get("published_post_id"):
                await query.message.reply_text(f"✅ Пост опубликован! ID: {result['published_post_id']}")
            else:
                await query.message.reply_text("⚠️ Пост не опубликован.")
        
        elif query.data == "status":
            published = get_posts_by_status('published')
            pending = get_posts_by_status('pending')
            await query.message.reply_text(f"📊 Опубликовано: {len(published)}\n⏳ Ожидают: {len(pending)}")
        
        elif query.data == "analytics":
            from agents.analytics_agent import AnalyticsAgent
            await query.message.reply_text("📊 Собираю аналитику...")
            analytics = AnalyticsAgent()
            await analytics.execute({})
            summary = analytics.get_summary()
            await query.message.reply_text(summary, parse_mode='Markdown')
        
        elif query.data == "admin_panel":
            keyboard = [
                [InlineKeyboardButton("🧹 Очистить старые посты", callback_data="clean_posts")],
                [InlineKeyboardButton("🔄 Перезапустить цикл", callback_data="restart_cycle")],
                [InlineKeyboardButton("🔙 Назад", callback_data="menu")],
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.message.edit_text("🔧 **Панель управления**", reply_markup=reply_markup, parse_mode='Markdown')
        
        elif query.data == "menu":
            await self.send_main_menu(query.message.chat_id)
            await query.message.delete()
        
        elif query.data == "clean_posts":
            deleted = clean_old_posts(7)
            await query.message.reply_text(f"🧹 Удалено {deleted} старых постов (старше 7 дней)")
        
        elif query.data == "restart_cycle":
            await query.message.reply_text("🔄 Перезапускаю цикл...")
            self.orchestrator = Orchestrator(bot=context.bot)
            await self.orchestrator.run()
    
    def run(self):
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self.setup())
        print("🚀 Telegram-бот запущен!")
        print("📌 Кнопки управления всегда в меню (/start) и внизу экрана")
        self.app.run_polling()