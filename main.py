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

# -------------------------------
# Настройки и секреты (Streamlit)
# -------------------------------
# Берём ключи из Streamlit Cloud → App → Settings → Secrets
OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]
TELEGRAM_TOKEN = st.secrets["TELEGRAM_TOKEN"]
GPT_ID = st.secrets["GPT_ID"]  # asst_...

# Убираем депрекейшн-варнинги про Assistants API
warnings.filterwarnings("ignore", category=DeprecationWarning)

# OpenAI (старый Assistants API)
openai.api_key = OPENAI_API_KEY


# ==========================
#     TELEGRAM (aiogram 3)
# ==========================
async def tg_cmd_start(message: Message):
    await message.answer("👋 Здравствуйте! Я ваш ИИ-помощник. Напишите, что вас тревожит?")

async def tg_handle_text(message: Message):
    user_text = message.text or ""
    try:
        # Создаём поток (thread) и запускаем ассистента без system prompt
        thread = openai.beta.threads.create(
            messages=[{"role": "user", "content": user_text}]
        )
        run = openai.beta.threads.runs.create(
            thread_id=thread.id,
            assistant_id=GPT_ID,
        )

        # Ждём завершения ответа ассистента
        while True:
            run_status = openai.beta.threads.runs.retrieve(
                thread_id=thread.id,
                run_id=run.id
            )
            if run_status.status == "completed":
                break
            if run_status.status == "failed":
                await message.answer("❌ Ассистент не смог ответить.")
                return
            await asyncio.sleep(0.8)

        # Получаем ответ
        messages_list = openai.beta.threads.messages.list(thread_id=thread.id)
        reply = messages_list.data[0].content[0].text.value
        await message.answer(reply)

    except Exception as e:
        await message.answer(f"⚠️ Ошибка: {e}")

def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.message.register(tg_cmd_start, CommandStart())
    dp.message.register(tg_handle_text, F.text)
    return dp

async def start_telegram_bot():
    bot = Bot(token=TELEGRAM_TOKEN, parse_mode=ParseMode.HTML)
    dp = build_dispatcher()
    # skip старые сообщения; оставим resolve_used_update_types()
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


# ==========================================================
#     ФОНОВЫЙ ЗАПУСК БОТА ДЛЯ РЕЖИМА STREAMLIT (один раз)
# ==========================================================
@st.cache_resource
def _bot_runtime():
    return {"started": False, "thread": None}

def start_bot_in_background_once():
    runtime = _bot_runtime()
    if runtime["started"]:
        return
    def _thread_target():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(start_telegram_bot())
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
    th = threading.Thread(target=_thread_target, name="tg-bot-thread", daemon=True)
    th.start()
    runtime["started"] = True
    runtime["thread"] = th


# ==================
#     STREAMLIT UI
# ==================
def streamlit_app():
    st.set_page_config(page_title="CheckDoc — Виртуальный доктор", page_icon="💊")
    st.title("💊 CheckDoc — Виртуальный доктор")
    st.caption("Telegram-бот + веб-чат (Assistants API)")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("▶️ Запустить Telegram-бота"):
            start_bot_in_background_once()
            st.success("Бот запущен в фоне. Напишите своему боту в Telegram.")

    with col2:
        st.markdown("**Статус:** бот будет работать в фоне даже при обновлении страницы.")

    st.divider()
    st.subheader("Веб-чат")

    user_msg = st.text_input("Опишите симптомы или задайте вопрос:")
    if st.button("Отправить в ассистента") and user_msg.strip():
        try:
            thread = openai.beta.threads.create(
                messages=[{"role": "user", "content": user_msg.strip()}]
            )
            run = openai.beta.threads.runs.create(
                thread_id=thread.id,
                assistant_id=GPT_ID,
            )

            # Ждём завершения (в синхронном контексте Streamlit — без await)
            while True:
                run_status = openai.beta.threads.runs.retrieve(
                    thread_id=thread.id,
                    run_id=run.id
                )
                if run_status.status == "completed":
                    break
                if run_status.status == "failed":
                    st.error("❌ Ассистент не смог ответить.")
                    return
                time.sleep(0.8)

            messages_list = openai.beta.threads.messages.list(thread_id=thread.id)
            answer = messages_list.data[0].content[0].text.value
            st.markdown("**Ответ ИИ:**")
            st.write(answer)

        except Exception as e:
            st.error(f"Ошибка: {e}")

    st.divider()
    


# =================================
#     ТОЧКИ ВХОДА В ПРИЛОЖЕНИЕ
# =================================
if "streamlit" in sys.modules:
    # Запуск как Streamlit-приложение (cloud/локально с `streamlit run main.py`)
    streamlit_app()
else:
    # Обычный запуск файла: python main.py  → только Telegram-бот
    if __name__ == "__main__":
        asyncio.run(start_telegram_bot())