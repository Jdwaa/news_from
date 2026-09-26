# test_image_store.py

"""
Проверка хранилища картинок.

Правило: на диске хранится РОВНО ОДИН файл — последняя картинка. Она нужна,
чтобы отправить пост и чтобы подстраховать публикацию, если генерация откажет.
История не копится: после поста картинка уже в Telegram, а каждая весит ~1.5 МБ.

Здесь проверяются и границы: после успешной публикации файл удаляется,
а после неудачной — остаётся резервом.
"""

import io
import os
import sys
import tempfile

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from config import Config
import image_store

_checks = []


def check(name, actual, expected):
    ok = actual == expected
    _checks.append(ok)
    print(f"{'✅' if ok else '❌'} {name}: получено {actual!r}, ожидалось {expected!r}")


def count_files(directory):
    if not os.path.isdir(directory):
        return 0
    return len([f for f in os.listdir(directory)
                if os.path.isfile(os.path.join(directory, f))])


def main():
    print("=" * 60)
    print("🖼️ ТЕСТ ХРАНИЛИЩА КАРТИНОК")
    print("=" * 60)

    tmp = tempfile.mkdtemp()
    old_dir = Config.IMAGE_DIR
    Config.IMAGE_DIR = os.path.join(tmp, "images")
    try:
        # ===== 1. Хранится ровно один файл =====
        p1 = image_store.save_new(b"a" * 1000, ".jpg")
        check("первая картинка сохранена", bool(p1 and os.path.exists(p1)), True)
        check("после первой картинки файл один", count_files(Config.IMAGE_DIR), 1)

        p2 = image_store.save_new(b"b" * 2000, ".jpg")
        check("после второй картинки файл ОДИН (старая удалена)",
              count_files(Config.IMAGE_DIR), 1)
        check("вторая картинка стала текущей", image_store.latest_image(), p2)
        check("размер новой картинки верный",
              os.path.getsize(p2) if p2 else 0, 2000)

        # ===== 2. resolve: latest_image возвращает актуальный файл =====
        check("latest_image отдаёт существующий файл",
              os.path.exists(image_store.latest_image() or ""), True)

        # ===== 3. remove удаляет файл (после успешной публикации) =====
        removed = image_store.remove(p2)
        check("удаление прошло", removed, True)
        check("после удаления файлов нет", count_files(Config.IMAGE_DIR), 0)
        check("latest_image при пустой папке -> None", image_store.latest_image(), None)

        # ===== 4. promote оставляет указанный файл единственным =====
        image_store.save_new(b"c" * 500, ".jpg")
        keeper = image_store.save_new(b"d" * 700, ".jpg")
        ok = image_store.promote(keeper)
        check("promote выполнен", ok, True)
        check("после promote файл один", count_files(Config.IMAGE_DIR), 1)
        check("promote сохранил нужный файл", image_store.latest_image(), keeper)

        # ===== 5. remove_all =====
        image_store.save_new(b"e" * 100, ".jpg")
        n = image_store.remove_all()
        check("remove_all удалил файлы", count_files(Config.IMAGE_DIR), 0)
        check("remove_all вернул число", n >= 1, True)

        # ===== 6. Пустой контент не сохраняется =====
        check("пустой контент не создаёт файл", image_store.save_new(b"", ".jpg"), None)

        # ===== 7. Обработка несуществующего пути =====
        check("remove несуществующего файла не падает",
              image_store.remove(os.path.join(tmp, "нет.jpg")), True)
        check("latest_image при отсутствии папки -> None", image_store.latest_image(), None)
    finally:
        Config.IMAGE_DIR = old_dir

    print("\n" + "=" * 60)
    if all(_checks):
        print(f"✅ ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ ({len(_checks)}/{len(_checks)})")
    else:
        print(f"❌ ПРОВАЛЕНО: {_checks.count(False)} из {len(_checks)}")
    print("=" * 60)
    return 0 if all(_checks) else 1


if __name__ == "__main__":
    sys.exit(main())
