import hashlib
import logging
import os

import main as ilumistore
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import CommandHandler, ContextTypes

logger = logging.getLogger("ilumistore.clear")
CLEAR_MESSAGE_LIMIT = 100


async def clear_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete recent private-chat messages without deleting store records."""
    message = update.effective_message
    chat = update.effective_chat

    if not message or not chat:
        return

    if chat.type != "private":
        await message.reply_text("El comando /clear solamente funciona en el chat privado con el bot.")
        return

    newest_id = message.message_id
    oldest_id = max(1, newest_id - CLEAR_MESSAGE_LIMIT + 1)
    message_ids = list(range(oldest_id, newest_id + 1))

    try:
        # Telegram accepts up to 100 IDs and skips IDs that don't exist.
        await context.bot.delete_messages(
            chat_id=chat.id,
            message_ids=message_ids,
        )
    except TelegramError:
        # Fallback for messages Telegram refuses to delete as one batch.
        logger.exception("La eliminación múltiple falló; intentando individualmente")
        for message_id in reversed(message_ids):
            try:
                await context.bot.delete_message(
                    chat_id=chat.id,
                    message_id=message_id,
                )
            except TelegramError:
                continue

    # Leave the chat clean with a single fresh welcome/menu message.
    await ilumistore.patched_start(update, context)


def build_application():
    app = ilumistore.build_application()
    app.add_handler(CommandHandler("clear", clear_chat), group=-2)
    return app


def run() -> None:
    app = build_application()
    hostname = os.getenv("RENDER_EXTERNAL_HOSTNAME")
    if hostname:
        webhook_path = hashlib.sha256(
            ilumistore.core.BOT_TOKEN.encode("utf-8")
        ).hexdigest()[:32]
        app.run_webhook(
            listen="0.0.0.0",
            port=int(os.getenv("PORT", "10000")),
            url_path=webhook_path,
            webhook_url=f"https://{hostname}/{webhook_path}",
            allowed_updates=Update.ALL_TYPES,
        )
    else:
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    run()
