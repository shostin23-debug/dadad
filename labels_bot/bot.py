import asyncio
import hashlib
import html
import logging
import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from storage import SupabaseStorage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("labels_bot")

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "7530261961"))
STORE_NAME = os.getenv("STORE_NAME", "Procesador de Etiquetas")
BINANCE_PAY_ID = os.getenv("BINANCE_PAY_ID", "796271520")
UNIT_PRICE = float(os.getenv("LABEL_PRICE", "25"))
MAX_LABELS = int(os.getenv("MAX_LABELS", "50"))

STATUS_LABELS = {
    "pending_payment_review": "⏳ Pago en revisión",
    "approved": "✅ Pago aprobado",
    "processing": "⚙️ Procesando etiquetas",
    "completed": "✅ Completado",
    "rejected": "❌ Pago rechazado",
}

STATUS_NOTICES = {
    "pending_payment_review": "Recibimos tus archivos y tu comprobante. Revisaremos el pago.",
    "approved": "Tu pago fue aprobado. Tus etiquetas entrarán al proceso.",
    "processing": "Tus etiquetas están siendo procesadas.",
    "completed": "El procesamiento de tus etiquetas fue completado.",
    "rejected": "No pudimos aprobar el pago. Abre un ticket para recibir ayuda.",
}

storage = SupabaseStorage()


def esc(value: Any) -> str:
    return html.escape(str(value or ""))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def money(value: float) -> str:
    return f"${value:,.2f}"


def is_admin(user) -> bool:
    return bool(user and user.id == ADMIN_CHAT_ID)


async def db(method, *args, **kwargs):
    return await asyncio.to_thread(method, *args, **kwargs)


def main_menu(user=None) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🏷 Procesar etiquetas", callback_data="new_order")],
        [InlineKeyboardButton("📍 Estado de mis etiquetas", callback_data="my_orders")],
        [InlineKeyboardButton("🎫 Ayuda y preguntas", callback_data="help_menu")],
    ]
    if is_admin(user):
        rows.append([InlineKeyboardButton("🛠 Panel administrativo", callback_data="admin_panel")])
    return InlineKeyboardMarkup(rows)


def quantity_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("1", callback_data="qty:1"),
            InlineKeyboardButton("2", callback_data="qty:2"),
            InlineKeyboardButton("3", callback_data="qty:3"),
        ],
        [
            InlineKeyboardButton("5", callback_data="qty:5"),
            InlineKeyboardButton("10", callback_data="qty:10"),
            InlineKeyboardButton("Otra cantidad", callback_data="qty:custom"),
        ],
        [InlineKeyboardButton("❌ Cancelar", callback_data="home")],
    ])


def help_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Pregunta sobre el pago", callback_data="ticket_category:Pago")],
        [InlineKeyboardButton("🏷 Pregunta sobre etiquetas", callback_data="ticket_category:Etiquetas")],
        [InlineKeyboardButton("📍 Estado de un pedido", callback_data="ticket_category:Estado")],
        [InlineKeyboardButton("💬 Otra pregunta", callback_data="ticket_category:Otro")],
        [InlineKeyboardButton("🏠 Menú principal", callback_data="home")],
    ])


def upload_progress_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("↩️ Quitar la última", callback_data="remove_last_label")],
        [InlineKeyboardButton("❌ Cancelar pedido", callback_data="cancel_order")],
    ])


def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 Pedidos", callback_data="admin_orders")],
        [InlineKeyboardButton("🎫 Tickets abiertos", callback_data="admin_tickets")],
        [InlineKeyboardButton("📊 Estadísticas", callback_data="admin_stats")],
        [InlineKeyboardButton("🏠 Menú principal", callback_data="home")],
    ])


def order_status_keyboard(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Actualizar", callback_data=f"order:{order_id}")],
        [InlineKeyboardButton("🎫 Pedir ayuda", callback_data="ticket_category:Estado")],
        [InlineKeyboardButton("⬅️ Mis pedidos", callback_data="my_orders")],
    ])


def admin_order_keyboard(order: dict) -> InlineKeyboardMarkup:
    order_id = order["id"]
    status = order.get("status")
    rows = []
    if status == "pending_payment_review":
        rows.append([
            InlineKeyboardButton("✅ Aprobar pago", callback_data=f"set_status:{order_id}:approved"),
            InlineKeyboardButton("❌ Rechazar", callback_data=f"set_status:{order_id}:rejected"),
        ])
    elif status == "approved":
        rows.append([InlineKeyboardButton("⚙️ Marcar procesando", callback_data=f"set_status:{order_id}:processing")])
        rows.append([InlineKeyboardButton("❌ Rechazar", callback_data=f"set_status:{order_id}:rejected")])
    elif status == "processing":
        rows.append([InlineKeyboardButton("✅ Marcar completado", callback_data=f"set_status:{order_id}:completed")])
    elif status == "rejected":
        rows.append([InlineKeyboardButton("↩️ Volver a revisión", callback_data=f"set_status:{order_id}:pending_payment_review")])
    rows.append([InlineKeyboardButton("⬅️ Pedidos", callback_data="admin_orders")])
    return InlineKeyboardMarkup(rows)


def describe_file(file_data: dict, index: int) -> str:
    name = file_data.get("file_name") or ("Imagen" if file_data.get("kind") == "photo" else "Documento")
    return f"{index}. {name}"


def order_text(order: dict, *, admin: bool = False) -> str:
    status = STATUS_LABELS.get(order.get("status"), order.get("status", "Sin estado"))
    text = (
        f"🏷 <b>Pedido {esc(order.get('id'))}</b>\n\n"
        f"Cantidad de etiquetas: <b>{order.get('quantity')}</b>\n"
        f"Precio por etiqueta: <b>{money(float(order.get('unit_price', UNIT_PRICE)))}</b>\n"
        f"Total: <b>{money(float(order.get('total', 0)))}</b>\n"
        f"Estado: <b>{status}</b>"
    )
    if admin:
        username = f"@{order.get('telegram_username')}" if order.get("telegram_username") else "Sin usuario"
        text += (
            f"\n\nCliente: {esc(order.get('customer_name'))}\n"
            f"Telegram: {esc(username)}\n"
            f"Telegram ID: <code>{order.get('telegram_user_id')}</code>"
        )
    return text


async def send_stored_file(context: ContextTypes.DEFAULT_TYPE, chat_id: int, file_data: dict, caption: str = "") -> None:
    if file_data.get("kind") == "photo":
        await context.bot.send_photo(chat_id, file_data["file_id"], caption=caption)
    else:
        await context.bot.send_document(chat_id, file_data["file_id"], caption=caption)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    await update.effective_message.reply_text(
        f"Bienvenid@ a <b>{esc(STORE_NAME)}</b>.\n\n"
        f"Cada etiqueta cuesta <b>{money(UNIT_PRICE)}</b>. Puedes enviar varias etiquetas en un solo pedido.\n\n"
        "Envía únicamente etiquetas legítimas que te pertenezcan o que tengas autorización para procesar.",
        reply_markup=main_menu(update.effective_user),
        parse_mode=ParseMode.HTML,
    )


async def home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.message.reply_text(
        f"Menú de <b>{esc(STORE_NAME)}</b>",
        reply_markup=main_menu(query.from_user),
        parse_mode=ParseMode.HTML,
    )


async def new_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.message.reply_text(
        f"¿Cuántas etiquetas deseas procesar?\n\nPrecio: <b>{money(UNIT_PRICE)} por etiqueta</b>.",
        reply_markup=quantity_menu(),
        parse_mode=ParseMode.HTML,
    )


async def choose_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    value = query.data.split(":", 1)[1]
    if value == "custom":
        context.user_data.clear()
        context.user_data["mode"] = "awaiting_custom_quantity"
        await query.message.reply_text(f"Escribe una cantidad entre 1 y {MAX_LABELS}:")
        return
    await begin_label_upload(query.message, context, int(value))


async def begin_label_upload(message, context: ContextTypes.DEFAULT_TYPE, quantity: int) -> None:
    if quantity < 1 or quantity > MAX_LABELS:
        await message.reply_text(f"La cantidad debe estar entre 1 y {MAX_LABELS}.")
        return
    context.user_data.clear()
    context.user_data.update({
        "mode": "collecting_labels",
        "quantity": quantity,
        "label_files": [],
    })
    total = quantity * UNIT_PRICE
    await message.reply_text(
        f"Enviarás <b>{quantity}</b> etiqueta(s).\n"
        f"Total: <b>{money(total)}</b>\n\n"
        "Ahora envía las etiquetas una por una. Se aceptan imágenes y archivos PDF.",
        reply_markup=upload_progress_keyboard(),
        parse_mode=ParseMode.HTML,
    )


async def remove_last_label(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    files = context.user_data.get("label_files") or []
    if context.user_data.get("mode") != "collecting_labels":
        await query.message.reply_text("No hay una carga activa.")
        return
    if files:
        files.pop()
    quantity = int(context.user_data.get("quantity", 0))
    await query.message.reply_text(
        f"Etiquetas recibidas: <b>{len(files)}/{quantity}</b>.",
        reply_markup=upload_progress_keyboard(),
        parse_mode=ParseMode.HTML,
    )


async def cancel_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.message.reply_text("Pedido cancelado.", reply_markup=main_menu(query.from_user))


def extract_file(message) -> dict | None:
    if message.photo:
        return {
            "kind": "photo",
            "file_id": message.photo[-1].file_id,
            "file_name": "imagen.jpg",
        }
    if message.document:
        mime = (message.document.mime_type or "").lower()
        name = message.document.file_name or "documento"
        if mime == "application/pdf" or mime.startswith("image/") or name.lower().endswith((".pdf", ".png", ".jpg", ".jpeg", ".webp")):
            return {
                "kind": "document",
                "file_id": message.document.file_id,
                "file_name": name,
                "mime_type": mime,
            }
    return None


async def handle_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    mode = context.user_data.get("mode")
    file_data = extract_file(update.message)
    if not file_data:
        await update.message.reply_text("Envía una imagen o un archivo PDF válido.")
        return

    if mode == "collecting_labels":
        files = context.user_data.setdefault("label_files", [])
        quantity = int(context.user_data.get("quantity", 0))
        if len(files) >= quantity:
            await update.message.reply_text("Ya recibí todas las etiquetas. Ahora envía el comprobante de Binance Pay.")
            return
        files.append(file_data)
        if len(files) < quantity:
            await update.message.reply_text(
                f"✅ Etiqueta recibida: <b>{len(files)}/{quantity}</b>.\nEnvía la siguiente.",
                reply_markup=upload_progress_keyboard(),
                parse_mode=ParseMode.HTML,
            )
            return

        context.user_data["mode"] = "awaiting_receipt"
        total = quantity * UNIT_PRICE
        await update.message.reply_text(
            f"✅ Recibí las <b>{quantity}</b> etiquetas.\n\n"
            f"Total a pagar: <b>{money(total)}</b>\n"
            f"Método: <b>Binance Pay</b>\n"
            f"Pay ID: <code>{esc(BINANCE_PAY_ID)}</code>\n\n"
            "Realiza el pago y envía una foto o archivo del comprobante.",
            parse_mode=ParseMode.HTML,
        )
        return

    if mode == "awaiting_receipt":
        await finalize_order(update, context, file_data)
        return

    await update.message.reply_text("No estoy esperando un archivo ahora. Usa /start para abrir el menú.")


async def finalize_order(update: Update, context: ContextTypes.DEFAULT_TYPE, receipt: dict) -> None:
    quantity = int(context.user_data.get("quantity", 0))
    labels = context.user_data.get("label_files") or []
    if quantity < 1 or len(labels) != quantity:
        await update.message.reply_text("La sesión está incompleta. Inicia un pedido nuevo.")
        context.user_data.clear()
        return

    order_id = "LB-" + uuid4().hex[:8].upper()
    created_at = now_iso()
    order = {
        "id": order_id,
        "telegram_user_id": update.effective_user.id,
        "telegram_username": update.effective_user.username,
        "customer_name": update.effective_user.full_name,
        "quantity": quantity,
        "unit_price": UNIT_PRICE,
        "total": quantity * UNIT_PRICE,
        "payment_method": "binance_pay",
        "status": "pending_payment_review",
        "label_files": labels,
        "receipt_file": receipt,
        "status_history": [{"status": "pending_payment_review", "created_at": created_at}],
        "created_at": created_at,
        "updated_at": created_at,
    }
    try:
        order = await db(storage.create_order, order)
    except Exception:
        logger.exception("No se pudo guardar el pedido")
        await update.message.reply_text("No pude guardar el pedido. Intenta nuevamente en unos minutos.")
        return

    context.user_data.clear()
    await update.message.reply_text(
        f"✅ Pedido recibido.\n\n"
        f"Número: <b>{esc(order_id)}</b>\n"
        f"Etiquetas: <b>{quantity}</b>\n"
        f"Total: <b>{money(quantity * UNIT_PRICE)}</b>\n"
        f"Estado: <b>{STATUS_LABELS['pending_payment_review']}</b>\n\n"
        "Puedes revisar el estado desde el menú.",
        reply_markup=main_menu(update.effective_user),
        parse_mode=ParseMode.HTML,
    )

    try:
        await context.bot.send_message(
            ADMIN_CHAT_ID,
            order_text(order, admin=True),
            reply_markup=admin_order_keyboard(order),
            parse_mode=ParseMode.HTML,
        )
        for index, label in enumerate(labels, start=1):
            await send_stored_file(context, ADMIN_CHAT_ID, label, f"Etiqueta {index}/{quantity} · {order_id}")
        await send_stored_file(context, ADMIN_CHAT_ID, receipt, f"Comprobante de pago · {order_id}")
    except TelegramError:
        logger.exception("No se pudo notificar al administrador")


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    mode = context.user_data.get("mode")
    text = update.message.text.strip()

    if mode == "awaiting_custom_quantity":
        if not text.isdigit():
            await update.message.reply_text("Escribe solamente un número entero.")
            return
        await begin_label_upload(update.message, context, int(text))
        return

    if mode == "awaiting_receipt":
        await update.message.reply_text("Envía una imagen o archivo del comprobante, no texto.")
        return

    if mode == "awaiting_ticket_message":
        category = context.user_data.get("ticket_category", "Otro")
        await create_ticket(update, context, category, text)
        return

    if mode == "awaiting_admin_reply" and is_admin(update.effective_user):
        await admin_send_ticket_reply(update, context, text)
        return

    if mode == "awaiting_user_reply":
        await user_send_ticket_reply(update, context, text)
        return

    await update.message.reply_text("Usa /start para abrir el menú.", reply_markup=main_menu(update.effective_user))


async def my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()
        message = query.message
        user = query.from_user
    else:
        message = update.effective_message
        user = update.effective_user
    try:
        orders = await db(storage.list_user_orders, user.id, 20)
    except Exception:
        logger.exception("No se pudieron consultar pedidos")
        await message.reply_text("No pude consultar tus pedidos ahora.")
        return
    if not orders:
        await message.reply_text("Todavía no tienes pedidos.", reply_markup=main_menu(user))
        return
    rows = []
    for order in orders:
        status = STATUS_LABELS.get(order.get("status"), order.get("status", "Sin estado"))
        rows.append([InlineKeyboardButton(
            f"{order.get('id')} · {status}"[:60],
            callback_data=f"order:{order.get('id')}",
        )])
    rows.append([InlineKeyboardButton("🏠 Menú principal", callback_data="home")])
    await message.reply_text(
        "📍 <b>Selecciona un pedido:</b>",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.HTML,
    )


async def order_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    order_id = query.data.split(":", 1)[1]
    order = await db(storage.get_order, order_id)
    if not order or (not is_admin(query.from_user) and order.get("telegram_user_id") != query.from_user.id):
        await query.message.reply_text("No encontré ese pedido.")
        return
    await query.message.reply_text(
        order_text(order, admin=is_admin(query.from_user)),
        reply_markup=admin_order_keyboard(order) if is_admin(query.from_user) else order_status_keyboard(order_id),
        parse_mode=ParseMode.HTML,
    )


async def pedido_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await my_orders(update, context)
        return
    order_id = context.args[0].strip().upper()
    order = await db(storage.get_order, order_id)
    if not order or order.get("telegram_user_id") != update.effective_user.id:
        await update.message.reply_text("No encontré ese pedido en tu cuenta.")
        return
    await update.message.reply_text(order_text(order), parse_mode=ParseMode.HTML)


async def notify_customer_status(context: ContextTypes.DEFAULT_TYPE, order: dict) -> None:
    status = order.get("status")
    label = STATUS_LABELS.get(status, status)
    notice = STATUS_NOTICES.get(status, "El estado de tu pedido cambió.")
    text = (
        f"🏷 <b>Actualización del pedido {esc(order.get('id'))}</b>\n\n"
        f"Estado: <b>{label}</b>\n{esc(notice)}"
    )
    if status == "completed":
        text += "\n\nGracias. Tu pedido ya fue marcado como completado."
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
        logger.exception("No se pudo notificar al cliente")


async def set_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_admin(query.from_user):
        await query.answer("No autorizado.", show_alert=True)
        return
    await query.answer()
    _, order_id, status = query.data.split(":", 2)
    if status not in STATUS_LABELS:
        await query.message.reply_text("Estado no válido.")
        return
    order = await db(storage.get_order, order_id)
    if not order:
        await query.message.reply_text("Pedido no encontrado.")
        return
    if order.get("status") == status:
        await query.answer("El pedido ya tiene ese estado.", show_alert=True)
        return
    history = list(order.get("status_history") or [])
    history.append({"status": status, "created_at": now_iso()})
    order = await db(storage.update_order, order_id, {"status": status, "status_history": history})
    await notify_customer_status(context, order)
    try:
        await query.edit_message_text(
            order_text(order, admin=True),
            reply_markup=admin_order_keyboard(order),
            parse_mode=ParseMode.HTML,
        )
    except TelegramError:
        await query.message.reply_text(
            order_text(order, admin=True),
            reply_markup=admin_order_keyboard(order),
            parse_mode=ParseMode.HTML,
        )


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()
        user = query.from_user
        message = query.message
    else:
        user = update.effective_user
        message = update.effective_message
    if not is_admin(user):
        await message.reply_text("No autorizado.")
        return
    await message.reply_text("🛠 <b>Panel administrativo</b>", reply_markup=admin_menu(), parse_mode=ParseMode.HTML)


async def admin_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_admin(query.from_user):
        await query.answer("No autorizado.", show_alert=True)
        return
    await query.answer()
    orders = await db(storage.list_orders, 100)
    if not orders:
        await query.message.reply_text("No hay pedidos.", reply_markup=admin_menu())
        return
    rows = []
    for order in orders:
        status = STATUS_LABELS.get(order.get("status"), order.get("status", "Sin estado"))
        label = f"{order.get('id')} · {status} · {order.get('quantity')} etiqueta(s)"
        rows.append([InlineKeyboardButton(label[:60], callback_data=f"order:{order.get('id')}")])
    rows.append([InlineKeyboardButton("⬅️ Panel", callback_data="admin_panel")])
    await query.message.reply_text(
        f"📦 <b>Pedidos: {len(orders)}</b>",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.HTML,
    )


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_admin(query.from_user):
        await query.answer("No autorizado.", show_alert=True)
        return
    await query.answer()
    orders = await db(storage.list_orders, 1000)
    tickets = await db(storage.list_tickets, limit=1000)
    counts = {status: 0 for status in STATUS_LABELS}
    labels_by_status = {status: 0 for status in STATUS_LABELS}
    money_by_status = {status: 0.0 for status in STATUS_LABELS}
    for order in orders:
        status = order.get("status")
        counts[status] = counts.get(status, 0) + 1
        labels_by_status[status] = labels_by_status.get(status, 0) + int(order.get("quantity", 0))
        money_by_status[status] = money_by_status.get(status, 0.0) + float(order.get("total", 0))
    lines = [
        "📊 <b>Estadísticas</b>",
        f"\nPedidos totales: <b>{len(orders)}</b>",
        f"Etiquetas totales: <b>{sum(int(o.get('quantity', 0)) for o in orders)}</b>",
        f"Valor total: <b>{money(sum(float(o.get('total', 0)) for o in orders))}</b>",
    ]
    for status, label in STATUS_LABELS.items():
        lines.append(
            f"\n{label}\nPedidos: <b>{counts.get(status, 0)}</b> · "
            f"Etiquetas: <b>{labels_by_status.get(status, 0)}</b> · "
            f"Total: <b>{money(money_by_status.get(status, 0))}</b>"
        )
    open_tickets = sum(1 for ticket in tickets if ticket.get("status") == "open")
    lines.append(f"\n🎫 Tickets abiertos: <b>{open_tickets}</b> · Totales: <b>{len(tickets)}</b>")
    await query.message.reply_text("\n".join(lines), reply_markup=admin_menu(), parse_mode=ParseMode.HTML)


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("🎫 Selecciona el tipo de ayuda:", reply_markup=help_menu())


async def ticket_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    category = query.data.split(":", 1)[1]
    context.user_data.clear()
    context.user_data.update({"mode": "awaiting_ticket_message", "ticket_category": category})
    await query.message.reply_text(f"Escribe tu pregunta sobre <b>{esc(category)}</b>:", parse_mode=ParseMode.HTML)


async def create_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE, category: str, message: str) -> None:
    ticket_id = "TK-" + uuid4().hex[:8].upper()
    created_at = now_iso()
    ticket = {
        "id": ticket_id,
        "telegram_user_id": update.effective_user.id,
        "telegram_username": update.effective_user.username,
        "customer_name": update.effective_user.full_name,
        "category": category,
        "status": "open",
        "messages": [{"sender": "customer", "text": message, "created_at": created_at}],
        "created_at": created_at,
        "updated_at": created_at,
    }
    ticket = await db(storage.create_ticket, ticket)
    context.user_data.clear()
    await update.message.reply_text(
        f"✅ Ticket abierto.\nNúmero: <b>{esc(ticket_id)}</b>\n\nRecibirás la respuesta en este chat.",
        reply_markup=main_menu(update.effective_user),
        parse_mode=ParseMode.HTML,
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✍️ Responder", callback_data=f"admin_ticket_reply:{ticket_id}"),
        InlineKeyboardButton("✅ Cerrar", callback_data=f"ticket_close:{ticket_id}"),
    ]])
    await context.bot.send_message(
        ADMIN_CHAT_ID,
        f"🎫 <b>NUEVO TICKET {esc(ticket_id)}</b>\n\n"
        f"Categoría: {esc(category)}\n"
        f"Cliente: {esc(ticket.get('customer_name'))}\n"
        f"Telegram ID: <code>{ticket.get('telegram_user_id')}</code>\n\n"
        f"<b>Mensaje:</b>\n{esc(message)}",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
    )


async def admin_tickets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_admin(query.from_user):
        await query.answer("No autorizado.", show_alert=True)
        return
    await query.answer()
    tickets = await db(storage.list_tickets, status="open", limit=100)
    if not tickets:
        await query.message.reply_text("No hay tickets abiertos.", reply_markup=admin_menu())
        return
    rows = []
    for ticket in tickets:
        label = f"{ticket.get('id')} · {ticket.get('category')} · {ticket.get('customer_name')}"
        rows.append([InlineKeyboardButton(label[:60], callback_data=f"ticket:{ticket.get('id')}")])
    rows.append([InlineKeyboardButton("⬅️ Panel", callback_data="admin_panel")])
    await query.message.reply_text("🎫 <b>Tickets abiertos</b>", reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.HTML)


async def ticket_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    ticket_id = query.data.split(":", 1)[1]
    ticket = await db(storage.get_ticket, ticket_id)
    if not ticket:
        await query.message.reply_text("Ticket no encontrado.")
        return
    if not is_admin(query.from_user) and ticket.get("telegram_user_id") != query.from_user.id:
        await query.message.reply_text("No autorizado.")
        return
    history = []
    for entry in ticket.get("messages") or []:
        sender = "Cliente" if entry.get("sender") == "customer" else "Soporte"
        history.append(f"<b>{sender}:</b> {esc(entry.get('text'))}")
    if is_admin(query.from_user):
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✍️ Responder", callback_data=f"admin_ticket_reply:{ticket_id}")],
            [InlineKeyboardButton("✅ Cerrar", callback_data=f"ticket_close:{ticket_id}")],
            [InlineKeyboardButton("⬅️ Tickets", callback_data="admin_tickets")],
        ])
    else:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("↩️ Agregar mensaje", callback_data=f"user_ticket_reply:{ticket_id}")],
            [InlineKeyboardButton("🏠 Menú principal", callback_data="home")],
        ])
    await query.message.reply_text(
        f"🎫 <b>{esc(ticket_id)}</b>\n"
        f"Categoría: {esc(ticket.get('category'))}\n"
        f"Estado: {esc(ticket.get('status'))}\n\n" + "\n\n".join(history),
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
    )


async def admin_ticket_reply_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_admin(query.from_user):
        await query.answer("No autorizado.", show_alert=True)
        return
    await query.answer()
    ticket_id = query.data.split(":", 1)[1]
    context.user_data.clear()
    context.user_data.update({"mode": "awaiting_admin_reply", "ticket_id": ticket_id})
    await query.message.reply_text(f"Escribe tu respuesta para <b>{esc(ticket_id)}</b>:", parse_mode=ParseMode.HTML)


async def user_ticket_reply_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    ticket_id = query.data.split(":", 1)[1]
    ticket = await db(storage.get_ticket, ticket_id)
    if not ticket or ticket.get("telegram_user_id") != query.from_user.id or ticket.get("status") != "open":
        await query.message.reply_text("Ese ticket no está disponible.")
        return
    context.user_data.clear()
    context.user_data.update({"mode": "awaiting_user_reply", "ticket_id": ticket_id})
    await query.message.reply_text("Escribe el mensaje que deseas agregar:")


async def admin_send_ticket_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    ticket_id = context.user_data.get("ticket_id")
    ticket = await db(storage.get_ticket, ticket_id)
    if not ticket or ticket.get("status") != "open":
        context.user_data.clear()
        await update.message.reply_text("Ese ticket ya no está abierto.")
        return
    messages = list(ticket.get("messages") or [])
    messages.append({"sender": "admin", "text": text, "created_at": now_iso()})
    ticket = await db(storage.update_ticket, ticket_id, {"messages": messages})
    context.user_data.clear()
    await context.bot.send_message(
        ticket["telegram_user_id"],
        f"💬 <b>Soporte — {esc(ticket_id)}</b>\n\n{esc(text)}",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("↩️ Responder", callback_data=f"user_ticket_reply:{ticket_id}")
        ]]),
        parse_mode=ParseMode.HTML,
    )
    await update.message.reply_text("✅ Respuesta enviada.", reply_markup=admin_menu())


async def user_send_ticket_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    ticket_id = context.user_data.get("ticket_id")
    ticket = await db(storage.get_ticket, ticket_id)
    if not ticket or ticket.get("telegram_user_id") != update.effective_user.id or ticket.get("status") != "open":
        context.user_data.clear()
        await update.message.reply_text("Ese ticket no está disponible.")
        return
    messages = list(ticket.get("messages") or [])
    messages.append({"sender": "customer", "text": text, "created_at": now_iso()})
    await db(storage.update_ticket, ticket_id, {"messages": messages})
    context.user_data.clear()
    await context.bot.send_message(
        ADMIN_CHAT_ID,
        f"↩️ <b>NUEVO MENSAJE — {esc(ticket_id)}</b>\n\n{esc(text)}",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✍️ Responder", callback_data=f"admin_ticket_reply:{ticket_id}"),
            InlineKeyboardButton("✅ Cerrar", callback_data=f"ticket_close:{ticket_id}"),
        ]]),
        parse_mode=ParseMode.HTML,
    )
    await update.message.reply_text("✅ Mensaje enviado.", reply_markup=main_menu(update.effective_user))


async def close_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_admin(query.from_user):
        await query.answer("No autorizado.", show_alert=True)
        return
    await query.answer()
    ticket_id = query.data.split(":", 1)[1]
    ticket = await db(storage.update_ticket, ticket_id, {"status": "closed"})
    if not ticket:
        await query.message.reply_text("Ticket no encontrado.")
        return
    try:
        await context.bot.send_message(
            ticket["telegram_user_id"],
            f"✅ El ticket <b>{esc(ticket_id)}</b> fue cerrado.",
            parse_mode=ParseMode.HTML,
        )
    except TelegramError:
        pass
    await query.message.reply_text(f"Ticket {ticket_id} cerrado.", reply_markup=admin_menu())


async def clear_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    message = update.effective_message
    if not chat or not message or chat.type != "private":
        return
    newest = message.message_id
    for message_id in range(newest, max(0, newest - 100), -1):
        try:
            await context.bot.delete_message(chat.id, message_id)
        except TelegramError:
            continue
    await start(update, context)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Error no controlado", exc_info=context.error)


def build_application() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("pedido", pedido_command))
    app.add_handler(CommandHandler("ticket", lambda u, c: help_handler(u, c)))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("clear", clear_chat))

    app.add_handler(CallbackQueryHandler(home, pattern=r"^home$"))
    app.add_handler(CallbackQueryHandler(new_order, pattern=r"^new_order$"))
    app.add_handler(CallbackQueryHandler(choose_quantity, pattern=r"^qty:"))
    app.add_handler(CallbackQueryHandler(remove_last_label, pattern=r"^remove_last_label$"))
    app.add_handler(CallbackQueryHandler(cancel_order, pattern=r"^cancel_order$"))

    app.add_handler(CallbackQueryHandler(my_orders, pattern=r"^my_orders$"))
    app.add_handler(CallbackQueryHandler(order_detail, pattern=r"^order:"))

    app.add_handler(CallbackQueryHandler(help_handler, pattern=r"^help_menu$"))
    app.add_handler(CallbackQueryHandler(ticket_category, pattern=r"^ticket_category:"))
    app.add_handler(CallbackQueryHandler(ticket_detail, pattern=r"^ticket:"))
    app.add_handler(CallbackQueryHandler(admin_ticket_reply_start, pattern=r"^admin_ticket_reply:"))
    app.add_handler(CallbackQueryHandler(user_ticket_reply_start, pattern=r"^user_ticket_reply:"))
    app.add_handler(CallbackQueryHandler(close_ticket, pattern=r"^ticket_close:"))

    app.add_handler(CallbackQueryHandler(admin_panel, pattern=r"^admin_panel$"))
    app.add_handler(CallbackQueryHandler(admin_orders, pattern=r"^admin_orders$"))
    app.add_handler(CallbackQueryHandler(admin_tickets, pattern=r"^admin_tickets$"))
    app.add_handler(CallbackQueryHandler(admin_stats, pattern=r"^admin_stats$"))
    app.add_handler(CallbackQueryHandler(set_status, pattern=r"^set_status:"))

    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, handle_upload))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_error_handler(error_handler)
    return app


def run() -> None:
    app = build_application()
    hostname = os.getenv("RENDER_EXTERNAL_HOSTNAME")
    if hostname:
        webhook_path = hashlib.sha256(BOT_TOKEN.encode("utf-8")).hexdigest()[:32]
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
