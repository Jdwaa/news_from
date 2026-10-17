# agents/rewriter_agent.py

"""
АГЕНТ-РЕРАЙТЕР
Переписывает новость в авторский футуристический пост.
Генерирует промпт для картинки.
Если пост слишком длинный — отправляет на повторный рерайт.
"""

import asyncio
import requests
from bs4 import BeautifulSoup
from .base_agent import BaseAgent
from config import Config
from net_utils import post_json


class RewriterAgent(BaseAgent):
    """Агент для рерайта новостей в авторские посты"""
    
    def __init__(self):
        super().__init__("Рерайтер постов")
        self.system_prompt = self._get_system_prompt()
    
    def _get_system_prompt(self, profile=None) -> str:
        """
        Системный промпт редактора: стиль берётся из профиля канала.

        Новостной канал и канал лайфхаков пишут по-разному: первому нужен
        яркий новостной пост, второму — инструкция «что мне с этого».
        Общий промпт делал бы оба канала одинаковыми.
        """
        from channels import get_profile, NEWS, UNIQUENESS_RULES
        profile = profile or get_profile(NEWS)
        max_length = getattr(profile, "max_length", Config.MAX_POST_LENGTH)
        min_length = getattr(profile, "min_length", Config.MIN_POST_LENGTH)
        return f"""{profile.style}

{UNIQUENESS_RULES}

ЖЁСТКИЕ ТРЕБОВАНИЯ:
- Пост: {min_length}-{max_length} символов (НЕ БОЛЬШЕ {max_length}!)
- ЯЗЫК: ТОЛЬКО РУССКИЙ. Если источник на английском, китайском или любом
  другом языке — переведи и переработай на русский. Английские слова и
  названия оставляй только там, где это имя продукта (ChatGPT, Notion).
- ЗАПРЕЩЕНО: "согласно", "как сообщает", ссылки на источники
- ОБЯЗАТЕЛЬНО: личное мнение и прогнозы
- ОЧЕНЬ МНОГО ЭМОДЗИ (МИНИМУМ 5 В ПОСТЕ)

СТРУКТУРА ПОСТА:
1. ЗАГОЛОВОК: КАПС + ЭМОДЗИ (до 10 слов)
2. ВСТУПЛЕНИЕ: факт, цифра или сцена — НЕ риторический вопрос.
   Чередуй зачины от поста к посту: цифра, контраст «было—стало»,
   короткий факт, реплика. Запрещены: «А вы знали?», «Знакомая картина?»,
   «Представьте…», «Бывало такое?», любой вопрос в первой строке.
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
ПРОМПТ_ДЛЯ_КАРТИНКИ: [текст]

Ответ начинается СРАЗУ со строки «ЗАГОЛОВОК:». Запрещено писать перед ней
что-либо: «Вот исправленный пост», «Вот конкретная информация без воды»,
«Учёл замечания», пояснения, приветствия, комментарии. Первая строка ответа —
всегда «ЗАГОЛОВОК:». Твой ответ — это готовый пост для канала, а не сообщение
собеседнику; в самом посте таких фраз быть не должно."""

    def _get_tips_prompt_suffix(self, profile=None) -> str:
        """
        Требования к структуре для канала лайфхаков.

        Отличается от новостной: вместо «анализ + прогноз» — конкретные шаги
        и оговорка, вместо «мощной мысли» — что сделать прямо сейчас.
        Лимит длины берётся из профиля канала: у лайфхаков он больше, иначе
        инструкция сжимается до «пресного» минимума.
        """
        from channels import tips_format_block
        min_length = getattr(profile, "min_length", Config.MIN_POST_LENGTH)
        max_length = getattr(profile, "max_length", Config.MAX_POST_LENGTH)
        return "\n" + tips_format_block(min_length, max_length)

    def _system_prompt_for(self, context: dict) -> str:
        """Системный промпт под канал из контекста (с учётом структуры)."""
        from channels import get_profile, NEWS
        profile = context.get("channel_profile") or get_profile(NEWS)
        prompt = self._get_system_prompt(profile)
        if profile.key == "tips":
            # Для лайфхаков структура другая: заменяем блок требований целиком.
            # Лимит длины берём из профиля — у лайфхаков он больше новостного.
            prompt = (prompt.split("ЖЁСТКИЕ ТРЕБОВАНИЯ:")[0]
                      + self._get_tips_prompt_suffix(profile))
        return prompt

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
                full_content = await asyncio.to_thread(self._fetch_full_article, selected["link"])
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
        
        # Лимиты берём из профиля канала: у лайфхаков он больше новостного,
        # иначе инструкция сжимается до «пресного» минимума.
        profile = context.get("channel_profile")
        MAX_LENGTH = getattr(profile, "max_length", Config.MAX_POST_LENGTH)
        MIN_LENGTH = getattr(profile, "min_length", Config.MIN_POST_LENGTH)
        
        # 4. Если пост слишком длинный — отправляем на сокращение
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
        
        # 5. Если всё ещё длинный — обрезаем до максимума (крайний случай)
        if current_length > MAX_LENGTH:
            self.log(f"⚠️ Пост всё ещё длинный ({current_length} символов), обрезаю до {MAX_LENGTH}")
            full_post = full_post[:MAX_LENGTH - 3] + "..."
            current_length = len(full_post.strip())
        
        # 6. Проверяем минимальную длину.
        # Расширяем настолько, чтобы попасть в цель с запасом: следующий агент
        # (редактор) может сократить текст, и без запаса пост окажется ниже
        # минимума уже после правки.
        if current_length < MIN_LENGTH:
            target = int(MIN_LENGTH * 1.4)
            self.log(f"⚠️ Пост слишком короткий ({current_length} символов, "
                     f"нужно {MIN_LENGTH}), расширяю до ~{target}...")
            expanded = await self._expand_post(full_post, context, target)
            if expanded:
                full_post = expanded
                current_length = len(full_post.strip())
                self.log(f"  ✅ Пост расширен до {current_length} символов")
        
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
            # to_thread: requests блокирующий, иначе он замораживает event loop бота
            # post_json добавляет повторы при временных сбоях DeepSeek
            result = await asyncio.to_thread(
                post_json, url, headers, payload, 60, 3
            )
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
    
    async def _expand_post(self, post_text: str, context, min_length: int = None) -> str:
        """
        Расширяет короткий пост.

        Инструкция расширения зависит от канала: для новостей просим анализ и
        прогнозы, для лайфхаков — конкретику (шаги, пример, промпт). Общий
        промпт добавлял бы в лайфхак «прогнозы», которые там запрещены, и
        ревизор такой пост отклонил бы.
        """
        self.log("  📝 Отправляю запрос на расширение...")
        
        from channels import get_profile, NEWS
        profile = context.get("channel_profile") or get_profile(NEWS)
        target = min_length or getattr(profile, "min_length", Config.MIN_POST_LENGTH)
        
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {Config.DEEPSEEK_API_KEY}",
            "Content-Type": "application/json"
        }
        
        if profile.key == "tips":
            system = (
                f"Ты — редактор канала практичных ИИ-лайфхаков. Расширь пост до "
                f"минимум {target} символов, НЕ ВЫДУМЫВАЯ факты. Добавляй только то, "
                f"что делает инструкцию полезнее: конкретные шаги, готовый промпт, "
                f"бытовой пример, оговорку про ограничения. Пиши живо и по-русски. "
                f"Запрещено: прогнозы про рынок, слова «инновационный» и «революционный»."
            )
        else:
            system = (
                f"Ты — главный редактор. Расширь пост до минимум {target} символов. "
                f"Добавь анализ, прогнозы, эмодзи, личное мнение. Сохрани яркий стиль."
            )
        
        payload = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": f"Этот пост слишком короткий. Расширь его:\n\n{post_text}"}
            ],
            "temperature": 0.8,
            "max_tokens": 2500
        }
        
        try:
            # to_thread: requests блокирующий; post_json добавляет повторы
            result = await asyncio.to_thread(
                post_json, url, headers, payload, 60, 3
            )
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
        best_practices = self.memory.get("best_practices", [])
        worst_practices = self.memory.get("worst_practices", [])
        
        practices_text = ""
        
        if not Config.USE_LEARNED_BEST_PRACTICES:
            self.log("⚙️ Учёт выученных практик выключен (USE_LEARNED_BEST_PRACTICES=false)")
        elif best_practices or worst_practices:
            # Практики выводятся из одной оценки (score) по просмотрам и реакциям,
            # поэтому «все посты с эмодзи хороши» — это шум, а не знание о тексте.
            # Даём их как мягкий сигнал и не заменяем ими основные требования.
            practices_text += "\n\n📎 НАБЛЮДЕНИЯ ПО СТАТИСТИКЕ КАНАЛА (учитывай как подсказку, не как догму):\n"
            for p in best_practices[:3]:
                practices_text += f"- {p}\n"
            for p in worst_practices[:2]:
                practices_text += f"- не делай так: {p}\n"
            
            self.log(f"📚 Подсказки из статистики: {len(best_practices[:3])} лучших, {len(worst_practices[:2])} худших")
        
        feedback_text = ""
        if feedback and "✅" not in feedback:
            # ВАЖНО: формулировка не должна выглядеть как реплика в диалоге.
            # «Вот замечания → перепиши пост» модель понимает буквально и
            # начинает ответ с «Вот исправленный пост…» — эта фраза попадает
            # в первую строку ОСНОВНОЙ ТЕКСТ и уезжает в канал.
            # Поэтому замечания даём как безличное «что учесть».
            feedback_text = (
                f"\n\nЧТО УЧЕСТЬ (требования к тексту, не комментарии):\n{feedback}"
            )
        
        # Источник НЕ передаём модели: иначе она вставляет «по данным ixbt.com»
        # или пересказывает ближе к оригиналу, выдавая, что текст спарсен.
        # Заголовок оригинала оставляем как ОРИЕНТИР по теме, но с явным
        # запретом копировать: буквальный перевод заголовка делает пост
        # узнаваемым при сравнении с публикацией-источником.
        user_prompt = f"""Материал для пересказа:
Тема (ориентир — НЕ копируй этот заголовок, придумай свой):
{selected.get('title', '')}
Текст: {content}
{feedback_text}
{practices_text}"""
        
        payload = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": self._system_prompt_for(context)},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.8,
            "max_tokens": 2000
        }
        
        try:
            # to_thread: requests блокирующий, иначе он замораживает event loop бота
            # post_json добавляет повторы при временных сбоях DeepSeek
            result = await asyncio.to_thread(
                post_json, url, headers, payload, 60, 3
            )
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
            
            # Срезаем «Вот исправленный пост…» и подобные отписки в начале
            # текстовых полей — они видны читателю и выдают машинную правку
            for field in ("title", "intro", "main_text", "conclusion"):
                post_data[field] = self._strip_preamble(post_data[field])
            
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
    
    # Начала фраз, которые модель пишет «собеседнику», а не в пост.
    # Они попадают в поле, если модель поставила их в одну строку с префиксом
    # (например «ОСНОВНОЙ ТЕКСТ: Вот исправленный вариант без воды:»).
    _PREAMBLE_MARKERS = (
        "вот исправленн", "вот конкретн", "вот пост", "вот вариант",
        "вот текст", "исправленный пост", "исправленная версия",
        "учёл замечания", "учел замечания", "с учётом замечаний",
        "без воды", "переписал", "перепишу", "готовый пост",
        "конечно", "разумеется", "понял", "принято",
    )

    def _strip_preamble(self, text: str) -> str:
        """
        Убирает служебную преамбулу из начала поля.

        Модель иногда отвечает как в диалоге: «Вот исправленный пост без воды:
        <текст>». Парсер берёт эту строку целиком вместе с префиксом, и фраза
        уезжает в канал. Здесь срезаем такие начала.
        """
        if not text:
            return text

        cleaned = text.strip()
        low = cleaned.lower()

        # Срезаем по маркеру, только если он стоит в самом начале строки
        for marker in self._PREAMBLE_MARKERS:
            if low.startswith(marker):
                # Отбрасываем до первого двоеточия — там обычно начинается
                # сам текст. Нет двоеточия → отбрасываем до конца фразы.
                colon = cleaned.find(":")
                if 0 < colon < 90:
                    cleaned = cleaned[colon + 1:].strip()
                else:
                    dot = cleaned.find(".")
                    if 0 < dot < 90:
                        cleaned = cleaned[dot + 1:].strip()
                    else:
                        return ""  # вся строка — отписка, текста нет
                self.log("🧹 Срезана служебная преамбула в начале поста")
                break

        # Двоеточие от самого префикса («ОСНОВНОЙ ТЕКСТ:») уже убрано парсером
        return cleaned

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