import hashlib
import html
import os
import re

import bot as core
import commands_app as base
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

ORIGINAL_BEGIN_LABEL_UPLOAD = core.begin_label_upload
ORIGINAL_TEXT_HANDLER = core.text_handler


def patched_is_admin(user) -> bool:
    """Grant administrator access only to the configured numeric Telegram ID."""
    return bool(user and user.id == core.ADMIN_CHAT_ID)


async def patched_begin_label_upload(message, context: ContextTypes.DEFAULT_TYPE, quantity: int) -> None:
    """Require the customer to confirm their real Telegram username first."""
    if quantity < 1 or quantity > core.MAX_LABELS:
        await message.reply_text(f"La cantidad debe estar entre 1 y {core.MAX_LABELS}.")
        return

    context.user_data.clear()
    context.user_data.update({
        "mode": "awaiting_order_username",
        "quantity": quantity,
    })
    await message.reply_text(
        "Antes de enviar tus etiquetas, escribe tu usuario de Telegram incluyendo la @.\n\n"
        "Ejemplo: <code>@tuusuario</code>\n\n"
        "El usuario debe coincidir con la cuenta desde la que estás haciendo el pedido.",
        parse_mode=ParseMode.HTML,
    )


async def patched_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Validate the typed username, then continue with the normal upload flow."""
    if context.user_data.get("mode") != "awaiting_order_username":
        await ORIGINAL_TEXT_HANDLER(update, context)
        return

    typed_username = (update.message.text or "").strip()
    if not re.fullmatch(r"@[A-Za-z0-9_]{5,32}", typed_username):
        await update.message.reply_text(
            "Escribe un usuario válido incluyendo la @.\nEjemplo: <code>@tuusuario</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    account_username = update.effective_user.username
    if not account_username:
        await update.message.reply_text(
            "Tu cuenta de Telegram todavía no tiene un nombre de usuario.\n\n"
            "Créalo en Ajustes de Telegram y luego vuelve a iniciar el pedido con /start."
        )
        return

    if typed_username[1:].lower() != account_username.lower():
        await update.message.reply_text(
            f"Ese usuario no coincide con esta cuenta. Escribe exactamente: "
            f"<code>@{html.escape(account_username)}</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    quantity = int(context.user_data.get("quantity", 0))
    await update.message.reply_text(
        f"✅ Usuario confirmado: <b>@{html.escape(account_username)}</b>",
        parse_mode=ParseMode.HTML,
    )
    await ORIGINAL_BEGIN_LABEL_UPLOAD(update.message, context, quantity)


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
            InlineKeyboardButton("✅ Marcar procesado", callback_data=f"set_status:{order_id}:completed")
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


async def patched_notify_customer_status(context: ContextTypes.DEFAULT_TYPE, order: dict) -> None:
    status = order.get("status")
    label = core.STATUS_LABELS.get(status, status)
    notice = core.STATUS_NOTICES.get(status, "El estado de tu pedido cambió.")
    text = (
        f"🏷 <b>Actualización del pedido {core.esc(order.get('id'))}</b>\n\n"
        f"Estado: <b>{label}</b>\n{core.esc(notice)}"
    )
    if status == "completed":
        text += "\n\nGracias. Tus etiquetas ya fueron procesadas."
    try:
        await context.bot.send_message(
            order["telegram_user_id"],
            text,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📍 Ver estado", callback_data=f"order:{order.get('id')}")
            ]]),
            parse_mode=ParseMode.HTML,
        )
    except TelegramError:
        core.logger.exception("No se pudo notificar al cliente")


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
    core.STATUS_LABELS["completed"] = "✅ Procesado"
    core.STATUS_NOTICES["completed"] = "El procesamiento de tus etiquetas fue finalizado."
    core.is_admin = patched_is_admin
    core.begin_label_upload = patched_begin_label_upload
    core.text_handler = patched_text_handler
    core.admin_order_keyboard = patched_admin_order_keyboard
    core.notify_customer_status = patched_notify_customer_status

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
