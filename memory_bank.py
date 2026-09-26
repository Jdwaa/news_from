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
    """
    Класс для хранения данных агентов в JSON-файлах.

    Каждый агент создаёт свой экземпляр, а записывают они в один и тот же файл,
    поэтому все операции читают файл заново, а не держат копию в памяти:
    - Рерайтер, созданный при старте процесса, иначе до перезапуска не видел бы
      практики, которые записал Аналитик;
    - заодно не теряются ключи, записанные другим агентом между чтением и записью.
    """
    
    def __init__(self, path: str = None):
        if path is None:
            # На хостинге путь лучше задать абсолютным (MEMORY_PATH), иначе
            # память агентов может пропасть вместе с пересборкой контейнера
            from config import Config
            path = Config.MEMORY_PATH
        self.path = path
        os.makedirs(path, exist_ok=True)
        self.context_file = os.path.join(path, "context.json")
        self.history_file = os.path.join(path, "history.json")
    
    def _load_json(self, filepath: str) -> Optional[Dict]:
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return None
    
    def _save_json(self, filepath: str, data: Any):
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def _read_context(self) -> dict:
        context = self._load_json(self.context_file)
        return context if isinstance(context, dict) else {}
    
    def _read_history(self) -> list:
        history = self._load_json(self.history_file)
        return history if isinstance(history, list) else []
    
    def get(self, key: str, default=None):
        return self._read_context().get(key, default)
    
    def set(self, key: str, value: Any):
        context = self._read_context()
        context[key] = value
        self._save_json(self.context_file, context)
    
    def update(self, data: dict):
        context = self._read_context()
        context.update(data)
        self._save_json(self.context_file, context)
    
    def add_to_history(self, entry: dict):
        entry["timestamp"] = datetime.now().isoformat()
        history = self._read_history()
        history.append(entry)
        self._save_json(self.history_file, history[-100:])
    
    def get_history(self, limit: int = 20):
        history = self._read_history()
        return history[-limit:] if history else []
