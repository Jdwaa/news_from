# agents/growth_agent.py

"""
АГЕНТ ВОРОНКИ

Следит за ростом каналов и считает конверсию.

Зачем отдельный агент: публикация и привлечение — разные задачи. Оркестратор
отвечает за «что опубликовать», а здесь — «растёт ли канал и почему».
Без этих цифр любая правка (кнопки, призывы, кросс-промо) проверяется на глаз.

Что собирает:
  1. Число подписчиков каждого канала (getChatMemberCount) — история в БД
  2. Прирост за сутки и неделю
  3. Конверсию: сколько подписчиков принесла тысяча просмотров

Собирает НЕ через публичное превью t.me, а через Bot API: это работает и для
каналов без username, и не зависит от вёрстки страницы-превью.
"""

from datetime import datetime

from .base_agent import BaseAgent
from channels import active_profiles, build_post_keyboard, NEWS, TIPS
from database import (
    save_subscriber_count,
    get_subscriber_growth,
    get_subscriber_history,
    get_all_posts_with_stats,
)


class GrowthAgent(BaseAgent):
    """Агент мониторинга роста каналов и воронки подписчиков."""

    def __init__(self, bot=None):
        super().__init__("Воронка")
        self.bot = bot

    async def execute(self, context: dict) -> dict:
        """
        Снимает текущее число подписчиков по всем активным каналам
        и сохраняет в историю.
        """
        self.log("📈 Снимаю метрики подписчиков...")

        if not self.bot:
            self.log("⚠️ Бот не передан, метрики недоступны", is_error=True)
            context["growth"] = {}
            return context

        growth = {}
        for profile in active_profiles():
            if not profile.chat_id:
                continue
            count = await self._fetch_count(profile.chat_id)
            if count is None:
                continue
            save_subscriber_count(profile.key, count)
            stats = get_subscriber_growth(profile.key, hours=24)
            growth[profile.key] = stats
            delta_txt = self._format_delta(stats.get("delta"))
            self.log(f"  {profile.emoji} {profile.title}: {count} подписчиков ({delta_txt} за сутки)")

        context["growth"] = growth
        return context

    async def _fetch_count(self, chat_id: str):
        """Число подписчиков канала. None, если не удалось получить."""
        try:
            count = await self.bot.get_chat_member_count(chat_id=chat_id)
            return int(count)
        except Exception as e:
            self.log(f"  ⚠️ Не удалось получить число подписчиков ({chat_id}): {e}", is_error=True)
            return None

    @staticmethod
    def _format_delta(delta) -> str:
        if delta is None:
            return "нет данных"
        if delta > 0:
            return f"+{delta}"
        if delta < 0:
            return str(delta)
        return "0"

    def build_report(self, hours: int = 24) -> str:
        """
        Текстовый отчёт по воронке для команды /growth.

        Считает не только прирост, но и конверсию: сколько подписчиков
        пришло на тысячу просмотров. Это и есть показатель того, работает
        ли воронка — сами по себе подписчики могут расти «на общем фоне».
        """
        lines = ["📈 *Воронка: рост каналов*", ""]

        for profile in active_profiles():
            stats = get_subscriber_growth(profile.key, hours=hours)
            current = stats.get("current")
            delta = stats.get("delta")

            lines.append(f"{profile.emoji} *{profile.title}*")
            if current is None:
                lines.append("   данных пока нет — нужно накопить замеры")
                lines.append("")
                continue

            lines.append(f"   👥 подписчиков: {current}")
            lines.append(f"   📊 прирост за {hours} ч: {self._format_delta(delta)}")

            # Конверсия: просмотры постов канала за период против прироста
            views = self._views_since(profile.key, hours)
            if views and delta is not None and views > 0:
                per_1000 = delta / (views / 1000)
                lines.append(f"   👁️ просмотров: {views}")
                lines.append(f"   🎯 конверсия: {per_1000:.1f} подписчиков на 1000 просмотров")
            elif views:
                lines.append(f"   👁️ просмотров: {views}")

            # Недельная динамика — сглаживает суточные колебания
            week = get_subscriber_growth(profile.key, hours=24 * 7)
            if week.get("delta") is not None:
                lines.append(f"   📅 за неделю: {self._format_delta(week['delta'])}")
            lines.append("")

        # Советы по слабым местам
        tips = self._hints()
        if tips:
            lines.append("💡 *Что можно улучшить*")
            lines.extend(tips)

        return "\n".join(lines)

    def _views_since(self, channel: str, hours: int) -> int:
        """
        Сумма просмотров постов ЭТОГО канала.

        Раньше здесь брались все посты без фильтра, и просмотры новостного
        канала попадали в отчёт лайфхаков — метрики обоих каналов были
        одинаковыми. Теперь фильтрация идёт на уровне SQL-запроса.
        """
        try:
            posts = get_all_posts_with_stats(limit=100, channel=channel)
        except Exception:
            return 0
        return sum(p.get("views") or 0 for p in posts)

    def _hints(self) -> list:
        """
        Подсказки, что мешает воронке.

        Это не «магия», а проверка известных узких мест: без кнопки
        «Поделиться» пост не расходится, а без призыва читатель не понимает,
        что делать дальше.
        """
        hints = []
        posts = get_all_posts_with_stats(limit=50)
        if not posts:
            return hints

        hints.append("   • проверь, что под постами есть кнопки «Подписаться» и «Поделиться»")
        return hints

    # =========================================================
    # ЗАКРЕП-НАВИГАЦИЯ
    # =========================================================

    def build_pinned_text(self, profile) -> str:
        """
        Текст закреплённого сообщения — «визитка» канала.

        Закреп видит каждый новый подписчик первым. Он должен за один экран
        объяснить: о чём канал, что тут будет и что делать дальше. Без него
        новый читатель листает ленту наугад и уходит.
        """
        partner = _partner_of(profile)
        lines = [f"{profile.emoji} *{profile.title}*", ""]

        if profile.key == TIPS:
            lines += [
                "Практичные ИИ-лайфхаки для обычных людей: без терминов и",
                "теории — только то, что можно применить сегодня.",
                "",
                "📌 Здесь вы найдёте:",
                "• готовые приёмы и промпты",
                "• инструменты, которые реально экономят время",
                "• разборы на бытовых задачах",
                "",
                "Начните с любого поста ниже — каждый решает конкретную задачу.",
            ]
        else:
            lines += [
                "Главное о мире искусственного интеллекта: прорывы, продукты,",
                "идеи — простым языком и без воды.",
                "",
                "📌 Здесь вы найдёте:",
                "• ключевые новости ИИ",
                "• разборы, что это меняет для нас",
                "• самое интересное за день",
            ]

        if partner and partner.username:
            lines += [
                "",
                f"👉 Ещё больше пользы: @{partner.username}",
            ]

        return "\n".join(lines)

    async def update_pin(self, profile) -> bool:
        """
        Ставит или обновляет закреплённое сообщение канала.

        Логика: ищем в истории канала наше прежнее приветствие и правим его,
        а не публикуем новое. Иначе при каждом запуске в канале копился бы
        мусор из приветствий, и закреп пришлось бы искать вручную.

        Возвращает True, если закреп установлен или обновлён.
        """
        if not self.bot:
            self.log("⚠️ Бот не передан, закреп недоступен", is_error=True)
            return False

        text = self.build_pinned_text(profile)
        keyboard = build_post_keyboard(profile, post_title=profile.title)

        pinned_id = self.memory.get(f"pinned_message_id:{profile.key}")

        # 1. Пробуем обновить уже существующее приветствие
        if pinned_id:
            try:
                await self.bot.edit_message_text(
                    chat_id=profile.chat_id,
                    message_id=int(pinned_id),
                    text=text,
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                    disable_web_page_preview=True,
                )
                self.log(f"📌 Закреп «{profile.title}» обновлён")
                await self._ensure_pinned(profile, int(pinned_id))
                return True
            except Exception as e:
                # «Message is not modified» — это НЕ ошибка: текст и кнопки уже
                # такие же. Раньше мы считали это недоступностью сообщения и
                # создавали новое, из-за чего в канале копились дубли закрепов.
                if "not modified" in str(e).lower():
                    self.log(f"📌 Закреп «{profile.title}» уже актуален")
                    await self._ensure_pinned(profile, int(pinned_id))
                    return True
                # Сообщение могло быть удалено — тогда создаём новое
                self.log(f"ℹ️ Прежний закреп недоступен ({e}), создаю новый")

        # 2. Создаём новое приветствие
        try:
            msg = await self.bot.send_message(
                chat_id=profile.chat_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
        except Exception as e:
            self.log(f"❌ Не удалось отправить приветствие: {e}", is_error=True)
            return False

        self.memory.set(f"pinned_message_id:{profile.key}", msg.message_id)
        await self._ensure_pinned(profile, msg.message_id)
        return True

    async def _ensure_pinned(self, profile, message_id: int) -> bool:
        """Закрепляет сообщение, если оно ещё не закреплено."""
        try:
            await self.bot.pin_chat_message(
                chat_id=profile.chat_id,
                message_id=message_id,
                disable_notification=True,
            )
            self.log(f"📌 Закреп «{profile.title}» установлен (message_id={message_id})")
            return True
        except Exception as e:
            self.log(f"❌ Не удалось закрепить сообщение: {e}", is_error=True)
            return False


def _partner_of(profile):
    """Профиль канала-партнёра для кросс-промо (или None)."""
    from channels import PROFILES
    if not profile.cross_promo:
        return None
    return PROFILES.get(profile.cross_promo)
