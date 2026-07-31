import hashlib
import html
import json
import logging
import os
from pathlib import Path

import bot as core
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logger = logging.getLogger("ilumistore.fixes")
BASE_DIR = Path(__file__).resolve().parent
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "Goldito1").lstrip("@").lower()
ADMIN_STATE_FILE = BASE_DIR / "data" / "admin_chat_id.txt"
PRODUCT_PHOTOS_FILE = BASE_DIR / "data" / "product_photos.json"
PRODUCT_PHOTOS_FILE.parent.mkdir(exist_ok=True)


def esc(value) -> str:
    return html.escape(str(value or ""))


def is_admin(user) -> bool:
    if not user:
        return False
    username = (user.username or "").lower()
    return user.id == core.ADMIN_CHAT_ID or bool(ADMIN_USERNAME and username == ADMIN_USERNAME)


def remember_admin(user) -> None:
    if not is_admin(user):
        return
    core.ADMIN_CHAT_ID = user.id
    ADMIN_STATE_FILE.write_text(str(user.id), encoding="utf-8")


def restore_admin() -> None:
    try:
        core.ADMIN_CHAT_ID = int(ADMIN_STATE_FILE.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        pass


def read_photo_data() -> dict:
    try:
        data = json.loads(PRODUCT_PHOTOS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_photo_data(data: dict) -> None:
    PRODUCT_PHOTOS_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def admin_home() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎫 Ver tickets abiertos", callback_data="panel_tickets")],
        [InlineKeyboardButton("🖼 Administrar fotos", callback_data="panel_photos")],
        [InlineKeyboardButton("📦 Pedidos pendientes", callback_data="panel_orders")],
        [InlineKeyboardButton("📊 Resumen", callback_data="panel_summary")],
        [InlineKeyboardButton("🏠 Menú principal", callback_data="home")],
    ])


def photo_action_menu(product_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Agregar fotos", callback_data=f"photo_add:{product_id}")],
        [InlineKeyboardButton("🗑 Borrar todas", callback_data=f"photo_clear:{product_id}")],
        [InlineKeyboardButton("⬅️ Volver", callback_data="panel_photos")],
    ])


async def patched_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    user = update.effective_user
    admin = is_admin(user)
    if admin:
        remember_admin(user)
    rows = [
        [InlineKeyboardButton("🧢 Ver catálogo", callback_data="catalog")],
        [InlineKeyboardButton("🎫 Abrir ticket de soporte", callback_data="ticket_menu")],
        [InlineKeyboardButton("📦 Consultar un pedido", callback_data="ticket_category:Pedido")],
    ]
    if admin:
        rows.append([InlineKeyboardButton("🛠 Panel de administración", callback_data="panel_admin")])
    text = (
        f"¡Bienvenid@ a <b>{esc(core.STORE_NAME)}</b>!\n\n"
        "Explora nuestro catálogo, selecciona tu talla y realiza tu pedido."
    )
    if admin:
        text += "\n\n🛠 Tu panel administrativo está activado."
    await update.effective_message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.HTML,
    )


async def patched_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    product_id = query.data.split(":", 1)[1]
    item = core.PRODUCTS.get(product_id)
    if not item:
        await query.message.reply_text("Producto no encontrado.")
        return

    photo_ids = read_photo_data().get(product_id, [])[:10]
    try:
        if len(photo_ids) == 1:
            await query.message.reply_photo(photo=photo_ids[0])
        elif photo_ids:
            await query.message.reply_media_group(
                media=[InputMediaPhoto(media=file_id) for file_id in photo_ids]
            )
        else:
            await query.message.reply_text("📷 Las fotos de esta gorra todavía no han sido cargadas.")
    except TelegramError:
        logger.exception("No se pudieron mostrar las fotos de %s", product_id)
        await query.message.reply_text("⚠️ No pude cargar las fotos en este momento.")

    await query.message.reply_text(
        f"<b>{esc(item['name'])}</b>\n\n"
        f"Precio: <b>{core.money(item['price'])}</b>\n"
        f"Tallas: {esc(', '.join(core.SIZES))}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 Comprar", callback_data=f"buy:{product_id}")],
            [InlineKeyboardButton("⬅️ Volver", callback_data="catalog")],
        ]),
        parse_mode=ParseMode.HTML,
    )


async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        f"Tu Telegram ID es: <code>{update.effective_user.id}</code>\n"
        f"Tu usuario es: @{esc(update.effective_user.username or 'sin_usuario')}",
        parse_mode=ParseMode.HTML,
    )


async def panel_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()
        user = query.from_user
        message = query.message
    else:
        user = update.effective_user
        message = update.effective_message
    if not is_admin(user):
        await message.reply_text("No tienes acceso al panel administrativo.")
        return
    remember_admin(user)
    await message.reply_text(
        "🛠 <b>Panel de administración</b>",
        reply_markup=admin_home(),
        parse_mode=ParseMode.HTML,
    )


async def panel_tickets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_admin(query.from_user):
        await query.answer("No autorizado.", show_alert=True)
        return
    await query.answer()
    remember_admin(query.from_user)
    tickets = [t for t in core.read_json(core.TICKETS_FILE) if t.get("status") == "open"]
    if not tickets:
        await query.message.reply_text("✅ No hay tickets abiertos.", reply_markup=admin_home())
        return
    rows = []
    for ticket in reversed(tickets[-30:]):
        label = (
            f"{ticket.get('ticket_id')} · {ticket.get('category', 'Otro')} · "
            f"{ticket.get('customer_name', 'Cliente')}"
        )
        rows.append([
            InlineKeyboardButton(
                label[:60],
                callback_data=f"panel_ticket:{ticket.get('ticket_id')}",
            )
        ])
    rows.append([InlineKeyboardButton("⬅️ Panel", callback_data="panel_admin")])
    await query.message.reply_text(
        f"🎫 <b>Tickets abiertos: {len(tickets)}</b>\n\nSelecciona uno:",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.HTML,
    )


async def panel_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_admin(query.from_user):
        await query.answer("No autorizado.", show_alert=True)
        return
    await query.answer()
    ticket_id = query.data.split(":", 1)[1]
    ticket = core.find_ticket(ticket_id)
    if not ticket:
        await query.message.reply_text("Ticket no encontrado.")
        return
    history = []
    for entry in ticket.get("messages", []):
        sender = "Cliente" if entry.get("sender") == "customer" else "Soporte"
        history.append(f"<b>{sender}:</b> {esc(entry.get('text'))}")
    meta = ticket.get("metadata", {})
    product = ""
    if meta.get("product_name"):
        product = (
            f"\nProducto: {esc(meta.get('product_name'))}"
            f"\nTalla: {esc(meta.get('size'))}"
            f"\nPrecio: <b>{core.money(meta.get('price', 0))}</b>"
            f"\nDirección: {esc(meta.get('address'))}"
        )
    text = (
        f"🎫 <b>{esc(ticket_id)}</b>\n"
        f"Estado: {esc(ticket.get('status'))}\n"
        f"Categoría: {esc(ticket.get('category'))}\n"
        f"Cliente: {esc(ticket.get('customer_name'))}\n"
        f"Contacto: {esc(ticket.get('contact'))}{product}\n\n"
        f"<b>Conversación:</b>\n" + ("\n\n".join(history) or "Sin mensajes.")
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✍️ Responder", callback_data=f"admin_reply:{ticket_id}"),
            InlineKeyboardButton("✅ Cerrar", callback_data=f"admin_close:{ticket_id}"),
        ],
        [InlineKeyboardButton("⬅️ Tickets", callback_data="panel_tickets")],
    ])
    await query.message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


async def panel_photos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_admin(query.from_user):
        await query.answer("No autorizado.", show_alert=True)
        return
    await query.answer()
    data = read_photo_data()
    rows = []
    for product_id, item in core.PRODUCTS.items():
        count = len(data.get(product_id, []))
        rows.append([
            InlineKeyboardButton(
                f"{item['short']} · {count} foto(s)",
                callback_data=f"photo_product:{product_id}",
            )
        ])
    rows.append([InlineKeyboardButton("⬅️ Panel", callback_data="panel_admin")])
    await query.message.reply_text(
        "🖼 <b>Fotos del catálogo</b>\n\nSelecciona una gorra:",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.HTML,
    )


async def photo_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_admin(query.from_user):
        await query.answer("No autorizado.", show_alert=True)
        return
    await query.answer()
    product_id = query.data.split(":", 1)[1]
    item = core.PRODUCTS.get(product_id)
    if not item:
        await query.message.reply_text("Producto no encontrado.")
        return
    count = len(read_photo_data().get(product_id, []))
    await query.message.reply_text(
        f"<b>{esc(item['short'])}</b>\n\nFotos guardadas: <b>{count}</b>",
        reply_markup=photo_action_menu(product_id),
        parse_mode=ParseMode.HTML,
    )


async def photo_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_admin(query.from_user):
        await query.answer("No autorizado.", show_alert=True)
        return
    await query.answer()
    product_id = query.data.split(":", 1)[1]
    context.user_data.clear()
    context.user_data["awaiting"] = "admin_product_photo"
    context.user_data["photo_product_id"] = product_id
    await query.message.reply_text(
        "Ahora envía las fotos de esta gorra, una por una o varias seguidas. "
        "Cuando termines, pulsa el botón.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Terminar", callback_data="photo_done")],
            [InlineKeyboardButton("❌ Cancelar", callback_data="panel_photos")],
        ]),
    )


async def photo_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_admin(query.from_user):
        await query.answer("No autorizado.", show_alert=True)
        return
    await query.answer()
    context.user_data.clear()
    await query.message.reply_text("✅ Carga de fotos terminada.", reply_markup=admin_home())


async def photo_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_admin(query.from_user):
        await query.answer("No autorizado.", show_alert=True)
        return
    await query.answer()
    product_id = query.data.split(":", 1)[1]
    data = read_photo_data()
    data[product_id] = []
    write_photo_data(data)
    await query.message.reply_text("🗑 Se borraron las fotos de esa gorra.", reply_markup=photo_action_menu(product_id))


async def admin_photo_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get("awaiting") != "admin_product_photo":
        return
    if not is_admin(update.effective_user):
        return
    product_id = context.user_data.get("photo_product_id")
    if product_id not in core.PRODUCTS:
        context.user_data.clear()
        return
    data = read_photo_data()
    photos = data.setdefault(product_id, [])
    file_id = update.message.photo[-1].file_id
    if file_id not in photos:
        photos.append(file_id)
    data[product_id] = photos[-10:]
    write_photo_data(data)
    await update.message.reply_text(
        f"✅ Foto guardada. Total para esta gorra: <b>{len(data[product_id])}</b>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Terminar", callback_data="photo_done")],
            [InlineKeyboardButton("🗑 Borrar todas", callback_data=f"photo_clear:{product_id}")],
        ]),
        parse_mode=ParseMode.HTML,
    )
    raise ApplicationHandlerStop


async def panel_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_admin(query.from_user):
        await query.answer("No autorizado.", show_alert=True)
        return
    await query.answer()
    orders = [
        order for order in core.read_json(core.ORDERS_FILE)
        if order.get("status") == "pending_payment_review"
    ]
    if not orders:
        await query.message.reply_text("✅ No hay pedidos pendientes.", reply_markup=admin_home())
        return
    lines = [f"📦 <b>Pedidos pendientes: {len(orders)}</b>"]
    for order in reversed(orders[-20:]):
        lines.append(
            f"\n<b>{esc(order.get('order_id'))}</b>\n"
            f"{esc(order.get('product_name'))}\n"
            f"Talla: {esc(order.get('size'))} · Total: {core.money(order.get('price', 0))}\n"
            f"Cliente: {esc(order.get('customer_name'))}"
        )
    await query.message.reply_text("\n".join(lines), reply_markup=admin_home(), parse_mode=ParseMode.HTML)


async def panel_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_admin(query.from_user):
        await query.answer("No autorizado.", show_alert=True)
        return
    await query.answer()
    tickets = core.read_json(core.TICKETS_FILE)
    orders = core.read_json(core.ORDERS_FILE)
    open_tickets = sum(1 for t in tickets if t.get("status") == "open")
    pending = sum(1 for o in orders if o.get("status") == "pending_payment_review")
    approved = [o for o in orders if o.get("status") == "approved"]
    revenue = sum(float(o.get("price", 0)) for o in approved)
    await query.message.reply_text(
        "📊 <b>Resumen de ilumistore</b>\n\n"
        f"Tickets abiertos: <b>{open_tickets}</b>\n"
        f"Pedidos pendientes: <b>{pending}</b>\n"
        f"Pedidos aprobados: <b>{len(approved)}</b>\n"
        f"Ingresos aprobados: <b>{core.money(revenue)}</b>",
        reply_markup=admin_home(),
        parse_mode=ParseMode.HTML,
    )


def build_application():
    restore_admin()
    core.start = patched_start
    core.product = patched_product
    app = core.build_application()

    app.add_handler(CommandHandler("admin", panel_admin), group=-1)
    app.add_handler(CommandHandler("myid", my_id), group=-1)
    app.add_handler(CallbackQueryHandler(panel_admin, pattern=r"^panel_admin$"), group=-1)
    app.add_handler(CallbackQueryHandler(panel_tickets, pattern=r"^panel_tickets$"), group=-1)
    app.add_handler(CallbackQueryHandler(panel_ticket, pattern=r"^panel_ticket:"), group=-1)
    app.add_handler(CallbackQueryHandler(panel_photos, pattern=r"^panel_photos$"), group=-1)
    app.add_handler(CallbackQueryHandler(photo_product, pattern=r"^photo_product:"), group=-1)
    app.add_handler(CallbackQueryHandler(photo_add, pattern=r"^photo_add:"), group=-1)
    app.add_handler(CallbackQueryHandler(photo_done, pattern=r"^photo_done$"), group=-1)
    app.add_handler(CallbackQueryHandler(photo_clear, pattern=r"^photo_clear:"), group=-1)
    app.add_handler(CallbackQueryHandler(panel_orders, pattern=r"^panel_orders$"), group=-1)
    app.add_handler(CallbackQueryHandler(panel_summary, pattern=r"^panel_summary$"), group=-1)
    app.add_handler(MessageHandler(filters.PHOTO, admin_photo_guard), group=-1)
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
