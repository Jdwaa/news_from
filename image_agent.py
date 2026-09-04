# agents/image_agent.py

"""
АГЕНТ-ГЕНЕРАТОР КАРТИНОК
Генерирует картинку через Kling 1.0 (OdiRouter)
"""

import asyncio
import requests
from .base_agent import BaseAgent
from config import Config


class ImageAgent(BaseAgent):
    """Агент для генерации картинок через Kling 1.0"""
    
    def __init__(self):
        super().__init__("Генератор картинок")
    
    async def execute(self, context: dict) -> dict:
        """
        1. Проверяет, есть ли промпт в контексте
        2. Отправляет задачу в OdiRouter
        3. Ждёт завершения
        4. Сохраняет URL картинки в context["image_url"]
        """
        prompt = context.get("image_prompt")
        
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
        else:
            context["image_url"] = None
            self.log("⚠️ Не удалось сгенерировать картинку", is_error=True)
        
        return context
    
    async def _generate_image(self, prompt: str) -> str:
        """Генерирует картинку через Kling 1.0 (OdiRouter)"""
        
        if len(prompt) > 2500:
            prompt = prompt[:2500]
        
        enhanced_prompt = f"{prompt}, photorealistic, 8k, high quality, sharp focus, detailed, professional photography, cinematic lighting"
        
        url = "https://api.odirouter.ai/model/v1/queue/kling-v1-image"
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
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            task_data = response.json()
            
            request_id = task_data.get("request_id")
            if not request_id:
                self.log("  ❌ Не удалось получить request_id", is_error=True)
                return None
            
            self.log(f"  ✅ Задача создана: {request_id}")
            
            # 2. Ждём завершения
            status_url = task_data.get("status_url") or f"https://api.odirouter.ai/model/v1/queue/kling-v1-image/requests/{request_id}/status"
            
            attempts = 0
            max_attempts = 45
            last_status = None
            
            while attempts < max_attempts:
                try:
                    status_response = requests.get(status_url, headers=headers, timeout=10)
                    status_response.raise_for_status()
                    status_data = status_response.json()
                    
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
            result_url = task_data.get("response_url") or f"https://api.odirouter.ai/model/v1/queue/kling-v1-image/requests/{request_id}/response"
            result_response = requests.get(result_url, headers=headers, timeout=30)
            result_response.raise_for_status()
            result_data = result_response.json()
            
            # Извлекаем URL картинки
            if "output" in result_data and isinstance(result_data["output"], list):
                for item in result_data["output"]:
                    if "content" in item and isinstance(item["content"], list):
                        for content_item in item["content"]:
                            if content_item.get("type") == "image" and "url" in content_item:
                                return content_item["url"]
            
            self.log("  ⚠️ Не удалось найти URL картинки", is_error=True)
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