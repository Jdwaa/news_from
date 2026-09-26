# test_rewriter.py

import asyncio
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from agents.parser_agent import ParserAgent
from agents.selector_agent import SelectorAgent
from agents.rewriter_agent import RewriterAgent
from database import init_db


async def test():
    init_db()
    
    # 1. Парсим
    parser = ParserAgent()
    context = await parser.execute({})
    print(f"📰 Собрано {len(context['news_list'])} новостей")
    
    # 2. Выбираем
    selector = SelectorAgent()
    context = await selector.execute(context)
    print(f"✅ Выбрана: {context['selected_news']['title'][:60]}...")
    
    # 3. Рерайтим
    rewriter = RewriterAgent()
    context = await rewriter.execute(context)
    
    print("\n" + "="*60)
    print("📝 ГОТОВЫЙ ПОСТ:")
    print("="*60)
    print(context.get("post_text", "Пост не сгенерирован"))
    print("\n" + "="*60)
    print("🎨 ПРОМПТ ДЛЯ КАРТИНКИ:")
    print("="*60)
    print(context.get("image_prompt", "Нет промпта"))


if __name__ == "__main__":
    asyncio.run(test())