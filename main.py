import os
import logging
import asyncio
from aiohttp import web
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from openai import OpenAI
from twilio.rest import Client

# Configuración de logs
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Cargar API Keys desde Variables de Entorno
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")

openai_client = OpenAI(api_key=OPENAI_API_KEY)

# -------------------------------------------------------------
# 1. Servidor Web Asíncrono para Render Gratis (usando aiohttp)
# -------------------------------------------------------------
async def handle_health_check(request):
    return web.Response(text="Bot activo 24/7 en Render!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle_health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logging.info(f"Servidor Web asíncrono corriendo en el puerto {port}")

# -------------------------------------------------------------
# 2. Handlers del Bot de Telegram
# -------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "¡Hola! Soy tu asistente @Asistente_Virtual_fatbot. 🎙️\n"
        "Mándame notas de voz y yo me encargaré de transcribirlas, agendar tus eventos "
        "o enviarte avisos."
    )

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎧 Procesando tu audio, dame un momento...")

    voice_file = await context.bot.get_file(update.message.voice.file_id)
    audio_path = f"voice_{update.message.message_id}.ogg"
    await voice_file.download_to_drive(audio_path)

    try:
        with open(audio_path, "rb") as audio:
            transcript = openai_client.audio.transcriptions.create(
                model="whisper-1",
                file=audio
            )
        text_content = transcript.text
        
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Eres un asistente personal. Analiza la transcripción del usuario "
                        "y resume la tarea o evento solicitado. Si el usuario pide agendar "
                        "o recordar algo, ordénalo claramente."
                    )
                },
                {"role": "user", "content": text_content}
            ]
        )
        ai_summary = response.choices[0].message.content

        reply_message = (
            f"📝 **Transcripción:**\n\"{text_content}\"\n\n"
            f"🤖 **Acción/Resumen:**\n{ai_summary}"
        )
        await update.message.reply_text(reply_message, parse_mode="Markdown")

    except Exception as e:
        logging.error(f"Error procesando el audio: {e}")
        await update.message.reply_text("❌ Ocurrió un error al procesar el audio.")
    
    finally:
        if os.path.exists(audio_path):
            os.remove(audio_path)

# -------------------------------------------------------------
# 3. Función Principal Concurrente
# -------------------------------------------------------------
async def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("Error: La variable TELEGRAM_TOKEN no está configurada.")

    # Iniciar servidor Web en segundo plano
    await start_web_server()

    # Iniciar aplicación de Telegram
    telegram_app = Application.builder().token(TELEGRAM_TOKEN).build()
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    # Inicializar y arrancar bot en el mismo loop de asyncio
    await telegram_app.initialize()
    await telegram_app.start()
    await telegram_app.updater.start_polling()
    
    logging.info("Bot y Servidor Web iniciados correctamente...")

    # Mantener el proceso corriendo infinitamente
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
