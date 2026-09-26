# agents/image_agent.py

"""
АГЕНТ-ГЕНЕРАТОР КАРТИНОК
Генерирует картинку через Kling 1.0 (OdiRouter)
"""

import asyncio
import os
import time
import uuid
import requests
from .base_agent import BaseAgent
from config import Config
from net_utils import post_json, get_json, retry_sync


class ImageAgent(BaseAgent):
    """Агент для генерации картинок через Kling 1.0"""
    
    def __init__(self):
        super().__init__("Генератор картинок")
    
    async def execute(self, context: dict) -> dict:
        """
        1. Проверяет, есть ли промпт в контексте
        2. Отправляет задачу в OdiRouter
        3. Ждёт завершения
        4. Скачивает картинку ЛОКАЛЬНО и кладёт путь в context["image_path"]

        Ссылки CDN у OdiRouter подписаны и живут ~7 суток (Expires=...).
        Если сохранить только URL, через неделю он отдаёт 403 AccessDenied,
        и пост уходит без картинки. Поэтому файл забираем сразу, пока ссылка жива.
        context["image_url"] остаётся для совместимости (БД, аналитика).
        """
        prompt = context.get("image_prompt")
        
        # Быстрый режим: ручной запуск из Telegram не ждёт картинку,
        # чтобы команда отвечала сразу.
        if context.get("skip_image_generation"):
            self.log("⚡ Быстрый режим: генерацию картинки пропускаю")
            context["image_url"] = None
            return context
        
        # Страховка: если генерация может не уложиться в отведённое время,
        # публикуем написанный пост без картинки, а не теряем его.
        if context.get("deadline"):
            remaining = context["deadline"] - time.time()
            if not Config.ODIROUTER_API_KEY or remaining < Config.PUBLISH_TIME_RESERVE:
                self.log(
                    f"⏳ До конца отведённого времени {remaining:.0f} с — "
                    f"пропускаю генерацию картинки, чтобы пост успел опубликоваться",
                    is_error=True
                )
                context["image_url"] = None
                return context
        
        if not prompt:
            self.log("⚠️ Нет промпта для генерации картинки", is_error=True)
            context["image_url"] = None
            return context
        
        self.log(f"🎨 Генерация картинки...")
        self.log(f"📝 Промпт: {prompt[:80]}...")
        
        # Генерируем картинку
        image_url = await self._generate_image(prompt)
        
        if image_url:
            context["image_url"] = image_url
            self.log(f"✅ Картинка сгенерирована: {image_url[:60]}...")
            # Забираем файл сразу: подписанная ссылка CDN живёт ~7 суток,
            # после чего публикация с ней превратится в пост без картинки.
            local_path = await self._download_image(image_url, context)
            context["image_path"] = local_path
        else:
            context["image_url"] = None
            context["image_path"] = None
            self.log("⚠️ Не удалось сгенерировать картинку", is_error=True)
        
        return context
    
    async def _download_image(self, url: str, context: dict) -> str | None:
        """
        Скачивает картинку в локальный файл и возвращает путь.
        
        Нужна, чтобы публикация не зависела от срока жизни подписанного URL.
        При неудаче возвращает None — тогда публикатор попробует старую логику.
        """
        try:
            # Повторяем при временных сбоях: картинка уже сгенерирована и
            # оплачена, потерять её из-за одного обрыва связи нельзя.
            def _fetch():
                resp = requests.get(url, timeout=60)
                resp.raise_for_status()
                return resp

            response = await asyncio.to_thread(retry_sync, _fetch, attempts=3)
            if not response.content:
                self.log("⚠️ Картинка пустая", is_error=True)
                return None
            
            dest = Config.IMAGE_DIR
            os.makedirs(dest, exist_ok=True)
            filename = f"post_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}.jpg"
            path = os.path.join(dest, filename)
            
            with open(path, "wb") as f:
                f.write(response.content)
            
            self.log(f"💾 Картинка сохранена локально: {filename} ({len(response.content)//1024} КБ)")
            return path
            
        except Exception as e:
            self.log(f"⚠️ Ошибка скачивания картинки: {e}", is_error=True)
            return None
    
    async def _generate_image(self, prompt: str) -> str:
        """
        Генерирует картинку через OdiRouter, перебирая модели по порядку.

        Первая модель — основная (kling-v1-image). Если провайдер её не
        обслуживает (задача завершается с error вместо картинки), пробуем
        следующую. Так пост получает картинку даже когда конкретная модель
        сломана на стороне OdiRouter.
        """
        models = Config.IMAGE_MODELS
        last_error = None

        for index, model in enumerate(models, start=1):
            label = f"{model} ({index}/{len(models)})" if len(models) > 1 else model
            self.log(f"🎨 Генерация картинки: {label}")
            try:
                image_url = await self._generate_image_with_model(prompt, model)
            except Exception as e:
                image_url = None
                last_error = f"{type(e).__name__}: {e}"
                self.log(f"  ❌ {model}: исключение — {last_error}", is_error=True)

            if image_url:
                return image_url

            last_error = last_error or "модель не вернула картинку"
            if index < len(models):
                self.log(f"  🔁 {model} не сработала, пробую {models[index]}")

        self.log(f"⚠️ Ни одна модель не дала картинку. Последняя ошибка: {last_error}", is_error=True)
        return None

    async def _generate_image_with_model(self, prompt: str, model: str) -> str | None:
        """Одна попытка генерации конкретной моделью. Возвращает URL картинки или None."""
        
        if len(prompt) > 2500:
            prompt = prompt[:2500]
        
        enhanced_prompt = f"{prompt}, photorealistic, 8k, high quality, sharp focus, detailed, professional photography, cinematic lighting"
        
        base_url = f"{Config.ODIROUTER_BASE_URL}/v1/queue/{model}"
        headers = {
            "Authorization": f"Bearer {Config.ODIROUTER_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "prompt": enhanced_prompt,
            "negative_prompt": "low quality, blurry, distorted, ugly, messy, cluttered, watermark, text",
            "resolution": "1k",
            "n": 1,
            "aspect_ratio": "16:9",
            "image_fidelity": 0.5,
            "watermark_info": {"enabled": False}
        }
        
        try:
            # 1. Отправляем задачу
            self.log("  📤 Отправка задачи в очередь...")
            # to_thread: requests блокирующий; post_json повторяет временные сбои
            task_data = await asyncio.to_thread(
                post_json, base_url, headers, payload, 30, 3
            )
            
            request_id = task_data.get("request_id")
            if not request_id:
                self.log(f"  ❌ {model}: не удалось получить request_id", is_error=True)
                return None
            
            self.log(f"  ✅ Задача создана: {request_id}")
            
            # 2. Ждём завершения
            status_url = task_data.get("status_url") or f"{base_url}/requests/{request_id}/status"
            
            attempts = 0
            max_attempts = 45
            last_status = None
            
            while attempts < max_attempts:
                try:
                    # Опрос статуса: временный сбой сети не должен считаться
                    # провалом генерации — задача на стороне OdiRouter живёт.
                    status_data = await asyncio.to_thread(
                        get_json, status_url, headers, None, 10, 2
                    )
                    
                    current_status = status_data.get("status")
                    last_status = current_status
                    attempts += 1
                    
                    self.log(f"  ⏳ Статус: {current_status} (попытка {attempts}/{max_attempts})")
                    
                    if current_status == "COMPLETED":
                        self.log("  ✅ Генерация завершена")
                        break
                    elif current_status in ["FAILED", "CANCELED"]:
                        error_msg = status_data.get("error", "Неизвестная ошибка")
                        self.log(f"  ❌ Генерация не удалась: {error_msg}", is_error=True)
                        return None
                    
                    await asyncio.sleep(3)
                    
                except requests.exceptions.Timeout:
                    self.log("  ⏰ Таймаут проверки статуса", is_error=True)
                    await asyncio.sleep(5)
                    continue
                except Exception as e:
                    self.log(f"  ⚠️ Ошибка проверки статуса: {e}", is_error=True)
                    await asyncio.sleep(5)
                    continue
            
            if attempts >= max_attempts:
                self.log("  ⏰ Таймаут ожидания генерации", is_error=True)
                return None
            
            if last_status != "COMPLETED":
                self.log(f"  ❌ Генерация не завершена (статус: {last_status})", is_error=True)
                return None
            
            # 3. Получаем результат
            result_url = task_data.get("response_url") or f"{base_url}/requests/{request_id}/response"
            result_data = await asyncio.to_thread(
                get_json, result_url, headers, None, 30, 3
            )
            
            # Задача может завершиться со статусом COMPLETED и одновременно
            # нести error: провайдер упал на своей стороне (мы видели
            # RUNTIME_CREATE_PROVIDER_FAILED: http_status=404). Раньше это
            # выглядело просто как «не нашёл URL», и причину нельзя было понять.
            provider_error = result_data.get("error")
            if provider_error:
                self.log(f"  ❌ Провайдер картинок вернул ошибку: {str(provider_error)[:200]}", is_error=True)
                return None
            
            # Извлекаем URL картинки
            if "output" in result_data and isinstance(result_data["output"], list):
                for item in result_data["output"]:
                    if "content" in item and isinstance(item["content"], list):
                        for content_item in item["content"]:
                            if content_item.get("type") == "image" and "url" in content_item:
                                return content_item["url"]
            
            self.log(
                "  ⚠️ Не удалось найти URL картинки. Ответ: "
                f"{str(result_data)[:200]}",
                is_error=True
            )
            return None
            
        except requests.exceptions.Timeout:
            self.log("  ⏰ Таймаут запроса к OdiRouter", is_error=True)
            return None
        except requests.exceptions.RequestException as e:
            self.log(f"  ❌ Ошибка запроса: {e}", is_error=True)
            return None
        except Exception as e:
            self.log(f"  ❌ Ошибка генерации: {e}", is_error=True)
            return None