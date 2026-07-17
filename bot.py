"""
Acompañante Mayor — Bot de Telegram para Railway

Funciones principales:
- Acceso controlado por Chat ID.
- /id universal para conocer el identificador del usuario.
- Medicamentos: agregar, quitar, listar y confirmar tomas diarias.
- Alarmas automáticas a la hora programada.
- Agenda: agregar, quitar y consultar eventos.
- Contactos: agregar, quitar, consultar y marcar contacto de emergencia.
- Noticias en texto y audio mediante RSS.
- Entrada por texto o mensaje de voz.
- Respuestas en texto y audio.

Variables de entorno recomendadas en Railway:
- TELEGRAM_TOKEN              Obligatoria.
- USUARIOS_AUTORIZADOS        IDs separados por comas.
- NOMBRE_USUARIO              Nombre del adulto mayor.
- DB_PATH                     /data/acompanante.db
- ZONA_HORARIA                America/Bogota
- RSS_NOTICIAS                URL RSS opcional.
- FAMILIAR_CHAT_ID            ID opcional para alertas.
- TWILIO_SID                  Opcional.
- TWILIO_TOKEN                Opcional.
- TWILIO_NUMERO               Opcional.
- TELEFONO_USUARIO            Opcional.
- CALLMEBOT_PHONE             Opcional.
- CALLMEBOT_KEY               Opcional.

El directorio /data debe montarse como volumen persistente en Railway.
"""

import os
import re
import html
import asyncio
import sqlite3
import tempfile
import logging
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, date, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests
import nest_asyncio
from gtts import gTTS
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

import whisper

nest_asyncio.apply()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("acompanante_mayor")


# ══════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
NOMBRE_USUARIO = os.environ.get("NOMBRE_USUARIO", "Abuelo").strip()
DB_PATH = os.environ.get("DB_PATH", "/data/acompanante.db").strip()
ZONA_HORARIA_NOMBRE = os.environ.get(
    "ZONA_HORARIA",
    "America/Bogota",
).strip()

RSS_NOTICIAS = os.environ.get(
    "RSS_NOTICIAS",
    "https://news.google.com/rss?hl=es-419&gl=CO&ceid=CO:es-419",
).strip()

FAMILIAR_CHAT_ID_RAW = os.environ.get("FAMILIAR_CHAT_ID", "").strip()
TELEFONO_USUARIO = os.environ.get("TELEFONO_USUARIO", "").strip()

TWILIO_SID = os.environ.get("TWILIO_SID", "").strip()
TWILIO_TOKEN = os.environ.get("TWILIO_TOKEN", "").strip()
TWILIO_NUMERO = os.environ.get("TWILIO_NUMERO", "").strip()

CALLMEBOT_PHONE = os.environ.get("CALLMEBOT_PHONE", "").strip()
CALLMEBOT_KEY = os.environ.get("CALLMEBOT_KEY", "").strip()

try:
    ZONA_HORARIA = ZoneInfo(ZONA_HORARIA_NOMBRE)
except Exception:
    logger.warning(
        "Zona horaria inválida '%s'. Se usará America/Bogota.",
        ZONA_HORARIA_NOMBRE,
    )
    ZONA_HORARIA = ZoneInfo("America/Bogota")

try:
    FAMILIAR_CHAT_ID = (
        int(FAMILIAR_CHAT_ID_RAW)
        if FAMILIAR_CHAT_ID_RAW
        else None
    )
except ValueError:
    FAMILIAR_CHAT_ID = None
    logger.warning("FAMILIAR_CHAT_ID no es válido.")


def cargar_usuarios_autorizados() -> set[int]:
    usuarios: set[int] = set()
    valor = os.environ.get("USUARIOS_AUTORIZADOS", "")

    for fragmento in valor.split(","):
        fragmento = fragmento.strip()
        if not fragmento:
            continue

        try:
            usuarios.add(int(fragmento))
        except ValueError:
            logger.warning(
                "Chat ID inválido ignorado: %s",
                fragmento,
            )

    return usuarios


USUARIOS_AUTORIZADOS = cargar_usuarios_autorizados()


def ahora_local() -> datetime:
    return datetime.now(ZONA_HORARIA)


def hoy_local() -> date:
    return ahora_local().date()


def normalizar(texto: str) -> str:
    texto = texto.lower().strip()
    texto = "".join(
        caracter
        for caracter in unicodedata.normalize("NFD", texto)
        if unicodedata.category(caracter) != "Mn"
    )
    texto = re.sub(r"\s+", " ", texto)
    return texto


# ══════════════════════════════════════════════════════════════
# VOZ
# ══════════════════════════════════════════════════════════════
print("Cargando modelo Whisper...")
MODELO_WHISPER = whisper.load_model("tiny")
print("Whisper listo")


def transcribir_audio(ruta_audio: str) -> str:
    if not ruta_audio or not os.path.exists(ruta_audio):
        return ""

    try:
        resultado = MODELO_WHISPER.transcribe(
            ruta_audio,
            language="es",
            fp16=False,
        )
        texto = resultado.get("text", "").strip()
        logger.info("Transcripción: %s", texto)
        return texto
    except Exception as error:
        logger.exception("Error de Whisper: %s", error)
        return ""


def crear_audio(texto: str) -> str | None:
    try:
        archivo = tempfile.NamedTemporaryFile(
            delete=False,
            suffix=".mp3",
        )
        archivo.close()

        gTTS(
            text=texto,
            lang="es",
            slow=False,
        ).save(archivo.name)

        return archivo.name
    except Exception as error:
        logger.exception("Error de gTTS: %s", error)
        return None


def eliminar_temporal(ruta: str | None) -> None:
    if not ruta:
        return

    try:
        if os.path.exists(ruta):
            os.remove(ruta)
    except OSError:
        pass


async def responder_con_audio(
    update: Update,
    texto: str,
    reply_markup=None,
) -> None:
    if not update.message:
        return

    await update.message.reply_text(
        texto,
        reply_markup=reply_markup,
    )

    audio = crear_audio(texto)

    try:
        if audio:
            with open(audio, "rb") as archivo:
                await update.message.reply_voice(
                    voice=archivo
                )
    finally:
        eliminar_temporal(audio)


# ══════════════════════════════════════════════════════════════
# BASE DE DATOS
# ══════════════════════════════════════════════════════════════
directorio_db = os.path.dirname(DB_PATH)
if directorio_db:
    os.makedirs(directorio_db, exist_ok=True)


def conectar_db() -> sqlite3.Connection:
    conexion = sqlite3.connect(DB_PATH)
    conexion.row_factory = sqlite3.Row
    return conexion


def iniciar_db() -> None:
    conexion = conectar_db()

    conexion.executescript(
        """
        CREATE TABLE IF NOT EXISTS medicamentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL COLLATE NOCASE,
            dosis TEXT DEFAULT '',
            hora TEXT NOT NULL,
            activo INTEGER NOT NULL DEFAULT 1,
            creado_en TEXT NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_medicamento_nombre_activo
        ON medicamentos(nombre)
        WHERE activo = 1;

        CREATE TABLE IF NOT EXISTS tomas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            medicamento_id INTEGER NOT NULL,
            fecha TEXT NOT NULL,
            hora_programada TEXT NOT NULL,
            hora_real TEXT NOT NULL,
            UNIQUE(medicamento_id, fecha)
        );

        CREATE TABLE IF NOT EXISTS agenda (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL,
            hora TEXT NOT NULL,
            descripcion TEXT NOT NULL,
            creado_en TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS contactos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL COLLATE NOCASE,
            telefono TEXT NOT NULL,
            relacion TEXT DEFAULT '',
            emergencia INTEGER NOT NULL DEFAULT 0,
            creado_en TEXT NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_contacto_nombre
        ON contactos(nombre);
        """
    )

    conexion.commit()
    conexion.close()


# ══════════════════════════════════════════════════════════════
# MEDICAMENTOS
# ══════════════════════════════════════════════════════════════
def listar_medicamentos() -> list[dict]:
    conexion = conectar_db()
    filas = conexion.execute(
        """
        SELECT *
        FROM medicamentos
        WHERE activo = 1
        ORDER BY hora, nombre
        """
    ).fetchall()
    conexion.close()
    return [dict(fila) for fila in filas]


def buscar_medicamento(nombre: str) -> dict | None:
    buscado = normalizar(nombre)

    for medicamento in listar_medicamentos():
        actual = normalizar(medicamento["nombre"])

        if actual == buscado:
            return medicamento

        if buscado in actual or actual in buscado:
            return medicamento

    return None


def agregar_medicamento(
    nombre: str,
    hora: str,
    dosis: str = "",
) -> tuple[bool, str]:
    nombre = nombre.strip().title()
    dosis = dosis.strip()

    if not nombre:
        return False, "Falta el nombre del medicamento."

    if not validar_hora(hora):
        return False, "La hora no es válida."

    conexion = conectar_db()

    existente = conexion.execute(
        """
        SELECT id
        FROM medicamentos
        WHERE lower(nombre) = lower(?) AND activo = 1
        """,
        (nombre,),
    ).fetchone()

    if existente:
        conexion.close()
        return (
            False,
            f"{nombre} ya está registrado.",
        )

    conexion.execute(
        """
        INSERT INTO medicamentos (
            nombre,
            dosis,
            hora,
            activo,
            creado_en
        )
        VALUES (?, ?, ?, 1, ?)
        """,
        (
            nombre,
            dosis,
            hora,
            ahora_local().isoformat(),
        ),
    )

    conexion.commit()
    conexion.close()

    return (
        True,
        f"Agregué {nombre} a las {hora}.",
    )


def quitar_medicamento(nombre: str) -> tuple[bool, str]:
    medicamento = buscar_medicamento(nombre)

    if not medicamento:
        return (
            False,
            "No encontré ese medicamento en la lista actual.",
        )

    conexion = conectar_db()
    conexion.execute(
        """
        UPDATE medicamentos
        SET activo = 0
        WHERE id = ?
        """,
        (medicamento["id"],),
    )
    conexion.commit()
    conexion.close()

    eliminar_alarmas_medicamento(medicamento["id"])

    return (
        True,
        f"Quité {medicamento['nombre']} de la lista.",
    )


def medicamento_tomado_hoy(medicamento_id: int) -> bool:
    conexion = conectar_db()
    fila = conexion.execute(
        """
        SELECT id
        FROM tomas
        WHERE medicamento_id = ? AND fecha = ?
        """,
        (
            medicamento_id,
            hoy_local().isoformat(),
        ),
    ).fetchone()
    conexion.close()
    return fila is not None


def registrar_toma(nombre: str) -> tuple[bool, str]:
    medicamento = buscar_medicamento(nombre)

    if not medicamento:
        return (
            False,
            "No encontré ese medicamento en la lista actual.",
        )

    if medicamento_tomado_hoy(medicamento["id"]):
        return (
            False,
            f"{medicamento['nombre']} ya estaba registrado como tomado hoy.",
        )

    conexion = conectar_db()

    conexion.execute(
        """
        INSERT INTO tomas (
            medicamento_id,
            fecha,
            hora_programada,
            hora_real
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            medicamento["id"],
            hoy_local().isoformat(),
            medicamento["hora"],
            ahora_local().strftime("%H:%M"),
        ),
    )

    conexion.commit()
    conexion.close()

    cancelar_recordatorio_familiar(medicamento["id"])

    return (
        True,
        f"Registré que tomaste {medicamento['nombre']} hoy.",
    )


def texto_lista_medicamentos() -> str:
    medicamentos = listar_medicamentos()

    if not medicamentos:
        return "No tienes medicamentos registrados."

    partes = ["Tus medicamentos actuales son:"]

    for medicamento in medicamentos:
        estado = (
            "tomado hoy"
            if medicamento_tomado_hoy(medicamento["id"])
            else "pendiente hoy"
        )

        dosis = (
            f", dosis {medicamento['dosis']}"
            if medicamento["dosis"]
            else ""
        )

        partes.append(
            f"{medicamento['nombre']}{dosis}, "
            f"a las {medicamento['hora']}, {estado}."
        )

    return " ".join(partes)


# ══════════════════════════════════════════════════════════════
# AGENDA
# ══════════════════════════════════════════════════════════════
def agregar_evento(
    fecha: str,
    hora: str,
    descripcion: str,
) -> tuple[bool, str]:
    descripcion = descripcion.strip()

    if not descripcion:
        return False, "Falta la descripción del evento."

    if not validar_fecha_iso(fecha):
        return False, "La fecha no es válida."

    if not validar_hora(hora):
        return False, "La hora no es válida."

    conexion = conectar_db()
    conexion.execute(
        """
        INSERT INTO agenda (
            fecha,
            hora,
            descripcion,
            creado_en
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            fecha,
            hora,
            descripcion,
            ahora_local().isoformat(),
        ),
    )
    conexion.commit()
    conexion.close()

    return (
        True,
        f"Agregué {descripcion} para {fecha} a las {hora}.",
    )


def listar_eventos(
    fecha: str | None = None,
) -> list[dict]:
    conexion = conectar_db()

    if fecha:
        filas = conexion.execute(
            """
            SELECT *
            FROM agenda
            WHERE fecha = ?
            ORDER BY hora, id
            """,
            (fecha,),
        ).fetchall()
    else:
        filas = conexion.execute(
            """
            SELECT *
            FROM agenda
            WHERE fecha >= ?
            ORDER BY fecha, hora, id
            """,
            (hoy_local().isoformat(),),
        ).fetchall()

    conexion.close()
    return [dict(fila) for fila in filas]


def quitar_evento(busqueda: str) -> tuple[bool, str]:
    busqueda_normalizada = normalizar(busqueda)
    eventos = listar_eventos()

    coincidencias = [
        evento
        for evento in eventos
        if busqueda_normalizada
        in normalizar(evento["descripcion"])
    ]

    if not coincidencias:
        return False, "No encontré ese evento."

    evento = coincidencias[0]

    conexion = conectar_db()
    conexion.execute(
        "DELETE FROM agenda WHERE id = ?",
        (evento["id"],),
    )
    conexion.commit()
    conexion.close()

    return (
        True,
        f"Quité {evento['descripcion']} de la agenda.",
    )


def texto_agenda(fecha: str | None = None) -> str:
    eventos = listar_eventos(fecha)

    if not eventos:
        if fecha:
            return f"No tienes eventos para {fecha}."
        return "No tienes eventos próximos."

    partes = ["Tu agenda es:"]

    for evento in eventos[:15]:
        partes.append(
            f"{evento['descripcion']}, "
            f"el {evento['fecha']} a las {evento['hora']}."
        )

    return " ".join(partes)


# ══════════════════════════════════════════════════════════════
# CONTACTOS
# ══════════════════════════════════════════════════════════════
def agregar_contacto(
    nombre: str,
    telefono: str,
    relacion: str = "",
    emergencia: bool = False,
) -> tuple[bool, str]:
    nombre = nombre.strip().title()
    telefono = telefono.strip()
    relacion = relacion.strip()

    if not nombre:
        return False, "Falta el nombre del contacto."

    if not re.fullmatch(r"\+?\d{7,15}", telefono):
        return (
            False,
            "El teléfono debe contener entre 7 y 15 dígitos.",
        )

    conexion = conectar_db()

    existente = conexion.execute(
        """
        SELECT id
        FROM contactos
        WHERE lower(nombre) = lower(?)
        """,
        (nombre,),
    ).fetchone()

    if existente:
        conexion.close()
        return (
            False,
            f"Ya existe un contacto llamado {nombre}.",
        )

    if emergencia:
        conexion.execute(
            "UPDATE contactos SET emergencia = 0"
        )

    conexion.execute(
        """
        INSERT INTO contactos (
            nombre,
            telefono,
            relacion,
            emergencia,
            creado_en
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            nombre,
            telefono,
            relacion,
            1 if emergencia else 0,
            ahora_local().isoformat(),
        ),
    )

    conexion.commit()
    conexion.close()

    extra = (
        " y lo marqué como contacto de emergencia"
        if emergencia
        else ""
    )

    return (
        True,
        f"Agregué a {nombre}{extra}.",
    )


def listar_contactos() -> list[dict]:
    conexion = conectar_db()
    filas = conexion.execute(
        """
        SELECT *
        FROM contactos
        ORDER BY emergencia DESC, nombre
        """
    ).fetchall()
    conexion.close()
    return [dict(fila) for fila in filas]


def buscar_contacto(nombre: str) -> dict | None:
    buscado = normalizar(nombre)

    for contacto in listar_contactos():
        actual = normalizar(contacto["nombre"])

        if actual == buscado:
            return contacto

        if buscado in actual or actual in buscado:
            return contacto

    return None


def quitar_contacto(nombre: str) -> tuple[bool, str]:
    contacto = buscar_contacto(nombre)

    if not contacto:
        return False, "No encontré ese contacto."

    conexion = conectar_db()
    conexion.execute(
        "DELETE FROM contactos WHERE id = ?",
        (contacto["id"],),
    )
    conexion.commit()
    conexion.close()

    return (
        True,
        f"Quité a {contacto['nombre']} de tus contactos.",
    )


def texto_contactos() -> str:
    contactos = listar_contactos()

    if not contactos:
        return "No tienes contactos registrados."

    partes = ["Tus contactos son:"]

    for contacto in contactos:
        relacion = (
            f", {contacto['relacion']}"
            if contacto["relacion"]
            else ""
        )
        emergencia = (
            ", contacto de emergencia"
            if contacto["emergencia"]
            else ""
        )

        partes.append(
            f"{contacto['nombre']}{relacion}, "
            f"teléfono {contacto['telefono']}{emergencia}."
        )

    return " ".join(partes)


# ══════════════════════════════════════════════════════════════
# NOTIFICACIONES
# ══════════════════════════════════════════════════════════════
TWILIO_CLIENT = None

if TWILIO_SID and TWILIO_TOKEN and TWILIO_NUMERO:
    try:
        from twilio.rest import Client

        TWILIO_CLIENT = Client(
            TWILIO_SID,
            TWILIO_TOKEN,
        )
    except Exception as error:
        logger.exception("Twilio no pudo iniciarse: %s", error)


def llamar(telefono: str, mensaje: str) -> str:
    if not TWILIO_CLIENT:
        logger.info(
            "[SIMULADO] Llamada a %s: %s",
            telefono,
            mensaje,
        )
        return "Llamada simulada."

    try:
        twiml = (
            "<Response>"
            f"<Say language='es-MX'>{html.escape(mensaje)}</Say>"
            "</Response>"
        )

        TWILIO_CLIENT.calls.create(
            twiml=twiml,
            to=telefono,
            from_=TWILIO_NUMERO,
        )

        return "Llamada iniciada."
    except Exception as error:
        logger.exception("Error de llamada: %s", error)
        return "No se pudo iniciar la llamada."


def enviar_whatsapp(
    telefono: str,
    mensaje: str,
) -> str:
    if not CALLMEBOT_PHONE or not CALLMEBOT_KEY:
        logger.info(
            "[SIMULADO] WhatsApp a %s: %s",
            telefono,
            mensaje,
        )
        return "Mensaje simulado."

    try:
        url = (
            "https://api.callmebot.com/whatsapp.php"
            f"?phone={telefono}"
            f"&text={quote(mensaje)}"
            f"&apikey={CALLMEBOT_KEY}"
        )
        respuesta = requests.get(url, timeout=15)
        return f"Mensaje enviado, código {respuesta.status_code}."
    except Exception as error:
        logger.exception("Error de WhatsApp: %s", error)
        return "No se pudo enviar el mensaje."


# ══════════════════════════════════════════════════════════════
# ALARMAS
# ══════════════════════════════════════════════════════════════
SCHEDULER = BackgroundScheduler(
    timezone=ZONA_HORARIA_NOMBRE
)

BOT_REF = {
    "app": None,
    "loop": None,
}


def ejecutar_en_loop(corutina) -> None:
    loop = BOT_REF.get("loop")

    if loop and loop.is_running():
        asyncio.run_coroutine_threadsafe(
            corutina,
            loop,
        )
    else:
        logger.error("No hay loop activo para enviar mensajes.")


def id_alarma(medicamento_id: int, chat_id: int) -> str:
    return f"med_{medicamento_id}_{chat_id}"


def id_recordatorio(
    medicamento_id: int,
    chat_id: int,
) -> str:
    return f"rec_{medicamento_id}_{chat_id}"


def eliminar_alarmas_medicamento(
    medicamento_id: int,
) -> None:
    for trabajo in SCHEDULER.get_jobs():
        if trabajo.id.startswith(
            f"med_{medicamento_id}_"
        ) or trabajo.id.startswith(
            f"rec_{medicamento_id}_"
        ):
            try:
                trabajo.remove()
            except Exception:
                pass


def cancelar_recordatorio_familiar(
    medicamento_id: int,
) -> None:
    for chat_id in USUARIOS_AUTORIZADOS:
        try:
            SCHEDULER.remove_job(
                id_recordatorio(
                    medicamento_id,
                    chat_id,
                )
            )
        except Exception:
            pass


def disparar_alarma(
    medicamento_id: int,
    chat_id: int,
) -> None:
    conexion = conectar_db()
    fila = conexion.execute(
        """
        SELECT *
        FROM medicamentos
        WHERE id = ? AND activo = 1
        """,
        (medicamento_id,),
    ).fetchone()
    conexion.close()

    if not fila:
        return

    medicamento = dict(fila)

    if medicamento_tomado_hoy(medicamento_id):
        return

    texto = (
        f"{NOMBRE_USUARIO}, es hora de tomar "
        f"{medicamento['nombre']}."
    )

    if medicamento["dosis"]:
        texto += f" La dosis es {medicamento['dosis']}."

    teclado = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"Ya tomé {medicamento['nombre']}",
                    callback_data=f"tomado:{medicamento_id}",
                )
            ]
        ]
    )

    audio = crear_audio(texto)
    app = BOT_REF.get("app")

    if app:

        async def enviar() -> None:
            try:
                await app.bot.send_message(
                    chat_id=chat_id,
                    text=f"⏰ {texto}",
                    reply_markup=teclado,
                )

                if audio:
                    with open(audio, "rb") as archivo:
                        await app.bot.send_voice(
                            chat_id=chat_id,
                            voice=archivo,
                        )
            finally:
                eliminar_temporal(audio)

        ejecutar_en_loop(enviar())

    SCHEDULER.add_job(
        aviso_no_confirmado,
        "date",
        run_date=ahora_local() + timedelta(minutes=15),
        args=[
            medicamento_id,
            chat_id,
        ],
        id=id_recordatorio(
            medicamento_id,
            chat_id,
        ),
        replace_existing=True,
    )


def aviso_no_confirmado(
    medicamento_id: int,
    chat_id: int,
) -> None:
    if medicamento_tomado_hoy(medicamento_id):
        return

    conexion = conectar_db()
    medicamento = conexion.execute(
        """
        SELECT nombre
        FROM medicamentos
        WHERE id = ?
        """,
        (medicamento_id,),
    ).fetchone()
    conexion.close()

    if not medicamento:
        return

    mensaje = (
        f"{NOMBRE_USUARIO} no confirmó que tomó "
        f"{medicamento['nombre']}."
    )

    app = BOT_REF.get("app")

    if app and FAMILIAR_CHAT_ID:

        async def avisar() -> None:
            await app.bot.send_message(
                chat_id=FAMILIAR_CHAT_ID,
                text=f"⚠️ {mensaje}",
            )

        ejecutar_en_loop(avisar())

    contacto_emergencia = next(
        (
            contacto
            for contacto in listar_contactos()
            if contacto["emergencia"]
        ),
        None,
    )

    if contacto_emergencia:
        enviar_whatsapp(
            contacto_emergencia["telefono"],
            mensaje,
        )

    if TELEFONO_USUARIO:
        llamar(
            TELEFONO_USUARIO,
            f"Recuerda tomar {medicamento['nombre']}.",
        )


def sincronizar_alarmas() -> None:
    for trabajo in SCHEDULER.get_jobs():
        if trabajo.id.startswith("med_"):
            trabajo.remove()

    medicamentos = listar_medicamentos()

    for medicamento in medicamentos:
        hora, minuto = medicamento["hora"].split(":")

        for chat_id in USUARIOS_AUTORIZADOS:
            SCHEDULER.add_job(
                disparar_alarma,
                CronTrigger(
                    hour=int(hora),
                    minute=int(minuto),
                    timezone=ZONA_HORARIA_NOMBRE,
                ),
                args=[
                    medicamento["id"],
                    chat_id,
                ],
                id=id_alarma(
                    medicamento["id"],
                    chat_id,
                ),
                replace_existing=True,
            )

            logger.info(
                "Alarma programada: %s, %s, chat %s",
                medicamento["nombre"],
                medicamento["hora"],
                chat_id,
            )


# ══════════════════════════════════════════════════════════════
# NOTICIAS
# ══════════════════════════════════════════════════════════════
def limpiar_titulo_noticia(titulo: str) -> str:
    titulo = html.unescape(titulo).strip()
    titulo = re.sub(r"\s+-\s+[^-]+$", "", titulo)
    return titulo


def obtener_noticias(limite: int = 5) -> list[str]:
    try:
        respuesta = requests.get(
            RSS_NOTICIAS,
            timeout=15,
            headers={
                "User-Agent": "Mozilla/5.0"
            },
        )
        respuesta.raise_for_status()

        raiz = ET.fromstring(respuesta.content)
        noticias: list[str] = []

        for item in raiz.findall(".//item"):
            titulo = item.findtext("title", "").strip()

            if titulo:
                noticias.append(
                    limpiar_titulo_noticia(titulo)
                )

            if len(noticias) >= limite:
                break

        return noticias
    except Exception as error:
        logger.exception("Error al obtener noticias: %s", error)
        return []


def texto_noticias() -> str:
    noticias = obtener_noticias()

    if not noticias:
        return (
            "No pude consultar las noticias en este momento. "
            "Intenta nuevamente más tarde."
        )

    partes = ["Estas son algunas noticias recientes:"]

    for indice, titulo in enumerate(noticias, start=1):
        partes.append(f"{indice}. {titulo}.")

    return " ".join(partes)


# ══════════════════════════════════════════════════════════════
# PARSEO DE FECHA Y HORA
# ══════════════════════════════════════════════════════════════
def validar_hora(hora: str) -> bool:
    try:
        datetime.strptime(hora, "%H:%M")
        return True
    except ValueError:
        return False


def validar_fecha_iso(fecha: str) -> bool:
    try:
        datetime.strptime(fecha, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def parsear_hora(texto: str) -> str | None:
    texto_n = normalizar(texto)

    coincidencia = re.search(
        r"\b([01]?\d|2[0-3]):([0-5]\d)\b",
        texto_n,
    )

    if coincidencia:
        return (
            f"{int(coincidencia.group(1)):02d}:"
            f"{int(coincidencia.group(2)):02d}"
        )

    coincidencia = re.search(
        r"\b(?:a las|las)\s+(\d{1,2})(?:\s+y\s+(\d{1,2}))?",
        texto_n,
    )

    hora = None
    minuto = 0

    if coincidencia:
        hora = int(coincidencia.group(1))
        minuto = int(coincidencia.group(2) or 0)
    else:
        numeros = {
            "una": 1,
            "dos": 2,
            "tres": 3,
            "cuatro": 4,
            "cinco": 5,
            "seis": 6,
            "siete": 7,
            "ocho": 8,
            "nueve": 9,
            "diez": 10,
            "once": 11,
            "doce": 12,
        }

        for palabra, valor in numeros.items():
            if re.search(
                rf"\b(?:a las|las)\s+{palabra}\b",
                texto_n,
            ):
                hora = valor
                break

    if hora is None:
        return None

    if "media" in texto_n:
        minuto = 30
    elif "cuarto" in texto_n:
        minuto = 15

    if any(
        periodo in texto_n
        for periodo in [
            "de la tarde",
            "de la noche",
            " pm",
        ]
    ) and hora < 12:
        hora += 12

    if (
        "de la manana" in texto_n
        and hora == 12
    ):
        hora = 0

    if hora > 23 or minuto > 59:
        return None

    return f"{hora:02d}:{minuto:02d}"


def parsear_fecha(texto: str) -> str:
    texto_n = normalizar(texto)
    hoy = hoy_local()

    coincidencia_iso = re.search(
        r"\b(20\d{2}-\d{2}-\d{2})\b",
        texto_n,
    )

    if coincidencia_iso:
        return coincidencia_iso.group(1)

    if "pasado manana" in texto_n:
        return (
            hoy + timedelta(days=2)
        ).isoformat()

    if "manana" in texto_n:
        return (
            hoy + timedelta(days=1)
        ).isoformat()

    if "hoy" in texto_n:
        return hoy.isoformat()

    dias = {
        "lunes": 0,
        "martes": 1,
        "miercoles": 2,
        "jueves": 3,
        "viernes": 4,
        "sabado": 5,
        "domingo": 6,
    }

    for nombre_dia, numero_dia in dias.items():
        if nombre_dia in texto_n:
            diferencia = (
                numero_dia - hoy.weekday()
            ) % 7

            if diferencia == 0:
                diferencia = 7

            return (
                hoy + timedelta(days=diferencia)
            ).isoformat()

    return hoy.isoformat()


def extraer_nombre_medicamento_agregar(
    texto: str,
) -> str:
    texto_n = normalizar(texto)

    texto_n = re.sub(
        r"^(agregar|agrega|anadir|anade|registrar|registra)\s+",
        "",
        texto_n,
    )

    texto_n = re.sub(
        r"\b(medicamento|medicina|pastilla)\b",
        "",
        texto_n,
    )

    texto_n = re.split(
        r"\b(?:a las|las)\b",
        texto_n,
        maxsplit=1,
    )[0]

    texto_n = re.sub(
        r"\b(dosis|debo tomar|tomar)\b.*$",
        "",
        texto_n,
    )

    return texto_n.strip().title()


def extraer_nombre_medicamento_quitar(
    texto: str,
) -> str:
    texto_n = normalizar(texto)

    texto_n = re.sub(
        r"^(quitar|quita|eliminar|elimina|borrar|borra|dejar de tomar)\s+",
        "",
        texto_n,
    )

    texto_n = re.sub(
        r"\b(medicamento|medicina|pastilla)\b",
        "",
        texto_n,
    )

    return texto_n.strip().title()


def extraer_descripcion_evento(texto: str) -> str:
    texto_n = normalizar(texto)

    texto_n = re.sub(
        r"^(agregar|agrega|anadir|anade|registrar|registra|agendar|agenda)\s+",
        "",
        texto_n,
    )

    texto_n = re.sub(
        r"\b(evento|cita)\b",
        "",
        texto_n,
    )

    texto_n = re.split(
        r"\b(?:para hoy|para manana|hoy|manana|lunes|martes|miercoles|jueves|viernes|sabado|domingo|a las|las)\b",
        texto_n,
        maxsplit=1,
    )[0]

    return texto_n.strip().title()


def extraer_nombre_contacto_agregar(
    texto: str,
) -> tuple[str, str, str, bool]:
    texto_n = normalizar(texto)

    emergencia = "emergencia" in texto_n

    telefono_match = re.search(
        r"(\+?\d{7,15})",
        texto_n,
    )
    telefono = (
        telefono_match.group(1)
        if telefono_match
        else ""
    )

    relacion = ""
    relacion_match = re.search(
        r"\b(?:relacion|es mi|mi)\s+([a-zñ ]+?)(?:\s+telefono|\s+\+?\d|$)",
        texto_n,
    )

    if relacion_match:
        relacion = relacion_match.group(1).strip()

    nombre_texto = re.sub(
        r"^(agregar|agrega|anadir|anade|registrar|registra)\s+",
        "",
        texto_n,
    )

    nombre_texto = re.sub(
        r"\b(contacto|telefono|emergencia)\b",
        "",
        nombre_texto,
    )

    nombre_texto = re.sub(
        r"\+?\d{7,15}",
        "",
        nombre_texto,
    )

    nombre_texto = re.split(
        r"\b(?:relacion|es mi|mi)\b",
        nombre_texto,
        maxsplit=1,
    )[0]

    nombre = nombre_texto.strip().title()

    return nombre, telefono, relacion, emergencia


# ══════════════════════════════════════════════════════════════
# CEREBRO DE COMANDOS
# ══════════════════════════════════════════════════════════════
def procesar_comando(texto: str) -> str:
    original = texto.strip()
    t = normalizar(original)

    if not t:
        return "No escuché ningún mensaje."

    # Hora y fecha
    if "que hora" in t or t == "hora":
        return (
            f"Son las {ahora_local().strftime('%I:%M %p')}."
        )

    if (
        "que dia" in t
        or "fecha" in t
        or "hoy es" in t
    ):
        return (
            f"Hoy es {ahora_local().strftime('%d/%m/%Y')}."
        )

    # Noticias
    if any(
        palabra in t
        for palabra in [
            "noticias",
            "noticia",
            "entretenimiento",
            "que esta pasando",
        ]
    ):
        return texto_noticias()

    # Agregar medicamento
    if (
        any(
            verbo in t
            for verbo in [
                "agregar medicamento",
                "agrega medicamento",
                "agregar medicina",
                "agrega medicina",
                "agregar pastilla",
                "agrega pastilla",
                "anadir medicamento",
                "registrar medicamento",
            ]
        )
    ):
        hora = parsear_hora(original)
        nombre = extraer_nombre_medicamento_agregar(
            original
        )

        if not nombre:
            return (
                "Dime el nombre del medicamento. "
                "Por ejemplo: agregar medicamento Aspirina a las ocho."
            )

        if not hora:
            return (
                "Dime la hora. Por ejemplo: "
                f"agregar medicamento {nombre} a las ocho."
            )

        exito, mensaje = agregar_medicamento(
            nombre,
            hora,
        )

        if exito:
            sincronizar_alarmas()

        return mensaje

    # Quitar medicamento
    if any(
        frase in t
        for frase in [
            "quitar medicamento",
            "quita medicamento",
            "eliminar medicamento",
            "elimina medicamento",
            "borrar medicamento",
            "dejar de tomar",
        ]
    ):
        nombre = extraer_nombre_medicamento_quitar(
            original
        )

        if not nombre:
            return (
                "Dime cuál medicamento quieres quitar."
            )

        return quitar_medicamento(nombre)[1]

    # Registrar toma específica
    if any(
        frase in t
        for frase in [
            "ya tome",
            "ya me tome",
            "registre que tome",
            "registrar toma",
            "marcar como tomado",
        ]
    ):
        medicamentos = listar_medicamentos()

        if not medicamentos:
            return "No tienes medicamentos registrados."

        mencionado = None

        for medicamento in medicamentos:
            nombre_n = normalizar(
                medicamento["nombre"]
            )

            if nombre_n in t:
                mencionado = medicamento
                break

            palabras = [
                palabra
                for palabra in nombre_n.split()
                if len(palabra) >= 4
            ]

            if any(palabra in t for palabra in palabras):
                mencionado = medicamento
                break

        pendientes = [
            medicamento
            for medicamento in medicamentos
            if not medicamento_tomado_hoy(
                medicamento["id"]
            )
        ]

        if mencionado:
            return registrar_toma(
                mencionado["nombre"]
            )[1]

        if len(pendientes) == 1:
            return registrar_toma(
                pendientes[0]["nombre"]
            )[1]

        if len(pendientes) > 1:
            nombres = ", ".join(
                medicamento["nombre"]
                for medicamento in pendientes
            )
            return (
                "Dime cuál tomaste. "
                f"Los pendientes son: {nombres}."
            )

        return (
            "Todos los medicamentos de hoy "
            "ya están registrados como tomados."
        )

    # Consultar medicamentos
    if any(
        frase in t
        for frase in [
            "mis medicamentos",
            "lista de medicamentos",
            "que medicamentos",
            "que medicinas",
            "que pastillas",
            "debo tomar",
        ]
    ):
        return texto_lista_medicamentos()

    # Agregar evento
    if any(
        frase in t
        for frase in [
            "agregar evento",
            "agrega evento",
            "agendar evento",
            "agregar cita",
            "agrega cita",
            "agendar cita",
        ]
    ):
        fecha = parsear_fecha(original)
        hora = parsear_hora(original)
        descripcion = extraer_descripcion_evento(
            original
        )

        if not descripcion:
            return (
                "Dime el nombre del evento. "
                "Por ejemplo: agregar cita médica "
                "para mañana a las tres."
            )

        if not hora:
            return (
                "Dime la hora del evento."
            )

        return agregar_evento(
            fecha,
            hora,
            descripcion,
        )[1]

    # Quitar evento
    if any(
        frase in t
        for frase in [
            "quitar evento",
            "quita evento",
            "eliminar evento",
            "elimina evento",
            "quitar cita",
            "eliminar cita",
        ]
    ):
        busqueda = re.sub(
            r"^(quitar|quita|eliminar|elimina)\s+",
            "",
            t,
        )
        busqueda = re.sub(
            r"\b(evento|cita)\b",
            "",
            busqueda,
        ).strip()

        if not busqueda:
            return (
                "Dime qué evento quieres quitar."
            )

        return quitar_evento(busqueda)[1]

    # Consultar agenda
    if any(
        frase in t
        for frase in [
            "mi agenda",
            "agenda de hoy",
            "agenda de manana",
            "mis eventos",
            "que tengo hoy",
            "que tengo manana",
        ]
    ):
        if "manana" in t:
            fecha = (
                hoy_local() + timedelta(days=1)
            ).isoformat()
        elif "hoy" in t:
            fecha = hoy_local().isoformat()
        else:
            fecha = None

        return texto_agenda(fecha)

    # Agregar contacto
    if any(
        frase in t
        for frase in [
            "agregar contacto",
            "agrega contacto",
            "anadir contacto",
            "registrar contacto",
        ]
    ):
        nombre, telefono, relacion, emergencia = (
            extraer_nombre_contacto_agregar(
                original
            )
        )

        if not nombre or not telefono:
            return (
                "Di, por ejemplo: agregar contacto Ana "
                "teléfono 3001234567."
            )

        return agregar_contacto(
            nombre,
            telefono,
            relacion,
            emergencia,
        )[1]

    # Quitar contacto
    if any(
        frase in t
        for frase in [
            "quitar contacto",
            "quita contacto",
            "eliminar contacto",
            "elimina contacto",
        ]
    ):
        nombre = re.sub(
            r"^(quitar|quita|eliminar|elimina)\s+",
            "",
            t,
        )
        nombre = re.sub(
            r"\bcontacto\b",
            "",
            nombre,
        ).strip()

        if not nombre:
            return (
                "Dime qué contacto quieres quitar."
            )

        return quitar_contacto(nombre)[1]

    # Consultar contactos
    if any(
        frase in t
        for frase in [
            "mis contactos",
            "lista de contactos",
            "que contactos",
        ]
    ):
        return texto_contactos()

    # Llamar
    if any(
        t.startswith(frase)
        for frase in [
            "llama a ",
            "llamar a ",
            "llamale a ",
        ]
    ):
        nombre = re.sub(
            r"^(llama a|llamar a|llamale a)\s+",
            "",
            t,
        ).strip()

        contacto = buscar_contacto(nombre)

        if not contacto:
            return "No encontré ese contacto."

        llamar(
            contacto["telefono"],
            f"Llamada de {NOMBRE_USUARIO}.",
        )

        return (
            f"Inicié la llamada a {contacto['nombre']}."
        )

    # Emergencia
    if any(
        frase in t
        for frase in [
            "emergencia",
            "auxilio",
            "socorro",
            "me cai",
            "me siento mal",
            "necesito ayuda",
        ]
    ):
        contacto = next(
            (
                contacto
                for contacto in listar_contactos()
                if contacto["emergencia"]
            ),
            None,
        )

        if not contacto:
            return (
                "No tienes un contacto de emergencia configurado."
            )

        mensaje = (
            f"EMERGENCIA: {NOMBRE_USUARIO} "
            "necesita ayuda urgente."
        )

        enviar_whatsapp(
            contacto["telefono"],
            mensaje,
        )
        llamar(
            contacto["telefono"],
            mensaje,
        )

        return (
            f"Envié una alerta a {contacto['nombre']}."
        )

    # Ayuda
    if any(
        palabra in t
        for palabra in [
            "ayuda",
            "comandos",
            "que puedo hacer",
        ]
    ):
        return (
            "Puedes decir: agregar medicamento Aspirina "
            "a las ocho; quitar medicamento Aspirina; "
            "mis medicamentos; ya tomé Aspirina; "
            "agregar cita médica para mañana a las tres; "
            "quitar evento cita médica; mi agenda; "
            "agregar contacto Ana teléfono 3001234567; "
            "quitar contacto Ana; mis contactos; "
            "noticias; emergencia; o qué hora es."
        )

    return (
        "No entendí el mensaje. Di ayuda "
        "para escuchar algunos ejemplos."
    )


# ══════════════════════════════════════════════════════════════
# SEGURIDAD Y MENÚ
# ══════════════════════════════════════════════════════════════
def usuario_autorizado(update: Update) -> bool:
    chat = update.effective_chat
    return bool(
        chat
        and chat.id in USUARIOS_AUTORIZADOS
    )


def mensaje_no_autorizado(chat_id: int) -> str:
    return (
        "Todavía no estás autorizado para utilizar "
        "este asistente.\n\n"
        f"Tu Chat ID es:\n{chat_id}\n\n"
        "Envía este número al administrador."
    )


MENU = ReplyKeyboardMarkup(
    [
        ["Mis medicamentos", "Mi agenda"],
        ["Mis contactos", "Noticias"],
        ["EMERGENCIA", "Ayuda"],
    ],
    resize_keyboard=True,
)


# ══════════════════════════════════════════════════════════════
# HANDLERS
# ══════════════════════════════════════════════════════════════
async def cmd_id(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    chat_id = update.effective_chat.id

    estado = (
        "Tu acceso ya está autorizado."
        if chat_id in USUARIOS_AUTORIZADOS
        else (
            "Tu acceso todavía no está autorizado. "
            "Envía este número al administrador."
        )
    )

    await update.message.reply_text(
        f"Tu Chat ID es:\n\n{chat_id}\n\n{estado}"
    )


async def cmd_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not usuario_autorizado(update):
        await update.message.reply_text(
            mensaje_no_autorizado(
                update.effective_chat.id
            )
        )
        return

    saludo = (
        "Buenos días"
        if ahora_local().hour < 12
        else (
            "Buenas tardes"
            if ahora_local().hour < 18
            else "Buenas noches"
        )
    )

    texto = (
        f"{saludo}, {NOMBRE_USUARIO}. "
        f"Son las {ahora_local().strftime('%I:%M %p')}. "
        "El asistente está listo. "
        "Puedes escribir, hablar o usar los botones."
    )

    await responder_con_audio(
        update,
        texto,
        reply_markup=MENU,
    )


async def manejar_texto(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not usuario_autorizado(update):
        await update.message.reply_text(
            mensaje_no_autorizado(
                update.effective_chat.id
            )
        )
        return

    texto = update.message.text or ""

    mapa = {
        "Mis medicamentos": "mis medicamentos",
        "Mi agenda": "mi agenda",
        "Mis contactos": "mis contactos",
        "Noticias": "noticias",
        "EMERGENCIA": "emergencia",
        "Ayuda": "ayuda",
    }

    comando = mapa.get(texto, texto)
    respuesta = procesar_comando(comando)

    await responder_con_audio(
        update,
        respuesta,
        reply_markup=MENU,
    )


async def manejar_voz(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not usuario_autorizado(update):
        await update.message.reply_text(
            mensaje_no_autorizado(
                update.effective_chat.id
            )
        )
        return

    voz = update.message.voice or update.message.audio

    if not voz:
        await update.message.reply_text(
            "No encontré el audio."
        )
        return

    chat_id = update.effective_chat.id
    ruta = f"/tmp/audio_{chat_id}_{voz.file_id}.ogg"

    await update.message.reply_text("Escuchando...")

    try:
        archivo = await context.bot.get_file(
            voz.file_id
        )
        await archivo.download_to_drive(ruta)

        texto = transcribir_audio(ruta)

        if not texto:
            await responder_con_audio(
                update,
                "No escuché bien. Intenta nuevamente.",
                reply_markup=MENU,
            )
            return

        await update.message.reply_text(
            f'Escuché: "{texto}"'
        )

        respuesta = procesar_comando(texto)

        await responder_con_audio(
            update,
            respuesta,
            reply_markup=MENU,
        )
    except Exception as error:
        logger.exception(
            "Error al procesar audio: %s",
            error,
        )
        await responder_con_audio(
            update,
            "Ocurrió un error al procesar el audio.",
            reply_markup=MENU,
        )
    finally:
        eliminar_temporal(ruta)


async def manejar_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    if not query:
        return

    if not usuario_autorizado(update):
        await query.answer(
            "No tienes autorización.",
            show_alert=True,
        )
        return

    await query.answer()

    if not query.data:
        return

    if query.data.startswith("tomado:"):
        try:
            medicamento_id = int(
                query.data.split(":", 1)[1]
            )
        except ValueError:
            await query.edit_message_text(
                "No pude interpretar la confirmación."
            )
            return

        conexion = conectar_db()
        medicamento = conexion.execute(
            """
            SELECT nombre
            FROM medicamentos
            WHERE id = ? AND activo = 1
            """,
            (medicamento_id,),
        ).fetchone()
        conexion.close()

        if not medicamento:
            await query.edit_message_text(
                "Ese medicamento ya no está activo."
            )
            return

        _, mensaje = registrar_toma(
            medicamento["nombre"]
        )

        await query.edit_message_text(mensaje)

        audio = crear_audio(mensaje)

        try:
            if audio:
                with open(audio, "rb") as archivo:
                    await context.bot.send_voice(
                        chat_id=query.message.chat_id,
                        voice=archivo,
                    )
        finally:
            eliminar_temporal(audio)


async def manejar_error(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    logger.exception(
        "Error no controlado:",
        exc_info=context.error,
    )


async def configurar_aplicacion(
    app: Application,
) -> None:
    BOT_REF["app"] = app
    BOT_REF["loop"] = asyncio.get_running_loop()

    sincronizar_alarmas()


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════
def main() -> None:
    if not TELEGRAM_TOKEN:
        raise RuntimeError(
            "Falta TELEGRAM_TOKEN en Railway."
        )

    if not USUARIOS_AUTORIZADOS:
        logger.warning(
            "USUARIOS_AUTORIZADOS está vacío. "
            "Solo /id funcionará."
        )

    iniciar_db()

    if not SCHEDULER.running:
        SCHEDULER.start()

    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(configurar_aplicacion)
        .build()
    )

    app.add_handler(
        CommandHandler("id", cmd_id)
    )
    app.add_handler(
        CommandHandler("start", cmd_start)
    )
    app.add_handler(
        CommandHandler("menu", cmd_start)
    )
    app.add_handler(
        MessageHandler(
            filters.VOICE | filters.AUDIO,
            manejar_voz,
        )
    )
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            manejar_texto,
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            manejar_callback
        )
    )
    app.add_error_handler(manejar_error)

    BOT_REF["app"] = app

    logger.info(
        "Bot iniciado. Zona horaria: %s",
        ZONA_HORARIA_NOMBRE,
    )

    app.run_polling(
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
