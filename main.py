import sys
import threading
import asyncio
import time
import warnings
import logging

import streamlit as st
import openai
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import Message

# ========= СЕКРЕТЫ =========
OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]
TELEGRAM_TOKEN = st.secrets["TELEGRAM_TOKEN"]
GPT_ID         = st.secrets["GPT_ID"]          # asst_...
TELEGRAM_LINK  = "https://t.me/CheckDoc"       # ссылка на бота

# Глушим DeprecationWarning для Assistants API
warnings.filterwarnings("ignore", category=DeprecationWarning)

# OpenAI (старый Assistants API)
openai.api_key = OPENAI_API_KEY

# ========= ЛОГИ aiogram/бота =========
logger = logging.getLogger("checkdoc")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
formatter = logging.Formatter("[%(levelname)s] %(asctime)s %(name)s: %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)

# ======= TELEGRAM BOT (aiogram 3) =======
async def cmd_start(message: Message):
    await message.answer("👋 Привет! Я ваш ИИ-помощник. Напишите, что вас тревожит.")

async def cmd_ping(message: Message):
    await message.answer("🏓 pong")

async def cmd_diag(message: Message):
    try:
        # Мини-проверка OpenAI (без ассистента, просто echo)
        _ = OPENAI_API_KEY[:6] + "..."
        await message.answer("✅ Бот жив. OpenAI ключ загружен. Попробуйте написать симптомы.")
    except Exception as e:
        await message.answer(f"❌ Диагностика: {e!r}")

async def handle_text(message: Message):
    user_text = message.text or ""
    try:
        # 1) Создаём короткий thread под конкретный запрос
        thread = openai.beta.threads.create(
            messages=[{"role": "user", "content": user_text}]
        )
        # 2) Запускаем ассистента
        run = openai.beta.threads.runs.create(
            thread_id=thread.id,
            assistant_id=GPT_ID,
        )
        # 3) Ожидаем готовность
        while True:
            status = openai.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
            if status.status == "completed":
                break
            if status.status == "failed":
                logger.error("Assistant run failed: %s", status)
                await message.answer("❌ Ассистент не смог ответить.")
                return
            await asyncio.sleep(0.7)

        # 4) Достаём ответ ассистента
        msgs = openai.beta.threads.messages.list(thread_id=thread.id)
        reply = None
        for m in msgs.data:
            if m.role == "assistant":
                reply = m.content[0].text.value
                break
        if not reply:
            reply = "⚠️ Ответ ассистента не найден."
        await message.answer(reply)
    except Exception as e:
        logger.exception("Handler error:")
        await message.answer("⚠️ Временная ошибка обработки. Попробуйте ещё раз через минуту.")

def build_dp() -> Dispatcher:
    dp = Dispatcher()
    dp.message.register(cmd_start, CommandStart())
    dp.message.register(cmd_ping,  Command("ping"))
    dp.message.register(cmd_diag,  Command("diag"))
    dp.message.register(handle_text, F.text)
    return dp

async def start_tg_polling():
    logger.info("Starting Telegram bot polling…")
    bot = Bot(token=TELEGRAM_TOKEN, parse_mode=ParseMode.HTML)
    dp = build_dp()
    # ВАЖНО: без ограничения allowed_updates — получаем всё
    await dp.start_polling(bot)
    # (если здесь упадём, увидим ошибку в логах)
    logger.info("Polling finished.")

# ======= ФОНОВЫЙ АВТОСТАРТ БОТА ДЛЯ STREAMLIT =======
@st.cache_resource
def _bot_runtime():
    """Чтобы бот стартовал один раз за процесс Streamlit."""
    return {"started": False, "thread": None, "last_error": None}

def ensure_bot_running():
    rt = _bot_runtime()
    if rt["started"]:
        return
    def _target():
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(start_tg_polling())
        except Exception as e:
            rt["last_error"] = repr(e)
            logger.exception("Bot thread crashed:")
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()
    th = threading.Thread(target=_target, name="tg-bot-thread", daemon=True)
    th.start()
    rt["started"] = True
    rt["thread"] = th

# ======= STREAMLIT: чат на Assistants API с историей =======
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

def streamlit_app():
    # автозапуск бота (без кнопок)
    ensure_bot_running()

    st.set_page_config(page_title="CheckDoc — Виртуальный доктор", page_icon="💊")
    st.title("💊 CheckDoc — Виртуальный доктор")
    st.caption("Веб-чат (Assistants API) + Telegram-бот (aiogram 3) запускаются одновременно.")

    # Ссылка на Telegram-бота
    st.link_button("Открыть бота в Telegram", TELEGRAM_LINK)

    # Покажем в сайдбаре статус бота
    rt = _bot_runtime()
    with st.sidebar:
        st.subheader("Статус бота")
        st.write("✅ Запущен" if rt["started"] else "⏳ Стартуется…")
        if rt["last_error"]:
            st.error(f"Последняя ошибка: {rt['last_error']}")
        st.caption("Попробуйте в Telegram команды: /start, /ping, /diag")

    st.divider()
    st.subheader("Веб-чат")

    init_chat_session()
    render_chat()

    # Ввод и ответ в стиле чата
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

    st.divider()
    st.caption("Секреты берутся из st.secrets. Используется старый OpenAI Assistants API.")

# ======= ТОЧКИ ВХОДА =======
if "streamlit" in sys.modules:
    streamlit_app()
else:
    if __name__ == "__main__":
        asyncio.run(start_tg_polling())
