# railway.py
import os
import uuid
import warnings
import asyncio
from typing import Dict, Optional, TypedDict

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

import openai

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import Message, Update
from aiogram.client.default import DefaultBotProperties

# ===================== ENV & helpers =====================

def require_env(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return val

OPENAI_API_KEY = require_env("OPENAI_API_KEY")
TELEGRAM_TOKEN = require_env("TELEGRAM_TOKEN")
GPT_ID         = require_env("GPT_ID")                      # asst_...
WEBHOOK_BASE   = "https://checkdoc.up.railway.app"                 # e.g. https://<project>.up.railway.app
WEBHOOK_PATH   = "/telegram/webhook"
WEBHOOK_URL    = (WEBHOOK_BASE.rstrip("/") + WEBHOOK_PATH) if WEBHOOK_BASE else None
TELEGRAM_LINK  = os.getenv("TELEGRAM_LINK", "https://t.me/MedAdvice_bot")

# ===================== OpenAI setup ======================
warnings.filterwarnings("ignore", category=DeprecationWarning)  # глушим Deprecation для Assistants API
openai.api_key = OPENAI_API_KEY

# ===================== Aiogram (Telegram) ================
bot = Bot(
    token=TELEGRAM_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()

# Привязка thread к пользователю Telegram, чтобы сохранялся контекст
TG_THREADS: Dict[int, str] = {}

@dp.message(CommandStart())
async def tg_start(m: Message):
    await m.answer("👋 Привет! Это Telegram-версии CheckDoc. Опишите симптомы или задайте вопрос.")

@dp.message()
async def tg_text(m: Message):
    text = (m.text or "").strip()
    if not text:
        await m.answer("Напишите текст вопроса.")
        return
    try:
        thread_id = TG_THREADS.get(m.from_user.id)
        if not thread_id:
            # создаём новый thread для этого пользователя
            thread = await asyncio.to_thread(openai.beta.threads.create)
            thread_id = thread.id
            TG_THREADS[m.from_user.id] = thread_id

        # кладём сообщение и запускаем ассистента в рамках thread
        reply = await run_assistant_in_thread(thread_id, text)
        await m.answer(reply or "⚠️ Ответ ассистента не найден.")
    except Exception:
        await m.answer("⚠️ Временная ошибка. Попробуйте ещё раз.")

# ===================== FastAPI (Web + Webhook) ===========
app = FastAPI(title="CheckDoc (Railway)")

# простейшее in-memory: session_id -> thread_id (для веб-чата)
WEB_SESSIONS: Dict[str, str] = {}

def _html_page(gpt_id: str, tg_link: str) -> str:
    return f"""<!doctype html>
<html lang="ru"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>CheckDoc — Веб-чат</title>
<style>
:root {{ --brand:#0ea5e9; --bg:#f7f7fb; --text:#111827; --muted:#6b7280; }}
* {{ box-sizing: border-box; }}
body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:var(--bg);color:var(--text)}}
header{{background:var(--brand);color:#fff;padding:16px 20px}}
main{{max-width:900px;margin:22px auto;padding:0 16px}}
.card{{background:#fff;border-radius:14px;box-shadow:0 1px 8px rgba(0,0,0,.06);padding:16px}}
.row{{display:flex;gap:10px}}
.input{{flex:1;padding:12px 14px;border:1px solid #ddd;border-radius:10px;font-size:16px}}
.btn{{background:var(--brand);color:#fff;border:none;border-radius:10px;padding:12px 16px;cursor:pointer}}
.btn:disabled{{opacity:.6;cursor:not-allowed}}
.bubble{{border-radius:12px;padding:12px 14px;margin:10px 0;max-width:90%}}
.user{{background:#e9f5ff;margin-left:auto}}
.bot{{background:#f0f0f3}}
.toprow{{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}}
.link{{text-decoration:none;background:#111827;color:#fff;padding:8px 10px;border-radius:8px}}
.muted{{color:var(--muted);font-size:14px}}
footer{{text-align:center;color:var(--muted);margin:30px 0 14px;font-size:13px}}
#log{{min-height:280px}}
.small{{font-size:12px;color:var(--muted);margin-top:8px}}
</style>
</head>
<body>
<header><h2>💊 CheckDoc — Веб-чат</h2></header>
<main>
  <div class="card">
    <div class="toprow">
      <div class="muted">Веб-чат работает независимо от Telegram-бота</div>
      <a class="link" href="{tg_link}" target="_blank" rel="noopener">Перейти в Telegram</a>
    </div>
    <div id="log"></div>
    <div class="row">
      <input id="msg" class="input" placeholder="Опишите симптомы или задайте вопрос…"/>
      <button id="send" class="btn">Отправить</button>
    </div>
    <div class="small">Assistant ID: {gpt_id}</div>
  </div>
  <footer>© CheckDoc</footer>
</main>
<script>
const log = document.getElementById('log');
const msg = document.getElementById('msg');
const btn = document.getElementById('send');

function addBubble(text, role) {{
  const d = document.createElement('div');
  d.className = 'bubble ' + (role === 'user' ? 'user' : 'bot');
  d.textContent = text;
  log.appendChild(d);
  log.scrollTop = log.scrollHeight;
}}

async function send() {{
  const text = msg.value.trim();
  if (!text) return;
  addBubble(text, 'user');
  msg.value = '';
  btn.disabled = true;
  try {{
    const r = await fetch('/chat', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ text }})
    }});
    const j = await r.json();
    addBubble(j.reply || 'Нет ответа', 'assistant');
  }} catch(e) {{
    addBubble('Ошибка сети', 'assistant');
  }} finally {{
    btn.disabled = false;
  }}
}}

btn.addEventListener('click', send);
msg.addEventListener('keydown', e => {{ if (e.key === 'Enter') send(); }});
</script>
</body></html>"""

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    # cookie для веб-сессии
    sid = request.cookies.get("sid") or str(uuid.uuid4())
    html = _html_page(GPT_ID, TELEGRAM_LINK)
    resp = HTMLResponse(html)
    resp.set_cookie("sid", sid, httponly=True, samesite="lax", max_age=60*60*24*14)
    return resp

class ChatIn(TypedDict):
    text: str

@app.post("/chat")
async def chat(request: Request):
    data: dict = await request.json()
    user_text: str = (data.get("text") or "").strip()
    if not user_text:
        return JSONResponse({"reply": "Введите вопрос."})
    sid = request.cookies.get("sid") or str(uuid.uuid4())
    thread_id = get_or_create_web_thread(sid)
    try:
        reply = await run_assistant_in_thread(thread_id, user_text)
        return JSONResponse({"reply": reply or "⚠️ Ответ ассистента не найден."})
    except Exception:
        return JSONResponse({"reply": "⚠️ Временная ошибка. Попробуйте ещё раз."})

@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    data = await request.json()
    upd = Update.model_validate(data)
    await dp.feed_update(bot, upd)
    return JSONResponse({"ok": True})

@app.get("/health")
async def health():
    return {"ok": True, "webhook_set": bool(WEBHOOK_URL)}

@app.on_event("startup")
async def on_startup():
    # Ставим вебхук Telegram, если уже есть домен
    if WEBHOOK_URL:
        await bot.set_webhook(WEBHOOK_URL)
        print(f"✅ Telegram webhook set: {WEBHOOK_URL}")
    else:
        print("⚠️ WEBHOOK_BASE не задан — пропускаю установку вебхука. "
              "Добавьте переменную в Railway и сделайте Redeploy.")

# ===================== Assistants helpers =================

def get_or_create_web_thread(session_id: str) -> str:
    th = WEB_SESSIONS.get(session_id)
    if not th:
        thread = openai.beta.threads.create()
        th = thread.id
        WEB_SESSIONS[session_id] = th
    return th

async def run_assistant_in_thread(thread_id: str, user_text: str) -> Optional[str]:
    """
    Ведение диалога в рамках существующего thread (веб-чат и Telegram).
    """
    # создаём сообщение
    await asyncio.to_thread(
        openai.beta.threads.messages.create,
        thread_id=thread_id, role="user", content=user_text
    )
    # запускаем ассистента
    run = await asyncio.to_thread(
        openai.beta.threads.runs.create,
        thread_id=thread_id, assistant_id=GPT_ID
    )
    # ждём завершения
    while True:
        status = await asyncio.to_thread(
            openai.beta.threads.runs.retrieve,
            thread_id=thread_id, run_id=run.id
        )
        if status.status == "completed":
            break
        if status.status == "failed":
            return "❌ Ассистент не смог ответить."
        await asyncio.sleep(0.6)
    # читаем ответ
    msgs = await asyncio.to_thread(openai.beta.threads.messages.list, thread_id=thread_id)
    for m in msgs.data:
        if m.role == "assistant" and m.content:
            part = m.content[0]
            if hasattr(part, "text") and hasattr(part.text, "value"):
                return part.text.value
    return None
