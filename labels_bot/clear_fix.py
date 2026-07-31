import hashlib
import os

import admin_fix as base
import bot as core
from telegram import Update
from telegram.ext import ContextTypes


async def patched_clear_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel the current flow and reopen the menu without deleting messages."""
    chat = update.effective_chat
    message = update.effective_message
    if not chat or not message:
        return

    if chat.type != "private":
        await message.reply_text("El comando /clear solamente funciona en el chat privado con el bot.")
        return

    # Keep every Telegram message so the conversation remains in the user's
    # pinned chat list. Only reset the bot's current temporary workflow.
    context.user_data.clear()
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
