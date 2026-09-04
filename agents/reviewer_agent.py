# agents/reviewer_agent.py

"""
АГЕНТ-РЕВИЗОР
Проверяет качество поста по жесткому чек-листу.
Возвращает замечания, если пост не прошёл проверку.
"""

import re
from .base_agent import BaseAgent
from config import Config


class ReviewerAgent(BaseAgent):
    """Агент для проверки качества постов"""
    
    def __init__(self):
        super().__init__("Ревизор постов")
    
    async def execute(self, context: dict) -> dict:
        """
        1. Берёт пост из context["post_text"]
        2. Проверяет по чек-листу
        3. Если всё хорошо → ставит text_approved = True
        4. Если есть проблемы → возвращает замечания в context["reviewer_feedback"]
        """
        post_text = context.get("post_text", "")
        post_title = context.get("post_title", "")
        
        if not post_text:
            self.log("❌ Нет текста для проверки", is_error=True)
            context["text_approved"] = False
            context["reviewer_feedback"] = "Нет текста для проверки"
            return context
        
        self.log("📋 Проверка поста...")
        
        # Список замечаний
        issues = []
        
        # ==========================================
        # 1. ПРОВЕРКА ДЛИНЫ
        # ==========================================
        length = len(post_text.strip())
        if length < Config.MIN_POST_LENGTH:
            issues.append(f"🔴 Пост слишком короткий: {length} символов (минимум {Config.MIN_POST_LENGTH})")
        elif length > Config.MAX_POST_LENGTH:
            issues.append(f"🔴 Пост слишком длинный: {length} символов (максимум {Config.MAX_POST_LENGTH})")
        
        # ==========================================
        # 2. ПРОВЕРКА ЗАГОЛОВКА (капс + эмодзи)
        # ==========================================
        if post_title:
            # Проверяем, есть ли в заголовке капс (минимум 2 слова в верхнем регистре)
            words = post_title.split()
            caps_count = sum(1 for w in words if w.isupper() and len(w) > 2)
            if caps_count < 2:
                issues.append("🔴 Заголовок должен содержать минимум 2 слова КАПСОМ")
            
            # Проверяем наличие эмодзи в заголовке
            emoji_in_title = len(re.findall(r'[\U0001F600-\U0001F9FF]', post_title))
            if emoji_in_title < 1:
                issues.append("🔴 Заголовок должен содержать хотя бы 1 эмодзи")
        
        # ==========================================
        # 3. ПРОВЕРКА ЭМОДЗИ В ПОСТЕ
        # ==========================================
        emoji_count = len(re.findall(r'[\U0001F600-\U0001F9FF]', post_text))
        if emoji_count < 3:
            issues.append(f"🔴 Мало эмодзи в посте: {emoji_count} (минимум 3)")
        
        # ==========================================
        # 4. ПРОВЕРКА ЛИЧНОГО МНЕНИЯ
        # ==========================================
        #opinion_keywords = [
        #   "мне кажется", "я считаю", "я думаю", "я вижу", 
        #    "по моему мнению", "я уверен", "я предполагаю"
        #]
        #has_opinion = any(keyword in post_text.lower() for keyword in opinion_keywords)
        #if not has_opinion:
        #   issues.append("🔴 Нет личного мнения. Добавь 'мне кажется', 'я считаю' или аналоги")
        
        # ==========================================
        # 5. ПРОВЕРКА ПРОГНОЗА
        # ==========================================
        forecast_keywords = [
            "через", "скоро", "в будущем", "в ближайшие", 
            "предстоит", "изменится", "станет", "будет"
        ]
        has_forecast = any(keyword in post_text.lower() for keyword in forecast_keywords)
        if not has_forecast:
            issues.append("🔴 Нет прогноза. Добавь 'в будущем', 'через 5 лет' или аналоги")
        
        # ==========================================
        # 6. ПРОВЕРКА ЗАПРЕЩЁННЫХ СЛОВ
        # ==========================================
        forbidden_words = ["согласно", "как сообщает", "по данным", "источник сообщает"]
        forbidden_found = [word for word in forbidden_words if word in post_text.lower()]
        if forbidden_found:
            issues.append(f"🔴 Найдены запрещённые слова: {', '.join(forbidden_found)}. Переформулируй.")
        
        # ==========================================
        # 7. ПРОВЕРКА СТРУКТУРЫ (абзацы)
        # ==========================================
        paragraphs = post_text.split('\n\n')
        if len(paragraphs) < 2:
            issues.append("🔴 Пост должен содержать минимум 2 абзаца")
        
        # ==========================================
        # ИТОГОВЫЙ ВЕРДИКТ
        # ==========================================
        if issues:
            context["text_approved"] = False
            context["reviewer_feedback"] = "\n".join(issues)
            self.log(f"❌ Пост не прошёл проверку: {len(issues)} замечаний")
            for issue in issues[:3]:
                self.log(f"  • {issue}")
            if len(issues) > 3:
                self.log(f"  • ... и ещё {len(issues)-3} замечаний")
        else:
            context["text_approved"] = True
            context["reviewer_feedback"] = "✅ Все проверки пройдены"
            self.log("✅ Пост прошёл все проверки")
        
        # Сохраняем в Memory Bank
        self.memory.add_to_history({
            "event": "review_completed",
            "status": "approved" if context["text_approved"] else "rejected",
            "issues_count": len(issues),
            "length": len(post_text.strip())
        })
        
        return context