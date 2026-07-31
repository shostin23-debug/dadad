import hashlib
import html
import os
from collections import Counter, defaultdict

import store_app as store
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ApplicationHandlerStop, ContextTypes


CONFIRMED_STATUSES = {"approved", "processing", "shipped", "delivered"}


def esc(value) -> str:
    return html.escape(str(value or ""))


def order_units(order: dict) -> int:
    items = order.get("items")
    if isinstance(items, list) and items:
        return sum(max(1, int(item.get("quantity", 1))) for item in items)
    return max(1, int(order.get("quantity", 1) or 1))


def order_amount(order: dict) -> float:
    try:
        return float(order.get("price", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def concise_items(order: dict, limit: int = 4) -> list[str]:
    items = order.get("items")
    if not isinstance(items, list) or not items:
        name = str(order.get("product_name") or "Producto")
        return [f"1× {name[:55]} · talla {order.get('size') or 'N/D'}"]

    lines = []
    for item in items[:limit]:
        quantity = max(1, int(item.get("quantity", 1)))
        name = str(item.get("short_name") or item.get("product_name") or "Gorra")
        size = item.get("size") or "N/D"
        lines.append(f"{quantity}× {name[:55]} · talla {size}")
    remaining = len(items) - limit
    if remaining > 0:
        lines.append(f"… y {remaining} artículo(s) más")
    return lines


def admin_order_text(order: dict, notice: str | None = None, compact: bool = False) -> str:
    status_key = order.get("status")
    status = store.STATUS_LABELS.get(status_key, str(status_key or "Sin estado"))
    lines = []
    if notice:
        lines.extend([f"✅ <b>{esc(notice)}</b>", ""])
    lines.extend([
        f"📦 <b>Pedido {esc(order.get('order_id'))}</b>",
        f"Estado actual: <b>{status}</b>",
        "",
        "<b>Artículos:</b>",
    ])
    item_limit = 3 if compact else 10
    lines.extend(f"• {esc(line)}" for line in concise_items(order, item_limit))
    lines.extend([
        "",
        f"Unidades: <b>{order_units(order)}</b>",
        f"Total: <b>{store.core.money(order_amount(order))}</b>",
        f"Pago: {esc(order.get('payment_method') or 'No especificado')}",
        f"Cliente: {esc(order.get('customer_name') or 'Sin nombre')}",
        f"Contacto: {esc(order.get('contact') or 'Sin contacto')}",
    ])
    if order.get("ticket_id"):
        lines.append(f"Ticket: <b>{esc(order.get('ticket_id'))}</b>")
    return "\n".join(lines)


def fixed_admin_status_keyboard(order_id: str, status: str | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    if status == "pending_payment_review":
        rows.append([
            InlineKeyboardButton("✅ Aprobar pago", callback_data=f"store_set:{order_id}:approved"),
            InlineKeyboardButton("❌ Rechazar pago", callback_data=f"store_set:{order_id}:rejected"),
        ])
    elif status == "awaiting_coordination":
        rows.append([
            InlineKeyboardButton("✅ Coordinación confirmada", callback_data=f"store_set:{order_id}:approved"),
            InlineKeyboardButton("🚫 Cancelar", callback_data=f"store_set:{order_id}:cancelled"),
        ])
    elif status == "approved":
        rows.extend([
            [InlineKeyboardButton("🧢 Marcar preparando", callback_data=f"store_set:{order_id}:processing")],
            [InlineKeyboardButton("🚚 Marcar enviado", callback_data=f"store_set:{order_id}:shipped")],
            [InlineKeyboardButton("🚫 Cancelar", callback_data=f"store_set:{order_id}:cancelled")],
        ])
    elif status == "processing":
        rows.extend([
            [InlineKeyboardButton("🚚 Marcar enviado", callback_data=f"store_set:{order_id}:shipped")],
            [InlineKeyboardButton("📦 Marcar entregado", callback_data=f"store_set:{order_id}:delivered")],
            [InlineKeyboardButton("🚫 Cancelar", callback_data=f"store_set:{order_id}:cancelled")],
        ])
    elif status == "shipped":
        rows.append([
            InlineKeyboardButton("📦 Marcar entregado", callback_data=f"store_set:{order_id}:delivered")
        ])
    elif status in {"rejected", "cancelled"}:
        rows.append([
            InlineKeyboardButton("↩️ Volver a revisión", callback_data=f"store_set:{order_id}:pending_payment_review")
        ])

    rows.append([InlineKeyboardButton("⬅️ Pedidos", callback_data="store_admin_orders")])
    return InlineKeyboardMarkup(rows)


def fixed_update_order(order_id: str, **changes):
    orders = store.core.read_json(store.core.ORDERS_FILE)
    result = None
    for order in orders:
        if order.get("order_id") != order_id:
            continue

        old_status = order.get("status")
        new_status = changes.get("status", old_status)
        status_changed = new_status != old_status

        for key, value in changes.items():
            order[key] = value

        if status_changed:
            order.setdefault("status_history", []).append({
                "status": new_status,
                "created_at": store.now_iso(),
            })

        result = order.copy()
        result["_status_changed"] = status_changed
        break

    store.core.write_json(store.core.ORDERS_FILE, orders)
    return result


async def edit_order_message(
    query,
    order: dict,
    notice: str | None = None,
) -> None:
    keyboard = fixed_admin_status_keyboard(order.get("order_id"), order.get("status"))
    is_media = bool(
        getattr(query.message, "photo", None)
        or getattr(query.message, "video", None)
        or getattr(query.message, "document", None)
        or query.message.caption is not None
    )
    try:
        if is_media:
            await query.edit_message_caption(
                caption=admin_order_text(order, notice=notice, compact=True),
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
            )
        else:
            await query.edit_message_text(
                text=admin_order_text(order, notice=notice, compact=False),
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML,
            )
    except TelegramError:
        try:
            await query.edit_message_reply_markup(reply_markup=keyboard)
        except TelegramError:
            pass


async def fixed_order_decision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not store.admin_ui.is_admin(query.from_user):
        await query.answer("No autorizado.", show_alert=True)
        raise ApplicationHandlerStop

    action, order_id = query.data.split(":", 1)
    status = "approved" if action == "approve" else "rejected"
    order = fixed_update_order(order_id, status=status)
    if not order:
        await query.answer("Pedido no encontrado.", show_alert=True)
        raise ApplicationHandlerStop

    if not order.pop("_status_changed", False):
        await query.answer("El pedido ya tenía ese estado.")
        await edit_order_message(query, order)
        raise ApplicationHandlerStop

    await store.notify_status(context, order)
    await query.answer("Estado actualizado")
    await edit_order_message(
        query,
        order,
        notice=f"Estado actualizado a {store.STATUS_LABELS[status]}",
    )
    raise ApplicationHandlerStop


async def fixed_admin_order_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not store.admin_ui.is_admin(query.from_user):
        await query.answer("No autorizado.", show_alert=True)
        raise ApplicationHandlerStop

    await query.answer()
    order_id = query.data.split(":", 1)[1]
    order = store.find_order(order_id)
    if not order:
        await query.message.reply_text("Pedido no encontrado.")
        raise ApplicationHandlerStop

    await query.message.reply_text(
        admin_order_text(order),
        reply_markup=fixed_admin_status_keyboard(order_id, order.get("status")),
        parse_mode=ParseMode.HTML,
    )
    raise ApplicationHandlerStop


async def fixed_set_order_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not store.admin_ui.is_admin(query.from_user):
        await query.answer("No autorizado.", show_alert=True)
        raise ApplicationHandlerStop

    _, order_id, status = query.data.split(":", 2)
    if status not in store.STATUS_LABELS:
        await query.answer("Estado no válido.", show_alert=True)
        raise ApplicationHandlerStop

    order = fixed_update_order(order_id, status=status)
    if not order:
        await query.answer("Pedido no encontrado.", show_alert=True)
        raise ApplicationHandlerStop

    if not order.pop("_status_changed", False):
        await query.answer("El pedido ya tenía ese estado.")
        await edit_order_message(query, order)
        raise ApplicationHandlerStop

    await store.notify_status(context, order)
    await query.answer("Estado actualizado")
    await edit_order_message(
        query,
        order,
        notice=f"Estado actualizado a {store.STATUS_LABELS[status]}",
    )
    raise ApplicationHandlerStop


async def fixed_panel_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not store.admin_ui.is_admin(query.from_user):
        await query.answer("No autorizado.", show_alert=True)
        return

    await query.answer()
    orders = store.core.read_json(store.core.ORDERS_FILE)
    tickets = store.core.read_json(store.core.TICKETS_FILE)

    counts = Counter()
    units = Counter()
    amounts = defaultdict(float)
    payments_count = Counter()
    payments_amount = defaultdict(float)
    unique_customers = set()

    for order in orders:
        status = str(order.get("status") or "unknown")
        amount = order_amount(order)
        unit_count = order_units(order)
        counts[status] += 1
        units[status] += unit_count
        amounts[status] += amount

        payment = str(order.get("payment_method") or "No especificado")
        payments_count[payment] += 1
        payments_amount[payment] += amount

        user_id = order.get("telegram_user_id")
        if user_id is not None:
            unique_customers.add(user_id)

    total_orders = len(orders)
    total_units = sum(order_units(order) for order in orders)
    total_value = sum(order_amount(order) for order in orders)
    confirmed_orders = [o for o in orders if o.get("status") in CONFIRMED_STATUSES]
    confirmed_value = sum(order_amount(o) for o in confirmed_orders)
    confirmed_units = sum(order_units(o) for o in confirmed_orders)
    delivered_value = amounts["delivered"]

    open_tickets = sum(1 for ticket in tickets if ticket.get("status") == "open")
    closed_tickets = sum(1 for ticket in tickets if ticket.get("status") == "closed")

    lines = [
        "📊 <b>Estadísticas completas de ilumistore</b>",
        "",
        f"Pedidos totales: <b>{total_orders}</b>",
        f"Gorras totales: <b>{total_units}</b>",
        f"Clientes únicos: <b>{len(unique_customers)}</b>",
        f"Valor de todos los pedidos: <b>{store.core.money(total_value)}</b>",
        "",
        "<b>Ventas confirmadas</b>",
        f"Pedidos: <b>{len(confirmed_orders)}</b>",
        f"Gorras: <b>{confirmed_units}</b>",
        f"Importe: <b>{store.core.money(confirmed_value)}</b>",
        f"Entregado: <b>{store.core.money(delivered_value)}</b>",
        "",
        "<b>Todos los estados</b>",
    ]

    for status_key, label in store.STATUS_LABELS.items():
        lines.append(
            f"{label}: <b>{counts[status_key]}</b> pedido(s) · "
            f"{units[status_key]} gorra(s) · {store.core.money(amounts[status_key])}"
        )

    unknown_statuses = sorted(set(counts) - set(store.STATUS_LABELS))
    if unknown_statuses:
        lines.extend(["", "<b>Estados antiguos/no reconocidos</b>"])
        for status in unknown_statuses:
            lines.append(
                f"• {esc(status)}: <b>{counts[status]}</b> · "
                f"{units[status]} gorra(s) · {store.core.money(amounts[status])}"
            )

    lines.extend(["", "<b>Métodos de pago</b>"])
    if payments_count:
        for payment in sorted(payments_count):
            lines.append(
                f"• {esc(payment)}: <b>{payments_count[payment]}</b> pedido(s) · "
                f"{store.core.money(payments_amount[payment])}"
            )
    else:
        lines.append("• Sin pedidos registrados")

    lines.extend([
        "",
        "<b>Soporte</b>",
        f"Tickets abiertos: <b>{open_tickets}</b>",
        f"Tickets cerrados: <b>{closed_tickets}</b>",
        f"Tickets totales: <b>{len(tickets)}</b>",
    ])

    await query.message.reply_text(
        "\n".join(lines),
        reply_markup=store.admin_home(),
        parse_mode=ParseMode.HTML,
    )


def build_application():
    store.update_order = fixed_update_order
    store.admin_status_keyboard = fixed_admin_status_keyboard
    store.order_decision = fixed_order_decision
    store.admin_order_detail = fixed_admin_order_detail
    store.set_order_status = fixed_set_order_status
    store.admin_ui.panel_summary = fixed_panel_summary
    return store.build_application()


def run() -> None:
    app = build_application()
    hostname = os.getenv("RENDER_EXTERNAL_HOSTNAME")
    if hostname:
        webhook_path = hashlib.sha256(
            store.core.BOT_TOKEN.encode("utf-8")
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
