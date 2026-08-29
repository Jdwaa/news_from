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
from database import save_post, update_post_image, update_post_status, get_placeholder_image, get_last_image_url


class PublisherAgent(BaseAgent):
    """Агент для публикации постов в Telegram"""
    
    def __init__(self, bot=None):
        super().__init__("Публикатор постов")
        self.bot = bot
    
    async def execute(self, context: dict) -> dict:
        """
        1. Берёт пост из контекста
        2. Сохраняет в БД
        3. Если картинка есть — использует её
        4. Если нет — берёт последнюю успешную из БД
        5. Отправляет в Telegram
        """
        post_text = context.get("post_text")
        post_title = context.get("post_title", "")
        new_image_url = context.get("image_url")  # Картинка от ImageAgent
        image_prompt = context.get("image_prompt", "")
        selected_news = context.get("selected_news", {})
        
        if not post_text:
            self.log("❌ Нет текста для публикации", is_error=True)
            return context
        
        self.log(f"📤 Сохранение и публикация поста...")
        
        # ==========================================
        # 1. СОХРАНЯЕМ ПОСТ В БД
        # ==========================================
        link = selected_news.get("link", "")
        post_id = save_post(post_title, link, post_text, image_prompt, new_image_url)
        
        if not post_id:
            self.log("⚠️ Пост не сохранён (возможно, дубликат)", is_error=True)
            return context
        
        self.log(f"✅ Пост #{post_id} сохранён в БД")
        
        # ==========================================
        # 2. ВЫБИРАЕМ КАРТИНКУ ДЛЯ ПУБЛИКАЦИИ
        # ==========================================
        image_url_to_use = None
        
        # 2.1. Если есть новая картинка — используем её
        if new_image_url:
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
                # 2.3. Если и там нет — заглушка
                image_url_to_use = get_placeholder_image()
                update_post_image(post_id, image_url_to_use)
                self.log("🔄 Использую заглушку (картинок в БД нет)")
        
        # ==========================================
        # 3. ПУБЛИКАЦИЯ В TELEGRAM
        # ==========================================
        await self._publish_to_telegram(post_text, image_url_to_use, context)
        
        # ==========================================
        # 4. ОБНОВЛЯЕМ СТАТУС
        # ==========================================
        update_post_status(post_id, 'published')
        
        context["published_post_id"] = post_id
        context["published_at"] = datetime.now().isoformat()
        context["image_url_used"] = image_url_to_use
        
        # Сохраняем в Memory Bank
        self.memory.add_to_history({
            "event": "post_published",
            "post_id": post_id,
            "title": post_title[:50],
            "has_image": image_url_to_use != get_placeholder_image(),
            "image_source": "new" if new_image_url else "db_fallback" if image_url_to_use != get_placeholder_image() else "placeholder"
        })
        
        self.log(f"✅ Пост #{post_id} опубликован!")
        return context
    
    async def _publish_to_telegram(self, post_text: str, image_url: str, context: dict):
        """Отправляет пост в Telegram"""
        
        if not self.bot:
            self.log("⚠️ Бот не передан, публикация только в БД", is_error=True)
            return
        
        try:
            # Пробуем отправить с картинкой
            if image_url and image_url != get_placeholder_image():
                success = await self._send_with_image(post_text, image_url)
                if success:
                    self.log("✅ Пост опубликован с картинкой")
                    return
            
            # Если без картинки
            await self.bot.send_message(
                chat_id=os.getenv("CHANNEL_ID"),
                text=post_text
            )
            self.log("✅ Пост опубликован без картинки")
            
        except Exception as e:
            self.log(f"❌ Ошибка публикации: {e}", is_error=True)
    
    async def _send_with_image(self, post_text: str, image_url: str) -> bool:
        """Отправляет пост с картинкой"""
        try:
            response = requests.get(image_url, timeout=30)
            if response.status_code != 200:
                self.log(f"⚠️ Не удалось скачать картинку (статус {response.status_code})", is_error=True)
                return False
            
            image_path = "temp_publish.jpg"
            with open(image_path, "wb") as f:
                f.write(response.content)
            
            with open(image_path, "rb") as f:
                await self.bot.send_photo(
                    chat_id=os.getenv("CHANNEL_ID"),
                    photo=f,
                    caption=post_text
                )
            
            os.remove(image_path)
            return True
            
        except Exception as e:
            self.log(f"⚠️ Ошибка отправки картинки: {e}", is_error=True)
            return False

            # agents/publisher_agent.py (добавить метод)

    async def wait_for_best_time(self):
        """
        Ждёт лучшее время для публикации на основе аналитики.
        НО НЕ ДОЛЬШЕ 24 ЧАСОВ!
        """
        self.log("📊 Загружаю аналитику для определения времени публикации...")
        
        # Читаем отчёт из Memory Bank
        analytics_report = self.memory.get("analytics_report")
        
        if not analytics_report:
            self.log("⚠️ Нет аналитики, публикую через 1 час")
            await asyncio.sleep(3600)
            return
        
        best_hour = analytics_report.get("best_hour", 10)
        best_day = analytics_report.get("best_day", 0)
        
        now = datetime.now()
        current_hour = now.hour
        current_day = now.weekday()
        
        self.log(f"📊 Лучшее время: {best_hour}:00, лучший день: {best_day}")
        
        # Проверяем, совпадает ли сегодняшний день
        if current_day != best_day:
            days_until = (best_day - current_day) % 7
            if days_until == 0:
                days_until = 7
            self.log(f"⏳ Сегодня не лучший день. Жду {days_until} дней до дня {best_day}")
            wait_seconds = days_until * 86400 + (best_hour - current_hour) * 3600
        else:
            # Если сегодня лучший день, ждём лучший час
            if current_hour < best_hour:
                wait_seconds = (best_hour - current_hour) * 3600
                self.log(f"⏳ Жду {wait_seconds/60:.0f} минут до {best_hour}:00")
            else:
                # Если уже позже лучшего часа — ждём следующий день
                self.log(f"⏳ Уже позже {best_hour}:00, жду до завтра")
                wait_seconds = (24 - current_hour + best_hour) * 3600
        
        # ===== НОВОЕ: НЕ ЖДАТЬ БОЛЬШЕ 24 ЧАСОВ =====
        max_wait = 86400  # 24 часа
        if wait_seconds > max_wait:
            self.log(f"⏳ Ожидание слишком долгое ({wait_seconds/3600:.1f} ч), сокращаю до 24 ч")
            wait_seconds = max_wait
        # ===========================================
        
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)