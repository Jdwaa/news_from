# agents/selector_agent.py

"""
АГЕНТ-СЕЛЕКТОР
Выбирает лучшую новость из списка через DeepSeek.
"""

import re
import requests
from .base_agent import BaseAgent
from config import Config


class SelectorAgent(BaseAgent):
    """Агент для выбора лучшей новости через AI"""
    
    def __init__(self):
        super().__init__("Селектор новостей")
    
    async def execute(self, context: dict) -> dict:
        """
        1. Проверяет, есть ли новости в контексте
        2. Отправляет список в DeepSeek
        3. Получает номер лучшей новости
        4. Сохраняет её в context["selected_news"]
        """
        news_list = context.get("news_list", [])
        
        if not news_list:
            self.log("❌ Нет новостей для выбора", is_error=True)
            return context
        
        self.log(f"📤 Выбор лучшей из {len(news_list)} новостей...")
        
        # Формируем текст для AI
        news_text = self._format_news_list(news_list)
        
        # Запрос к DeepSeek
        selected_index, justification = await self._call_deepseek(news_text, context)
        
        # Сохраняем выбранную новость
        if selected_index is not None and 0 <= selected_index < len(news_list):
            selected = news_list[selected_index]
            selected["justification"] = justification
            context["selected_news"] = selected
            context["selected_index"] = selected_index
            
            self.log(f"✅ Выбрана новость: {selected['title'][:60]}...")
            self.log(f"📊 Обоснование: {justification[:100]}...")
            
            # Сохраняем в Memory Bank
            self.memory.add_to_history({
                "event": "news_selected",
                "title": selected["title"],
                "source": selected["source"],
                "justification": justification
            })
        else:
            # Если AI не смог выбрать — берём первую
            self.log("⚠️ AI не выбрал новость, беру первую", is_error=True)
            context["selected_news"] = news_list[0]
            context["selected_index"] = 0
        
        return context
    
    def _format_news_list(self, news_list: list) -> str:
        """Форматирует список новостей для отправки в AI"""
        result = []
        for i, news in enumerate(news_list):
            title = news.get("title", "Без заголовка")
            summary = news.get("summary", "Нет описания")[:200]
            source = news.get("source", "Неизвестный источник")
            result.append(f"Новость {i+1}:\nЗаголовок: {title}\nОписание: {summary}\nИсточник: {source}\n")
        return "\n".join(result)
    
    async def _call_deepseek(self, news_text: str, context) -> tuple:
        """Отправляет запрос в DeepSeek и возвращает (индекс, обоснование)"""
        
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {Config.DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        
        system_prompt = """Ты — редактор технологического канала. Из списка новостей выбери одну самую интересную, полезную или необычную.

Критерии выбора:
1. Новость должна быть полезной для обычного человека (лайфхак, инструмент, идея)
2. Или содержать необычное применение AI
3. Или быть важной для рынка AI

Ответ строго в формате:
НОМЕР: [номер новости из списка]
ОБОСНОВАНИЕ: [почему выбрал эту новость, 2-3 предложения]"""

        payload = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Список новостей:\n{news_text}"}
            ],
            "temperature": 0.7,
            "max_tokens": 500
        }
        
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            result = response.json()
            content = result["choices"][0]["message"]["content"]
            
            # Парсим ответ
            match = re.search(r"НОМЕР:\s*(\d+)", content)
            if match:
                selected_index = int(match.group(1)) - 1
            else:
                self.log("⚠️ Не удалось найти номер в ответе AI", is_error=True)
                return 0, "Выбрана как первая доступная"
            
            justification_match = re.search(r"ОБОСНОВАНИЕ:\s*(.+)", content, re.DOTALL)
            justification = justification_match.group(1).strip() if justification_match else "Новость выбрана как самая интересная"
            
            return selected_index, justification
            
        except Exception as e:
            self.log(f"❌ Ошибка выбора новости: {e}", is_error=True)
            return 0, "Выбрана как первая доступная"