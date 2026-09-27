# agents/publisher_agent.py

"""
АГЕНТ-ПУБЛИКАТОР
Сохраняет пост в БД и отправляет в Telegram.
Если картинка не сгенерировалась — использует последнюю успешную из БД.
"""

import asyncio
import os
import requests
from datetime import datetime
from .base_agent import BaseAgent
from database import (
    save_post,
    update_post_image,
    update_post_status,
    update_post_message_id,
    get_recent_post_links,
    get_placeholder_image,
    get_last_image_url,
)
from config import Config
from channels import get_profile, NEWS, build_post_keyboard
from net_utils import retry_sync
import image_store


class PublisherAgent(BaseAgent):
    """Агент для публикации постов в Telegram"""
    
    def __init__(self, bot=None):
        super().__init__("Публикатор постов")
        self.bot = bot
    
    async def execute(self, context: dict) -> dict:
        """
        1. Берёт пост из контекста
        2. Сохраняет в БД (с указанием канала)
        3. Если картинка есть — использует её
        4. Если нет — берёт последнюю успешную из БД
        5. Отправляет в Telegram канал, заданный профилем

        Канал берётся из context["channel_profile"] — один бот ведёт два канала,
        и адрес нельзя брать из Config.CHANNEL_ID напрямую.
        """
        profile = context.get("channel_profile") or get_profile(NEWS)
        context["channel"] = profile.key

        post_text = context.get("post_text")
        post_title = context.get("post_title", "")
        new_image_url = context.get("image_url")  # Картинка от ImageAgent
        # Локальная копия картинки: подписанная ссылка CDN живёт ~7 суток,
        # поэтому публикуем файл, а не URL.
        local_image_path = context.get("image_path")
        image_prompt = context.get("image_prompt", "")
        selected_news = context.get("selected_news", {})
        
        if not post_text:
            self.log("❌ Нет текста для публикации", is_error=True)
            return context
        
        self.log(f"📤 Сохранение и публикация поста в «{profile.title}»...")
        
        # ==========================================
        # 1. СОХРАНЯЕМ ПОСТ В БД
        # ==========================================
        link = selected_news.get("link", "")
        
        # Суточный дедуп по ссылке внутри канала: парсер проверяет всю таблицу,
        # а старые посты вычищаются из БД, поэтому одну и ту же новость можно
        # было опубликовать повторно. Дедуп ограничен каналом: новость, ушедшая
        # в новостной, не должна блокировать лайфхак по ней же.
        if link and link in get_recent_post_links(days=1, channel=profile.key):
            self.log(f"⚠️ Эта новость уже была в «{profile.title}» за сутки: {link[:60]}", is_error=True)
            context["duplicate_skipped"] = True
            return context
        
        post_id = save_post(post_title, link, post_text, image_prompt, new_image_url,
                            channel=profile.key)
        
        if not post_id:
            self.log("⚠️ Пост не сохранён (возможно, дубликат)", is_error=True)
            return context
        
        self.log(f"✅ Пост #{post_id} сохранён в БД [{profile.key}]")
        
        # ==========================================
        # 2. ВЫБИРАЕМ КАРТИНКУ ДЛЯ ПУБЛИКАЦИИ
        # ==========================================
        image_url_to_use = None
        # Файл, который удалим после успешной отправки: он больше не нужен,
        # картинка уже в Telegram, а место занимает.
        temp_file_to_remove = None
        
        # 2.1. Свежая картинка: локальный файл в приоритете, URL — запасной путь
        if local_image_path and os.path.exists(local_image_path):
            image_url_to_use = local_image_path
            temp_file_to_remove = local_image_path
            update_post_image(post_id, new_image_url or local_image_path)
            self.log("📸 Использую новую картинку")
        elif new_image_url:
            image_url_to_use = new_image_url
            update_post_image(post_id, new_image_url)
            self.log("📸 Использую новую картинку (по ссылке)")
        
        # 2.2. Если генерация не дала картинку — берём последний сохранённый
        # резерв. Именно для этого один файл и хранится: пост выйдет
        # с иллюстрацией, а не текстом, даже когда OdiRouter недоступен.
        if not image_url_to_use:
            self.log("🔄 Своей картинки нет, беру последнюю сохранённую...")
            reserved = image_store.latest_image()
            if reserved:
                image_url_to_use = reserved
                temp_file_to_remove = reserved
                # В БД пишем ссылку прошлого поста, чтобы Аналитик и статистика
                # не считали картинку отсутствующей
                previous_url = get_last_image_url()
                if previous_url and previous_url != get_placeholder_image():
                    update_post_image(post_id, previous_url)
                self.log(f"📸 Использую резервную картинку: {os.path.basename(reserved)}")
            else:
                # Резерва нет — публикуем текстом. Ссылку via.placeholder.com
                # Telegram скачать не может, поэтому лучше сразу без картинки.
                self.log("🔄 Резервной картинки нет — публикую текстом")
        
        # ==========================================
        # 3. ПУБЛИКАЦИЯ В TELEGRAM
        # ==========================================
        # Призыв к действию добавляем по вероятностной схеме, а не в каждый пост:
        # постоянный «подпишись» в конце читается как реклама и снижает доверие.
        # Целевой процент задаётся CTA_EVERY_N_POSTS (по умолчанию каждый третий).
        post_text = self._maybe_add_cta(post_text, profile, post_id)
        
        message_id = await self._publish_to_telegram(post_text, image_url_to_use, context)
        
        # ==========================================
        # 3.1. УБИРАЕМ ЛОКАЛЬНУЮ КОПИЮ ПОСЛЕ ОТПРАВКИ
        # ==========================================
        # Картинка уже в Telegram, локальный файл больше не нужен. Удаляем
        # только при УСПЕШНОЙ отправке: если пост не ушёл, резерв должен
        # остаться, иначе следующий пост выйдет без картинки.
        if message_id and temp_file_to_remove:
            if image_store.remove(temp_file_to_remove):
                self.log("🗑️ Локальная копия картинки удалена после публикации")
            else:
                self.log("⚠️ Не удалось удалить локальную копию картинки", is_error=True)
        elif not message_id:
            self.log("ℹ️ Пост не ушёл — оставляю картинку как резерв")
        
        # ==========================================
        # 4. ОБНОВЛЯЕМ СТАТУС
        # ==========================================
        update_post_status(post_id, 'published')
        # message_id нужен Аналитику, чтобы потом забрать статистику именно этого поста
        if message_id:
            update_post_message_id(post_id, message_id)
            self.log(f"🔖 message_id={message_id} сохранён (для сбора статистики)")
        else:
            self.log("⚠️ message_id не получен — статистику по этому посту собрать не удастся", is_error=True)
        
        context["published_post_id"] = post_id
        context["message_id"] = message_id
        context["published_at"] = datetime.now().isoformat()
        context["image_url_used"] = image_url_to_use
        
        # Сохраняем в Memory Bank
        self.memory.add_to_history({
            "event": "post_published",
            "post_id": post_id,
            "title": post_title[:50],
            "has_image": bool(image_url_to_use),
            "image_source": "new" if new_image_url else "db_fallback" if image_url_to_use else "none"
        })
        
        self.log(f"✅ Пост #{post_id} опубликован!")
        return context
    
    def _maybe_add_cta(self, post_text: str, profile, post_id: int) -> str:
        """
        Иногда добавляет в конец поста призыв перейти в канал-партнёр.

        Не в каждый пост: постоянный призыв выглядит как реклама и снижает
        доверие. Частота задаётся Config.CTA_EVERY_N_POSTS (0 — выключено).
        Считаем по post_id, а не случайно: так поведение воспроизводимо и
        его можно проверить тестом.
        """
        every = getattr(Config, "CTA_EVERY_N_POSTS", 3)
        if not every or every <= 0:
            return post_text
        if post_id % every != 0:
            return post_text

        cta = profile.cta_text()
        if not cta:
            return post_text

        # Проверяем, нет ли уже упоминания канала-партнёра: рерайтер мог
        # добавить его сам, и второй раз это будет выглядеть навязчиво.
        partner = profile.cross_promo
        if partner and f"@{partner}" in post_text:
            return post_text

        self.log(f"📣 Добавляю призыв к действию (в каждом {every}-м посте)")
        return f"{post_text}\n\n{cta}"

    async def _publish_to_telegram(self, post_text: str, image_url: str, context: dict):
        """
        Отправляет пост в Telegram-канал из профиля.
        Возвращает message_id опубликованного сообщения или None.
        """
        
        if not self.bot:
            self.log("⚠️ Бот не передан, публикация только в БД", is_error=True)
            return None
        
        profile = context.get("channel_profile") or get_profile(NEWS)
        chat_id = profile.chat_id
        
        if not chat_id:
            self.log(f"❌ У канала «{profile.title}» не задан адрес — публиковать некуда", is_error=True)
            return None
        
        # Кнопки воронки: «Подписаться», «Поделиться», «Источник».
        # Ссылка на источник берётся из выбранной новости.
        source_url = (context.get("selected_news") or {}).get("link", "")
        keyboard = build_post_keyboard(
            profile,
            post_title=context.get("post_title", ""),
            source_url=source_url,
        )
        if keyboard:
            self.log(f"🔗 Кнопки под постом: {len(keyboard.inline_keyboard)} ряд(ов)")
        else:
            self.log("ℹ️ Кнопки не добавлены (нет username канала или источника)")
        
        try:
            # Пробуем отправить с картинкой
            if image_url and image_url != get_placeholder_image():
                message_id = await self._send_with_image(post_text, image_url, chat_id, keyboard)
                if message_id:
                    self.log(f"✅ Пост опубликован с картинкой в «{profile.title}»")
                    return message_id
            
            result = await self.bot.send_message(
                chat_id=chat_id,
                text=post_text,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
            self.log(f"✅ Пост опубликован без картинки в «{profile.title}»")
            return result.message_id if result else None
            
        except Exception as e:
            self.log(f"❌ Ошибка публикации в «{profile.title}»: {e}", is_error=True)
            return None
    
    async def _send_with_image(self, post_text: str, image_url: str, chat_id: str,
                               keyboard=None):
        """
        Отправляет пост с картинкой в указанный чат.
        `image_url` может быть локальным путём к уже скачанному файлу.
        Возвращает message_id опубликованного сообщения или None.
        """
        # Локальный файл: ничего скачивать не нужно, отправляем как есть
        if os.path.exists(image_url):
            try:
                with open(image_url, "rb") as f:
                    result = await self.bot.send_photo(
                        chat_id=chat_id,
                        photo=f,
                        caption=post_text,
                        reply_markup=keyboard,
                    )
                return result.message_id if result else None
            except Exception as e:
                self.log(f"⚠️ Ошибка отправки локальной картинки: {e}", is_error=True)
                return None
        
        image_path = os.path.join("memory_bank", "temp_publish.jpg")
        try:
            # Качаем блоками с повтором оборвавшегося куска: CDN OdiRouter
            # отдаёт файл неровно, а обычный requests.get теряет весь прогресс
            # при обрыве на середине — пост тогда уходил без картинки.
            from agents.image_agent import ImageAgent
            content = await asyncio.to_thread(
                ImageAgent()._fetch_image_bytes, image_url
            )
            if not content:
                self.log("⚠️ Не удалось скачать картинку для отправки", is_error=True)
                return None

            with open(image_path, "wb") as f:
                f.write(content)
            
            with open(image_path, "rb") as f:
                result = await self.bot.send_photo(
                    chat_id=chat_id,
                    photo=f,
                    caption=post_text,
                    reply_markup=keyboard,
                )
            
            return result.message_id if result else None
            
        except Exception as e:
            self.log(f"⚠️ Ошибка отправки картинки: {e}", is_error=True)
            return None
        finally:
            # Временный файл удаляем всегда: он не резерв, а промежуточная копия
            if os.path.exists(image_path):
                os.remove(image_path)
