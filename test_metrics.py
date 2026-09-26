# test_metrics.py

"""
ПРОВЕРКА СБОРА РЕАЛЬНЫХ МЕТРИК

1. Публикатор сохраняет message_id опубликованного сообщения.
2. Аналитик забирает реальные просмотры/реакции из публичного превью t.me
   (bot.get_chat -> @username -> t.me/<username>/<message_id>?embed=1).
3. Заглушка с random-числами больше не используется.

Работает на временной БД и временной памяти — news_bot.db и
memory_bank/context.json не изменяются.
"""

import asyncio
import os
import shutil
import sqlite3
import sys
from types import SimpleNamespace

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import agents.publisher_agent as publisher_module
from agents.analytics_agent import AnalyticsAgent
from agents.publisher_agent import PublisherAgent
from config import Config
from database import get_all_posts_with_stats, init_db
from memory_bank import MemoryBank

TMP_DB = "test_metrics_tmp.db"
TMP_MEMORY = ".tmp_test_metrics_memory"

# HTML-превью: как его отдаёт t.me/<channel>/<id>?embed=1
PREVIEW_HTML = """
<html><body>
<div class="tgme_widget_message_wrap">
  <div class="tgme_widget_message">
    <div class="tgme_widget_message_text">ТЕСТОВЫЙ ПОСТ</div>
    <div class="tgme_widget_message_footer">
      <span class="tgme_widget_message_views">1.2K</span>
      <div class="tgme_widget_message_reactions">
        <span class="tgme_widget_message_reaction">
          <span class="tgme_widget_message_reaction_emoji">👍</span>
          <span class="tgme_widget_message_reaction_count">5</span>
        </span>
        <span class="tgme_widget_message_reaction">
          <span class="tgme_widget_message_reaction_emoji">🔥</span>
          <span class="tgme_widget_message_reaction_count">8</span>
        </span>
      </div>
    </div>
  </div>
</div>
</body></html>
"""

_checks = []


def check(name, actual, expected):
    ok = actual == expected
    _checks.append(ok)
    print(f"{'✅' if ok else '❌'} {name}: получено {actual!r}, ожидалось {expected!r}")


class FakeMessage:
    def __init__(self, message_id):
        self.message_id = message_id


class FakeBot:
    """Заглушка Telegram-бота: фиксирует отправки и отдаёт @username канала."""

    def __init__(self, message_id=555, username="testchannel"):
        self.message_id = message_id
        self.username = username
        self.sent = []
        self.keyboards = []

    async def send_message(self, chat_id, text, reply_markup=None,
                           disable_web_page_preview=None, parse_mode=None):
        self.sent.append(("message", text))
        self.keyboards.append(reply_markup)
        return FakeMessage(self.message_id)

    async def send_photo(self, chat_id, photo, caption=None, reply_markup=None):
        self.sent.append(("photo", caption))
        self.keyboards.append(reply_markup)
        return FakeMessage(self.message_id)

    async def get_chat(self, chat_id):
        return SimpleNamespace(username=self.username)


class FakeResponse:
    status_code = 200
    content = b"fake-jpeg-bytes"


async def main():
    # ==========================================
    # 0. ИЗОЛИРОВАННАЯ БД И ПАМЯТЬ
    # ==========================================
    for path in (TMP_DB, TMP_MEMORY):
        if os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.exists(path):
            os.remove(path)

    Config.DB_PATH = TMP_DB

    # Создаём БД со СТАРОЙ схемой (без message_id) — как у пользователя,
    # и проверяем, что init_db() доводит её до актуальной.
    legacy = sqlite3.connect(TMP_DB)
    legacy.execute("""CREATE TABLE posts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT, link TEXT UNIQUE, content TEXT, image_prompt TEXT,
        image_url TEXT, published_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        status TEXT DEFAULT 'pending'
    )""")
    legacy.commit()
    legacy.close()

    init_db()

    columns_conn = sqlite3.connect(TMP_DB)
    columns = [r[1] for r in columns_conn.execute("PRAGMA table_info(posts)")]
    columns_conn.close()
    check("миграция: message_id добавлен в старую БД", "message_id" in columns, True)

    # ==========================================
    # 1. ПАРСИНГ ПРОСМОТРОВ
    # ==========================================
    parse = AnalyticsAgent._parse_views
    check("парсинг '1.2K'", parse("1.2K"), 1200)
    check("парсинг '15,3 тыс.'", parse("15,3 тыс."), 15300)
    check("парсинг '2.5M'", parse("2.5M"), 2500000)
    check("парсинг '1 234'", parse("1 234"), 1234)
    check("парсинг '999'", parse("999"), 999)
    check("парсинг мусора", parse("views"), None)

    # ==========================================
    # 2. ПАРСИНГ ПРЕВЬЮ
    # ==========================================
    analytics = AnalyticsAgent(bot=FakeBot())
    analytics.memory = MemoryBank(path=TMP_MEMORY)
    views, reactions = analytics._parse_preview(PREVIEW_HTML)
    check("превью: просмотры", views, 1200)
    check("превью: реакции", reactions, {"👍": 5, "🔥": 8})

    empty_views, _ = analytics._parse_preview("<html><body>Post not found</body></html>")
    check("превью без просмотров -> None", empty_views, None)

    # ==========================================
    # 3. ПУБЛИКАЦИЯ: message_id СОХРАНЯЕТСЯ
    # ==========================================
    bot = FakeBot(message_id=777)
    publisher = PublisherAgent(bot=bot)
    publisher.memory = MemoryBank(path=TMP_MEMORY)

    # 3.1. Пост с картинкой
    publisher_module.requests.get = lambda *a, **kw: FakeResponse()
    context = {
        "post_title": "ТЕСТ С КАРТИНКОЙ",
        "post_text": "Текст поста с картинкой 🚀",
        "image_url": "https://example.com/pic.jpg",
        "image_prompt": "test",
        "selected_news": {"link": "https://test.local/1", "source": "test"},
    }
    result = await publisher.execute(context)
    check("публикация: published_post_id получен", bool(result.get("published_post_id")), True)
    check("публикация: message_id в контексте", result.get("message_id"), 777)

    published = [
        p for p in get_all_posts_with_stats(limit=10)
        if p["id"] == result["published_post_id"]
    ]
    check("публикация: message_id в БД", published[0]["message_id"] if published else None, 777)

    # 3.2. Пост без картинки (нет последней картинки в БД -> текстовый путь)
    bot_text = FakeBot(message_id=888)
    publisher_text = PublisherAgent(bot=bot_text)
    publisher_text.memory = MemoryBank(path=TMP_MEMORY)
    publisher_text.bot = bot_text
    publisher_module.get_last_image_url = lambda: None

    context2 = {
        "post_title": "ТЕСТ БЕЗ КАРТИНКИ",
        "post_text": "Текст поста без картинки 🚀",
        "image_url": None,
        "image_prompt": "test",
        "selected_news": {"link": "https://test.local/2", "source": "test"},
    }
    result2 = await publisher_text.execute(context2)
    check("публикация без картинки: message_id в контексте", result2.get("message_id"), 888)

    # 3.3. Третий пост — чтобы аналитике хватило данных для выводов
    context3 = dict(context2, selected_news={"link": "https://test.local/3", "source": "test"})
    await publisher_text.execute(context3)

    # ==========================================
    # 4. АНАЛИТИК ЗАБИРАЕТ РЕАЛЬНЫЕ МЕТРИКИ ПО message_id
    # ==========================================
    analytics.memory = MemoryBank(path=TMP_MEMORY)
    analytics._fetch_preview = lambda username, message_id: PREVIEW_HTML

    posts = get_all_posts_with_stats(limit=10)
    check("БД отдаёт message_id для сбора метрик", posts[0]["message_id"], 888)

    post = posts[0]
    stats = await analytics._collect_stats_from_telegram(post)
    check("метрики: просмотры реальные", stats["views"], 1200)
    check("метрики: реакции реальные", stats["reactions"], {"👍": 5, "🔥": 8})
    check(
        "метрики: оценка посчитана по формуле",
        stats["score"],
        analytics._calculate_score(1200, {"👍": 5, "🔥": 8}, 0, 0, 0),
    )

    # ==========================================
    # 5. НЕТ ДАННЫХ -> НЕТ ВЫДУМАННЫХ ЧИСЕЛ
    # ==========================================
    check("нет message_id -> None", await analytics._collect_stats_from_telegram({"id": 999}), None)
    check(
        "нет бота -> None",
        await AnalyticsAgent()._collect_stats_from_telegram({"id": 999, "message_id": 1}),
        None,
    )
    check("заглушка _generate_dummy_stats удалена", hasattr(analytics, "_generate_dummy_stats"), False)
    check("random больше не импортируется в Аналитике", "import random" in open("agents/analytics_agent.py", encoding="utf-8").read(), False)

    # ==========================================
    # 6. СКВОЗНОЙ ПРОГОН execute(): МЕТРИКИ ДОХОДЯТ ДО БД И ДО ОТЧЁТА
    # ==========================================
    analytics.memory = MemoryBank(path=TMP_MEMORY)
    analytics._fetch_preview = lambda username, message_id: PREVIEW_HTML
    await analytics.execute({})

    stored = {p["id"]: p for p in get_all_posts_with_stats(limit=10)}
    check("execute(): просмотры всех 3 постов записаны", [p["views"] for p in stored.values()], [1200, 1200, 1200])
    check("execute(): оценки посчитаны и записаны", [p["score"] for p in stored.values()], [8, 8, 8])

    report = analytics.memory.get("analytics_report")
    check("execute(): отчёт аналитики сохранён", bool(report), True)
    check(
        "execute(): отчёт в каноническом формате (час/день валидны)",
        sorted(report.keys()) if report else None,
        ["day", "hour", "interval_hours", "reason"],
    )
    check(
        "execute(): час/день в допустимых диапазонах",
        (0 <= report["hour"] <= 23, 0 <= report["day"] <= 6) if report else None,
        (True, True),
    )

    # ==========================================
    # ИТОГ
    # ==========================================
    print("\n" + "=" * 60)
    if all(_checks):
        print(f"✅ ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ ({len(_checks)}/{len(_checks)})")
    else:
        print(f"❌ ПРОВАЛЕНО: {_checks.count(False)} из {len(_checks)}")
    print("=" * 60)

    for path in (TMP_DB, TMP_MEMORY):
        if os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.exists(path):
            os.remove(path)
    if os.path.exists("temp_publish.jpg"):
        os.remove("temp_publish.jpg")

    return 0 if all(_checks) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
