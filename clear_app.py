import asyncio
import hashlib
import os

import main as ilumistore
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import CommandHandler, ContextTypes


async def delete_history_except_anchor(context, chat_id: int, anchor_id: int) -> None:
    """Delete every deletable message before the anchor, from newest to oldest."""
    for high in range(anchor_id - 1, 0, -100):
        low = max(1, high - 99)
        message_ids = list(range(low, high + 1))
        try:
            await context.bot.delete_messages(chat_id=chat_id, message_ids=message_ids)
        except TelegramError:
            for index, message_id in enumerate(reversed(message_ids), start=1):
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
                except TelegramError:
                    continue
                if index % 25 == 0:
                    await asyncio.sleep(0.1)
        await asyncio.sleep(0.05)


async def clear_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear the visible history while always keeping one message in the chat."""
    message = update.effective_message
    chat = update.effective_chat

    if not message or not chat:
        return

    if chat.type != "private":
        await message.reply_text("El comando /clear solamente funciona en el chat privado con el bot.")
        return

    context.user_data.clear()

    # Keep this message alive while the rest of the history is removed so the
    # conversation never becomes empty or disappears from the pinned list.
    anchor = await context.bot.send_message(
        chat_id=chat.id,
        text="🧹 Limpiando el historial…",
    )

    await delete_history_except_anchor(context, chat.id, anchor.message_id)

    # Reopen the regular menu, then remove only the temporary anchor.
    await ilumistore.patched_start(update, context)
    try:
        await context.bot.delete_message(chat_id=chat.id, message_id=anchor.message_id)
    except TelegramError:
        pass


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
