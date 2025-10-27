import sys
import threading
import asyncio
import time
import warnings

import streamlit as st
import openai
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.types import Message
from aiogram.filters import CommandStart

# =========================
#  СЕКРЕТЫ (Streamlit Cloud)
# =========================
OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]
TELEGRAM_TOKEN = st.secrets["TELEGRAM_TOKEN"]
GPT_ID         = st.secrets["GPT_ID"]  # ассистент (asst_...)

# Ссылка на бота в Telegram
TELEGRAM_BOT_LINK = "https://t.me/MedAdvice_bot"

# Глушим DeprecationWarning для Assistants API
warnings.filterwarnings("ignore", category=DeprecationWarning)

# OpenAI (старый интерфейс)
openai.api_key = OPENAI_API_KEY


# ======================================================
#                TELEGRAM BOT (aiogram 3)
# ======================================================
async def tg_cmd_start(message: Message):
    await message.answer("👋 Привет! Я ваш ИИ-помощник. Напишите, что вас тревожит.")

async def tg_handle_text(message: Message):
    user_text = message.text or ""
    try:
        # Для Telegram: короткая сессия (новый thread на запрос)
        thread = openai.beta.threads.create(
            messages=[{"role": "user", "content": user_text}]
        )
        run = openai.beta.threads.runs.create(
            thread_id=thread.id,
            assistant_id=GPT_ID,
        )

        while True:
            status = openai.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
            if status.status == "completed":
                break
            if status.status == "failed":
                await message.answer("❌ Ассистент не смог ответить.")
                return
            await asyncio.sleep(0.8)

        msgs = openai.beta.threads.messages.list(thread_id=thread.id)
        reply = None
        for m in msgs.data:  # ищем первый ответ ассистента
            if m.role == "assistant":
                reply = m.content[0].text.value
                break
        await message.answer(reply or "⚠️ Ответ ассистента не найден.")
    except Exception as e:
        await message.answer(f"⚠️ Ошибка: {e}")

def build_dp() -> Dispatcher:
    dp = Dispatcher()
    dp.message.register(tg_cmd_start, CommandStart())
    dp.message.register(tg_handle_text, F.text)
    return dp

async def start_telegram_bot():
    bot = Bot(token=TELEGRAM_TOKEN, parse_mode=ParseMode.HTML)
    dp = build_dp()
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


# ======================================================
#      ФОНОВЫЙ АВТОСТАРТ БОТА ДЛЯ STREAMLIT
# ======================================================
@st.cache_resource
def _bot_runtime():
    """Кэшируем состояние, чтобы бот стартовал один раз за процесс."""
    return {"started": False, "thread": None}

def start_bot_in_background_once():
    rt = _bot_runtime()
    if rt["started"]:
        return
    def _target():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(start_telegram_bot())
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
    th = threading.Thread(target=_target, name="tg-bot-thread", daemon=True)
    th.start()
    rt["started"] = True
    rt["thread"] = th


# ======================================================
#                   STREAMLIT  UI (чат)
# ======================================================
def init_chat_session():
    if "thread_id" not in st.session_state:
        # Для веб-чата создаём PERSISTENT thread (история сохраняется)
        thread = openai.beta.threads.create()
        st.session_state.thread_id = thread.id
    if "messages" not in st.session_state:
        st.session_state.messages = []  # [{"role": "user"/"assistant", "content": str}, ...]

def render_chat():
    for msg in st.session_state.messages:
        with st.chat_message("user" if msg["role"] == "user" else "assistant"):
            st.markdown(msg["content"])

def add_user_message(text: str):
    st.session_state.messages.append({"role": "user", "content": text})

def add_assistant_message(text: str):
    st.session_state.messages.append({"role": "assistant", "content": text})

def ask_assistant_via_thread(user_text: str) -> str:
    """Отправка в существующий thread веб-чата и ожидание ответа ассистента."""
    thread_id = st.session_state.thread_id

    openai.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=user_text
    )
    run = openai.beta.threads.runs.create(
        thread_id=thread_id,
        assistant_id=GPT_ID,
    )

    while True:
        status = openai.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
        if status.status == "completed":
            break
        if status.status == "failed":
            return "❌ Ассистент не смог ответить."
        time.sleep(0.8)

    msgs = openai.beta.threads.messages.list(thread_id=thread_id)
    for m in msgs.data:
        if m.role == "assistant":
            return m.content[0].text.value
    return "⚠️ Ответ ассистента не найден."

def streamlit_app():
    # 👉 Автостарт Telegram-бота при загрузке страницы (без кнопки)
    start_bot_in_background_once()

    st.set_page_config(page_title="CheckDoc — Виртуальный доктор", page_icon="💊")
    st.title("💊 CheckDoc — Виртуальный доктор")
    

    # Ссылка на бота
    st.link_button("Открыть бота в Telegram", TELEGRAM_BOT_LINK)

    st.divider()
    st.subheader("Веб-чат")

    init_chat_session()
    render_chat()

    # Поле ввода в стиле чата
    user_text = st.chat_input("Опишите симптомы или задайте вопрос…")
    if user_text:
        add_user_message(user_text)
        with st.chat_message("user"):
            st.markdown(user_text)

        # Ответ ассистента
        with st.chat_message("assistant"):
            with st.spinner("ИИ печатает…"):
                try:
                    answer = ask_assistant_via_thread(user_text)
                except Exception as e:
                    answer = f"Ошибка: {e}"
                st.markdown(answer)
                add_assistant_message(answer)

    st.divider()
    


# =================================
#     ТОЧКИ ВХОДА (оба сценария)
# =================================
if "streamlit" in sys.modules:
    # Запущено как Streamlit-приложение → веб и бот поднимаются вместе
    streamlit_app()
else:
    # Прямой запуск файла → только Telegram-бот
    if __name__ == "__main__":
        asyncio.run(start_telegram_bot())
