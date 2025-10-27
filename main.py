import os
import time
import openai
import asyncio
import nest_asyncio
import streamlit as st
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
ApplicationBuilder,
CommandHandler,
MessageHandler,
ContextTypes,
filters,
)

# Устанавливаем поддержку Replit-окружения
nest_asyncio.apply()
load_dotenv() 

# Переменные среды


OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]
TELEGRAM_TOKEN = st.secrets["TELEGRAM_TOKEN"]
GPT_ID = st.secrets["GPT_ID"]

# Настройка OpenAI
openai.api_key = OPENAI_API_KEY

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Здравствуйте! Я ваш ИИ-помощник. Напишите мне, что вас тревожит?")

# Обработка текстовых сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text

    try:
        thread = openai.beta.threads.create(
            messages=[{"role": "user", "content": user_message}]
        )

        run = openai.beta.threads.runs.create(
            thread_id=thread.id,
            assistant_id=GPT_ID,
        )

        while True:
            run_status = openai.beta.threads.runs.retrieve(
                thread_id=thread.id,
                run_id=run.id
            )
            if run_status.status == "completed":
                break
            elif run_status.status == "failed":
                await update.message.reply_text("❌ Агент не смог ответить.")
                return
            await asyncio.sleep(1)

        messages = openai.beta.threads.messages.list(thread_id=thread.id)
        reply = messages.data[0].content[0].text.value
        await update.message.reply_text(reply)

    except Exception as e:
        await update.message.reply_text(f"⚠️ Ошибка: {str(e)}")

# Главная функция
async def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("🤖 Бот запущен и работает...")
    await app.run_polling()

# Запуск с поддержкой Replit
if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
