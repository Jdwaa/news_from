# test_improvements.py

"""
ПРОВЕРКА ПОСЛЕДНЕГО ПАКЕТА ИСПРАВЛЕНИЙ

1. Консоль Windows переводится в UTF-8 (иначе бот падает на первом print с эмодзи).
2. Настройки читаются из .env (POST_INTERVAL и др.), битые значения -> дефолт.
3. Время из БД (UTC от SQLite) читается как UTC и переводится в локальное —
   «лучшее время публикации» больше не уезжает на величину смещения.
4. Блокирующие HTTP-запросы уходят в поток, event loop бота не замораживается.
5. Практики аналитики считаются сравнением средних оценок: «делай» и «избегай»
   больше не дублируют друг друга.

Работает на временной БД и временной памяти — данные проекта не изменяются.
"""

import asyncio
import importlib
import os
import shutil
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import requests

import config as config_module
from agents.analytics_agent import AnalyticsAgent
from agents.selector_agent import SelectorAgent
from config import Config
from console_utils import setup_utf8_console
from database import init_db, save_post_stats
from memory_bank import MemoryBank

TMP_DB = "test_improvements_tmp.db"
TMP_MEMORY = ".tmp_test_improvements"

_checks = []


def check(name, actual, expected):
    ok = actual == expected
    _checks.append(ok)
    print(f"{'✅' if ok else '❌'} {name}: получено {actual!r}, ожидалось {expected!r}")


class FakeResponse:
    """Ответ DeepSeek с корректным форматом выбора новости."""

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": "НОМЕР: 2\nОБОСНОВАНИЕ: тест"}}]}


def cleanup():
    for path in (TMP_DB, TMP_MEMORY):
        if os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.exists(path):
            os.remove(path)


async def main():
    cleanup()

    # ==========================================
    # 1. КОНСОЛЬ В UTF-8
    # ==========================================
    setup_utf8_console()
    setup_utf8_console()  # идемпотентность
    check("консоль переведена в UTF-8", sys.stdout.encoding.lower().replace("-", ""), "utf8")
    print("✅ эмодзи печатаются: 🔧 📊 🚀")

    # ==========================================
    # 2. НАСТРОЙКИ ИЗ .ENV
    # ==========================================
    os.environ["POST_INTERVAL"] = "12345"
    importlib.reload(config_module)
    check("POST_INTERVAL читается из .env", config_module.Config.POST_INTERVAL, 12345)

    os.environ["POST_INTERVAL"] = "не число"
    importlib.reload(config_module)
    check("битое значение -> дефолт", config_module.Config.POST_INTERVAL, 10800)

    os.environ.pop("POST_INTERVAL", None)
    importlib.reload(config_module)
    check("значение из .env подхватывается", config_module.Config.POST_INTERVAL, 10800)
    check("MAX_POST_LENGTH из Config", config_module.Config.MAX_POST_LENGTH, 950)

    # ==========================================
    # 3. ТАЙМЗОНА: UTC В БД -> ЛОКАЛЬНОЕ ВРЕМЯ
    # ==========================================
    Config.DB_PATH = TMP_DB
    init_db()

    # Берём UTC-момент, который в локальной зоне приходится ровно на 12:00
    local_noon = datetime.now().replace(hour=12, minute=0, second=0, microsecond=0)
    utc_moment = local_noon.astimezone(timezone.utc).replace(tzinfo=None)
    utc_str = utc_moment.strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect(TMP_DB)
    for i in range(3):
        conn.execute(
            "INSERT INTO posts (title, link, content, status, published_at) VALUES (?, ?, ?, 'published', ?)",
            (f"Пост {i}", f"https://test.local/{i}", "текст", utc_str),
        )
        conn.commit()
        save_post_stats(conn.execute("SELECT last_insert_rowid()").fetchone()[0], {"score": 7, "views": 100})
    conn.close()

    analytics = AnalyticsAgent()
    analytics.memory = MemoryBank(path=TMP_MEMORY)
    report = analytics.get_best_posting_time()

    print(f"ℹ️  В БД (UTC): {utc_str} -> в локальной зоне: {local_noon.strftime('%H:%M')}")
    check("лучший час — локальный, а не UTC", report["hour"], 12)
    check("лучший день — локальный", report["day"], local_noon.weekday())

    offset_hours = int(local_noon.astimezone().utcoffset().total_seconds() // 3600)
    if offset_hours != 0:
        check(
            f"час не равен UTC-часу (смещение {offset_hours:+d} ч)",
            report["hour"] != utc_moment.hour,
            True,
        )
    else:
        print("ℹ️  Смещение 0 ч — эта проверка неприменима")

    # ==========================================
    # 4. БЛОКИРУЮЩИЙ HTTP НЕ МОРОЗИТ EVENT LOOP
    # ==========================================
    threads = []

    def slow_post(url, headers=None, json=None, timeout=None):
        threads.append(threading.current_thread().name)
        time.sleep(0.3)  # имитируем долгий ответ DeepSeek
        return FakeResponse()

    beats = 0

    async def heartbeat():
        nonlocal beats
        while True:
            await asyncio.sleep(0.01)
            beats += 1

    selector = SelectorAgent()
    selector.memory = MemoryBank(path=TMP_MEMORY)

    with patch.object(requests, "post", slow_post):
        pulse = asyncio.create_task(heartbeat())
        await asyncio.sleep(0.05)
        beats_before = beats
        context = await selector.execute({
            "news_list": [
                {"title": "A", "summary": "описание A", "source": "test"},
                {"title": "B", "summary": "описание B", "source": "test"},
                {"title": "C", "summary": "описание C", "source": "test"},
            ]
        })
        beats_during = beats - beats_before
        pulse.cancel()

    check("запрос ушёл в отдельный поток", threads and threads[0] != "MainThread", True)
    check("event loop не был заблокирован (тикал во время запроса)", beats_during > 5, True)
    check("выбор новости всё ещё работает", context.get("selected_news", {}).get("title"), "B")

    # ==========================================
    # 5. ПРАКТИКИ: СРАВНЕНИЕ СРЕДНИХ ОЦЕНОК
    # ==========================================
    posts = [
        {"title": "ЗАГОЛОВОК ОДИН", "content": "А что думаете вы? Текст поста.", "score": 9},
        {"title": "ЗАГОЛОВОК ДВА", "content": "А что думаете вы? Ещё текст.", "score": 8},
        {"title": "ЗАГОЛОВОК ТРИ", "content": "Согласно отчёту, всё хорошо.", "score": 4},
        {"title": "ЗАГОЛОВОК ЧЕТЫРЕ", "content": "Как сообщает источник, всё хорошо.", "score": 4},
    ]

    best, worst = analytics._analyze_practices(posts)

    check("вопрос к читателю попал в лучшие практики", any("Задавай читателю вопрос" in p for p in best), True)
    check("вопрос к читателю НЕ попал в худшие", any("Задавай читателю вопрос" in p for p in worst), False)
    check("запрещённые слова попали в «избегай»", any("Убирай «согласно»" in p for p in worst), True)
    check("запрещённые слова НЕ попали в лучшие", any("Убирай «согласно»" in p for p in best), False)
    check("списки не пересекаются", set(best) & set(worst), set())
    check("старая логика _extract_practices удалена", hasattr(analytics, "_extract_practices"), False)

    print("\nℹ️  Лучшие практики:")
    for p in best:
        print(f"   • {p}")
    print("ℹ️  Избегать:")
    for p in worst:
        print(f"   • {p}")

    # ==========================================
    # 6. ПАМЯТЬ ВИДИТ ЗАПИСИ ДРУГИХ АГЕНТОВ
    # ==========================================
    reader = MemoryBank(path=TMP_MEMORY)      # агент, созданный раньше
    writer = MemoryBank(path=TMP_MEMORY)      # агент, который потом записал
    writer.set("best_practices", ["практика от Аналитика"])
    check("другой экземпляр видит свежую запись", reader.get("best_practices"), ["практика от Аналитика"])

    writer.add_to_history({"event": "test"})
    check("история тоже обновляется", reader.get_history(1)[0]["event"], "test")

    reader.set("last_parsed_count", 7)
    check("своя запись не теряется", reader.get("last_parsed_count"), 7)

    # ==========================================
    # ИТОГ
    # ==========================================
    print("\n" + "=" * 60)
    if all(_checks):
        print(f"✅ ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ ({len(_checks)}/{len(_checks)})")
    else:
        print(f"❌ ПРОВАЛЕНО: {_checks.count(False)} из {len(_checks)}")
    print("=" * 60)

    cleanup()
    return 0 if all(_checks) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
