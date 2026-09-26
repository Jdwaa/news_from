# maintenance.py

"""
ОБСЛУЖИВАНИЕ

Регулярные задачи, которые не относятся к публикации, но без них бот
со временем «зарастает»:

  1. Чистка старых картинок — каждая генерация даёт ~1.5 МБ, за месяц это
     сотни мегабайт. Посты в БД остаются, удаляются только файлы.
  2. Бэкап базы — вся история и статистика лежат в одном файле, и без копий
     потерять их можно разом (сбой диска, случайное удаление, порча файла).
  3. Сводка по подписчикам — раз в сутки пишет админу, как растут каналы.
"""

import os
import shutil
import sqlite3
import time
from datetime import datetime, timedelta

from config import Config


# =========================================================
# ЧИСТКА КАРТИНОК
# =========================================================

def cleanup_images(days: int = 14, dry_run: bool = False) -> dict:
    """
    Удаляет сгенерированные картинки старше N дней.

    Файлы нужны только на момент публикации: пост в БД хранит ссылку на CDN
    и локальный путь, но для истории важен текст, а не мегабайты картинок.
    Возвращает {'deleted': n, 'freed_mb': float, 'kept': n}.
    """
    image_dir = Config.IMAGE_DIR
    if not os.path.isdir(image_dir):
        return {"deleted": 0, "freed_mb": 0.0, "kept": 0}

    cutoff = time.time() - days * 86400
    deleted = 0
    freed = 0
    kept = 0

    for name in os.listdir(image_dir):
        path = os.path.join(image_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            if os.path.getmtime(path) >= cutoff:
                kept += 1
                continue
            size = os.path.getsize(path)
            if not dry_run:
                os.remove(path)
            deleted += 1
            freed += size
        except OSError:
            # Файл занят или уже удалён — не повод ронять обслуживание
            continue

    return {
        "deleted": deleted,
        "freed_mb": round(freed / (1024 * 1024), 2),
        "freed_bytes": freed,
        "kept": kept,
    }


# =========================================================
# БЭКАП БАЗЫ
# =========================================================

def backup_database(keep: int = 7, backup_dir: str = None) -> dict:
    """
    Делает резервную копию базы и удаляет самые старые копии.

    Используем sqlite3.backup(), а не копирование файла: бот может писать в
    базу в этот момент, и обычное копирование дало бы битый снимок.

    `keep` — сколько последних копий хранить.
    Возвращает {'path': ..., 'size_kb': ..., 'removed': n}.
    """
    db_path = Config.DB_PATH
    if not os.path.exists(db_path):
        return {"path": None, "size_kb": 0, "removed": 0, "error": "база не найдена"}

    backup_dir = backup_dir or Config.BACKUP_DIR
    os.makedirs(backup_dir, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(backup_dir, f"news_bot_{stamp}.db")

    try:
        src = sqlite3.connect(db_path)
        dst = sqlite3.connect(dest)
        with dst:
            src.backup(dst)          # безопасный снимок даже при активной записи
        dst.close()
        src.close()
        size_kb = round(os.path.getsize(dest) / 1024, 1)
    except Exception as e:
        return {"path": None, "size_kb": 0, "removed": 0, "error": str(e)}

    # Удаляем самые старые копии, оставляя `keep` последних
    removed = 0
    try:
        backups = sorted(
            (f for f in os.listdir(backup_dir)
             if f.startswith("news_bot_") and f.endswith(".db")),
            reverse=True,
        )
        for old in backups[max(1, keep):]:
            try:
                os.remove(os.path.join(backup_dir, old))
                removed += 1
            except OSError:
                continue
    except OSError:
        pass

    return {"path": dest, "size_kb": size_kb, "removed": removed}


# =========================================================
# СВОДКА ПО РОСТУ
# =========================================================

def daily_growth_text() -> str:
    """
    Текст суточной сводки по каналам: подписчики и прирост.

    Отдельный отчёт, а не часть /growth: его бот присылает сам раз в сутки,
    чтобы владелец видел динамику, не запрашивая вручную.
    """
    from channels import active_profiles
    from database import get_subscriber_growth

    lines = ["📊 Сводка за сутки", ""]
    for p in active_profiles():
        stats = get_subscriber_growth(p.key, hours=24)
        current = stats.get("current")
        delta = stats.get("delta")
        if current is None:
            lines.append(f"{p.emoji} {p.title}: данных пока нет")
            continue
        if delta is None:
            delta_text = "сравнить пока не с чем"
        elif delta > 0:
            delta_text = f"+{delta}"
        else:
            delta_text = str(delta)
        lines.append(f"{p.emoji} {p.title}: {current} ({delta_text})")
    return "\n".join(lines)
