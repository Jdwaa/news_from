# test_orchestrator.py

"""
ТЕСТ ОРКЕСТРАТОРА (без Telegram)
Запускает полную цепочку агентов через Оркестратора.
"""

import asyncio
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from orchestrator import Orchestrator
from database import init_db


async def test():
    init_db()
    
    print("\n" + "="*70)
    print("🧪 ТЕСТ ОРКЕСТРАТОРА (без Telegram)")
    print("="*70)
    
    # Создаём оркестратора без бота (только БД)
    orchestrator = Orchestrator(bot=None)
    
    # Запускаем полный цикл
    context = await orchestrator.run()
    
    # Проверяем результат
    print("\n" + "="*70)
    print("📊 РЕЗУЛЬТАТ")
    print("="*70)
    
    if context.get("published_post_id"):
        print(f"✅ Пост опубликован! ID: {context['published_post_id']}")
        print(f"📝 Заголовок: {context.get('post_title', '—')}")
        print(f"📏 Длина: {len(context.get('post_text', ''))} символов")
        print(f"📸 Картинка: {'✅' if context.get('image_url') else '❌'}")
    else:
        print("❌ Пост не опубликован")
        
        if context.get("news_list") is None:
            print("   ⚠️ Новостей не найдено (проверь интернет и RSS)")
        elif not context.get("selected_news"):
            print("   ⚠️ Не удалось выбрать новость")
        elif not context.get("post_text"):
            print("   ⚠️ Пост не сгенерирован")


if __name__ == "__main__":
    asyncio.run(test())