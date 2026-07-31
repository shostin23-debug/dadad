# Bot de procesamiento de etiquetas

Bot de Telegram separado del bot de ilumistore.

## Funciones

- Precio fijo de 25 USD por etiqueta.
- Cantidad seleccionable de 1 a 50 etiquetas por pedido.
- Recepción de imágenes y archivos PDF.
- Pago únicamente mediante Binance Pay.
- Comprobante de pago obligatorio.
- Estados: pago en revisión, aprobado, procesando, completado y rechazado.
- Notificaciones automáticas cuando cambia el estado.
- Consulta de estado desde el menú o con `/pedido`.
- Tickets de ayuda con conversación entre cliente y administrador.
- Panel administrativo de pedidos, tickets y estadísticas.
- Comando `/clear` para limpiar mensajes recientes.
- Datos persistentes en Supabase para que no se borren durante despliegues.

## Preparación de Supabase

1. Crear un proyecto de Supabase.
2. Abrir SQL Editor.
3. Ejecutar todo el archivo `schema.sql`.
4. Guardar en Render como secretos:
   - `SUPABASE_URL`
   - `SUPABASE_SECRET_KEY`

El bot también acepta temporalmente `SUPABASE_SERVICE_KEY` para proyectos antiguos, pero se recomienda la clave moderna `sb_secret_...`.

Nunca guardar una clave secreta de Supabase en GitHub ni enviarla por chat.

## Variables de Render

- `BOT_TOKEN`: token privado del nuevo bot de BotFather.
- `ADMIN_CHAT_ID`: ID de Telegram del administrador.
- `BINANCE_PAY_ID`: identificador de Binance Pay.
- `LABEL_PRICE`: precio por etiqueta; actualmente 25.
- `MAX_LABELS`: máximo permitido por pedido; actualmente 50.
- `SUPABASE_URL`: URL del proyecto de Supabase.
- `SUPABASE_SECRET_KEY`: clave secreta del servidor de Supabase.

Para crear un segundo servicio en Render usando este mismo repositorio, establece **Root Directory** en `labels_bot`.
