import hashlib
import html
import os
from datetime import datetime, timezone
from uuid import uuid4

import bot as core
import clear_app as base
import main as admin_ui
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

MAX_CART_LINES = 20
QUANTITIES = (1, 2, 3, 4, 5)

STATUS_LABELS = {
    "pending_payment_review": "⏳ Pago en revisión",
    "approved": "✅ Pago aprobado",
    "processing": "🧢 Preparando el pedido",
    "shipped": "🚚 Pedido enviado",
    "delivered": "📦 Pedido entregado",
    "rejected": "❌ Pago rechazado",
    "awaiting_coordination": "🤝 Coordinando compra en persona",
    "cancelled": "🚫 Pedido cancelado",
}

STATUS_MESSAGES = {
    "approved": "Tu pago fue aprobado.",
    "processing": "Estamos preparando tu pedido.",
    "shipped": "Tu pedido fue marcado como enviado.",
    "delivered": "Tu pedido fue marcado como entregado.",
    "rejected": "El pago no pudo ser aprobado. Abre un ticket para recibir ayuda.",
    "awaiting_coordination": "Tu compra en persona está pendiente de coordinación por ticket.",
    "cancelled": "Tu pedido fue cancelado.",
}


def esc(value) -> str:
    return html.escape(str(value or ""))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_cart(context: ContextTypes.DEFAULT_TYPE) -> list:
    cart = context.user_data.get("cart")
    if not isinstance(cart, list):
        cart = []
        context.user_data["cart"] = cart
    return cart


def cart_units(cart: list) -> int:
    return sum(int(item.get("quantity", 1)) for item in cart)


def cart_total(cart: list) -> float:
    return sum(float(item.get("price", 0)) * int(item.get("quantity", 1)) for item in cart)


def item_subtotal(item: dict) -> float:
    return float(item.get("price", 0)) * int(item.get("quantity", 1))


def cart_lines(cart: list) -> list[str]:
    lines = []
    for index, item in enumerate(cart, start=1):
        lines.append(
            f"{index}. <b>{esc(item.get('short_name') or item.get('product_name'))}</b>\n"
            f"   Talla: {esc(item.get('size'))} · Cantidad: {item.get('quantity', 1)} · "
            f"{core.money(item_subtotal(item))}"
        )
    return lines


def cart_keyboard(cart: list) -> InlineKeyboardMarkup:
    rows = []
    for index, item in enumerate(cart):
        label = f"🗑 Quitar {item.get('short_name', 'artículo')} · {item.get('size')}"
        rows.append([InlineKeyboardButton(label[:60], callback_data=f"cart_remove:{index}")])
    if cart:
        rows.extend([
            [InlineKeyboardButton("✅ Proceder al pago", callback_data="cart_checkout")],
            [InlineKeyboardButton("🧢 Seguir comprando", callback_data="catalog")],
            [InlineKeyboardButton("🗑 Vaciar carrito", callback_data="cart_empty")],
        ])
    else:
        rows.append([InlineKeyboardButton("🧢 Ver catálogo", callback_data="catalog")])
    rows.append([InlineKeyboardButton("🏠 Menú principal", callback_data="home")])
    return InlineKeyboardMarkup(rows)


def checkout_payment_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🤝 Pagar en persona", callback_data="cart_pay:cash")],
        [InlineKeyboardButton("🅿️ PayPal", callback_data="cart_pay:paypal")],
        [InlineKeyboardButton("🟡 Binance Pay", callback_data="cart_pay:binance")],
        [InlineKeyboardButton("🏦 Zelle", callback_data="cart_pay:zelle")],
        [InlineKeyboardButton("⬅️ Volver al carrito", callback_data="cart_view")],
    ])


def start_keyboard(user, cart: list) -> InlineKeyboardMarkup:
    units = cart_units(cart)
    rows = [
        [InlineKeyboardButton("🧢 Ver catálogo", callback_data="catalog")],
        [InlineKeyboardButton(f"🛒 Mi carrito ({units})", callback_data="cart_view")],
        [InlineKeyboardButton("📍 Estado de mi pedido", callback_data="order_status_menu")],
        [InlineKeyboardButton("🎫 Abrir ticket de soporte", callback_data="ticket_menu")],
    ]
    if admin_ui.is_admin(user):
        rows.append([InlineKeyboardButton("🛠 Panel de administración", callback_data="panel_admin")])
    return InlineKeyboardMarkup(rows)


def admin_home() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎫 Ver tickets abiertos", callback_data="panel_tickets")],
        [InlineKeyboardButton("🖼 Administrar fotos", callback_data="panel_photos")],
        [InlineKeyboardButton("📦 Gestionar pedidos", callback_data="store_admin_orders")],
        [InlineKeyboardButton("📊 Resumen", callback_data="panel_summary")],
        [InlineKeyboardButton("🏠 Menú principal", callback_data="home")],
    ])


async def patched_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cart = get_cart(context).copy()
    context.user_data.clear()
    context.user_data["cart"] = cart
    user = update.effective_user
    if admin_ui.is_admin(user):
        admin_ui.remember_admin(user)
    text = (
        f"¡Bienvenid@ a <b>{esc(core.STORE_NAME)}</b>!\n\n"
        "Agrega una o varias gorras al carrito, selecciona sus tallas y paga todo en un solo pedido."
    )
    if cart:
        text += f"\n\n🛒 Tienes <b>{cart_units(cart)}</b> gorra(s) en el carrito."
    await update.effective_message.reply_text(
        text,
        reply_markup=start_keyboard(user, cart),
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

    photo_ids = admin_ui.read_photo_data().get(product_id, [])[:10]
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
        await query.message.reply_text("⚠️ No pude cargar las fotos en este momento.")

    units = cart_units(get_cart(context))
    await query.message.reply_text(
        f"<b>{esc(item['name'])}</b>\n\n"
        f"Precio por unidad: <b>{core.money(item['price'])}</b>\n"
        f"Tallas: {esc(', '.join(core.SIZES))}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Agregar al carrito", callback_data=f"cart_choose_size:{product_id}")],
            [InlineKeyboardButton(f"🛒 Ver carrito ({units})", callback_data="cart_view")],
            [InlineKeyboardButton("⬅️ Volver al catálogo", callback_data="catalog")],
        ]),
        parse_mode=ParseMode.HTML,
    )


def payment_instructions(method: str, total: float) -> str:
    if method == "paypal":
        return (
            "🅿️ <b>PayPal — Bienes y servicios</b>\n\n"
            f"Envía <b>{core.money(total)}</b> a:\n<code>{esc(core.PAYPAL_ADDRESS)}</code>"
        )
    if method == "zelle":
        return (
            "🏦 <b>Zelle</b>\n\n"
            f"Envía <b>{core.money(total)}</b> a:\n<code>{esc(core.ZELLE_RECIPIENT)}</code>\n\n"
            "Verifica el nombre del destinatario directamente en tu banco."
        )
    return (
        "🟡 <b>Binance Pay</b>\n\n"
        f"Envía el equivalente a <b>{core.money(total)}</b> al Pay ID:\n"
        f"<code>{esc(core.BINANCE_PAY_ID)}</code>"
    )


def order_summary(order: dict) -> str:
    items = order.get("items") or []
    if items:
        lines = cart_lines(items)
    else:
        lines = [
            f"1. <b>{esc(order.get('product_name'))}</b>\n"
            f"   Talla: {esc(order.get('size'))} · {core.money(order.get('price', 0))}"
        ]
    return "\n\n".join(lines)


def find_order(order_id: str):
    return next(
        (order for order in core.read_json(core.ORDERS_FILE) if order.get("order_id") == order_id),
        None,
    )


def update_order(order_id: str, **changes):
    orders = core.read_json(core.ORDERS_FILE)
    result = None
    for order in orders:
        if order.get("order_id") == order_id:
            order.update(changes)
            order.setdefault("status_history", []).append({
                "status": order.get("status"),
                "created_at": now_iso(),
            })
            result = order.copy()
            break
    core.write_json(core.ORDERS_FILE, orders)
    return result


def save_order(order: dict) -> None:
    orders = core.read_json(core.ORDERS_FILE)
    orders.append(order)
    core.write_json(core.ORDERS_FILE, orders)


def customer_orders(user_id: int) -> list:
    return [
        order for order in core.read_json(core.ORDERS_FILE)
        if order.get("telegram_user_id") == user_id
    ]


async def cart_choose_size(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    product_id = query.data.split(":", 1)[1]
    if product_id not in core.PRODUCTS:
        await query.message.reply_text("Producto no encontrado.")
        raise ApplicationHandlerStop
    rows = []
    for index in range(0, len(core.SIZES), 2):
        row = []
        for size_index in range(index, min(index + 2, len(core.SIZES))):
            row.append(InlineKeyboardButton(
                core.SIZES[size_index],
                callback_data=f"cart_size:{product_id}:{size_index}",
            ))
        rows.append(row)
    rows.append([InlineKeyboardButton("❌ Cancelar", callback_data=f"product:{product_id}")])
    await query.message.reply_text("Selecciona la talla:", reply_markup=InlineKeyboardMarkup(rows))
    raise ApplicationHandlerStop


async def cart_choose_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, product_id, size_index_text = query.data.split(":", 2)
    size_index = int(size_index_text)
    size = core.SIZES[size_index]
    rows = [[
        InlineKeyboardButton(str(quantity), callback_data=f"cart_qty:{product_id}:{size_index}:{quantity}")
        for quantity in QUANTITIES
    ]]
    rows.append([InlineKeyboardButton("⬅️ Cambiar talla", callback_data=f"cart_choose_size:{product_id}")])
    await query.message.reply_text(
        f"Talla: <b>{esc(size)}</b>\n¿Cuántas deseas agregar?",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.HTML,
    )
    raise ApplicationHandlerStop


async def cart_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, product_id, size_index_text, quantity_text = query.data.split(":", 3)
    item = core.PRODUCTS.get(product_id)
    if not item:
        await query.message.reply_text("Producto no encontrado.")
        raise ApplicationHandlerStop
    size = core.SIZES[int(size_index_text)]
    quantity = int(quantity_text)
    cart = get_cart(context)
    existing = next(
        (line for line in cart if line.get("product_id") == product_id and line.get("size") == size),
        None,
    )
    if existing:
        existing["quantity"] = min(20, int(existing.get("quantity", 1)) + quantity)
    elif len(cart) < MAX_CART_LINES:
        cart.append({
            "product_id": product_id,
            "product_name": item["name"],
            "short_name": item["short"],
            "size": size,
            "quantity": quantity,
            "price": item["price"],
        })
    else:
        await query.message.reply_text("El carrito alcanzó el máximo de artículos distintos.")
        raise ApplicationHandlerStop

    await query.message.reply_text(
        f"✅ Se agregaron <b>{quantity}</b> gorra(s) al carrito.\n\n"
        f"Artículos: <b>{cart_units(cart)}</b>\n"
        f"Total: <b>{core.money(cart_total(cart))}</b>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 Ver carrito", callback_data="cart_view")],
            [InlineKeyboardButton("🧢 Seguir comprando", callback_data="catalog")],
            [InlineKeyboardButton("✅ Proceder al pago", callback_data="cart_checkout")],
        ]),
        parse_mode=ParseMode.HTML,
    )
    raise ApplicationHandlerStop


async def cart_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()
        message = query.message
    else:
        message = update.effective_message
    cart = get_cart(context)
    if not cart:
        text = "🛒 <b>Tu carrito está vacío.</b>"
    else:
        text = (
            "🛒 <b>Tu carrito</b>\n\n"
            + "\n\n".join(cart_lines(cart))
            + f"\n\nUnidades: <b>{cart_units(cart)}</b>\n"
            + f"Total: <b>{core.money(cart_total(cart))}</b>"
        )
    await message.reply_text(text, reply_markup=cart_keyboard(cart), parse_mode=ParseMode.HTML)
    if query:
        raise ApplicationHandlerStop


async def cart_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    index = int(query.data.split(":", 1)[1])
    cart = get_cart(context)
    if 0 <= index < len(cart):
        cart.pop(index)
    await cart_view(update, context)


async def cart_empty(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    context.user_data["cart"] = []
    await query.message.reply_text(
        "🗑 El carrito fue vaciado.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🧢 Ver catálogo", callback_data="catalog")]]),
    )
    raise ApplicationHandlerStop


async def cart_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    cart = get_cart(context)
    if not cart:
        await query.message.reply_text("Tu carrito está vacío.", reply_markup=cart_keyboard(cart))
        raise ApplicationHandlerStop
    context.user_data["checkout_cart"] = [item.copy() for item in cart]
    await query.message.reply_text(
        f"Total del pedido: <b>{core.money(cart_total(cart))}</b>\n\n¿Cómo deseas pagar?",
        reply_markup=checkout_payment_menu(),
        parse_mode=ParseMode.HTML,
    )
    raise ApplicationHandlerStop


async def cart_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    method = query.data.split(":", 1)[1]
    checkout_cart = context.user_data.get("checkout_cart") or get_cart(context)
    if not checkout_cart:
        await query.message.reply_text("Tu carrito está vacío.")
        raise ApplicationHandlerStop
    context.user_data["checkout_cart"] = [item.copy() for item in checkout_cart]
    context.user_data["cart_payment_method"] = method
    total = cart_total(checkout_cart)
    if method == "cash":
        context.user_data["awaiting"] = "cart_cash_address"
        await query.message.reply_text(
            "🤝 <b>Pago en persona</b>\n\n"
            "Disponible solamente para direcciones de New Jersey. Escribe tu dirección completa, "
            "incluyendo ciudad, estado y código postal:",
            parse_mode=ParseMode.HTML,
        )
    else:
        context.user_data["awaiting"] = "cart_order_name"
        await query.message.reply_text(
            payment_instructions(method, total) + "\n\nEscribe tu <b>nombre completo</b>:",
            parse_mode=ParseMode.HTML,
        )
    raise ApplicationHandlerStop


async def cart_cash_ticket_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not context.user_data.get("checkout_cart") or not context.user_data.get("cart_cash_address"):
        await query.message.reply_text("La sesión expiró. Vuelve al carrito.")
        raise ApplicationHandlerStop
    context.user_data["awaiting"] = "cart_cash_name"
    await query.message.reply_text("Escribe tu nombre completo para abrir el ticket de compra en persona:")
    raise ApplicationHandlerStop


async def custom_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    awaiting = context.user_data.get("awaiting")
    if not awaiting or not awaiting.startswith("cart_"):
        return
    text = update.message.text.strip()
    checkout_cart = context.user_data.get("checkout_cart") or []

    if awaiting == "cart_cash_address":
        if not core.is_new_jersey(text):
            context.user_data.pop("awaiting", None)
            await update.message.reply_text(
                "❌ La dirección no parece estar en New Jersey. El pago en persona no está disponible.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💳 Elegir otro pago", callback_data="cart_checkout")],
                    [InlineKeyboardButton("🎫 Abrir ticket", callback_data="ticket_menu")],
                ]),
            )
        else:
            context.user_data["cart_cash_address"] = text
            context.user_data.pop("awaiting", None)
            await update.message.reply_text(
                "✅ La dirección parece estar en New Jersey. Para proceder debes abrir un ticket de compra en persona.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🎫 Abrir ticket para proceder", callback_data="cart_cash_ticket")
                ]]),
            )
        raise ApplicationHandlerStop

    if awaiting == "cart_cash_name":
        context.user_data["cart_customer_name"] = text
        context.user_data["awaiting"] = "cart_cash_contact"
        await update.message.reply_text("Escribe tu teléfono o @usuario de Telegram:")
        raise ApplicationHandlerStop

    if awaiting == "cart_cash_contact":
        context.user_data["cart_contact"] = text
        context.user_data["awaiting"] = "cart_cash_details"
        await update.message.reply_text("Escribe tu ciudad, horario disponible o cualquier detalle adicional:")
        raise ApplicationHandlerStop

    if awaiting == "cart_cash_details":
        total = cart_total(checkout_cart)
        order_id = "IL-" + uuid4().hex[:7].upper()
        summary = "; ".join(
            f"{item.get('quantity')}x {item.get('short_name')} talla {item.get('size')}"
            for item in checkout_cart
        )
        ticket = core.create_ticket(
            update,
            "Compra en persona",
            text,
            metadata={
                "product_name": summary,
                "size": "Varias tallas" if len(checkout_cart) > 1 else checkout_cart[0].get("size"),
                "price": total,
                "address": context.user_data.get("cart_cash_address"),
            },
            name=context.user_data.get("cart_customer_name"),
            contact=context.user_data.get("cart_contact"),
        )
        order = {
            "order_id": order_id,
            "items": [item.copy() for item in checkout_cart],
            "product_name": summary,
            "size": "Varias",
            "price": total,
            "payment_method": "cash",
            "customer_name": context.user_data.get("cart_customer_name"),
            "contact": context.user_data.get("cart_contact"),
            "telegram_user_id": update.effective_user.id,
            "telegram_username": update.effective_user.username,
            "ticket_id": ticket.get("ticket_id"),
            "status": "awaiting_coordination",
            "status_history": [{"status": "awaiting_coordination", "created_at": now_iso()}],
            "created_at": now_iso(),
        }
        save_order(order)
        await core.notify_admin_ticket(context, ticket)
        await core.confirm_ticket(update, ticket)
        await update.message.reply_text(
            f"📦 Tu número de pedido es <b>{esc(order_id)}</b>.\n"
            f"Estado: <b>{STATUS_LABELS['awaiting_coordination']}</b>\n\n"
            "Puedes revisar el estado desde el menú principal.",
            parse_mode=ParseMode.HTML,
        )
        context.user_data.clear()
        context.user_data["cart"] = []
        raise ApplicationHandlerStop

    if awaiting == "cart_order_name":
        context.user_data["cart_customer_name"] = text
        context.user_data["awaiting"] = "cart_order_contact"
        await update.message.reply_text("Escribe tu teléfono o @usuario de Telegram:")
        raise ApplicationHandlerStop

    if awaiting == "cart_order_contact":
        context.user_data["cart_contact"] = text
        context.user_data["awaiting"] = "cart_receipt"
        await update.message.reply_text(
            "Realiza el pago y envía aquí una foto o captura del comprobante."
        )
        raise ApplicationHandlerStop

    if awaiting == "cart_receipt":
        await update.message.reply_text("Debes enviar una imagen del comprobante, no texto.")
        raise ApplicationHandlerStop


async def cart_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get("awaiting") != "cart_receipt":
        return
    checkout_cart = context.user_data.get("checkout_cart") or []
    if not checkout_cart:
        await update.message.reply_text("La sesión expiró. Vuelve al carrito.")
        raise ApplicationHandlerStop
    total = cart_total(checkout_cart)
    order_id = "IL-" + uuid4().hex[:7].upper()
    method = context.user_data.get("cart_payment_method")
    order = {
        "order_id": order_id,
        "items": [item.copy() for item in checkout_cart],
        "product_name": f"Carrito de {cart_units(checkout_cart)} gorra(s)",
        "size": "Varias",
        "price": total,
        "payment_method": method,
        "customer_name": context.user_data.get("cart_customer_name"),
        "contact": context.user_data.get("cart_contact"),
        "telegram_user_id": update.effective_user.id,
        "telegram_username": update.effective_user.username,
        "receipt_file_id": update.message.photo[-1].file_id,
        "status": "pending_payment_review",
        "status_history": [{"status": "pending_payment_review", "created_at": now_iso()}],
        "created_at": now_iso(),
    }
    save_order(order)
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Aprobar", callback_data=f"approve:{order_id}"),
        InlineKeyboardButton("❌ Rechazar", callback_data=f"reject:{order_id}"),
    ]])
    await context.bot.send_photo(
        core.ADMIN_CHAT_ID,
        order["receipt_file_id"],
        caption=(
            f"🛍 <b>NUEVO PEDIDO {esc(order_id)}</b>\n\n"
            f"{order_summary(order)}\n\n"
            f"Unidades: <b>{cart_units(checkout_cart)}</b>\n"
            f"Total: <b>{core.money(total)}</b>\n"
            f"Pago: {esc(method)}\n"
            f"Cliente: {esc(order.get('customer_name'))}\n"
            f"Contacto: {esc(order.get('contact'))}\n\n"
            "Verifica el dinero directamente en tu cuenta antes de aprobar."
        ),
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
    )
    await update.message.reply_text(
        f"✅ Pedido recibido.\n\n"
        f"Número: <b>{esc(order_id)}</b>\n"
        f"Unidades: <b>{cart_units(checkout_cart)}</b>\n"
        f"Total: <b>{core.money(total)}</b>\n"
        f"Estado: <b>{STATUS_LABELS['pending_payment_review']}</b>\n\n"
        "Puedes consultar el estado desde el menú principal.",
        reply_markup=start_keyboard(update.effective_user, []),
        parse_mode=ParseMode.HTML,
    )
    context.user_data.clear()
    context.user_data["cart"] = []
    raise ApplicationHandlerStop


async def order_status_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()
        message = query.message
    else:
        message = update.effective_message
    orders = customer_orders(update.effective_user.id)
    if not orders:
        await message.reply_text(
            "📍 Todavía no tienes pedidos registrados.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🧢 Ver catálogo", callback_data="catalog")],
                [InlineKeyboardButton("🏠 Menú principal", callback_data="home")],
            ]),
        )
    else:
        rows = []
        for order in reversed(orders[-10:]):
            status = STATUS_LABELS.get(order.get("status"), order.get("status", "Sin estado"))
            rows.append([InlineKeyboardButton(
                f"{order.get('order_id')} · {status}"[:60],
                callback_data=f"order_status:{order.get('order_id')}",
            )])
        rows.append([InlineKeyboardButton("🏠 Menú principal", callback_data="home")])
        await message.reply_text(
            "📍 <b>Selecciona un pedido para ver su estado:</b>",
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode=ParseMode.HTML,
        )
    if query:
        raise ApplicationHandlerStop


async def order_status_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    order_id = query.data.split(":", 1)[1]
    order = find_order(order_id)
    if not order or order.get("telegram_user_id") != query.from_user.id:
        await query.message.reply_text("No encontré ese pedido en tu cuenta.")
        raise ApplicationHandlerStop
    status = STATUS_LABELS.get(order.get("status"), order.get("status", "Sin estado"))
    text = (
        f"📦 <b>Pedido {esc(order_id)}</b>\n\n"
        f"{order_summary(order)}\n\n"
        f"Total: <b>{core.money(order.get('price', 0))}</b>\n"
        f"Estado actual: <b>{status}</b>"
    )
    if order.get("ticket_id"):
        text += f"\nTicket relacionado: <b>{esc(order.get('ticket_id'))}</b>"
    await query.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Actualizar estado", callback_data=f"order_status:{order_id}")],
            [InlineKeyboardButton("⬅️ Mis pedidos", callback_data="order_status_menu")],
            [InlineKeyboardButton("🎫 Abrir soporte", callback_data="ticket_category:Pedido")],
        ]),
        parse_mode=ParseMode.HTML,
    )
    raise ApplicationHandlerStop


async def pedido_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.args:
        order_id = context.args[0].strip().upper()
        order = find_order(order_id)
        if not order or order.get("telegram_user_id") != update.effective_user.id:
            await update.message.reply_text("No encontré ese pedido en tu cuenta.")
            return
        status = STATUS_LABELS.get(order.get("status"), order.get("status", "Sin estado"))
        await update.message.reply_text(
            f"📦 <b>{esc(order_id)}</b>\nEstado: <b>{status}</b>\nTotal: <b>{core.money(order.get('price', 0))}</b>",
            parse_mode=ParseMode.HTML,
        )
    else:
        await order_status_menu(update, context)


async def notify_status(context: ContextTypes.DEFAULT_TYPE, order: dict) -> None:
    status = order.get("status")
    label = STATUS_LABELS.get(status, status)
    message = STATUS_MESSAGES.get(status, "El estado de tu pedido fue actualizado.")
    try:
        await context.bot.send_message(
            order.get("telegram_user_id"),
            f"📦 <b>Actualización del pedido {esc(order.get('order_id'))}</b>\n\n"
            f"Estado: <b>{label}</b>\n{esc(message)}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📍 Ver pedido", callback_data=f"order_status:{order.get('order_id')}")
            ]]),
            parse_mode=ParseMode.HTML,
        )
    except TelegramError:
        pass


def admin_status_keyboard(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Aprobar", callback_data=f"store_set:{order_id}:approved"),
            InlineKeyboardButton("❌ Rechazar", callback_data=f"store_set:{order_id}:rejected"),
        ],
        [InlineKeyboardButton("🧢 Preparando", callback_data=f"store_set:{order_id}:processing")],
        [InlineKeyboardButton("🚚 Enviado", callback_data=f"store_set:{order_id}:shipped")],
        [InlineKeyboardButton("📦 Entregado", callback_data=f"store_set:{order_id}:delivered")],
        [InlineKeyboardButton("⬅️ Pedidos", callback_data="store_admin_orders")],
    ])


async def order_decision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not admin_ui.is_admin(query.from_user):
        await query.answer("No autorizado.", show_alert=True)
        raise ApplicationHandlerStop
    await query.answer()
    action, order_id = query.data.split(":", 1)
    status = "approved" if action == "approve" else "rejected"
    order = update_order(order_id, status=status)
    if not order:
        await query.message.reply_text("Pedido no encontrado.")
        raise ApplicationHandlerStop
    await notify_status(context, order)
    await query.edit_message_reply_markup(reply_markup=admin_status_keyboard(order_id))
    raise ApplicationHandlerStop


async def admin_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not admin_ui.is_admin(query.from_user):
        await query.answer("No autorizado.", show_alert=True)
        raise ApplicationHandlerStop
    await query.answer()
    admin_ui.remember_admin(query.from_user)
    orders = core.read_json(core.ORDERS_FILE)
    if not orders:
        await query.message.reply_text("No hay pedidos registrados.", reply_markup=admin_home())
        raise ApplicationHandlerStop
    rows = []
    for order in reversed(orders[-30:]):
        status = STATUS_LABELS.get(order.get("status"), order.get("status", "Sin estado"))
        label = f"{order.get('order_id')} · {status} · {order.get('customer_name', 'Cliente')}"
        rows.append([InlineKeyboardButton(label[:60], callback_data=f"store_order:{order.get('order_id')}")])
    rows.append([InlineKeyboardButton("⬅️ Panel", callback_data="panel_admin")])
    await query.message.reply_text(
        f"📦 <b>Pedidos registrados: {len(orders)}</b>\n\nSelecciona uno:",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.HTML,
    )
    raise ApplicationHandlerStop


async def admin_order_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not admin_ui.is_admin(query.from_user):
        await query.answer("No autorizado.", show_alert=True)
        raise ApplicationHandlerStop
    await query.answer()
    order_id = query.data.split(":", 1)[1]
    order = find_order(order_id)
    if not order:
        await query.message.reply_text("Pedido no encontrado.")
        raise ApplicationHandlerStop
    status = STATUS_LABELS.get(order.get("status"), order.get("status", "Sin estado"))
    await query.message.reply_text(
        f"📦 <b>{esc(order_id)}</b>\n\n"
        f"{order_summary(order)}\n\n"
        f"Cliente: {esc(order.get('customer_name'))}\n"
        f"Contacto: {esc(order.get('contact'))}\n"
        f"Pago: {esc(order.get('payment_method'))}\n"
        f"Total: <b>{core.money(order.get('price', 0))}</b>\n"
        f"Estado: <b>{status}</b>",
        reply_markup=admin_status_keyboard(order_id),
        parse_mode=ParseMode.HTML,
    )
    raise ApplicationHandlerStop


async def set_order_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not admin_ui.is_admin(query.from_user):
        await query.answer("No autorizado.", show_alert=True)
        raise ApplicationHandlerStop
    await query.answer()
    _, order_id, status = query.data.split(":", 2)
    if status not in STATUS_LABELS:
        await query.message.reply_text("Estado no válido.")
        raise ApplicationHandlerStop
    order = update_order(order_id, status=status)
    if not order:
        await query.message.reply_text("Pedido no encontrado.")
        raise ApplicationHandlerStop
    await notify_status(context, order)
    await query.message.reply_text(
        f"✅ Pedido <b>{esc(order_id)}</b> actualizado a <b>{STATUS_LABELS[status]}</b>.",
        reply_markup=admin_status_keyboard(order_id),
        parse_mode=ParseMode.HTML,
    )
    raise ApplicationHandlerStop


def build_application():
    admin_ui.patched_start = patched_start
    admin_ui.patched_product = patched_product
    admin_ui.admin_home = admin_home
    app = base.build_application()

    app.add_handler(CommandHandler("carrito", cart_view), group=-3)
    app.add_handler(CommandHandler("pedido", pedido_command), group=-3)

    app.add_handler(CallbackQueryHandler(cart_choose_size, pattern=r"^cart_choose_size:"), group=-3)
    app.add_handler(CallbackQueryHandler(cart_choose_quantity, pattern=r"^cart_size:"), group=-3)
    app.add_handler(CallbackQueryHandler(cart_add, pattern=r"^cart_qty:"), group=-3)
    app.add_handler(CallbackQueryHandler(cart_view, pattern=r"^cart_view$"), group=-3)
    app.add_handler(CallbackQueryHandler(cart_remove, pattern=r"^cart_remove:"), group=-3)
    app.add_handler(CallbackQueryHandler(cart_empty, pattern=r"^cart_empty$"), group=-3)
    app.add_handler(CallbackQueryHandler(cart_checkout, pattern=r"^cart_checkout$"), group=-3)
    app.add_handler(CallbackQueryHandler(cart_payment, pattern=r"^cart_pay:"), group=-3)
    app.add_handler(CallbackQueryHandler(cart_cash_ticket_start, pattern=r"^cart_cash_ticket$"), group=-3)

    app.add_handler(CallbackQueryHandler(order_status_menu, pattern=r"^order_status_menu$"), group=-3)
    app.add_handler(CallbackQueryHandler(order_status_detail, pattern=r"^order_status:"), group=-3)

    app.add_handler(CallbackQueryHandler(order_decision, pattern=r"^(approve|reject):"), group=-3)
    app.add_handler(CallbackQueryHandler(admin_orders, pattern=r"^store_admin_orders$"), group=-3)
    app.add_handler(CallbackQueryHandler(admin_order_detail, pattern=r"^store_order:"), group=-3)
    app.add_handler(CallbackQueryHandler(set_order_status, pattern=r"^store_set:"), group=-3)

    app.add_handler(MessageHandler(filters.PHOTO, cart_receipt), group=-3)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, custom_text), group=-3)
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
