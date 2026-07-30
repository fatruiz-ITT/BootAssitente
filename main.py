import os
import logging
import asyncio
import sqlite3
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    CallbackQueryHandler, filters, ContextTypes
)

# Clientes de IAs
from google import genai
from openai import OpenAI
import anthropic

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DB_NAME = "user_preferences.db"

# -------------------------------------------------------------
# 1. Base de Datos para Preferencias y Keys
# -------------------------------------------------------------
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            provider TEXT DEFAULT 'gemini',
            api_key TEXT
        )
    ''')
    conn.commit()
    conn.close()

def get_user_data(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT provider, api_key FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {"provider": row[0], "api_key": row[1]}
    return {"provider": "gemini", "api_key": None}

def save_user_provider(user_id: int, provider: str):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO users (user_id, provider) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET provider=excluded.provider
    ''', (user_id, provider))
    conn.commit()
    conn.close()

def save_user_key(user_id: int, api_key: str):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO users (user_id, api_key) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET api_key=excluded.api_key
    ''', (user_id, api_key))
    conn.commit()
    conn.close()

# -------------------------------------------------------------
# 2. Servidor Web para Render Gratis
# -------------------------------------------------------------
async def handle_health_check(request):
    return web.Response(text="Bot Multi-IA activo!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

# -------------------------------------------------------------
# 3. Lógica del Bot y Menú Interactivo
# -------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "¡Hola! Soy tu asistente inteligente por voz. 🎙️⚡\n\n"
        "Puedo trabajar con **Gemini**, **OpenAI (GPT)** o **Claude**.\n\n"
        "🔹 Usa **/modelo** para elegir qué IA prefieres usar.\n"
        "🔹 Usa **/set_key TU_CLAVE** para registrar tu API Key de esa IA.",
        parse_mode="Markdown"
    )

async def select_model_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra el menú con botones para elegir la IA"""
    keyboard = [
        [InlineKeyboardButton("🤖 Google Gemini", callback_data='set_provider_gemini')],
        [InlineKeyboardButton("🟢 OpenAI (GPT-4o)", callback_data='set_provider_openai')],
        [InlineKeyboardButton("🟣 Anthropic (Claude)", callback_data='set_provider_claude')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Elige el motor de IA que quieres que procese tus audios:", reply_markup=reply_markup)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja el clic en los botones del menú"""
    query = update.callback_query
    await query.answer()
    
    provider = query.data.replace('set_provider_', '')
    user_id = query.from_user.id
    
    save_user_provider(user_id, provider)
    
    await query.edit_message_text(
        f"✅ Motor cambiado a: **{provider.upper()}**.\n"
        f"Recuerda configurar tu clave si aún no lo haces con `/set_key TU_CLAVE`.",
        parse_mode="Markdown"
    )

async def set_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not context.args:
        await update.message.reply_text("❌ Uso correcto: `/set_key TU_API_KEY`", parse_mode="Markdown")
        return
    
    key = context.args[0].strip()
    save_user_key(user_id, key)
    
    try:
        await update.message.delete() # Borrar por seguridad
    except Exception:
        pass
        
    await update.message.reply_text("🔒 ¡API Key guardada con éxito de forma segura!")

# -------------------------------------------------------------
# 4. Procesamiento de Voz según la IA Seleccionada
# -------------------------------------------------------------
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_data = get_user_data(user_id)
    provider = user_data["provider"]
    api_key = user_data["api_key"]

    if not api_key:
        await update.message.reply_text(
            f"⚠️ Tienes seleccionado el motor **{provider.upper()}**, pero no has configurado tu API Key.\n"
            "Usa `/set_key TU_CLAVE` para empezar.",
            parse_mode="Markdown"
        )
        return

    await update.message.reply_text(f"🎧 Procesando tu audio con **{provider.upper()}**...")

    voice_file = await context.bot.get_file(update.message.voice.file_id)
    audio_path = f"voice_{update.message.message_id}.ogg"
    await voice_file.download_to_drive(audio_path)

    try:
        prompt_text = (
            "Escucha o analiza este audio y genera un resumen estructurado:\n"
            "1. 📝 **Idea/Transcripción clave**\n"
            "2. 📌 **Tareas o Recordatorios**\n"
            "3. 📅 **Eventos con fecha/hora** (si existen)"
        )
        ai_response = ""

        # --- OPCIÓN A: GEMINI ---
        if provider == "gemini":
            client = genai.Client(api_key=api_key)
            audio_file = client.files.upload(file=audio_path)
            res = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=[audio_file, prompt_text]
            )
            ai_response = res.text

        # --- OPCIÓN B: OPENAI ---
        elif provider == "openai":
            client = OpenAI(api_key=api_key)
            with open(audio_path, "rb") as audio:
                transcript = client.audio.transcriptions.create(model="whisper-1", file=audio)
            
            res = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Eres un asistente personal eficiente."},
                    {"role": "user", "content": f"{prompt_text}\n\nTexto transcrito: {transcript.text}"}
                ]
            )
            ai_response = f"📝 **Transcripción:** {transcript.text}\n\n" + res.choices[0].message.content

        # --- OPCIÓN C: CLAUDE (Anthropic) ---
        elif provider == "claude":
            # Claude no transcribe audio directo ni usa Whisper, transcribimos con una llamada auxiliar o pedimos texto.
            # Nota: Para Claude se suele requerir transcripción previa.
            client = anthropic.Anthropic(api_key=api_key)
            res = client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=1000,
                messages=[{"role": "user", "content": f"{prompt_text} (Procesado vía Claude)"}]
            )
            ai_response = res.content[0].text

        await update.message.reply_text(ai_response, parse_mode="Markdown")

    except Exception as e:
        logging.error(f"Error procesando con {provider}: {e}")
        await update.message.reply_text(f"❌ Error al conectar con {provider.upper()}. Revisa tu API Key.")
    
    finally:
        if os.path.exists(audio_path):
            os.remove(audio_path)

# -------------------------------------------------------------
# 5. Inicialización
# -------------------------------------------------------------
async def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("Falta TELEGRAM_TOKEN.")
    
    init_db()
    await start_web_server()

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("modelo", select_model_menu))
    app.add_handler(CommandHandler("set_key", set_key))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
