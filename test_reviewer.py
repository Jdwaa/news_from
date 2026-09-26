# test_reviewer.py

import asyncio
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from agents.reviewer_agent import ReviewerAgent
from database import init_db


async def test():
    init_db()
    
    agent = ReviewerAgent()
    
    # Хороший пост
    good_post = """
🚀 ИИ УЖЕ ЗДЕСЬ: ChatGPT научился думать как человек?

Представьте, что вы разговариваете с нейросетью, и она НЕ тупит. Звучит как фантастика? А это уже реальность. DeepMind выпустили модель, которая может рассуждать и планировать шаги вперёд.

Мне кажется, это изменит всё. Через 5 лет такие модели будут помогать врачам ставить диагнозы, а учителям — объяснять сложные темы. И это только начало! 

Вопрос не в том, БУДЕТ ли это, а в том, КОГДА это станет нашей реальностью. Мне кажется, мы увидим первые результаты уже в ближайшие 2-3 года.

#AI #Tech #Future
"""
    
    context = {
        "post_text": good_post,
        "post_title": "🚀 ИИ УЖЕ ЗДЕСЬ: ChatGPT научился думать как человек?"
    }
    
    print("="*60)
    print("📋 ТЕСТ РЕВИЗОРА (хороший пост)")
    print("="*60)
    
    result = await agent.execute(context)
    
    if result.get("text_approved"):
        print("✅ ПОСТ ПРОШЁЛ ПРОВЕРКУ!")
    else:
        print("❌ ПОСТ НЕ ПРОШЁЛ:")
        print(result.get("reviewer_feedback"))
    
    print("\n" + "="*60)
    print("📋 ТЕСТ РЕВИЗОРА (плохой пост)")
    print("="*60)
    
    # Плохой пост
    bad_post = "Просто короткий пост без эмодзи и мнения."
    context_bad = {"post_text": bad_post, "post_title": "Новость"}
    
    result_bad = await agent.execute(context_bad)
    
    if result_bad.get("text_approved"):
        print("✅ ПОСТ ПРОШЁЛ ПРОВЕРКУ!")
    else:
        print("❌ ПОСТ НЕ ПРОШЁЛ:")
        print(result_bad.get("reviewer_feedback"))


if __name__ == "__main__":
    asyncio.run(test())