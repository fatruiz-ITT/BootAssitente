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
from twilio.rest import Client

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")

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
            awaiting_key INTEGER DEFAULT 0,
            user_phone TEXT
        )
    ''')
    conn.commit()
    conn.close()

def get_user_data(user_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT provider, api_key, awaiting_key, user_phone FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {"provider": row[0], "api_key": row[1], "awaiting_key": row[2], "user_phone": row[3]}
    return {"provider": "gemini", "api_key": None, "awaiting_key": 0, "user_phone": None}

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

def save_user_phone(user_id: int, phone: str):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO users (user_id, user_phone) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET user_phone=excluded.user_phone
    ''', (user_id, phone))
    conn.commit()
    conn.close()

# -------------------------------------------------------------
# Función Auxiliar WhatsApp (Twilio)
# -------------------------------------------------------------
def send_whatsapp_message(to_number: str, message_body: str) -> bool:
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN or not TWILIO_WHATSAPP_NUMBER:
        logging.error("Faltan variables de entorno para Twilio.")
        return False
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        
        # Asegurar formato whatsapp:+...
        formatted_to = to_number if to_number.startswith("whatsapp:") else f"whatsapp:{to_number}"
        formatted_from = TWILIO_WHATSAPP_NUMBER if TWILIO_WHATSAPP_NUMBER.startswith("whatsapp:") else f"whatsapp:{TWILIO_WHATSAPP_NUMBER}"
        
        client.messages.create(
            from_=formatted_from,
            body=message_body,
            to=formatted_to
        )
        return True
    except Exception as e:
        logging.error(f"Error enviando WhatsApp con Twilio: {e}")
        return False

# -------------------------------------------------------------
# Petición Directa HTTP a Gemini (gemini-flash-latest)
# -------------------------------------------------------------
async def call_gemini_api(api_key: str, text_prompt: str) -> str:
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent"
    headers = {
        "Content-Type": "application/json",
        "X-goog-api-key": api_key
    }
    payload = {
        "contents": [{"parts": [{"text": text_prompt}]}]
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            if resp.status == 200:
                data = await resp.json()
                try:
                    candidates = data.get('candidates', [])
                    if not candidates:
                        return "Gemini no devolvió ninguna respuesta."
                    parts = candidates[0].get('content', {}).get('parts', [])
                    for part in parts:
                        if 'text' in part:
                            return part['text']
                    return "No se encontró texto en la respuesta de Gemini."
                except Exception as e:
                    return f"Error leyendo la respuesta: {e}"
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
    
    msg = (
        f"👋 <b>¡Hola! Soy tu asistente inteligente por voz y texto.</b>\n\n"
        f"🤖 <b>Motor actual:</b> {provider_name}\n\n"
        "<b>📌 Comandos principales:</b>\n"
        "• /modelo - Cambia entre Gemini, OpenAI o Claude.\n"
        "• /set_key - Registra tu API Key.\n"
        "• /mi_numero +52123456789 - Guarda tu número para recibir WhatsApps.\n"
        "• /whatsapp +52123456789 Tu Mensaje - Envía un WhatsApp directo."
    )
    await update.message.reply_text(msg, parse_mode="HTML")

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
        f"⚙️ <b>Configuración de Motor de IA</b>\n\nActualmente usas: <b>{current_provider}</b>\n\nSelecciona el nuevo motor:",
        reply_markup=reply_markup,
        parse_mode="HTML"
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
        f"🎉 <b>¡Motor cambiado a {provider.upper()}!</b>\n\n🔑 API Key: {key_status}\n\nEscribe /set_key si necesitas actualizar tu clave.",
        parse_mode="HTML"
    )

async def set_key_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    
    if context.args:
        key = context.args[0].strip()
        save_user_key(user_id, key)
        await update.message.reply_text("🔒 <b>¡API Key guardada con éxito!</b>", parse_mode="HTML")
        try:
            await update.message.delete()
        except Exception:
            pass
        return

    user_data = get_user_data(user_id)
    provider = user_data["provider"].upper()
    set_awaiting_key(user_id, 1)
    
    await update.message.reply_text(
        f"📥 <b>Configuración para {provider}</b>\n\nEnvía tu API Key en el siguiente mensaje:",
        parse_mode="HTML"
    )

async def set_my_phone_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not context.args:
        await update.message.reply_text("❌ <b>Uso correcto:</b> <code>/mi_numero +521234567890</code>", parse_mode="HTML")
        return
    
    phone = context.args[0].strip()
    save_user_phone(user_id, phone)
    await update.message.reply_text(f"📱 <b>Teléfono guardado:</b> <code>{phone}</code>\nAhora podrás enviarte resúmenes por WhatsApp.", parse_mode="HTML")

async def send_whatsapp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("❌ <b>Uso correcto:</b> <code>/whatsapp +521234567890 Hola esto es una prueba</code>", parse_mode="HTML")
        return
    
    to_phone = context.args[0].strip()
    message_text = " ".join(context.args[1:])
    
    await update.message.reply_text("💬 Enviando WhatsApp...")
    
    success = send_whatsapp_message(to_phone, message_text)
    if success:
        await update.message.reply_text(f"✅ <b>WhatsApp enviado con éxito a {to_phone}</b>", parse_mode="HTML")
    else:
        await update.message.reply_text("❌ <b>Error al enviar WhatsApp.</b> Revisa que el número esté registrado en el Sandbox de Twilio.", parse_mode="HTML")

# -------------------------------------------------------------
# Procesador Unificado para Texto y Voz (Smart Formatting)
# -------------------------------------------------------------
async def process_user_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_data = get_user_data(user_id)

    if user_data["awaiting_key"] == 1:
        new_key = update.message.text.strip()
        save_user_key(user_id, new_key)
        await update.message.reply_text(
            f"🔒 <b>¡API Key de {user_data['provider'].upper()} activada!</b>", parse_mode="HTML"
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
            f"⚠️ <b>Atención:</b> Seleccionaste {provider.upper()} pero falta tu API Key.\nEscribe /set_key para ingresarla.",
            parse_mode="HTML"
        )
        return

    is_voice = update.message.voice is not None
    user_text = update.message.text if not is_voice else ""

    status_icon = "🎧" if is_voice else "💬"
    await update.message.reply_text(f"{status_icon} Procesando con {provider.upper()}...")

    audio_path = None
    if is_voice:
        voice_file = await context.bot.get_file(update.message.voice.file_id)
        audio_path = f"voice_{update.message.message_id}.ogg"
        await voice_file.download_to_drive(audio_path)

    try:
        # Prompt Adaptativo e Inteligente
        system_instructions = (
            "Eres un asistente personal ultra eficiente y conciso. Reglas de respuesta:\n"
            "1. Sé directo, breve y scannable. Usa viñetas limpias y negritas en conceptos clave.\n"
            "2. Si la entrada es una nota de voz, llamada o un resumen de reunión, estructúrala en: 📌 Resumen, 📝 Tareas pendientes y 📅 Fechas/Citas.\n"
            "3. Si es una pregunta libre, receta o solicitud de ideas, responde directo a lo solicitado sin forzar categorías rígidas ni textos largos.\n"
            "4. NO uses marcas de agua ni introducciones innecesarias."
        )
        ai_response = ""

        if provider == "gemini":
            prompt = f"{system_instructions}\n\nMensaje/Audio del usuario: {user_text if user_text else 'Nota de voz recibida'}"
            ai_response = await call_gemini_api(api_key, prompt)

        elif provider == "openai":
            client = OpenAI(api_key=api_key)
            if is_voice:
                with open(audio_path, "rb") as audio:
                    transcript = client.audio.transcriptions.create(model="whisper-1", file=audio)
                prompt_content = f"Transcripción de voz: {transcript.text}"
            else:
                prompt_content = f"Usuario: {user_text}"

            res = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_instructions},
                    {"role": "user", "content": prompt_content}
                ]
            )
            ai_response = res.choices[0].message.content

        elif provider == "claude":
            client = anthropic.Anthropic(api_key=api_key)
            prompt_content = f"Usuario: {user_text}" if not is_voice else "Nota de voz recibida"
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
            f"❌ <b>Error de conexión con {provider.upper()}.</b>\nDetalle: {e}", parse_mode="HTML"
        )
    
    finally:
        if audio_path and os.path.exists(audio_path):
            os.remove(audio_path)

# -------------------------------------------------------------
# Servidor Web Render Independiente
# -------------------------------------------------------------
async def handle_health(request):
    return web.Response(text="Bot Activo")

async def run_web():
    app = web.Application()
    app.router.add_get('/', handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

# -------------------------------------------------------------
# Ejecución Principal
# -------------------------------------------------------------
async def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("Falta TELEGRAM_TOKEN.")
    
    init_db()
    asyncio.create_task(run_web())

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("modelo", select_model_menu))
    app.add_handler(CommandHandler("set_key", set_key_command))
    app.add_handler(CommandHandler("mi_numero", set_my_phone_command))
    app.add_handler(CommandHandler("whatsapp", send_whatsapp_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    
    app.add_handler(MessageHandler(filters.VOICE, process_user_input))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_user_input))

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
