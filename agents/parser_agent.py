# agents/parser_agent.py

"""
АГЕНТ-ПАРСЕР
Собирает новости из RSS-лент и NewsAPI.
Проверяет дубликаты через БД.
Сохраняет список в context["news_list"].
"""

import feedparser
import requests
import random
from datetime import datetime
from .base_agent import BaseAgent
from config import Config
from database import is_post_exists


class ParserAgent(BaseAgent):
    """Агент для сбора новостей из RSS и NewsAPI"""
    
    def __init__(self):
        super().__init__("Парсер новостей")
    
    async def execute(self, context: dict) -> dict:
        """
        Основной метод:
        1. Парсит все RSS-ленты из Config.RSS_FEEDS
        2. Парсит NewsAPI (если есть ключ)
        3. Проверяет дубликаты по ссылкам
        4. Сохраняет список в context["news_list"]
        """
        self.log("🔍 Начинаю парсинг новостей...")
        all_news = []
        
        # === 1. ПАРСИМ RSS ===
        for source in Config.RSS_FEEDS:
            try:
                feed = feedparser.parse(source)
                count = 0
                for entry in feed.entries[:5]:  # Берём по 5 новостей из каждого источника
                    link = entry.link
                    
                    # Проверяем, не публиковали ли уже эту новость
                    if is_post_exists(link):
                        continue
                    
                    # Извлекаем краткое описание
                    summary = ""
                    if hasattr(entry, 'summary') and entry.summary:
                        summary = entry.summary[:800]
                    elif hasattr(entry, 'description') and entry.description:
                        summary = entry.description[:800]
                    
                    all_news.append({
                        "title": entry.title,
                        "link": link,
                        "summary": summary,
                        "source": source.split('/')[2],  # Имя источника
                        "has_description": len(summary) > 100
                    })
                    count += 1
                
                if count > 0:
                    self.log(f"  ✅ {source.split('/')[2]}: {count} новостей")
                    
            except Exception as e:
                self.log(f"  ❌ Ошибка {source}: {e}", is_error=True)
        
        # === 2. ПАРСИМ NEWSAPI ===
        if Config.NEWSAPI_KEY:
            newsapi_news = self._fetch_newsapi()
            all_news.extend(newsapi_news)
            self.log(f"  ✅ NewsAPI: {len(newsapi_news)} новостей")
        
        # Перемешиваем и ограничиваем до 15 новостей
        random.shuffle(all_news)
        context["news_list"] = all_news[:15]
        
        self.log(f"📊 Всего собрано: {len(context['news_list'])} новостей")
        
        # Сохраняем статистику в Memory Bank
        self.memory.set("last_parsed_count", len(context["news_list"]))
        self.memory.set("last_parsed_at", datetime.now().isoformat())
        
        return context
    
    def _fetch_newsapi(self) -> list:
        """
        Парсит новости из NewsAPI.
        Возвращает список новостей в том же формате.
        """
        if not Config.NEWSAPI_KEY:
            return []
        
        news_list = []
        categories = ['technology', 'science', 'business']
        
        for category in categories:
            try:
                url = "https://newsapi.org/v2/top-headlines"
                params = {
                    'category': category,
                    'language': 'en',
                    'pageSize': 5,
                    'apiKey': Config.NEWSAPI_KEY
                }
                
                response = requests.get(url, params=params, timeout=15)
                response.raise_for_status()
                data = response.json()
                
                if data.get('status') == 'ok':
                    for article in data.get('articles', []):
                        # Пропускаем статьи без описания
                        if not article.get('description') or len(article['description']) < 50:
                            continue
                        
                        # Проверяем дубликаты
                        if is_post_exists(article.get('url', '')):
                            continue
                        
                        news_list.append({
                            "title": article.get('title', ''),
                            "link": article.get('url', ''),
                            "summary": article.get('description', '')[:800],
                            "source": f"NewsAPI/{category}",
                            "has_description": True
                        })
                        
            except Exception as e:
                self.log(f"  ⚠️ Ошибка NewsAPI ({category}): {e}", is_error=True)
        
        return news_list