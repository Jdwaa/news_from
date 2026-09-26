# config.py

import os
from dotenv import load_dotenv
from console_utils import setup_utf8_console

load_dotenv()

# Консоль Windows по умолчанию cp1251, и первый же print с эмодзи падает с
# UnicodeEncodeError. config импортируют все точки входа — настраиваем один раз здесь.
setup_utf8_console()


def _env_int(name: str, default: int) -> int:
    """Читает целое из переменных окружения, не падая на пустом или битом значении."""
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    """Читает флаг: 1/true/yes/on — да, 0/false/no/off — нет, мусор — дефолт."""
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    value = str(raw).strip().lower()
    if value in ("1", "true", "yes", "on", "да"):
        return True
    if value in ("0", "false", "no", "off", "нет"):
        return False
    return default


class Config:
    # Telegram
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
    CHANNEL_ID = os.getenv("CHANNEL_ID")
    ADMIN_ID = _env_int("ADMIN_ID", 0)
    # Дополнительные админы через запятую: ADMIN_IDS=111,222
    EXTRA_ADMIN_IDS = [
        int(u.strip()) for u in os.getenv("ADMIN_IDS", "").split(",")
        if u.strip().lstrip("-").isdigit()
    ]
    # Notion-админ, который управляет ботом-приложением (используется как id чата)
    OWNER_CHAT_ID = _env_int("OWNER_CHAT_ID", 0)
    
    # AI
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
    ODIROUTER_API_KEY = os.getenv("ODIROUTER_API_KEY")
    NEWSAPI_KEY = os.getenv("NEWSAPI_KEY")
    
    # RSS источники
    RSS_FEEDS = [
    # ===== РОССИЙСКИЕ IT/ТЕХНОЛОГИИ =====
    "https://3dnews.ru/news/rss/",           # 3DNews — цифровые технологии [citation:2]
    "https://www.osp.ru/rss/",               # Открытые системы — ИТ-индустрия [citation:7]
    "https://vc.ru/rss/new",                 # VC.ru — бизнес и стартапы [citation:5]
    
    # ===== ХАБЫ И СООБЩЕСТВА =====
    "https://habrahabr.ru/rss/",             # Habr — главная
    "https://habrahabr.ru/rss/hubs/",        # Habr — по хабам
    "https://habrahabr.ru/rss/companies/",   # Habr — по компаниям
    
    # ===== МЕЖДУНАРОДНЫЕ АНГЛОЯЗЫЧНЫЕ =====
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://openai.com/blog/rss.xml",
    "https://ai.googleblog.com/feeds/posts/default",
    "https://deepmind.com/blog/feed/basic/",
    "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml",
    "https://venturebeat.com/ai/feed/",
    "https://feeds.arstechnica.com/arstechnica/technology-lab",
    "https://towardsdatascience.com/feed/tagged/artificial-intelligence",
    "https://www.producthunt.com/feed?topic=ai",
    "https://www.reddit.com/r/ChatGPT/.rss",
    "https://www.reddit.com/r/artificial/.rss",
    "https://www.reddit.com/r/SideProject/.rss",
    
    # ===== НОВОСТНЫЕ АГРЕГАТОРЫ =====
    "https://www.rbc.ru/rss/",               # РБК — деловые
    "https://lenta.ru/rss/",                 # Лента.ру
    "https://www.bbc.com/russian/index.xml", # BBC Russian
    "https://www.dw.com/ru/rss.xml",         # Deutsche Welle
]
    
    # Настройки. Значения читаются из переменных окружения хостинга;
    # при локальном запуске их можно положить в .env (см. README).
    POST_INTERVAL = _env_int("POST_INTERVAL", 10800)        # 3 часа
    MAX_POST_LENGTH = _env_int("MAX_POST_LENGTH", 950)
    MIN_POST_LENGTH = _env_int("MIN_POST_LENGTH", 300)
    # Путь к БД. На хостинге лучше абсолютный путь к смонтированному диску,
    # иначе данные могут пропасть при пересборке контейнера.
    DB_PATH = os.getenv("DB_PATH") or "news_bot.db"
    # Папка для памяти агентов (JSON-файлы), та же логика про сохранность данных
    MEMORY_PATH = os.getenv("MEMORY_PATH") or "memory_bank"
    
    # Сколько попыток «рерайтер → редактор → ревизор» делать до публикации
    MAX_REWRITE_ATTEMPTS = _env_int("MAX_REWRITE_ATTEMPTS", 5)
    
    # Сколько источников парсить одновременно
    PARSER_CONCURRENCY = _env_int("PARSER_CONCURRENCY", 8)
    
    # Ручной запуск цикла из Telegram: пауза между запусками и запрет параллельных циклов
    POST_COOLDOWN = _env_int("POST_COOLDOWN", 600)  # 10 минут
    
    # Алгоритм учится по оценке, поэтому задаёт стиль на основе того, что заходило
    USE_LEARNED_BEST_PRACTICES = _env_bool("USE_LEARNED_BEST_PRACTICES", True)
    
    # Страховка публикации: генерация картинки может занять минуты, а пост уже
    # написан. Если до конца отведённого времени осталось меньше резерва,
    # публикуем без картинки, чтобы не потерять пост.
    PUBLISH_TIME_RESERVE = _env_int("PUBLISH_TIME_RESERVE", 120)  # 2 минуты
    
    # Куда складывать сгенерированные картинки. Подписанные ссылки CDN у OdiRouter
    # живут ~7 суток, поэтому файл забираем сразу и публикуем уже локальную копию.
    IMAGE_DIR = os.getenv("IMAGE_DIR", os.path.join("memory_bank", "images"))
    
    # Базовый URL OdiRouter и модели генерации картинок.
    # Порядок = приоритет: сначала kling-v1-image, при его падении — kling-v2-1-image.
    # OdiRouter иногда перестаёт обслуживать конкретную модель (задача завершается
    # с error вместо картинки), поэтому перебор даёт запасной путь.
    ODIROUTER_BASE_URL = os.getenv("ODIROUTER_BASE_URL", "https://api.odirouter.ai/model")
    IMAGE_MODELS = [
        m.strip() for m in os.getenv(
            "IMAGE_MODELS", "kling-v1-image,kling-v2-1-image"
        ).split(",") if m.strip()
    ]
    
    # Переменные, без которых бот не может работать вообще.
    # ADMIN_ID сюда не входит: без него бот запустится, но команды будут открыты всем.
    REQUIRED_FIELDS = (
        "TELEGRAM_TOKEN",
        "CHANNEL_ID",
        "DEEPSEEK_API_KEY",
        "ODIROUTER_API_KEY",
    )
    
    @classmethod
    def validate(cls) -> list:
        """
        Проверяет обязательные переменные окружения.
        Возвращает список замечаний (не критичных), бросает ValueError на критичных.
        """
        problems = [
            name for name in cls.REQUIRED_FIELDS if not getattr(cls, name, None)
        ]
        if problems:
            raise ValueError(
                "❌ Не заданы обязательные переменные окружения: "
                + ", ".join(problems)
                + "\n   Задайте их в переменных окружения хостинга (или в .env при локальном запуске)."
            )
        
        warnings = []
        
        # ADMIN_ID нужен для доступа к командам: без него они открыты всем
        if not cls.ADMIN_ID:
            warnings.append(
                "ADMIN_ID не задан — команды управления (/post, /analytics, /admin) "
                "доступны любому пользователю бота"
            )
        
        if not cls.NEWSAPI_KEY:
            warnings.append("NEWSAPI_KEY не задан — новости берутся только из RSS")
        
        if cls.MIN_POST_LENGTH >= cls.MAX_POST_LENGTH:
            warnings.append(
                f"MIN_POST_LENGTH ({cls.MIN_POST_LENGTH}) >= MAX_POST_LENGTH "
                f"({cls.MAX_POST_LENGTH}) — ревизор будет отклонять все посты"
            )
        
        if cls.MAX_REWRITE_ATTEMPTS < 1:
            warnings.append("MAX_REWRITE_ATTEMPTS < 1 — пост не будет написан ни разу")
        
        if cls.PARSER_CONCURRENCY < 1:
            warnings.append("PARSER_CONCURRENCY < 1 — RSS-ленты не будут парситься")
        
        if cls.POST_INTERVAL < 300:
            warnings.append(
                f"POST_INTERVAL={cls.POST_INTERVAL} c — слишком частые публикации, "
                "канал быстро заспамится"
            )
        
        return warnings