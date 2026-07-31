import hashlib
import os

import admin_fix as base
import bot as core
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes


async def patched_clear_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete recent messages while preserving the currently pinned message."""
    chat = update.effective_chat
    message = update.effective_message
    if not chat or not message or chat.type != "private":
        return

    pinned_message_id = None
    try:
        full_chat = await context.bot.get_chat(chat.id)
        pinned_message = getattr(full_chat, "pinned_message", None)
        if pinned_message:
            pinned_message_id = pinned_message.message_id
    except TelegramError:
        core.logger.exception("No se pudo consultar el mensaje fijado")

    newest = message.message_id
    for message_id in range(newest, max(0, newest - 100), -1):
        if message_id == pinned_message_id:
            continue
        try:
            await context.bot.delete_message(chat.id, message_id)
        except TelegramError:
            continue

    await core.start(update, context)


def build_application():
    core.clear_chat = patched_clear_chat
    return base.build_application()


def run() -> None:
    app = build_application()
    hostname = os.getenv("RENDER_EXTERNAL_HOSTNAME")
    if hostname:
        webhook_path = hashlib.sha256(core.BOT_TOKEN.encode("utf-8")).hexdigest()[:32]
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
