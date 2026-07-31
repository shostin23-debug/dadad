import hashlib
import html
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("ilumistore")

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "7530261961"))
STORE_NAME = os.getenv("STORE_NAME", "ilumistore")
PAYPAL_ADDRESS = os.getenv("PAYPAL_ADDRESS", "shostin23@gmail.com")
ZELLE_RECIPIENT = os.getenv("ZELLE_RECIPIENT", "Pshahaha63@gmail.com")
BINANCE_PAY_ID = os.getenv("BINANCE_PAY_ID", "796271520")

DATA_DIR = Path(__file__).resolve().parent / "data"
ORDERS_FILE = DATA_DIR / "orders.json"
TICKETS_FILE = DATA_DIR / "tickets.json"
DATA_DIR.mkdir(exist_ok=True)
for path in (ORDERS_FILE, TICKETS_FILE):
    if not path.exists():
        path.write_text("[]\n", encoding="utf-8")

SIZES = [
    "6 7/8", "7", "7 1/8", "7 1/4", "7 3/8",
    "7 1/2", "7 5/8", "7 3/4", "7 7/8", "8",
]

PRODUCTS = {
    "onfield": {
        "name": "New York Yankees New Era Authentic Collection On-Field Low Profile 59FIFTY Fitted Hat - Navy",
        "short": "Yankees On-Field Low Profile — Navy",
        "price": 40.0,
    },
    "sideflag": {
        "name": "New York Yankees New Era Championship Side Flag A-Frame 59FIFTY Fitted Hat - Navy",
        "short": "Yankees Championship Side Flag — Navy",
        "price": 40.0,
    },
    "cityicon": {
        "name": "New York Yankees New Era City Icon 59FIFTY Fitted Hat - Khaki",
        "short": "Yankees City Icon — Khaki",
        "price": 40.0,
    },
}


def esc(value) -> str:
    return html.escape(str(value or ""))


def money(value: float) -> str:
    return f"${value:,.2f}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> list:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def write_json(path: Path, data: list) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧢 Ver catálogo", callback_data="catalog")],
        [InlineKeyboardButton("🎫 Abrir ticket de soporte", callback_data="ticket_menu")],
        [InlineKeyboardButton("📦 Consultar un pedido", callback_data="ticket_category:Pedido")],
    ])


def payment_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🤝 Pagar en persona", callback_data="pay:cash")],
        [InlineKeyboardButton("🅿️ PayPal", callback_data="pay:paypal")],
        [InlineKeyboardButton("🟡 Binance Pay", callback_data="pay:binance")],
        [InlineKeyboardButton("🏦 Zelle", callback_data="pay:zelle")],
        [InlineKeyboardButton("❌ Cancelar", callback_data="cancel")],
    ])


def support_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Problema con un pago", callback_data="ticket_category:Pago")],
        [InlineKeyboardButton("📦 Pregunta sobre un pedido", callback_data="ticket_category:Pedido")],
        [InlineKeyboardButton("🤝 Compra en persona", callback_data="ticket_category:Compra en persona")],
        [InlineKeyboardButton("💬 Otro asunto", callback_data="ticket_category:Otro")],
        [InlineKeyboardButton("🏠 Menú principal", callback_data="home")],
    ])


def is_new_jersey(address: str) -> bool:
    normalized = address.upper()
    if "NEW JERSEY" in normalized or re.search(r"\bN\.?J\.?\b", normalized):
        return True
    match = re.search(r"\b(\d{5})(?:-\d{4})?\b", normalized)
    return bool(match and 7001 <= int(match.group(1)) <= 8989)


def find_ticket(ticket_id: str):
    return next((t for t in read_json(TICKETS_FILE) if t["ticket_id"] == ticket_id), None)


def update_ticket(ticket_id: str, **changes):
    tickets = read_json(TICKETS_FILE)
    result = None
    for ticket in tickets:
        if ticket["ticket_id"] == ticket_id:
            ticket.update(changes)
            ticket["updated_at"] = now_iso()
            result = ticket.copy()
            break
    write_json(TICKETS_FILE, tickets)
    return result


def add_ticket_message(ticket_id: str, sender: str, text: str):
    tickets = read_json(TICKETS_FILE)
    result = None
    for ticket in tickets:
        if ticket["ticket_id"] == ticket_id:
            ticket.setdefault("messages", []).append({
                "sender": sender,
                "text": text,
                "created_at": now_iso(),
            })
            ticket["updated_at"] = now_iso()
            result = ticket.copy()
            break
    write_json(TICKETS_FILE, tickets)
    return result


def create_ticket(update: Update, category: str, message: str, metadata=None, name=None, contact=None):
    user = update.effective_user
    ticket = {
        "ticket_id": "TK-" + uuid4().hex[:7].upper(),
        "category": category,
        "status": "open",
        "telegram_user_id": user.id,
        "telegram_username": user.username,
        "customer_name": name or user.full_name,
        "contact": contact or (f"@{user.username}" if user.username else ""),
        "metadata": metadata or {},
        "messages": [{"sender": "customer", "text": message, "created_at": now_iso()}],
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    tickets = read_json(TICKETS_FILE)
    tickets.append(ticket)
    write_json(TICKETS_FILE, tickets)
    return ticket


async def notify_admin_ticket(context: ContextTypes.DEFAULT_TYPE, ticket: dict) -> None:
    meta = ticket.get("metadata", {})
    details = ""
    if meta.get("product_name"):
        details = (
            f"\nProducto: {esc(meta['product_name'])}"
            f"\nTalla: {esc(meta.get('size'))}"
            f"\nPrecio: <b>{money(meta.get('price', 0))}</b>"
            f"\nDirección: {esc(meta.get('address'))}"
        )
    username = f"@{esc(ticket['telegram_username'])}" if ticket.get("telegram_username") else "Sin username"
    text = (
        f"🎫 <b>NUEVO TICKET {esc(ticket['ticket_id'])}</b>\n\n"
        f"Categoría: {esc(ticket['category'])}\n"
        f"Cliente: {esc(ticket['customer_name'])}\n"
        f"Contacto: {esc(ticket['contact'])}\n"
        f"Telegram: {username}\n"
        f"Telegram ID: <code>{ticket['telegram_user_id']}</code>"
        f"{details}\n\n"
        f"<b>Mensaje:</b>\n{esc(ticket['messages'][0]['text'])}"
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✍️ Responder", callback_data=f"admin_reply:{ticket['ticket_id']}"),
        InlineKeyboardButton("✅ Cerrar", callback_data=f"admin_close:{ticket['ticket_id']}"),
    ]])
    await context.bot.send_message(ADMIN_CHAT_ID, text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


async def confirm_ticket(update: Update, ticket: dict) -> None:
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Agregar mensaje", callback_data=f"user_reply:{ticket['ticket_id']}")],
        [InlineKeyboardButton("✅ Cerrar ticket", callback_data=f"user_close:{ticket['ticket_id']}")],
        [InlineKeyboardButton("🏠 Menú principal", callback_data="home")],
    ])
    await update.effective_message.reply_text(
        f"✅ Tu ticket fue abierto.\n\nNúmero: <b>{esc(ticket['ticket_id'])}</b>\n"
        "Recibirás la respuesta directamente en este chat.",
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    await update.effective_message.reply_text(
        f"¡Bienvenid@ a <b>{esc(STORE_NAME)}</b>!\n\n"
        "Explora nuestro catálogo, selecciona tu talla y realiza tu pedido.",
        reply_markup=main_menu(),
        parse_mode=ParseMode.HTML,
    )


async def catalog(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton(f"{p['short']} — {money(p['price'])}", callback_data=f"product:{pid}")]
        for pid, p in PRODUCTS.items()
    ]
    keyboard.append([InlineKeyboardButton("🏠 Menú principal", callback_data="home")])
    await query.message.reply_text(
        "🧢 <b>Catálogo</b>\n\nSelecciona una gorra:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML,
    )


async def product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    product_id = query.data.split(":", 1)[1]
    item = PRODUCTS.get(product_id)
    if not item:
        await query.message.reply_text("Producto no encontrado.")
        return
    await query.message.reply_text(
        f"<b>{esc(item['name'])}</b>\n\nPrecio: <b>{money(item['price'])}</b>\n"
        f"Tallas: {esc(', '.join(SIZES))}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 Comprar", callback_data=f"buy:{product_id}")],
            [InlineKeyboardButton("⬅️ Volver", callback_data="catalog")],
        ]),
        parse_mode=ParseMode.HTML,
    )


async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    product_id = query.data.split(":", 1)[1]
    item = PRODUCTS.get(product_id)
    if not item:
        await query.message.reply_text("Producto no encontrado.")
        return
    context.user_data.clear()
    context.user_data["order"] = {
        "product_id": product_id,
        "product_name": item["name"],
        "price": item["price"],
    }
    rows = []
    for index in range(0, len(SIZES), 2):
        rows.append([InlineKeyboardButton(size, callback_data=f"size:{size}") for size in SIZES[index:index + 2]])
    rows.append([InlineKeyboardButton("❌ Cancelar", callback_data="cancel")])
    await query.message.reply_text("Selecciona tu talla:", reply_markup=InlineKeyboardMarkup(rows))


async def choose_size(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    order = context.user_data.get("order")
    if not order:
        await query.message.reply_text("La sesión expiró. Usa /start.")
        return
    order["size"] = query.data.split(":", 1)[1]
    await query.message.reply_text("¿Cómo deseas pagar?", reply_markup=payment_menu())


def payment_text(method: str, amount: float) -> str:
    if method == "paypal":
        return f"🅿️ <b>PayPal — Bienes y servicios</b>\n\nEnvía <b>{money(amount)}</b> a:\n<code>{esc(PAYPAL_ADDRESS)}</code>"
    if method == "zelle":
        return f"🏦 <b>Zelle</b>\n\nEnvía <b>{money(amount)}</b> a:\n<code>{esc(ZELLE_RECIPIENT)}</code>"
    return f"🟡 <b>Binance Pay</b>\n\nEnvía el equivalente a <b>{money(amount)}</b> al Pay ID:\n<code>{esc(BINANCE_PAY_ID)}</code>"


async def choose_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    order = context.user_data.get("order")
    if not order:
        await query.message.reply_text("La sesión expiró. Usa /start.")
        return
    method = query.data.split(":", 1)[1]
    order["payment_method"] = method
    if method == "cash":
        context.user_data["awaiting"] = "cash_address"
        await query.message.reply_text(
            "🤝 <b>Pago en persona</b>\n\nEsta opción solamente está disponible en New Jersey. "
            "Escribe tu dirección completa con ciudad, estado y código postal:",
            parse_mode=ParseMode.HTML,
        )
        return
    context.user_data["awaiting"] = "order_name"
    await query.message.reply_text(payment_text(method, order["price"]) + "\n\nEscribe tu <b>nombre completo</b>:", parse_mode=ParseMode.HTML)


async def ticket_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("🎫 Selecciona el motivo del ticket:", reply_markup=support_menu())


async def ticket_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    category = query.data.split(":", 1)[1]
    context.user_data.clear()
    context.user_data["ticket_category"] = category
    context.user_data["awaiting"] = "ticket_message"
    await query.message.reply_text(f"Describe con detalles tu asunto de <b>{esc(category)}</b>:", parse_mode=ParseMode.HTML)


async def cash_ticket_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not context.user_data.get("order"):
        await query.message.reply_text("La sesión expiró. Usa /start.")
        return
    context.user_data["awaiting"] = "cash_name"
    await query.message.reply_text("Escribe tu nombre completo para abrir el ticket de compra en persona:")


async def admin_reply_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query.from_user.id != ADMIN_CHAT_ID:
        await query.answer("No autorizado.", show_alert=True)
        return
    await query.answer()
    ticket_id = query.data.split(":", 1)[1]
    ticket = find_ticket(ticket_id)
    if not ticket or ticket["status"] != "open":
        await query.message.reply_text("Ese ticket no está abierto.")
        return
    context.user_data.clear()
    context.user_data.update({"awaiting": "admin_ticket_reply", "ticket_id": ticket_id})
    await query.message.reply_text(f"Escribe tu respuesta para <b>{esc(ticket_id)}</b>:", parse_mode=ParseMode.HTML)


async def user_reply_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    ticket_id = query.data.split(":", 1)[1]
    ticket = find_ticket(ticket_id)
    if not ticket or ticket["status"] != "open" or ticket["telegram_user_id"] != query.from_user.id:
        await query.message.reply_text("Ese ticket no está disponible.")
        return
    context.user_data.clear()
    context.user_data.update({"awaiting": "user_ticket_reply", "ticket_id": ticket_id})
    await query.message.reply_text("Escribe el mensaje que deseas agregar:")


async def close_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    action, ticket_id = query.data.split(":", 1)
    ticket = find_ticket(ticket_id)
    if not ticket:
        await query.message.reply_text("Ticket no encontrado.")
        return
    if action == "admin_close" and query.from_user.id != ADMIN_CHAT_ID:
        return
    if action == "user_close" and query.from_user.id != ticket["telegram_user_id"]:
        return
    update_ticket(ticket_id, status="closed")
    if action == "admin_close":
        await context.bot.send_message(ticket["telegram_user_id"], f"✅ El ticket <b>{esc(ticket_id)}</b> fue cerrado.", parse_mode=ParseMode.HTML)
    else:
        await context.bot.send_message(ADMIN_CHAT_ID, f"ℹ️ El cliente cerró el ticket <b>{esc(ticket_id)}</b>.", parse_mode=ParseMode.HTML)
    await query.message.reply_text(f"Ticket {ticket_id} cerrado.")


async def navigation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.data == "cancel":
        context.user_data.clear()
        await query.message.reply_text("Operación cancelada.", reply_markup=main_menu())
    else:
        context.user_data.clear()
        await query.message.reply_text(f"Menú de <b>{esc(STORE_NAME)}</b>", reply_markup=main_menu(), parse_mode=ParseMode.HTML)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    awaiting = context.user_data.get("awaiting")

    if awaiting == "admin_ticket_reply" and update.effective_user.id == ADMIN_CHAT_ID:
        ticket_id = context.user_data.get("ticket_id")
        ticket = add_ticket_message(ticket_id, "admin", text)
        context.user_data.clear()
        if ticket:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("↩️ Responder", callback_data=f"user_reply:{ticket_id}")],
                [InlineKeyboardButton("✅ Cerrar ticket", callback_data=f"user_close:{ticket_id}")],
            ])
            await context.bot.send_message(ticket["telegram_user_id"], f"💬 <b>Soporte — {esc(ticket_id)}</b>\n\n{esc(text)}", reply_markup=keyboard, parse_mode=ParseMode.HTML)
            await update.message.reply_text("✅ Respuesta enviada.")
        return

    if awaiting == "user_ticket_reply":
        ticket_id = context.user_data.get("ticket_id")
        ticket = add_ticket_message(ticket_id, "customer", text)
        context.user_data.clear()
        if ticket:
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("✍️ Responder", callback_data=f"admin_reply:{ticket_id}"),
                InlineKeyboardButton("✅ Cerrar", callback_data=f"admin_close:{ticket_id}"),
            ]])
            await context.bot.send_message(ADMIN_CHAT_ID, f"↩️ <b>NUEVO MENSAJE — {esc(ticket_id)}</b>\n\n{esc(text)}", reply_markup=keyboard, parse_mode=ParseMode.HTML)
            await update.message.reply_text("✅ Mensaje agregado al ticket.", reply_markup=main_menu())
        return

    if awaiting == "ticket_message":
        ticket = create_ticket(update, context.user_data.get("ticket_category", "Otro"), text)
        context.user_data.clear()
        await notify_admin_ticket(context, ticket)
        await confirm_ticket(update, ticket)
        return

    order = context.user_data.get("order")
    if awaiting == "cash_address" and order:
        order["address"] = text
        context.user_data.pop("awaiting", None)
        if not is_new_jersey(text):
            await update.message.reply_text(
                "❌ La dirección no parece estar en New Jersey. El pago en persona no está disponible. ",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💳 Elegir otro método", callback_data="buy:" + order["product_id"])],
                    [InlineKeyboardButton("🎫 Abrir ticket", callback_data="ticket_menu")],
                ]),
            )
            return
        await update.message.reply_text(
            "✅ La dirección parece estar en New Jersey. Para proceder debes abrir un ticket y coordinar la compra.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎫 Abrir ticket para proceder", callback_data="cash_ticket")]]),
        )
        return

    if awaiting == "cash_name" and order:
        order["customer_name"] = text
        context.user_data["awaiting"] = "cash_contact"
        await update.message.reply_text("Escribe tu teléfono o @usuario de Telegram:")
        return

    if awaiting == "cash_contact" and order:
        order["contact"] = text
        context.user_data["awaiting"] = "cash_details"
        await update.message.reply_text("Escribe tu ciudad, horario disponible o cualquier detalle adicional:")
        return

    if awaiting == "cash_details" and order:
        ticket = create_ticket(
            update,
            "Compra en persona",
            text,
            metadata={
                "product_name": order["product_name"],
                "size": order["size"],
                "price": order["price"],
                "address": order["address"],
            },
            name=order.get("customer_name"),
            contact=order.get("contact"),
        )
        context.user_data.clear()
        await notify_admin_ticket(context, ticket)
        await confirm_ticket(update, ticket)
        return

    if awaiting == "order_name" and order:
        order["customer_name"] = text
        context.user_data["awaiting"] = "order_contact"
        await update.message.reply_text("Escribe tu teléfono o @usuario de Telegram:")
        return

    if awaiting == "order_contact" and order:
        order["contact"] = text
        context.user_data["awaiting"] = "receipt"
        await update.message.reply_text("Realiza el pago y envía una foto o captura del comprobante.")
        return

    if awaiting == "receipt":
        await update.message.reply_text("Debes enviar una imagen del comprobante, no texto.")
        return

    await update.message.reply_text("Usa /start para abrir el menú.", reply_markup=main_menu())


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get("awaiting") != "receipt" or not context.user_data.get("order"):
        await update.message.reply_text("No estoy esperando un comprobante ahora.", reply_markup=main_menu())
        return
    order = context.user_data["order"].copy()
    order.update({
        "order_id": "IL-" + uuid4().hex[:7].upper(),
        "telegram_user_id": update.effective_user.id,
        "telegram_username": update.effective_user.username,
        "receipt_file_id": update.message.photo[-1].file_id,
        "status": "pending_payment_review",
        "created_at": now_iso(),
    })
    orders = read_json(ORDERS_FILE)
    orders.append(order)
    write_json(ORDERS_FILE, orders)
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Aprobar", callback_data=f"approve:{order['order_id']}"),
        InlineKeyboardButton("❌ Rechazar", callback_data=f"reject:{order['order_id']}"),
    ]])
    await context.bot.send_photo(
        ADMIN_CHAT_ID,
        order["receipt_file_id"],
        caption=(
            f"🛍 <b>NUEVO PEDIDO {esc(order['order_id'])}</b>\n\n"
            f"Producto: {esc(order['product_name'])}\nTalla: {esc(order['size'])}\n"
            f"Total: <b>{money(order['price'])}</b>\nPago: {esc(order['payment_method'])}\n"
            f"Cliente: {esc(order['customer_name'])}\nContacto: {esc(order['contact'])}\n\n"
            "Verifica el dinero directamente en tu cuenta antes de aprobar."
        ),
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
    )
    await update.message.reply_text(
        f"✅ Pedido recibido.\n\nNúmero: <b>{esc(order['order_id'])}</b>\nTotal: <b>{money(order['price'])}</b>\n"
        "Revisaremos el pago y te enviaremos la confirmación.",
        reply_markup=main_menu(),
        parse_mode=ParseMode.HTML,
    )
    context.user_data.clear()


async def order_decision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query.from_user.id != ADMIN_CHAT_ID:
        await query.answer("No autorizado.", show_alert=True)
        return
    await query.answer()
    action, order_id = query.data.split(":", 1)
    orders = read_json(ORDERS_FILE)
    order = None
    for item in orders:
        if item["order_id"] == order_id:
            item["status"] = "approved" if action == "approve" else "rejected"
            order = item.copy()
            break
    write_json(ORDERS_FILE, orders)
    if not order:
        await query.message.reply_text("Pedido no encontrado.")
        return
    if action == "approve":
        text = f"✅ Tu pedido <b>{esc(order_id)}</b> fue aprobado.\n\nGracias por haber comprado, ¡hasta pronto!"
    else:
        text = f"❌ Tu pedido <b>{esc(order_id)}</b> no pudo ser aprobado. Abre un ticket para recibir ayuda."
    await context.bot.send_message(order["telegram_user_id"], text, reply_markup=main_menu(), parse_mode=ParseMode.HTML)
    await query.edit_message_reply_markup(reply_markup=None)


def build_application() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancelar", start))
    app.add_handler(CallbackQueryHandler(catalog, pattern=r"^catalog$"))
    app.add_handler(CallbackQueryHandler(product, pattern=r"^product:"))
    app.add_handler(CallbackQueryHandler(buy, pattern=r"^buy:"))
    app.add_handler(CallbackQueryHandler(choose_size, pattern=r"^size:"))
    app.add_handler(CallbackQueryHandler(choose_payment, pattern=r"^pay:"))
    app.add_handler(CallbackQueryHandler(ticket_menu_handler, pattern=r"^ticket_menu$"))
    app.add_handler(CallbackQueryHandler(ticket_category, pattern=r"^ticket_category:"))
    app.add_handler(CallbackQueryHandler(cash_ticket_button, pattern=r"^cash_ticket$"))
    app.add_handler(CallbackQueryHandler(admin_reply_start, pattern=r"^admin_reply:"))
    app.add_handler(CallbackQueryHandler(user_reply_start, pattern=r"^user_reply:"))
    app.add_handler(CallbackQueryHandler(close_ticket, pattern=r"^(admin_close|user_close):"))
    app.add_handler(CallbackQueryHandler(order_decision, pattern=r"^(approve|reject):"))
    app.add_handler(CallbackQueryHandler(navigation, pattern=r"^(home|cancel)$"))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
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
