import hashlib
import html
import os

import bot as core
import commands_app as base
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CommandHandler, ContextTypes

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "Goldito1").lstrip("@").lower()


def patched_is_admin(user) -> bool:
    if not user:
        return False
    username = (user.username or "").lower()
    return user.id == core.ADMIN_CHAT_ID or bool(ADMIN_USERNAME and username == ADMIN_USERNAME)


async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.effective_message.reply_text(
        f"Tu Telegram ID es: <code>{user.id}</code>\n"
        f"Tu usuario es: @{html.escape(user.username or 'sin_usuario')}",
        parse_mode=ParseMode.HTML,
    )


def build_application():
    core.is_admin = patched_is_admin
    app = base.build_application()
    app.add_handler(CommandHandler("myid", my_id), group=-3)
    return app


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
