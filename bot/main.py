from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler

from bot.config import TELEGRAM_BOT_TOKEN
from bot.handlers import handle_message, handle_callback
from bot.lifecycle import post_init, post_shutdown


def main():
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .concurrent_updates(True)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_callback))
    print("Bot started. Press Ctrl+C to stop.")
    app.run_polling()
