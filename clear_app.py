import hashlib
import os

import main as ilumistore
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes


async def clear_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel the current flow and reopen the menu without deleting messages."""
    message = update.effective_message
    chat = update.effective_chat

    if not message or not chat:
        return

    if chat.type != "private":
        await message.reply_text("El comando /clear solamente funciona en el chat privado con el bot.")
        return

    # Do not delete Telegram messages. Deleting the whole visible history can
    # make the conversation disappear from the user's pinned chat list.
    context.user_data.clear()
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
