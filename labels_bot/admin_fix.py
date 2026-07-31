import hashlib
import html
import os

import bot as core
import commands_app as base
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes


def patched_is_admin(user) -> bool:
    """Grant administrator access only to the configured numeric Telegram ID."""
    return bool(user and user.id == core.ADMIN_CHAT_ID)


def patched_admin_order_keyboard(order: dict) -> InlineKeyboardMarkup:
    """Admin controls, including direct access to labels and payment receipt."""
    order_id = order["id"]
    status = order.get("status")
    rows = [
        [
            InlineKeyboardButton(
                f"🏷 Ver etiquetas ({len(order.get('label_files') or [])})",
                callback_data=f"admin_labels:{order_id}",
            ),
            InlineKeyboardButton(
                "🧾 Ver comprobante",
                callback_data=f"admin_receipt:{order_id}",
            ),
        ]
    ]

    if status == "pending_payment_review":
        rows.append([
            InlineKeyboardButton("✅ Aprobar pago", callback_data=f"set_status:{order_id}:approved"),
            InlineKeyboardButton("❌ Rechazar", callback_data=f"set_status:{order_id}:rejected"),
        ])
    elif status == "approved":
        rows.append([
            InlineKeyboardButton("⚙️ Marcar procesando", callback_data=f"set_status:{order_id}:processing")
        ])
        rows.append([
            InlineKeyboardButton("❌ Rechazar", callback_data=f"set_status:{order_id}:rejected")
        ])
    elif status == "processing":
        rows.append([
            InlineKeyboardButton("✅ Marcar completado", callback_data=f"set_status:{order_id}:completed")
        ])
    elif status == "rejected":
        rows.append([
            InlineKeyboardButton(
                "↩️ Volver a revisión",
                callback_data=f"set_status:{order_id}:pending_payment_review",
            )
        ])

    rows.append([InlineKeyboardButton("⬅️ Pedidos", callback_data="admin_orders")])
    return InlineKeyboardMarkup(rows)


async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.effective_message.reply_text(
        f"Tu Telegram ID es: <code>{user.id}</code>\n"
        f"Tu usuario es: @{html.escape(user.username or 'sin_usuario')}",
        parse_mode=ParseMode.HTML,
    )


async def show_order_labels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not patched_is_admin(query.from_user):
        await query.answer("No autorizado.", show_alert=True)
        return

    await query.answer()
    order_id = query.data.split(":", 1)[1]
    order = await core.db(core.storage.get_order, order_id)
    if not order:
        await query.message.reply_text("Pedido no encontrado.")
        return

    labels = order.get("label_files") or []
    if not labels:
        await query.message.reply_text(f"El pedido {order_id} no tiene etiquetas guardadas.")
        return

    sent = 0
    for index, file_data in enumerate(labels, start=1):
        try:
            await core.send_stored_file(
                context,
                query.message.chat_id,
                file_data,
                f"Etiqueta {index}/{len(labels)} · {order_id}",
            )
            sent += 1
        except (TelegramError, KeyError, TypeError):
            await query.message.reply_text(
                f"⚠️ No pude abrir la etiqueta {index} del pedido {order_id}."
            )

    await query.message.reply_text(
        f"✅ Se mostraron {sent} de {len(labels)} etiqueta(s) del pedido {order_id}.",
        reply_markup=patched_admin_order_keyboard(order),
    )


async def show_order_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not patched_is_admin(query.from_user):
        await query.answer("No autorizado.", show_alert=True)
        return

    await query.answer()
    order_id = query.data.split(":", 1)[1]
    order = await core.db(core.storage.get_order, order_id)
    if not order:
        await query.message.reply_text("Pedido no encontrado.")
        return

    receipt = order.get("receipt_file") or {}
    if not receipt.get("file_id"):
        await query.message.reply_text(f"El pedido {order_id} no tiene comprobante guardado.")
        return

    try:
        await core.send_stored_file(
            context,
            query.message.chat_id,
            receipt,
            f"Comprobante de pago · {order_id}",
        )
    except (TelegramError, KeyError, TypeError):
        await query.message.reply_text(
            f"⚠️ No pude abrir el comprobante del pedido {order_id}."
        )
        return

    await query.message.reply_text(
        f"✅ Comprobante mostrado para el pedido {order_id}.",
        reply_markup=patched_admin_order_keyboard(order),
    )


def build_application():
    core.is_admin = patched_is_admin
    core.admin_order_keyboard = patched_admin_order_keyboard
    app = base.build_application()
    app.add_handler(CommandHandler("myid", my_id), group=-3)
    app.add_handler(
        CallbackQueryHandler(show_order_labels, pattern=r"^admin_labels:"),
        group=-3,
    )
    app.add_handler(
        CallbackQueryHandler(show_order_receipt, pattern=r"^admin_receipt:"),
        group=-3,
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
