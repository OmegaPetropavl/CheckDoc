# main.py
import os
import sys
import time
import asyncio
import warnings
import logging
import subprocess
import platform

# ===== Общие настройки/логи =====
warnings.filterwarnings("ignore", category=DeprecationWarning)

logger = logging.getLogger("checkdoc")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("[%(levelname)s] %(asctime)s %(name)s: %(message)s"))
    logger.addHandler(_h)

# Режим работы: 'web' (Streamlit) или 'bot' (подпроцесс)
RUN_MODE = os.getenv("RUN_MODE", "web").lower().strip()

# ============================================================
# ================   РЕЖИМ TELEGRAM-БОТА   ====================
# ============================================================

if RUN_MODE == "bot":
    """Запуск чистого aiogram-бота без Streamlit (в подпроцессе)."""

    # На Windows нужна эта policy, на Linux/Streamlit она не мешает
    if platform.system() == "Windows":
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except Exception:
            pass

    import openai
    from aiogram import Bot, Dispatcher, F
    from aiogram.enums import ParseMode
    from aiogram.types import Message
    from aiogram.filters import CommandStart, Command
    from aiogram.client.default import DefaultBotProperties

    # Ключи приходят в окружении из родительского процесса
    OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
    TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
    GPT_ID         = os.environ["GPT_ID"]   # asst_...

    openai.api_key = OPENAI_API_KEY

    async def cmd_start(message: Message):
        await message.answer("👋 Привет! Я ваш ИИ-помощник. Напишите, что вас тревожит.")

    async def cmd_ping(message: Message):
        await message.answer("🏓 pong")

    async def cmd_diag(message: Message):
        await message.answer("✅ Бот активен. Напишите симптомы для консультации.")

    async def handle_text(message: Message):
        user_text = (message.text or "").strip()
        if not user_text:
            await message.answer("Напишите текст вопроса.")
            return

        try:
            # 1) создаём thread
            thread = openai.beta.threads.create()

            # 2) добавляем сообщение пользователя
            openai.beta.threads.messages.create(
                thread_id=thread.id,
                role="user",
                content=user_text,
            )

            # 3) запускаем ассистента
            run = openai.beta.threads.runs.create(
                thread_id=thread.id,
                assistant_id=GPT_ID,
            )

            # 4) ждём завершения
            while True:
                status = openai.beta.threads.runs.retrieve(
                    thread_id=thread.id,
                    run_id=run.id,
                )
                if status.status == "completed":
                    break
                if status.status == "failed":
                    await message.answer("❌ Ассистент не смог ответить.")
                    return
                await asyncio.sleep(0.7)

            # 5) читаем ответ
            msgs = openai.beta.threads.messages.list(thread_id=thread.id)
            reply = None
            for m in msgs.data:
                if m.role == "assistant" and m.content:
                    reply = m.content[0].text.value
                    break

            await message.answer(reply or "⚠️ Ответ ассистента не найден.")
        except Exception as e:
            logger.error("Ошибка в Telegram-боте: %r", e)
            await message.answer("⚠️ Временная ошибка. Попробуйте ещё раз.")

    def build_dp() -> Dispatcher:
        dp = Dispatcher()
        dp.message.register(cmd_start, CommandStart())
        dp.message.register(cmd_ping,  Command("ping"))
        dp.message.register(cmd_diag,  Command("diag"))
        dp.message.register(handle_text, F.text)
        return dp

    async def start_tg_polling():
        bot = Bot(
            token=TELEGRAM_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )

        # ВАЖНО: убираем старый webhook, чтобы работал polling
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            logger.info("Webhook удалён, переходим на polling.")
        except Exception as e:
            logger.warning("Не удалось удалить webhook: %r", e)

        dp = build_dp()
        logger.info("Запуск Telegram-бота (polling)...")
        await dp.start_polling(bot)

    if __name__ == "__main__":
        try:
            asyncio.run(start_tg_polling())
        except KeyboardInterrupt:
            logger.info("Бот остановлен (KeyboardInterrupt).")
        sys.exit(0)

# ============================================================
# ================   РЕЖИМ WEB (STREAMLIT)   =================
# ============================================================

import streamlit as st
import openai

# Секреты из Streamlit Cloud
OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]
TELEGRAM_TOKEN = st.secrets["TELEGRAM_TOKEN"]
GPT_ID         = st.secrets["GPT_ID"]
TELEGRAM_LINK  = "https://t.me/MedAdvice_bot"

openai.api_key = OPENAI_API_KEY

# ---------- управление подпроцессом бота ----------

@st.cache_resource
def _bot_proc_state():
    return {"proc": None, "started": False, "last_error": None}

def ensure_bot_subprocess():
    """Стартует отдельный процесс с RUN_MODE=bot, если ещё не запущен."""
    state = _bot_proc_state()

    # уже запущен и живой
    if state["proc"] and state["proc"].poll() is None:
        return

    env = os.environ.copy()
    env["RUN_MODE"]        = "bot"
    env["OPENAI_API_KEY"]  = OPENAI_API_KEY
    env["TELEGRAM_TOKEN"]  = TELEGRAM_TOKEN
    env["GPT_ID"]          = GPT_ID

    py = sys.executable
    script = os.path.abspath(__file__)

    try:
        proc = subprocess.Popen(
            [py, script],
            env=env,
            stdout=sys.stdout,   # пишем логи прямо в Streamlit Logs
            stderr=sys.stderr,
        )
        state["proc"] = proc
        state["started"] = True
        state["last_error"] = None
        logger.info("Подпроцесс Telegram-бота запущен, pid=%s", proc.pid)
    except Exception as e:
        state["last_error"] = repr(e)
        logger.error("Ошибка запуска подпроцесса бота: %r", e)

# ---------- чатовые утилиты (Assistants API) ----------

def init_chat_session():
    if "thread_id" not in st.session_state:
        thread = openai.beta.threads.create()
        st.session_state.thread_id = thread.id
    if "messages" not in st.session_state:
        st.session_state.messages = []  # [{"role": "user"/"assistant", "content": str}]

def render_chat():
    for m in st.session_state.messages:
        with st.chat_message("user" if m["role"] == "user" else "assistant"):
            st.markdown(m["content"])

def add_msg(role: str, text: str):
    st.session_state.messages.append({"role": role, "content": text})

def ask_assistant(user_text: str) -> str:
    thread_id = st.session_state.thread_id

    openai.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=user_text,
    )

    run = openai.beta.threads.runs.create(
        thread_id=thread_id,
        assistant_id=GPT_ID,
    )

    while True:
        status = openai.beta.threads.runs.retrieve(
            thread_id=thread_id,
            run_id=run.id,
        )
        if status.status == "completed":
            break
        if status.status == "failed":
            return "❌ Ассистент не смог ответить."
        time.sleep(0.7)

    msgs = openai.beta.threads.messages.list(thread_id=thread_id)
    for m in msgs.data:
        if m.role == "assistant" and m.content:
            return m.content[0].text.value

    return "⚠️ Ответ ассистента не найден."

# ---------- Streamlit UI ----------

def streamlit_app():
    # Автозапуск бота
    ensure_bot_subprocess()

    st.set_page_config(page_title="CheckDoc — Виртуальный доктор", page_icon="💊")
    st.title("💊 CheckDoc — Виртуальный доктор")
    st.link_button("Открыть бота в Telegram", TELEGRAM_LINK)

    # Статус бота (сайдбар)
    state = _bot_proc_state()
    with st.sidebar:
        st.subheader("Статус бота")
        if state["proc"] and state["proc"].poll() is None:
            st.write("✅ Запущен (подпроцесс)")
        else:
            st.write("⏳ Бот не запущен или завершился")
        if state["last_error"]:
            st.error(f"Последняя ошибка: {state['last_error']}")
        st.write("Команды: /start, /ping, /diag")

    st.divider()
    st.subheader("Веб-чат")

    init_chat_session()
    render_chat()

    user_text = st.chat_input("Опишите симптомы или задайте вопрос…")
    if user_text:
        add_msg("user", user_text)
        with st.chat_message("user"):
            st.markdown(user_text)

        with st.chat_message("assistant"):
            with st.spinner("ИИ печатает…"):
                try:
                    answer = ask_assistant(user_text)
                except Exception as e:
                    logger.error("Ошибка в веб-чате: %r", e)
                    answer = f"Ошибка: {e}"
                st.markdown(answer)
                add_msg("assistant", answer)

# Точка входа веба
if __name__ == "__main__" or "streamlit" in sys.modules:
    streamlit_app()