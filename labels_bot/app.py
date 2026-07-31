import hashlib
import os

import bot as core
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import (
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)


async def ticket_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "🎫 Selecciona el tipo de ayuda:",
        reply_markup=core.help_menu(),
    )
    raise ApplicationHandlerStop


async def fixed_set_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not core.is_admin(query.from_user):
        await query.answer("No autorizado.", show_alert=True)
        raise ApplicationHandlerStop

    _, order_id, status = query.data.split(":", 2)
    if status not in core.STATUS_LABELS:
        await query.answer("Estado no válido.", show_alert=True)
        raise ApplicationHandlerStop

    order = await core.db(core.storage.get_order, order_id)
    if not order:
        await query.answer("Pedido no encontrado.", show_alert=True)
        raise ApplicationHandlerStop

    if order.get("status") == status:
        await query.answer("El pedido ya tiene ese estado.", show_alert=True)
        raise ApplicationHandlerStop

    await query.answer()
    history = list(order.get("status_history") or [])
    history.append({"status": status, "created_at": core.now_iso()})
    order = await core.db(
        core.storage.update_order,
        order_id,
        {"status": status, "status_history": history},
    )
    await core.notify_customer_status(context, order)

    try:
        await query.edit_message_text(
            core.order_text(order, admin=True),
            reply_markup=core.admin_order_keyboard(order),
            parse_mode="HTML",
        )
    except TelegramError:
        await query.message.reply_text(
            core.order_text(order, admin=True),
            reply_markup=core.admin_order_keyboard(order),
            parse_mode="HTML",
        )
    raise ApplicationHandlerStop


def build_application():
    app = core.build_application()
    app.add_handler(CommandHandler("ticket", ticket_command), group=-1)
    app.add_handler(
        CallbackQueryHandler(fixed_set_status, pattern=r"^set_status:"),
        group=-1,
    )
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
