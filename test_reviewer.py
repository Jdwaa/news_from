# test_reviewer.py

"""
Проверка ревизора для ДВУХ каналов.

Ревизор применяет разный чек-лист: новостному каналу нужны КАПС, эмодзи и
прогноз, каналу лайфхаков — практическая часть (КАПС и прогнозы там запрещены).
Раньше чек-лист был один, и посты лайфхаков отбраковывались все подряд.
"""

import asyncio
import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from agents.reviewer_agent import ReviewerAgent, count_emojis
from channels import PROFILES, NEWS, TIPS
from database import init_db

_checks = []


def check(name, actual, expected):
    ok = actual == expected
    _checks.append(ok)
    print(f"{'✅' if ok else '❌'} {name}: получено {actual!r}, ожидалось {expected!r}")


# Хороший пост новостного канала: КАПС, эмодзи (>=3), прогноз, 2+ абзаца.
# Вступление — утверждение/факт, а не риторический вопрос: ревизор теперь
# отбраковывает избитые зачины вроде «Представьте…» и вопросы в первом абзаце.
NEWS_GOOD = """
🚀 ИИ УЖЕ ЗДЕСЬ: ChatGPT научился думать как человек 🧠

DeepMind выпустили модель, которая может рассуждать и планировать шаги вперёд — без подсказок и разжёвывания по пунктам. Это уже не фантастика, а работающий продукт, который доступен сегодня. 🔥

Мне кажется, это изменит всё. Через 5 лет такие модели будут помогать врачам ставить диагнозы, а учителям — объяснять сложные темы. И это только начало! ✨

Вопрос не в том, БУДЕТ ли это, а в том, КОГДА это станет нашей реальностью.

#AI #Tech #Future
"""

# Хороший лайфхак: без КАПСА и прогнозов, но с эмодзи и практической частью.
# Объём соответствует лимиту канала (700-2100): инструкции нужно место
# на шаги, пример и оговорку, иначе она получается «пресной».
TIPS_GOOD = """
Как за 5 минут сделать расшифровку созвона 🎧

Знакомая ситуация: часовой созвон, а потом нужно вспомнить, о чём договорились.
Переслушивать некогда, а конспект никто не вёл. Знакомо? 🤔

💡 Решение: загружаешь запись в Whisper — он бесплатный и работает в браузере.
Дальше три шага. Первый: открой сервис и загрузи файл записи. Второй: выбери язык
и дождись расшифровки. Третий: скопируй текст в ChatGPT и попроси саммари таким
промптом — «выдели решения и задачи с ответственными, укажи сроки». Инструмент
понимает русскую речь и не требует настройки.

📌 Пример: встреча по проекту на 40 минут. Раньше я тратил час, чтобы разобрать
записи и собрать задачи в список. Теперь на это уходит 5 минут: расшифровка готова,
список задач сформирован, копируешь в заметки и отправляешь команде. Экономия
почти час на каждой встрече, а за неделю это уже половина рабочего дня.

⚠️ Оговорка: качество падает, если запись шумная или говорят одновременно.
Тогда возьми сервис с разделением голосов по спикерам — иначе в тексте будет каша
из реплик, и разбирать её придётся вручную.

Попробуй на следующем созвоне — сэкономишь час. 🚀

#лайфхак #ИИ #продуктивность
"""

BAD_POST = "Просто короткий пост без эмодзи и мнения."

# Текст, написанный латиницей: должен быть отбракован как непереведённый
ENGLISH_POST = (
    "How to use AI for your daily tasks\n\n"
    "You can use ChatGPT to summarize long emails quickly. "
    "Just paste the text and ask for a summary with key points. "
    "This saves a lot of time every single day for busy people. "
    "The tool works in a browser and needs no setup at all. "
    "Try it today and see how much faster your work becomes.\n\n"
    "Use it every day and your routine becomes easier than before. "
    "Many people already do this and report great results in their work. "
    "The best part is that everything is absolutely free of charge. "
    "Start with one simple task and see how it goes for you today. "
    "AI is here to help you save your time and focus on what matters."
)


def run(agent, text, title, profile):
    """Прогоняет ревизора синхронно и возвращает (одобрен, число замечаний, фидбек)."""
    ctx = {"post_text": text, "post_title": title, "channel_profile": profile}
    res = asyncio.get_event_loop().run_until_complete(agent.execute(ctx))
    return bool(res.get("text_approved")), res.get("reviewer_issues_count", 0), res.get("reviewer_feedback", "")


async def main():
    init_db()
    agent = ReviewerAgent()

    # --- подсчёт эмодзи: раньше регулярка видела только смайлики ---
    check("эмодзи 🎧 💡 📌 считаются", count_emojis("🎧 💡 📌") >= 3, True)
    check("в обычном тексте эмодзи нет", count_emojis("Привет, мир"), 0)

    # --- лимиты длины различаются по каналам ---
    from channels import PROFILES as _P
    check("у лайфхаков лимит выше новостного",
          _P[TIPS].min_length > _P[NEWS].min_length, True)
    check("минимум лайфхаков не меньше 700", _P[TIPS].min_length >= 700, True)

    # --- новостной канал ---
    print("\n" + "=" * 60)
    print("📋 ХОРОШИЙ ПОСТ (news)")
    print("=" * 60)
    ctx = {"post_text": NEWS_GOOD, "post_title": "🚀 ИИ УЖЕ ЗДЕСЬ: ChatGPT научился думать как человек? 🧠",
           "channel_profile": PROFILES[NEWS]}
    res = await agent.execute(ctx)
    check("новостной пост принят", res.get("text_approved"), True)

    print("\n" + "=" * 60)
    print("📋 ПЛОХОЙ ПОСТ (news)")
    print("=" * 60)
    ctx_bad = {"post_text": BAD_POST, "post_title": "Новость",
               "channel_profile": PROFILES[NEWS]}
    res_bad = await agent.execute(ctx_bad)
    check("плохой пост отклонён", res_bad.get("text_approved"), False)
    check("у плохого поста есть замечания", res_bad.get("reviewer_issues_count", 0) > 0, True)

    # --- канал лайфхаков ---
    print("\n" + "=" * 60)
    print("📋 ХОРОШИЙ ЛАЙФХАК (tips)")
    print("=" * 60)
    ctx_tips = {"post_text": TIPS_GOOD, "post_title": "Как за 5 минут сделать расшифровку созвона",
                "channel_profile": PROFILES[TIPS]}
    res_tips = await agent.execute(ctx_tips)
    check("лайфхак принят без КАПСА и прогноза", res_tips.get("text_approved"), True)

    # Лайфхак, в котором нет практической части, должен быть отклонён
    print("\n" + "=" * 60)
    print("📋 ЛАЙФХАК БЕЗ ПРАКТИКИ (tips)")
    print("=" * 60)
    empty_tips = ("Компания представила новую модель. Это большое событие для отрасли. "
                  "Многие эксперты отмечают высокий уровень исполнения. 🎯💡📌\n\n"
                  "Отрасль продолжает развиваться, и мы наблюдаем интересные процессы. "
                  "Остаётся следить за новостями и ждать дальнейших анонсов. 🚀✨")
    ctx_empty = {"post_text": empty_tips, "post_title": "О новой модели",
                 "channel_profile": PROFILES[TIPS]}
    res_empty = await agent.execute(ctx_empty)
    check("лайфхак без практической части отклонён", res_empty.get("text_approved"), False)

    # Англоязычный текст должен отбраковываться в обоих каналах
    print("\n" + "=" * 60)
    print("📋 АНГЛОЯЗЫЧНЫЙ ПОСТ (tips) — должен требовать перевода")
    print("=" * 60)
    ctx_en = {"post_text": ENGLISH_POST, "post_title": "How to use AI daily",
              "channel_profile": PROFILES[TIPS]}
    res_en = await agent.execute(ctx_en)
    check("непереведённый пост отклонён", res_en.get("text_approved"), False)
    check("причина — не русский язык",
          "не по-русски" in (res_en.get("reviewer_feedback") or ""), True)

    # ==========================================
    # ЗАЧИН ПОСТА: избитые фразы и вопросы запрещены
    # ==========================================
    # Промпт раньше требовал «Знакомая картина?», и каждый пост открывался
    # одним и тем же вопросом. Теперь такие зачины отклоняются.
    print("\n" + "=" * 60)
    print("🚪 ПРОВЕРКА ЗАЧИНА")
    print("=" * 60)

    async def first_para_issue(text):
        """True, если ревизор отклонил зачин (штамп или вопрос)."""
        probe = {"post_text": text, "post_title": "Как за 5 минут сделать расшифровку созвона",
                 "channel_profile": PROFILES[TIPS]}
        r = await agent.execute(probe)
        fb = r.get("reviewer_feedback") or ""
        return ("Избитый зачин" in fb) or ("начинается с вопроса" in fb)

    check("зачин «Знакомая ситуация» отклонён",
          await first_para_issue("Знакомая ситуация: созвон, а потом надо вспомнить детали.\n\n"
                                 + TIPS_GOOD.strip()), True)
    check("зачин-вопрос отклонён",
          await first_para_issue("Часто забываешь, о чём договорились на созвоне?\n\n"
                                 + TIPS_GOOD.strip()), True)
    check("зачин-сцена не отклонён",
          await first_para_issue("Часовой созвон, а через час надо вспомнить, о чём договорились.\n\n"
                                 + TIPS_GOOD.strip()), False)

    print("\n" + "=" * 60)
    if all(_checks):
        print(f"✅ ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ ({len(_checks)}/{len(_checks)})")
    else:
        print(f"❌ ПРОВАЛЕНО: {_checks.count(False)} из {len(_checks)}")
    print("=" * 60)
    return 0 if all(_checks) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
