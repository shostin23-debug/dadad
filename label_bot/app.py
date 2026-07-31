import hashlib
import html
import json
import logging
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from uuid import uuid4

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
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
logger = logging.getLogger("label_processor")

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "7530261961"))
BOT_NAME = os.getenv("BOT_NAME", "Procesador de Etiquetas")
SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
PAYPAL_ADDRESS = os.getenv("PAYPAL_ADDRESS", "shostin23@gmail.com")
ZELLE_RECIPIENT = os.getenv("ZELLE_RECIPIENT", "Pshahaha63@gmail.com")
BINANCE_PAY_ID = os.getenv("BINANCE_PAY_ID", "796271520")
PRICE_PER_LABEL = float(os.getenv("PRICE_PER_LABEL", "25"))
MAX_LABELS_PER_ORDER = int(os.getenv("MAX_LABELS_PER_ORDER", "50"))

STATUS_LABELS = {
    "draft_uploading": "📤 Esperando etiquetas",
    "awaiting_payment_method": "💳 Esperando método de pago",
    "awaiting_customer_name": "👤 Esperando nombre",
    "awaiting_contact": "📱 Esperando contacto",
    "awaiting_receipt": "🧾 Esperando comprobante",
    "pending_payment_review": "⏳ Pago en revisión",
    "approved": "✅ Pago aprobado",
    "processing": "⚙️ Procesando etiquetas",
    "completed": "✅ Procesamiento completado",
    "rejected": "❌ Pago rechazado",
    "cancelled": "🚫 Solicitud cancelada",
}

IN_PROGRESS_STATUSES = {
    "draft_uploading",
    "awaiting_payment_method",
    "awaiting_customer_name",
    "awaiting_contact",
    "awaiting_receipt",
}

PAYMENT_LABELS = {
    "paypal": "PayPal",
    "zelle": "Zelle",
    "binance": "Binance Pay",
}


def esc(value) -> str:
    return html.escape(str(value or ""))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def money(value: float) -> str:
    return f"${float(value):,.2f}"


def is_admin(update: Update) -> bool:
    return bool(update.effective_user and update.effective_user.id == ADMIN_CHAT_ID)


class SupabaseDB:
    def __init__(self) -> None:
        self.base_url = f"{SUPABASE_URL}/rest/v1"
        self.headers = {
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
            "Content-Type": "application/json",
        }

    async def request(
        self,
        method: str,
        table: str,
        *,
        params: dict | None = None,
        payload: dict | list | None = None,
        prefer: str | None = None,
    ):
        headers = dict(self.headers)
        if prefer:
            headers["Prefer"] = prefer
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.request(
                method,
                f"{self.base_url}/{table}",
                params=params,
                json=payload,
                headers=headers,
            )
        if response.status_code >= 400:
            logger.error(
                "Supabase %s %s failed: %s %s",
                method,
                table,
                response.status_code,
                response.text,
            )
            response.raise_for_status()
        if not response.content:
            return None
        return response.json()

    async def insert(self, table: str, payload: dict):
        rows = await self.request(
            "POST",
            table,
            payload=payload,
            prefer="return=representation",
        )
        return rows[0] if rows else None

    async def select(self, table: str, **params):
        params = {"select": "*", **params}
        return await self.request("GET", table, params=params) or []

    async def update(self, table: str, filters_map: dict, payload: dict):
        params = {key: f"eq.{value}" for key, value in filters_map.items()}
        rows = await self.request(
            "PATCH",
            table,
            params=params,
            payload=payload,
            prefer="return=representation",
        )
        return rows[0] if rows else None

    async def create_order(self, update: Update, quantity: int):
        order_id = "LB-" + uuid4().hex[:8].upper()
        status = "draft_uploading"
        payload = {
            "order_id": order_id,
            "telegram_user_id": update.effective_user.id,
            "telegram_username": update.effective_user.username,
            "quantity": quantity,
            "total": quantity * PRICE_PER_LABEL,
            "status": status,
            "status_history": [{"status": status, "created_at": now_iso()}],
        }
        return await self.insert("label_orders", payload)

    async def get_order(self, order_id: str):
        rows = await self.select(
            "label_orders",
            order_id=f"eq.{order_id}",
            limit="1",
        )
        return rows[0] if rows else None

    async def get_user_orders(self, user_id: int, limit: int = 20):
        return await self.select(
            "label_orders",
            telegram_user_id=f"eq.{user_id}",
            order="created_at.desc",
            limit=str(limit),
        )

    async def get_latest_in_progress(self, user_id: int):
        orders = await self.get_user_orders(user_id, limit=20)
        return next(
            (order for order in orders if order.get("status") in IN_PROGRESS_STATUSES),
            None,
        )

    async def get_all_orders(self, limit: int = 1000):
        return await self.select(
            "label_orders",
            order="created_at.desc",
            limit=str(limit),
        )

    async def update_order(self, order_id: str, **changes):
        changes["updated_at"] = now_iso()
        return await self.update("label_orders", {"order_id": order_id}, changes)

    async def set_status(self, order_id: str, status: str):
        order = await self.get_order(order_id)
        if not order:
            return None, False
        if order.get("status") == status:
            return order, False
        history = list(order.get("status_history") or [])
        history.append({"status": status, "created_at": now_iso()})
        updated = await self.update_order(
            order_id,
            status=status,
            status_history=history,
        )
        return updated, True

    async def add_label_file(self, order_id: str, file_data: dict):
        existing = await self.select(
            "label_files",
            order_id=f"eq.{order_id}",
            file_unique_id=f"eq.{file_data['file_unique_id']}",
            limit="1",
        )
        if existing:
            return existing[0], False
        current = await self.get_label_files(order_id)
        payload = {
            "order_id": order_id,
            "sequence": len(current) + 1,
            **file_data,
        }
        return await self.insert("label_files", payload), True

    async def get_label_files(self, order_id: str):
        return await self.select(
            "label_files",
            order_id=f"eq.{order_id}",
            order="sequence.asc",
        )


db = SupabaseDB()


def main_keyboard(admin: bool = False, has_draft: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("➕ Nueva solicitud", callback_data="new_order")],
        [InlineKeyboardButton("📍 Mis solicitudes", callback_data="my_orders")],
    ]
    if has_draft:
        rows.insert(1, [InlineKeyboardButton("▶️ Continuar solicitud", callback_data="resume_order")])
    if admin:
        rows.append([InlineKeyboardButton("🛠 Panel administrativo", callback_data="admin_home")])
    return InlineKeyboardMarkup(rows)


def quantity_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("1", callback_data="qty:1"),
            InlineKeyboardButton("2", callback_data="qty:2"),
            InlineKeyboardButton("3", callback_data="qty:3"),
            InlineKeyboardButton("4", callback_data="qty:4"),
            InlineKeyboardButton("5", callback_data="qty:5"),
        ],
        [InlineKeyboardButton("🔢 Otra cantidad", callback_data="qty:custom")],
        [InlineKeyboardButton("🏠 Menú principal", callback_data="home")],
    ])


def payment_keyboard(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🅿️ PayPal", callback_data=f"pay:{order_id}:paypal")],
        [InlineKeyboardButton("🏦 Zelle", callback_data=f"pay:{order_id}:zelle")],
        [InlineKeyboardButton("🟡 Binance Pay", callback_data=f"pay:{order_id}:binance")],
        [InlineKeyboardButton("🚫 Cancelar solicitud", callback_data=f"cancel:{order_id}")],
    ])


def admin_status_keyboard(order_id: str, status: str) -> InlineKeyboardMarkup:
    rows = []
    if status == "pending_payment_review":
        rows.append([
            InlineKeyboardButton("✅ Aprobar", callback_data=f"set:{order_id}:approved"),
            InlineKeyboardButton("❌ Rechazar", callback_data=f"set:{order_id}:rejected"),
        ])
    elif status == "approved":
        rows.append([InlineKeyboardButton("⚙️ Marcar procesando", callback_data=f"set:{order_id}:processing")])
    elif status == "processing":
        rows.append([InlineKeyboardButton("✅ Marcar completado", callback_data=f"set:{order_id}:completed")])
    elif status == "rejected":
        rows.append([InlineKeyboardButton("↩️ Volver a revisión", callback_data=f"set:{order_id}:pending_payment_review")])
    elif status == "completed":
        rows.append([InlineKeyboardButton("↩️ Volver a procesando", callback_data=f"set:{order_id}:processing")])
    rows.append([InlineKeyboardButton("📎 Ver etiquetas", callback_data=f"files:{order_id}")])
    rows.append([InlineKeyboardButton("⬅️ Pedidos", callback_data="admin_orders")])
    return InlineKeyboardMarkup(rows)


def payment_instructions(method: str, total: float) -> str:
    if method == "paypal":
        return (
            "🅿️ <b>PayPal — Bienes y servicios</b>\n\n"
            f"Envía <b>{money(total)}</b> a:\n<code>{esc(PAYPAL_ADDRESS)}</code>"
        )
    if method == "zelle":
        return (
            "🏦 <b>Zelle</b>\n\n"
            f"Envía <b>{money(total)}</b> a:\n<code>{esc(ZELLE_RECIPIENT)}</code>"
        )
    return (
        "🟡 <b>Binance Pay</b>\n\n"
        f"Envía el equivalente a <b>{money(total)}</b> al Pay ID:\n"
        f"<code>{esc(BINANCE_PAY_ID)}</code>"
    )


def get_message_file(update: Update):
    message = update.effective_message
    if not message:
        return None
    if message.photo:
        photo = message.photo[-1]
        return {
            "file_id": photo.file_id,
            "file_unique_id": photo.file_unique_id,
            "file_type": "photo",
            "file_name": None,
        }
    if message.document:
        document = message.document
        return {
            "file_id": document.file_id,
            "file_unique_id": document.file_unique_id,
            "file_type": "document",
            "file_name": document.file_name,
        }
    return None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    draft = await db.get_latest_in_progress(update.effective_user.id)
    await update.effective_message.reply_text(
        f"¡Bienvenid@ a <b>{esc(BOT_NAME)}</b>!\n\n"
        f"Cada etiqueta cuesta <b>{money(PRICE_PER_LABEL)}</b>. Puedes enviar varias etiquetas dentro de una sola solicitud.",
        reply_markup=main_keyboard(is_admin(update), bool(draft)),
        parse_mode=ParseMode.HTML,
    )


async def new_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    existing = await db.get_latest_in_progress(query.from_user.id)
    if existing:
        await query.message.reply_text(
            f"Ya tienes una solicitud incompleta: <b>{esc(existing['order_id'])}</b>.\n"
            "Continúala o cancélala antes de crear otra.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("▶️ Continuar", callback_data="resume_order")],
                [InlineKeyboardButton("🚫 Cancelar", callback_data=f"cancel:{existing['order_id']}")],
            ]),
            parse_mode=ParseMode.HTML,
        )
        return
    await query.message.reply_text(
        "¿Cuántas etiquetas deseas procesar?",
        reply_markup=quantity_keyboard(),
    )


async def choose_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    value = query.data.split(":", 1)[1]
    if value == "custom":
        context.user_data["awaiting"] = "custom_quantity"
        await query.message.reply_text(
            f"Escribe una cantidad entre 1 y {MAX_LABELS_PER_ORDER}:"
        )
        return
    await begin_order(update, context, int(value))


async def begin_order(update: Update, context: ContextTypes.DEFAULT_TYPE, quantity: int) -> None:
    if quantity < 1 or quantity > MAX_LABELS_PER_ORDER:
        await update.effective_message.reply_text(
            f"La cantidad debe estar entre 1 y {MAX_LABELS_PER_ORDER}."
        )
        return
    order = await db.create_order(update, quantity)
    context.user_data.clear()
    context.user_data.update({
        "active_order_id": order["order_id"],
        "awaiting": "labels",
    })
    await update.effective_message.reply_text(
        f"📤 <b>Solicitud {esc(order['order_id'])}</b>\n\n"
        f"Envía ahora <b>{quantity}</b> etiqueta(s). Puedes mandarlas como foto, PNG, JPG o PDF, una por una.\n\n"
        f"Total a pagar después de subirlas: <b>{money(order['total'])}</b>",
        parse_mode=ParseMode.HTML,
    )


async def resume_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    order = await db.get_latest_in_progress(query.from_user.id)
    if not order:
        await query.message.reply_text("No tienes solicitudes incompletas.")
        return
    await continue_order(query.message, context, order)


async def continue_order(message, context: ContextTypes.DEFAULT_TYPE, order: dict) -> None:
    order_id = order["order_id"]
    status = order["status"]
    context.user_data.clear()
    context.user_data["active_order_id"] = order_id
    if status == "draft_uploading":
        files = await db.get_label_files(order_id)
        context.user_data["awaiting"] = "labels"
        await message.reply_text(
            f"📤 Solicitud <b>{esc(order_id)}</b>\n"
            f"Has enviado <b>{len(files)}</b> de <b>{order['quantity']}</b> etiquetas.\n\n"
            "Envía las etiquetas restantes.",
            parse_mode=ParseMode.HTML,
        )
    elif status == "awaiting_payment_method":
        await message.reply_text(
            f"Las etiquetas están completas. Total: <b>{money(order['total'])}</b>.\n\nSelecciona cómo pagar:",
            reply_markup=payment_keyboard(order_id),
            parse_mode=ParseMode.HTML,
        )
    elif status == "awaiting_customer_name":
        context.user_data["awaiting"] = "customer_name"
        await message.reply_text("Escribe tu nombre completo:")
    elif status == "awaiting_contact":
        context.user_data["awaiting"] = "contact"
        await message.reply_text("Escribe tu teléfono o @usuario de Telegram:")
    elif status == "awaiting_receipt":
        context.user_data["awaiting"] = "receipt"
        method = order.get("payment_method")
        await message.reply_text(
            payment_instructions(method, order["total"])
            + "\n\nEnvía una foto, captura o PDF del comprobante.",
            parse_mode=ParseMode.HTML,
        )


async def choose_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, order_id, method = query.data.split(":", 2)
    order = await db.get_order(order_id)
    if not order or order.get("telegram_user_id") != query.from_user.id:
        await query.message.reply_text("No encontré esa solicitud.")
        return
    if method not in PAYMENT_LABELS:
        await query.message.reply_text("Método de pago no válido.")
        return
    await db.update_order(
        order_id,
        payment_method=method,
        status="awaiting_customer_name",
    )
    context.user_data.clear()
    context.user_data.update({
        "active_order_id": order_id,
        "awaiting": "customer_name",
    })
    await query.message.reply_text(
        payment_instructions(method, order["total"])
        + "\n\nEscribe tu <b>nombre completo</b>:",
        parse_mode=ParseMode.HTML,
    )


async def cancel_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    order_id = query.data.split(":", 1)[1]
    order = await db.get_order(order_id)
    if not order or order.get("telegram_user_id") != query.from_user.id:
        await query.message.reply_text("No encontré esa solicitud.")
        return
    if order.get("status") not in IN_PROGRESS_STATUSES:
        await query.message.reply_text("Esa solicitud ya no se puede cancelar desde aquí.")
        return
    await db.set_status(order_id, "cancelled")
    context.user_data.clear()
    await query.message.reply_text(
        f"🚫 Solicitud <b>{esc(order_id)}</b> cancelada.",
        reply_markup=main_keyboard(is_admin(update)),
        parse_mode=ParseMode.HTML,
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.effective_message.text.strip()
    awaiting = context.user_data.get("awaiting")
    if awaiting == "custom_quantity":
        try:
            quantity = int(text)
        except ValueError:
            await update.effective_message.reply_text("Escribe solamente un número entero.")
            return
        await begin_order(update, context, quantity)
        return

    order_id = context.user_data.get("active_order_id")
    order = await db.get_order(order_id) if order_id else await db.get_latest_in_progress(update.effective_user.id)
    if not order:
        await update.effective_message.reply_text("Usa /start para comenzar una solicitud.")
        return
    context.user_data["active_order_id"] = order["order_id"]

    if awaiting == "customer_name" or order.get("status") == "awaiting_customer_name":
        await db.update_order(
            order["order_id"],
            customer_name=text,
            status="awaiting_contact",
        )
        context.user_data["awaiting"] = "contact"
        await update.effective_message.reply_text("Escribe tu teléfono o @usuario de Telegram:")
        return

    if awaiting == "contact" or order.get("status") == "awaiting_contact":
        await db.update_order(
            order["order_id"],
            contact=text,
            status="awaiting_receipt",
        )
        context.user_data["awaiting"] = "receipt"
        await update.effective_message.reply_text(
            payment_instructions(order["payment_method"], order["total"])
            + "\n\nEnvía una foto, captura o PDF del comprobante.",
            parse_mode=ParseMode.HTML,
        )
        return

    if awaiting == "receipt" or order.get("status") == "awaiting_receipt":
        await update.effective_message.reply_text("El comprobante debe enviarse como foto, imagen o PDF.")
        return

    if order.get("status") == "draft_uploading":
        await update.effective_message.reply_text("Envía la etiqueta como foto, imagen o PDF.")
        return

    await update.effective_message.reply_text("Usa /start para ver las opciones disponibles.")


async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    file_data = get_message_file(update)
    if not file_data:
        return
    order_id = context.user_data.get("active_order_id")
    order = await db.get_order(order_id) if order_id else await db.get_latest_in_progress(update.effective_user.id)
    if not order:
        await update.effective_message.reply_text("Primero crea una solicitud con /start.")
        raise ApplicationHandlerStop
    if order.get("telegram_user_id") != update.effective_user.id:
        await update.effective_message.reply_text("No puedes modificar esa solicitud.")
        raise ApplicationHandlerStop
    context.user_data["active_order_id"] = order["order_id"]

    if order.get("status") == "draft_uploading":
        files = await db.get_label_files(order["order_id"])
        if len(files) >= int(order["quantity"]):
            await update.effective_message.reply_text("Ya enviaste todas las etiquetas de esta solicitud.")
            raise ApplicationHandlerStop
        _, created = await db.add_label_file(order["order_id"], file_data)
        files = await db.get_label_files(order["order_id"])
        if not created:
            await update.effective_message.reply_text("Esa etiqueta ya fue recibida anteriormente.")
            raise ApplicationHandlerStop
        if len(files) < int(order["quantity"]):
            await update.effective_message.reply_text(
                f"✅ Etiqueta recibida: <b>{len(files)} de {order['quantity']}</b>.\n"
                f"Faltan <b>{int(order['quantity']) - len(files)}</b>.",
                parse_mode=ParseMode.HTML,
            )
        else:
            await db.set_status(order["order_id"], "awaiting_payment_method")
            context.user_data.pop("awaiting", None)
            await update.effective_message.reply_text(
                f"✅ Recibimos las <b>{order['quantity']}</b> etiquetas.\n\n"
                f"Total: <b>{money(order['total'])}</b>\nSelecciona cómo deseas pagar:",
                reply_markup=payment_keyboard(order["order_id"]),
                parse_mode=ParseMode.HTML,
            )
        raise ApplicationHandlerStop

    if order.get("status") == "awaiting_receipt":
        updated = await db.update_order(
            order["order_id"],
            receipt_file_id=file_data["file_id"],
            receipt_file_type=file_data["file_type"],
            status="pending_payment_review",
        )
        history = list(updated.get("status_history") or [])
        history.append({"status": "pending_payment_review", "created_at": now_iso()})
        updated = await db.update_order(order["order_id"], status_history=history)
        await notify_admin_new_order(context, updated)
        context.user_data.clear()
        await update.effective_message.reply_text(
            f"✅ <b>Solicitud recibida</b>\n\n"
            f"Número: <b>{esc(updated['order_id'])}</b>\n"
            f"Etiquetas: <b>{updated['quantity']}</b>\n"
            f"Total: <b>{money(updated['total'])}</b>\n"
            f"Estado: <b>{STATUS_LABELS['pending_payment_review']}</b>\n\n"
            "Puedes revisar el estado desde el menú principal.",
            reply_markup=main_keyboard(is_admin(update)),
            parse_mode=ParseMode.HTML,
        )
        raise ApplicationHandlerStop

    await update.effective_message.reply_text("No estoy esperando archivos en este momento.")
    raise ApplicationHandlerStop


async def send_stored_file(context: ContextTypes.DEFAULT_TYPE, chat_id: int, file_row: dict, caption: str | None = None):
    if file_row.get("file_type") == "photo":
        await context.bot.send_photo(chat_id, file_row["file_id"], caption=caption)
    else:
        await context.bot.send_document(chat_id, file_row["file_id"], caption=caption)


async def notify_admin_new_order(context: ContextTypes.DEFAULT_TYPE, order: dict) -> None:
    files = await db.get_label_files(order["order_id"])
    await context.bot.send_message(
        ADMIN_CHAT_ID,
        f"🏷 <b>NUEVA SOLICITUD {esc(order['order_id'])}</b>\n\n"
        f"Cliente: {esc(order.get('customer_name'))}\n"
        f"Contacto: {esc(order.get('contact'))}\n"
        f"Usuario: @{esc(order.get('telegram_username') or 'sin_usuario')}\n"
        f"Etiquetas: <b>{order['quantity']}</b>\n"
        f"Total: <b>{money(order['total'])}</b>\n"
        f"Pago: {esc(PAYMENT_LABELS.get(order.get('payment_method'), order.get('payment_method')))}",
        parse_mode=ParseMode.HTML,
    )
    for index, file_row in enumerate(files, start=1):
        try:
            await send_stored_file(
                context,
                ADMIN_CHAT_ID,
                file_row,
                caption=f"Etiqueta {index}/{len(files)} · {order['order_id']}",
            )
        except TelegramError:
            logger.exception("No se pudo reenviar una etiqueta")
    receipt = {
        "file_id": order["receipt_file_id"],
        "file_type": order.get("receipt_file_type") or "photo",
    }
    caption = (
        f"🧾 <b>COMPROBANTE {esc(order['order_id'])}</b>\n\n"
        f"Total esperado: <b>{money(order['total'])}</b>\n"
        "Verifica el dinero directamente en tu cuenta antes de aprobar."
    )
    keyboard = admin_status_keyboard(order["order_id"], order["status"])
    if receipt["file_type"] == "photo":
        await context.bot.send_photo(
            ADMIN_CHAT_ID,
            receipt["file_id"],
            caption=caption,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
    else:
        await context.bot.send_document(
            ADMIN_CHAT_ID,
            receipt["file_id"],
            caption=caption,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )


async def my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()
        message = query.message
    else:
        message = update.effective_message
    orders = await db.get_user_orders(update.effective_user.id, limit=20)
    orders = [order for order in orders if order.get("status") != "draft_uploading"]
    if not orders:
        await message.reply_text("Todavía no tienes solicitudes registradas.", reply_markup=main_keyboard(is_admin(update)))
        return
    rows = []
    for order in orders[:15]:
        status = STATUS_LABELS.get(order.get("status"), order.get("status"))
        rows.append([InlineKeyboardButton(
            f"{order['order_id']} · {status}"[:60],
            callback_data=f"view:{order['order_id']}",
        )])
    rows.append([InlineKeyboardButton("🏠 Menú principal", callback_data="home")])
    await message.reply_text(
        "📍 <b>Selecciona una solicitud:</b>",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.HTML,
    )


async def view_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    order_id = query.data.split(":", 1)[1]
    order = await db.get_order(order_id)
    if not order or (not is_admin(update) and order.get("telegram_user_id") != query.from_user.id):
        await query.message.reply_text("No encontré esa solicitud.")
        return
    status = STATUS_LABELS.get(order.get("status"), order.get("status"))
    text = (
        f"🏷 <b>Solicitud {esc(order_id)}</b>\n\n"
        f"Etiquetas: <b>{order['quantity']}</b>\n"
        f"Precio por etiqueta: <b>{money(PRICE_PER_LABEL)}</b>\n"
        f"Total: <b>{money(order['total'])}</b>\n"
        f"Método de pago: {esc(PAYMENT_LABELS.get(order.get('payment_method'), order.get('payment_method') or 'Pendiente'))}\n"
        f"Estado actual: <b>{status}</b>"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Actualizar", callback_data=f"view:{order_id}")],
        [InlineKeyboardButton("⬅️ Mis solicitudes", callback_data="my_orders")],
    ])
    await query.message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


async def order_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.args:
        order_id = context.args[0].upper()
        order = await db.get_order(order_id)
        if not order or order.get("telegram_user_id") != update.effective_user.id:
            await update.effective_message.reply_text("No encontré esa solicitud en tu cuenta.")
            return
        status = STATUS_LABELS.get(order.get("status"), order.get("status"))
        await update.effective_message.reply_text(
            f"🏷 <b>{esc(order_id)}</b>\nEstado: <b>{status}</b>\nTotal: <b>{money(order['total'])}</b>",
            parse_mode=ParseMode.HTML,
        )
    else:
        await my_orders(update, context)


async def admin_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()
        message = query.message
    else:
        message = update.effective_message
    if not is_admin(update):
        await message.reply_text("No tienes acceso al panel administrativo.")
        return
    await message.reply_text(
        "🛠 <b>Panel administrativo</b>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🏷 Solicitudes", callback_data="admin_orders")],
            [InlineKeyboardButton("📊 Estadísticas", callback_data="admin_stats")],
            [InlineKeyboardButton("🏠 Menú principal", callback_data="home")],
        ]),
        parse_mode=ParseMode.HTML,
    )


async def admin_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_admin(update):
        await query.answer("No autorizado.", show_alert=True)
        return
    await query.answer()
    orders = await db.get_all_orders(limit=100)
    orders = [order for order in orders if order.get("status") not in IN_PROGRESS_STATUSES]
    if not orders:
        await query.message.reply_text("No hay solicitudes registradas.")
        return
    rows = []
    for order in orders[:50]:
        status = STATUS_LABELS.get(order.get("status"), order.get("status"))
        label = f"{order['order_id']} · {status} · {order.get('customer_name') or 'Cliente'}"
        rows.append([InlineKeyboardButton(label[:60], callback_data=f"admin_view:{order['order_id']}")])
    rows.append([InlineKeyboardButton("⬅️ Panel", callback_data="admin_home")])
    await query.message.reply_text(
        f"🏷 <b>Solicitudes registradas: {len(orders)}</b>",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.HTML,
    )


async def admin_view_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_admin(update):
        await query.answer("No autorizado.", show_alert=True)
        return
    await query.answer()
    order_id = query.data.split(":", 1)[1]
    order = await db.get_order(order_id)
    if not order:
        await query.message.reply_text("Solicitud no encontrada.")
        return
    files = await db.get_label_files(order_id)
    status = STATUS_LABELS.get(order.get("status"), order.get("status"))
    await query.message.reply_text(
        f"🏷 <b>{esc(order_id)}</b>\n\n"
        f"Cliente: {esc(order.get('customer_name'))}\n"
        f"Contacto: {esc(order.get('contact'))}\n"
        f"Etiquetas recibidas: <b>{len(files)} de {order['quantity']}</b>\n"
        f"Pago: {esc(PAYMENT_LABELS.get(order.get('payment_method'), order.get('payment_method') or 'Pendiente'))}\n"
        f"Total: <b>{money(order['total'])}</b>\n"
        f"Estado: <b>{status}</b>",
        reply_markup=admin_status_keyboard(order_id, order["status"]),
        parse_mode=ParseMode.HTML,
    )


async def show_files(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_admin(update):
        await query.answer("No autorizado.", show_alert=True)
        return
    await query.answer()
    order_id = query.data.split(":", 1)[1]
    files = await db.get_label_files(order_id)
    if not files:
        await query.message.reply_text("No hay etiquetas guardadas para esa solicitud.")
        return
    for index, file_row in enumerate(files, start=1):
        await send_stored_file(
            context,
            ADMIN_CHAT_ID,
            file_row,
            caption=f"Etiqueta {index}/{len(files)} · {order_id}",
        )


async def set_order_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_admin(update):
        await query.answer("No autorizado.", show_alert=True)
        return
    _, order_id, status = query.data.split(":", 2)
    if status not in STATUS_LABELS:
        await query.answer("Estado no válido.", show_alert=True)
        return
    order, changed = await db.set_status(order_id, status)
    if not order:
        await query.answer("Solicitud no encontrada.", show_alert=True)
        return
    if not changed:
        await query.answer("La solicitud ya tiene ese estado.", show_alert=True)
        return
    await query.answer("Estado actualizado.")
    await notify_customer_status(context, order)
    status_label = STATUS_LABELS[status]
    text = (
        f"🏷 <b>{esc(order_id)}</b>\n\n"
        f"Cliente: {esc(order.get('customer_name'))}\n"
        f"Etiquetas: <b>{order['quantity']}</b>\n"
        f"Total: <b>{money(order['total'])}</b>\n"
        f"Estado: <b>{status_label}</b>"
    )
    try:
        if query.message.photo or query.message.document:
            await query.edit_message_caption(
                caption=text,
                reply_markup=admin_status_keyboard(order_id, status),
                parse_mode=ParseMode.HTML,
            )
        else:
            await query.edit_message_text(
                text=text,
                reply_markup=admin_status_keyboard(order_id, status),
                parse_mode=ParseMode.HTML,
            )
    except TelegramError:
        await query.message.reply_text(
            text,
            reply_markup=admin_status_keyboard(order_id, status),
            parse_mode=ParseMode.HTML,
        )


async def notify_customer_status(context: ContextTypes.DEFAULT_TYPE, order: dict) -> None:
    try:
        await context.bot.send_message(
            order["telegram_user_id"],
            f"🏷 <b>Actualización de {esc(order['order_id'])}</b>\n\n"
            f"Estado: <b>{STATUS_LABELS.get(order['status'], order['status'])}</b>",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📍 Ver solicitud", callback_data=f"view:{order['order_id']}")
            ]]),
            parse_mode=ParseMode.HTML,
        )
    except TelegramError:
        logger.exception("No se pudo notificar al cliente")


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_admin(update):
        await query.answer("No autorizado.", show_alert=True)
        return
    await query.answer()
    orders = await db.get_all_orders(limit=1000)
    counts = Counter(order.get("status") for order in orders)
    labels_by_status = defaultdict(int)
    money_by_status = defaultdict(float)
    payment_counts = Counter()
    payment_money = defaultdict(float)
    customers = set()
    for order in orders:
        status = order.get("status") or "sin_estado"
        quantity = int(order.get("quantity") or 0)
        total = float(order.get("total") or 0)
        labels_by_status[status] += quantity
        money_by_status[status] += total
        if order.get("telegram_user_id"):
            customers.add(order["telegram_user_id"])
        method = order.get("payment_method")
        if method:
            payment_counts[method] += 1
            payment_money[method] += total

    active_orders = [order for order in orders if order.get("status") not in {"cancelled", "draft_uploading"}]
    confirmed_statuses = {"approved", "processing", "completed"}
    confirmed_revenue = sum(
        float(order.get("total") or 0)
        for order in orders
        if order.get("status") in confirmed_statuses
    )
    completed_revenue = sum(
        float(order.get("total") or 0)
        for order in orders
        if order.get("status") == "completed"
    )

    lines = [
        "📊 <b>Estadísticas del bot de etiquetas</b>",
        "",
        f"Solicitudes totales: <b>{len(orders)}</b>",
        f"Solicitudes activas/históricas: <b>{len(active_orders)}</b>",
        f"Clientes únicos: <b>{len(customers)}</b>",
        f"Etiquetas solicitadas: <b>{sum(int(o.get('quantity') or 0) for o in orders)}</b>",
        f"Valor total registrado: <b>{money(sum(float(o.get('total') or 0) for o in orders))}</b>",
        f"Ingresos confirmados: <b>{money(confirmed_revenue)}</b>",
        f"Ingresos completados: <b>{money(completed_revenue)}</b>",
        "",
        "<b>Desglose por estado</b>",
    ]
    for status, label in STATUS_LABELS.items():
        lines.append(
            f"{label}: <b>{counts.get(status, 0)}</b> solicitudes · "
            f"<b>{labels_by_status.get(status, 0)}</b> etiquetas · "
            f"<b>{money(money_by_status.get(status, 0))}</b>"
        )
    unknown_statuses = sorted(set(counts) - set(STATUS_LABELS))
    for status in unknown_statuses:
        lines.append(
            f"{esc(status)}: <b>{counts[status]}</b> solicitudes · "
            f"<b>{labels_by_status[status]}</b> etiquetas · "
            f"<b>{money(money_by_status[status])}</b>"
        )
    lines.extend(["", "<b>Desglose por pago</b>"])
    for method, label in PAYMENT_LABELS.items():
        lines.append(
            f"{label}: <b>{payment_counts.get(method, 0)}</b> solicitudes · "
            f"<b>{money(payment_money.get(method, 0))}</b>"
        )
    await query.message.reply_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Panel", callback_data="admin_home")]]),
        parse_mode=ParseMode.HTML,
    )


async def clear_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    message = update.effective_message
    if not chat or not message or chat.type != "private":
        return
    newest = message.message_id
    ids = list(range(max(1, newest - 99), newest + 1))
    try:
        await context.bot.delete_messages(chat.id, ids)
    except TelegramError:
        for message_id in reversed(ids):
            try:
                await context.bot.delete_message(chat.id, message_id)
            except TelegramError:
                continue
    await start(update, context)


async def home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await start(update, context)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled bot error", exc_info=context.error)


def build_application() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("pedido", order_command))
    app.add_handler(CommandHandler("admin", admin_home))
    app.add_handler(CommandHandler("clear", clear_chat))

    app.add_handler(CallbackQueryHandler(home, pattern=r"^home$"))
    app.add_handler(CallbackQueryHandler(new_order, pattern=r"^new_order$"))
    app.add_handler(CallbackQueryHandler(choose_quantity, pattern=r"^qty:"))
    app.add_handler(CallbackQueryHandler(resume_order, pattern=r"^resume_order$"))
    app.add_handler(CallbackQueryHandler(choose_payment, pattern=r"^pay:"))
    app.add_handler(CallbackQueryHandler(cancel_order, pattern=r"^cancel:"))
    app.add_handler(CallbackQueryHandler(my_orders, pattern=r"^my_orders$"))
    app.add_handler(CallbackQueryHandler(view_order, pattern=r"^view:"))

    app.add_handler(CallbackQueryHandler(admin_home, pattern=r"^admin_home$"))
    app.add_handler(CallbackQueryHandler(admin_orders, pattern=r"^admin_orders$"))
    app.add_handler(CallbackQueryHandler(admin_view_order, pattern=r"^admin_view:"))
    app.add_handler(CallbackQueryHandler(show_files, pattern=r"^files:"))
    app.add_handler(CallbackQueryHandler(set_order_status, pattern=r"^set:"))
    app.add_handler(CallbackQueryHandler(admin_stats, pattern=r"^admin_stats$"))

    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, handle_file))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
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
