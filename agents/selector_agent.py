# agents/selector_agent.py

"""
АГЕНТ-СЕЛЕКТОР
Выбирает лучшую новость из списка через DeepSeek.
"""

import asyncio
import re
import requests
from .base_agent import BaseAgent
from config import Config
from channels import get_profile, NEWS
from net_utils import post_json


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
        
        # Запрос к DeepSeek (критерии зависят от канала)
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
    
    def _build_system_prompt(self, profile) -> str:
        """
        Критерии отбора под конкретный канал.

        Каналы отбирают РАЗНОЕ. Новостному нужен значимый инфоповод, каналу
        лайфхаков — материал, из которого получится инструкция. Если отбирать
        одинаково, лайфхаки будут выходить из релизов, где применять нечего.
        """
        if profile.key == "tips":
            return """Ты — редактор канала практичных ИИ-лайфхаков. Из списка материала выбери ОДИН, из которого получится применимая инструкция для обычного человека.

ГЛАВНЫЙ КРИТЕРИЙ — ПРИМЕНИМОСТЬ:
Материал годится, только если из него можно сделать «как это сделать самому».

ХОРОШО подходит:
- Конкретный инструмент/сервис с понятным применением (написал текст, сделал картинку, автоматизировал рутину)
- Личный опыт «я попробовал и вот что вышло»
- Готовый приём, промпт, схема работы
- Подборка полезных инструментов с описанием задач
- Решение бытовой или рабочей задачи через ИИ

ПЛОХО подходит (НЕ выбирай):
- Анонсы и релизы без практики («компания представила модель»)
- Инвестиции, раунды, отчётность, судебные иски
- Политика, регулирование, назначения
- Научные статьи без бытового применения
- Видео и подкасты без текстового содержания

При выборе спроси себя: «Смогу я написать по этому шаги, которые читатель повторит сегодня?» Если нет — бери другой материал.

Ответ строго в формате:
НОМЕР: [номер материала из списка]
ОБОСНОВАНИЕ: [какую конкретную пользу получит читатель и какую задачу решит, 2-3 предложения]"""

        return """Ты — редактор технологического канала. Из списка новостей выбери одну самую интересную, полезную или необычную.

Критерии выбора:
1. Новость должна быть полезной для обычного человека (лайфхак, инструмент, идея)
2. Или содержать необычное применение AI
3. Или быть важной для рынка AI

Ответ строго в формате:
НОМЕР: [номер новости из списка]
ОБОСНОВАНИЕ: [почему выбрал эту новость, 2-3 предложения]"""

    async def _call_deepseek(self, news_text: str, context) -> tuple:
        """Отправляет запрос в DeepSeek и возвращает (индекс, обоснование)"""
        
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {Config.DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        
        profile = context.get("channel_profile") or get_profile(NEWS)
        system_prompt = self._build_system_prompt(profile)

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
            # post_json повторяет временные сбои: иначе один сетевой сбой
            # заставлял брать первую новость списка наугад
            result = await asyncio.to_thread(
                post_json, url, headers, payload, 30, 3
            )
            content = result["choices"][0]["message"]["content"]
            
            # Парсим ответ
            match = re.search(r"НОМЕР:\s*(\d+)", content)
            if match:
                selected_index = int(match.group(1)) - 1
                # Проверяем, что индекс в допустимых пределах
                news_list = context.get("news_list", [])
                if selected_index < 0 or selected_index >= len(news_list):
                    self.log(f"⚠️ AI вернул неверный номер: {selected_index + 1}, использую первую")
                    return 0, "Выбрана как первая доступная"
            else:
                self.log("⚠️ Не удалось найти номер в ответе AI", is_error=True)
                return 0, "Выбрана как первая доступная"
            
            justification_match = re.search(r"ОБОСНОВАНИЕ:\s*(.+)", content, re.DOTALL)
            justification = justification_match.group(1).strip() if justification_match else "Новость выбрана как самая интересная"
            
            return selected_index, justification
            
        except Exception as e:
            self.log(f"❌ Ошибка выбора новости: {e}", is_error=True)
            return 0, "Выбрана как первая доступная"