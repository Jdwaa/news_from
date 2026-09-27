# test_scheduler_time.py

"""
ПРОВЕРКА КОНТРАКТА ВРЕМЕНИ: Аналитик -> Memory Bank -> Планировщик.

Проверяет, что Планировщик реально читает час/день из отчёта Аналитика,
а не сваливается в дефолт 10:00 / понедельник (баг с ключами best_hour/best_day).

Реальная память проекта (memory_bank/context.json) НЕ изменяется:
проверка работает в отдельной временной папке.
"""

import asyncio
import os
import shutil
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import agents.scheduler_agent as scheduler_module
from agents.analytics_agent import AnalyticsAgent, normalize_analytics_report
from agents.scheduler_agent import SchedulerAgent
from database import init_db
from memory_bank import MemoryBank

TMP_MEMORY = ".tmp_test_scheduler_time"

_checks = []


def check(name: str, actual, expected) -> None:
    ok = actual == expected
    _checks.append(ok)
    print(f"{'✅' if ok else '❌'} {name}: получено {actual!r}, ожидалось {expected!r}")


class DummyOrchestrator:
    """Заглушка оркестратора — публиковать ничего не надо."""

    def __init__(self):
        self.calls = 0
        # Планировщик обращается к bot для уведомлений о сбоях; None означает
        # «уведомлять некому», и это допустимый случай
        self.bot = None

    async def run(self, context: dict = None) -> dict:
        # Планировщик передаёт контекст с профилем канала, поэтому сигнатура
        # должна его принимать: иначе вызов падает с TypeError.
        self.calls += 1
        return {"published_post_id": None}


class FakeAsyncio:
    """Подменяет asyncio внутри scheduler_agent: вместо 30-минутного сна выходим из цикла."""

    def __init__(self, target):
        self._target = target

    async def sleep(self, seconds):
        self._target.is_running = False


async def main():
    init_db()

    # ==========================================
    # 1. НОРМАЛИЗАЦИЯ ОТЧЁТА
    # ==========================================
    canonical = normalize_analytics_report(
        {"hour": 18, "day": 5, "interval_hours": 6, "reason": "тест"}
    )
    check(
        "normalize: канонические ключи hour/day",
        (canonical["hour"], canonical["day"], canonical["interval_hours"]),
        (18, 5, 6.0),
    )

    legacy = normalize_analytics_report({"best_hour": 7, "best_day": 2})
    check(
        "normalize: старые ключи best_hour/best_day",
        (legacy["hour"], legacy["day"]),
        (7, 2),
    )

    empty = normalize_analytics_report(None)
    check("normalize: пустой отчёт -> дефолт", (empty["hour"], empty["day"]), (10, 0))

    broken = normalize_analytics_report({"hour": 99, "day": 42})
    check("normalize: битые значения -> дефолт", (broken["hour"], broken["day"]), (10, 0))

    # ==========================================
    # 2. ЧТО ЗАПИСЫВАЕТ АНАЛИТИК (на реальной БД, во временную память)
    # ==========================================
    if os.path.isdir(TMP_MEMORY):
        shutil.rmtree(TMP_MEMORY)
    memory = MemoryBank(path=TMP_MEMORY)

    analytics = AnalyticsAgent()
    analytics.memory = memory
    report = analytics.get_best_posting_time()
    memory.set("analytics_report", report)

    print(f"\nℹ️  Отчёт Аналитика: {report}\n")

    check(
        "отчёт Аналитика в каноническом формате",
        (sorted(report.keys()), "best_hour" in report),
        (["day", "hour", "interval_hours", "reason"], False),
    )

    # ==========================================
    # 3. ПЛАНИРОВЩИК ЧИТАЕТ ЭТОТ ОТЧЁТ
    # ==========================================
    scheduler = SchedulerAgent(orchestrator=DummyOrchestrator())
    scheduler.memory = memory

    hour, day = scheduler._load_best_time()
    check(
        "Планировщик: _load_best_time() == отчёт Аналитика",
        (hour, day),
        (report["hour"], report["day"]),
    )
    check(
        "Планировщик: время не свалилось в дефолт",
        (hour, day) != (10, 0),
        (report["hour"], report["day"]) != (10, 0),
    )

    # ==========================================
    # 4. СКВОЗНАЯ ПРОВЕРКА: execute() ПЕРЕДАЁТ ТЕ ЖЕ ЧАС/ДЕНЬ В _should_publish
    # ==========================================
    captured = {}
    real_should_publish = scheduler._should_publish

    async def spy_should_publish(best_hour, best_day):
        captured["hour"] = best_hour
        captured["day"] = best_day
        return await real_should_publish(best_hour, best_day)

    scheduler._should_publish = spy_should_publish
    scheduler_module.asyncio = FakeAsyncio(scheduler)  # цикл сделает ровно одну итерацию

    try:
        await scheduler.execute({})
    finally:
        scheduler_module.asyncio = asyncio

    check(
        "Планировщик: execute() -> _should_publish получил час/день из отчёта",
        (captured.get("hour"), captured.get("day")),
        (report["hour"], report["day"]),
    )

    # ==========================================
    # ИТОГ
    # ==========================================
    print("\n" + "=" * 60)
    if all(_checks):
        print(f"✅ ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ ({len(_checks)}/{len(_checks)})")
    else:
        print(f"❌ ПРОВАЛЕНО: {_checks.count(False)} из {len(_checks)}")
    print("=" * 60)

    if os.path.isdir(TMP_MEMORY):
        shutil.rmtree(TMP_MEMORY)

    return 0 if all(_checks) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
