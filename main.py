import os
import logging
import asyncio
import sqlite3
import aiohttp
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    CallbackQueryHandler, filters, ContextTypes
)

from openai import OpenAI
import anthropic

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
DB_NAME = "user_preferences.db"

# -------------------------------------------------------------
# Base de Datos
# -------------------------------------------------------------
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            provider TEXT DEFAULT 'gemini',
            api_key TEXT,
            awaiting_key INTEGER DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()

def get_user_data(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT provider, api_key, awaiting_key FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {"provider": row[0], "api_key": row[1], "awaiting_key": row[2]}
    return {"provider": "gemini", "api_key": None, "awaiting_key": 0}

def save_user_provider(user_id: int, provider: str):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO users (user_id, provider) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET provider=excluded.provider
    ''', (user_id, provider))
    conn.commit()
    conn.close()

def set_awaiting_key(user_id: int, status: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO users (user_id, awaiting_key) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET awaiting_key=excluded.awaiting_key
    ''', (user_id, status))
    conn.commit()
    conn.close()

def save_user_key(user_id: int, api_key: str):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO users (user_id, api_key, awaiting_key) VALUES (?, ?, 0)
        ON CONFLICT(user_id) DO UPDATE SET api_key=excluded.api_key, awaiting_key=0
    ''', (user_id, api_key))
    conn.commit()
    conn.close()

# -------------------------------------------------------------
# Servidor Web Render
# -------------------------------------------------------------
async def handle_health_check(request):
    return web.Response(text="Bot Multi-IA Activo!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

# -------------------------------------------------------------
# Petición Directa HTTP a Gemini (Compatible con AQ... y AIza...)
# -------------------------------------------------------------
async def call_gemini_api(api_key: str, text_prompt: str) -> str:
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    headers = {
        "Content-Type": "application/json",
        "X-goog-api-key": api_key
    }
    payload = {
        "contents": [
            {
                "parts": [{"text": text_prompt}]
            }
        ]
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            if resp.status == 200:
                data = await resp.json()
                try:
                    return data['candidates'][0]['content']['parts'][0]['text']
                except Exception:
                    return "Respuesta de Gemini recibida pero vacía."
            else:
                err = await resp.text()
                logging.error(f"Error Gemini API Status {resp.status}: {err}")
                raise Exception(f"HTTP {resp.status}: {err}")

# -------------------------------------------------------------
# Comandos del Bot
# -------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_data = get_user_data(user_id)
    provider_name = user_data['provider'].upper()
    
    await update.message.reply_text(
        f"👋 ¡Hola! Soy tu asistente inteligente por voz y texto.\n\n"
        f"🤖 Motor actual seleccionado: {provider_name}\n\n"
        "📌 Opciones disponibles:\n"
        "• Usa /modelo para cambiar entre Gemini, OpenAI o Claude.\n"
        "• Usa /set_key para ingresar tu API Key."
    )

async def select_model_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_data = get_user_data(user_id)
    current_provider = user_data["provider"].upper()

    keyboard = [
        [InlineKeyboardButton("🤖 Google Gemini", callback_data='set_provider_gemini')],
        [InlineKeyboardButton("🟢 OpenAI (GPT-4o)", callback_data='set_provider_openai')],
        [InlineKeyboardButton("🟣 Anthropic (Claude)", callback_data='set_provider_claude')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"⚙️ Configuración de Motor de IA\n\n"
        f"Actualmente estás usando: {current_provider}\n\n"
        f"Selecciona abajo cuál motor deseas activar:",
        reply_markup=reply_markup
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    provider = query.data.replace('set_provider_', '')
    user_id = query.from_user.id
    
    save_user_provider(user_id, provider)
    user_data = get_user_data(user_id)
    
    key_status = "✅ Configurada" if user_data["api_key"] else "❌ No configurada"

    await query.edit_message_text(
        f"🎉 ¡Motor actualizado con éxito!\n\n"
        f"🤖 Motor activo: {provider.upper()}\n"
        f"🔑 Estado de la API Key: {key_status}\n\n"
        f"Si aún no registras tu clave o deseas cambiarla, escribe ahora /set_key."
    )

async def set_key_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
    if context.args:
        key = context.args[0].strip()
        save_user_key(user_id, key)
        await update.message.reply_text("🔒 ¡API Key guardada con éxito de forma segura!")
        try:
            await update.message.delete()
        except Exception:
            pass
        return

    user_data = get_user_data(user_id)
    provider = user_data["provider"].upper()
    set_awaiting_key(user_id, 1)
    
    await update.message.reply_text(
        f"📥 Configuración de clave para {provider}\n\n"
        f"Por favor, envía tu API Key en el siguiente mensaje:"
    )

# -------------------------------------------------------------
# Procesador Unificado para Texto y Voz
# -------------------------------------------------------------
async def process_user_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_data = get_user_data(user_id)

    if user_data["awaiting_key"] == 1:
        new_key = update.message.text.strip()
        save_user_key(user_id, new_key)
        
        await update.message.reply_text(
            f"🔒 ¡API Key de {user_data['provider'].upper()} guardada y activada con éxito!\n\n"
            "Ya puedes enviarme cualquier mensaje de texto para procesarlo."
        )
        
        try:
            await update.message.delete()
        except Exception:
            pass
        return

    provider = user_data["provider"]
    api_key = user_data["api_key"]

    if not api_key:
        await update.message.reply_text(
            f"⚠️ Atención: Tienes seleccionado el motor {provider.upper()}, pero no has registrado tu API Key.\n\n"
            "Escribe /set_key para ingresarla ahora."
        )
        return

    is_voice = update.message.voice is not None
    user_text = update.message.text if not is_voice else ""

    status_icon = "🎧" if is_voice else "💬"
    await update.message.reply_text(f"{status_icon} Procesando solicitud con {provider.upper()}...")

    audio_path = None
    if is_voice:
        voice_file = await context.bot.get_file(update.message.voice.file_id)
        audio_path = f"voice_{update.message.message_id}.ogg"
        await voice_file.download_to_drive(audio_path)

    try:
        system_instructions = (
            "Eres un asistente personal altamente eficiente. Analiza la información recibida y responde estructurado así:\n"
            "1. 📝 Idea principal / Resumen\n"
            "2. 📌 Tareas o Recordatorios\n"
            "3. 📅 Eventos con fecha/hora (si aplican)"
        )
        ai_response = ""

        # --- GEMINI ---
        if provider == "gemini":
            prompt = f"{system_instructions}\n\nMensaje del usuario: {user_text if user_text else 'Nota de voz recibida'}"
            ai_response = await call_gemini_api(api_key, prompt)

        # --- OPENAI ---
        elif provider == "openai":
            client = OpenAI(api_key=api_key)
            if is_voice:
                with open(audio_path, "rb") as audio:
                    transcript = client.audio.transcriptions.create(model="whisper-1", file=audio)
                prompt_content = f"Texto transcrito del audio: {transcript.text}"
            else:
                prompt_content = f"Texto enviado por usuario: {user_text}"

            res = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_instructions},
                    {"role": "user", "content": prompt_content}
                ]
            )
            ai_response = res.choices[0].message.content

        # --- CLAUDE ---
        elif provider == "claude":
            client = anthropic.Anthropic(api_key=api_key)
            prompt_content = f"Texto del usuario: {user_text}" if not is_voice else "Nota de voz recibida"
            res = client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=1000,
                messages=[{"role": "user", "content": f"{system_instructions}\n\n{prompt_content}"}]
            )
            ai_response = res.content[0].text

        await update.message.reply_text(ai_response)

    except Exception as e:
        logging.error(f"Error procesando con {provider}: {e}")
        await update.message.reply_text(
            f"❌ Error de conexión con {provider.upper()}.\n\n"
            f"Detalle: {e}\n\n"
            f"Asegúrate de que tu API Key sea válida. Escribe /set_key para volver a ingresarla."
        )
    
    finally:
        if audio_path and os.path.exists(audio_path):
            os.remove(audio_path)

# -------------------------------------------------------------
# Inicialización
# -------------------------------------------------------------
async def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("Falta TELEGRAM_TOKEN.")
    
    init_db()
    await start_web_server()

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("modelo", select_model_menu))
    app.add_handler(CommandHandler("set_key", set_key_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    
    app.add_handler(MessageHandler(filters.VOICE, process_user_input))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_user_input))

    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
