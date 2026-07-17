"""
Acompañante Mayor — Bot de Telegram
Asistente 100% por voz para adultos mayores con dificultad visual
"""
import os, re, asyncio, sqlite3, tempfile, logging
from datetime import datetime, date, timedelta
from urllib.parse import quote

import requests
import nest_asyncio
from gtts import gTTS
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

nest_asyncio.apply()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════
# CONFIGURACIÓN — variables de entorno (se definen en Railway)
# ══════════════════════════════════════════════════════════════
CONFIG = {
    "telegram_token":   os.environ.get("TELEGRAM_TOKEN", ""),
    "nombre":           os.environ.get("NOMBRE_USUARIO", "Abuelo"),
    "familiar_chat_id": os.environ.get("FAMILIAR_CHAT_ID", ""),
    "twilio_sid":       os.environ.get("TWILIO_SID", ""),
    "twilio_token":     os.environ.get("TWILIO_TOKEN", ""),
    "twilio_numero":    os.environ.get("TWILIO_NUMERO", ""),
    "telefono_usuario": os.environ.get("TELEFONO_USUARIO", ""),
    "callmebot_phone":  os.environ.get("CALLMEBOT_PHONE", ""),
    "callmebot_key":    os.environ.get("CALLMEBOT_KEY", ""),
}

MODO_LLAMADAS = bool(CONFIG["twilio_sid"] and CONFIG["twilio_token"] and CONFIG["twilio_numero"])
MODO_WHATSAPP = bool(CONFIG["callmebot_phone"] and CONFIG["callmebot_key"])
FAMILIAR_ID   = int(CONFIG["familiar_chat_id"]) if CONFIG["familiar_chat_id"] else None

# ══════════════════════════════════════════════════════════════
# VOZ — Whisper (STT) + gTTS (TTS)
# ══════════════════════════════════════════════════════════════
import whisper
print("Cargando modelo Whisper...")
modelo_whisper = whisper.load_model("tiny")
print("Whisper listo")

def escuchar(ruta_audio: str) -> str:
    if not ruta_audio or not os.path.exists(ruta_audio):
        return ""
    try:
        r = modelo_whisper.transcribe(ruta_audio, language="es", fp16=False)
        texto = r["text"].strip()
        logger.info(f'Transcripcion: "{texto}"')
        return texto
    except Exception as e:
        logger.error(f"Error Whisper: {e}")
        return ""

def hablar(texto: str) -> str:
    try:
        tts = gTTS(text=texto, lang="es", slow=False)
        f = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
        tts.save(f.name)
        return f.name
    except Exception as e:
        logger.error(f"Error gTTS: {e}")
        return None

def saludo_hora() -> str:
    h = datetime.now().hour
    t = datetime.now().strftime("%I:%M %p")
    n = CONFIG["nombre"]
    if h < 12:   return f"Buenos dias {n}. Son las {t}."
    elif h < 18: return f"Buenas tardes {n}. Son las {t}."
    else:        return f"Buenas noches {n}. Son las {t}."

# ══════════════════════════════════════════════════════════════
# BASE DE DATOS — SQLite
# ══════════════════════════════════════════════════════════════
DB = os.environ.get("DB_PATH", "/data/acompanante.db")
os.makedirs(os.path.dirname(DB), exist_ok=True)

def get_db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def iniciar_db():
    con = get_db()
    con.executescript("""
        CREATE TABLE IF NOT EXISTS medicamentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL, dosis TEXT DEFAULT '',
            hora TEXT NOT NULL, activo INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS tomas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            med_id INTEGER, fecha TEXT, hora_prog TEXT,
            tomado INTEGER DEFAULT 0, hora_real TEXT);
        CREATE TABLE IF NOT EXISTS contactos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL, telefono TEXT NOT NULL,
            relacion TEXT DEFAULT '', emergencia INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS agenda (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL, hora TEXT NOT NULL, descripcion TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS salud (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT DEFAULT CURRENT_TIMESTAMP,
            tipo TEXT, valor REAL, unidad TEXT);
    """)
    if con.execute("SELECT COUNT(*) FROM medicamentos").fetchone()[0] == 0:
        con.executemany("INSERT INTO medicamentos (nombre,dosis,hora) VALUES (?,?,?)", [
            ("Aspirina",   "1 pastilla",              "08:00"),
            ("Vitamina D", "1 capsula",               "13:00"),
            ("Metformina", "1 tableta con la comida", "19:00"),
        ])
        print("Datos de ejemplo cargados")
    con.commit()
    con.close()

def get_meds():
    con = get_db()
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM medicamentos WHERE activo=1 ORDER BY hora").fetchall()]
    con.close(); return rows

def add_med(nombre, dosis, hora):
    con = get_db()
    con.execute("INSERT INTO medicamentos (nombre,dosis,hora) VALUES (?,?,?)", (nombre, dosis, hora))
    con.commit(); con.close()

def ya_tomado(med_id, hora_prog):
    con = get_db()
    r = con.execute("SELECT tomado FROM tomas WHERE med_id=? AND fecha=? AND hora_prog=?",
                    (med_id, date.today().isoformat(), hora_prog)).fetchone()
    con.close(); return bool(r and r["tomado"])

def marcar_tomado(med_id, hora_prog):
    con = get_db()
    hoy = date.today().isoformat()
    ahora = datetime.now().strftime("%H:%M")
    ex = con.execute("SELECT id FROM tomas WHERE med_id=? AND fecha=? AND hora_prog=?",
                     (med_id, hoy, hora_prog)).fetchone()
    if ex:
        con.execute("UPDATE tomas SET tomado=1,hora_real=? WHERE id=?", (ahora, ex["id"]))
    else:
        con.execute("INSERT INTO tomas (med_id,fecha,hora_prog,tomado,hora_real) VALUES (?,?,?,1,?)",
                    (med_id, hoy, hora_prog, ahora))
    con.commit(); con.close()

def get_contactos():
    con = get_db()
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM contactos ORDER BY emergencia DESC,nombre").fetchall()]
    con.close(); return rows

def add_contacto(nombre, telefono, relacion="", emergencia=False):
    con = get_db()
    con.execute("INSERT INTO contactos (nombre,telefono,relacion,emergencia) VALUES (?,?,?,?)",
                (nombre, telefono, relacion, 1 if emergencia else 0))
    con.commit(); con.close()

def buscar_contacto(nb):
    cs = get_contactos(); nb = nb.lower().strip()
    for c in cs:
        if c["nombre"].lower() == nb: return c
    for c in cs:
        if nb in c["nombre"].lower() or c["nombre"].lower() in nb: return c
    for p in nb.split():
        if len(p) > 2:
            for c in cs:
                if p in c["nombre"].lower(): return c
    return None

def get_agenda(fecha=None):
    con = get_db(); f = fecha or date.today().isoformat()
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM agenda WHERE fecha=? ORDER BY hora", (f,)).fetchall()]
    con.close(); return rows

def add_agenda(fecha, hora, descripcion):
    con = get_db()
    con.execute("INSERT INTO agenda (fecha,hora,descripcion) VALUES (?,?,?)", (fecha, hora, descripcion))
    con.commit(); con.close()

def add_salud(tipo, valor, unidad=""):
    con = get_db()
    con.execute("INSERT INTO salud (tipo,valor,unidad) VALUES (?,?,?)", (tipo, valor, unidad))
    con.commit(); con.close()

# ══════════════════════════════════════════════════════════════
# NOTIFICACIONES — Twilio + CallMeBot
# ══════════════════════════════════════════════════════════════
twilio_client = None
if MODO_LLAMADAS:
    try:
        from twilio.rest import Client
        twilio_client = Client(CONFIG["twilio_sid"], CONFIG["twilio_token"])
        print("Twilio conectado")
    except Exception as e:
        print(f"Twilio error: {e}"); MODO_LLAMADAS = False

def llamar(telefono: str, mensaje: str) -> str:
    if not MODO_LLAMADAS:
        print(f"[SIMULADO] Llamando a {telefono}: {mensaje}")
        return "Llamada simulada"
    try:
        twiml = (f"<Response>"
                 f"<Say language='es-MX' voice='Polly.Mia'>{mensaje}</Say>"
                 f"<Pause length='2'/>"
                 f"<Say language='es-MX' voice='Polly.Mia'>{mensaje}</Say>"
                 f"</Response>")
        call = twilio_client.calls.create(
            twiml=twiml, to=telefono, from_=CONFIG["twilio_numero"])
        return "Llamada iniciada"
    except Exception as e:
        return f"Error: {e}"

def enviar_whatsapp(telefono: str, mensaje: str) -> str:
    if not MODO_WHATSAPP:
        print(f"[SIMULADO] WhatsApp a {telefono}: {mensaje}")
        return "Simulado"
    try:
        url = (f"https://api.callmebot.com/whatsapp.php"
               f"?phone={telefono}&text={quote(mensaje)}&apikey={CONFIG['callmebot_key']}")
        r = requests.get(url, timeout=10)
        return f"WhatsApp enviado ({r.status_code})"
    except Exception as e:
        return f"Error: {e}"

# ══════════════════════════════════════════════════════════════
# MOTOR DE ALARMAS — APScheduler
# ══════════════════════════════════════════════════════════════
scheduler = BackgroundScheduler(timezone="America/Bogota")
bot_ref = {"app": None}

def disparar_alarma_sync(med_id: int, chat_id: int):
    con = get_db()
    med = con.execute("SELECT * FROM medicamentos WHERE id=?", (med_id,)).fetchone()
    con.close()
    if not med or not med["activo"]: return
    med = dict(med)
    if ya_tomado(med_id, med["hora"]): return
    nombre = CONFIG["nombre"]
    msg_txt = f"Hora de tomar {med['nombre']}\n{med['dosis']}"
    msg_voz = f"{nombre}, es hora de tomar {med['nombre']}. {med['dosis']}. Toca el boton verde para confirmar."
    audio = hablar(msg_voz)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(
        f"Ya tome {med['nombre']}",
        callback_data=f"tomado_{med_id}_{med['hora']}")]])
    logger.info(f"ALARMA: {med['nombre']}")
    app = bot_ref["app"]
    if app:
        async def enviar():
            await app.bot.send_message(chat_id=chat_id, text=f"⏰ {msg_txt}", reply_markup=kb)
            if audio:
                with open(audio, "rb") as f:
                    await app.bot.send_voice(chat_id=chat_id, voice=f)
        asyncio.ensure_future(enviar())
    if CONFIG["telefono_usuario"]:
        llamar(CONFIG["telefono_usuario"], msg_voz)
    scheduler.add_job(
        recordatorio_familiar_sync, "date",
        run_date=datetime.now() + timedelta(minutes=15),
        args=[med_id, med["hora"], med["nombre"], chat_id],
        id=f"rec_{med_id}", replace_existing=True)

def recordatorio_familiar_sync(med_id, hora_prog, nombre_med, chat_id):
    if ya_tomado(med_id, hora_prog): return
    nombre = CONFIG["nombre"]
    msg = f"{nombre} no confirmo que tomo {nombre_med} a las {hora_prog}."
    cs = get_contactos()
    ec = next((c for c in cs if c["emergencia"]), None)
    if ec and MODO_WHATSAPP:
        enviar_whatsapp(ec["telefono"], msg)
    if FAMILIAR_ID:
        app = bot_ref["app"]
        if app:
            async def avisar():
                await app.bot.send_message(
                    chat_id=FAMILIAR_ID,
                    text=f"⚠️ {msg} Por favor verificar.")
            asyncio.ensure_future(avisar())
    if CONFIG["telefono_usuario"]:
        llamar(CONFIG["telefono_usuario"], f"Recuerda tomar {nombre_med}.")

def agenda_matutina_sync(chat_id):
    meds = get_meds(); agenda = get_agenda(); nombre = CONFIG["nombre"]
    partes = [f"Buenos dias {nombre}."]
    if meds:
        partes.append(f"Tienes {len(meds)} medicamentos hoy:")
        partes += [f"{m['nombre']} a las {m['hora']}." for m in meds]
    if agenda:
        partes.append("En tu agenda:")
        partes += [f"{e['descripcion']} a las {e['hora']}." for e in agenda]
    msg = " ".join(partes); audio = hablar(msg)
    app = bot_ref["app"]
    if app:
        async def enviar():
            await app.bot.send_message(chat_id=chat_id, text=f"🌅 {msg}")
            if audio:
                with open(audio, "rb") as f:
                    await app.bot.send_voice(chat_id=chat_id, voice=f)
        asyncio.ensure_future(enviar())

def sincronizar_alarmas(chat_id: int):
    for job in scheduler.get_jobs():
        if job.id.startswith("med_"): job.remove()
    for m in get_meds():
        try:
            h, mi = m["hora"].split(":")
            scheduler.add_job(
                disparar_alarma_sync, CronTrigger(hour=int(h), minute=int(mi)),
                args=[m["id"], chat_id], id=f"med_{m['id']}", replace_existing=True)
            logger.info(f"Alarma: {m['nombre']} -> {m['hora']}")
        except Exception as e:
            logger.error(f"Error alarma {m['nombre']}: {e}")

# ══════════════════════════════════════════════════════════════
# CEREBRO DE COMANDOS DE VOZ
# ══════════════════════════════════════════════════════════════
def parsear_fecha(texto):
    hoy = date.today(); t = texto.lower()
    t = t.replace("manana", "mañana").replace("sabado", "sábado").replace("miercoles", "miércoles")
    if "pasado" in t and "mañana" in t: return (hoy + timedelta(days=2)).isoformat()
    if "mañana" in t: return (hoy + timedelta(days=1)).isoformat()
    if "hoy" in t: return hoy.isoformat()
    dias = {"lunes":0,"martes":1,"miércoles":2,"jueves":3,"viernes":4,"sábado":5,"domingo":6}
    for nd, num in dias.items():
        if nd in t:
            diff = (num - hoy.weekday()) % 7
            if diff == 0: diff = 7
            return (hoy + timedelta(days=diff)).isoformat()
    return hoy.isoformat()

def parsear_hora(texto):
    t = texto.lower(); hora_num = None; min_num = 0
    m = re.search(r"(\d{1,2}):(\d{2})", t)
    if m: hora_num, min_num = int(m.group(1)), int(m.group(2))
    else:
        m = re.search(r"las\s+(\d{1,2})", t)
        if m: hora_num = int(m.group(1))
    if hora_num is None:
        for n, v in {"una":1,"dos":2,"tres":3,"cuatro":4,"cinco":5,"seis":6,
                     "siete":7,"ocho":8,"nueve":9,"diez":10,"once":11,"doce":12}.items():
            if n in t: hora_num = v; break
    if hora_num is None: return "09:00"
    if any(p in t for p in ["tarde","noche","pm"]) and hora_num < 12: hora_num += 12
    if "media" in t or "treinta" in t: min_num = 30
    elif "cuarto" in t or "quince" in t: min_num = 15
    return f"{hora_num:02d}:{min_num:02d}"

def procesar_comando(texto: str, chat_id=None) -> str:
    if not texto.strip(): return "No escuche nada. Manda un audio con tu voz."
    t = texto.lower().strip(); nombre = CONFIG["nombre"]

    # Confirmar toma
    if any(p in t for p in ["ya tome","ya tomi","ya me tome","confirmar","tomado","si tome"]):
        meds = get_meds(); confirmados = []
        for m in meds:
            if not ya_tomado(m["id"], m["hora"]):
                marcar_tomado(m["id"], m["hora"]); confirmados.append(m["nombre"])
                try: scheduler.remove_job(f"rec_{m['id']}")
                except: pass
        if confirmados: return f"Perfecto {nombre}. Registre que tomaste: {', '.join(confirmados)}. Muy bien!"
        return "No encontre medicamentos pendientes para confirmar hoy."

    # Medicamentos
    if any(p in t for p in ["medicamento","medicina","pastilla","pildora","que tomo","debo tomar"]):
        meds = get_meds()
        if not meds: return "No tienes medicamentos registrados."
        partes = [f"{nombre}, tus medicamentos de hoy son:"]
        for m in meds:
            estado = "ya lo tomaste" if ya_tomado(m["id"], m["hora"]) else "pendiente"
            partes.append(f"{m['nombre']}, {m['dosis']}, a las {m['hora']}, {estado}.")
        return " ".join(partes)

    # Agenda consultar
    if any(p in t for p in ["agenda","cita","que tengo","plan del dia","que hay"]) and \
       not any(p in t for p in ["agendame","programa","anota","recuerdame"]):
        if "mañana" in t or "manana" in t:
            f = (date.today() + timedelta(days=1)).isoformat(); periodo = "manana"
        else:
            f = date.today().isoformat(); periodo = "hoy"
        eventos = get_agenda(f); meds = get_meds() if periodo == "hoy" else []
        partes = [f"{nombre}, tu agenda para {periodo}:"]
        if eventos:
            for e in eventos: partes.append(f"{e['descripcion']} a las {e['hora']}.")
        if meds:
            partes.append("Medicamentos:")
            for m in meds: partes.append(f"{m['nombre']} a las {m['hora']}.")
        if len(partes) == 1: partes.append("No tienes eventos programados.")
        return " ".join(partes)

    # Agendar
    if any(p in t for p in ["agendame","programa","anota","recuerdame","guarda cita"]):
        fecha = parsear_fecha(t); hora = parsear_hora(t)
        desc = re.sub(
            r"(agendame|programa|anota|recuerdame|guarda|cita|una|un|para|"
            r"mañana|manana|hoy|lunes|martes|miercoles|jueves|viernes|"
            r"sabado|domingo|a las|de la tarde|de la manana|de la noche|"
            r"\d{1,2}|:\d{2})", "", t).strip()
        desc = " ".join(desc.split()).title()
        if len(desc) > 2:
            add_agenda(fecha, hora, desc)
            hoy_iso = date.today().isoformat()
            man_iso = (date.today() + timedelta(days=1)).isoformat()
            fl = "hoy" if fecha == hoy_iso else ("manana" if fecha == man_iso else fecha)
            return f"Listo. Agende {desc} para {fl} a las {hora}."
        return "No entendi el evento. Di: agendame cita con el medico para manana a las tres de la tarde."

    # Llamar
    if any(p in t for p in ["llama","llamar","llamale","comunicame"]):
        nom = re.sub(r"(llama|llamar|llamale|comunicame|a |al |con |por favor)", "", t).strip()
        c = buscar_contacto(nom)
        if c:
            llamar(c["telefono"], f"Llamada de {nombre} a traves de su asistente.")
            return f"Llamando a {c['nombre']} al numero {c['telefono']}."
        cs = get_contactos()
        nombres = ", ".join([x["nombre"] for x in cs]) if cs else "ninguno"
        return f"No encontre ese contacto. Tus contactos son: {nombres}."

    # WhatsApp
    if any(p in t for p in ["whatsapp","mensaje","escribele","manda"]):
        nom = re.sub(r"(whatsapp|mensaje|escribele|manda|envia|a |al |con |por favor)", "", t).strip()
        c = buscar_contacto(nom)
        if c:
            enviar_whatsapp(c["telefono"], f"Hola, te escribe {nombre} desde su asistente.")
            return f"Mensaje de WhatsApp enviado a {c['nombre']}."
        cs = get_contactos()
        nombres = ", ".join([x["nombre"] for x in cs]) if cs else "ninguno"
        return f"No encontre ese contacto. Tus contactos son: {nombres}."

    # Emergencia
    if any(p in t for p in ["emergencia","socorro","auxilio","me cai","me siento mal","necesito ayuda"]):
        cs = get_contactos()
        ec = next((c for c in cs if c["emergencia"]), cs[0] if cs else None)
        if ec:
            llamar(ec["telefono"], f"EMERGENCIA. {nombre} necesita ayuda urgente. Acudan de inmediato.")
            enviar_whatsapp(ec["telefono"], f"EMERGENCIA: {nombre} necesita ayuda urgente ahora.")
            return f"Alerta enviada a {ec['nombre']}. Ayuda en camino {nombre}. Mantente tranquilo."
        return "No hay contacto de emergencia. Llama al 112."

    # Salud
    if any(p in t for p in ["presion","tension"]):
        nums = re.findall(r"\d+", t)
        if len(nums) >= 2:
            add_salud("presion_sistolica", int(nums[0]), "mmHg")
            add_salud("presion_diastolica", int(nums[1]), "mmHg")
            return f"Presion {nums[0]} sobre {nums[1]} mmHg registrada."
        return "Di: registra presion 120 sobre 80."

    if any(p in t for p in ["glucosa","azucar","glicemia"]):
        nums = re.findall(r"\d+", t)
        if nums:
            add_salud("glucosa", int(nums[0]), "mg/dL")
            return f"Glucosa {nums[0]} mg/dL registrada."
        return "Di: glucosa 95."

    # Hora / fecha
    if any(p in t for p in ["hora","que hora"]): return f"Son las {datetime.now().strftime('%I:%M %p')}."
    if any(p in t for p in ["fecha","que dia","hoy es"]): return f"Hoy es {datetime.now().strftime('%A %d de %B de %Y')}."

    # Ayuda
    if any(p in t for p in ["ayuda","que puedo","comandos","instrucciones"]):
        return (f"Hola {nombre}. Puedes enviarme un audio diciendo: "
                "mis medicamentos. "
                "Ya tome el medicamento. "
                "Llama a seguido del nombre. "
                "Agendame seguido del evento la fecha y la hora. "
                "Mi agenda de hoy. "
                "Emergencia. "
                "Que hora es. "
                "O toca uno de los botones del menu.")

    return f"No entendi: {texto}. Di ayuda para ver los comandos disponibles."

# ══════════════════════════════════════════════════════════════
# HANDLERS DE TELEGRAM
# ══════════════════════════════════════════════════════════════
MENU = ReplyKeyboardMarkup(
    [["Mis medicamentos"],
     ["Mi agenda de hoy"],
     ["EMERGENCIA"],
     ["Ayuda"]],
    resize_keyboard=True
)

async def cmd_id(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    await update.message.reply_text(
        f"Tu chat ID es:\n\n{chat_id}\n\n"
        "Guarda este número para configurar el acceso autorizado."
    )

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    sincronizar_alarmas(chat_id)
    scheduler.add_job(agenda_matutina_sync, CronTrigger(hour=8, minute=0),
                      args=[chat_id], id="agenda_mat", replace_existing=True)
    saludo = saludo_hora()
    bienvenida = (
        f"{saludo}\n\n"
        f"Soy tu asistente personal.\n"
        f"Mantiene presionado el microfono y habla.\n"
        f"O toca uno de los botones de abajo.\n\n"
        f"Tus alarmas de medicamentos estan activas."
    )
    audio = hablar(bienvenida)
    await update.message.reply_text(bienvenida, reply_markup=MENU)
    if audio:
        with open(audio, "rb") as f: await update.message.reply_voice(voice=f)

async def manejar_voz(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text("Escuchando...")
    voz = update.message.voice or update.message.audio
    archivo = await ctx.bot.get_file(voz.file_id)
    ruta = f"/tmp/audio_{chat_id}.ogg"
    await archivo.download_to_drive(ruta)
    texto = escuchar(ruta)
    if not texto:
        msg = "No escuche bien. Habla mas cerca del microfono e intenta de nuevo."
        await update.message.reply_text(msg, reply_markup=MENU)
        audio = hablar(msg)
        if audio:
            with open(audio, "rb") as f: await update.message.reply_voice(voice=f)
        return
    await update.message.reply_text(f'Escuche: "{texto}"')
    respuesta = procesar_comando(texto, chat_id=chat_id)
    await update.message.reply_text(respuesta, reply_markup=MENU)
    audio = hablar(respuesta)
    if audio:
        with open(audio, "rb") as f: await update.message.reply_voice(voice=f)
    try: os.remove(ruta)
    except: pass

async def manejar_texto(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    texto = update.message.text
    mapa = {
        "Mis medicamentos": "mis medicamentos de hoy",
        "Mi agenda de hoy": "mi agenda de hoy",
        "EMERGENCIA":       "emergencia necesito ayuda urgente",
        "Ayuda":            "ayuda que puedo hacer",
    }
    cmd = mapa.get(texto, texto)
    respuesta = procesar_comando(cmd, chat_id=chat_id)
    await update.message.reply_text(respuesta, reply_markup=MENU)
    audio = hablar(respuesta)
    if audio:
        with open(audio, "rb") as f: await update.message.reply_voice(voice=f)

async def manejar_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("tomado_"):
        partes = data.split("_")
        med_id = int(partes[1]); hora_prog = partes[2]
        con = get_db()
        med = con.execute("SELECT nombre FROM medicamentos WHERE id=?", (med_id,)).fetchone()
        con.close()
        nombre_med = med["nombre"] if med else "medicamento"
        marcar_tomado(med_id, hora_prog)
        try: scheduler.remove_job(f"rec_{med_id}")
        except: pass
        nombre = CONFIG["nombre"]
        respuesta = f"Perfecto {nombre}. {nombre_med} registrado como tomado. Muy bien!"
        await query.edit_message_text(f"Confirmado: {respuesta}")
        audio = hablar(respuesta)
        if audio:
            with open(audio, "rb") as f:
                await ctx.bot.send_voice(chat_id=query.message.chat_id, voice=f)

# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════
def main():
    iniciar_db()
    scheduler.start()
    logger.info("Motor de alarmas iniciado")

    app = Application.builder().token(CONFIG["telegram_token"]).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu",  cmd_start))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, manejar_voz))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, manejar_texto))
    app.add_handler(CallbackQueryHandler(manejar_callback))
    bot_ref["app"] = app

    logger.info("Bot iniciado. Escuchando mensajes...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
