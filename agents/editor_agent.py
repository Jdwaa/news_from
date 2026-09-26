# agents/editor_agent.py

"""
АГЕНТ-РЕДАКТОР

Приводит пост к лимитам канала: сокращает слишком длинный.

Важно: лимиты берутся из профиля канала. Раньше здесь стоял общий
Config.MAX_POST_LENGTH, и редактор обрезал уже написанный пост-лайфхак до
новостного максимума — инструкция теряла шаги и пример, оставался «пресный»
огрызок. Плюс его промпт требовал прогноз и капс, которые в лайфхаках
запрещены: редактор сам ломал то, что просил рерайтер.
"""

import asyncio
import requests
from .base_agent import BaseAgent
from config import Config
from channels import get_profile, NEWS
from net_utils import post_json


class EditorAgent(BaseAgent):
    """Агент-редактор, который правит и сокращает посты"""
    
    def __init__(self):
        super().__init__("Редактор")
    
    def _system_prompt(self, profile) -> str:
        """
        Промпт редактора под канал.

        Для лайфхаков сохраняем практичность и запрет на прогнозы; для новостей
        оставляем прежние требования (мнение, капс, прогноз).
        """
        if profile.key == "tips":
            return """Ты — ПРОФЕССИОНАЛЬНЫЙ РЕДАКТОР канала практичных ИИ-лайфхаков.

ТВОЯ ЗАДАЧА:
Сократить пост, СОХРАНИВ всю практическую пользу. Лучше убрать вводные
слова, чем потерять конкретный шаг или пример.

ТВОИ ПРАВИЛА:
1. СОХРАНИ конкретику: названия инструментов, шаги, промпт, цифры
2. СОХРАНИ эмодзи (4-6 штук) — они задают структуру
3. СОХРАНИ оговорку: где приём НЕ сработает
4. СОКРАЩАЙ за счёт воды, а не за счёт пользы
5. НЕ добавляй прогнозы, не используй слова «инновационный»,
   «революционный» — в этом канале они запрещены

ФОРМАТ ОТВЕТА (строго):
ЗАГОЛОВОК: ...
ВСТУПЛЕНИЕ: ...
ОСНОВНОЙ ТЕКСТ: ...
ВЫВОД: ...
ХЕШТЕГИ: ...
ПРОМПТ_ДЛЯ_КАРТИНКИ: ..."""

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
        2. Сравнивает с лимитами канала
        3. Если длиннее максимума — правит и сокращает через AI
        4. Возвращает исправленный пост
        """
        profile = context.get("channel_profile") or get_profile(NEWS)
        max_length = getattr(profile, "max_length", Config.MAX_POST_LENGTH)
        min_length = getattr(profile, "min_length", Config.MIN_POST_LENGTH)

        post_text = context.get("post_text")
        post_title = context.get("post_title", "")
        image_prompt = context.get("image_prompt", "")
        
        if not post_text:
            self.log("❌ Нет текста для редактирования", is_error=True)
            return context
        
        current_length = len(post_text.strip())
        
        if current_length <= max_length:
            self.log(f"✅ Длина поста в норме: {current_length} символов "
                     f"(лимит канала {min_length}-{max_length})")
            context["editor_ok"] = True
            return context
        
        self.log(f"⚠️ Пост слишком длинный: {current_length} символов (макс {max_length})")
        self.log("✍️ Редактирую и сокращаю...")
        
        # Отправляем на редактирование
        edited = await self._edit_post(post_text, post_title, image_prompt,
                                        profile, max_length, min_length)
        
        if edited:
            new_length = len(edited.strip())
            # Модель иногда режет сильнее, чем просили, и пост проваливается
            # ниже минимума канала — тогда ревизор его отклоняет, и попытка
            # рерайта тратится впустую. Если сокращённая версия вышла за нижнюю
            # границу, оставляем исходную: она длиннее максимума, но ревизор
            # примет её с одним замечанием, а не отклонит целиком.
            if new_length < min_length <= current_length:
                self.log(f"⚠️ Редактор сократил слишком сильно ({new_length} < {min_length}), "
                         f"оставляю исходный текст")
                context["editor_ok"] = True
                return context
            context["post_text"] = edited
            context["editor_ok"] = True
            self.log(f"✅ Пост отредактирован: {new_length} символов")
        else:
            self.log("⚠️ Не удалось отредактировать, обрезаю...")
            context["post_text"] = post_text[:max_length - 3] + "..."
            context["editor_ok"] = True
        
        return context
    
    async def _edit_post(self, post_text: str, post_title: str, image_prompt: str,
                         profile, max_length: int, min_length: int) -> str:
        """Редактирует и сокращает пост через AI"""
        
        if profile.key == "tips":
            requirements = (
                f"- Должен быть {min_length}-{max_length} символов\n"
                f"- Обязательно сохранить конкретные шаги, названия инструментов и пример\n"
                f"- Эмодзи 4-6 штук\n"
                f"- Обязательно оговорка: где приём НЕ сработает\n"
                f"- ЗАПРЕЩЕНО: прогнозы про будущее, слова «инновационный», «революционный»"
            )
        else:
            requirements = (
                f"- Должен быть {min_length}-{max_length} символов\n"
                f"- Обязательно мнение автора (\"мне кажется\", \"я считаю\")\n"
                f"- Минимум 3-5 эмодзи\n"
                f"- Капс в ключевых моментах\n"
                f"- Прогноз о будущем"
            )
        
        user_prompt = f"""
Отредактируй этот пост. Он слишком длинный ({len(post_text)} символов).

Нужно сократить его до {max_length} символов, но НЕ КОРОЧЕ {min_length}.

Требования к посту:
{requirements}
- Все факты сохранены

Текущий пост:
{post_text}

Напиши отредактированную версию (максимум {max_length} символов).
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
                {"role": "system", "content": self._system_prompt(profile)},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.6,
            "max_tokens": 2500
        }
        
        try:
            # post_json добавляет повторы при временных сбоях DeepSeek
            result = await asyncio.to_thread(
                post_json, url, headers, payload, 60, 3
            )
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