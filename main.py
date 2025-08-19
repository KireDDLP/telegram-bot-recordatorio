from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
import sqlite3
import os
import datetime
import requests
import asyncio

# ------------ CONFIG --------------
TOKEN = "8251636418:AAHmr0pZ0W4M2JiSjit7Kp1gZ-5AIkI4Yoc"
WEBHOOK_URL = "https://hook.us2.make.com/381ufyzdly9s9fe26mjj7ks8hqfn0pf3"

# Ruta base de documentos
DOCUMENTOS_DIR = os.path.join(os.path.dirname(__file__), "documentos")

# ------------ BASE DE DATOS --------------
# check_same_thread=False porque el worker y el bot usan la misma conexión
conn = sqlite3.connect("usuarios.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS usuarios (
    user_id INTEGER PRIMARY KEY,
    nombre TEXT,
    foto_id TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS recordatorios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    titulo TEXT,
    fecha_inicio TEXT,
    fecha_fin TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS notificaciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recordatorio_id INTEGER,
    user_id INTEGER,
    titulo TEXT,
    trigger_ts TEXT,
    tipo TEXT,
    sent INTEGER DEFAULT 0
)
""")

conn.commit()

usuarios_pendientes = set()

# ------------ UTIL / DB --------------
def guardar_usuario(user_id, nombre, foto_id):
    cursor.execute("""
        INSERT OR REPLACE INTO usuarios (user_id, nombre, foto_id)
        VALUES (?, ?, ?)
    """, (user_id, nombre, foto_id))
    conn.commit()

def obtener_usuario(user_id):
    cursor.execute("SELECT nombre, foto_id FROM usuarios WHERE user_id = ?", (user_id,))
    fila = cursor.fetchone()
    if fila:
        return {"nombre": fila[0], "foto_id": fila[1]}
    return None

def guardar_recordatorio(user_id, titulo, fecha_inicio, fecha_fin=None):
    cursor.execute("""
        INSERT INTO recordatorios (user_id, titulo, fecha_inicio, fecha_fin)
        VALUES (?, ?, ?, ?)
    """, (user_id, titulo, fecha_inicio, fecha_fin))
    conn.commit()
    record_id = cursor.lastrowid
    crear_notificaciones_para_recordatorio(record_id, user_id, titulo, fecha_inicio)
    return record_id

def obtener_recordatorios(user_id):
    cursor.execute("""
        SELECT id, titulo, fecha_inicio, fecha_fin FROM recordatorios WHERE user_id = ?
        ORDER BY fecha_inicio
    """, (user_id,))
    return cursor.fetchall()

# ------------ CREACIÓN DE TRIGGERS --------------
def crear_notificaciones_para_recordatorio(record_id, user_id, titulo, fecha_inicio_str):
    try:
        y, m, d = [int(x) for x in fecha_inicio_str.split("-")]
    except Exception:
        return

    # Hora por defecto del evento: 09:00
    event_dt = datetime.datetime(y, m, d, 9, 0, 0)
    now = datetime.datetime.now()

    triggers = [
        ("confirm", now + datetime.timedelta(minutes=1)),
        ("7d", event_dt - datetime.timedelta(days=7)),
        ("1d", event_dt - datetime.timedelta(days=1)),
        ("1h", event_dt - datetime.timedelta(hours=1)),
        ("start", event_dt)
    ]

    for tipo, ts in triggers:
        if ts <= now:
            continue
        cursor.execute("""
            SELECT id FROM notificaciones WHERE recordatorio_id = ? AND tipo = ?
        """, (record_id, tipo))
        if cursor.fetchone():
            continue
        cursor.execute("""
            INSERT INTO notificaciones (recordatorio_id, user_id, titulo, trigger_ts, tipo, sent)
            VALUES (?, ?, ?, ?, ?, 0)
        """, (record_id, user_id, titulo, ts.isoformat(), tipo))
    conn.commit()

# ------------ HELPERS BOTONES --------------
def botones_meses(prefix="mes_"):
    filas = [
        ["Enero", "Febrero", "Marzo"],
        ["Abril", "Mayo", "Junio"],
        ["Julio", "Agosto", "Septiembre"],
        ["Octubre", "Noviembre", "Diciembre"]
    ]
    return [[InlineKeyboardButton(m, callback_data=f"{prefix}{m}") for m in fila] for fila in filas]

def botones_dias(prefix="dia_"):
    return [[InlineKeyboardButton(str(d), callback_data=f"{prefix}{d}") for d in range(i, min(i+5, 32))] for i in range(1, 32, 5)]

def botones_anios(actual_cb="anio_actual", siguiente_cb="anio_siguiente"):
    return [
        [InlineKeyboardButton("Este año", callback_data=actual_cb)],
        [InlineKeyboardButton("Siguiente año", callback_data=siguiente_cb)],
        [InlineKeyboardButton("Volver 🔙", callback_data="volver_menu")]
    ]

# ------------ MENÚS / DOCUMENTOS / FAQ --------------
async def mostrar_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if hasattr(update, "message") and update.message:
        user_id = update.message.from_user.id
    else:
        user_id = update.callback_query.from_user.id

    if not obtener_usuario(user_id):
        if hasattr(update, "message") and update.message:
            await update.message.reply_text("Primero regístrate usando /start.")
        else:
            await update.callback_query.message.reply_text("Primero regístrate usando /start.")
        return

    botones = [
        [InlineKeyboardButton("Añadir recordatorio ⏰", callback_data="añadir_recordatorio")],
        [InlineKeyboardButton("Ver recordatorios actuales 📋", callback_data="ver_recordatorios")],
        [InlineKeyboardButton("Descargar documentos 📄", callback_data="descargar_documentos")],
        [InlineKeyboardButton("Preguntas frecuentes ❓", callback_data="preguntas_frecuentes")]
    ]
    teclado = InlineKeyboardMarkup(botones)
    if hasattr(update, "message") and update.message:
        await update.message.reply_text("Selecciona una opción del menú:", reply_markup=teclado)
    else:
        await update.callback_query.message.reply_text("Selecciona una opción del menú:", reply_markup=teclado)

async def mostrar_submenu_documentos(query, context):
    try:
        archivos = [f for f in os.listdir(DOCUMENTOS_DIR) if f.lower().endswith(".pdf")]
    except FileNotFoundError:
        archivos = []

    if not archivos:
        await query.message.reply_text("No hay documentos disponibles en este momento.")
        return

    botones = [[InlineKeyboardButton(a, callback_data=f"doc_{a}")] for a in archivos]
    botones.append([InlineKeyboardButton("Volver al menú principal 🔙", callback_data="volver_menu")])
    teclado = InlineKeyboardMarkup(botones)
    await query.message.reply_text("Selecciona un documento para descargar:", reply_markup=teclado)

async def mostrar_submenu_preguntas(query, context):
    preguntas = ["¿Cómo añadir un recordatorio?", "¿Cómo descargar documentos?", "¿Cómo cambiar mi foto?"]
    botones = [[InlineKeyboardButton(p, callback_data=f"faq_{i}")] for i, p in enumerate(preguntas)]
    botones.append([InlineKeyboardButton("Volver al menú principal 🔙", callback_data="volver_menu")])
    teclado = InlineKeyboardMarkup(botones)
    await query.message.reply_text("Selecciona una pregunta:", reply_markup=teclado)

# ------------ FLUJO RECORDATORIOS --------------
async def iniciar_recordatorio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    botones = [
        [InlineKeyboardButton("Fecha única 📅", callback_data="tipo_fecha_unica")],
        [InlineKeyboardButton("Rango de fechas 📆", callback_data="tipo_rango_fechas")],
        [InlineKeyboardButton("Volver al menú 🔙", callback_data="volver_menu")]
    ]
    teclado = InlineKeyboardMarkup(botones)
    await query.message.reply_text("Selecciona el tipo de recordatorio:", reply_markup=teclado)

async def elegir_mes_inicio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    context.user_data["tipo_recordatorio"] = "fecha_unica" if query.data == "tipo_fecha_unica" else "rango_fechas"
    teclado = InlineKeyboardMarkup(botones_meses(prefix="mes_") + [[InlineKeyboardButton("Volver 🔙", callback_data="volver_menu")]])
    await query.message.reply_text("Selecciona el mes de INICIO:", reply_markup=teclado)

async def elegir_dia_inicio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    mes = query.data.replace("mes_", "")
    context.user_data["mes_inicio"] = mes
    teclado = InlineKeyboardMarkup(botones_dias(prefix="dia_") + [[InlineKeyboardButton("Volver 🔙", callback_data="volver_menu")]])
    await query.message.reply_text(f"Selecciona el DÍA de INICIO para {mes}:", reply_markup=teclado)

async def elegir_anio_inicio_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    dia = int(query.data.replace("dia_", ""))
    context.user_data["dia_inicio"] = dia
    teclado = InlineKeyboardMarkup(botones_anios(actual_cb="anio_inicio_actual", siguiente_cb="anio_inicio_siguiente"))
    await query.message.reply_text("Selecciona el AÑO de INICIO:", reply_markup=teclado)

async def elegir_anio_inicio_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    hoy = datetime.date.today()
    año_inicio = hoy.year if data == "anio_inicio_actual" else hoy.year + 1
    context.user_data["año_inicio"] = año_inicio

    if context.user_data.get("tipo_recordatorio") == "fecha_unica":
        context.user_data["año_fin"] = año_inicio
        context.user_data["mes_fin"] = context.user_data["mes_inicio"]
        context.user_data["dia_fin"] = context.user_data["dia_inicio"]
        context.user_data["esperando_titulo"] = True
        await query.message.reply_text("Escribe el título o descripción del recordatorio:")
        return

    teclado = InlineKeyboardMarkup(botones_meses(prefix="mes_fin_") + [[InlineKeyboardButton("Volver 🔙", callback_data="volver_menu")]])
    await query.message.reply_text("Selecciona el MES de FIN:", reply_markup=teclado)

async def elegir_dia_fin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    mes_fin = query.data.replace("mes_fin_", "")
    context.user_data["mes_fin"] = mes_fin
    teclado = InlineKeyboardMarkup(botones_dias(prefix="dia_fin_") + [[InlineKeyboardButton("Volver 🔙", callback_data="volver_menu")]])
    await query.message.reply_text(f"Selecciona el DÍA de FIN para {mes_fin}:", reply_markup=teclado)

async def elegir_anio_fin_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    dia_fin = int(query.data.replace("dia_fin_", ""))
    context.user_data["dia_fin"] = dia_fin
    teclado = InlineKeyboardMarkup(botones_anios(actual_cb="anio_fin_actual", siguiente_cb="anio_fin_siguiente"))
    await query.message.reply_text("Selecciona el AÑO de FIN:", reply_markup=teclado)

async def elegir_anio_fin_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    hoy = datetime.date.today()
    año_fin = hoy.year if data == "anio_fin_actual" else hoy.year + 1
    context.user_data["año_fin"] = año_fin
    context.user_data["esperando_titulo"] = True
    await query.message.reply_text("Escribe el título o descripción del recordatorio:")

# ----------- recepción título -----------
async def recibir_titulo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("esperando_titulo"):
        return
    context.user_data["esperando_titulo"] = False
    user_id = update.message.from_user.id
    titulo = update.message.text.strip()

    meses_dict = {
        "Enero":1,"Febrero":2,"Marzo":3,"Abril":4,"Mayo":5,"Junio":6,
        "Julio":7,"Agosto":8,"Septiembre":9,"Octubre":10,"Noviembre":11,"Diciembre":12
    }

    dia_inicio = context.user_data.get("dia_inicio")
    mes_inicio = context.user_data.get("mes_inicio")
    año_inicio = context.user_data.get("año_inicio", datetime.date.today().year)
    fecha_inicio = datetime.date(año_inicio, meses_dict[mes_inicio], dia_inicio)

    if context.user_data.get("tipo_recordatorio") == "rango_fechas":
        dia_fin = context.user_data.get("dia_fin")
        mes_fin = context.user_data.get("mes_fin")
        año_fin = context.user_data.get("año_fin", año_inicio)
        fecha_fin = datetime.date(año_fin, meses_dict[mes_fin], dia_fin)
    else:
        fecha_fin = fecha_inicio

    fecha_inicio_str = fecha_inicio.strftime("%Y-%m-%d")
    fecha_fin_str = fecha_fin.strftime("%Y-%m-%d")

    guardar_recordatorio(user_id, titulo, fecha_inicio_str, fecha_fin_str)

    payload = {
        "user_id": user_id,
        "titulo": titulo,
        "fecha_inicio": fecha_inicio_str,
        "fecha_fin": fecha_fin_str
    }
    try:
        requests.post(WEBHOOK_URL, json=payload, timeout=8)
    except Exception:
        pass

    for k in ["tipo_recordatorio", "mes_inicio", "dia_inicio", "año_inicio", "mes_fin", "dia_fin", "año_fin", "esperando_titulo"]:
        context.user_data.pop(k, None)

    await update.message.reply_text(f"Recordatorio '{titulo}' guardado del {fecha_inicio_str} al {fecha_fin_str} ✅")

# ------------ mostrar recordatorios --------------
async def mostrar_recordatorios_usuario(update_or_query, context: ContextTypes.DEFAULT_TYPE):
    if hasattr(update_or_query, "message") and update_or_query.message:
        user_id = update_or_query.message.from_user.id
        send = update_or_query.message.reply_text
    else:
        user_id = update_or_query.callback_query.from_user.id
        send = update_or_query.callback_query.message.reply_text

    rows = obtener_recordatorios(user_id)
    if not rows:
        await send("No tienes recordatorios guardados.")
        return

    lines = []
    for r in rows:
        rid, titulo, fi, ff = r
        lines.append(f"• {titulo}\n  {fi} → {ff}\n  (id: {rid})")
    texto = "📋 Tus recordatorios:\n\n" + "\n\n".join(lines)
    await send(texto)

# ------------ WORKER (coroutine) --------------
async def reminder_worker(app: Application):
    while True:
        try:
            now = datetime.datetime.now()
            cursor.execute("""
                SELECT id, recordatorio_id, user_id, titulo, trigger_ts, tipo
                FROM notificaciones
                WHERE sent = 0 AND datetime(trigger_ts) <= datetime(?)
            """, (now.isoformat(),))
            rows = cursor.fetchall()
            for nid, record_id, user_id, titulo, trigger_ts, tipo in rows:
                try:
                    ts_dt = datetime.datetime.fromisoformat(trigger_ts)
                    ts_str = ts_dt.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    ts_str = trigger_ts

                if tipo == "confirm":
                    text = (f"✅ Confirmación: '{titulo}'\n"
                            f"Se han programado las notificaciones y te avisaremos antes del evento.\n"
                            f"(Confirmación: {ts_str})")
                else:
                    text = f"⏰ Recordatorio — {titulo}\nFecha: {ts_str}\nTipo: {tipo}"

                try:
                    await app.bot.send_message(chat_id=user_id, text=text)
                except Exception:
                    pass

                cursor.execute("UPDATE notificaciones SET sent = 1 WHERE id = ?", (nid,))
            conn.commit()
        except Exception:
            pass
        await asyncio.sleep(60)

# ------------ CALLBACK UNIFICADO --------------
async def manejar_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data in ["añadir_recordatorio", "ver_recordatorios", "descargar_documentos",
                "preguntas_frecuentes", "volver_menu"] or data.startswith("doc_") or data.startswith("faq_"):
        if data == "añadir_recordatorio":
            await iniciar_recordatorio(update, context)
        elif data == "ver_recordatorios":
            await mostrar_recordatorios_usuario(update, context)
        elif data == "descargar_documentos":
            await mostrar_submenu_documentos(query, context)
        elif data.startswith("doc_"):
            nombre_doc = data.replace("doc_", "")
            ruta_doc = os.path.join(DOCUMENTOS_DIR, nombre_doc)
            if os.path.exists(ruta_doc):
                with open(ruta_doc, "rb") as archivo:
                    await query.message.reply_document(archivo)
            else:
                await query.message.reply_text(f"No se encontró el documento {nombre_doc}.")
        elif data == "preguntas_frecuentes":
            await mostrar_submenu_preguntas(query, context)
        elif data.startswith("faq_"):
            idx = int(data.replace("faq_", ""))
            respuestas = [
                "Para añadir un recordatorio, usa la opción 'Añadir recordatorio' y sigue las instrucciones.",
                "Para descargar documentos, selecciona 'Descargar documentos' y elige el archivo que necesites.",
                "Para cambiar tu foto, envía una nueva foto y tu perfil se actualizará automáticamente."
            ]
            await query.message.reply_text(respuestas[idx])
        elif data == "volver_menu":
            await mostrar_menu(update, context)
        return

    if data.startswith("tipo_"):
        await elegir_mes_inicio(update, context)
        return
    if data.startswith("mes_") and not data.startswith("mes_fin_"):
        await elegir_dia_inicio(update, context)
        return
    if data.startswith("dia_") and not data.startswith("dia_fin_"):
        await elegir_anio_inicio_prompt(update, context)
        return
    if data in ("anio_inicio_actual", "anio_inicio_siguiente"):
        await elegir_anio_inicio_selected(update, context)
        return
    if data.startswith("mes_fin_"):
        await elegir_dia_fin(update, context)
        return
    if data.startswith("dia_fin_"):
        await elegir_anio_fin_prompt(update, context)
        return
    if data in ("anio_fin_actual", "anio_fin_siguiente"):
        await elegir_anio_fin_selected(update, context)
        return

# ------------ START / FOTO --------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    datos = obtener_usuario(user_id)
    if datos:
        await update.message.reply_photo(photo=datos["foto_id"], caption=f"¡Hola, {datos['nombre']}! 😊")
        await mostrar_menu(update, context)
    else:
        usuarios_pendientes.add(user_id)
        await update.message.reply_text("¡Bienvenido! Primero envíame la foto que quieres usar como tu perfil para el bot.")

async def recibir_foto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if update.message.photo:
        foto_id = update.message.photo[-1].file_id
        nombre = f"{update.message.from_user.first_name} {update.message.from_user.last_name or ''}".strip()
        guardar_usuario(user_id, nombre, foto_id)
        usuarios_pendientes.discard(user_id)
        await update.message.reply_text(f"¡Registro completado, {nombre}! 👋")
        await update.message.reply_photo(photo=foto_id, caption=f"¡Hola {nombre}! 😊")
        await mostrar_menu(update, context)
    else:
        await update.message.reply_text("Por favor, envíame una foto, no un mensaje de texto.")

# ------------ STARTUP / MAIN --------------
async def on_startup(app: Application):
    # Lanzar worker en background una vez la app esté corriendo
    try:
        if hasattr(app, "create_task"):
            app.create_task(reminder_worker(app))
        else:
            asyncio.get_event_loop().create_task(reminder_worker(app))
    except Exception:
        # no queremos que fallos aquí eviten que el bot arranque
        pass

def main():
    # construimos la app registrando on_startup con post_init
    try:
        app = Application.builder().token(TOKEN).post_init(on_startup).build()
    except Exception:
        # Si tu versión de PTB no soporta post_init, intenta construir sin él
        app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, recibir_foto))
    app.add_handler(CommandHandler("menu", mostrar_menu))
    app.add_handler(CallbackQueryHandler(manejar_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_titulo))

    # arrancar polling (blocking)
    app.run_polling()

if __name__ == "__main__":
    main()
if __name__ == "__main__":
    main()
