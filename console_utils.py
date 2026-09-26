# console_utils.py

"""
Утилиты для консоли.
"""

import io
import os
import sys


def setup_utf8_console() -> None:
    """
    Делает вывод в консоль устойчивым к эмодзи.

    В cmd на Windows консоль по умолчанию cp1251, и первый же print с эмодзи
    падает с UnicodeEncodeError — бот не доходит даже до инициализации БД.
    reconfigure() в этом случае не помогает (Python берёт кодировку из ОС),
    поэтому переключаем потоки на UTF-8 самостоятельно.

    Функция безопасна: вызывается много раз и никогда не ломает запуск.
    """
    # Переменную ставим и для дочерних процессов (в т.ч. для запусков из хостинга)
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    for name, stream in (("stdout", sys.stdout), ("stderr", sys.stderr)):
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
            continue
        except (AttributeError, ValueError, OSError):
            pass
        try:
            # Поток не переконфигурируется или это консоль, читающая кодировку из ОС:
            # подменяем его на текстовую обёртку вокруг того же файлового дескриптора.
            buffer = getattr(stream, "buffer", None)
            if buffer is None:
                continue
            setattr(sys, name, io.TextIOWrapper(
                buffer, encoding="utf-8", errors="replace", line_buffering=True
            ))
        except (AttributeError, ValueError, OSError):
            # Совсем экзотический случай (например, вывод перенаправлен) — не критично:
            # остаётся print с ошибками, но бот продолжает работать.
            pass
