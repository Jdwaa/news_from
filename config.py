# config.py

import os
from dotenv import load_dotenv
load_dotenv()

class Config:
    # Telegram
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
    CHANNEL_ID = os.getenv("CHANNEL_ID")
    ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
    
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
    
    # Настройки
    POST_INTERVAL = 10800  # 3 часа
    AUTO_PUBLISH_DELAY = 600  # 10 минут
    MAX_POST_LENGTH = 950
    MIN_POST_LENGTH = 300
    DB_PATH = "news_bot.db"
    
    @classmethod
    def validate(cls):
        required = ["TELEGRAM_TOKEN", "CHANNEL_ID", "ADMIN_ID", "DEEPSEEK_API_KEY", "ODIROUTER_API_KEY"]
        for key in required:
            if not getattr(cls, key):
                raise ValueError(f"❌ {key} не задан в .env!")