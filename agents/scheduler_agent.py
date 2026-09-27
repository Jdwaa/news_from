# agents/scheduler_agent.py

"""
АГЕНТ-ПЛАНИРОВЩИК
Умная автопубликация:
- Анализирует лучшее время из Memory Bank
- Не публикует ночью (23:00 - 07:00)
- Ждёт лучшее время для публикации
"""

import asyncio
import os
from datetime import datetime
from .base_agent import BaseAgent
from .analytics_agent import normalize_analytics_report
from config import Config
from database import save_log
from channels import get_profile, NEWS


class SchedulerAgent(BaseAgent):
    """
    Агент для умной автопубликации по расписанию.

    Один экземпляр обслуживает ОДИН канал: профиль передаётся при создании.
    Два канала — два планировщика, каждый со своей меткой последней публикации
    и своим сдвигом расписания. Общая метка приводила бы к тому, что публикация
    в новостном канале сдвигала бы интервал канала лайфхаков.
    """
    
    def __init__(self, orchestrator=None, profile=None):
        super().__init__("Планировщик")
        self.orchestrator = orchestrator
        self.profile = profile or get_profile(NEWS)
        self.is_running = False
        
        # Настройки времени
        self.min_hour = 7    # Не публиковать раньше 7:00
        self.max_hour = 22   # Не публиковать позже 22:00
    
    @property
    def _last_publish_key(self) -> str:
        """Ключ метки последней публикации, изолированный по каналу."""
        return self.profile.memory_key("last_publish_at")
    
    def log(self, message: str, is_error: bool = False) -> str:
        """Логи с именем канала: в общем потоке иначе не понять, чей это цикл."""
        return super().log(f"[{self.profile.title}] {message}", is_error=is_error)
    
    async def execute(self, context: dict) -> dict:
        """
        Основной метод:
        1. Проверяет лучшее время из аналитики
        2. Определяет, когда публиковать
        3. Запускает цикл умной публикации
        """
        self.log("⏰ Запускаю умный планировщик публикаций...")
        
        # Загружаем аналитику (единый контракт ключей — normalize_analytics_report)
        best_hour, best_day = self._load_best_time()
        
        # Запускаем бесконечный цикл
        self.is_running = True
        
        while self.is_running:
            try:
                # Проверяем, нужно ли публиковать сейчас
                should_publish = await self._should_publish(best_hour, best_day)
                
                if should_publish:
                    self.log("🚀 Время публикации! Запускаю цикл создания поста...")
                    
                    if self.orchestrator:
                        result = await self.orchestrator.run(
                            {"channel_profile": self.profile}
                        )
                        
                        if result.get("published_post_id"):
                            self.log(f"✅ Пост #{result['published_post_id']} опубликован!")
                            # Метка с таймзоной: наивное время роняло арифметику
                            # интервалов с TypeError (aware минус naive).
                            self.memory.set(self._last_publish_key,
                                            datetime.now().astimezone().isoformat())
                            
                            # Обновляем аналитику после публикации
                            await self._update_analytics()
                            
                            # Обновляем лучшее время из новой аналитики
                            new_analytics = self.memory.get("analytics_report")
                            if new_analytics:
                                report = normalize_analytics_report(new_analytics)
                                best_hour = report["hour"]
                                best_day = report["day"]
                        else:
                            self.log("⚠️ Пост не опубликован, пробую снова через час...")
                            # Дубликат — штатная ситуация, о ней не сообщаем;
                            # остальные причины публикации не состоялись, и
                            # админ должен узнать об этом сразу.
                            if not result.get("duplicate_skipped") and self.orchestrator.bot:
                                try:
                                    from alerts import notify_admin
                                    reason = result.get("error") or "причина в логе"
                                    await notify_admin(
                                        self.orchestrator.bot,
                                        f"Пост не опубликован в «{self.profile.title}».\n"
                                        f"Причина: {reason}",
                                        kind=f"publish-failed:{self.profile.key}",
                                    )
                                except Exception:
                                    pass
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
                # Сообщаем админу: сбой цикла иначе заметен только по пустому каналу
                if self.orchestrator and self.orchestrator.bot:
                    try:
                        from alerts import notify_admin
                        await notify_admin(
                            self.orchestrator.bot,
                            f"Сбой цикла публикации в «{self.profile.title}»:\n{e}",
                            kind=f"scheduler-error:{self.profile.key}",
                        )
                    except Exception:
                        pass
                await asyncio.sleep(3600)  # Ждём час перед повторной попыткой
        
        return context
    
    async def run_maintenance(self):
        """
        Ежедневное обслуживание: бэкап БД, чистка картинок, сводка админу.

        Запускает только планировщик новостного канала: работа одна на весь
        бот, и дублировать её вторым планировщиком нельзя (два бэкапа, две
        сводки, двойная чистка).
        """
        from channels import NEWS
        if self.profile.key != NEWS:
            return
        if not self.orchestrator:
            return

        import maintenance
        from alerts import notify_admin

        self.log("🧰 Обслуживание запущено (раз в сутки)")
        while self.is_running:
            try:
                # 1. Бэкап базы
                res = maintenance.backup_database(keep=Config.BACKUP_KEEP)
                if res.get("path"):
                    self.log(f"💾 Бэкап БД: {os.path.basename(res['path'])} "
                             f"({res['size_kb']} КБ, удалено старых: {res['removed']})")
                else:
                    self.log(f"⚠️ Бэкап не сделан: {res.get('error')}", is_error=True)

                # 2. Чистка лишних файлов картинок (после публикации они
                # удаляются сразу, здесь — страховка от сиротских файлов)
                clean = maintenance.cleanup_images()
                if clean["deleted"] or clean.get("temp_removed"):
                    self.log(f"🧹 Убрано лишних картинок: {clean['deleted']} "
                             f"({clean['freed_mb']} МБ), временных: {clean.get('temp_removed')}, "
                             f"резерв оставлен: {clean['kept']}")

                # 3. Суточная сводка по росту
                if Config.DAILY_REPORT_HOUR >= 0 and self.orchestrator.bot:
                    hour_now = datetime.now().hour
                    if hour_now == Config.DAILY_REPORT_HOUR:
                        await notify_admin(
                            self.orchestrator.bot,
                            maintenance.daily_growth_text(),
                            kind="daily-report",
                            critical=True,   # сводка важна, окно тишины не мешает
                        )
            except Exception as e:
                self.log(f"⚠️ Ошибка обслуживания: {e}", is_error=True)

            await asyncio.sleep(max(600, Config.BACKUP_INTERVAL))

    async def run_growth_monitor(self):
        """
        Отдельный цикл: снимает метрики подписчиков раз в GROWTH_INTERVAL.

        Метрики снимает только планировщик НОВОСТНОГО канала: иначе два
        планировщика писали бы одни и те же замеры дважды, и прирост за сутки
        считался бы неверно.
        """
        from channels import NEWS
        if self.profile.key != NEWS:
            return
        if not self.orchestrator:
            return

        from .growth_agent import GrowthAgent
        interval = max(300, Config.GROWTH_INTERVAL)
        self.log(f"📈 Мониторинг воронки запущен (каждые {interval // 60} мин)")
        agent = GrowthAgent(bot=self.orchestrator.bot)

        while self.is_running:
            try:
                await agent.execute({})
            except Exception as e:
                self.log(f"⚠️ Ошибка сбора метрик: {e}", is_error=True)
            await asyncio.sleep(interval)
    
    def _load_best_time(self) -> tuple:
        """
        Читает отчёт аналитики из Memory Bank и возвращает (лучший час, лучший день).
        
        Отчёт всегда проходит через normalize_analytics_report(), поэтому
        Планировщик одинаково понимает и канонические ключи ("hour"/"day"),
        и старые ("best_hour"/"best_day"), а битые значения заменяются дефолтом.
        """
        analytics_report = self.memory.get("analytics_report")
        report = normalize_analytics_report(analytics_report)
        
        if analytics_report:
            self.log(
                f"📊 Лучшее время: {report['hour']}:00 "
                f"(день {report['day']}, {report.get('reason', '—')})"
            )
        else:
            self.log(
                f"⚠️ Нет аналитики, использую стандартное время "
                f"{report['hour']}:00 (день {report['day']})"
            )
        
        return report["hour"], report["day"]
    
    @staticmethod
    def _parse_last_publish(raw) -> datetime:
        """
        Разбирает метку последней публикации.
        
        Планировщик пишет время в локальной зоне с таймзоной, а ручной запуск из
        Telegram — наивное локальное время. Наивную метку приписываем к локальной
        зоне, иначе арифметика с aware-временем падает.
        """
        dt = datetime.fromisoformat(str(raw))
        return dt if dt.tzinfo else dt.astimezone()
    
    async def _should_publish(self, best_hour: int, best_day: int) -> bool:
        """
        Определяет, нужно ли публиковать сейчас.
        
        Правила:
        1. Не публикуем ночью (23:00 - 07:00)
        2. Проверяем, прошло ли достаточно времени с последней публикации
           ЭТОГО канала
        3. Проверяем, наступило ли лучшее время (со сдвигом канала)
        """
        now = datetime.now()
        current_hour = now.hour
        current_day = now.weekday()
        
        # 1. Проверяем, не ночь ли сейчас
        if not self.min_hour <= current_hour <= self.max_hour:
            self.log(f"🌙 Сейчас {current_hour}:00 — ночь, пропускаю публикацию")
            return False
        
        # 1.1. Дневной лимит: защита от «выгорания» канала, если интервал
        # сбит или ручные запуски идут слишком часто
        if Config.MAX_POSTS_PER_DAY > 0:
            try:
                from database import count_posts_today
                posted_today = count_posts_today(self.profile.key)
                if posted_today >= Config.MAX_POSTS_PER_DAY:
                    self.log(f"🛑 Дневной лимит достигнут: {posted_today} постов "
                             f"за сутки (максимум {Config.MAX_POSTS_PER_DAY})")
                    return False
            except Exception as e:
                self.log(f"⚠️ Не удалось проверить дневной лимит ({e})", is_error=True)
        
        # Сдвиг расписания канала: у лайфхаков он на час позже новостного,
        # чтобы два канала не публиковали одновременно.
        offset_hours = self.profile.schedule_offset // 3600
        effective_best_hour = min(best_hour + offset_hours, self.max_hour)
        
        # 2. Проверяем время с последней публикации
        last_publish = self.memory.get(self._last_publish_key)
        if last_publish:
            try:
                last_time = self._parse_last_publish(last_publish)
                # now берём в той же (локальной) зоне, что и метку: aware - aware,
                # иначе вычитание падает с TypeError прямо в проверке интервала
                time_diff = (datetime.now().astimezone() - last_time).total_seconds()
                
                # Минимальный интервал между публикациями — свой у каждого канала
                min_interval = self.profile.interval
                
                # Отрицательный diff означает метку из будущего (сбились часы) —
                # считаем её «только что», чтобы не публиковать вторым постом
                time_diff = max(0.0, time_diff)
                
                if time_diff < min_interval:
                    wait_hours = (min_interval - time_diff) / 3600
                    self.log(f"⏳ Последняя публикация была {time_diff/3600:.1f} ч назад")
                    self.log(f"⏳ Жду ещё {wait_hours:.1f} ч...")
                    return False
                
            except Exception as e:
                # Молчать нельзя: именно проглоченная ошибка раньше отключала
                # проверку интервала и выпускала второй пост сразу после первого.
                # Битую метку трактуем как «публикация была только что» — лучше
                # пропустить цикл, чем задублировать пост в канале.
                self.log(f"⚠️ Не удалось прочитать метку публикации ({e}) — "
                         f"считаю, что публикация была только что", is_error=True)
                return False
        
        # 3. Если сегодня лучший день — проверяем лучшее время со сдвигом канала
        if current_day == best_day:
            if current_hour >= effective_best_hour:
                self.log(f"✅ Лучший день ({current_day}), время {current_hour}:00 "
                         f">= {effective_best_hour}:00 (сдвиг канала +{offset_hours} ч)")
                return True
            else:
                wait_minutes = (effective_best_hour - current_hour) * 60
                self.log(f"⏳ Жду до {effective_best_hour}:00 "
                         f"(сдвиг канала +{offset_hours} ч, осталось {wait_minutes} мин)")
                return False
        
        # 4. Если сегодня не лучший день, но прошёл интервал — публикуем
        # (в рабочее время, чтобы не было простоев)
        # Метки нет вовсе (первый запуск канала) — публикуем сразу: иначе бот
        # ждал бы «лучшего дня», которого может не быть неделю, и в канале
        # не появилось бы ни одного поста.
        if not last_publish:
            if self.min_hour <= current_hour <= self.max_hour:
                self.log("📊 Публикаций ещё не было — публикую первую")
                return True
            return False

        hours_since_last = 0
        try:
            last_time = self._parse_last_publish(last_publish)
            hours_since_last = max(0.0, (datetime.now().astimezone() - last_time).total_seconds() / 3600)
        except Exception as e:
            self.log(f"⚠️ Ошибка чтения метки публикации ({e}) — интервал не выдержан", is_error=True)
            hours_since_last = 0
        
        if hours_since_last >= self.profile.interval / 3600:
            # Проверяем, что сейчас рабочее время
            if self.min_hour <= current_hour <= self.max_hour:
                self.log(f"📊 Не лучший день, но прошло {hours_since_last:.1f} ч — публикую")
                return True
        
        return False
    
    async def _update_analytics(self):
        """Обновляет аналитику после публикации"""
        self.log("📊 Обновляю аналитику...")
        
        try:
            from .analytics_agent import AnalyticsAgent
            bot = self.orchestrator.bot if self.orchestrator else None
            analytics = AnalyticsAgent(bot=bot)
            # execute() сам пишет отчёт в память — считаем аналитику один раз
            await analytics.execute({})
                
        except Exception as e:
            self.log(f"⚠️ Ошибка обновления аналитики: {e}", is_error=True)
    
    def stop(self):
        """Останавливает планировщик"""
        self.is_running = False
        self.log("⏹️ Планировщик остановлен")