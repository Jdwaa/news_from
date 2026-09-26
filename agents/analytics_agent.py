# agents/analytics_agent.py

"""
АГЕНТ-АНАЛИТИК
Собирает статистику постов из Telegram.
Анализирует, какие посты работают лучше.
Определяет лучшее время для публикации.
Сохраняет уроки в Memory Bank.
"""

import asyncio
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from .base_agent import BaseAgent
from config import Config
from database import get_all_posts_with_stats, save_post_stats
from channels import NEWS

# ===== КОНТРАКТ ОТЧЁТА АНАЛИТИКИ =====
# Канонические ключи отчёта: "hour", "day", "interval_hours", "reason".
# Отчёт кладётся в Memory Bank под ключом "analytics_report".
# Все потребители (Планировщик, Публикатор) обязаны читать его через
# normalize_analytics_report(), чтобы ключи не разъезжались снова.

DEFAULT_BEST_HOUR = 10
DEFAULT_BEST_DAY = 0
DEFAULT_INTERVAL_HOURS = 4


def parse_db_timestamp(raw):
    """
    Разбирает время из БД (posts.published_at).

    SQLite CURRENT_TIMESTAMP пишет UTC и без таймзоны, а весь остальной код
    живёт в локальном времени. Если прочитать такую строку как локальную,
    «лучшее время публикации» уезжает на величину смещения (например, на 4 часа),
    поэтому здесь строка трактуется как UTC и переводится в локальное время.
    """
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone()


def normalize_analytics_report(report: dict = None) -> dict:
    """
    Приводит отчёт аналитики к каноническому виду
    {"hour", "day", "interval_hours", "reason"}.

    Понимает и старый вариант ключей ("best_hour"/"best_day"), чтобы уже
    сохранённые в Memory Bank отчёты читались корректно, а мусорные значения
    не ломали расписание (час вне 0-23 и день вне 0-6 → дефолт).
    """
    if not report:
        return {
            "hour": DEFAULT_BEST_HOUR,
            "day": DEFAULT_BEST_DAY,
            "interval_hours": DEFAULT_INTERVAL_HOURS,
            "reason": "Нет аналитики, используется стандартное время"
        }

    hour = report.get("hour")
    if hour is None:
        hour = report.get("best_hour")
    day = report.get("day")
    if day is None:
        day = report.get("best_day")

    try:
        hour = int(hour)
    except (TypeError, ValueError):
        hour = DEFAULT_BEST_HOUR
    if not 0 <= hour <= 23:
        hour = DEFAULT_BEST_HOUR

    try:
        day = int(day)
    except (TypeError, ValueError):
        day = DEFAULT_BEST_DAY
    if not 0 <= day <= 6:
        day = DEFAULT_BEST_DAY

    try:
        interval_hours = float(report.get("interval_hours"))
    except (TypeError, ValueError):
        interval_hours = float(DEFAULT_INTERVAL_HOURS)

    return {
        "hour": hour,
        "day": day,
        "interval_hours": interval_hours,
        "reason": report.get("reason", "Отчёт аналитики")
    }


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
        posts_to_collect = [p for p in posts if p.get('views') == 0 and p.get('score') == 0]
        
        # Посты без message_id собрать нельзя (опубликованы до того, как мы начали его сохранять)
        without_message_id = [p for p in posts_to_collect if not p.get('message_id')]
        posts_to_collect = [p for p in posts_to_collect if p.get('message_id')]
        
        if without_message_id:
            self.log(
                f"ℹ️ Пропускаю {len(without_message_id)} постов без message_id "
                f"(опубликованы до этого фикса)"
            )
        
        collected = 0
        failed = 0
        for post in posts_to_collect:
            stats = await self._collect_stats_from_telegram(post)
            if stats:
                save_post_stats(post['id'], stats)
                collected += 1
            else:
                failed += 1
        
        if collected > 0:
            self.log(f"✅ Собрано статистики для {collected} постов")
        
        if failed > 0:
            self.log(f"⚠️ Не удалось собрать статистику для {failed} постов")
        
        # 3. Анализируем все посты с данными
        # Перечитываем из БД: у части постов статистика появилась только что, выше
        posts = get_all_posts_with_stats(limit=50)
        posts_with_stats = [p for p in posts if p.get('score', 0) > 0]
        
        if len(posts_with_stats) < 3:
            self.log(f"⚠️ Недостаточно данных для анализа (нужно минимум 3 поста, есть {len(posts_with_stats)})")
            return context
        
        # 4. Находим лучшие посты (для лога)
        best_posts = sorted(posts_with_stats, key=lambda x: x['score'], reverse=True)[:3]
        
        # 5. Извлекаем уроки: сравниваем признаки успешных и слабых постов
        best_practices, worst_practices = self._analyze_practices(posts_with_stats)
        
        # 6. Сохраняем в Memory Bank
        self.memory.set("best_practices", best_practices)
        self.memory.set("worst_practices", worst_practices)
        
        # 7. Определяем лучшее время для публикации и сохраняем отчёт
        report = self.get_best_posting_time()
        self.memory.set("analytics_report", report)
        self.memory.set("last_analysis_at", datetime.now().isoformat())
        
        if best_practices or worst_practices:
            self.log(f"✅ Сохранено {len(best_practices)} лучших практик")
            self.log(f"✅ Сохранено {len(worst_practices)} практик для избегания")
        else:
            self.log("⚠️ Явных закономерностей не нашлось: оценки постов слишком близки")
        
        if best_posts:
            self.log(f"🏆 Лучший пост: {best_posts[0]['title'][:40]}... (оценка: {best_posts[0]['score']}/10)")
        
        # 7. Определяем лучшее время для публикации.
        # Это уже сделано выше (отчёт сохранён в памяти), поэтому считаем один раз:
        # раньше после каждой публикации аналитика проходила целиком дважды —
        # и отсюда, и из планировщика через get_best_posting_time().
        self.log(f"📊 Лучшее время: {report['hour']}:00 (день {report['day']})")
        
        return context
    
    async def _collect_stats_from_telegram(self, post: dict):
        """
        Собирает РЕАЛЬНУЮ статистику поста.
        
        Bot API не отдаёт просмотры конкретного сообщения (метода getMessage нет),
        поэтому схема такая:
        1. через getChat узнаём @username канала (Bot API),
        2. скачиваем публичное превью поста: t.me/<username>/<message_id>?embed=1,
        3. вытаскиваем просмотры и реакции из HTML.
        
        Возвращает dict со статистикой или None, если собрать не удалось.
        Никаких выдуманных чисел: нет данных — нет записи в БД.
        """
        post_id = post.get("id")
        message_id = post.get("message_id")
        
        if not message_id:
            self.log(f"⚠️ Пост #{post_id}: нет message_id, статистику не собрать", is_error=True)
            return None
        
        if not self.bot:
            self.log("⚠️ Бот не передан — не могу определить @username канала", is_error=True)
            return None
        
        # Канал берём из самого поста: у бота их два, и статистика лайфхаков
        # собиралась бы по username новостного канала — то есть попадала бы
        # в чужие данные или не собиралась вовсе.
        channel_key = post.get("channel") or NEWS
        username = await self._get_channel_username(channel_key)
        if not username:
            self.log(f"⚠️ Пост #{post_id}: у канала нет @username, web-preview недоступен", is_error=True)
            return None
        
        try:
            html = await asyncio.to_thread(self._fetch_preview, username, message_id)
        except Exception as e:
            self.log(f"⚠️ Пост #{post_id}: не удалось скачать превью — {e}", is_error=True)
            return None
        
        views, reactions = self._parse_preview(html)
        
        if views is None:
            self.log(f"⚠️ Пост #{post_id}: в превью нет просмотров (пост не найден?)", is_error=True)
            return None
        
        # clicks / shares / read_ratio публичное превью не отдаёт
        score = self._calculate_score(views, reactions, 0, 0, 0)
        
        self.log(
            f"📈 Пост #{post_id}: {views} просмотров, "
            f"реакций {sum(reactions.values())}, оценка {score}/10"
        )
        
        return {
            'views': views,
            'reactions': reactions,
            'clicks': 0,
            'shares': 0,
            'read_ratio': 0.0,
            'score': score
        }
    
    async def _get_channel_username(self, channel_key: str = None):
        """
        Возвращает @username канала через Bot API (с кэшем в Memory Bank).

        Кэш отдельный на каждый канал: у бота их два, и общий ключ отдавал бы
        username новостного канала при сборе статистики лайфхаков.
        """
        from channels import PROFILES, NEWS, get_profile

        key = channel_key or NEWS
        cache_key = f"channel_username:{key}"

        cached = self.memory.get(cache_key)
        if cached:
            return cached

        try:
            profile = get_profile(key)
            chat_id = profile.chat_id or Config.CHANNEL_ID
            chat = await self.bot.get_chat(chat_id)
            username = getattr(chat, "username", None)
        except Exception as e:
            self.log(f"⚠️ Не удалось получить информацию о канале {key}: {e}", is_error=True)
            return None

        if username:
            self.memory.set(cache_key, username)
            self.log(f"📡 Канал {key} определён: @{username}")

        return username
    
    def _fetch_preview(self, username: str, message_id: int) -> str:
        """Скачивает публичную страницу-превью поста."""
        url = f"https://t.me/{username}/{message_id}?embed=1&mode=tme"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        return response.text
    
    def _parse_preview(self, html: str) -> tuple:
        """Возвращает (просмотры, реакции) из HTML превью. Просмотры = None, если не нашлись."""
        soup = BeautifulSoup(html, 'html.parser')
        
        views = None
        views_el = soup.find(class_="tgme_widget_message_views")
        if views_el:
            views = self._parse_views(views_el.get_text(strip=True))
        
        reactions = {}
        for el in soup.find_all(class_="tgme_widget_message_reaction"):
            emoji_el = el.find(class_="tgme_widget_message_reaction_emoji")
            if not emoji_el:
                continue
            emoji = emoji_el.get_text(strip=True)
            if not emoji:
                continue
            
            count = 0
            count_el = el.find(class_="tgme_widget_message_reaction_count")
            if count_el:
                digits = re.sub(r"\D", "", count_el.get_text())
                count = int(digits) if digits else 0
            reactions[emoji] = count
        
        return views, reactions
    
    @staticmethod
    def _parse_views(raw: str):
        """
        '1.2K' -> 1200, '15,3 тыс.' -> 15300, '2.5M' -> 2500000, '1 234' -> 1234.
        Возвращает None, если разобрать не удалось.
        """
        if not raw:
            return None
        
        text = raw.replace("\xa0", " ").strip().lower()
        match = re.match(r"^([0-9][0-9\s.,]*)\s*([a-zа-я.]*)$", text)
        if not match:
            return None
        
        number_part = match.group(1).replace(" ", "")
        suffix = match.group(2).strip(". ")
        
        if number_part.count(",") and number_part.count("."):
            # '1,234.5' — запятая как разделитель тысяч
            number_part = number_part.replace(",", "")
        else:
            number_part = number_part.replace(",", ".")
        
        if number_part.count(".") > 1:
            number_part = number_part.replace(".", "")
        else:
            integer, _, fraction = number_part.partition(".")
            if len(fraction) == 3 and not suffix:
                number_part = integer + fraction  # '1.234' — это 1234, а не 1.234
        
        try:
            value = float(number_part)
        except ValueError:
            return None
        
        multipliers = {
            "": 1,
            "k": 1000,
            "m": 1_000_000,
            "b": 1_000_000_000,
            "тыс": 1000,
            "млн": 1_000_000,
            "млрд": 1_000_000_000
        }
        return int(value * multipliers.get(suffix, 1))
    
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
    
    # Признаки поста: (название, проверка(title, text), совет «делай», совет «избегай»)
    FEATURE_CHECKS = [
        (
            "вопрос к читателю",
            lambda title, text: "?" in text or "❓" in text,
            "Задавай читателю вопрос",
            "Не оставляй пост без обращения к читателю",
        ),
        (
            "личное мнение",
            lambda title, text: any(
                k in text.lower() for k in ("мне кажется", "я считаю", "я думаю", "я вижу")
            ),
            "Добавляй личное мнение",
            "Пиши не только факты — добавляй свою оценку",
        ),
        (
            "прогноз о будущем",
            lambda title, text: any(
                k in text.lower() for k in ("через", "в будущем", "скоро", "станет", "будет")
            ),
            "Делай прогноз о будущем",
            "Без прогноза пост читается как сухая новость",
        ),
        (
            "≥5 эмодзи",
            lambda title, text: len(re.findall(r'[\U0001F600-\U0001F9FF]', text)) >= 5,
            "Ставь минимум 5 эмодзи",
            "Слишком мало эмодзи — текст выглядит сухо",
        ),
        (
            f"длина {Config.MIN_POST_LENGTH}-{Config.MAX_POST_LENGTH} символов",
            lambda title, text: Config.MIN_POST_LENGTH <= len(text.strip()) <= Config.MAX_POST_LENGTH,
            f"Держи длину в диапазоне {Config.MIN_POST_LENGTH}-{Config.MAX_POST_LENGTH} символов",
            f"Уходи от длины {Config.MIN_POST_LENGTH}-{Config.MAX_POST_LENGTH} символов",
        ),
        (
            "≥3 абзаца",
            lambda title, text: len([p for p in text.split("\n\n") if p.strip()]) >= 3,
            "Разбивай пост на 3+ абзаца",
            "Сплошной текст без абзацев читается хуже",
        ),
        (
            "КАПС в заголовке",
            lambda title, text: sum(
                1 for w in title.split() if w.isupper() and len(w) > 2
            ) >= 2,
            "Выделяй заголовок КАПСОМ",
            "Заголовок без КАПСА теряется в ленте",
        ),
        (
            "хештеги",
            lambda title, text: "#" in text,
            "Добавляй 2-3 хештега",
            "Без хештегов пост хуже находят",
        ),
        (
            "запрещённые формулировки",
            lambda title, text: any(
                w in text.lower() for w in ("согласно", "как сообщает", "по данным")
            ),
            "Ссылки на источник можно оставить",
            "Убирай «согласно», «как сообщает», «по данным»",
        ),
    ]

    # Минимальная разница средних оценок, чтобы считать закономерность реальной
    PRACTICE_SCORE_MARGIN = 0.5

    def _analyze_practices(self, posts: list) -> tuple:
        """
        Ищет признаки, которые реально связаны с оценкой поста.

        Для каждого признака сравнивает среднюю оценку постов с ним и без него.
        Один признак попадает максимум в один список, поэтому «делай так» и
        «избегай этого» больше не противоречат друг другу (как было раньше,
        когда одни и те же фразы уходили и в лучшие, и в худшие практики).

        Возвращает (best_practices, worst_practices).
        """
        best_practices = []
        worst_practices = []
        
        for name, check, advice_good, advice_bad in self.FEATURE_CHECKS:
            with_feature = []
            without_feature = []
            
            for post in posts:
                try:
                    has_feature = bool(check(post.get('title', ''), post.get('content', '')))
                except Exception:
                    continue
                (with_feature if has_feature else without_feature).append(post.get('score', 0) or 0)
            
            # Нужны обе группы, иначе сравнивать не с чем
            if not with_feature or not without_feature:
                continue
            
            avg_with = sum(with_feature) / len(with_feature)
            avg_without = sum(without_feature) / len(without_feature)
            delta = avg_with - avg_without
            stats = (
                f"средняя оценка {avg_with:.1f}/10 против {avg_without:.1f}/10 без него, "
                f"постов с признаком: {len(with_feature)}"
            )
            
            if delta >= self.PRACTICE_SCORE_MARGIN:
                best_practices.append(f"{advice_good} ({stats})")
            elif -delta >= self.PRACTICE_SCORE_MARGIN:
                worst_practices.append(f"{advice_bad} ({stats})")
        
        return best_practices[:5], worst_practices[:5]
    
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
        
        practices = self.memory.get('best_practices') or ["Пока нет данных"]
        practices_text = "\n".join(f"• {p}" for p in practices[:3])
        
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
{practices_text}
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
            return normalize_analytics_report({
                "hour": DEFAULT_BEST_HOUR,
                "day": DEFAULT_BEST_DAY,
                "interval_hours": DEFAULT_INTERVAL_HOURS,
                "reason": "Недостаточно данных, используется стандартное время"
            })
        
        # Анализируем часы и дни
        hour_stats = {}
        day_stats = {}
        
        for post in posts_with_stats:
            published_at = post.get('published_at')
            if not published_at:
                continue
            
            try:
                dt = parse_db_timestamp(published_at)
                if dt is None:
                    continue
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
                    prev = parse_db_timestamp(sorted_posts[i-1].get('published_at'))
                    curr = parse_db_timestamp(sorted_posts[i].get('published_at'))
                    if prev is None or curr is None:
                        continue
                    diff = (curr - prev).total_seconds() / 3600
                    intervals.append(diff)
                except Exception:
                    continue
            avg_interval = sum(intervals) / len(intervals) if intervals else 4
        else:
            avg_interval = 4
        
        # Ограничиваем интервал от 2 до 6 часов
        avg_interval = max(2, min(6, avg_interval))
        
        self.log(f"📊 Лучшее время: {best_hour}:00, день недели: {best_day}, интервал: {avg_interval:.1f} ч")
        
        return normalize_analytics_report({
            "hour": best_hour,
            "day": best_day,
            "interval_hours": round(avg_interval, 1),
            "reason": f"На основе {len(posts_with_stats)} постов"
        })
    
    def _day_name(self, day_num: int) -> str:
        """Возвращает название дня недели"""
        days = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]
        return days[day_num] if 0 <= day_num <= 6 else "Неизвестно"