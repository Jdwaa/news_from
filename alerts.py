# alerts.py

"""
УВЕДОМЛЕНИЯ АДМИНИСТРАТОРУ

Сообщения о сбоях в личку владельцу бота.

Зачем: ошибки уходили только в лог. Если бот падал ночью или переставал
публиковать из-за сбоя API, узнать об этом можно было лишь утром — по
пустому каналу. Теперь бот сам сообщает о проблеме.

Защита от спама: одинаковые уведомления не чаще, чем раз в ALERT_COOLDOWN
(по умолчанию час). Иначе при затяжном сбое админ получил бы сотни сообщений
и перестал бы их читать.
"""

import asyncio
from datetime import datetime

from config import Config


# Время последнего уведомления по каждому типу события (в памяти процесса)
_last_sent = {}

# Одинаковые уведомления не чаще раза в час: при затяжном сбое иначе
# накопились бы сотни сообщений, и админ перестал бы их читать
ALERT_COOLDOWN = 3600


def _recipients() -> list:
    """Кому отправлять уведомления: ADMIN_ID и OWNER_CHAT_ID."""
    ids = [Config.ADMIN_ID, Config.OWNER_CHAT_ID, *Config.EXTRA_ADMIN_IDS]
    return [i for i in ids if i]


def should_notify(kind: str, cooldown: int = ALERT_COOLDOWN) -> bool:
    """
    Проверяет, не слишком ли часто мы шлём уведомление этого типа.

    Отметку времени ставит только notify_admin() после фактической отправки,
    поэтому неудачная отправка не «съедает» окно тишины.
    """
    last = _last_sent.get(kind)
    if not last:
        return True
    return (datetime.now() - last).total_seconds() >= cooldown


def mark_notified(kind: str):
    _last_sent[kind] = datetime.now()


async def notify_admin(bot, text: str, kind: str = "generic",
                       critical: bool = False) -> bool:
    """
    Отправляет уведомление администратору.

    `kind` группирует однотипные сообщения: «сбой публикации», «сбой API» —
    каждое со своим окном тишины.
    `critical=True` — сообщение уходит даже повторно, минуя окно тишины
    (например, полная остановка бота).

    Возвращает True, если хотя бы одному получателю отправлено.
    """
    if bot is None:
        return False
    if not critical and not should_notify(kind):
        return False

    recipients = _recipients()
    if not recipients:
        return False

    prefix = "🚨" if critical else "⚠️"
    message = f"{prefix} {text}"

    sent = False
    for chat_id in recipients:
        try:
            await bot.send_message(chat_id=chat_id, text=message)
            sent = True
        except Exception:
            # Не смогли уведомить — не роняем из-за этого рабочий цикл.
            # Ошибку всё равно увидим в логе.
            continue

    if sent:
        mark_notified(kind)
    return sent


def notify_admin_sync(bot_loop, bot, text: str, kind: str = "generic",
                      critical: bool = False):
    """
    Уведомление из синхронного кода: создаёт задачу в работающем event loop.

    Нужно для мест, которые вызываются вне async (например, из планировщика
    по таймеру), чтобы не заводить второй цикл событий.
    """
    if bot is None or bot_loop is None:
        return None
    try:
        return bot_loop.create_task(notify_admin(bot, text, kind, critical))
    except Exception:
        return None
