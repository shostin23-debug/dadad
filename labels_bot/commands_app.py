import hashlib
import os

import bot as base
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
    "<code>/pedido</code> — Ver tus pedidos de etiquetas.\n"
    "<code>/pedido LB-XXXXXXXX</code> — Consultar un pedido específico.\n"
    "<code>/ticket</code> — Abrir la sección de ayuda y preguntas.\n"
    "<code>/clear</code> — Limpiar hasta 100 mensajes recientes del chat.\n"
    "<code>/comandos</code> — Mostrar esta lista.\n"
    "<code>/ayuda</code> — Mostrar esta lista.\n\n"
    "También puedes usar todos los botones del menú sin escribir comandos."
)


def patched_main_menu(user=None) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🏷 Procesar etiquetas", callback_data="new_order")],
        [InlineKeyboardButton("📍 Estado de mis etiquetas", callback_data="my_orders")],
        [InlineKeyboardButton("🎫 Ayuda y preguntas", callback_data="help_menu")],
        [InlineKeyboardButton("📚 Comandos y ayuda", callback_data="commands_help")],
    ]
    if base.is_admin(user):
        rows.append([InlineKeyboardButton("🛠 Panel administrativo", callback_data="admin_panel")])
    return InlineKeyboardMarkup(rows)


async def commands_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query:
        await query.answer()
        message = query.message
    else:
        message = update.effective_message

    rows = [
        [InlineKeyboardButton("🏷 Procesar etiquetas", callback_data="new_order")],
        [InlineKeyboardButton("📍 Ver mis pedidos", callback_data="my_orders")],
        [InlineKeyboardButton("🎫 Abrir ayuda", callback_data="help_menu")],
        [InlineKeyboardButton("🏠 Menú principal", callback_data="home")],
    ]
    await message.reply_text(
        COMMANDS_TEXT,
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.HTML,
    )
    if query:
        raise ApplicationHandlerStop


async def configure_commands(application) -> None:
    commands = [
        BotCommand("start", "Abrir el menú principal"),
        BotCommand("pedido", "Ver el estado de tus etiquetas"),
        BotCommand("ticket", "Abrir ayuda y preguntas"),
        BotCommand("clear", "Limpiar mensajes recientes"),
        BotCommand("comandos", "Ver todos los comandos"),
        BotCommand("ayuda", "Ver todos los comandos"),
    ]
    await application.bot.set_my_commands(commands)


def build_application():
    base.main_menu = patched_main_menu
    app = base.build_application()
    app.add_handler(CommandHandler("comandos", commands_help), group=-1)
    app.add_handler(CommandHandler("ayuda", commands_help), group=-1)
    app.add_handler(CommandHandler("help", commands_help), group=-1)
    app.add_handler(
        CallbackQueryHandler(commands_help, pattern=r"^commands_help$"),
        group=-1,
    )
    app.post_init = configure_commands
    return app


def run() -> None:
    app = build_application()
    hostname = os.getenv("RENDER_EXTERNAL_HOSTNAME")
    if hostname:
        webhook_path = hashlib.sha256(base.BOT_TOKEN.encode("utf-8")).hexdigest()[:32]
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
