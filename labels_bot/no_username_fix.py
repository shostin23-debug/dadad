import hashlib
import os

import admin_fix as admin
import bot as core
import clear_fix as base
from telegram import Update


def build_application():
    """Keep every current fix but skip the manual Telegram username step."""
    admin.patched_begin_label_upload = admin.ORIGINAL_BEGIN_LABEL_UPLOAD
    admin.patched_text_handler = admin.ORIGINAL_TEXT_HANDLER
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
