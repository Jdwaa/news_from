# agents/analytics_agent.py

"""
АГЕНТ-АНАЛИТИК
Собирает статистику постов из Telegram.
Анализирует, какие посты работают лучше.
Определяет лучшее время для публикации.
Сохраняет уроки в Memory Bank.
"""

import json
import re
from datetime import datetime, timedelta
from collections import Counter
from .base_agent import BaseAgent
from database import get_all_posts_with_stats, save_post_stats, get_post_stats


class AnalyticsAgent(BaseAgent):
    """Агент для сбора и анализа статистики постов"""
    
    def __init__(self, bot=None):
        super().__init__("Аналитик")
        self.bot = bot
    
    async def execute(self, context: dict) -> dict:
        """
        1. Проверяет, есть ли посты без статистики
        2. Собирает статистику из Telegram
        3. Анализирует лучшие/худшие посты
        4. Определяет лучшее время для публикации
        5. Сохраняет уроки в Memory Bank
        """
        self.log("📊 Начинаю сбор и анализ статистики...")
        
        # 1. Получаем последние посты
        posts = get_all_posts_with_stats(limit=50)
        
        if not posts:
            self.log("⚠️ Нет постов для анализа", is_error=True)
            return context
        
        # 2. Собираем статистику для постов, у которых её нет
        collected = 0
        for post in posts:
            if post.get('views') == 0 and post.get('score') == 0:
                stats = await self._collect_stats_from_telegram(post)
                if stats:
                    save_post_stats(post['id'], stats)
                    collected += 1
        
        if collected > 0:
            self.log(f"✅ Собрано статистики для {collected} постов")
        
        # 3. Анализируем все посты с данными
        posts_with_stats = [p for p in posts if p.get('score', 0) > 0]
        
        if len(posts_with_stats) < 3:
            self.log(f"⚠️ Недостаточно данных для анализа (нужно минимум 3 поста, есть {len(posts_with_stats)})")
            return context
        
        # 4. Находим лучшие и худшие посты
        best_posts = sorted(posts_with_stats, key=lambda x: x['score'], reverse=True)[:3]
        worst_posts = sorted(posts_with_stats, key=lambda x: x['score'])[:3]
        
        # 5. Извлекаем уроки
        best_practices = self._extract_practices(best_posts, "success")
        worst_practices = self._extract_practices(worst_posts, "fail")
        
        # 6. Сохраняем в Memory Bank
        self.memory.set("best_practices", best_practices)
        self.memory.set("worst_practices", worst_practices)
        self.memory.set("last_analysis_at", datetime.now().isoformat())
        
        self.log(f"✅ Сохранено {len(best_practices)} лучших практик")
        self.log(f"✅ Сохранено {len(worst_practices)} практик для избегания")
        
        if best_posts:
            self.log(f"🏆 Лучший пост: {best_posts[0]['title'][:40]}... (оценка: {best_posts[0]['score']}/10)")
        
        # 7. Определяем лучшее время для публикации
        best_time = self.get_best_posting_time()
        self.memory.set("analytics_report", best_time)
        self.log(f"📊 Лучшее время: {best_time['hour']}:00 (день {best_time['day']})")
        
        return context
    
    async def _collect_stats_from_telegram(self, post: dict) -> dict:
        """Собирает реальную статистику поста из Telegram"""
        
        if not self.bot:
            self.log("⚠️ Бот не передан, использую заглушку")
            return self._generate_dummy_stats(post)
        
        try:
            # В реальном коде здесь нужно получать статистику из Telegram API
            # Пока используем заглушку
            return self._generate_dummy_stats(post)
        except Exception as e:
            self.log(f"⚠️ Ошибка сбора статистики: {e}", is_error=True)
            return self._generate_dummy_stats(post)

    def _generate_dummy_stats(self, post: dict) -> dict:
        """Генерирует тестовую статистику"""
        import random
        views = random.randint(50, 500)
        reactions = {'👍': random.randint(0, 20)}
        return {
            'views': views,
            'reactions': reactions,
            'clicks': 0,
            'shares': 0,
            'read_ratio': random.uniform(0.3, 0.8),
            'score': random.randint(4, 9)
        }
    
    def _calculate_score(self, views: int, reactions: dict, clicks: int, shares: int, read_ratio: float) -> int:
        """Вычисляет оценку качества поста (1-10)"""
        score = 5  # База
        
        # Просмотры
        if views > 100:
            score += 1
        if views > 300:
            score += 1
        
        # Реакции
        total_reactions = sum(reactions.values())
        if total_reactions > 5:
            score += 1
        if total_reactions > 15:
            score += 1
        
        # Дочитываемость
        if read_ratio > 0.5:
            score += 1
        if read_ratio > 0.7:
            score += 1
        
        # Клики
        if clicks > 3:
            score += 1
        
        # Шары
        if shares > 2:
            score += 1
        
        return min(10, max(1, score))
    
    def _extract_practices(self, posts: list, post_type: str) -> list:
        """Извлекает практики из постов"""
        practices = []
        
        for post in posts:
            text = post.get('content', '')
            if not text:
                continue
            
            # Анализируем заголовки
            if '???' in text:
                practices.append("Используй вопросы в заголовках для вовлечения")
            if '❗' in text or '!' in text:
                practices.append("Используй восклицания для эмоциональности")
            
            # Анализируем личное мнение
            if 'мне кажется' in text or 'я считаю' in text:
                practices.append("Добавляй личное мнение в посты")
            
            # Анализируем прогнозы
            if 'через 5 лет' in text or 'в будущем' in text:
                practices.append("Делай прогнозы о будущем")
            
            # Анализируем эмодзи
            emoji_count = len(re.findall(r'[\U0001F600-\U0001F9FF]', text))
            if emoji_count >= 5:
                practices.append("Используй минимум 5 эмодзи в посте")
            elif emoji_count < 2:
                practices.append("Добавляй больше эмодзи для живости")
            
            # Анализируем длину
            length = len(text.strip())
            if 600 <= length <= 950:
                practices.append("Держи пост в диапазоне 600-950 символов")
        
        # Убираем дубликаты
        return list(set(practices))
    
    def get_summary(self) -> str:
        """Возвращает краткую сводку аналитики"""
        posts = get_all_posts_with_stats(limit=50)
        posts_with_stats = [p for p in posts if p.get('score', 0) > 0]
        
        if not posts_with_stats:
            return "📊 Нет данных для анализа"
        
        avg_score = sum(p['score'] for p in posts_with_stats) / len(posts_with_stats)
        avg_views = sum(p['views'] for p in posts_with_stats) / len(posts_with_stats)
        
        best = max(posts_with_stats, key=lambda x: x['score'])
        worst = min(posts_with_stats, key=lambda x: x['score'])
        
        # Получаем лучшее время
        best_time = self.get_best_posting_time()
        
        summary = f"""
📊 **Аналитика за последние {len(posts_with_stats)} постов**

📈 **Средняя оценка:** {avg_score:.1f}/10
👁️ **Средние просмотры:** {avg_views:.0f}

🏆 **Лучший пост:** 
{best['title'][:40]}... (оценка {best['score']}/10)

📉 **Худший пост:**
{worst['title'][:40]}... (оценка {worst['score']}/10)

⏰ **Лучшее время публикации:**
{best_time['hour']}:00 (день {self._day_name(best_time['day'])})
Интервал: {best_time['interval_hours']} ч

📚 **Лучшие практики:**
{self.memory.get('best_practices', ['Нет данных'])[:3]}
        """
        return summary
    
    def get_best_posting_time(self) -> dict:
        """
        Анализирует историю постов и возвращает лучшее время для публикации.
        Учитывает:
        - Просмотры и оценки постов
        - День недели
        - Час публикации
        - Не рекомендует ночное время (23:00 - 07:00)
        """
        posts = get_all_posts_with_stats(limit=100)
        posts_with_stats = [p for p in posts if p.get('score', 0) > 0]
        
        if not posts_with_stats:
            return {
                "hour": 10,
                "day": 0,
                "interval_hours": 4,
                "reason": "Недостаточно данных, используется стандартное время"
            }
        
        # Анализируем часы и дни
        hour_stats = {}
        day_stats = {}
        
        for post in posts_with_stats:
            published_at = post.get('published_at')
            if not published_at:
                continue
            
            try:
                dt = datetime.fromisoformat(published_at.replace('Z', '+00:00'))
                hour = dt.hour
                day = dt.weekday()
                score = post.get('score', 0)
                views = post.get('views', 0)
                weight = score / 10 + views / 100
                
                if hour not in hour_stats:
                    hour_stats[hour] = 0
                hour_stats[hour] += weight
                
                if day not in day_stats:
                    day_stats[day] = 0
                day_stats[day] += weight
                
            except Exception as e:
                self.log(f"  ⚠️ Ошибка парсинга даты: {e}", is_error=True)
                continue
        
        # Находим лучший час (с учётом ночного запрета 23:00 - 07:00)
        valid_hours = {h: w for h, w in hour_stats.items() if 7 <= h <= 22}
        best_hour = max(valid_hours, key=valid_hours.get) if valid_hours else 10
        
        # Находим лучший день недели
        best_day = max(day_stats, key=day_stats.get) if day_stats else 0
        
        # Рассчитываем средний интервал между публикациями
        if len(posts_with_stats) > 1:
            intervals = []
            sorted_posts = sorted(posts_with_stats, key=lambda x: x.get('published_at', ''))
            for i in range(1, len(sorted_posts)):
                try:
                    prev = datetime.fromisoformat(sorted_posts[i-1]['published_at'].replace('Z', '+00:00'))
                    curr = datetime.fromisoformat(sorted_posts[i]['published_at'].replace('Z', '+00:00'))
                    diff = (curr - prev).total_seconds() / 3600
                    intervals.append(diff)
                except:
                    continue
            avg_interval = sum(intervals) / len(intervals) if intervals else 4
        else:
            avg_interval = 4
        
        # Ограничиваем интервал от 2 до 6 часов
        avg_interval = max(2, min(6, avg_interval))
        
        self.log(f"📊 Лучшее время: {best_hour}:00, день недели: {best_day}, интервал: {avg_interval:.1f} ч")
        
        return {
            "hour": best_hour,
            "day": best_day,
            "interval_hours": round(avg_interval, 1),
            "reason": f"На основе {len(posts_with_stats)} постов"
        }
    
    def _day_name(self, day_num: int) -> str:
        """Возвращает название дня недели"""
        days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
        return days[day_num] if 0 <= day_num <= 6 else "Неизвестно"