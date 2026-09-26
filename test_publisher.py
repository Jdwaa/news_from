# test_publisher.py

"""
Проверка публикатора: канал берётся из профиля, дедуп считается по каналу.

Ссылка делается уникальной на каждый прогон: фиксированный https://test.com
уже лежал в БД, и повторный запуск теста падал на дедупликации.
"""

import asyncio
import sys
import os
import uuid

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from agents.publisher_agent import PublisherAgent
from channels import PROFILES, NEWS, TIPS
from database import init_db, get_recent_post_links

_checks = []


def check(name, actual, expected):
    ok = actual == expected
    _checks.append(ok)
    print(f"{'✅' if ok else '❌'} {name}: получено {actual!r}, ожидалось {expected!r}")


POST_TEXT = """🚀 ТЕСТОВЫЙ ПОСТ: ИИ уже здесь 🤖

Это тестовый пост для проверки агента-публикатора. Он проверяет, что пост
сохраняется в базу и связывается с правильным каналом.

Мне кажется, через 5 лет ИИ изменит всё. ✨

#AI #Test"""


async def main():
    init_db()
    agent = PublisherAgent()
    link = f"https://test.local/publisher-{uuid.uuid4().hex[:10]}"

    async def publish(profile, title):
        ctx = {
            "post_title": title,
            "post_text": POST_TEXT,
            "image_url": None,
            "image_prompt": "Futuristic AI technology",
            "selected_news": {"link": link, "source": "test"},
            "channel_profile": profile,
        }
        return await agent.execute(ctx)

    print("=" * 60)
    print("📤 ПУБЛИКАТОР: канал из профиля + дедуп по каналу")
    print("=" * 60)

    # 1. Публикация в новостной канал
    res_news = await publish(PROFILES[NEWS], "🚀 ТЕСТ: новостной канал")
    check("пост сохранён для новостного канала", bool(res_news.get("published_post_id")), True)
    check("канал записан как news", res_news.get("channel"), NEWS)
    check("ссылка видна в новостном канале",
          link in get_recent_post_links(days=1, channel=NEWS), True)

    # 2. Та же ссылка в канал лайфхаков: должна пройти — это другой продукт
    res_tips = await publish(PROFILES[TIPS], "💡 ТЕСТ: канал лайфхаков")
    check("та же ссылка прошла в другой канал", bool(res_tips.get("published_post_id")), True)
    check("канал записан как tips", res_tips.get("channel"), TIPS)

    # 3. Повтор в том же канале: должен быть отсечён дедупом
    res_dup = await publish(PROFILES[TIPS], "💡 ТЕСТ: повтор в лайфхаках")
    check("повтор в том же канале отсечён", res_dup.get("duplicate_skipped"), True)

    print("\n" + "=" * 60)
    if all(_checks):
        print(f"✅ ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ ({len(_checks)}/{len(_checks)})")
    else:
        print(f"❌ ПРОВАЛЕНО: {_checks.count(False)} из {len(_checks)}")
    print("=" * 60)
    return 0 if all(_checks) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
