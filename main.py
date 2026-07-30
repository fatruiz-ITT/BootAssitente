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

from google import genai
from openai import OpenAI
import anthropic
from twilio.rest import Client
from pypdf import PdfReader

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")

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
# Envío de WhatsApp con Twilio
# -------------------------------------------------------------
def send_whatsapp_message(to_number: str, message_body: str) -> bool:
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        logging.error("Faltan credenciales de Twilio.")
        return False
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        formatted_to = to_number if to_number.startswith("whatsapp:") else f"whatsapp:{to_number}"
        formatted_from = TWILIO_WHATSAPP_NUMBER if TWILIO_WHATSAPP_NUMBER.startswith("whatsapp:") else f"whatsapp:{TWILIO_WHATSAPP_NUMBER}"
        
        client.messages.create(
            from_=formatted_from,
            body=message_body,
            to=formatted_to
        )
        return True
    except Exception as e:
        logging.error(f"Error enviando WhatsApp: {e}")
        return False

# -------------------------------------------------------------
# Comandos del Bot
# -------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_data = get_user_data(user_id)
    provider_name = user_data['provider'].upper()
    phone_str = user_data['user_phone'] if user_data['user_phone'] else "No registrado"
    
    msg = (
        f"👋 <b>¡Hola! Soy tu asistente inteligente.</b>\n\n"
        f"🤖 <b>Motor activo:</b> {provider_name}\n"
        f"📱 <b>Tu WhatsApp:</b> {phone_str}\n\n"
        "<b>📌 Capacidades disponibles:</b>\n"
        "• 🎧 <b>Notas de voz:</b> Te las proceso directamente.\n"
        "• 📄 <b>PDFs:</b> Súbelos para resúmenes o guías de estudio.\n"
        "• /modelo - Cambia de motor de IA.\n"
        "• /set_key - Registra tu API Key.\n"
        "• /mi_numero +521... - Guarda tu WhatsApp.\n"
        "• /whatsapp Hola - Mándate notas por WhatsApp."
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
        f"⚙️ <b>Configuración de Motor</b>\n\nMotor actual: <b>{current_provider}</b>\nSelecciona el nuevo motor:",
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
        f"🎉 <b>¡Motor cambiado a {provider.upper()}!</b>\n\n🔑 API Key: {key_status}\n\nUsa /set_key si deseas cambiar la clave.",
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
        f"📥 <b>Configuración de clave para {provider}</b>\n\nEnvía tu API Key en el siguiente mensaje:",
        parse_mode="HTML"
    )

async def set_my_phone_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if not context.args:
        await update.message.reply_text("❌ <b>Uso correcto:</b> <code>/mi_numero +521234567890</code>", parse_mode="HTML")
        return
    
    phone = context.args[0].strip()
    save_user_phone(user_id, phone)
    await update.message.reply_text(f"📱 <b>Tu teléfono quedó guardado:</b> <code>{phone}</code>", parse_mode="HTML")

async def send_whatsapp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_data = get_user_data(user_id)

    if len(context.args) >= 2 and context.args[0].startswith("+"):
        to_phone = context.args[0].strip()
        message_text = " ".join(context.args[1:])
    elif user_data["user_phone"]:
        to_phone = user_data["user_phone"]
        message_text = " ".join(context.args)
    else:
        await update.message.reply_text(
            "❌ <b>Uso:</b>\n• A otro número: <code>/whatsapp +521... Mensaje</code>\n• A ti: Registra con <code>/mi_numero +521...</code>",
            parse_mode="HTML"
        )
        return
    
    if not message_text.strip():
        await update.message.reply_text("⚠️ Escribe un mensaje para enviar por WhatsApp.", parse_mode="HTML")
        return

    await update.message.reply_text("💬 Enviando WhatsApp...")
    
    success = send_whatsapp_message(to_phone, message_text)
    if success:
        await update.message.reply_text(f"✅ <b>WhatsApp enviado con éxito a {to_phone}</b>", parse_mode="HTML")
    else:
        await update.message.reply_text("❌ <b>Error al enviar.</b> Asegúrate de enviar primero el comando <code>join</code> a Twilio.", parse_mode="HTML")

# -------------------------------------------------------------
# Procesador Unificado (Texto, Voz Nactiva y PDFs)
# -------------------------------------------------------------
async def process_user_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_data = get_user_data(user_id)

    if user_data["awaiting_key"] == 1:
        new_key = update.message.text.strip()
        save_user_key(user_id, new_key)
        await update.message.reply_text(
            f"🔒 <b>¡API Key de {user_data['provider'].upper()} guardada!</b>", parse_mode="HTML"
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
            f"⚠️ <b>Falta API Key para {provider.upper()}.</b> Escribe /set_key para ingresarla.",
            parse_mode="HTML"
        )
        return

    is_voice = update.message.voice is not None
    is_doc = update.message.document is not None
    user_text = update.message.text or update.message.caption or ""

    status_icon = "🎧" if is_voice else ("📄" if is_doc else "💬")
    await update.message.reply_text(f"{status_icon} Procesando con {provider.upper()}...")

    file_path = None
    try:
        # Descarga de archivo de audio o PDF
        if is_voice:
            voice_file = await context.bot.get_file(update.message.voice.file_id)
            file_path = f"voice_{update.message.message_id}.ogg"
            await voice_file.download_to_drive(file_path)

        elif is_doc:
            doc_file = await context.bot.get_file(update.message.document.file_id)
            file_path = f"doc_{update.message.message_id}.pdf"
            await doc_file.download_to_drive(file_path)

        system_instructions = (
            "Eres un asistente personal experto tipo NotebookLM. Instrucciones de respuesta:\n"
            "1. Sé conciso, directo y estructurado en HTML limpio de Telegram (<b>negrita</b>).\n"
            "2. Si procesas un PDF o Nota de Voz tipo clase/reunión, organízalo como Guía NotebookLM:\n"
            "   • 📌 <b>Resumen Ejecutivo</b>\n"
            "   • 🎯 <b>Preguntas Clave para Examen/Estudio</b>\n"
            "   • 🗣️ <b>Estructura para Exposición</b>\n"
            "   • 📝 <b>Tareas o Acuerdos Pendientes</b>"
        )

        ai_response = ""

        # --- MOTOR GEMINI ---
        if provider == "gemini":
            client = genai.Client(api_key=api_key)
            contents = [system_instructions]

            if is_voice or is_doc:
                uploaded_file = client.files.upload(file=file_path)
                contents.append(uploaded_file)
            
            if user_text:
                contents.append(f"Mensaje del usuario: {user_text}")

            res = client.models.generate_content(
                model='gemini-flash-latest',
                contents=contents
            )
            ai_response = res.text

        # --- MOTOR OPENAI ---
        elif provider == "openai":
            client = OpenAI(api_key=api_key)
            prompt_content = user_text

            if is_voice:
                with open(file_path, "rb") as audio:
                    transcript = client.audio.transcriptions.create(model="whisper-1", file=audio)
                prompt_content = f"Audio transcrito: {transcript.text}\n\nInstrucción extra: {user_text}"
            elif is_doc:
                reader = PdfReader(file_path)
                pdf_text = "".join([page.extract_text() or "" for page in reader.pages])
                prompt_content = f"Texto extraído del PDF:\n{pdf_text[:12000]}\n\nInstrucción extra: {user_text}"

            res = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_instructions},
                    {"role": "user", "content": prompt_content}
                ]
            )
            ai_response = res.choices[0].message.content

        # --- MOTOR CLAUDE ---
        elif provider == "claude":
            client = anthropic.Anthropic(api_key=api_key)
            prompt_content = user_text

            if is_voice or is_doc:
                if is_doc:
                    reader = PdfReader(file_path)
                    pdf_text = "".join([page.extract_text() or "" for page in reader.pages])
                    prompt_content = f"Texto del documento:\n{pdf_text[:12000]}\n\nInstrucción: {user_text}"
                else:
                    prompt_content = f"Nota de voz recibida. Instrucción: {user_text}"

            res = client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=1500,
                messages=[{"role": "user", "content": f"{system_instructions}\n\n{prompt_content}"}]
            )
            ai_response = res.content[0].text

        formatted_response = ai_response.replace('**', '<b>').replace('**', '</b>')
        await update.message.reply_text(formatted_response, parse_mode="HTML")

    except Exception as e:
        logging.error(f"Error procesando solicitud: {e}")
        await update.message.reply_text(f"❌ <b>Error de procesamiento.</b>\nDetalle: {e}", parse_mode="HTML")

    finally:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)

# -------------------------------------------------------------
# Servidor Web e Inicialización
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
    app.add_handler(MessageHandler(filters.Document.PDF, process_user_input))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_user_input))

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
