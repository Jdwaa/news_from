# test_maintenance.py

"""
Проверка обслуживания: бэкап базы и чистка старых картинок.

Бэкап критичен тем, что проверяется здесь: копия должна быть ЦЕЛОЙ базой,
а не пустым файлом. Обычное копирование при активной записи даёт битый
снимок, поэтому используется sqlite3.backup().
"""

import io
import os
import sqlite3
import sys
import tempfile
import time

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config import Config
import maintenance

_checks = []


def check(name, actual, expected):
    ok = actual == expected
    _checks.append(ok)
    print(f"{'✅' if ok else '❌'} {name}: получено {actual!r}, ожидалось {expected!r}")


def main():
    print("=" * 60)
    print("🧰 ТЕСТ ОБСЛУЖИВАНИЯ")
    print("=" * 60)

    tmp = tempfile.mkdtemp()

    # ===== БЭКАП БАЗЫ =====
    # Создаём маленькую БД с данными: проверяем, что копия их сохранит
    db_path = os.path.join(tmp, "test.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.execute("INSERT INTO t (v) VALUES ('важные данные')")
    conn.commit()
    conn.close()

    old_db, old_dir = Config.DB_PATH, Config.BACKUP_DIR
    Config.DB_PATH = db_path
    Config.BACKUP_DIR = os.path.join(tmp, "backups")
    try:
        res = maintenance.backup_database(keep=3)
        check("бэкап создан", bool(res.get("path")), True)
        check("размер копии больше нуля", res.get("size_kb", 0) > 0, True)

        # Ключевая проверка: копия открывается и данные на месте
        backup_ok = False
        content = None
        if res.get("path") and os.path.exists(res["path"]):
            try:
                c = sqlite3.connect(res["path"])
                content = c.execute("SELECT v FROM t").fetchone()
                c.close()
                backup_ok = True
            except Exception:
                backup_ok = False
        check("копия открывается как база", backup_ok, True)
        check("данные в копии сохранены", content, ("важные данные",))

        # Ротация: при keep=3 четвёртая копия вытесняет самую старую
        for _ in range(3):
            time.sleep(0.01)
            maintenance.backup_database(keep=3)
        backups = [f for f in os.listdir(Config.BACKUP_DIR)
                   if f.startswith("news_bot_") and f.endswith(".db")]
        check("ротация хранит не больше 3 копий", len(backups) <= 3, True)
    finally:
        Config.DB_PATH, Config.BACKUP_DIR = old_db, old_dir

    # ===== ЧИСТКА КАРТИНОК =====
    img_dir = os.path.join(tmp, "images")
    os.makedirs(img_dir)
    old_img_dir = Config.IMAGE_DIR
    Config.IMAGE_DIR = img_dir
    try:
        fresh = os.path.join(img_dir, "fresh.jpg")
        stale = os.path.join(img_dir, "stale.jpg")
        for path in (fresh, stale):
            with open(path, "wb") as f:
                f.write(b"x" * 1024)

        # Состариваем один файл на 30 дней
        old_time = time.time() - 30 * 86400
        os.utime(stale, (old_time, old_time))

        # Пробный прогон ничего не удаляет
        dry = maintenance.cleanup_images(days=14, dry_run=True)
        check("пробный прогон нашёл старый файл", dry["deleted"], 1)
        check("пробный прогон не удалил файл", os.path.exists(stale), True)

        # Реальная чистка
        real = maintenance.cleanup_images(days=14)
        check("старый файл удалён", real["deleted"], 1)
        check("старый файл исчез с диска", os.path.exists(stale), False)
        check("свежий файл остался", os.path.exists(fresh), True)
        # Считаем в байтах: файл в тесте маленький, и в мегабайтах он округлился
        # бы до нуля, хотя место реально освободилось
        check("освобождено больше нуля", real["freed_bytes"] > 0, True)
    finally:
        Config.IMAGE_DIR = old_img_dir

    # ===== СВОДКА =====
    try:
        text = maintenance.daily_growth_text()
        check("сводка не пустая", bool(text.strip()), True)
        check("сводка содержит заголовок", "Сводка" in text, True)
    except Exception as e:
        check(f"сводка формируется (ошибка: {e})", False, True)

    print("\n" + "=" * 60)
    if all(_checks):
        print(f"✅ ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ ({len(_checks)}/{len(_checks)})")
    else:
        print(f"❌ ПРОВАЛЕНО: {_checks.count(False)} из {len(_checks)}")
    print("=" * 60)
    return 0 if all(_checks) else 1


if __name__ == "__main__":
    sys.exit(main())
