# test_parser.py

import asyncio
import sys
import os

# Добавляем путь к корню проекта
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from agents.parser_agent import ParserAgent
from database import init_db  # 👈 ПРАВИЛЬНЫЙ импорт


async def test():
    # 1. Инициализируем базу данных
    init_db()
    
    # 2. Создаём агента
    agent = ParserAgent()
    context = {}
    
    # 3. Запускаем агента
    result = await agent.execute(context)
    
    # 4. Выводим результаты
    print(f"\n✅ Найдено новостей: {len(result['news_list'])}")
    for i, news in enumerate(result['news_list'][:3]):
        print(f"{i+1}. {news['title'][:60]}...")


if __name__ == "__main__":
    asyncio.run(test())