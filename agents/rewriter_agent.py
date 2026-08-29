# agents/rewriter_agent.py

"""
АГЕНТ-РЕРАЙТЕР
Переписывает новость в авторский футуристический пост.
Генерирует промпт для картинки.
Если пост слишком длинный — отправляет на повторный рерайт.
"""

import re
import requests
from bs4 import BeautifulSoup
from .base_agent import BaseAgent
from config import Config


class RewriterAgent(BaseAgent):
    """Агент для рерайта новостей в авторские посты"""
    
    def __init__(self):
        super().__init__("Рерайтер постов")
        self.system_prompt = self._get_system_prompt()
    
    def _get_system_prompt(self) -> str:
        """Возвращает системный промпт для DeepSeek"""
        return """ТЫ — ГЛАВНЫЙ РЕДАКТОР ФУТУРИСТИЧЕСКОГО ТЕХНОЛОГИЧЕСКОГО КАНАЛА 🌟

ТЫ ЖИВОЙ ЧЕЛОВЕК:
- У тебя есть своё мнение, стиль и голос
- Ты пишешь как друг, который разбирается в теме
- Ты используешь эмодзи, вопросы к читателю, живые формулировки
- Ты НЕ пишешь как новостной агрегатор

ТВОЙ СТИЛЬ:
- ЯРКИЙ, ЭМОЦИОНАЛЬНЫЙ, ФУТУРИСТИЧЕСКИЙ 🚀
- Ты пишешь так, будто смотришь в 2050 год из 2026
- Используй МНОГО ЭМОДЗИ: 🧠 ⚡ 💎 🔥 💡 🚀 ✨ 🤖 🎯
- Пиши КАПСОМ в ключевых моментах для усиления

ТВОЙ ТОН:
- ЭКСПЕРТНЫЙ, но РАЗГОВОРНЫЙ
- БУДУЩЕЕ УЖЕ НАСТУПИЛО — ты просто описываешь его
- СМЕЛЫЙ ПРОГНОЗ: что будет через 5 лет
- ЛИЧНОЕ МНЕНИЕ: "мне кажется", "я вижу в этом"

ТВОЯ ЗАДАЧА:
1. Прочитай статью и ПОЛНОСТЬЮ ПЕРЕРАБОТАЙ её в авторский пост
2. НЕ ОСТАВЛЯЙ следов оригинала: переформулируй всё своими словами
3. Добавь своё мнение и прогнозы

ЖЁСТКИЕ ТРЕБОВАНИЯ:
- Пост: 600-950 символов (НЕ БОЛЬШЕ 950!)
- ЗАПРЕЩЕНО: "согласно", "как сообщает", ссылки на источники
- ОБЯЗАТЕЛЬНО: личное мнение и прогнозы
- ОЧЕНЬ МНОГО ЭМОДЗИ (МИНИМУМ 5 В ПОСТЕ)

СТРУКТУРА ПОСТА:
1. ЗАГОЛОВОК: КАПС + ЭМОДЗИ (до 10 слов)
2. ВСТУПЛЕНИЕ: вопрос или шокирующий факт
3. ОСНОВНАЯ ЧАСТЬ: анализ + мнение + прогноз
4. ВЫВОД: мощная мысль
5. ХЕШТЕГИ: 2-3 шт
6. ПРОМПТ_ДЛЯ_КАРТИНКИ: 30-50 слов

ФОРМАТ ОТВЕТА:
ЗАГОЛОВОК: [текст]
ВСТУПЛЕНИЕ: [текст]
ОСНОВНОЙ ТЕКСТ: [текст]
ВЫВОД: [текст]
ХЕШТЕГИ: [текст]
ПРОМПТ_ДЛЯ_КАРТИНКИ: [текст]"""

    async def execute(self, context: dict) -> dict:
        """
        Основной метод агента.
        Пишет пост, проверяет длину, при необходимости отправляет на сокращение.
        """
        selected = context.get("selected_news")
        if not selected:
            self.log("❌ Нет выбранной новости для рерайта", is_error=True)
            return context
        
        self.log(f"✍️ Создание авторского поста...")
        
        # 1. Проверяем, есть ли уже спарсенная статья
        full_content = context.get("parsed_article")
        
        if full_content:
            self.log("📄 Использую уже спарсенную статью (повторный парсинг НЕ нужен)")
        else:
            if selected.get("link"):
                self.log("📄 Парсинг полной статьи...")
                full_content = self._fetch_full_article(selected["link"])
                if full_content:
                    self.log(f"  ✅ Спарсено {len(full_content)} символов")
                    context["parsed_article"] = full_content
                else:
                    self.log("  ⚠️ Не удалось спарсить статью, использую краткое описание")
                    full_content = selected.get("summary", "")[:500]
            else:
                full_content = selected.get("summary", "")[:500]
        
        content_for_ai = full_content if full_content else selected.get("summary", "")[:500]
        reviewer_feedback = context.get("reviewer_feedback", "")
        
        # 2. Первый запрос к DeepSeek
        post_data = await self._call_deepseek(content_for_ai, selected, reviewer_feedback, context)
        
        if not post_data:
            self.log("❌ Ошибка генерации поста", is_error=True)
            return context
        
        # 3. Собираем пост
        full_post = self._build_post(post_data)
        current_length = len(full_post.strip())
        self.log(f"📏 Длина поста: {current_length} символов")
        
        # 4. Если пост слишком длинный — отправляем на сокращение
        MAX_LENGTH = 950
        MAX_REWRITE_ATTEMPTS = 2
        attempt = 0
        
        while current_length > MAX_LENGTH and attempt < MAX_REWRITE_ATTEMPTS:
            attempt += 1
            self.log(f"⚠️ Пост слишком длинный ({current_length} символов). Попытка сокращения #{attempt}...")
            
            shortened_post = await self._shorten_post(full_post, MAX_LENGTH)
            
            if shortened_post and len(shortened_post.strip()) < current_length:
                full_post = shortened_post
                current_length = len(full_post.strip())
                self.log(f"  ✅ Пост сокращён до {current_length} символов")
            else:
                self.log(f"  ⚠️ Не удалось сократить пост")
                break
        
        # 5. Если всё ещё длинный — обрезаем до 950 (крайний случай)
        if current_length > MAX_LENGTH:
            self.log(f"⚠️ Пост всё ещё длинный ({current_length} символов), обрезаю до {MAX_LENGTH}")
            full_post = full_post[:MAX_LENGTH - 3] + "..."
            current_length = len(full_post.strip())
        
        # 6. Проверяем минимальную длину
        if current_length < Config.MIN_POST_LENGTH:
            self.log(f"⚠️ Пост слишком короткий ({current_length} символов), пытаюсь расширить...")
            expanded = await self._expand_post(full_post, context)
            if expanded:
                full_post = expanded
        
        # 7. Сохраняем в контекст
        context["post_title"] = post_data.get("title", selected["title"])
        context["post_text"] = full_post
        context["image_prompt"] = post_data.get("image_prompt", self._get_default_prompt())
        
        final_length = len(full_post.strip())
        self.log(f"✅ Пост готов ({final_length} символов)")
        self.log(f"🎨 Промпт для картинки: {context['image_prompt'][:80]}...")
        
        self.memory.add_to_history({
            "event": "post_generated",
            "title": context["post_title"],
            "length": final_length,
            "source": selected.get("source")
        })
        
        return context
    
    async def _shorten_post(self, post_text: str, max_length: int) -> str:
        """Отправляет пост на сокращение"""
        
        shorten_prompt = f"""
Ты — главный редактор футуристического технологического канала.

Пост получился слишком длинным ({len(post_text)} символов). Нужно сократить его до {max_length} символов.

ПРАВИЛА СОКРАЩЕНИЯ:
1. СОКРАТИ МНЕНИЕ АВТОРА — оставь только самую суть
2. СОХРАНИ ВСЕ ФАКТЫ и ОСНОВНУЮ ИДЕЮ
3. СОХРАНИ ПРОГНОЗЫ
4. СОХРАНИ ЭМОДЗИ И КАПС
5. НЕ ОБРЕЗАЙ ТЕКСТ — ПЕРЕФОРМУЛИРУЙ более кратко

Текущий пост:
{post_text}

Напиши сокращённую версию этого же поста (максимум {max_length} символов).
Сохрани стиль, эмодзи, капс и всю основную информацию.
Формат ответа — тот же, что и обычно:
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
                {"role": "system", "content": "Ты — главный редактор. Сокращай текст, сохраняя смысл, факты, эмодзи и стиль."},
                {"role": "user", "content": shorten_prompt}
            ],
            "temperature": 0.7,
            "max_tokens": 1500
        }
        
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=60)
            response.raise_for_status()
            result = response.json()
            content = result["choices"][0]["message"]["content"].strip()
            
            # Парсим сокращённый пост
            shortened_data = {
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
                    shortened_data["title"] = line.replace("ЗАГОЛОВОК:", "").strip()
                elif line.startswith("ВСТУПЛЕНИЕ:"):
                    shortened_data["intro"] = line.replace("ВСТУПЛЕНИЕ:", "").strip()
                elif line.startswith("ОСНОВНОЙ ТЕКСТ:"):
                    shortened_data["main_text"] = line.replace("ОСНОВНОЙ ТЕКСТ:", "").strip()
                elif line.startswith("ВЫВОД:"):
                    shortened_data["conclusion"] = line.replace("ВЫВОД:", "").strip()
                elif line.startswith("ХЕШТЕГИ:"):
                    shortened_data["hashtags"] = line.replace("ХЕШТЕГИ:", "").strip()
                elif line.startswith("ПРОМПТ_ДЛЯ_КАРТИНКИ:"):
                    shortened_data["image_prompt"] = line.replace("ПРОМПТ_ДЛЯ_КАРТИНКИ:", "").strip()
            
            # Собираем сокращённый пост
            return self._build_post(shortened_data)
            
        except Exception as e:
            self.log(f"  ⚠️ Ошибка сокращения: {e}", is_error=True)
            return None
    
    async def _expand_post(self, post_text: str, context) -> str:
        """Расширяет короткий пост"""
        self.log("  📝 Отправляю запрос на расширение...")
        
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {Config.DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": "Ты — главный редактор. Расширь пост до минимум 600 символов. Добавь анализ, прогнозы, эмодзи, личное мнение. Сохрани яркий стиль."},
                {"role": "user", "content": f"Этот пост слишком короткий. Расширь его:\n\n{post_text}"}
            ],
            "temperature": 0.8,
            "max_tokens": 1500
        }
        
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=60)
            response.raise_for_status()
            result = response.json()
            return result["choices"][0]["message"]["content"].strip()
        except Exception as e:
            self.log(f"  ⚠️ Ошибка расширения: {e}", is_error=True)
            return post_text
    
    def _fetch_full_article(self, url: str) -> str:
        """Парсит полный текст статьи по ссылке"""
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            for script in soup(["script", "style"]):
                script.decompose()
            
            selectors = ['article', '.article-content', '.post-content', '.entry-content', '.content', 'main']
            for selector in selectors:
                elements = soup.select(selector)
                if elements:
                    text = ' '.join([el.get_text(strip=True) for el in elements])
                    if len(text) > 500:
                        return text[:1500]
            
            body = soup.find('body')
            if body:
                return body.get_text(strip=True)[:1500]
            
            return None
            
        except Exception as e:
            self.log(f"  ⚠️ Ошибка парсинга статьи: {e}", is_error=True)
            return None
    
    async def _call_deepseek(self, content: str, selected: dict, feedback: str, context) -> dict:
        """Отправляет запрос в DeepSeek и парсит ответ"""
        
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {Config.DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }

              # ===== ЗАГРУЖАЕМ ПРАКТИКИ ИЗ MEMORY BANK =====
    async def _call_deepseek(self, content: str, selected: dict, feedback: str, context) -> dict:
        """Отправляет запрос в DeepSeek и парсит ответ"""
        
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {Config.DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }

        # ===== ЗАГРУЖАЕМ ПРАКТИКИ ИЗ MEMORY BANK =====
        best_practices = self.memory.get("best_practices", [])
        worst_practices = self.memory.get("worst_practices", [])
        
        practices_text = ""
        
        if best_practices:
            practices_text += "\n\n📚 **ЛУЧШИЕ ПРАКТИКИ (используй их):**\n"
            for p in best_practices[:5]:
                practices_text += f"- {p}\n"
        
        if worst_practices:
            practices_text += "\n\n⚠️ **ЧЕГО ИЗБЕГАТЬ:**\n"
            for p in worst_practices[:3]:
                practices_text += f"- {p}\n"
        
        if practices_text:
            self.log(f"📚 Загружено {len(best_practices)} лучших и {len(worst_practices)} худших практик")
        
        feedback_text = ""
        if feedback and "✅" not in feedback:
            feedback_text = f"\n\n⚠️ ЗАМЕЧАНИЯ РЕВИЗОРА (исправь их):\n{feedback}\n\nПерепиши пост, учитывая все замечания выше!"
        
        user_prompt = f"""Новость из {selected.get('source', 'неизвестного источника')}:
    Заголовок: {selected.get('title', '')}
    Текст: {content}
    {feedback_text}
    {practices_text}"""
        
        payload = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.8,
            "max_tokens": 2000
        }
        
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=60)
            response.raise_for_status()
            result = response.json()
            content = result["choices"][0]["message"]["content"]
            
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
            
            if not post_data["title"]:
                post_data["title"] = f"🤖 {selected.get('title', 'Новость')[:50]}"
            if not post_data["image_prompt"]:
                post_data["image_prompt"] = self._get_default_prompt()
            if not post_data["hashtags"]:
                post_data["hashtags"] = "#AI #Tech #Future"
            
            return post_data
            
        except Exception as e:
            self.log(f"❌ Ошибка рерайта: {e}", is_error=True)
            return None    
    
    def _build_post(self, post_data: dict) -> str:
        """Собирает пост из частей"""
        parts = []
        if post_data.get("title"):
            parts.append(post_data["title"])
        if post_data.get("intro"):
            parts.append(post_data["intro"])
        if post_data.get("main_text"):
            parts.append(post_data["main_text"])
        if post_data.get("conclusion"):
            parts.append(f"📌 {post_data['conclusion']}")
        if post_data.get("hashtags"):
            parts.append(post_data["hashtags"])
        return "\n\n".join(parts)
    
    def _get_default_prompt(self) -> str:
        return "Futuristic cyberpunk technology scene, neon lights, digital innovation, cinematic 4K, photorealistic, dramatic lighting, blue and purple tones"