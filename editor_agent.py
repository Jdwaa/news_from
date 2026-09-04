# agents/editor_agent.py

"""
АГЕНТ-РЕДАКТОР
Исправляет и сокращает пост до нужной длины,
сохраняя все требования: мнение, эмодзи, капс, прогнозы.
"""

import re
import requests
from .base_agent import BaseAgent
from config import Config


class EditorAgent(BaseAgent):
    """Агент-редактор, который правит и сокращает посты"""
    
    def __init__(self):
        super().__init__("Редактор")
        self.max_length = 950
        self.system_prompt = self._get_system_prompt()
    
    def _get_system_prompt(self) -> str:
        return """Ты — ПРОФЕССИОНАЛЬНЫЙ РЕДАКТОР в крупном издательстве.

ТВОЯ ЗАДАЧА:
Отредактировать пост, сохранив его смысл, стиль и все требования.

ТВОИ ПРАВИЛА:
1. СОХРАНИ МНЕНИЕ АВТОРА — оно должно быть в тексте
2. СОХРАНИ ЭМОДЗИ — минимум 3-5 штук
3. СОХРАНИ КАПС в ключевых моментах
4. СОХРАНИ ПРОГНОЗЫ
5. СОХРАНИ ВСЕ ФАКТЫ
6. НЕ ОБРЕЗАЙ ТЕКСТ — ПЕРЕФОРМУЛИРУЙ более кратко

ФОРМАТ ОТВЕТА (строго):
ЗАГОЛОВОК: ...
ВСТУПЛЕНИЕ: ...
ОСНОВНОЙ ТЕКСТ: ...
ВЫВОД: ...
ХЕШТЕГИ: ...
ПРОМПТ_ДЛЯ_КАРТИНКИ: ..."""

    async def execute(self, context: dict) -> dict:
        """
        1. Берёт пост из контекста
        2. Проверяет длину
        3. Если > 950 — правит и сокращает через AI
        4. Возвращает исправленный пост
        """
        post_text = context.get("post_text")
        post_title = context.get("post_title", "")
        image_prompt = context.get("image_prompt", "")
        
        if not post_text:
            self.log("❌ Нет текста для редактирования", is_error=True)
            return context
        
        current_length = len(post_text.strip())
        
        if current_length <= self.max_length:
            self.log(f"✅ Длина поста в норме: {current_length} символов")
            context["editor_ok"] = True
            return context
        
        self.log(f"⚠️ Пост слишком длинный: {current_length} символов (макс {self.max_length})")
        self.log("✍️ Редактирую и сокращаю...")
        
        # Отправляем на редактирование
        edited = await self._edit_post(post_text, post_title, image_prompt)
        
        if edited:
            new_length = len(edited.strip())
            context["post_text"] = edited
            context["editor_ok"] = True
            self.log(f"✅ Пост отредактирован: {new_length} символов")
            
            # Проверяем, сохранилось ли мнение
            opinion_keywords = ["мне кажется", "я считаю", "я думаю"]
            has_opinion = any(kw in edited.lower() for kw in opinion_keywords)
            if not has_opinion:
                self.log("⚠️ Мнение автора потеряно, добавляю...")
                context["post_text"] = edited + "\n\n📌 А что думаете вы?"
        else:
            self.log("⚠️ Не удалось отредактировать, обрезаю...")
            context["post_text"] = post_text[:self.max_length - 3] + "..."
            context["editor_ok"] = True
        
        return context
    
    async def _edit_post(self, post_text: str, post_title: str, image_prompt: str) -> str:
        """Редактирует и сокращает пост через AI"""
        
        user_prompt = f"""
Отредактируй этот пост. Он слишком длинный ({len(post_text)} символов).

Нужно сократить его до {self.max_length} символов.

Требования к посту:
- Должен быть 600-950 символов
- Обязательно мнение автора ("мне кажется", "я считаю")
- Минимум 3-5 эмодзи
- Капс в ключевых моментах
- Прогноз о будущем
- Все факты сохранены

Текущий пост:
{post_text}

Напиши отредактированную версию (максимум {self.max_length} символов).
Сохрани все важное. Формат ответа строго:
ЗАГОЛОВОК: ...
ВСТУПЛЕНИЕ: ...
ОСНОВНОЙ ТЕКСТ: ...
ВЫВОД: ...
ХЕШТЕГИ: ...
ПРОМПТ_ДЛЯ_КАРТИНКИ: ...
"""
        
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {Config.DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.6,
            "max_tokens": 1500
        }
        
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=60)
            response.raise_for_status()
            result = response.json()
            content = result["choices"][0]["message"]["content"].strip()
            
            # Парсим ответ
            post_data = {
                "title": "",
                "intro": "",
                "main_text": "",
                "conclusion": "",
                "hashtags": "",
                "image_prompt": ""
            }
            
            for line in content.split('\n'):
                line = line.strip()
                if line.startswith("ЗАГОЛОВОК:"):
                    post_data["title"] = line.replace("ЗАГОЛОВОК:", "").strip()
                elif line.startswith("ВСТУПЛЕНИЕ:"):
                    post_data["intro"] = line.replace("ВСТУПЛЕНИЕ:", "").strip()
                elif line.startswith("ОСНОВНОЙ ТЕКСТ:"):
                    post_data["main_text"] = line.replace("ОСНОВНОЙ ТЕКСТ:", "").strip()
                elif line.startswith("ВЫВОД:"):
                    post_data["conclusion"] = line.replace("ВЫВОД:", "").strip()
                elif line.startswith("ХЕШТЕГИ:"):
                    post_data["hashtags"] = line.replace("ХЕШТЕГИ:", "").strip()
                elif line.startswith("ПРОМПТ_ДЛЯ_КАРТИНКИ:"):
                    post_data["image_prompt"] = line.replace("ПРОМПТ_ДЛЯ_КАРТИНКИ:", "").strip()
            
            # Собираем пост
            parts = []
            if post_data["title"]:
                parts.append(post_data["title"])
            if post_data["intro"]:
                parts.append(post_data["intro"])
            if post_data["main_text"]:
                parts.append(post_data["main_text"])
            if post_data["conclusion"]:
                parts.append(f"📌 {post_data['conclusion']}")
            if post_data["hashtags"]:
                parts.append(post_data["hashtags"])
            
            return "\n\n".join(parts)
            
        except Exception as e:
            self.log(f"  ⚠️ Ошибка редактирования: {e}", is_error=True)
            return None