# agents/scheduler_agent.py

"""
АГЕНТ-ПЛАНИРОВЩИК
Умная автопубликация:
- Анализирует лучшее время из Memory Bank
- Не публикует ночью (23:00 - 07:00)
- Ждёт лучшее время для публикации
"""

import asyncio
from datetime import datetime, timedelta
from .base_agent import BaseAgent
from config import Config
from database import save_log


class SchedulerAgent(BaseAgent):
    """Агент для умной автопубликации по расписанию"""
    
    def __init__(self, orchestrator=None):
        super().__init__("Планировщик")
        self.orchestrator = orchestrator
        self.is_running = False
        
        # Настройки времени
        self.min_hour = 7    # Не публиковать раньше 7:00
        self.max_hour = 22   # Не публиковать позже 22:00
    
    async def execute(self, context: dict) -> dict:
        """
        Основной метод:
        1. Проверяет лучшее время из аналитики
        2. Определяет, когда публиковать
        3. Запускает цикл умной публикации
        """
        self.log("⏰ Запускаю умный планировщик публикаций...")
        
        # Загружаем аналитику
        analytics_report = self.memory.get("analytics_report")
        best_hour = 10
        best_day = 0
        
        if analytics_report:
            best_hour = analytics_report.get("best_hour", 10)
            best_day = analytics_report.get("best_day", 0)
            self.log(f"📊 Лучшее время: {best_hour}:00 (день {best_day})")
        else:
            self.log("⚠️ Нет аналитики, использую стандартное время 10:00")
        
        # Запускаем бесконечный цикл
        self.is_running = True
        
        while self.is_running:
            try:
                # Проверяем, нужно ли публиковать сейчас
                should_publish = await self._should_publish(best_hour, best_day)
                
                if should_publish:
                    self.log("🚀 Время публикации! Запускаю цикл создания поста...")
                    
                    if self.orchestrator:
                        result = await self.orchestrator.run()
                        
                        if result.get("published_post_id"):
                            self.log(f"✅ Пост #{result['published_post_id']} опубликован!")
                            self.memory.set("last_publish_at", datetime.now().isoformat())
                            
                            # Обновляем аналитику после публикации
                            await self._update_analytics()
                            
                            # Обновляем лучшее время из новой аналитики
                            new_analytics = self.memory.get("analytics_report")
                            if new_analytics:
                                best_hour = new_analytics.get("best_hour", best_hour)
                                best_day = new_analytics.get("best_day", best_day)
                        else:
                            self.log("⚠️ Пост не опубликован, пробую снова через час...")
                            await asyncio.sleep(3600)
                            continue
                    else:
                        self.log("❌ Оркестратор не передан!", is_error=True)
                        return context
                
                # Ждём проверки через 30 минут
                await asyncio.sleep(1800)
                
            except Exception as e:
                self.log(f"❌ Ошибка в цикле публикации: {e}", is_error=True)
                save_log(f"Ошибка планировщика: {e}", "error")
                await asyncio.sleep(3600)  # Ждём час перед повторной попыткой
        
        return context
    
    async def _should_publish(self, best_hour: int, best_day: int) -> bool:
        """
        Определяет, нужно ли публиковать сейчас.
        
        Правила:
        1. Не публикуем ночью (23:00 - 07:00)
        2. Проверяем, прошло ли достаточно времени с последней публикации
        3. Проверяем, наступило ли лучшее время
        """
        now = datetime.now()
        current_hour = now.hour
        current_day = now.weekday()
        
        # 1. Проверяем, не ночь ли сейчас
        if current_hour >= 23 or current_hour < 7:
            self.log(f"🌙 Сейчас {current_hour}:00 — ночь, пропускаю публикацию")
            return False
        
        # 2. Проверяем время с последней публикации
        last_publish = self.memory.get("last_publish_at")
        if last_publish:
            try:
                last_time = datetime.fromisoformat(last_publish)
                time_diff = (now - last_time).total_seconds()
                
                # Минимальный интервал между публикациями (3 часа)
                min_interval = Config.POST_INTERVAL  # 10800 секунд = 3 часа
                
                if time_diff < min_interval:
                    wait_hours = (min_interval - time_diff) / 3600
                    self.log(f"⏳ Последняя публикация была {time_diff/3600:.1f} ч назад")
                    self.log(f"⏳ Жду ещё {wait_hours:.1f} ч...")
                    return False
                
            except Exception:
                pass
        
        # 3. Если сегодня лучший день — проверяем лучшее время
        if current_day == best_day:
            if current_hour >= best_hour:
                self.log(f"✅ Сегодня лучший день ({current_day}), время {current_hour}:00 >= {best_hour}:00")
                return True
            else:
                wait_minutes = (best_hour - current_hour) * 60
                self.log(f"⏳ Жду до лучшего времени {best_hour}:00 (осталось {wait_minutes} мин)")
                return False
        
        # 4. Если сегодня не лучший день, но прошло 3+ часа — публикуем
        # (в рабочее время, чтобы не было простоев)
        hours_since_last = 0
        if last_publish:
            last_time = datetime.fromisoformat(last_publish)
            hours_since_last = (now - last_time).total_seconds() / 3600
        
        if hours_since_last >= Config.POST_INTERVAL / 3600:
            # Проверяем, что сейчас рабочее время
            if 7 <= current_hour <= 22:
                self.log(f"📊 Не лучший день, но прошло {hours_since_last:.1f} ч — публикую")
                return True
        
        return False
    
    async def _update_analytics(self):
        """Обновляет аналитику после публикации"""
        self.log("📊 Обновляю аналитику...")
        
        try:
            from .analytics_agent import AnalyticsAgent
            analytics = AnalyticsAgent()
            await analytics.execute({})
            
            # Сохраняем новый отчёт
            report = analytics.get_best_posting_time()
            if report:
                self.memory.set("analytics_report", report)
                self.log(f"📊 Новое лучшее время: {report.get('hour', 10)}:00")
                
        except Exception as e:
            self.log(f"⚠️ Ошибка обновления аналитики: {e}", is_error=True)
    
    def stop(self):
        """Останавливает планировщик"""
        self.is_running = False
        self.log("⏹️ Планировщик остановлен")