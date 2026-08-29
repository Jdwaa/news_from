# agents/base_agent.py

"""
БАЗОВЫЙ АГЕНТ — шаблон для всех агентов.
Все агенты будут наследовать этот класс.
"""

from abc import ABC, abstractmethod
from datetime import datetime
import sys
import os

# Добавляем путь к корню проекта, чтобы импортировать модули
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory_bank import MemoryBank
from database import save_log


class BaseAgent(ABC):
    """
    Базовый класс для всех агентов.
    
    Каждый агент имеет:
    - имя (name) — для логов
    - память (memory) — доступ к Memory Bank
    - метод execute(context) — основная работа агента
    - метод log(message) — для логирования
    """
    
    def __init__(self, name: str):
        self.name = name
        self.memory = MemoryBank()
    
    @abstractmethod
    async def execute(self, context: dict) -> dict:
        """
        Основной метод агента.
        Принимает контекст (словарь с данными).
        Возвращает обновлённый контекст.
        """
        pass
    
    def log(self, message: str, is_error: bool = False) -> str:
        """
        Логирование с временной меткой и именем агента.
        Сохраняет в БД и выводит в консоль.
        """
        timestamp = datetime.now().strftime("%H:%M:%S")
        prefix = "❌" if is_error else "📌"
        log_line = f"[{timestamp}] {prefix} [{self.name}] {message}"
        print(log_line)
        save_log(message, "error" if is_error else "info")
        return log_line