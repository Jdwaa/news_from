import os
import time
import random
import feedparser
import requests
import json
import sqlite3
import traceback
import re
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from bs4 import BeautifulSoup
from dotenv import load_dotenv
load_dotenv()

# ==========================================
# 1. КЛЮЧИ (из переменных окружения)
# ==========================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
ODIROUTER_API_KEY = os.getenv("ODIROUTER_API_KEY")
NEWSAPI_KEY = os.getenv("NEWSAPI_KEY")

# Проверка обязательных переменных
if not TELEGRAM_TOKEN:
    raise ValueError("❌ TELEGRAM_TOKEN не задан!")
if not CHANNEL_ID:
    raise ValueError("❌ CHANNEL_ID не задан!")
if not ADMIN_ID:
    raise ValueError("❌ ADMIN_ID не задан!")
if not DEEPSEEK_API_KEY:
    raise ValueError("❌ DEEPSEEK_API_KEY не задан!")
if not ODIROUTER_API_KEY:
    raise ValueError("❌ ODIROUTER_API_KEY не задан!")

# ==========================================
# 2. ИСТОЧНИКИ НОВОСТЕЙ (RSS)
# ==========================================
RSS_FEEDS = [
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
]

# ==========================================
# 3. БАЗА ДАННЫХ
# ==========================================
DB_PATH = "news_bot.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        link TEXT UNIQUE,
        content TEXT,
        image_prompt TEXT,
        image_url TEXT,
        published_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        status TEXT DEFAULT 'pending'
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message TEXT,
        log_type TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    conn.close()
    print("✅ База данных инициализирована")

def save_post(title, link, content, image_prompt=None, image_url=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO posts (title, link, content, image_prompt, image_url, status) VALUES (?, ?, ?, ?, ?, 'pending')",
                  (title, link, content, image_prompt, image_url))
        conn.commit()
        conn.close()
        print(f"✅ Пост сохранён: {title[:40]}...")
        return True
    except sqlite3.IntegrityError:
        conn.close()
        print(f"⚠️ Дубликат ссылки, пост пропущен: {link}")
        return False

def update_post_image(post_id, image_url):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE posts SET image_url = ? WHERE id = ?", (image_url, post_id))
    conn.commit()
    conn.close()

def update_post_status(post_id, status):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE posts SET status = ? WHERE id = ?", (status, post_id))
    conn.commit()
    conn.close()

def get_pending_post():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, title, content, image_prompt, image_url FROM posts WHERE status = 'pending' ORDER BY id DESC LIMIT 1")
    result = c.fetchone()
    conn.close()
    return result

def get_last_post():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, title, content, image_prompt, image_url FROM posts ORDER BY id DESC LIMIT 1")
    result = c.fetchone()
    conn.close()
    return result

def get_post_by_id(post_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, title, content, image_prompt, image_url, status FROM posts WHERE id = ?", (post_id,))
    result = c.fetchone()
    conn.close()
    return result

def is_post_exists(link, title=None):
    """Проверяет, существует ли пост с такой ссылкой или похожим заголовком"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Проверяем по ссылке (точное совпадение)
    c.execute("SELECT id FROM posts WHERE link = ?", (link,))
    result = c.fetchone()
    
    if not result and title:
        # Проверяем по заголовку (первые 5 слов)
        title_keywords = ' '.join(title.split()[:5])
        c.execute("SELECT id FROM posts WHERE title LIKE ?", (f'%{title_keywords}%',))
        result = c.fetchone()
    
    conn.close()
    return result is not None

def clean_old_posts(days=7):
    """Удаляет посты старше указанного количества дней"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM posts WHERE published_at < datetime('now', '-' || ? || ' days')", (days,))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    print(f"🧹 Удалено старых постов: {deleted}")
    return deleted

def save_log_to_db(message, log_type="info"):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO logs (message, log_type) VALUES (?, ?)", (message, log_type))
    conn.commit()
    conn.close()

def get_placeholder_image():
    return "https://via.placeholder.com/1280x720/1a1a2e/ffffff?text=AI+News"

# ==========================================
# 4. ПАРСИНГ ПОЛНОЙ СТАТЬИ (BeautifulSoup)
# ==========================================
def fetch_full_article(url):
    """Парсит полный текст статьи по ссылке с помощью BeautifulSoup"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Удаляем скрипты и стили
        for script in soup(["script", "style"]):
            script.decompose()
        
        # Ищем основной контент
        article_content = ""
        
        # Пробуем найти статью по разным селекторам
        selectors = [
            'article',
            '.article-content',
            '.post-content',
            '.entry-content',
            '.content',
            'main',
            '.story-content',
            '.blog-post-content'
        ]
        
        for selector in selectors:
            elements = soup.select(selector)
            if elements:
                for element in elements:
                    text = element.get_text(separator=' ', strip=True)
                    if len(text) > 500:
                        article_content = text
                        break
                if article_content:
                    break
        
        # Если не нашли по селекторам - берём всё тело
        if not article_content:
            body = soup.find('body')
            if body:
                article_content = body.get_text(separator=' ', strip=True)
        
        # Очищаем текст
        article_content = re.sub(r'\s+', ' ', article_content)
        article_content = re.sub(r'\n+', '\n', article_content)
        
        # Ограничиваем длину до 1500 символов (чтобы DeepSeek не пересказывал)
        if len(article_content) > 1500:
            article_content = article_content[:1500]
        
        return article_content.strip()
        
    except Exception as e:
        print(f"⚠️ Ошибка парсинга статьи: {e}")
        return None

# ==========================================
# 5. ЛОГГЕР
# ==========================================
async def send_log(message, context=None, is_error=False, send_to_admin=True):
    timestamp = datetime.now().strftime("%H:%M:%S")
    log_line = f"[{timestamp}] {'❌' if is_error else '📌'} {message}"
    print(log_line)
    save_log_to_db(message, "error" if is_error else "info")
    
    if send_to_admin and context and ADMIN_ID:
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"{'❌ ОШИБКА' if is_error else '📡 ЛОГ'}:\n{message}\n\n🕐 {timestamp}"
            )
        except Exception as e:
            print(f"⚠️ Не удалось отправить лог в Telegram: {e}")

# ==========================================
# 6. ПАРСИНГ NEWSAPI
# ==========================================
def fetch_newsapi_news():
    """Парсит новости из NewsAPI"""
    if not NEWSAPI_KEY:
        print("⚠️ NEWSAPI_KEY не задан, пропускаем NewsAPI")
        return []
    
    news_list = []
    
    # Категории для поиска
    categories = ['technology', 'science', 'business']
    
    for category in categories:
        try:
            url = "https://newsapi.org/v2/top-headlines"
            params = {
                'category': category,
                'language': 'en',
                'pageSize': 5,
                'apiKey': NEWSAPI_KEY
            }
            
            response = requests.get(url, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            
            if data.get('status') == 'ok':
                for article in data.get('articles', []):
                    # Пропускаем статьи без описания
                    if not article.get('description') or len(article['description']) < 50:
                        continue
                    
                    # Проверяем на дубликаты (по ссылке и по заголовку)
                    if is_post_exists(article.get('url', ''), article.get('title', '')):
                        continue
                    
                    news_list.append({
                        "title": article.get('title', ''),
                        "link": article.get('url', ''),
                        "summary": article.get('description', '')[:800],
                        "source": f"NewsAPI/{category}",
                        "has_description": True
                    })
                
                print(f"  ✅ NewsAPI ({category}): {len(data.get('articles', []))} новостей")
                
        except Exception as e:
            print(f"⚠️ Ошибка парсинга NewsAPI ({category}): {e}")
    
    return news_list

# ==========================================
# 7. ПАРСИНГ НОВОСТЕЙ (RSS + NewsAPI)
# ==========================================
async def fetch_news(context):
    await send_log("🔍 Начинаю парсинг новостей...", context)
    all_news = []
    
    # ========== ПАРСИМ NEWSAPI ==========
    if NEWSAPI_KEY:
        newsapi_news = fetch_newsapi_news()
        if newsapi_news:
            all_news.extend(newsapi_news)
            await send_log(f"  ✅ NewsAPI: {len(newsapi_news)} новостей", context)
    else:
        await send_log("  ⚠️ NewsAPI ключ не задан, пропускаем", context)
    
    # ========== ПАРСИМ RSS ==========
    for source in RSS_FEEDS:
        try:
            feed = feedparser.parse(source)
            count = 0
            for entry in feed.entries[:5]:
                link = entry.link
                
                # Проверяем на дубликаты (по ссылке и по заголовку)
                if is_post_exists(link, entry.title):
                    continue
                
                summary = ""
                if hasattr(entry, 'summary') and entry.summary:
                    summary = entry.summary[:800]
                elif hasattr(entry, 'description') and entry.description:
                    summary = entry.description[:800]
                elif hasattr(entry, 'content') and entry.content:
                    if isinstance(entry.content, list):
                        summary = entry.content[0].value[:800] if entry.content else ""
                    else:
                        summary = entry.content[:800]
                
                if not summary or len(summary) < 30:
                    summary = ""
                
                all_news.append({
                    "title": entry.title,
                    "link": link,
                    "summary": summary,
                    "source": source.split('/')[2],
                    "has_description": len(summary) > 100
                })
                count += 1
            if count > 0:
                await send_log(f"  ✅ {source.split('/')[2]}: {count} новостей", context)
        except Exception as e:
            error_msg = f"❌ Ошибка парсинга {source}: {e}"
            await send_log(error_msg, context, is_error=True)
    
    random.shuffle(all_news)
    await send_log(f"📊 Всего собрано уникальных новостей: {len(all_news)}", context)
    return all_news[:15]

# ==========================================
# 8. ВЫБОР ЛУЧШЕЙ НОВОСТИ
# ==========================================
async def select_best_news(news_list, context):
    if not news_list:
        await send_log("❌ Нет новостей для выбора", context, is_error=True)
        return None
    
    await send_log(f"📤 Отправляю список из {len(news_list)} новостей в DeepSeek для выбора...", context)
    
    news_text = "\n\n".join([
        f"Новость {i+1}:\nЗаголовок: {n['title']}\nОписание: {n['summary'][:200] if n['summary'] else 'Нет описания'}"
        for i, n in enumerate(news_list)
    ])
    
    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    
    system_prompt = """Ты — редактор технологического канала. Из списка новостей выбери одну самую интересную, полезную или необычную.

    Критерии выбора:
    1. Новость должна быть полезной для обычного человека (лайфхак, инструмент, идея)
    2. Или содержать необычное применение AI
    3. Или быть важной для рынка AI

    Ответ строго в формате:
    НОМЕР: [номер новости из списка]
    ОБОСНОВАНИЕ: [почему выбрал эту новость, 2-3 предложения]"""

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Список новостей:\n{news_text}"}
        ]
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        result = response.json()
        content = result["choices"][0]["message"]["content"]
        
        match = re.search(r"НОМЕР:\s*(\d+)", content)
        if not match:
            selected_index = 0
            justification = "Не удалось определить номер, выбрана первая новость"
        else:
            selected_index = int(match.group(1)) - 1
            justification_match = re.search(r"ОБОСНОВАНИЕ:\s*(.+)", content, re.DOTALL)
            justification = justification_match.group(1).strip() if justification_match else "Новость выбрана как самая интересная"
        
        if selected_index >= len(news_list) or selected_index < 0:
            selected_index = 0
        
        selected = news_list[selected_index]
        selected["justification"] = justification
        
        await send_log(f"✅ DeepSeek выбрал новость: {selected['title'][:60]}...", context)
        await send_log(f"📊 Обоснование: {justification[:100]}...", context)
        
        return selected
        
    except Exception as e:
        error_msg = f"❌ Ошибка выбора новости: {e}"
        await send_log(error_msg, context, is_error=True)
        news_list[0]["justification"] = "Выбрана как первая доступная"
        return news_list[0]

# ==========================================
# 9. РЕРАЙТ ПОСТА + ГЕНЕРАЦИЯ ПРОМПТА ДЛЯ КАРТИНКИ
# ==========================================
async def rewrite_post(news, context):
    await send_log(f"✍️ Создание авторского поста...", context)
    
    if not news.get("summary") or len(news["summary"]) < 50:
        await send_log(f"⚠️ Новость без описания, пропускаем: {news['title'][:60]}...", context, is_error=True)
        return None, None, None
    
    # ========== ПАРСИМ ПОЛНУЮ СТАТЬЮ ==========
    full_content = None
    if news.get("link"):
        await send_log(f"📄 Парсинг полной статьи...", context)
        full_content = fetch_full_article(news['link'])
        if full_content:
            await send_log(f"  ✅ Спарсено {len(full_content)} символов", context)
    
    # Если статья не спарсилась - используем summary
    content_for_ai = full_content if full_content else news['summary'][:500]
    
    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    
    system_prompt = """Ты — автор экспертного Telegram-канала о технологиях, AI и инновациях.

ТЫ ЖИВОЙ ЧЕЛОВЕК:
- У тебя есть своё мнение, стиль и голос
- Ты пишешь как друг, который разбирается в теме
- Ты используешь эмодзи, вопросы к читателю, живые формулировки
- Ты НЕ пишешь как новостной агрегатор

ТВОЯ ЗАДАЧА:
1. Прочитай статью и ПОЛНОСТЬЮ ПЕРЕРАБОТАЙ её в авторский пост
2. НЕ ОСТАВЛЯЙ следов оригинала: переформулируй всё своими словами
3. Добавь своё мнение: "мне кажется", "я вижу в этом", "обратите внимание"
4. Сделай прогноз: "что будет дальше", "как это повлияет"
5. Задавай вопросы читателю: "представьте", "а вы замечали"

ЖЁСТКИЕ ТРЕБОВАНИЯ К ДЛИНЕ:
- Пост ДОЛЖЕН БЫТЬ минимум 600 символов (это обязательно!)
- Если после написания пост короче 600 символов — ПЕРЕПИШИ ЕГО снова
- Добавь больше анализа, контекста, примеров, прогнозов
- Если не хватает информации — ДОПОЛНИ своими знаниями

СТРУКТУРА ПОСТА (600-1000 символов):
1. ЗАГОЛОВОК: яркий, интригующий, с эмодзи (до 10 слов)
2. ВСТУПЛЕНИЕ: вопрос или неожиданный факт (1-2 предложения)
3. ОСНОВНАЯ ЧАСТЬ: твой анализ, мнение, контекст, прогнозы (3-5 абзацев)
4. ВЫВОД: чёткая мысль или призыв к действию (1-2 предложения)
5. ХЕШТЕГИ: 2-3 шт
6. ПРОМПТ_ДЛЯ_КАРТИНКИ: 30-50 слов на английском

ВАЖНО! Если пост получился короче 600 символов — перепиши его СНОВА, добавив больше анализа и контента.

ФОРМАТ ОТВЕТА:
ЗАГОЛОВОК: [текст]
ВСТУПЛЕНИЕ: [текст]
ОСНОВНОЙ ТЕКСТ: [текст]
ВЫВОД: [текст]
ХЕШТЕГИ: [текст]
ПРОМПТ_ДЛЯ_КАРТИНКИ: [текст]"""

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Новость из {news['source']}:\nЗаголовок: {news['title']}\nТекст: {content_for_ai}"}
        ],
        "temperature": 0.7,
        "top_p": 0.9,
        "max_tokens": 2000
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        result = response.json()
        content = result["choices"][0]["message"]["content"]
        
        # Парсим ответ
        post_data = {
            "title": "",
            "intro": "",
            "main_text": "",
            "conclusion": "",
            "hashtags": "",
            "image_prompt": ""
        }
        
        for line in content.split('\n'):
            line = line.strip()
            if line.startswith("ЗАГОЛОВОК:"):
                post_data["title"] = line.replace("ЗАГОЛОВОК:", "").strip()
            elif line.startswith("ВСТУПЛЕНИЕ:"):
                post_data["intro"] = line.replace("ВСТУПЛЕНИЕ:", "").strip()
            elif line.startswith("ОСНОВНОЙ ТЕКСТ:"):
                post_data["main_text"] = line.replace("ОСНОВНОЙ ТЕКСТ:", "").strip()
            elif line.startswith("ВЫВОД:"):
                post_data["conclusion"] = line.replace("ВЫВОД:", "").strip()
            elif line.startswith("ХЕШТЕГИ:"):
                post_data["hashtags"] = line.replace("ХЕШТЕГИ:", "").strip()
            elif line.startswith("ПРОМПТ_ДЛЯ_КАРТИНКИ:"):
                post_data["image_prompt"] = line.replace("ПРОМПТ_ДЛЯ_КАРТИНКИ:", "").strip()
        
        # Собираем пост
        full_post = f"{post_data['title']}\n\n"
        if post_data["intro"]:
            full_post += f"{post_data['intro']}\n\n"
        if post_data["main_text"]:
            full_post += f"{post_data['main_text']}\n\n"
        if post_data["conclusion"]:
            full_post += f"📌 {post_data['conclusion']}\n\n"
        if post_data["hashtags"]:
            full_post += f"{post_data['hashtags']}"
        
        # Проверяем длину поста
        post_length = len(full_post.strip())
        await send_log(f"📏 Длина поста: {post_length} символов", context)
        
        # Если пост короче 600 символов — отправляем второй запрос на расширение
        if post_length < 600:
            await send_log(f"⚠️ Пост слишком короткий ({post_length} символов), отправляю запрос на расширение...", context)
            
            expand_prompt = f"""Этот пост получился слишком коротким ({post_length} символов). Нужно минимум 600 символов.

    Расширь этот пост: добавь больше анализа, контекста, прогнозов, примеров, личного мнения. Если нужно — дополни своими знаниями.

    Текущий пост:
    {full_post}

    Напиши расширенную версию этого же поста (минимум 600 символов). Сохрани структуру и стиль."""

            payload_expand = {
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": "Ты — автор Telegram-канала. Расширь пост до минимум 600 символов. Добавь анализ, контекст, прогнозы, личное мнение. Сохрани стиль."},
                    {"role": "user", "content": expand_prompt}
                ],
                "temperature": 0.7,
                "max_tokens": 2000
            }
            
            try:
                response_expand = requests.post(url, headers=headers, json=payload_expand, timeout=60)
                response_expand.raise_for_status()
                result_expand = response_expand.json()
                expanded_content = result_expand["choices"][0]["message"]["content"].strip()
                
                # Парсим расширенный ответ
                expanded_data = {
                    "title": "",
                    "intro": "",
                    "main_text": "",
                    "conclusion": "",
                    "hashtags": "",
                    "image_prompt": ""
                }
                
                for line in expanded_content.split('\n'):
                    line = line.strip()
                    if line.startswith("ЗАГОЛОВОК:"):
                        expanded_data["title"] = line.replace("ЗАГОЛОВОК:", "").strip()
                    elif line.startswith("ВСТУПЛЕНИЕ:"):
                        expanded_data["intro"] = line.replace("ВСТУПЛЕНИЕ:", "").strip()
                    elif line.startswith("ОСНОВНОЙ ТЕКСТ:"):
                        expanded_data["main_text"] = line.replace("ОСНОВНОЙ ТЕКСТ:", "").strip()
                    elif line.startswith("ВЫВОД:"):
                        expanded_data["conclusion"] = line.replace("ВЫВОД:", "").strip()
                    elif line.startswith("ХЕШТЕГИ:"):
                        expanded_data["hashtags"] = line.replace("ХЕШТЕГИ:", "").strip()
                    elif line.startswith("ПРОМПТ_ДЛЯ_КАРТИНКИ:"):
                        expanded_data["image_prompt"] = line.replace("ПРОМПТ_ДЛЯ_КАРТИНКИ:", "").strip()
                
                # Если не удалось распарсить расширенный ответ - используем старый
                if expanded_data["main_text"]:
                    post_data = expanded_data
                    full_post = f"{post_data['title']}\n\n"
                    if post_data["intro"]:
                        full_post += f"{post_data['intro']}\n\n"
                    if post_data["main_text"]:
                        full_post += f"{post_data['main_text']}\n\n"
                    if post_data["conclusion"]:
                        full_post += f"📌 {post_data['conclusion']}\n\n"
                    if post_data["hashtags"]:
                        full_post += f"{post_data['hashtags']}"
                    
                    await send_log(f"✅ Пост расширен до {len(full_post.strip())} символов", context)
                else:
                    # Если не удалось распарсить - добавляем свой анализ
                    post_data["main_text"] += "\n\nМне кажется, эта тема очень важна для понимания того, куда движется технологический мир. Мы видим, как меняются подходы и открываются новые возможности. Важно быть в курсе, чтобы не отставать."
                    full_post = f"{post_data['title']}\n\n"
                    if post_data["intro"]:
                        full_post += f"{post_data['intro']}\n\n"
                    if post_data["main_text"]:
                        full_post += f"{post_data['main_text']}\n\n"
                    if post_data["conclusion"]:
                        full_post += f"📌 {post_data['conclusion']}\n\n"
                    if post_data["hashtags"]:
                        full_post += f"{post_data['hashtags']}"
                    await send_log(f"➕ Добавлен анализ, длина: {len(full_post.strip())} символов", context)
                    
            except Exception as e:
                await send_log(f"⚠️ Ошибка расширения поста: {e}", context, is_error=True)
                # Добавляем свой анализ к короткому посту
                post_data["main_text"] += "\n\nМне кажется, эта тема очень важна для понимания того, куда движется технологический мир. Мы видим, как меняются подходы и открываются новые возможности. Важно быть в курсе, чтобы не отставать."
                full_post = f"{post_data['title']}\n\n"
                if post_data["intro"]:
                    full_post += f"{post_data['intro']}\n\n"
                if post_data["main_text"]:
                    full_post += f"{post_data['main_text']}\n\n"
                if post_data["conclusion"]:
                    full_post += f"📌 {post_data['conclusion']}\n\n"
                if post_data["hashtags"]:
                    full_post += f"{post_data['hashtags']}"
        
        if not post_data["title"]:
            post_data["title"] = f"🤖 {news['title'][:50]}"
        if not post_data["image_prompt"]:
            post_data["image_prompt"] = f"Futuristic technology scene, digital innovation, cinematic lighting, 4K, high detail"
        if not post_data["hashtags"]:
            post_data["hashtags"] = "#AI #Tech #Innovation"
        
        if len(full_post.strip()) < 300:
            await send_log(f"⚠️ Пост слишком короткий ({len(full_post)} символов), пропускаем", context, is_error=True)
            return None, None, None
        
        await send_log(f"✅ Пост готов ({len(full_post)} символов)", context)
        await send_log(f"🎨 Промпт для картинки: {post_data['image_prompt'][:80]}...", context)
        
        return full_post, post_data["title"], post_data["image_prompt"]
        
    except Exception as e:
        error_msg = f"❌ Ошибка рерайта: {e}"
        await send_log(error_msg, context, is_error=True)
        return None, None, None

# ==========================================
# 10. ГЕНЕРАЦИЯ КАРТИНКИ (free-nano-banana-2) — 60 попыток
# ==========================================
async def generate_image_odirouter(prompt, context):
    """Генерирует картинку через free-nano-banana-2 (бесплатно)"""
    await send_log(f"🎨 Генерация картинки...", context)
    
    url = "https://api.odirouter.ai/model/v1/queue/free-nano-banana-2"
    headers = {
        "Authorization": f"Bearer {ODIROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    
    if len(prompt) > 500:
        prompt = prompt[:500]
    
    payload = {
        "prompt": prompt,
        "aspect_ratio": "16:9",
        "resolution": "1K"
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        task_data = response.json()
        request_id = task_data.get("request_id")
        
        if not request_id:
            await send_log("  ❌ Не удалось получить request_id", context, is_error=True)
            return None
        
        await send_log(f"  ✅ Задача создана: {request_id}", context)
        
        status_url = f"https://api.odirouter.ai/model/v1/queue/free-nano-banana-2/requests/{request_id}/status"
        attempts = 0
        max_attempts = 60
        while attempts < max_attempts:
            status_response = requests.get(status_url, headers=headers)
            status_data = status_response.json()
            current_status = status_data.get("status")
            attempts += 1
            await send_log(f"  ⏳ Статус: {current_status} (попытка {attempts}/{max_attempts})", context)
            
            if current_status == "COMPLETED":
                await send_log("  ✅ Картинка сгенерирована", context)
                break
            elif current_status in ["FAILED", "CANCELED"]:
                await send_log(f"  ❌ Ошибка генерации", context, is_error=True)
                return None
            time.sleep(3)
        else:
            await send_log("  ⏰ Таймаут генерации (3 минуты)", context, is_error=True)
            return None
        
        result_url = f"https://api.odirouter.ai/model/v1/queue/free-nano-banana-2/requests/{request_id}/response"
        result_response = requests.get(result_url, headers=headers)
        result_data = result_response.json()
        
        for item in result_data.get("output", []):
            for content in item.get("content", []):
                if content.get("type") == "image" and "url" in content:
                    return content["url"]
        return None
        
    except Exception as e:
        error_msg = f"❌ Ошибка генерации картинки: {e}"
        await send_log(error_msg, context, is_error=True)
        return None

# ==========================================
# 11. МОДЕРАЦИЯ + АВТОПУБЛИКАЦИЯ ЧЕРЕЗ 1 ЧАС
# ==========================================
async def send_for_moderation(context, news, post_text, title, image_prompt, justification):
    saved = save_post(title, news['link'], post_text, image_prompt)
    
    if not saved:
        await send_log(f"⚠️ Пост не сохранён (дубликат): {news['link']}", context, is_error=True)
        return
    
    # Получаем ID сохранённого поста
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id FROM posts WHERE link = ? ORDER BY id DESC LIMIT 1", (news['link'],))
    result = c.fetchone()
    conn.close()
    
    if not result:
        await send_log("❌ Не удалось получить ID поста", context, is_error=True)
        return
    
    post_id = result[0]
    
    message_text = f"""
📨 **НОВОСТЬ НА МОДЕРАЦИЮ**

📰 **Заголовок:**
{title}

📝 **Текст поста:**
{post_text[:1000]}{"..." if len(post_text) > 1000 else ""}

🎨 **Промпт для картинки:**
{image_prompt}

🔗 **Источник:** {news['source']}

📊 **Обоснование выбора:**
{justification}

⏳ **Автопубликация через 1 час, если не ответить**

---
✅ Опубликовать
❌ Отклонить
🔄 Следующая новость
"""
    
    keyboard = [
        [
            InlineKeyboardButton("✅ Опубликовать", callback_data=f"publish_{post_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{post_id}")
        ],
        [InlineKeyboardButton("🔄 Следующая новость", callback_data="next")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await context.bot.send_message(chat_id=ADMIN_ID, text=message_text, reply_markup=reply_markup)
    
    # Запускаем таймер автопубликации через 1 час
    asyncio.create_task(auto_publish_after_timeout(context, post_id, title, post_text))

async def auto_publish_after_timeout(context, post_id, title, post_text):
    """Автопубликация через 1 час, если пост всё ещё pending"""
    await asyncio.sleep(3600)  # 1 час
    
    # Проверяем статус поста
    post = get_post_by_id(post_id)
    if not post:
        return
    
    if post[5] == 'pending':  # status = pending
        await send_log(f"⏰ Автопубликация поста #{post_id} (таймаут 1 час)", context)
        update_post_status(post_id, 'published')
        await publish_post(context, title, post_text)

# ==========================================
# 12. ПУБЛИКАЦИЯ С КАРТИНКОЙ
# ==========================================
async def publish_post(context, title, post_text):
    await send_log(f"📤 Публикация поста в канал...", context)
    
    post = get_pending_post()
    if not post:
        post = get_last_post()
        if not post:
            await send_log("❌ Нет поста для публикации", context, is_error=True)
            return
    
    post_id, db_title, db_content, image_prompt, image_url = post
    
    if not image_url:
        if image_prompt:
            await send_log(f"🎨 Генерация картинки по промпту...", context)
            image_url = await generate_image_odirouter(image_prompt, context)
        else:
            image_url = get_placeholder_image()
            await send_log("🔄 Промпта нет, использую заглушку", context)
        
        if image_url:
            update_post_image(post_id, image_url)
        else:
            image_url = get_placeholder_image()
            await send_log("🔄 Использую картинку-заглушку", context)
    
    try:
        if image_url and image_url != get_placeholder_image():
            try:
                response = requests.get(image_url, timeout=30)
                if response.status_code == 200:
                    image_path = "temp_publish.jpg"
                    with open(image_path, "wb") as f:
                        f.write(response.content)
                    with open(image_path, "rb") as f:
                        await context.bot.send_photo(chat_id=CHANNEL_ID, photo=f, caption=post_text)
                    os.remove(image_path)
                    await send_log(f"✅ Пост опубликован с картинкой", context)
                    return
            except Exception as e:
                await send_log(f"⚠️ Ошибка отправки картинки: {e}", context, is_error=True)
        
        await context.bot.send_message(chat_id=CHANNEL_ID, text=post_text)
        await send_log(f"✅ Пост опубликован без картинки", context)
        
    except Exception as e:
        error_msg = f"❌ Ошибка публикации: {e}"
        await send_log(error_msg, context, is_error=True)

# ==========================================
# 13. ОСНОВНОЙ ЦИКЛ
# ==========================================
async def prepare_and_moderate(context: ContextTypes.DEFAULT_TYPE):
    await send_log("="*50, context)
    await send_log(f"📡 [{datetime.now().strftime('%H:%M')}] ЗАПУСК ЦИКЛА ПОДГОТОВКИ ПОСТА", context)
    await send_log("="*50, context)
    
    try:
        news_list = await fetch_news(context)
        if not news_list:
            await send_log("❌ Новостей нет", context, is_error=True)
            return
        
        for attempt in range(len(news_list)):
            selected = await select_best_news(news_list, context)
            if not selected:
                await send_log("❌ Не удалось выбрать новость", context, is_error=True)
                return
            
            post_text, title, image_prompt = await rewrite_post(selected, context)
            
            if post_text and len(post_text.strip()) >= 300:
                await send_for_moderation(
                    context, 
                    selected, 
                    post_text, 
                    title, 
                    image_prompt,
                    selected.get("justification", "Выбрана как самая интересная")
                )
                return
            
            await send_log(f"🔄 Пост слишком короткий, пробуем следующую...", context)
            news_list.remove(selected)
        
        await send_log("❌ Все новости не подошли", context, is_error=True)
        
    except Exception as e:
        error_trace = traceback.format_exc()
        error_msg = f"❌ КРИТИЧЕСКАЯ ОШИБКА:\n{str(e)}\n\n{error_trace[:500]}"
        await send_log(error_msg, context, is_error=True)

# ==========================================
# 14. ОБРАБОТЧИКИ КНОПОК
# ==========================================
async def moderation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    callback_type = data.split('_')[0]
    
    if callback_type == "publish":
        # Извлекаем post_id
        try:
            post_id = int(data.split('_')[1])
        except:
            post = get_pending_post()
            if not post:
                await query.message.reply_text("❌ Нет поста для публикации.")
                return
            post_id = post[0]
        
        post = get_post_by_id(post_id)
        if not post:
            await query.message.reply_text("❌ Пост не найден.")
            return
        
        post_id, title, content, image_prompt, image_url, status = post
        
        update_post_status(post_id, 'published')
        
        await query.message.reply_text("📤 Публикую пост...")
        await publish_post(context, title, content)
        await query.message.reply_text("✅ Пост опубликован!")
        
    elif callback_type == "reject":
        try:
            post_id = int(data.split('_')[1])
        except:
            post = get_pending_post()
            if not post:
                await query.message.reply_text("❌ Нет поста для отклонения.")
                return
            post_id = post[0]
        
        update_post_status(post_id, 'rejected')
        
        await query.message.reply_text("❌ Пост отклонён.")
        await send_log(f"❌ Пост #{post_id} отклонён админом", context)
        await query.message.reply_text("🔄 Загружаю следующую новость...")
        await prepare_and_moderate(context)
        
    elif callback_type == "next":
        await query.message.reply_text("🔄 Загружаю следующую новость...")
        await prepare_and_moderate(context)

# ==========================================
# 15. КОМАНДЫ TELEGRAM
# ==========================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📰 Пост сейчас", callback_data="post_now")],
        [InlineKeyboardButton("📊 Статус", callback_data="status")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🤖 **AI News Bot**\n\n"
        "Автоматический сбор и публикация AI-новостей с модерацией.\n"
        f"📡 Посты выходят каждые 4 часа\n"
        f"📌 Канал: {CHANNEL_ID}\n\n"
        "📌 Команды:\n"
        "/post — принудительный запуск цикла\n"
        "/status — статистика\n"
        "/start — это меню",
        reply_markup=reply_markup
    )

async def post_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📡 Запускаю цикл подготовки поста...")
    await prepare_and_moderate(context)

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM posts")
    total_posts = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM posts WHERE DATE(published_at) = DATE('now')")
    today_posts = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM posts WHERE status = 'published'")
    published_posts = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM posts WHERE status = 'pending'")
    pending_posts = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM logs WHERE DATE(created_at) = DATE('now') AND log_type = 'error'")
    today_errors = c.fetchone()[0]
    conn.close()
    
    status_text = f"📊 **Статистика**\n\n"
    status_text += f"📰 Всего постов: {total_posts}\n"
    status_text += f"📆 Сегодня: {today_posts}\n"
    status_text += f"✅ Опубликовано: {published_posts}\n"
    status_text += f"⏳ Ожидают модерации: {pending_posts}\n"
    status_text += f"❌ Ошибок сегодня: {today_errors}\n"
    status_text += f"📡 Источников: {len(RSS_FEEDS)}\n"
    status_text += f"⏱️ Интервал: 4 часа\n"
    status_text += f"📌 Канал: {CHANNEL_ID}"
    
    await update.message.reply_text(status_text)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "post_now":
        await query.message.reply_text("📡 Запускаю цикл подготовки поста...")
        await prepare_and_moderate(context)
    elif query.data == "status":
        await status_cmd(update, context)
    else:
        await moderation_callback(update, context)

# ==========================================
# 16. ЗАПУСК
# ==========================================
if __name__ == "__main__":
    print("\n" + "="*60)
    print("🚀 ЗАПУСК БОТА")
    print("="*60)
    
    init_db()
    clean_old_posts(7)  # Удаляем посты старше 7 дней при запуске
    
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("post", post_now))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    job_queue = app.job_queue
    if job_queue:
        job_queue.run_repeating(prepare_and_moderate, interval=14400, first=10)
        print("📡 Автопостинг: включён (интервал 4 часа)")
    
    print("="*60)
    print("✅ Бот запущен")
    print("📌 Логи отправляются в Telegram и консоль")
    print("📌 Модерация — через кнопки в личке")
    print("📌 Посты: минимум 600 символов (автоматическое расширение)")
    print("📌 Картинка генерируется через free-nano-banana-2 (60 попыток)")
    print("📌 Автопубликация через 1 час, если нет ответа")
    print("📌 Старые посты удаляются через 7 дней")
    print("📌 Источники: RSS + NewsAPI + BeautifulSoup парсинг статей")
    print("="*60 + "\n")
    
    app.run_polling()