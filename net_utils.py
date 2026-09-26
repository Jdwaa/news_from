# net_utils.py

"""
СЕТЕВЫЕ УТИЛИТЫ

Повторы при временных сбоях сети и API.

Зачем: DeepSeek и OdiRouter отвечают 5xx, таймаутят или обрывают соединение
не регулярно, а «иногда». Без повторов каждый такой сбой терял попытку
рерайта или генерации картинки — пост выходил хуже или не выходил вовсе,
и заметить это можно было только по пустому каналу.

Здесь один общий механизм: повторяем только ВРЕМЕННЫЕ ошибки, а постоянные
(неверный ключ, 4xx) отдаём сразу — повторять их бессмысленно и дорого.
"""

import asyncio
import random
import time

import requests


# Коды, которые означают «попробуй ещё раз»: проблемы на стороне сервиса
RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class RetryableError(Exception):
    """Временная ошибка: имеет смысл повторить запрос."""


def is_retryable(exc: Exception) -> bool:
    """
    Определяет, стоит ли повторять запрос после этой ошибки.

    Постоянные ошибки (401 — неверный ключ, 400 — плохой запрос) не повторяем:
    от повторов они не пройдут, а время и квоту мы потратим.
    """
    if isinstance(exc, RetryableError):
        return True

    if isinstance(exc, (requests.exceptions.Timeout,
                        requests.exceptions.ConnectionError,
                        requests.exceptions.ChunkedEncodingError)):
        return True

    if isinstance(exc, requests.exceptions.HTTPError):
        resp = getattr(exc, "response", None)
        if resp is not None and resp.status_code in RETRYABLE_STATUS:
            return True
        if resp is not None and resp.status_code in (401, 403, 404, 400, 422):
            return False  # постоянные: ключ, права, формат запроса

    return False


def retry_sync(func, *args, attempts: int = 3, base_delay: float = 1.5,
               max_delay: float = 20.0, on_retry=None, **kwargs):
    """
    Синхронный вызов с повторами. Возвращает результат или бросает последнюю ошибку.

    Задержка растёт экспоненциально с небольшим разбросом: при массовом сбое
    все клиенты не должны бить в сервис одновременно.
    """
    last_exc = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if attempt >= attempts or not is_retryable(exc):
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay += random.uniform(0, delay * 0.3)
            if on_retry:
                try:
                    on_retry(attempt, exc, delay)
                except Exception:
                    pass
            time.sleep(delay)
    raise last_exc  # на случай attempts <= 0


async def retry_async(coro_factory, attempts: int = 3, base_delay: float = 1.5,
                      max_delay: float = 20.0, on_retry=None):
    """
    Асинхронный вызов с повторами.

    `coro_factory` — функция без аргументов, возвращающая новый корутин-объект:
    повторно ожидать уже завершённый корутин нельзя, поэтому каждый раз
    создаём новый.
    """
    last_exc = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return await coro_factory()
        except Exception as exc:
            last_exc = exc
            if attempt >= attempts or not is_retryable(exc):
                raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay += random.uniform(0, delay * 0.3)
            if on_retry:
                try:
                    on_retry(attempt, exc, delay)
                except Exception:
                    pass
            await asyncio.sleep(delay)
    raise last_exc


def post_json(url: str, headers: dict, payload: dict, timeout: int = 60,
              attempts: int = 3, on_retry=None) -> dict:
    """
    POST с JSON-ответом и повторами. Общая точка для вызовов DeepSeek.

    `raise_for_status()` здесь не вызываем отдельно: retry_sync повторяет
    только временные коды, а постоянные сразу отдаёт наружу.
    """
    def _do():
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    return retry_sync(_do, attempts=attempts, on_retry=on_retry)


def get_json(url: str, headers: dict = None, params: dict = None,
             timeout: int = 30, attempts: int = 3, on_retry=None) -> dict:
    """GET с JSON-ответом и повторами."""
    def _do():
        resp = requests.get(url, headers=headers, params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    return retry_sync(_do, attempts=attempts, on_retry=on_retry)
