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
from channels import get_profile, NEWS


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
        
        # 2.1. Свежая картинка: локальный файл в приоритете, URL — запасной путь
        if local_image_path and os.path.exists(local_image_path):
            image_url_to_use = local_image_path
            update_post_image(post_id, new_image_url or local_image_path)
            self.log(f"📸 Использую новую картинку (локальный файл)")
        elif new_image_url:
            image_url_to_use = new_image_url
            update_post_image(post_id, new_image_url)
            self.log(f"📸 Использую новую картинку")
        
        # 2.2. Если нет — берём последнюю успешную из БД
        else:
            self.log("🔄 Нет новой картинки, ищу последнюю успешную из БД...")
            last_image_url = get_last_image_url()
            
            if last_image_url and last_image_url != get_placeholder_image():
                image_url_to_use = last_image_url
                update_post_image(post_id, last_image_url)
                self.log(f"📸 Использую последнюю картинку из БД: {last_image_url[:60]}...")
            else:
                # 2.3. Картинки нет — публикуем текстом.
                # Ссылку на via.placeholder.com Telegram скачать не может, поэтому
                # отправка с ней заведомо проваливается и пост уходил текстом —
                # лучше сразу не тратить попытку.
                self.log("🔄 Картинок в БД нет — публикую текстом")
        
        # ==========================================
        # 3. ПУБЛИКАЦИЯ В TELEGRAM
        # ==========================================
        message_id = await self._publish_to_telegram(post_text, image_url_to_use, context)
        
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
        
        try:
            # Пробуем отправить с картинкой
            if image_url and image_url != get_placeholder_image():
                message_id = await self._send_with_image(post_text, image_url, chat_id)
                if message_id:
                    self.log(f"✅ Пост опубликован с картинкой в «{profile.title}»")
                    return message_id
            
            result = await self.bot.send_message(
                chat_id=chat_id,
                text=post_text
            )
            self.log(f"✅ Пост опубликован без картинки в «{profile.title}»")
            return result.message_id if result else None
            
        except Exception as e:
            self.log(f"❌ Ошибка публикации в «{profile.title}»: {e}", is_error=True)
            return None
    
    async def _send_with_image(self, post_text: str, image_url: str, chat_id: str):
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
                        caption=post_text
                    )
                return result.message_id if result else None
            except Exception as e:
                self.log(f"⚠️ Ошибка отправки локальной картинки: {e}", is_error=True)
                return None
        
        image_path = os.path.join("memory_bank", "temp_publish.jpg")
        try:
            # to_thread: скачивание картинки блокирующее
            response = await asyncio.to_thread(requests.get, image_url, timeout=30)
            if response.status_code != 200:
                self.log(f"⚠️ Не удалось скачать картинку (статус {response.status_code})", is_error=True)
                return None
            
            with open(image_path, "wb") as f:
                f.write(response.content)
            
            with open(image_path, "rb") as f:
                result = await self.bot.send_photo(
                    chat_id=chat_id,
                    photo=f,
                    caption=post_text
                )
            
            return result.message_id if result else None
            
        except Exception as e:
            self.log(f"⚠️ Ошибка отправки картинки: {e}", is_error=True)
            return None
        finally:
            if os.path.exists(image_path):
                os.remove(image_path)
