# agents/reviewer_agent.py

"""
АГЕНТ-РЕВИЗОР
Проверяет качество поста по жесткому чек-листу.
Возвращает замечания, если пост не прошёл проверку.
"""

import re
from .base_agent import BaseAgent
from config import Config


# Диапазон эмодзи. Изначально здесь был только \U0001F600-\U0001F9FF
# (смайлики), из-за чего ревизор НЕ видел 🎧 💡 📌 ⚠️ 🔥 📰 📊 — то есть почти
# все эмодзи, которые ставит рерайтер, — и браковал посты за «мало эмодзи»,
# хотя они там были. Покрываем пиктограммы, символы, стрелки и вариации.
EMOJI_PATTERN = re.compile(
    r'[\U0001F300-\U0001FAFF'   # пиктограммы, транспорт, лица, предметы
    r'\u2600-\u27BF'            # символы и дингбаты (☀ ✅ ➡)
    r'\u2B00-\u2BFF'            # стрелки и фигуры (⬛ ⭐)
    r'\u2190-\u21FF'            # стрелки (← →)
    r'\uFE0F\u200D]'            # вариационный селектор и ZWJ
)


def count_emojis(text: str) -> int:
    """Считает эмодзи в тексте с учётом реальных диапазонов Unicode."""
    return len(EMOJI_PATTERN.findall(text or ""))


class ReviewerAgent(BaseAgent):
    """Агент для проверки качества постов"""
    
    def __init__(self):
        super().__init__("Ревизор постов")
    
    @staticmethod
    def _title_similarity(a: str, b: str) -> float:
        """
        Доля общих значимых слов между двумя заголовками (0..1).

        Сравниваем по корням длиной от 5 символов: «превратить» и «преврати»
        должны считаться совпадением. Служебные слова игнорируем — они дают
        ложное сходство при разных темах.
        """
        import re as _re
        stop = {"как", "что", "для", "the", "and", "with", "your", "you",
                "этот", "этом", "свой", "свои", "тебе", "себя", "него", "чем"}

        def roots(text: str):
            words = _re.findall(r"[A-Za-zА-Яа-яЁё]{5,}", (text or "").lower())
            return {w[:6] for w in words if w not in stop}

        ra, rb = roots(a), roots(b)
        if not ra or not rb:
            return 0.0
        return len(ra & rb) / min(len(ra), len(rb))
    
    async def execute(self, context: dict) -> dict:
        """
        1. Берёт пост из context["post_text"]
        2. Проверяет по чек-листу, зависящему от канала
        3. Если всё хорошо → ставит text_approved = True
        4. Если есть проблемы → возвращает замечания в context["reviewer_feedback"]

        Чек-лист разный для каналов. Новостной канал требует КАПС, эмодзи и
        прогноз; канал лайфхаков пишет инструкции, где КАПС и прогнозы прямо
        запрещены промптом. Общий чек-лист отбраковывал бы каждый лайфхак,
        и оркестратор впустую выжигал бы все попытки рерайта.
        """
        from channels import get_profile, NEWS
        profile = context.get("channel_profile") or get_profile(NEWS)
        is_tips = profile.key == "tips"

        post_text = context.get("post_text", "")
        post_title = context.get("post_title", "")
        
        if not post_text:
            self.log("❌ Нет текста для проверки", is_error=True)
            context["text_approved"] = False
            context["reviewer_feedback"] = "Нет текста для проверки"
            context["reviewer_issues_count"] = 1
            return context
        
        self.log(f"📋 Проверка поста для «{profile.title}»...")
        
        # Список замечаний
        issues = []
        
        # ==========================================
        # 1. ПРОВЕРКА ДЛИНЫ
        # ==========================================
        # Лимиты берём из профиля канала: у лайфхаков пост длиннее, потому что
        # инструкции нужно место на шаги, пример и оговорку.
        min_length = getattr(profile, "min_length", Config.MIN_POST_LENGTH)
        max_length = getattr(profile, "max_length", Config.MAX_POST_LENGTH)
        length = len(post_text.strip())
        if length < min_length:
            issues.append(f"🔴 Пост слишком короткий: {length} символов (минимум {min_length})")
        elif length > max_length:
            issues.append(f"🔴 Пост слишком длинный: {length} символов (максимум {max_length})")
        
        # ==========================================
        # 2. ПРОВЕРКА ЗАГОЛОВКА
        # ==========================================
        if post_title:
            if is_tips:
                # Заголовок лайфхака должен обещать пользу, а не кричать КАПСОМ.
                # Слишком длинный заголовок не помещается в превью Telegram.
                if len(post_title) > 90:
                    issues.append(f"🔴 Заголовок слишком длинный: {len(post_title)} символов (максимум 90)")
                if not re.search(r'[а-яА-ЯёЁ]', post_title):
                    issues.append("🔴 Заголовок должен быть на русском языке")
            else:
                # Проверяем, есть ли в заголовке капс (минимум 2 слова в верхнем регистре)
                words = post_title.split()
                caps_count = sum(1 for w in words if w.isupper() and len(w) > 2)
                if caps_count < 2:
                    issues.append("🔴 Заголовок должен содержать минимум 2 слова КАПСОМ")
                
                # Проверяем наличие эмодзи в заголовке
                emoji_in_title = count_emojis(post_title)
                if emoji_in_title < 1:
                    issues.append("🔴 Заголовок должен содержать хотя бы 1 эмодзи")
        
        # ==========================================
        # 3. ПРОВЕРКА ЭМОДЗИ В ПОСТЕ
        # ==========================================
        emoji_count = count_emojis(post_text)
        # У лайфхаков порог ниже: промпт просит 3-5 эмодзи для структуры,
        # а не «минимум 5» для эмоционального усиления
        min_emoji = 2 if is_tips else 3
        if emoji_count < min_emoji:
            issues.append(f"🔴 Мало эмодзи в посте: {emoji_count} (минимум {min_emoji})")
        
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
        # 5. ПРОВЕРКА ПРОГНОЗА (только для новостного канала)
        # ==========================================
        # Лайфхак — это инструкция, прогнозы в ней запрещены промптом,
        # поэтому требовать их бессмысленно.
        if not is_tips:
            forecast_keywords = [
                "через", "скоро", "в будущем", "в ближайшие", 
                "предстоит", "изменится", "станет", "будет"
            ]
            has_forecast = any(keyword in post_text.lower() for keyword in forecast_keywords)
            if not has_forecast:
                issues.append("🔴 Нет прогноза. Добавь 'в будущем', 'через 5 лет' или аналоги")
        else:
            # Вместо прогноза у лайфхака требуем практическую ценность:
            # конкретное действие, инструмент или результат.
            actionable = [
                "шаг", "промпт", "попробуй", "открой", "напиши", "загрузи",
                "сервис", "инструмент", "бесплатн", "подписк", "настрой",
                "можно", "как ", "способ", "пример"
            ]
            if not any(word in post_text.lower() for word in actionable):
                issues.append(
                    "🔴 Нет практической части: опиши, что именно делать "
                    "(шаги, промпт, инструмент или пример)"
                )
        
        # ==========================================
        # 6. ПРОВЕРКА ЗАПРЕЩЁННЫХ СЛОВ
        # ==========================================
        forbidden_words = ["согласно", "как сообщает", "по данным", "источник сообщает"]
        if is_tips:
            # Для лайфхаков дополнительно запрещены новостные штампы:
            # они превращают инструкцию обратно в новость
            forbidden_words = forbidden_words + [
                "инновационный", "революционный", "прорывной",
                "в этом году компания", "рынок", "инвестиц"
            ]
        forbidden_found = [word for word in forbidden_words if word in post_text.lower()]
        if forbidden_found:
            issues.append(f"🔴 Найдены запрещённые слова: {', '.join(forbidden_found)}. Переформулируй.")
        
        # ==========================================
        # 6.1. ПРОВЕРКА СЛЕДОВ ИСТОЧНИКА
        # ==========================================
        # Пост должен читаться как авторский текст. Ссылка, имя сайта или
        # обороты «в статье» сразу выдают, что материал спарсен из чужой
        # публикации — а это видно и читателю, и поисковикам.
        source_markers = [
            "http://", "https://", "www.",
            "в статье", "в публикации", "в материале", "в источнике",
            "как пишет", "как пишут", "по сообщению", "сообщается",
            "издание", "портал", "автор статьи", "оригинал статьи",
            "подробнее по ссылке", "читать далее",
        ]
        marks_found = [m for m in source_markers if m in post_text.lower()]
        if marks_found:
            preview = ", ".join(marks_found[:4])
            issues.append(
                f"🔴 Видны следы источника ({preview}). Убери ссылку, название "
                f"сайта и обороты «в статье» — пост должен читаться как авторский"
            )
        
        # ==========================================
        # 6.2. ПОХОЖ ЛИ ЗАГОЛОВОК НА ОРИГИНАЛ
        # ==========================================
        # Буквальный перевод чужого заголовка делает пост узнаваемым при
        # сравнении с публикацией-источником. Сравниваем по значимым словам:
        # если больше половины совпадает — это пересказ, а не свой заголовок.
        original_title = (context.get("selected_news") or {}).get("title", "")
        if post_title and original_title:
            sim = self._title_similarity(post_title, original_title)
            if sim >= 0.6:
                issues.append(
                    f"🔴 Заголовок слишком близок к заголовку источника "
                    f"(совпадение {sim:.0%}). Придумай свой — с другим углом "
                    f"и другими словами"
                )
        
        # ==========================================
        # 7. ПРОВЕРКА СТРУКТУРЫ (абзацы)
        # ==========================================
        paragraphs = post_text.split('\n\n')
        if len(paragraphs) < 2:
            issues.append("🔴 Пост должен содержать минимум 2 абзаца")
        
        # ==========================================
        # 8. ПРОВЕРКА ЯЗЫКА (для обоих каналов)
        # ==========================================
        # Иностранные источники должны пересказываться по-русски.
        # Считаем долю латиницы: отдельные названия продуктов нормальны,
        # но текст, написанный в основном латиницей, — это не пересказ.
        letters = re.findall(r'[A-Za-zА-Яа-яЁё]', post_text)
        if letters:
            latin_share = len(re.findall(r'[A-Za-z]', post_text)) / len(letters)
            if latin_share > 0.5:
                issues.append(
                    f"🔴 Пост написан не по-русски (латиницы {latin_share:.0%}). "
                    f"Переведи и переработай текст на русский"
                )
        
        # ==========================================
        # ИТОГОВЫЙ ВЕРДИКТ
        # ==========================================
        if issues:
            context["text_approved"] = False
            context["reviewer_feedback"] = "\n".join(issues)
            context["reviewer_issues_count"] = len(issues)
            self.log(f"❌ Пост не прошёл проверку: {len(issues)} замечаний")
            for issue in issues[:3]:
                self.log(f"  • {issue}")
            if len(issues) > 3:
                self.log(f"  • ... и ещё {len(issues)-3} замечаний")
        else:
            context["text_approved"] = True
            context["reviewer_feedback"] = "✅ Все проверки пройдены"
            context["reviewer_issues_count"] = 0
            self.log("✅ Пост прошёл все проверки")
        
        # Сохраняем в Memory Bank
        self.memory.add_to_history({
            "event": "review_completed",
            "status": "approved" if context["text_approved"] else "rejected",
            "issues_count": len(issues),
            "length": len(post_text.strip())
        })
        
        return context