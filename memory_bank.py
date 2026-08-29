# memory_bank.py

"""
ПРОСТАЯ ПАМЯТЬ ДЛЯ АГЕНТОВ
Хранит данные в JSON-файлах.
"""

import json
import os
from datetime import datetime
from typing import Optional, Dict, Any


class MemoryBank:
    """Класс для хранения данных агентов в JSON-файлах"""
    
    def __init__(self, path: str = "memory_bank"):
        self.path = path
        os.makedirs(path, exist_ok=True)
        self.context_file = os.path.join(path, "context.json")
        self.history_file = os.path.join(path, "history.json")
        
        # Загружаем существующие данные
        self._context = self._load_json(self.context_file) or {}
        self._history = self._load_json(self.history_file) or []
    
    def _load_json(self, filepath: str) -> Optional[Dict]:
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return None
    
    def _save_json(self, filepath: str, data: Any):
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def get(self, key: str, default=None):
        return self._context.get(key, default)
    
    def set(self, key: str, value: Any):
        self._context[key] = value
        self._save_json(self.context_file, self._context)
    
    def update(self, data: dict):
        self._context.update(data)
        self._save_json(self.context_file, self._context)
    
    def add_to_history(self, entry: dict):
        entry["timestamp"] = datetime.now().isoformat()
        self._history.append(entry)
        if len(self._history) > 100:
            self._history = self._history[-100:]
        self._save_json(self.history_file, self._history)
    
    def get_history(self, limit: int = 20):
        return self._history[-limit:] if self._history else []