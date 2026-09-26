# test_publisher.py

import asyncio
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from agents.publisher_agent import PublisherAgent
from database import init_db


async def test():
    init_db()
    
    # Создаём тестовый контекст
    context = {
        "post_title": "🚀 ТЕСТОВЫЙ ПОСТ: ИИ уже здесь",
        "post_text": """
🚀 ТЕСТОВЫЙ ПОСТ: ИИ уже здесь

Это тестовый пост для проверки агента-публикатора.

Мне кажется, через 5 лет ИИ изменит всё.

#AI #Test
""",
        "image_url": None,
        "image_prompt": "Futuristic AI technology",
        "selected_news": {"link": "https://test.com", "source": "test"}
    }
    
    print("="*60)
    print("📤 ТЕСТ ПУБЛИКАТОРА")
    print("="*60)
    
    agent = PublisherAgent()
    result = await agent.execute(context)
    
    if result.get("published_post_id"):
        print(f"\n✅ Пост опубликован! ID: {result['published_post_id']}")
    else:
        print("\n❌ Пост не опубликован")


if __name__ == "__main__":
    asyncio.run(test())