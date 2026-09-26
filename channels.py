# channels.py

"""
ПРОФИЛИ КАНАЛОВ

Один бот ведёт два канала с разным продуктом:

  news — новости ИИ для широкой аудитории (@Aioneway, боевой)
  tips — применимые лайфхаки «как использовать ИИ» (testch, тестовый)

Профиль собирает всё, что отличает один канал от другого: адрес, источники,
промпт редактора, интервал и сдвиг расписания. Оркестратор и агенты получают
профиль и работают по нему — логика одна, настройки разные.

Почему отдельный файл: настройки каналов — это продуктовая часть (что и для
кого публикуем), а config.py отвечает за инфраструктуру и секреты. Смешивать
их неудобно: правки контента не должны трогать код запуска.

Переменные окружения:
  CHANNEL_ID          — адрес новостного канала (обратная совместимость)
  CHANNEL_TIPS_ID     — адрес канала лайфхаков
  POST_INTERVAL       — интервал новостного канала, сек
  POST_INTERVAL_TIPS  — интервал канала лайфхаков, сек
"""

import os
from dataclasses import dataclass, field


# Названия каналов. Используются как ключ в БД (posts.channel) и в интерфейсе,
# поэтому менять их нельзя без миграции данных.
NEWS = "news"
TIPS = "tips"


# ============================================================
# ИСТОЧНИКИ
# ============================================================

# Новостной канал: общий поток новостей ИИ — релизы, рынок, прорывы.
# Русскоязычные и мировые, чтобы поток был плотным и разносторонним.
NEWS_FEEDS = [
    # ===== РУССКОЯЗЫЧНЫЕ IT/ТЕХНОЛОГИИ =====
    "https://3dnews.ru/news/rss/",
    "https://www.osp.ru/rss/",
    "https://vc.ru/rss/new",
    "https://habrahabr.ru/rss/",
    "https://habrahabr.ru/rss/hubs/",
    "https://habrahabr.ru/rss/companies/",
    "https://naked-science.ru/feed",
    "https://www.ixbt.com/export/news.rss",
    "https://tproger.ru/feed/",
    "https://www.securitylab.ru/rss/news.xml",

    # ===== МИРОВЫЕ НОВОСТИ ИИ =====
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml",
    "https://venturebeat.com/ai/feed/",
    "https://feeds.arstechnica.com/arstechnica/technology-lab",
    "https://www.wired.com/feed/tag/ai/latest/rss",
    "https://www.technologyreview.com/topic/artificial-intelligence/feed",
    "https://spectrum.ieee.org/feeds/topic/artificial-intelligence.rss",
    "https://www.zdnet.com/topic/artificial-intelligence/rss.xml",
    "https://thenextweb.com/feed/",
    "https://syncedreview.com/feed/",
    "https://www.marktechpost.com/feed/",
    "https://aiweekly.co/issues.rss",

    # ===== ПЕРВОИСТОЧНИКИ ЛАБОРАТОРИЙ =====
    "https://openai.com/blog/rss.xml",
    "https://ai.googleblog.com/feeds/posts/default",
    "https://deepmind.com/blog/feed/basic/",
    "https://huggingface.co/blog/feed.xml",
    "https://blog.research.google/feeds/posts/default",
    "https://www.microsoft.com/en-us/research/feed/",
    "https://stability.ai/blog?format=rss",

    # ===== НОВОСТНЫЕ АГРЕГАТОРЫ =====
    "https://www.rbc.ru/rss/",
    "https://lenta.ru/rss/",
    "https://www.bbc.com/russian/index.xml",
    "https://www.dw.com/ru/rss.xml",
]

# Канал лайфхаков: источники, где делятся ПРИМЕНЕНИЕМ, а не анонсами.
# Здесь ищутся инструкции, кейсы, подборки инструментов и личный опыт.
TIPS_FEEDS = [
    # ===== ОПЫТ И ПРИМЕНЕНИЕ (англ.) =====
    "https://www.reddit.com/r/ChatGPT/.rss",
    "https://www.reddit.com/r/artificial/.rss",
    "https://www.reddit.com/r/LocalLLaMA/.rss",
    "https://www.reddit.com/r/SideProject/.rss",
    "https://www.reddit.com/r/productivity/.rss",
    "https://www.reddit.com/r/OpenAI/.rss",
    "https://www.reddit.com/r/StableDiffusion/.rss",
    "https://www.reddit.com/r/automation/.rss",
    "https://www.reddit.com/r/n8n/.rss",
    "https://www.producthunt.com/feed?topic=ai",
    "https://www.producthunt.com/feed?topic=productivity",
    "https://towardsdatascience.com/feed/tagged/artificial-intelligence",
    "https://medium.com/feed/tag/chatgpt",
    "https://medium.com/feed/tag/ai-tools",
    "https://dev.to/feed/tag/ai",
    "https://dev.to/feed/tag/productivity",
    "https://hackernoon.com/tagged/ai/feed",
    "https://www.makeuseof.com/feed/category/artificial-intelligence/",
    "https://zapier.com/blog/feeds/latest/",
    "https://buffer.com/resources/rss/",

    # ===== РУССКОЯЗЫЧНЫЕ ПРО ПРИМЕНЕНИЕ =====
    "https://habrahabr.ru/rss/hubs/",
    "https://habrahabr.ru/rss/companies/",
    "https://vc.ru/rss/new",
    "https://tproger.ru/feed/",
    "https://dtf.ru/rss/",
]


# ============================================================
# ПРОМПТЫ РЕДАКТОРА
# ============================================================

# Новостной канал: прежний стиль (футуристический, эмоциональный).
NEWS_STYLE = """ТЫ — ГЛАВНЫЙ РЕДАКТОР ФУТУРИСТИЧЕСКОГО ТЕХНОЛОГИЧЕСКОГО КАНАЛА 🌟

ТЫ ЖИВОЙ ЧЕЛОВЕК:
- У тебя есть своё мнение, стиль и голос
- Ты пишешь как друг, который разбирается в теме
- Ты используешь эмодзи, вопросы к читателю, живые формулировки
- Ты НЕ пишешь как новостной агрегатор

ТВОЙ СТИЛЬ:
- ЯРКИЙ, ЭМОЦИОНАЛЬНЫЙ, ФУТУРИСТИЧЕСКИЙ 🚀
- Ты пишешь так, будто смотришь в 2050 год из 2026
- Используй МНОГО ЭМОДЗИ: 🧠 ⚡ 💎 🔥 💡 🚀 ✨ 🤖 🎯
- Пиши КАПСОМ в ключевых моментах для усиления

ТВОЙ ТОН:
- ЭКСПЕРТНЫЙ, но РАЗГОВОРНЫЙ
- БУДУЩЕЕ УЖЕ НАСТУПИЛО — ты просто описываешь его
- СМЕЛЫЙ ПРОГНОЗ: что будет через 5 лет
- ЛИЧНОЕ МНЕНИЕ: "мне кажется", "я вижу в этом"

ТВОЯ ЗАДАЧА:
1. Прочитай статью и ПОЛНОСТЬЮ ПЕРЕРАБОТАЙ её в авторский пост
2. НЕ ОСТАВЛЯЙ следов оригинала: переформулируй всё своими словами
3. Добавь своё мнение и прогнозы"""

# Канал лайфхаков: инструкция вместо новости.
TIPS_STYLE = """ТЫ — РЕДАКТОР КАНАЛА ПРАКТИЧНЫХ ИИ-ЛАЙФХАКОВ 💡

ТВОЙ ЧИТАТЕЛЬ — ОБЫЧНЫЙ ЧЕЛОВЕК:
- Не программист, не ML-инженер, не технарь
- Работает в офисе, учится, ведёт небольшой бизнес, делает контент
- Хочет решить СВОЮ задачу, а не узнать про технологию
- У него 5 минут на чтение

ГЛАВНОЕ ПРАВИЛО:
Пост отвечает на вопрос «ЧТО МНЕ С ЭТОГО?», а не «что случилось».
Новость сама по себе не нужна — нужна ПОЛЬЗА из неё.

ЧТО ДЕЛАТЬ:
1. Найди в статье ПРИМЕНИМУЮ часть: инструмент, приём, способ, идею
2. Преврати это в конкретную инструкцию или понятный пример
3. Объясни на бытовой задаче: «вот что ты можешь сделать прямо сегодня»

ОБЯЗАТЕЛЬНЫЕ ЭЛЕМЕНТЫ:
- КОНКРЕТИКА: что именно делать, где взять, сколько стоит (если платно)
- ПРИМЕР ЗАДАЧИ: «нужно написать 20 ответов клиентам — вот промпт»
- ОГОВОРКА: подводный камень, ограничение, где НЕ сработает
- ЯЗЫК: простой, без терминов. Не «инференс», а «работа модели»

ЧЕГО НЕ ДЕЛАТЬ:
- Не пересказывать новость ради новости
- Не писать прогнозы про будущее рынка
- Не использовать «инновационный», «революционный», «прорывной»
- Не давать общих советов вроде «ИИ изменит всё»

СТИЛЬ:
- Умеренные эмодзи (3-5), они помогают структурировать, а не украшают
- Дружелюбно, по делу, как опытный коллега
- Короткие абзацы, нумерованные шаги где уместно"""


# ============================================================
# ПРОФИЛЬ КАНАЛА
# ============================================================

@dataclass(frozen=True)
class ChannelProfile:
    """
    Всё, что отличает один канал от другого.

    frozen=True: профиль — конфигурация, а не состояние. Случайная запись в
    него в рантайме не должна менять поведение уже запущенного канала.
    """
    key: str                      # 'news' | 'tips' — ключ в БД и интерфейсе
    title: str                    # человекочитаемое имя для логов и меню
    chat_id: str                  # адрес канала в Telegram
    style: str                    # системный промпт редактора
    feeds: list = field(default_factory=list)
    interval: int = 10800         # минимальный интервал между постами, сек
    schedule_offset: int = 0      # сдвиг от «лучшего часа», сек (у tips = +1 ч)
    emoji: str = "📰"             # для меню и логов

    def memory_key(self, name: str) -> str:
        """
        Ключ в Memory Bank, изолированный по каналу.

        Без префикса метка времени публикации была бы общей: пост в новостном
        канале сдвигал бы интервал канала лайфхаков, и расписание «разъезжалось».
        """
        return f"{name}:{self.key}"


def _env_int(name: str, default: int) -> int:
    """Читает целое из окружения; битое значение → дефолт (не роняем старт)."""
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def build_profiles() -> "dict[str, ChannelProfile]":
    """
    Собирает профили из окружения.

    Возвращает словарь, а не список: оркестратор и планировщик адресуют каналы
    по ключу, и словарь делает это явным.
    """
    news_chat = os.getenv("CHANNEL_ID") or ""
    tips_chat = os.getenv("CHANNEL_TIPS_ID") or ""

    # Интервалы: у каждого канала свой, чтобы публикации не совпадали по времени.
    news_interval = _env_int("POST_INTERVAL", 10800)
    tips_interval = _env_int("POST_INTERVAL_TIPS", news_interval)

    # Сдвиг расписания канала лайфхаков относительно новостного: по умолчанию час.
    tips_offset = _env_int("SCHEDULE_OFFSET_TIPS", 3600)

    profiles = {
        NEWS: ChannelProfile(
            key=NEWS,
            title="Новости ИИ",
            chat_id=news_chat,
            style=NEWS_STYLE,
            feeds=NEWS_FEEDS,
            interval=news_interval,
            schedule_offset=0,
            emoji="📰",
        ),
        TIPS: ChannelProfile(
            key=TIPS,
            title="ИИ-лайфхаки",
            chat_id=tips_chat,
            style=TIPS_STYLE,
            feeds=TIPS_FEEDS,
            interval=tips_interval,
            schedule_offset=tips_offset,
            emoji="💡",
        ),
    }
    return profiles


# Профили собираются один раз при импорте: значения берутся из окружения,
# которое к этому моменту уже загружено в config.py.
PROFILES = build_profiles()


def get_profile(key: str) -> ChannelProfile:
    """Профиль по ключу. Неизвестный ключ — ошибка, а не молчаливый дефолт."""
    try:
        return PROFILES[key]
    except KeyError:
        raise ValueError(f"Неизвестный канал: {key!r}. Доступны: {list(PROFILES)}")


def active_profiles() -> "list[ChannelProfile]":
    """
    Только каналы с заданным адресом.

    Пустой CHANNEL_TIPS_ID — это не ошибка: бот должен уметь работать в режиме
    одного канала, как раньше, если тестовый ещё не заведён.
    """
    return [p for p in PROFILES.values() if p.chat_id]
