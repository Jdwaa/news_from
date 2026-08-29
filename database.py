# database.py

import sqlite3
from config import Config
from datetime import datetime

def get_connection():
    return sqlite3.connect(Config.DB_PATH)

def init_db():
    conn = get_connection()
    c = conn.cursor()
    
    # Таблица постов
    c.execute('''CREATE TABLE IF NOT EXISTS posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        link TEXT UNIQUE,
        content TEXT,
        image_prompt TEXT,
        image_url TEXT,
        message_id INTEGER,
        published_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        status TEXT DEFAULT 'pending'
    )''')
    
    # Таблица логов
    c.execute('''CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        message TEXT,
        log_type TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    conn.commit()
    conn.close()
    print("✅ База данных инициализирована")
    init_stats_table()

def save_log(message, log_type="info"):
    conn = get_connection()
    c = conn.cursor()
    c.execute("INSERT INTO logs (message, log_type) VALUES (?, ?)", (message, log_type))
    conn.commit()
    conn.close()

# ===== ФУНКЦИИ ДЛЯ ПОСТОВ =====

def save_post(title, link, content, image_prompt=None, image_url=None):
    """Сохраняет пост в БД"""
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO posts (title, link, content, image_prompt, image_url, status) 
            VALUES (?, ?, ?, ?, ?, 'pending')
        """, (title, link, content, image_prompt, image_url))
        conn.commit()
        post_id = c.lastrowid
        conn.close()
        print(f"✅ Пост #{post_id} сохранён: {title[:40]}...")
        return post_id
    except sqlite3.IntegrityError:
        conn.close()
        print(f"⚠️ Дубликат ссылки, пост пропущен: {link}")
        return None

def update_post_image(post_id, image_url):
    conn = get_connection()
    c = conn.cursor()
    c.execute("UPDATE posts SET image_url = ? WHERE id = ?", (image_url, post_id))
    conn.commit()
    conn.close()

def update_post_status(post_id, status):
    conn = get_connection()
    c = conn.cursor()
    c.execute("UPDATE posts SET status = ? WHERE id = ?", (status, post_id))
    conn.commit()
    conn.close()

def get_post_by_id(post_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id, title, content, image_prompt, image_url, status FROM posts WHERE id = ?", (post_id,))
    result = c.fetchone()
    conn.close()
    return result

def get_pending_post():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id, title, content, image_prompt, image_url FROM posts WHERE status = 'pending' ORDER BY id DESC LIMIT 1")
    result = c.fetchone()
    conn.close()
    return result

def get_last_post():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id, title, content, image_prompt, image_url FROM posts ORDER BY id DESC LIMIT 1")
    result = c.fetchone()
    conn.close()
    return result

def get_posts_by_status(status):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id, title, content, published_at FROM posts WHERE status = ? ORDER BY id DESC", (status,))
    result = c.fetchall()
    conn.close()
    return result

def is_post_exists(link):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id FROM posts WHERE link = ?", (link,))
    result = c.fetchone()
    conn.close()
    return result is not None

def clean_old_posts(days=30):
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM posts WHERE published_at < datetime('now', '-' || ? || ' days')", (days,))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    print(f"🧹 Удалено старых постов: {deleted}")
    return deleted

def get_placeholder_image():
    return "https://via.placeholder.com/1280x720/1a1a2e/ffffff?text=AI+News"

def get_last_image_url():
    """Возвращает URL последней успешной картинки из БД"""
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT image_url FROM posts 
        WHERE image_url IS NOT NULL 
        AND image_url != 'https://via.placeholder.com/1280x720/1a1a2e/ffffff?text=AI+News'
        ORDER BY id DESC LIMIT 1
    """)
    result = c.fetchone()
    conn.close()
    return result[0] if result else None    

    # ===== ФУНКЦИИ ДЛЯ СТАТИСТИКИ =====

def init_stats_table():
    """Создаёт таблицу для статистики постов, если её нет"""
    conn = get_connection()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS post_stats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id INTEGER UNIQUE,
        views INTEGER DEFAULT 0,
        reactions TEXT DEFAULT '{}',
        clicks INTEGER DEFAULT 0,
        shares INTEGER DEFAULT 0,
        read_ratio REAL DEFAULT 0,
        score INTEGER DEFAULT 0,
        collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (post_id) REFERENCES posts (id)
    )''')
    conn.commit()
    conn.close()
    print("✅ Таблица post_stats создана (или уже существует)")

def save_post_stats(post_id: int, stats: dict):
    """Сохраняет статистику поста"""
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO post_stats 
        (post_id, views, reactions, clicks, shares, read_ratio, score)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        post_id,
        stats.get('views', 0),
        str(stats.get('reactions', {})),
        stats.get('clicks', 0),
        stats.get('shares', 0),
        stats.get('read_ratio', 0),
        stats.get('score', 0)
    ))
    conn.commit()
    conn.close()

def get_post_stats(post_id: int):
    """Получает статистику поста по ID"""
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT views, reactions, clicks, shares, read_ratio, score FROM post_stats WHERE post_id = ?", (post_id,))
    result = c.fetchone()
    conn.close()
    if result:
        return {
            'views': result[0],
            'reactions': result[1],
            'clicks': result[2],
            'shares': result[3],
            'read_ratio': result[4],
            'score': result[5]
        }
    return None

def get_all_posts_with_stats(limit: int = 30):
    """Возвращает последние посты с их статистикой"""
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT p.id, p.title, p.content, p.published_at, 
               ps.views, ps.reactions, ps.clicks, ps.shares, ps.read_ratio, ps.score
        FROM posts p
        LEFT JOIN post_stats ps ON p.id = ps.post_id
        WHERE p.status = 'published'
        ORDER BY p.id DESC
        LIMIT ?
    """, (limit,))
    rows = c.fetchall()
    conn.close()
    
    posts = []
    for row in rows:
        posts.append({
            'id': row[0],
            'title': row[1],
            'content': row[2],
            'published_at': row[3],
            'views': row[4] or 0,
            'reactions': row[5] or '{}',
            'clicks': row[6] or 0,
            'shares': row[7] or 0,
            'read_ratio': row[8] or 0,
            'score': row[9] or 0
        })
    return posts

def update_stats_table():
    """Обновляет статистику из таблицы post_stats в основную таблицу posts (если нужно)"""
    conn = get_connection()
    c = conn.cursor()
    # Тут можно добавить логику, если понадобится
    conn.close()

def update_post_message_id(post_id: int, message_id: int):
    conn = get_connection()
    c = conn.cursor()
    c.execute("UPDATE posts SET message_id = ? WHERE id = ?", (message_id, post_id))
    conn.commit()
    conn.close()    