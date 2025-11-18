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
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(levelname)s] %(asctime)s %(name)s: %(message)s"))
    logger.addHandler(_h)

# ===== Режимы =====
RUN_MODE = os.getenv("RUN_MODE", "web")  # 'web' (Streamlit) или 'bot' (подпроцесс)

# ====== BOT MODE (чистый aiogram в отдельном процессе) ======
if RUN_MODE == "bot":
    # На Windows безопасная policy
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

    OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
    TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
    GPT_ID         = os.environ["GPT_ID"]

    openai.api_key = OPENAI_API_KEY

    async def cmd_start(message: Message):
        await message.answer("👋 Привет! Я ваш ИИ-помощник. Напишите, что вас тревожит.")

    async def cmd_ping(message: Message):
        await message.answer("🏓 pong")

    async def cmd_diag(message: Message):
        await message.answer("✅ Бот активен. Напишите симптомы для консультации.")

    async def handle_text(message: Message):
        user_text = message.text or ""
        try:
            thread = openai.beta.threads.create(messages=[{"role": "user", "content": user_text}])
            run = openai.beta.threads.runs.create(thread_id=thread.id, assistant_id=GPT_ID)
            while True:
                status = openai.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
                if status.status == "completed":
                    break
                if status.status == "failed":
                    await message.answer("❌ Ассистент не смог ответить.")
                    return
                await asyncio.sleep(0.7)
            msgs = openai.beta.threads.messages.list(thread_id=thread.id)
            reply = None
            for m in msgs.data:
                if m.role == "assistant":
                    reply = m.content[0].text.value
                    break
            await message.answer(reply or "⚠️ Ответ ассистента не найден.")
        except Exception:
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
            default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )
        dp = build_dp()
        # В главном процессе подпроцесса — сигналы уже ОК
        await dp.start_polling(bot)

    if __name__ == "__main__":
        asyncio.run(start_tg_polling())
        sys.exit(0)

# ===== WEB MODE (Streamlit: UI + автозапуск подпроцесса бота) =====
# Тут импортируем streamlit только в веб-режиме
import streamlit as st
import openai

# Секреты из Streamlit Cloud
OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]
TELEGRAM_TOKEN = st.secrets["TELEGRAM_TOKEN"]
GPT_ID         = st.secrets["GPT_ID"]
TELEGRAM_LINK  = "https://t.me/MedAdvice_bot"

openai.api_key = OPENAI_API_KEY

# --- управление подпроцессом бота ---
@st.cache_resource
def _bot_proc_state():
    return {"proc": None, "started": False, "last_error": None}

def ensure_bot_subprocess():
    state = _bot_proc_state()
    if state["started"] and state["proc"] and state["proc"].poll() is None:
        return
    env = os.environ.copy()
    env["RUN_MODE"]      = "bot"
    env["OPENAI_API_KEY"] = OPENAI_API_KEY
    env["TELEGRAM_TOKEN"] = TELEGRAM_TOKEN
    env["GPT_ID"]         = GPT_ID

    # Запускаем ЭТОТ ЖЕ файл как отдельный процесс в режиме бота
    py = sys.executable
    script = os.path.abspath(__file__)
    try:
        proc = subprocess.Popen(
            [py, script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env
        )
        state["proc"] = proc
        state["started"] = True
        state["last_error"] = None
    except Exception as e:
        state["last_error"] = repr(e)

# --- чатовые утилиты (Assistants API) ---
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
    openai.beta.threads.messages.create(thread_id=thread_id, role="user", content=user_text)
    run = openai.beta.threads.runs.create(thread_id=thread_id, assistant_id=GPT_ID)
    while True:
        status = openai.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
        if status.status == "completed":
            break
        if status.status == "failed":
            return "❌ Ассистент не смог ответить."
        time.sleep(0.7)
    msgs = openai.beta.threads.messages.list(thread_id=thread_id)
    for m in msgs.data:
        if m.role == "assistant":
            return m.content[0].text.value
    return "⚠️ Ответ ассистента не найден."

# --- Streamlit UI ---
def streamlit_app():
    # Автозапуск подпроцесса бота
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
            st.write("⏳ Стартуется…")
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
                    answer = f"Ошибка: {e}"
                st.markdown(answer)
                add_msg("assistant", answer)

    
    

# Точка входа веба
if __name__ == "__main__" or "streamlit" in sys.modules:
    streamlit_app()
