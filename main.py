import os
import logging
from threading import Thread
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from openai import OpenAI
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from twilio.rest import Client

# -------------------------------------------------------------
# Servidor Web en segundo plano (Requerido para Render Gratis)
# -------------------------------------------------------------
flask_app = Flask(__name__)

@flask_app.route('/')
def health_check():
    return "Bot de Telegram activo 24/7!", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    flask_app.run(host="0.0.0.0", port=port)

# -------------------------------------------------------------
# Configuración de Logs y Claves de API
# -------------------------------------------------------------
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")

openai_client = OpenAI(api_key=OPENAI_API_KEY)

# -------------------------------------------------------------
# Funciones del Bot de Telegram
# -------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Responde al comando /start"""
    await update.message.reply_text(
        "¡Hola! Soy tu asistente @Asistente_Virtual_fatbot. 🎙️\n"
        "Mándame notas de voz y yo me encargaré de transcribirlas, agendar tus eventos "
        "en Google Calendar o enviar avisos por WhatsApp."
    )

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Procesa los audios enviados por el usuario"""
    chat_id = update.message.chat_id
    await update.message.reply_text("🎧 Procesando tu audio, dame un momento...")

    # 1. Descargar el audio enviado desde Telegram
    voice_file = await context.bot.get_file(update.message.voice.file_id)
    audio_path = f"voice_{update.message.message_id}.ogg"
    await voice_file.download_to_drive(audio_path)

    try:
        # 2. Transcribir Audio usando OpenAI Whisper
        with open(audio_path, "rb") as audio:
            transcript = openai_client.audio.transcriptions.create(
                model="whisper-1",
                file=audio
            )
        text_content = transcript.text
        
        # 3. Procesar intención con GPT para extraer acciones
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Eres un asistente personal. Analiza la transcripción del usuario "
                        "y resume la tarea o evento solicitado. Si el usuario pide enviar un "
                        "mensaje a WhatsApp o agendar algo, indícalo claramente."
                    )
                },
                {"role": "user", "content": text_content}
            ]
        )
        ai_summary = response.choices[0].message.content

        # 4. Responder al usuario en Telegram
        reply_message = (
            f"📝 **Transcripción:**\n\"{text_content}\"\n\n"
            f"🤖 **Acción/Resumen:**\n{ai_summary}"
        )
        await update.message.reply_text(reply_message, parse_mode="Markdown")

    except Exception as e:
        logging.error(f"Error procesando el audio: {e}")
        await update.message.reply_text("❌ Ocurrió un error al procesar el audio.")
    
    finally:
        # Limpieza de archivo local
        if os.path.exists(audio_path):
            os.remove(audio_path)

def send_whatsapp_message(to_number: str, body_text: str):
    """Función auxiliar para enviar un mensaje de WhatsApp usando Twilio"""
    if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        message = client.messages.create(
            from_=TWILIO_WHATSAPP_NUMBER,
            body=body_text,
            to=f"whatsapp:{to_number}"
        )
        return message.sid
    return None

def main():
    """Inicio del Bot"""
    if not TELEGRAM_TOKEN:
        raise ValueError("Error: La variable de entorno TELEGRAM_TOKEN no está configurada.")
    
    # Arrancar el servidor Flask en un hilo independiente
    Thread(target=run_flask, daemon=True).start()

    # Arrancar el bot de Telegram
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    logging.info("Bot y Servidor Web iniciados correctamente...")
    app.run_polling()

if __name__ == "__main__":
    main()
