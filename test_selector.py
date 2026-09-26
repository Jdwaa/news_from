# test_selector.py

import asyncio
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from agents.parser_agent import ParserAgent
from agents.selector_agent import SelectorAgent
from database import init_db


async def test():
    init_db()
    
    # 1. Парсим новости
    parser = ParserAgent()
    context = {}
    context = await parser.execute(context)
    
    print(f"\n📰 Собрано {len(context['news_list'])} новостей")
    
    # 2. Выбираем лучшую
    selector = SelectorAgent()
    context = await selector.execute(context)
    
    selected = context.get("selected_news")
    if selected:
        print(f"\n✅ ВЫБРАНА НОВОСТЬ:")
        print(f"   Заголовок: {selected['title']}")
        print(f"   Источник: {selected['source']}")
        print(f"   Обоснование: {selected.get('justification', '—')}")
    else:
        print("❌ Новость не выбрана")


if __name__ == "__main__":
    asyncio.run(test())