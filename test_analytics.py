# test_analytics.py

import asyncio
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from agents.analytics_agent import AnalyticsAgent
from database import init_db, init_stats_table


async def test():
    # Инициализируем БД
    init_db()
    init_stats_table()
    
    print("\n" + "="*60)
    print("🧪 ТЕСТ АНАЛИТИКА")
    print("="*60)
    
    # Создаём агента
    analytics = AnalyticsAgent()
    
    # Запускаем
    context = await analytics.execute({})
    
    # Показываем результат
    summary = analytics.get_summary()
    print("\n📊 РЕЗУЛЬТАТ:")
    print(summary)


if __name__ == "__main__":
    asyncio.run(test())