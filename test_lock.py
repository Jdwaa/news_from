# test_lock.py

"""
Проверка защиты от второго экземпляра бота.

Два бота на одной БД дают дубли постов в канале, поэтому блокировка —
критичный механизм. Здесь проверяются оба случая:

  1. Живой процесс с PID определяется как живой, мёртвый — как мёртвый.
     Раньше проверки не было вообще, и после аварийного завершения в файле
     оставался чужой PID — понять, работает бот или нет, было нельзя.

  2. Файл с мёртвым PID не блокирует запуск: блокировка ОС снимается сама
     при завершении процесса, и устаревший lock перезахватывается.
"""

import io
import os
import sys
import tempfile

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import main as main_module

_checks = []


def check(name, actual, expected):
    ok = actual == expected
    _checks.append(ok)
    print(f"{'✅' if ok else '❌'} {name}: получено {actual!r}, ожидалось {expected!r}")


def main():
    print("=" * 60)
    print("🔒 ТЕСТ БЛОКИРОВКИ ВТОРОГО ЭКЗЕМПЛЯРА")
    print("=" * 60)

    # ===== 1. Определение живого процесса =====
    check("свой PID определяется как живой",
          main_module._process_alive(os.getpid()), True)
    check("несуществующий PID — мёртвый",
          main_module._process_alive(999999), False)
    check("нулевой PID — мёртвый",
          main_module._process_alive(0), False)
    check("отрицательный PID — мёртвый",
          main_module._process_alive(-1), False)

    # ===== 2. Устаревший файл не мешает запуску =====
    # Имитируем аварию: файл есть, PID чужой, блокировки ОС нет
    tmp_dir = tempfile.mkdtemp()
    stale_path = os.path.join(tmp_dir, ".bot.lock")
    with open(stale_path, "w", encoding="utf-8") as f:
        f.write("999999")

    check("PID из устаревшего файла распознан как мёртвый",
          main_module._process_alive(999999), False)

    # Захватываем этот файл так же, как это делает бот
    lock_file = open(stale_path, "a+", encoding="utf-8")
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        acquired = True
    except OSError:
        acquired = False

    check("устаревший файл перезахватывается (запуск не блокируется)", acquired, True)

    if acquired:
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(str(os.getpid()))
        lock_file.flush()
        with open(stale_path, encoding="utf-8") as f:
            check("в файл записан новый PID", f.read().strip(), str(os.getpid()))

    # ===== 3. Другой ПРОЦЕСС не может захватить тот же файл =====
    # Важно: внутри одного процесса Windows блокировку не повторяет, поэтому
    # проверять надо именно отдельным процессом — второй бот всегда отдельный
    # процесс, и защита нужна ровно против него.
    #
    # Оба процесса запускаются НЕЗАВИСИМО (Popen), а не как потомок держателя:
    # у дочернего процесса наследуется хендл родителя, и Windows ошибочно
    # разрешает повторный захват — проверка тогда даёт ложное «ACQUIRED».
    #
    # Результат читаем через файлы, а не через пайпы: под файловой песочницей
    # DSH создание пайпов запрещено (WinError 5), и capture_output падает.
    import subprocess
    import time as _time

    holder_path = os.path.join(tmp_dir, "holder.txt")
    second_path = os.path.join(tmp_dir, "second.txt")

    # Освобождаем файл, чтобы сценарий был чистым: держателем станет отдельный процесс
    if acquired:
        try:
            lock_file.seek(0)
            import msvcrt
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        lock_file.close()
        acquired = False

    holder_code = (
        "import msvcrt, time\n"
        f"f = open(r'{stale_path}', 'a+', encoding='utf-8')\n"
        "try:\n"
        "    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)\n"
        f"    open(r'{holder_path}','w').write('HELD')\n"
        "except OSError:\n"
        f"    open(r'{holder_path}','w').write('FAIL')\n"
        "time.sleep(6)\n"
    )
    second_code = (
        "import msvcrt, time\n"
        "time.sleep(1.5)\n"
        f"f = open(r'{stale_path}', 'a+', encoding='utf-8')\n"
        "try:\n"
        "    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)\n"
        f"    open(r'{second_path}','w').write('ACQUIRED')\n"
        "except OSError:\n"
        f"    open(r'{second_path}','w').write('BLOCKED')\n"
    )

    with open(os.devnull, "wb") as devnull:
        p1 = subprocess.Popen([sys.executable, "-c", holder_code],
                              stdout=devnull, stderr=devnull)
        p2 = subprocess.Popen([sys.executable, "-c", second_code],
                              stdout=devnull, stderr=devnull)
        p2.wait(timeout=30)
        p1.wait(timeout=30)

    def _read(path):
        if not os.path.exists(path):
            return "нет результата"
        with open(path, encoding="utf-8") as f:
            return f.read().strip()

    check("первый процесс захватил блокировку", _read(holder_path), "HELD")
    check("другой процесс не может захватить залоченный файл",
          _read(second_path), "BLOCKED")

    # Снимаем блокировку ПЕРЕД удалением: на Windows залоченный файл удалить
    # нельзя, и уборка падала с PermissionError уже после всех проверок.
    if acquired:
        try:
            lock_file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        lock_file.close()

    try:
        os.remove(stale_path)
        os.rmdir(tmp_dir)
    except OSError:
        pass

    print("\n" + "=" * 60)
    if all(_checks):
        print(f"✅ ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ ({len(_checks)}/{len(_checks)})")
    else:
        print(f"❌ ПРОВАЛЕНО: {_checks.count(False)} из {len(_checks)}")
    print("=" * 60)
    return 0 if all(_checks) else 1


if __name__ == "__main__":
    sys.exit(main())
