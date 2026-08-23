import os
import time
import random
import feedparser
import requests
import json
import sqlite3
import traceback
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# ==========================================
# 1. КЛЮЧИ (из переменных окружения)
# ==========================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

# Проверка обязательных переменных
if not TELEGRAM_TOKEN:
    raise ValueError("❌ TELEGRAM_TOKEN не задан!")
if not CHANNEL_ID:
    raise ValueError("❌ CHANNEL_ID не задан!")
if not ADMIN_ID:
    raise ValueError("❌ ADMIN_ID не задан!")
if not DEEPSEEK_API_KEY:
    raise ValueError("❌ DEEPSEEK_API_KEY не задан!")

# ==========================================
# 2. ИСТОЧНИКИ НОВОСТЕЙ
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

def save_post(title, link, content):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO posts (title, link, content, status) VALUES (?, ?, ?, 'pending')",
                  (title, link, content))
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        conn.close()
        return False

def is_post_exists(link):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id FROM posts WHERE link = ?", (link,))
    result = c.fetchone()
    conn.close()
    return result is not None

def save_log_to_db(message, log_type="info"):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO logs (message, log_type) VALUES (?, ?)", (message, log_type))
    conn.commit()
    conn.close()

# ==========================================
# 4. ЛОГГЕР
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
# 5. ПАРСИНГ НОВОСТЕЙ
# ==========================================
async def fetch_news(context):
    await send_log("🔍 Начинаю парсинг новостей...", context)
    all_news = []
    
    for source in RSS_FEEDS:
        try:
            feed = feedparser.parse(source)
            count = 0
            for entry in feed.entries[:5]:
                link = entry.link
                if is_post_exists(link):
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
                    summary = f"Новость: {entry.title}. Подробности неизвестны."
                
                all_news.append({
                    "title": entry.title,
                    "link": link,
                    "summary": summary,
                    "source": source.split('/')[2]
                })
                count += 1
            if count > 0:
                await send_log(f"  ✅ {source.split('/')[2]}: {count} новостей", context)
        except Exception as e:
            error_msg = f"❌ Ошибка парсинга {source}: {e}"
            await send_log(error_msg, context, is_error=True)
    
    random.shuffle(all_news)
    await send_log(f"📊 Всего собрано уникальных новостей: {len(all_news)}", context)
    return all_news[:10]

# ==========================================
# 6. ВЫБОР ЛУЧШЕЙ НОВОСТИ (DeepSeek)
# ==========================================
async def select_best_news(news_list, context):
    if not news_list:
        await send_log("❌ Нет новостей для выбора", context, is_error=True)
        return None
    
    await send_log(f"📤 Отправляю список из {len(news_list)} новостей в DeepSeek для выбора...", context)
    
    news_text = "\n\n".join([
        f"Новость {i+1}:\nЗаголовок: {n['title']}\nОписание: {n['summary'][:200]}..."
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
        
        import re
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
# 7. РЕРАЙТ ПОСТА (DeepSeek)
# ==========================================
async def rewrite_post(news, context):
    await send_log(f"✍️ Глубокий рерайт новости...", context)
    
    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json"
    }
    
    system_prompt = """Ты — автор экспертного Telegram-канала о технологиях, AI и инновациях. Твоя аудитория — умные, занятые люди, которые ценят факты, инсайты и практическую пользу.

    Твоя задача — на основе новости написать пост, который:
    1. Сразу даёт понять суть — что произошло или что появилось
    2. Объясняет, как это устроено (механизм, технология, логика)
    3. Показывает, почему это важно — для рынка, для людей, для будущего
    4. Даёт читателю чёткий вывод: что ему с этим делать или как это может повлиять на его жизнь/работу

    **ВАЖНО:** Если новость содержит только заголовок или очень короткое описание (менее 100 символов) — используй свои знания, чтобы дать контекст, объяснить, что произошло, и почему это важно. Не ограничивайся пересказом заголовка. Раскрывай тему полностью.

    Обязательные требования:
    - Если в новости упоминаются люди, компании или организация — дай краткую справку о них (кто это, чем занимаются, почему важны)
    - Обязательно укажи факты: кто, что, когда, сколько, зачем
    - Не используй кликбейтные заголовки без фактов
    - Не используй имена без контекста (читатель не обязан знать, кто такой Хуан или Илон)

    Правила оформления:
    - Пиши как живой человек, который разбирается в теме. Не используй канцелярит.
    - Используй эмодзи, но не перебарщивай (1–2 в заголовке, 1–2 в тексте)
    - Разбивай текст на короткие абзацы. Не пиши "стену" текста.
    - Используй жирный шрифт для ключевых фактов, цифр, выводов.
    - Если есть цифры, сроки, суммы — выделяй их **жирным**.
    - Добавляй своё мнение, прогноз, контекст — но отделяй факты от предположений.
    - Пост должен быть логичным и законченным. Читатель не должен додумывать.
    - Длина поста — от 600 до 1000 символов.

    Формат ответа:

    ЗАГОЛОВОК: [короткий, до 10 слов, с эмодзи, понятный без контекста]
    ВСТУПЛЕНИЕ: [1–2 предложения, ввод в тему]
    ОСНОВНОЙ ТЕКСТ: [суть, механизм, контекст, прогноз, справка о людях/компаниях]
    ВЫВОД: [1–2 предложения, чёткая мысль]
    ХЕШТЕГИ: [2–3 хештега]

    Важно: Текст должен быть понятен человеку, который читает пост впервые. Не предполагай, что читатель уже знает контекст."""

    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Новость из {news['source']}:\nЗаголовок: {news['title']}\nТекст: {news['summary']}"}
        ],
        "temperature": 0.3,
        "top_p": 0.9,
        "max_tokens": 1500
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=45)
        response.raise_for_status()
        result = response.json()
        content = result["choices"][0]["message"]["content"]
        
        import re
        post_data = {
            "title": "",
            "intro": "",
            "main_text": "",
            "conclusion": "",
            "hashtags": ""
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
        
        if not post_data["title"]:
            post_data["title"] = f"🤖 {news['title'][:50]}"
        
        full_post = f"{post_data['title']}\n\n"
        if post_data["intro"]:
            full_post += f"*{post_data['intro']}*\n\n"
        if post_data["main_text"]:
            full_post += f"{post_data['main_text']}\n\n"
        if post_data["conclusion"]:
            full_post += f"📌 {post_data['conclusion']}\n\n"
        if post_data["hashtags"]:
            full_post += f"{post_data['hashtags']}"
        
        await send_log(f"✅ Рерайт готов", context)
        return full_post, post_data["title"]
        
    except Exception as e:
        error_msg = f"❌ Ошибка рерайта: {e}"
        await send_log(error_msg, context, is_error=True)
        return f"🤖 {news['title']}\n\n{news['summary'][:300]}...", news['title']

# ==========================================
# 8. МОДЕРАЦИЯ
# ==========================================
async def send_for_moderation(context, news, post_text, title, justification):
    save_post(title, news['link'], post_text)
    
    message_text = f"""
📨 **НОВОСТЬ НА МОДЕРАЦИЮ**

📰 **Заголовок:**
{title}

📝 **Текст поста:**
{post_text[:1000]}{"..." if len(post_text) > 1000 else ""}

🔗 **Источник:** {news['source']}

📊 **Обоснование выбора:**
{justification}

---
✅ Опубликовать
❌ Отклонить
🔄 Следующая новость
"""
    
    keyboard = [
        [
            InlineKeyboardButton("✅ Опубликовать", callback_data=f"publish_{title[:20]}_{news['link'][:10]}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{title[:20]}_{news['link'][:10]}")
        ],
        [InlineKeyboardButton("🔄 Следующая новость", callback_data="next")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await context.bot.send_message(chat_id=ADMIN_ID, text=message_text, reply_markup=reply_markup)

# ==========================================
# 9. ПУБЛИКАЦИЯ
# ==========================================
async def publish_post(context, title, post_text):
    await send_log(f"📤 Публикация поста в канал...", context)
    
    try:
        await context.bot.send_message(chat_id=CHANNEL_ID, text=post_text)
        await send_log(f"✅ Пост опубликован", context)
    except Exception as e:
        error_msg = f"❌ Ошибка публикации: {e}"
        await send_log(error_msg, context, is_error=True)

# ==========================================
# 10. ОСНОВНОЙ ЦИКЛ
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
        
        selected = await select_best_news(news_list, context)
        if not selected:
            await send_log("❌ Не удалось выбрать новость", context, is_error=True)
            return
        
        post_text, title = await rewrite_post(selected, context)
        
        await send_for_moderation(context, selected, post_text, title, selected.get("justification", "Выбрана как самая интересная"))
        
    except Exception as e:
        error_trace = traceback.format_exc()
        error_msg = f"❌ КРИТИЧЕСКАЯ ОШИБКА:\n{str(e)}\n\n{error_trace[:500]}"
        await send_log(error_msg, context, is_error=True)

# ==========================================
# 11. ОБРАБОТЧИКИ КНОПОК
# ==========================================
async def moderation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    callback_type = data.split('_')[0]
    
    if callback_type == "publish":
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT id, title, content FROM posts WHERE status = 'pending' ORDER BY id DESC LIMIT 1")
        post = c.fetchone()
        conn.close()
        
        if not post:
            await query.message.reply_text("❌ Нет поста для публикации.")
            return
        
        post_id, title, content = post
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE posts SET status = 'published' WHERE id = ?", (post_id,))
        conn.commit()
        conn.close()
        
        await query.message.reply_text("📤 Публикую пост...")
        await publish_post(context, title, content)
        await query.message.reply_text("✅ Пост опубликован!")
        
    elif callback_type == "reject":
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT id FROM posts WHERE status = 'pending' ORDER BY id DESC LIMIT 1")
        post = c.fetchone()
        if post:
            c.execute("UPDATE posts SET status = 'rejected' WHERE id = ?", (post[0],))
            conn.commit()
        conn.close()
        
        await query.message.reply_text("❌ Пост отклонён.")
        await send_log("❌ Пост отклонён админом", context)
        await query.message.reply_text("🔄 Загружаю следующую новость...")
        await prepare_and_moderate(context)
        
    elif callback_type == "next":
        await query.message.reply_text("🔄 Загружаю следующую новость...")
        await prepare_and_moderate(context)

# ==========================================
# 12. КОМАНДЫ TELEGRAM
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
    c.execute("SELECT COUNT(*) FROM logs WHERE DATE(created_at) = DATE('now') AND log_type = 'error'")
    today_errors = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM posts WHERE status = 'pending'")
    pending_posts = c.fetchone()[0]
    conn.close()
    
    status_text = f"📊 **Статистика**\n\n"
    status_text += f"📰 Всего постов: {total_posts}\n"
    status_text += f"📆 Сегодня: {today_posts}\n"
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
# 13. ЗАПУСК
# ==========================================
if __name__ == "__main__":
    print("\n" + "="*60)
    print("🚀 ЗАПУСК БОТА")
    print("="*60)
    
    init_db()
    
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
    print("📌 Настройки API: temperature=0.3, top_p=0.9")
    print("🎨 Генерация картинок: ОТКЛЮЧЕНА")
    print("="*60 + "\n")
    
    app.run_polling()