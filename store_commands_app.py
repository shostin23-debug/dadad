import hashlib
import os

import fix_app as base
import store_app as store
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)


COMMANDS_TEXT = (
    "📚 <b>Comandos disponibles</b>\n\n"
    "<code>/start</code> — Abrir el menú principal.\n"
    "<code>/catalogo</code> — Ver todas las gorras.\n"
    "<code>/carrito</code> — Ver tu carrito de compras.\n"
    "<code>/pedido</code> — Consultar el estado de tus pedidos.\n"
    "<code>/pedido IL-XXXXXXXX</code> — Consultar un pedido específico.\n"
    "<code>/ticket</code> — Abrir ayuda y soporte.\n"
    "<code>/clear</code> — Limpiar los mensajes recientes del chat.\n"
    "<code>/cancelar</code> — Cancelar la acción actual y volver al menú.\n"
    "<code>/comandos</code> — Mostrar esta lista.\n"
    "<code>/ayuda</code> — Mostrar esta lista.\n\n"
    "También puedes usar los botones del menú sin escribir comandos."
)


def patched_start_keyboard(user, cart: list) -> InlineKeyboardMarkup:
    units = store.cart_units(cart)
    rows = [
        [InlineKeyboardButton("🧢 Ver catálogo", callback_data="catalog")],
        [InlineKeyboardButton(f"🛒 Mi carrito ({units})", callback_data="cart_view")],
        [InlineKeyboardButton("📍 Estado de mi pedido", callback_data="order_status_menu")],
        [InlineKeyboardButton("🎫 Abrir ticket de soporte", callback_data="ticket_menu")],
        [InlineKeyboardButton("📚 Comandos y ayuda", callback_data="store_commands_help")],
    ]
    if store.admin_ui.is_admin(user):
        rows.append([
            InlineKeyboardButton("🛠 Panel de administración", callback_data="panel_admin")
        ])
    return InlineKeyboardMarkup(rows)


async def commands_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()
        message = query.message
    else:
        message = update.effective_message

    await message.reply_text(
        COMMANDS_TEXT,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🧢 Ver catálogo", callback_data="catalog")],
            [InlineKeyboardButton("🛒 Ver carrito", callback_data="cart_view")],
            [InlineKeyboardButton("📍 Estado de mi pedido", callback_data="order_status_menu")],
            [InlineKeyboardButton("🎫 Abrir soporte", callback_data="ticket_menu")],
            [InlineKeyboardButton("🏠 Menú principal", callback_data="home")],
        ]),
        parse_mode=ParseMode.HTML,
    )
    if query:
        raise ApplicationHandlerStop


async def catalog_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [
            InlineKeyboardButton(
                f"{product['short']} — {store.core.money(product['price'])}",
                callback_data=f"product:{product_id}",
            )
        ]
        for product_id, product in store.core.PRODUCTS.items()
    ]
    keyboard.append([InlineKeyboardButton("🏠 Menú principal", callback_data="home")])
    await update.effective_message.reply_text(
        "🧢 <b>Catálogo</b>\n\nSelecciona una gorra:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML,
    )


async def ticket_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "🎫 Selecciona el motivo del ticket:",
        reply_markup=store.core.support_menu(),
    )


async def configure_commands(application) -> None:
    await application.bot.set_my_commands([
        BotCommand("start", "Abrir el menú principal"),
        BotCommand("catalogo", "Ver el catálogo de gorras"),
        BotCommand("carrito", "Ver tu carrito de compras"),
        BotCommand("pedido", "Consultar el estado de tus pedidos"),
        BotCommand("ticket", "Abrir ayuda y soporte"),
        BotCommand("clear", "Limpiar mensajes recientes"),
        BotCommand("cancelar", "Cancelar y volver al menú"),
        BotCommand("comandos", "Ver todos los comandos"),
        BotCommand("ayuda", "Ver todos los comandos"),
    ])


def build_application():
    store.start_keyboard = patched_start_keyboard
    app = base.build_application()

    app.add_handler(CommandHandler("catalogo", catalog_command), group=-4)
    app.add_handler(CommandHandler("ticket", ticket_command), group=-4)
    app.add_handler(CommandHandler("comandos", commands_help), group=-4)
    app.add_handler(CommandHandler("ayuda", commands_help), group=-4)
    app.add_handler(CommandHandler("help", commands_help), group=-4)
    app.add_handler(
        CallbackQueryHandler(commands_help, pattern=r"^store_commands_help$"),
        group=-4,
    )
    app.post_init = configure_commands
    return app


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
