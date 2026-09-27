# test_final.py

"""
ФИНАЛЬНАЯ ПРОВЕРКА ПЕРЕД ЗАПУСКОМ

Проверяет то, что добавилось в финальной версии:
1. Суточный дедуп публикатора (повторную новость не публикуем).
2. Пост без картинки публикуется текстом, файл-артефакт не остаётся.
3. Ревёрс-цикл: пост публикуется после исчерпания попыток ревизора,
   а хороший пост с парой придирок не жжёт все попытки.
4. Планировщик корректно читает метку ручного запуска (aware/naive время).
5. UI: сборка меню и клавиатуры, разметка Markdown с подчёркиваниями.

Работает на временной БД и памяти — данные проекта не изменяются.
"""

import asyncio
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import agents.publisher_agent as publisher_module
from agents.image_agent import ImageAgent
from agents.publisher_agent import PublisherAgent
from agents.reviewer_agent import ReviewerAgent
from agents.scheduler_agent import SchedulerAgent
from config import Config
from database import get_all_posts_with_stats, get_recent_post_links, init_db
from memory_bank import MemoryBank
from orchestrator import Orchestrator

TMP_DB = "test_final_tmp.db"
TMP_MEMORY = ".tmp_test_final"

_checks = []


def check(name, actual, expected):
    ok = actual == expected
    _checks.append(ok)
    print(f"{'✅' if ok else '❌'} {name}: получено {actual!r}, ожидалось {expected!r}")


class FakeMessage:
    def __init__(self, message_id):
        self.message_id = message_id


class FakeBot:
    """
    Заглушка бота.

    Сигнатуры повторяют реальный python-telegram-bot: публикатор передаёт
    reply_markup (кнопки воронки) и disable_web_page_preview. Заглушка со
    старой сигнатурой падала с TypeError и роняла публикацию в тесте.
    """

    def __init__(self, message_id=1234):
        self.message_id = message_id
        self.sent = []
        self.keyboards = []

    async def send_message(self, chat_id, text, reply_markup=None,
                           disable_web_page_preview=None, parse_mode=None):
        self.sent.append(("message", text))
        self.keyboards.append(reply_markup)
        return FakeMessage(self.message_id)

    async def send_photo(self, chat_id, photo, caption=None, reply_markup=None):
        self.sent.append(("photo", caption))
        self.keyboards.append(reply_markup)
        return FakeMessage(self.message_id)


class DummyOrchestrator:
    def __init__(self, bot=None):
        self.bot = bot
        self.calls = 0

    async def run(self, context=None):
        self.calls += 1
        return {"published_post_id": None}


def cleanup():
    for path in (TMP_DB, TMP_MEMORY):
        if os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.exists(path):
            os.remove(path)


async def main():
    cleanup()

    Config.DB_PATH = TMP_DB
    init_db()

    # ==========================================
    # 1. ДЕДУП ПО ССЫЛКЕ ЗА СУТКИ
    # ==========================================
    publisher = PublisherAgent(bot=FakeBot())
    publisher.memory = MemoryBank(path=TMP_MEMORY)

    news = {"title": "НОВОСТЬ ДНЯ", "link": "https://test.local/news-1", "source": "test"}
    context = {
        "post_title": "НОВОСТЬ ДНЯ",
        "post_text": "Текст поста 🚀",
        "image_url": None,
        "image_prompt": "test",
        "selected_news": news,
    }

    first = await publisher.execute(dict(context, selected_news=dict(news)))
    check("первая публикация прошла", bool(first.get("published_post_id")), True)

    second = await publisher.execute(dict(context, selected_news=dict(news)))
    check("повторная новость отсечена дедупом", second.get("duplicate_skipped"), True)
    check("дубликат не попал в БД", len(get_all_posts_with_stats(limit=10)), 1)
    check("ссылка видна в get_recent_post_links", "https://test.local/news-1" in get_recent_post_links(days=1), True)

    # ==========================================
    # 2. КАРТИНКА: ПУБЛИКАЦИЯ И РЕЗЕРВ
    # ==========================================
    # Картинок на диске может не быть (или лежать резерв) — от этого зависит,
    # уйдёт пост текстом или с фото. Изолируем папку картинок ДО публикаций,
    # чтобы тест не зависел от содержимого реальной memory_bank/images.
    from image_store import save_new as _save_image
    from config import Config as _Config

    _old_image_dir = _Config.IMAGE_DIR
    _test_image_dir = os.path.join(TMP_MEMORY, "images")
    os.makedirs(_test_image_dir, exist_ok=True)
    _Config.IMAGE_DIR = _test_image_dir
    # Пустая папка -> резерва нет -> пост уходит текстом
    for _f in os.listdir(_test_image_dir):
        _p = os.path.join(_test_image_dir, _f)
        if os.path.isfile(_p):
            os.remove(_p)

    _before = len(publisher.bot.sent)
    _norez = {"link": "https://test.local/news-norez", "source": "test"}
    _res_norez = await publisher.execute(dict(context, selected_news=_norez))
    check("публикация без резерва прошла", bool(_res_norez.get("published_post_id")), True)
    try:
        # Именно последнюю отправку проверяем: без резерва картинки нет,
        # значит пост должен уйти текстом
        check("без резерва пост ушёл текстом", publisher.bot.sent[-1][0], "message")

        # Теперь кладём резерв: пост при отсутствии новой картинки должен
        # уйти С ФОТО — именно для этого последний файл и хранится.
        _save_image(b"fake-jpeg-bytes" * 100, ".jpg")
        _news3 = {"link": "https://test.local/news-3", "source": "test"}
        _res3 = await publisher.execute(dict(context, selected_news=_news3))
        check("с резервом пост ушёл с картинкой", publisher.bot.sent[-1][0], "photo")
        check("резервная публикация прошла", bool(_res3.get("published_post_id")), True)
    finally:
        _Config.IMAGE_DIR = _old_image_dir

    # Битая картинка по ссылке: пост всё равно должен уйти (текстом, если
    # резерва нет) и временный файл не должен остаться
    async def failing_image(post_text, image_url, chat_id=None, keyboard=None):
        return None

    publisher._send_with_image = failing_image
    context_bad_image = dict(context, image_url="https://test.local/broken.jpg",
                             selected_news={"link": "https://test.local/news-2", "source": "test"})
    result_bad = await publisher.execute(context_bad_image)
    check("битая картинка -> пост всё равно ушёл", bool(result_bad.get("published_post_id")), True)
    check("временный файл картинки удалён", os.path.exists(os.path.join("memory_bank", "temp_publish.jpg")), False)

    # ==========================================
    # 3. РЕВЁРС-ЦИКЛ ОРКЕСТРАТОРА
    # ==========================================
    orchestrator = Orchestrator(bot=None)
    orchestrator.max_rewrite_attempts = 3

    reviewer_calls = {"n": 0}

    async def rewriter_stub(ctx):
        ctx["post_title"] = "ЗАГОЛОВОК ПОСТА"
        ctx["post_text"] = "Текст поста с эмодзи 🚀 и прогнозом: скоро всё изменится."
        ctx["image_prompt"] = "промпт"
        return ctx

    async def editor_stub(ctx):
        return ctx

    async def reviewer_reject(ctx):
        reviewer_calls["n"] += 1
        ctx["text_approved"] = False
        ctx["reviewer_feedback"] = "🔴 мало эмодзи"
        ctx["reviewer_issues_count"] = 1
        return ctx

    orchestrator.rewriter.execute = rewriter_stub
    orchestrator.editor.execute = editor_stub
    orchestrator.reviewer.execute = reviewer_reject
    orchestrator.parser.execute = lambda ctx: _identity(ctx, {"news_list": [{"title": "T", "link": "l", "source": "s"}]})
    orchestrator.selector.execute = lambda ctx: _identity(ctx, {"selected_news": {"title": "T", "link": "l", "source": "s"}})
    orchestrator.image.execute = lambda ctx: _identity(ctx, {"image_url": None})

    published = {}

    async def publisher_stub(ctx):
        published["called"] = True
        ctx["published_post_id"] = 42
        return ctx

    orchestrator.publisher.execute = publisher_stub

    result = await orchestrator.run()
    check("пост опубликован даже без одобрения ревизора", result.get("published_post_id"), 42)
    # 1 замечание (<= допустимых 2) — принимаем пост после первой попытки, не жжём остальные
    check("пост с парой придирок принимается сразу", result.get("rewrite_attempts"), 1)
    check("одобрения с первой попытки нет", result.get("approved_first_try"), False)

    # А вот с кучей замечаний цикл честно дорабатывает до максимума
    reviewer_calls["n"] = 0

    async def reviewer_hard_reject(ctx):
        reviewer_calls["n"] += 1
        ctx["text_approved"] = False
        ctx["reviewer_feedback"] = "🔴 много замечаний"
        ctx["reviewer_issues_count"] = 5
        return ctx

    orchestrator.reviewer.execute = reviewer_hard_reject
    published.clear()
    result_hard = await orchestrator.run()
    check("с 5 замечаниями цикл идёт до максимума попыток", result_hard.get("rewrite_attempts"), 3)
    check("и пост всё равно публикуется", result_hard.get("published_post_id"), 42)

    # ==========================================
    # 4. ПЛАНИРОВЩИК И МЕТКА РУЧНОГО ЗАПУСКА
    # ==========================================
    scheduler = SchedulerAgent(orchestrator=DummyOrchestrator())
    scheduler.memory = MemoryBank(path=TMP_MEMORY)

    naive = datetime.now().replace(microsecond=0)
    aware = naive.astimezone()
    check("наивная метка -> локальная зона", scheduler._parse_last_publish(naive.isoformat()).tzinfo is not None, True)
    check("aware-метка сохраняет смещение",
          scheduler._parse_last_publish(aware.isoformat()).utcoffset(), aware.utcoffset())

    # Метка публикации хранится ПОД КЛЮЧОМ КАНАЛА (last_publish_at:<канал>):
    # у бота два канала, и общая метка сдвигала бы интервал соседнего.
    _publish_key = scheduler._last_publish_key
    scheduler.memory.set(_publish_key, datetime.now().astimezone().isoformat())
    check("свежая публикация не даёт публиковать снова",
          await scheduler._should_publish(10, 0), False)

    scheduler.memory.set(_publish_key, "не дата")
    check("битая метка не роняет планировщик",
          await scheduler._should_publish(10, 0), False)

    # Пустая метка = канал ещё не публиковал -> публикуем сразу, иначе бот
    # ждал бы «лучшего дня» и не выпустил ни одного поста
    scheduler.memory.set(_publish_key, None)
    check("без метки канал публикует первую запись",
          await scheduler._should_publish(10, 0), True)

    # ==========================================
    # 5. UI: МЕНЮ И БЕЗОПАСНАЯ РАЗМЕТКА
    # ==========================================
    from telegram_bot import TelegramBot

    bot_ui = TelegramBot("000:test")
    keyboard = bot_ui._admin_keyboard()
    check("админ-клавиатура собрана", len(keyboard.inline_keyboard), 3)
    check("в UI нет второго определения клавиатуры",
          open("telegram_bot.py", encoding="utf-8").read().count("InlineKeyboardButton(\"🧹"), 1)

    sent = {}

    async def reply_markdown(text, parse_mode=None):
        if parse_mode == "Markdown":
            raise RuntimeError("Can't parse entities")
        sent["fallback"] = text

    await TelegramBot._send(reply_markdown, "Сводка с analysis_agent и score")
    check("Markdown-ошибка -> повтор без разметки", sent.get("fallback"), "Сводка с analysis_agent и score")

    async def reply_ok(text, parse_mode=None):
        sent["markdown"] = parse_mode

    await TelegramBot._send(reply_ok, "Сводка")
    check("обычная отправка идёт с Markdown", sent.get("markdown"), "Markdown")

    # ==========================================
    # 5.1. ВСЕ ОБРАБОТЧИКИ КОМАНД СУЩЕСТВУЮТ
    # Ровно этот баг ломал /start: метод вызывал несуществующий self.send_main_menu.
    # ==========================================
    import inspect as _inspect

    handler_names = (
        "start", "help_cmd", "post_now", "status", "analytics_cmd",
        "admin_menu", "button_handler", "_send_main_menu", "_main_menu_keyboard",
        "_admin_keyboard", "_run_manual_cycle", "_collect_analytics",
    )
    missing = [name for name in handler_names if not callable(getattr(bot_ui, name, None))]
    check("все методы UI на месте", missing, [])

    # Каждый self.<метод>() внутри класса должен существовать
    class_source = _inspect.getsource(TelegramBot)
    import re as _re
    called = set(_re.findall(r"self\.([a-zA-Z_][a-zA-Z0-9_]*)\(", class_source))
    unknown = sorted(
        name for name in called
        if not hasattr(TelegramBot, name) and name not in ("app", "memory", "orchestrator")
    )
    check("нет вызовов несуществующих методов", unknown, [])

    # Клавиатуры действительно собираются.
    # Строк в меню: по одной на активный канал (публикация) + Статус,
    # Воронка, Аналитика и Админ-панель. Проверяем по фактическому составу,
    # а не по «магическому» числу: меню расширяется, и тест не должен
    # падать от каждой новой кнопки.
    from channels import active_profiles as _active_profiles
    _fixed_rows = {"status", "growth", "analytics", "admin_panel"}
    _menu = bot_ui._main_menu_keyboard().inline_keyboard
    _callbacks = [btn.callback_data for row in _menu for btn in row]
    _expected_rows = len(_active_profiles()) + len(_fixed_rows)
    check("меню главного экрана собирается", len(_menu), _expected_rows)
    check("в меню есть отчёт по воронке", "growth" in _callbacks, True)
    check("все обязательные кнопки на месте",
          sorted(_fixed_rows - set(_callbacks)), [])
    _missing_channels = [
        p.key for p in _active_profiles() if f"post_now:{p.key}" not in _callbacks
    ]
    check("на каждый канал есть кнопка публикации", _missing_channels, [])

    # ==========================================
    # 5.2. /start РЕАЛЬНО ОТПРАВЛЯЕТ МЕНЮ
    # Проверяем вызовом: раньше здесь падало AttributeError с несуществующим методом.
    # ==========================================
    sent_menu = {}

    class FakeMessageStub:
        def __init__(self):
            self.sent = []

        async def reply_text(self, text, **kwargs):
            self.sent.append(text)

    class FakeBotStub:
        def __init__(self):
            self.messages = []

        async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None):
            self.messages.append((chat_id, text, reply_markup is not None))

    class FakeUpdateStub:
        def __init__(self, chat_id):
            self.effective_chat = SimpleNamespace(id=chat_id)
            self.message = FakeMessageStub()

    bot_ui.app = SimpleNamespace(bot=FakeBotStub())
    update = FakeUpdateStub(chat_id=42)
    await bot_ui.start(update, SimpleNamespace())
    check("/start отправил меню", bot_ui.app.bot.messages[0][0], 42)
    check("/start приложил кнопки", bot_ui.app.bot.messages[0][2], True)

    # ==========================================
    # 6. АРТЕФАКТ ПУБЛИКАЦИИ ЛЕЖИТ В memory_bank, А НЕ В КОРНЕ ПРОЕКТА
    # ==========================================
    import inspect
    source = inspect.getsource(PublisherAgent._send_with_image)
    check("артефакт публикации лежит в memory_bank", 'os.path.join("memory_bank"' in source, True)

    # ==========================================
    # 7. ЗАПУСК НА ХОСТИНГЕ ЧЕРЕЗ ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ
    # ==========================================
    import config as config_module
    import importlib

    env_backup = {k: os.environ.get(k) for k in
                  ("TELEGRAM_TOKEN", "CHANNEL_ID", "ADMIN_ID", "DEEPSEEK_API_KEY",
                   "ODIROUTER_API_KEY", "POST_INTERVAL", "MAX_POST_LENGTH", "USE_LEARNED_BEST_PRACTICES")}

    def reload_with(**values):
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return importlib.reload(config_module).Config

    try:
        cfg = reload_with(
            TELEGRAM_TOKEN="111:server", CHANNEL_ID="@server_channel", ADMIN_ID="555",
            DEEPSEEK_API_KEY="sk-d", ODIROUTER_API_KEY="sk-o",
            POST_INTERVAL="7200", MAX_POST_LENGTH="1000", USE_LEARNED_BEST_PRACTICES="false",
        )
        check("окружение задаёт токен/канал/админа",
              (cfg.TELEGRAM_TOKEN, cfg.CHANNEL_ID, cfg.ADMIN_ID), ("111:server", "@server_channel", 555))
        check("окружение задаёт числовые настройки",
              (cfg.POST_INTERVAL, cfg.MAX_POST_LENGTH), (7200, 1000))
        check("окружение задаёт флаги", cfg.USE_LEARNED_BEST_PRACTICES, False)
        check("обязательные переменные на месте -> замечаний нет", cfg.validate(), [])

        # Битые значения не должны ронять старт процесса
        cfg = reload_with(ADMIN_ID="не число", POST_INTERVAL="", MAX_POST_LENGTH="девять",
                          USE_LEARNED_BEST_PRACTICES="может быть")
        check("битый ADMIN_ID -> 0, старт не падает", cfg.ADMIN_ID, 0)
        check("пустой POST_INTERVAL -> дефолт", cfg.POST_INTERVAL, 10800)
        check("битый MAX_POST_LENGTH -> дефолт", cfg.MAX_POST_LENGTH, 950)
        check("непонятный флаг -> дефолт", cfg.USE_LEARNED_BEST_PRACTICES, True)

        # Без ADMIN_ID бот работает, но предупреждает об открытых командах.
        # Проверяем в отдельном процессе: load_dotenv() вернул бы значение из .env,
        # а на хостинге .env нет — там переменные задаёт панель окружения.
        cfg = reload_with(ADMIN_ID=None)
        check("нет ADMIN_ID -> бот запускается", bool(cfg.TELEGRAM_TOKEN), True)
        check("нет ADMIN_ID -> validate() не падает", cfg.validate() is not None, True)
    finally:
        for key, value in env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        importlib.reload(config_module)

    # Проверки на «пустом» окружении, как на хостинге без .env.
    # load_dotenv() ищет .env ВВЕРХ по дереву каталогов, поэтому песочница обязана
    # лежать ВНЕ папки проекта: подпапка внутри проекта всё равно увидит его .env.
    project_dir = os.getcwd()
    sandbox_root = None
    for base in (os.path.join(os.path.dirname(project_dir), ".botcheck"),
                 os.path.join(tempfile.gettempdir(), ".botcheck")):
        candidate = f"{base}_{uuid.uuid4().hex[:8]}"
        try:
            os.makedirs(candidate)
            sandbox_root = candidate
            break
        except OSError:
            continue

    if sandbox_root is None:
        print("⚠️ Не удалось создать песочницу вне проекта — подпроцессные проверки пропущены")
    sandbox_proj = os.path.join(sandbox_root, "app") if sandbox_root else None
    if sandbox_proj:
        os.makedirs(sandbox_proj, exist_ok=True)
        ignore = shutil.ignore_patterns(
            ".env", "__pycache__", "*.pyc", "*.db", ".tmp_*", "*.log", "memory_bank"
        )
        # .bot.lock и .git — рантайм-артефакты: lock залочен запущенным ботом
        # и копирование падало с PermissionError, а .git не нужен песочнице.
        skip = {".env", "__pycache__", "memory_bank", ".bot.lock", ".git", ".dsh_tmp", ".dsh_tools"}
        for entry in os.listdir(project_dir):
            if entry in skip or entry.startswith(".tmp_"):
                continue
            src = os.path.join(project_dir, entry)
            dst = os.path.join(sandbox_proj, entry)
            try:
                if os.path.isdir(src):
                    shutil.copytree(src, dst, ignore=ignore)
                else:
                    shutil.copy2(src, dst)
            except (PermissionError, OSError) as e:
                # Залоченный файл не должен ронять весь тест: он не участвует
                # в подпроцессных проверках конфигурации.
                print(f"⚠️ Не скопировано в песочницу: {entry} ({e})")

    env = {k: v for k, v in os.environ.items()
           if k not in ("TELEGRAM_TOKEN", "CHANNEL_ID", "ADMIN_ID", "DEEPSEEK_API_KEY",
                        "ODIROUTER_API_KEY", "PYTHONPATH")}
    env["PYTHONPATH"] = sandbox_proj  # строка: список Python молча игнорирует

    def run_probe(script, extra_env=None, tag="report"):
        if sandbox_root is None:
            return "", 1
        env_run = dict(env)
        if extra_env:
            env_run.update(extra_env)
        path = os.path.join(sandbox_root, f"report_{tag}.txt")
        script = script.replace("@REPORT@", path)
        # cwd — отдельная пустая подпапка: рабочая папка не должна быть родителем
        # кода, иначе load_dotenv() поднимется вверх и найдёт .env проекта.
        workdir = os.path.join(sandbox_root, f"work_{tag}")
        os.makedirs(workdir, exist_ok=True)
        completed = subprocess.run(
            [sys.executable, "-c", script], cwd=workdir, env=env_run,
            stdout=subprocess.DEVNULL,
        )
        text = ""
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                text = f.read()
        if not text:
            # stderr не глушим — иначе причина (например, отсутствие модуля) теряется
            print(f"⚠️ Подпроцесс {tag} не оставил отчёта (returncode={completed.returncode})")
        return text, completed.returncode

    missing_report, _ = run_probe(
        "import sys\n"
        "from config import Config\n"
        "try:\n"
        "    Config.validate()\n"
        "    out = 'NO_ERROR'\n"
        "except ValueError as e:\n"
        "    out = 'ERROR:' + str(e)\n"
        "open(r'@REPORT@', 'w', encoding='utf-8').write(out)\n",
        tag="missing",
    )
    check("без обязательных переменных старт падает с понятным текстом",
          "TELEGRAM_TOKEN" in missing_report, True)
    check("сообщение говорит про переменные окружения хостинга",
          "переменных окружения" in missing_report, True)

    probe_ok, _ = run_probe(
        "from config import Config\n"
        "warns = Config.validate()\n"
        "open(r'@REPORT@', 'w', encoding='utf-8').write('WARN:' + '|'.join(warns))\n",
        extra_env={"TELEGRAM_TOKEN": "111:t", "CHANNEL_ID": "@c",
                   "DEEPSEEK_API_KEY": "d", "ODIROUTER_API_KEY": "o"},
        tag="ok",
    )
    check("без ADMIN_ID бот стартует и предупреждает об открытых командах",
          probe_ok.startswith("WARN:") and "ADMIN_ID" in probe_ok, True)

    bad_report, _ = run_probe(
        "from config import Config\n"
        "out = f'ADMIN:{Config.ADMIN_ID} INTERVAL:{Config.POST_INTERVAL}'\n"
        "open(r'@REPORT@', 'w', encoding='utf-8').write(out)\n",
        extra_env={"TELEGRAM_TOKEN": "111:t", "CHANNEL_ID": "@c", "DEEPSEEK_API_KEY": "d",
                   "ODIROUTER_API_KEY": "o", "ADMIN_ID": "abc", "POST_INTERVAL": "девять"},
        tag="bad",
    )
    check("битые значения в окружении не роняют процесс",
          bad_report, "ADMIN:0 INTERVAL:10800")

    if sandbox_root:
        shutil.rmtree(sandbox_root, ignore_errors=True)

    # ==========================================
    # ИТОГ
    # ==========================================
    print("\n" + "=" * 60)
    if all(_checks):
        print(f"✅ ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ ({len(_checks)}/{len(_checks)})")
    else:
        print(f"❌ ПРОВАЛЕНО: {_checks.count(False)} из {len(_checks)}")
    print("=" * 60)

    cleanup()
    tmp_image = os.path.join("memory_bank", "temp_publish.jpg")
    if os.path.exists(tmp_image):
        os.remove(tmp_image)
    return 0 if all(_checks) else 1


async def _identity(ctx, extra):
    ctx.update(extra)
    return ctx


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
