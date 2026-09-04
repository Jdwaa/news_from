# orchestrator.py

"""
ОРКЕСТРАТОР
Управляет цепочкой агентов и циклами.
"""

import asyncio
from datetime import datetime, timedelta
from agents.parser_agent import ParserAgent
from agents.selector_agent import SelectorAgent
from agents.rewriter_agent import RewriterAgent
from agents.reviewer_agent import ReviewerAgent
from agents.image_agent import ImageAgent
from agents.publisher_agent import PublisherAgent
from agents.analytics_agent import AnalyticsAgent
from agents.editor_agent import EditorAgent
from agents.scheduler_agent import SchedulerAgent
from memory_bank import MemoryBank
from database import save_log


class Orchestrator:
    """Главный оркестратор, управляющий всеми агентами"""
    
    def __init__(self, bot=None):
        self.bot = bot
        self.max_rewrite_attempts = 7
        
        # Создаём агентов
        self.parser = ParserAgent()
        self.selector = SelectorAgent()
        self.rewriter = RewriterAgent()
        self.editor = EditorAgent()
        self.reviewer = ReviewerAgent()
        self.image = ImageAgent()
        self.publisher = PublisherAgent(bot=bot)
        self.scheduler = SchedulerAgent(orchestrator=self)
        self.memory = MemoryBank()
        
        # Флаг для принудительной публикации
        self.force_publish = False
    
    async def run(self, context: dict = None) -> dict:
        """
        Запускает полный цикл создания поста:
        1. Парсинг новостей
        2. Выбор лучшей
        3. Цикл Рерайтер → Редактор → Ревизор (до 7 попыток)
        4. Генерация картинки
        5. Публикация
        6. Аналитика (в фоне)
        """
        if context is None:
            context = {}
        
        self._log("="*60)
        self._log("🚀 ЗАПУСК ОРКЕСТРАТОРА")
        self._log("="*60)
        
        # ==========================================
        # 1. ПАРСИНГ НОВОСТЕЙ
        # ==========================================
        self._log("📡 ШАГ 1: ПАРСИНГ НОВОСТЕЙ")
        context = await self.parser.execute(context)
        
        if not context.get("news_list"):
            self._log("❌ Новостей не найдено. Завершаю.", is_error=True)
            return context
        
        self._log(f"✅ Собрано {len(context['news_list'])} новостей")
        
        # ==========================================
        # 2. ВЫБОР ЛУЧШЕЙ НОВОСТИ
        # ==========================================
        self._log("🎯 ШАГ 2: ВЫБОР ЛУЧШЕЙ НОВОСТИ")
        context = await self.selector.execute(context)
        
        if not context.get("selected_news"):
            self._log("❌ Не удалось выбрать новость. Завершаю.", is_error=True)
            return context
        
        selected = context["selected_news"]
        self._log(f"✅ Выбрана: {selected['title'][:60]}...")
        self._log(f"   Источник: {selected.get('source', 'неизвестен')}")
        self._log(f"   Обоснование: {selected.get('justification', '—')[:80]}...")
        
        # ==========================================
        # 3. ЦИКЛ РЕРАЙТЕР → РЕДАКТОР → РЕВИЗОР (до 7 попыток)
        # ==========================================
        self._log("="*60)
        self._log("📝 ШАГ 3: ЦИКЛ НАПИСАНИЯ ПОСТА")
        self._log("="*60)
        
        text_approved = False
        attempts = 0
        context["reviewer_feedback"] = ""
        self.force_publish = False
        
        while not text_approved and attempts < self.max_rewrite_attempts:
            attempts += 1
            self._log(f"\n🔄 ПОПЫТКА {attempts}/{self.max_rewrite_attempts}")
            
            # 3.1. Рерайтер пишет пост
            context = await self.rewriter.execute(context)
            
            # 3.2. Редактор правит и сокращает
            context = await self.editor.execute(context)
            
            # 3.3. Ревизор проверяет (без проверки длины!)
            context = await self.reviewer.execute(context)
            
            # 3.4. Проверяем результат
            text_approved = context.get("text_approved", False)
            
            if text_approved:
                self._log(f"✅ Пост утверждён на попытке {attempts}")
            else:
                feedback = context.get("reviewer_feedback", "Без замечаний")
                self._log(f"❌ Пост не прошёл проверку: {feedback[:100]}...")
                
                if attempts >= self.max_rewrite_attempts:
                    self._log("⚠️ Достигнут максимум попыток. Публикую как есть.", is_error=True)
                    context["text_approved"] = False
                    self.force_publish = True
                    break
                
                self._log("🔄 Отправляю на доработку...")
        
        # ==========================================
        # 4. ГЕНЕРАЦИЯ КАРТИНКИ
        # ==========================================
        self._log("="*60)
        self._log("🎨 ШАГ 4: ГЕНЕРАЦИЯ КАРТИНКИ")
        self._log("="*60)
        
        context = await self.image.execute(context)
        
        if context.get("image_url"):
            self._log(f"✅ Картинка сгенерирована")
        else:
            self._log("⚠️ Не удалось сгенерировать картинку, использую последнюю из БД", is_error=True)
        
        # ==========================================
        # 5. ПУБЛИКАЦИЯ
        # ==========================================
        if context.get("post_text"):
            self._log("="*60)
            self._log("📤 ШАГ 5: ПУБЛИКАЦИЯ")
            self._log("="*60)
            
            context = await self.publisher.execute(context)
            
            if context.get("published_post_id"):
                self._log(f"✅ Пост #{context['published_post_id']} опубликован!")
            else:
                self._log("⚠️ Пост не опубликован", is_error=True)
        else:
            self._log("❌ Нет поста для публикации", is_error=True)
        
        # ==========================================
        # 6. АНАЛИТИКА (запускается только если нет свежей)
        # ==========================================
        if context.get("published_post_id"):
            self._log("="*60)
            self._log("📊 ШАГ 6: ЗАПУСК АНАЛИТИКИ")
            self._log("="*60)
            await self.run_analytics(context)
        
        # ==========================================
        # ИТОГИ
        # ==========================================
        self._log("="*60)
        self._log("✅ РАБОТА ОРКЕСТРАТОРА ЗАВЕРШЕНА")
        self._log("="*60)
        self._log(f"📝 Заголовок: {context.get('post_title', '—')}")
        self._log(f"📏 Длина поста: {len(context.get('post_text', ''))} символов")
        self._log(f"📸 Картинка: {'✅' if context.get('image_url') else '❌'}")
        self._log(f"📤 Опубликован: {'✅' if context.get('published_post_id') else '❌'}")
        
        return context
    
    async def run_scheduler(self):
        """Запускает планировщик публикаций"""
        await self.scheduler.execute({})
    
    async def run_analytics(self, context: dict = None) -> dict:
        """Запускает только аналитику"""
        if context is None:
            context = {}
        
        self._log("📊 ЗАПУСК АНАЛИТИКА")
        
        # Проверяем, есть ли уже аналитика за сегодня
        last_analysis = self.memory.get("last_analysis_at")
        if last_analysis:
            try:
                last_time = datetime.fromisoformat(last_analysis)
                if datetime.now() - last_time < timedelta(hours=24):
                    self._log("⏳ Аналитика уже была сегодня, пропускаю")
                    return context
            except:
                pass
        
        analytics = AnalyticsAgent()
        context = await analytics.execute(context)
        
        # Сохраняем отчёт в Memory Bank
        report = analytics.get_best_posting_time()
        if report:
            self.memory.set("analytics_report", report)
            self._log(f"📊 Отчёт сохранён: лучшее время {report.get('hour', '?')}:00")
        else:
            self._log("⚠️ Не удалось получить отчёт по лучшему времени")
        
        return context
    
    def _log(self, message: str, is_error: bool = False):
        """Логирование от имени Оркестратора"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        prefix = "❌" if is_error else "📌"
        log_line = f"[{timestamp}] {prefix} [Оркестратор] {message}"
        print(log_line)
        save_log(message, "error" if is_error else "info")