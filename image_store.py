# image_store.py

"""
ХРАНИЛИЩЕ КАРТИНОК

Управляет локальными файлами сгенерированных картинок.

Зачем отдельный модуль: файлы нужны ровно на две вещи —
  1. отправить пост в Telegram (пока картинка не ушла, файл нужен);
  2. дать резерв, если генерация откажет.

Держать всю историю бессмысленно: каждая картинка ~1.5 МБ, а после публикации
она уже в Telegram. Поэтому храним РОВНО ОДИН файл — последний успешный.
Нет данных для поста, генерация упала — берём этот файл, и пост всё равно
выходит с картинкой, а не текстом.
"""

import os
import time
import uuid

from config import Config


def ensure_dir() -> str:
    """Создаёт папку картинок, если её нет. Возвращает путь."""
    os.makedirs(Config.IMAGE_DIR, exist_ok=True)
    return Config.IMAGE_DIR


def latest_image() -> str | None:
    """
    Путь к последней сохранённой картинке (резерву) или None.

    Если файлов оказалось несколько (например, остались от прежних версий),
    возвращаем самый свежий по времени изменения.
    """
    directory = Config.IMAGE_DIR
    if not os.path.isdir(directory):
        return None

    files = []
    for name in os.listdir(directory):
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            try:
                files.append((os.path.getmtime(path), path))
            except OSError:
                continue
    if not files:
        return None
    return max(files)[1]


def save_new(content: bytes, ext: str = ".jpg") -> str | None:
    """
    Сохраняет новую картинку как текущий резерв.

    Перед записью удаляет прежний резерв: хранится ровно один файл.
    Возвращает путь к файлу или None при ошибке.
    """
    if not content:
        return None
    try:
        directory = ensure_dir()
        remove_all()
        path = os.path.join(
            directory, f"last_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}{ext}"
        )
        with open(path, "wb") as f:
            f.write(content)
        return path
    except OSError:
        return None


def promote(path: str) -> bool:
    """
    Делает указанный файл текущим резервом, удаляя остальные.

    Нужно, когда картинка уже лежит на диске (скачана генератором) и её
    нужно оставить как резерв до момента публикации.
    """
    if not path or not os.path.exists(path):
        return False
    target = latest_image()
    if target == path:
        return True
    try:
        for name in os.listdir(Config.IMAGE_DIR):
            full = os.path.join(Config.IMAGE_DIR, name)
            if os.path.isfile(full) and full != path:
                try:
                    os.remove(full)
                except OSError:
                    continue
        return True
    except OSError:
        return False


def remove(path: str) -> bool:
    """Удаляет файл картинки. Возвращает True, если файла больше нет."""
    if not path:
        return True
    try:
        if os.path.exists(path):
            os.remove(path)
        return True
    except OSError:
        return False


def remove_all() -> int:
    """Удаляет все файлы картинок. Возвращает число удалённых."""
    directory = Config.IMAGE_DIR
    if not os.path.isdir(directory):
        return 0
    removed = 0
    for name in os.listdir(directory):
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            try:
                os.remove(path)
                removed += 1
            except OSError:
                continue
    return removed


def cleanup_temp() -> int:
    """
    Убирает временные файлы публикации, оставшиеся после сбоя.

    temp_publish.jpg создаётся при отправке картинки по URL; если процесс
    упал между записью и удалением, файл остаётся мусором.
    """
    removed = 0
    for name in ("temp_publish.jpg",):
        path = os.path.join("memory_bank", name)
        if os.path.exists(path):
            try:
                os.remove(path)
                removed += 1
            except OSError:
                continue
    return removed
