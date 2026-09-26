# database.py

import sqlite3
from config import Config
from datetime import datetime

# Канал по умолчанию: исторически бот публиковал только в новостной канал,
# поэтому он же — значение для старых записей и для вызовов без явного канала.
DEFAULT_CHANNEL = "news"

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
        published_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,  -- UTC! читать через parse_db_timestamp()
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
    migrate_schema()
    print("✅ База данных инициализирована")
    init_stats_table()
    init_subscribers_table()

def migrate_schema():
    """
    Догоняет схему на уже существующих БД: CREATE TABLE IF NOT EXISTS
    не добавляет новые колонки в старые таблицы.
    """
    conn = get_connection()
    c = conn.cursor()
    
    columns = [row[1] for row in c.execute("PRAGMA table_info(posts)")]
    
    # message_id нужен, чтобы Аналитик мог забрать статистику конкретного поста
    if columns and "message_id" not in columns:
        c.execute("ALTER TABLE posts ADD COLUMN message_id INTEGER")
        conn.commit()
        print("🔧 Миграция: добавлена колонка posts.message_id")
    
    # channel нужен, чтобы один бот вёл два канала: дедуп и статистика считаются
    # отдельно по каждому. Без него новость, ушедшая в новостной канал,
    # блокировала бы такую же публикацию в канале лайфхаков.
    if columns and "channel" not in columns:
        c.execute("ALTER TABLE posts ADD COLUMN channel TEXT DEFAULT 'news'")
        # Старые записи публиковались только в новостной канал
        c.execute("UPDATE posts SET channel = 'news' WHERE channel IS NULL")
        conn.commit()
        print("🔧 Миграция: добавлена колонка posts.channel (старые посты -> 'news')")
    
    # link был UNIQUE в одиночку — это мешает публиковать одну новость в два канала
    _migrate_link_unique_to_composite(conn)
    
    conn.close()


def _migrate_link_unique_to_composite(conn):
    """
    Снимает UNIQUE с posts.link и заменяет его составным (link, channel).

    В исходной схеме link был UNIQUE в одиночку: одну и ту же новость нельзя
    было сохранить дважды. Теперь каналов два, и новость вправе уйти и в
    новостной канал, и в канал лайфхаков — блокировать должен только повтор
    в пределах ОДНОГО канала.

    SQLite не умеет удалять UNIQUE-ограничение через ALTER TABLE, поэтому
    таблица пересоздаётся: данные переносятся как есть, затем меняются местами.
    """
    c = conn.cursor()

    # Уже мигрировано (есть составной индекс) — выходим
    indexes = [row[0] for row in c.execute("SELECT name FROM sqlite_master "
                                           "WHERE type='index' AND tbl_name='posts'")]
    if "idx_posts_link_channel" in indexes:
        return

    table_sql = c.execute("SELECT sql FROM sqlite_master WHERE name='posts'").fetchone()
    if not table_sql or "link TEXT UNIQUE" not in (table_sql[0] or ""):
        return  # схема уже без одиночного UNIQUE

    print("🔧 Миграция: link UNIQUE -> составной UNIQUE (link, channel)")

    # Колонки берём из фактической схемы: набор полей мог расшириться
    existing = [row[1] for row in c.execute("PRAGMA table_info(posts)")]
    has_channel = "channel" in existing

    c.execute("PRAGMA foreign_keys=off")
    c.execute("ALTER TABLE posts RENAME TO posts_old")

    cols_new = ("id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, link TEXT, "
                "content TEXT, image_prompt TEXT, image_url TEXT, "
                "published_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
                "status TEXT DEFAULT 'pending', message_id INTEGER, "
                "channel TEXT DEFAULT 'news', "
                "UNIQUE(link, channel)")
    c.execute(f"CREATE TABLE posts ({cols_new})")

    # Переносим данные; channel у старых записей = 'news'
    target_cols = ["id", "title", "link", "content", "image_prompt", "image_url",
                   "published_at", "status", "message_id"]
    if has_channel:
        target_cols.append("channel")
    col_list = ", ".join(target_cols)
    c.execute(f"INSERT INTO posts ({col_list}) SELECT {col_list} FROM posts_old")

    c.execute("DROP TABLE posts_old")
    c.execute("CREATE INDEX IF NOT EXISTS idx_posts_link_channel ON posts(link, channel)")
    c.execute("PRAGMA foreign_keys=on")
    conn.commit()

    moved = c.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
    print(f"🔧 Миграция завершена: перенесено постов — {moved}")

def save_log(message, log_type="info"):
    conn = get_connection()
    c = conn.cursor()
    c.execute("INSERT INTO logs (message, log_type) VALUES (?, ?)", (message, log_type))
    conn.commit()
    conn.close()

# ===== ФУНКЦИИ ДЛЯ ПОСТОВ =====

def save_post(title, link, content, image_prompt=None, image_url=None,
              channel=DEFAULT_CHANNEL):
    """
    Сохраняет пост в БД.

    `channel` определяет, в какой канал уходит пост. Одна и та же новость
    может быть опубликована в оба канала (новости и лайфхаки) — это разные
    продукты, поэтому дедуп считается отдельно по каналам.
    """
    conn = get_connection()
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO posts (title, link, content, image_prompt, image_url, status, channel) 
            VALUES (?, ?, ?, ?, ?, 'pending', ?)
        """, (title, link, content, image_prompt, image_url, channel))
        conn.commit()
        post_id = c.lastrowid
        conn.close()
        print(f"✅ Пост #{post_id} сохранён [{channel}]: {title[:40]}...")
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

def get_recent_post_links(days: int = 1, channel: str = None):
    """
    Возвращает ссылки постов за последние N дней.

    Нужно для дедупа: парсер проверяет всю таблицу, а старые посты удаляются,
    поэтому без этой проверки новость можно было опубликовать повторно.

    `channel` ограничивает выборку одним каналом: новостной канал и канал
    лайфхаков — разные продукты, и новость, вышедшая в одном, не должна
    блокировать публикацию в другом. Без аргумента проверяются все каналы.
    """
    conn = get_connection()
    c = conn.cursor()
    sql = ("SELECT link FROM posts WHERE link IS NOT NULL AND link != '' "
           "AND published_at >= datetime('now', '-' || ? || ' days')")
    params = [days]
    if channel:
        sql += " AND channel = ?"
        params.append(channel)
    c.execute(sql, tuple(params))
    links = {row[0] for row in c.fetchall()}
    conn.close()
    return links

def get_last_post():
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id, title, content, image_prompt, image_url FROM posts ORDER BY id DESC LIMIT 1")
    result = c.fetchone()
    conn.close()
    return result

def get_posts_by_status(status, channel: str = None):
    """Посты по статусу. С `channel` — только этого канала (для раздельной статистики)."""
    conn = get_connection()
    c = conn.cursor()
    if channel:
        c.execute("SELECT id, title, content, published_at FROM posts "
                  "WHERE status = ? AND channel = ? ORDER BY id DESC", (status, channel))
    else:
        c.execute("SELECT id, title, content, published_at FROM posts "
                  "WHERE status = ? ORDER BY id DESC", (status,))
    result = c.fetchall()
    conn.close()
    return result


def count_posts_today(channel: str) -> int:
    """
    Сколько постов канал реально опубликовал за последние сутки.

    Считаем только записи с message_id: строка в БД появляется раньше
    публикации (статус 'pending'), а тестовые прогоны и повторные попытки
    оставляют следы без реальной отправки. Без этого фильтра дневной лимит
    срабатывал бы ложно и останавливал публикации.
    """
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "SELECT COUNT(*) FROM posts WHERE channel = ? AND status = 'published' "
        "AND message_id IS NOT NULL "
        "AND published_at >= datetime('now', '-1 day')",
        (channel,)
    )
    n = c.fetchone()[0]
    conn.close()
    return n or 0


def get_last_publish_at(channel: str):
    """
    Время последней публикации в конкретном канале (ISO-строка) или None.

    Планировщику нужна своя метка на каждый канал: если оба будут смотреть на
    общий счётчик, публикация в одном сдвинет интервал другого и каналыначнут «съезжать» относительно заданного расписания.
    """
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT published_at FROM posts WHERE channel = ? AND status = 'published' "
              "ORDER BY id DESC LIMIT 1", (channel,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def is_post_exists(link, channel: str = None):
    """Проверяет, публиковалась ли ссылка. С `channel` — только в этом канале."""
    conn = get_connection()
    c = conn.cursor()
    if channel:
        c.execute("SELECT id FROM posts WHERE link = ? AND channel = ?", (link, channel))
    else:
        c.execute("SELECT id FROM posts WHERE link = ?", (link,))
    result = c.fetchone()
    conn.close()
    return result is not None

def clean_old_posts(days=30):
    """
    Удаляет старые посты.
    
    Удаляем и их статистику из post_stats. Ссылки на удалённые посты отдельно
    не хранятся, поэтому защита от повторной публикации той же новости — это
    get_recent_post_links() в публикаторе плюс 30-дневное окно хранения здесь:
    при уменьшении окна меньше суток дедуп перестанет работать.
    """
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "DELETE FROM post_stats WHERE post_id IN ("
        "  SELECT id FROM posts WHERE published_at < datetime('now', '-' || ? || ' days')"
        ")",
        (days,)
    )
    stats_deleted = c.rowcount
    c.execute("DELETE FROM posts WHERE published_at < datetime('now', '-' || ? || ' days')", (days,))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    print(f"🧹 Удалено старых постов: {deleted} (со статистикой: {stats_deleted})")
    return deleted

def get_placeholder_image():
    """
    Ссылка-заглушка. Не используется для публикации: Telegram не может скачать
    via.placeholder.com, поэтому пост уходил бы текстом. Оставлена для совместимости
    со старыми записями в БД.
    """
    return "https://via.placeholder.com/1280x720/1a1a2e/ffffff?text=AI+News"

def get_last_image_url():
    """Возвращает URL последней успешной картинки из БД"""
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT image_url FROM posts 
        WHERE image_url IS NOT NULL 
        AND image_url != ?
        ORDER BY id DESC LIMIT 1
    """, (get_placeholder_image(),))
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

def get_all_posts_with_stats(limit: int = 30, channel: str = None):
    """
    Возвращает последние посты с их статистикой.

    `channel` ограничивает выборку одним каналом: без него метрики каналов
    смешиваются (просмотры лайфхаков попадали в отчёт новостного канала).
    """
    conn = get_connection()
    c = conn.cursor()
    sql = """
        SELECT p.id, p.title, p.content, p.published_at, p.message_id,
               ps.views, ps.reactions, ps.clicks, ps.shares, ps.read_ratio, ps.score,
               p.channel
        FROM posts p
        LEFT JOIN post_stats ps ON p.id = ps.post_id
        WHERE p.status = 'published'
    """
    params = []
    if channel:
        sql += " AND p.channel = ?"
        params.append(channel)
    sql += " ORDER BY p.id DESC LIMIT ?"
    params.append(limit)
    c.execute(sql, tuple(params))
    rows = c.fetchall()
    conn.close()
    
    posts = []
    for row in rows:
        posts.append({
            'id': row[0],
            'title': row[1],
            'content': row[2],
            'published_at': row[3],
            'message_id': row[4],
            'views': row[5] or 0,
            'reactions': row[6] or '{}',
            'clicks': row[7] or 0,
            'shares': row[8] or 0,
            'read_ratio': row[9] or 0,
            'score': row[10] or 0,
            'channel': row[11] or DEFAULT_CHANNEL,
        })
    return posts

def update_post_message_id(post_id: int, message_id: int):
    conn = get_connection()
    c = conn.cursor()
    c.execute("UPDATE posts SET message_id = ? WHERE id = ?", (message_id, post_id))
    conn.commit()
    conn.close()


# ===== МОНИТОРИНГ ПОДПИСЧИКОВ (воронка) =====

def init_subscribers_table():
    """
    Таблица истории подписчиков.

    Без неё видно только текущее число: нельзя понять, растёт канал или
    стоит на месте, и невозможно проверить, дала ли что-то конкретная
    правка (кнопки, призывы, кросс-промо).
    """
    conn = get_connection()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS subscribers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel TEXT NOT NULL,
        count INTEGER NOT NULL,
        recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE INDEX IF NOT EXISTS idx_subscribers_channel_time
                 ON subscribers(channel, recorded_at)''')
    conn.commit()
    conn.close()


def save_subscriber_count(channel: str, count: int):
    """Записывает текущее число подписчиков канала."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("INSERT INTO subscribers (channel, count) VALUES (?, ?)", (channel, count))
    conn.commit()
    conn.close()


def get_subscriber_history(channel: str, days: int = 30):
    """История подписчиков канала за N дней: [(дата, число), ...]."""
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "SELECT recorded_at, count FROM subscribers "
        "WHERE channel = ? AND recorded_at >= datetime('now', '-' || ? || ' days') "
        "ORDER BY recorded_at",
        (channel, days)
    )
    rows = c.fetchall()
    conn.close()
    return rows


def get_subscriber_growth(channel: str, hours: int = 24) -> dict:
    """
    Прирост подписчиков за период.

    Возвращает {
        'current': последнее известное число,
        'previous': число на начало периода,
        'delta': прирост (может быть отрицательным),
        'hours': фактический интервал между замерами, часы
    }
    Пустые значения — None, если данных мало.
    """
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "SELECT count, recorded_at FROM subscribers WHERE channel = ? "
        "ORDER BY id DESC LIMIT 1", (channel,)
    )
    last = c.fetchone()

    c.execute(
        "SELECT count, recorded_at FROM subscribers "
        "WHERE channel = ? AND recorded_at <= datetime('now', '-' || ? || ' hours') "
        "ORDER BY id DESC LIMIT 1",
        (channel, hours)
    )
    old = c.fetchone()
    conn.close()

    if not last:
        return {'current': None, 'previous': None, 'delta': None, 'hours': hours}

    current = last[0]
    previous = old[0] if old else None
    return {
        'current': current,
        'previous': previous,
        'delta': (current - previous) if previous is not None else None,
        'hours': hours,
    }