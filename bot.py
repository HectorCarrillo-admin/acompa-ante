"""
Acompañante Mayor — Bot de Telegram para Railway

Características:
- Base de datos inicialmente vacía.
- Medicamentos: agregar, quitar, consultar y registrar tomas diarias.
- Alarmas automáticas con botón de confirmación.
- Agenda: agregar, quitar y consultar eventos.
- Contactos: agregar, quitar, consultar y marcar emergencia.
- Noticias por RSS, en texto y audio.
- Entrada por texto o voz.
- Respuestas en texto y audio.
- Control de acceso por Chat ID.
- Zona horaria configurable, por defecto America/Bogota.

Variables de entorno:
OBLIGATORIAS
- TELEGRAM_TOKEN
- USUARIOS_AUTORIZADOS        Ejemplo: 1532627802,1876543210

RECOMENDADAS
- NOMBRE_USUARIO              Ejemplo: Nubia
- DB_PATH                     /data/acompanante.db
- ZONA_HORARIA                America/Bogota
- MODELO_VOZ                  base
- WHISPER_CPU_THREADS         2
- WHISPER_BEAM_SIZE           3

OPCIONALES
- USUARIOS_ALARMA             IDs que reciben alarmas. Si está vacío, usa USUARIOS_AUTORIZADOS.
- FAMILIAR_CHAT_ID            Recibe aviso si no se confirma una toma.
- RSS_NOTICIAS                Feed RSS.
- TWILIO_SID
- TWILIO_TOKEN
- TWILIO_NUMERO
- TELEFONO_USUARIO
- CALLMEBOT_PHONE
- CALLMEBOT_KEY

Railway:
- Montar un volumen persistente en /data.
- El Dockerfile debe incluir ffmpeg.
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
import re
import sqlite3
import subprocess
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable
from urllib.parse import quote
from zoneinfo import ZoneInfo

import nest_asyncio
import requests
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from faster_whisper import WhisperModel
from gtts import gTTS
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

nest_asyncio.apply()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("acompanante_mayor")


# ══════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
NOMBRE_USUARIO = os.getenv("NOMBRE_USUARIO", "Usuario").strip()
DB_PATH = os.getenv("DB_PATH", "/data/acompanante.db").strip()
ZONA_HORARIA_NOMBRE = os.getenv("ZONA_HORARIA", "America/Bogota").strip()

MODELO_VOZ = os.getenv("MODELO_VOZ", "base").strip()
WHISPER_CPU_THREADS = int(os.getenv("WHISPER_CPU_THREADS", "2"))
WHISPER_BEAM_SIZE = int(os.getenv("WHISPER_BEAM_SIZE", "3"))

RSS_NOTICIAS = os.getenv(
    "RSS_NOTICIAS",
    "https://news.google.com/rss?hl=es-419&gl=CO&ceid=CO:es-419",
).strip()

FAMILIAR_CHAT_ID_RAW = os.getenv("FAMILIAR_CHAT_ID", "").strip()

TWILIO_SID = os.getenv("TWILIO_SID", "").strip()
TWILIO_TOKEN = os.getenv("TWILIO_TOKEN", "").strip()
TWILIO_NUMERO = os.getenv("TWILIO_NUMERO", "").strip()
TELEFONO_USUARIO = os.getenv("TELEFONO_USUARIO", "").strip()

CALLMEBOT_PHONE = os.getenv("CALLMEBOT_PHONE", "").strip()
CALLMEBOT_KEY = os.getenv("CALLMEBOT_KEY", "").strip()


def parsear_ids(valor: str) -> set[int]:
    resultado: set[int] = set()

    for fragmento in valor.split(","):
        fragmento = fragmento.strip()

        if not fragmento:
            continue

        try:
            resultado.add(int(fragmento))
        except ValueError:
            logger.warning("Chat ID inválido ignorado: %s", fragmento)

    return resultado


USUARIOS_AUTORIZADOS = parsear_ids(
    os.getenv("USUARIOS_AUTORIZADOS", "")
)

USUARIOS_ALARMA = parsear_ids(
    os.getenv("USUARIOS_ALARMA", "")
)
if not USUARIOS_ALARMA:
    USUARIOS_ALARMA = set(USUARIOS_AUTORIZADOS)

try:
    FAMILIAR_CHAT_ID = (
        int(FAMILIAR_CHAT_ID_RAW)
        if FAMILIAR_CHAT_ID_RAW
        else None
    )
except ValueError:
    FAMILIAR_CHAT_ID = None
    logger.warning("FAMILIAR_CHAT_ID no es válido.")

try:
    ZONA_HORARIA = ZoneInfo(ZONA_HORARIA_NOMBRE)
except Exception:
    logger.warning(
        "Zona horaria inválida '%s'. Se usará America/Bogota.",
        ZONA_HORARIA_NOMBRE,
    )
    ZONA_HORARIA_NOMBRE = "America/Bogota"
    ZONA_HORARIA = ZoneInfo(ZONA_HORARIA_NOMBRE)


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


def texto_sin_puntuacion(texto: str) -> str:
    texto = normalizar(texto)
    texto = re.sub(r"[,.;:!?¡¿]", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    texto = texto.replace("madrugrada", "madrugada")
    texto = texto.replace("madrujada", "madrugada")
    texto = re.sub(r"\balas\b", "a las", texto)
    texto = re.sub(
        r"\ba la\s+(?=\d|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce)",
        "a las ",
        texto,
    )
    return texto


# ══════════════════════════════════════════════════════════════
# VOZ
# ══════════════════════════════════════════════════════════════
VOCABULARIO_VOZ = (
    "Acompañante Mayor. Comandos frecuentes: agregar medicamento, "
    "quitar medicamento, mis medicamentos, ya tomé, Aspirina, "
    "Metformina, Losartán, Omeprazol, Vitamina D, agenda, cita, "
    "contacto, emergencia, noticias, mañana, tarde, noche, madrugada."
)

print(f"Cargando modelo de voz '{MODELO_VOZ}'...")
MODELO_WHISPER = WhisperModel(
    MODELO_VOZ,
    device="cpu",
    compute_type="int8",
    cpu_threads=WHISPER_CPU_THREADS,
    num_workers=1,
)
print("Modelo de voz listo")


def eliminar_temporal(ruta: str | None) -> None:
    if not ruta:
        return

    try:
        Path(ruta).unlink(missing_ok=True)
    except OSError:
        pass


def convertir_audio(ruta_origen: str) -> str | None:
    archivo = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    archivo.close()

    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-i",
                ruta_origen,
                "-ac",
                "1",
                "-ar",
                "16000",
                "-vn",
                archivo.name,
            ],
            check=True,
            timeout=30,
        )
        return archivo.name
    except Exception as error:
        logger.exception("Error al convertir audio: %s", error)
        eliminar_temporal(archivo.name)
        return None


def corregir_errores_voz(texto: str) -> str:
    texto = texto_sin_puntuacion(texto)

    reemplazos = {
        "abregar": "agregar",
        "agragar": "agregar",
        "agregarme": "agregar",
        "anotar medicamento": "agregar medicamento",
        "programar medicamento": "agregar medicamento",
        "me he comiendo": "medicamento",
        "me comiendo": "medicamento",
        "mis medicamento": "mis medicamentos",
        "mis contacto": "mis contactos",
        "quite medicamento": "quitar medicamento",
    }

    for origen, destino in reemplazos.items():
        texto = texto.replace(origen, destino)

    return texto


def transcribir_audio(ruta_audio: str) -> str:
    ruta_wav = convertir_audio(ruta_audio)

    if not ruta_wav:
        return ""

    try:
        segmentos, _ = MODELO_WHISPER.transcribe(
            ruta_wav,
            language="es",
            beam_size=WHISPER_BEAM_SIZE,
            best_of=WHISPER_BEAM_SIZE,
            vad_filter=True,
            vad_parameters={
                "min_silence_duration_ms": 350,
                "speech_pad_ms": 200,
            },
            condition_on_previous_text=False,
            initial_prompt=VOCABULARIO_VOZ,
            temperature=0.0,
        )

        texto = " ".join(
            segmento.text.strip()
            for segmento in segmentos
            if segmento.text.strip()
        ).strip()

        corregido = corregir_errores_voz(texto)

        logger.info('Transcripción original: "%s"', texto)
        logger.info('Transcripción corregida: "%s"', corregido)

        return corregido
    except Exception as error:
        logger.exception("Error al transcribir audio: %s", error)
        return ""
    finally:
        eliminar_temporal(ruta_wav)


def crear_audio(texto: str) -> str | None:
    archivo = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
    archivo.close()

    try:
        gTTS(text=texto, lang="es", slow=False).save(archivo.name)
        return archivo.name
    except Exception as error:
        logger.exception("Error al crear audio: %s", error)
        eliminar_temporal(archivo.name)
        return None


async def responder(
    update: Update,
    texto: str,
    reply_markup=None,
    con_audio: bool = True,
) -> None:
    if not update.message:
        return

    await update.message.reply_text(
        texto,
        reply_markup=reply_markup,
    )

    if not con_audio:
        return

    audio = await asyncio.to_thread(crear_audio, texto)

    try:
        if audio:
            with open(audio, "rb") as archivo:
                await update.message.reply_voice(voice=archivo)
    finally:
        eliminar_temporal(audio)


# ══════════════════════════════════════════════════════════════
# BASE DE DATOS
# ══════════════════════════════════════════════════════════════
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)


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

        CREATE UNIQUE INDEX IF NOT EXISTS idx_medicamento_activo
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
# HORAS Y FECHAS
# ══════════════════════════════════════════════════════════════
NUMEROS_HORA = {
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


def parsear_hora(texto: str) -> str | None:
    texto = texto_sin_puntuacion(texto)

    coincidencia = re.search(
        r"\b([01]?\d|2[0-3])\s*[:h]\s*([0-5]\d)\b",
        texto,
    )

    if coincidencia:
        return (
            f"{int(coincidencia.group(1)):02d}:"
            f"{int(coincidencia.group(2)):02d}"
        )

    coincidencia = re.search(
        r"\b(?:a\s+las|las)\s+(\d{1,2})(?:\s+y\s+(\d{1,2}))?\b",
        texto,
    )

    hora: int | None = None
    minuto = 0

    if coincidencia:
        hora = int(coincidencia.group(1))
        minuto = int(coincidencia.group(2) or 0)
    else:
        for palabra, valor in NUMEROS_HORA.items():
            if re.search(
                rf"\b(?:a\s+las|las)\s+{palabra}\b",
                texto,
            ):
                hora = valor
                break

        if hora is None:
            for palabra, valor in NUMEROS_HORA.items():
                if re.search(
                    rf"\b{palabra}\b"
                    rf"(?:\s+de\s+la\s+(?:manana|tarde|noche|madrugada))?"
                    rf"\s*$",
                    texto,
                ):
                    hora = valor
                    break

    if hora is None:
        return None

    if "media" in texto:
        minuto = 30
    elif "cuarto" in texto:
        minuto = 15

    if "de la tarde" in texto and 1 <= hora <= 11:
        hora += 12
    elif "de la noche" in texto and 1 <= hora <= 11:
        hora += 12
    elif "de la madrugada" in texto and hora == 12:
        hora = 0
    elif re.search(r"\bpm\b", texto) and 1 <= hora <= 11:
        hora += 12
    elif re.search(r"\bam\b", texto) and hora == 12:
        hora = 0

    if hora > 23 or minuto > 59:
        return None

    return f"{hora:02d}:{minuto:02d}"


def parsear_fecha(texto: str) -> str:
    texto = texto_sin_puntuacion(texto)
    hoy = hoy_local()

    coincidencia = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", texto)
    if coincidencia:
        return coincidencia.group(1)

    if "pasado manana" in texto:
        return (hoy + timedelta(days=2)).isoformat()

    if "manana" in texto:
        return (hoy + timedelta(days=1)).isoformat()

    if "hoy" in texto:
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

    for nombre, numero in dias.items():
        if nombre in texto:
            diferencia = (numero - hoy.weekday()) % 7

            if diferencia == 0:
                diferencia = 7

            return (hoy + timedelta(days=diferencia)).isoformat()

    return hoy.isoformat()


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


def medicamento_tomado_hoy(medicamento_id: int) -> bool:
    conexion = conectar_db()
    fila = conexion.execute(
        """
        SELECT id
        FROM tomas
        WHERE medicamento_id = ? AND fecha = ?
        """,
        (medicamento_id, hoy_local().isoformat()),
    ).fetchone()
    conexion.close()
    return fila is not None


def agregar_medicamento(
    nombre: str,
    hora: str,
    dosis: str = "",
) -> str:
    nombre = nombre.strip().title()
    dosis = dosis.strip()

    if not nombre:
        return "No pude identificar el nombre del medicamento."

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
        return f"{nombre} ya está registrado."

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

    sincronizar_alarmas()

    return f"Agregué {nombre} a las {hora}."


def quitar_medicamento(nombre: str) -> str:
    medicamento = buscar_medicamento(nombre)

    if not medicamento:
        return "No encontré ese medicamento."

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

    return f"Quité {medicamento['nombre']} de la lista."


def registrar_toma(nombre: str) -> str:
    medicamento = buscar_medicamento(nombre)

    if not medicamento:
        return "No encontré ese medicamento."

    if medicamento_tomado_hoy(medicamento["id"]):
        return (
            f"{medicamento['nombre']} ya está registrado "
            "como tomado hoy."
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

    cancelar_recordatorios(medicamento["id"])

    return f"Registré que tomaste {medicamento['nombre']} hoy."


def texto_medicamentos() -> str:
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


def extraer_nombre_medicamento_agregar(texto: str) -> str:
    texto = corregir_errores_voz(texto)

    texto = re.sub(
        r"^(?:a\s+)?(?:(?:agregar|agrega|anadir|anade|registrar|registra|programar|programa)\s+)+",
        "",
        texto,
    )

    texto = re.sub(
        r"\b(?:un|una|el|la)\b",
        " ",
        texto,
    )

    texto = re.sub(
        r"\b(?:medicamento|medicina|pastilla)\b",
        " ",
        texto,
        count=1,
    )

    texto = re.split(
        r"\b(?:a\s+las|las)\b",
        texto,
        maxsplit=1,
    )[0]

    texto = re.sub(
        r"\b(?:una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce)"
        r"(?:\s+de\s+la\s+(?:manana|tarde|noche|madrugada))?\s*$",
        "",
        texto,
    )

    texto = re.sub(
        r"\b(?:agregar|agrega|anadir|registrar|programar)\b",
        " ",
        texto,
    )

    texto = re.sub(r"\s+", " ", texto).strip()

    return texto.title()


def extraer_nombre_medicamento_quitar(texto: str) -> str:
    texto = corregir_errores_voz(texto)

    texto = re.sub(
        r"^(?:quitar|quita|eliminar|elimina|borrar|borra|dejar de tomar)\s+",
        "",
        texto,
    )

    texto = re.sub(
        r"\b(?:el|la|un|una|medicamento|medicina|pastilla)\b",
        " ",
        texto,
    )

    return re.sub(r"\s+", " ", texto).strip().title()


# ══════════════════════════════════════════════════════════════
# AGENDA
# ══════════════════════════════════════════════════════════════
def listar_eventos(fecha: str | None = None) -> list[dict]:
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


def agregar_evento(
    descripcion: str,
    fecha: str,
    hora: str,
) -> str:
    descripcion = descripcion.strip().title()

    if not descripcion:
        return "No pude identificar el evento."

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
        f"Agregué {descripcion} para {fecha} "
        f"a las {hora}."
    )


def quitar_evento(busqueda: str) -> str:
    busqueda = normalizar(busqueda)
    coincidencias = [
        evento
        for evento in listar_eventos()
        if busqueda in normalizar(evento["descripcion"])
    ]

    if not coincidencias:
        return "No encontré ese evento."

    evento = coincidencias[0]

    conexion = conectar_db()
    conexion.execute(
        "DELETE FROM agenda WHERE id = ?",
        (evento["id"],),
    )
    conexion.commit()
    conexion.close()

    return f"Quité {evento['descripcion']} de la agenda."


def texto_agenda(fecha: str | None = None) -> str:
    eventos = listar_eventos(fecha)

    if not eventos:
        return (
            f"No tienes eventos para {fecha}."
            if fecha
            else "No tienes eventos próximos."
        )

    partes = ["Tu agenda es:"]

    for evento in eventos[:15]:
        partes.append(
            f"{evento['descripcion']}, "
            f"el {evento['fecha']} a las {evento['hora']}."
        )

    return " ".join(partes)


def extraer_descripcion_evento(texto: str) -> str:
    texto = corregir_errores_voz(texto)

    texto = re.sub(
        r"^(?:agregar|agrega|anadir|anade|registrar|registra|agendar|agenda)\s+",
        "",
        texto,
    )

    texto = re.sub(
        r"\b(?:un|una|el|la|evento|cita)\b",
        " ",
        texto,
    )

    texto = re.split(
        r"\b(?:para hoy|para manana|hoy|manana|lunes|martes|miercoles|jueves|viernes|sabado|domingo|a\s+las|las)\b",
        texto,
        maxsplit=1,
    )[0]

    return re.sub(r"\s+", " ", texto).strip().title()


# ══════════════════════════════════════════════════════════════
# CONTACTOS
# ══════════════════════════════════════════════════════════════
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


def agregar_contacto(
    nombre: str,
    telefono: str,
    relacion: str = "",
    emergencia: bool = False,
) -> str:
    nombre = nombre.strip().title()
    telefono = telefono.strip()

    if not nombre:
        return "No pude identificar el nombre del contacto."

    if not re.fullmatch(r"\+?\d{7,15}", telefono):
        return "El número debe tener entre 7 y 15 dígitos."

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
        return f"Ya existe un contacto llamado {nombre}."

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

    return f"Agregué a {nombre}{extra}."


def quitar_contacto(nombre: str) -> str:
    contacto = buscar_contacto(nombre)

    if not contacto:
        return "No encontré ese contacto."

    conexion = conectar_db()
    conexion.execute(
        "DELETE FROM contactos WHERE id = ?",
        (contacto["id"],),
    )
    conexion.commit()
    conexion.close()

    return f"Quité a {contacto['nombre']} de tus contactos."


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


def extraer_contacto_agregar(
    texto: str,
) -> tuple[str, str, str, bool]:
    texto = corregir_errores_voz(texto)
    emergencia = "emergencia" in texto

    telefono_match = re.search(r"(\+?\d{7,15})", texto)
    telefono = telefono_match.group(1) if telefono_match else ""

    nombre_texto = re.sub(
        r"^(?:agregar|agrega|anadir|anade|registrar|registra)\s+",
        "",
        texto,
    )
    nombre_texto = re.sub(
        r"\b(?:un|una|el|la|contacto|telefono|emergencia)\b",
        " ",
        nombre_texto,
    )
    nombre_texto = re.sub(r"\+?\d{7,15}", " ", nombre_texto)
    nombre_texto = re.sub(r"\s+", " ", nombre_texto).strip()

    return nombre_texto.title(), telefono, "", emergencia


# ══════════════════════════════════════════════════════════════
# NOTIFICACIONES EXTERNAS
# ══════════════════════════════════════════════════════════════
TWILIO_CLIENT = None

if TWILIO_SID and TWILIO_TOKEN and TWILIO_NUMERO:
    try:
        from twilio.rest import Client

        TWILIO_CLIENT = Client(TWILIO_SID, TWILIO_TOKEN)
    except Exception as error:
        logger.exception("Twilio no pudo iniciarse: %s", error)


def llamar(telefono: str, mensaje: str) -> None:
    if not TWILIO_CLIENT:
        logger.info("[SIMULADO] Llamada a %s: %s", telefono, mensaje)
        return

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
    except Exception as error:
        logger.exception("Error de llamada: %s", error)


def enviar_whatsapp(telefono: str, mensaje: str) -> None:
    if not CALLMEBOT_PHONE or not CALLMEBOT_KEY:
        logger.info("[SIMULADO] WhatsApp a %s: %s", telefono, mensaje)
        return

    try:
        requests.get(
            (
                "https://api.callmebot.com/whatsapp.php"
                f"?phone={telefono}"
                f"&text={quote(mensaje)}"
                f"&apikey={CALLMEBOT_KEY}"
            ),
            timeout=15,
        )
    except Exception as error:
        logger.exception("Error de WhatsApp: %s", error)


# ══════════════════════════════════════════════════════════════
# ALARMAS
# ══════════════════════════════════════════════════════════════
SCHEDULER = BackgroundScheduler(timezone=ZONA_HORARIA_NOMBRE)
BOT_REF = {
    "app": None,
    "loop": None,
}


def ejecutar_en_loop(corutina) -> None:
    loop = BOT_REF.get("loop")

    if loop and loop.is_running():
        asyncio.run_coroutine_threadsafe(corutina, loop)
    else:
        logger.error("No hay loop activo para enviar mensajes.")


def id_alarma(medicamento_id: int, chat_id: int) -> str:
    return f"med_{medicamento_id}_{chat_id}"


def id_recordatorio(medicamento_id: int, chat_id: int) -> str:
    return f"rec_{medicamento_id}_{chat_id}"


def eliminar_alarmas_medicamento(medicamento_id: int) -> None:
    for trabajo in SCHEDULER.get_jobs():
        if (
            trabajo.id.startswith(f"med_{medicamento_id}_")
            or trabajo.id.startswith(f"rec_{medicamento_id}_")
        ):
            try:
                trabajo.remove()
            except Exception:
                pass


def cancelar_recordatorios(medicamento_id: int) -> None:
    for chat_id in USUARIOS_ALARMA:
        try:
            SCHEDULER.remove_job(
                id_recordatorio(medicamento_id, chat_id)
            )
        except Exception:
            pass


def disparar_alarma(medicamento_id: int, chat_id: int) -> None:
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

    app = BOT_REF.get("app")
    audio = crear_audio(texto)

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
        args=[medicamento_id],
        id=id_recordatorio(medicamento_id, chat_id),
        replace_existing=True,
    )


def aviso_no_confirmado(medicamento_id: int) -> None:
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

    for medicamento in listar_medicamentos():
        hora, minuto = medicamento["hora"].split(":")

        for chat_id in USUARIOS_ALARMA:
            SCHEDULER.add_job(
                disparar_alarma,
                CronTrigger(
                    hour=int(hora),
                    minute=int(minuto),
                    timezone=ZONA_HORARIA_NOMBRE,
                ),
                args=[medicamento["id"], chat_id],
                id=id_alarma(medicamento["id"], chat_id),
                replace_existing=True,
            )


# ══════════════════════════════════════════════════════════════
# NOTICIAS
# ══════════════════════════════════════════════════════════════
def obtener_noticias(limite: int = 5) -> list[str]:
    try:
        respuesta = requests.get(
            RSS_NOTICIAS,
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        respuesta.raise_for_status()

        raiz = ET.fromstring(respuesta.content)
        noticias: list[str] = []

        for item in raiz.findall(".//item"):
            titulo = html.unescape(
                item.findtext("title", "")
            ).strip()

            if titulo:
                titulo = re.sub(r"\s+-\s+[^-]+$", "", titulo)
                noticias.append(titulo)

            if len(noticias) >= limite:
                break

        return noticias
    except Exception as error:
        logger.exception("Error al obtener noticias: %s", error)
        return []


def texto_noticias() -> str:
    noticias = obtener_noticias()

    if not noticias:
        return "No pude consultar las noticias en este momento."

    partes = ["Estas son algunas noticias recientes:"]

    for indice, noticia in enumerate(noticias, start=1):
        partes.append(f"{indice}. {noticia}.")

    return " ".join(partes)


# ══════════════════════════════════════════════════════════════
# INTENCIONES
# ══════════════════════════════════════════════════════════════
def contiene(texto: str, opciones: Iterable[str]) -> bool:
    return any(opcion in texto for opcion in opciones)


def parece_agregar_medicamento(texto: str) -> bool:
    texto = corregir_errores_voz(texto)

    tiene_verbo = contiene(
        texto,
        [
            "agregar",
            "agrega",
            "anadir",
            "registrar",
            "programar",
        ],
    )

    otra_categoria = contiene(
        texto,
        [
            "contacto",
            "telefono",
            "evento",
            "cita",
            "agenda",
        ],
    )

    medicamento = contiene(
        texto,
        [
            "medicamento",
            "medicina",
            "pastilla",
        ],
    )

    return (
        tiene_verbo
        and not otra_categoria
        and (
            medicamento
            or parsear_hora(texto) is not None
        )
    )


def procesar_comando(texto: str) -> str:
    original = corregir_errores_voz(texto)
    t = normalizar(original)

    if not t:
        return "No escuché ningún mensaje."

    if t in {"hora", "que hora es", "que hora"}:
        return f"Son las {ahora_local().strftime('%I:%M %p')}."

    if contiene(t, ["que dia", "fecha", "hoy es"]):
        return f"Hoy es {ahora_local().strftime('%d/%m/%Y')}."

    if contiene(
        t,
        [
            "noticias",
            "noticia",
            "que esta pasando",
            "entretenimiento",
        ],
    ):
        return texto_noticias()

    if parece_agregar_medicamento(original):
        hora = parsear_hora(original)
        nombre = extraer_nombre_medicamento_agregar(original)

        if not nombre:
            return (
                "Dime el nombre del medicamento. "
                "Por ejemplo: agregar Aspirina a las ocho."
            )

        if not hora:
            return (
                "Dime la hora. Por ejemplo: "
                f"agregar {nombre} a las ocho."
            )

        return agregar_medicamento(nombre, hora)

    if contiene(
        t,
        [
            "quitar medicamento",
            "quita medicamento",
            "eliminar medicamento",
            "elimina medicamento",
            "borrar medicamento",
            "dejar de tomar",
        ],
    ):
        nombre = extraer_nombre_medicamento_quitar(original)

        if not nombre:
            return "Dime qué medicamento quieres quitar."

        return quitar_medicamento(nombre)

    if contiene(
        t,
        [
            "ya tome",
            "ya me tome",
            "registrar toma",
            "marcar como tomado",
        ],
    ):
        medicamentos = listar_medicamentos()

        if not medicamentos:
            return "No tienes medicamentos registrados."

        for medicamento in medicamentos:
            nombre_n = normalizar(medicamento["nombre"])

            if nombre_n in t:
                return registrar_toma(medicamento["nombre"])

        pendientes = [
            medicamento
            for medicamento in medicamentos
            if not medicamento_tomado_hoy(medicamento["id"])
        ]

        if len(pendientes) == 1:
            return registrar_toma(pendientes[0]["nombre"])

        if pendientes:
            nombres = ", ".join(
                medicamento["nombre"]
                for medicamento in pendientes
            )
            return f"Dime cuál tomaste. Los pendientes son: {nombres}."

        return "Todos los medicamentos de hoy ya están tomados."

    if contiene(
        t,
        [
            "mis medicamentos",
            "lista de medicamentos",
            "que medicamentos",
            "que medicinas",
            "que pastillas",
            "debo tomar",
        ],
    ):
        return texto_medicamentos()

    if contiene(
        t,
        [
            "agregar evento",
            "agrega evento",
            "agendar evento",
            "agregar cita",
            "agrega cita",
            "agendar cita",
        ],
    ):
        hora = parsear_hora(original)
        fecha = parsear_fecha(original)
        descripcion = extraer_descripcion_evento(original)

        if not descripcion:
            return "Dime el nombre del evento."

        if not hora:
            return "Dime la hora del evento."

        return agregar_evento(descripcion, fecha, hora)

    if contiene(
        t,
        [
            "quitar evento",
            "quita evento",
            "eliminar evento",
            "elimina evento",
            "quitar cita",
            "eliminar cita",
        ],
    ):
        busqueda = re.sub(
            r"^(?:quitar|quita|eliminar|elimina)\s+",
            "",
            t,
        )
        busqueda = re.sub(
            r"\b(?:evento|cita)\b",
            " ",
            busqueda,
        )
        busqueda = re.sub(r"\s+", " ", busqueda).strip()

        if not busqueda:
            return "Dime qué evento quieres quitar."

        return quitar_evento(busqueda)

    if contiene(
        t,
        [
            "mi agenda",
            "agenda de hoy",
            "agenda de manana",
            "mis eventos",
            "que tengo hoy",
            "que tengo manana",
        ],
    ):
        if "manana" in t:
            fecha = (hoy_local() + timedelta(days=1)).isoformat()
        elif "hoy" in t:
            fecha = hoy_local().isoformat()
        else:
            fecha = None

        return texto_agenda(fecha)

    if contiene(
        t,
        [
            "agregar contacto",
            "agrega contacto",
            "anadir contacto",
            "registrar contacto",
        ],
    ):
        nombre, telefono, relacion, emergencia = (
            extraer_contacto_agregar(original)
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
        )

    if contiene(
        t,
        [
            "quitar contacto",
            "quita contacto",
            "eliminar contacto",
            "elimina contacto",
        ],
    ):
        nombre = re.sub(
            r"^(?:quitar|quita|eliminar|elimina)\s+",
            "",
            t,
        )
        nombre = re.sub(r"\bcontacto\b", " ", nombre)
        nombre = re.sub(r"\s+", " ", nombre).strip()

        if not nombre:
            return "Dime qué contacto quieres quitar."

        return quitar_contacto(nombre)

    if contiene(
        t,
        [
            "mis contactos",
            "lista de contactos",
            "que contactos",
        ],
    ):
        return texto_contactos()

    if t.startswith(("llama a ", "llamar a ", "llamale a ")):
        nombre = re.sub(
            r"^(?:llama a|llamar a|llamale a)\s+",
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

        return f"Inicié la llamada a {contacto['nombre']}."

    if contiene(
        t,
        [
            "emergencia",
            "auxilio",
            "socorro",
            "me cai",
            "me siento mal",
            "necesito ayuda",
        ],
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
            return "No tienes un contacto de emergencia configurado."

        mensaje = (
            f"EMERGENCIA: {NOMBRE_USUARIO} "
            "necesita ayuda urgente."
        )

        enviar_whatsapp(contacto["telefono"], mensaje)
        llamar(contacto["telefono"], mensaje)

        return f"Envié una alerta a {contacto['nombre']}."

    if contiene(t, ["ayuda", "comandos", "que puedo hacer"]):
        return (
            "Puedes decir: agregar medicamento Aspirina a las ocho "
            "de la mañana; quitar medicamento Aspirina; "
            "mis medicamentos; ya tomé Aspirina; "
            "agregar cita médica para mañana a las tres de la tarde; "
            "quitar evento cita médica; mi agenda; "
            "agregar contacto Ana teléfono 3001234567; "
            "quitar contacto Ana; mis contactos; noticias; "
            "emergencia; o qué hora es."
        )

    return "No entendí el mensaje. Di ayuda para escuchar ejemplos."


# ══════════════════════════════════════════════════════════════
# SEGURIDAD Y MENÚ
# ══════════════════════════════════════════════════════════════
def usuario_autorizado(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat and chat.id in USUARIOS_AUTORIZADOS)


def mensaje_no_autorizado(chat_id: int) -> str:
    return (
        "Todavía no estás autorizado para utilizar este asistente.\n\n"
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
            mensaje_no_autorizado(update.effective_chat.id)
        )
        return

    hora = ahora_local().hour

    saludo = (
        "Buenos días"
        if hora < 12
        else "Buenas tardes"
        if hora < 18
        else "Buenas noches"
    )

    texto = (
        f"{saludo}, {NOMBRE_USUARIO}. "
        f"Son las {ahora_local().strftime('%I:%M %p')}. "
        "El asistente está listo. "
        "Puedes escribir, hablar o usar los botones."
    )

    await responder(update, texto, reply_markup=MENU)


async def manejar_texto(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not usuario_autorizado(update):
        await update.message.reply_text(
            mensaje_no_autorizado(update.effective_chat.id)
        )
        return

    texto = update.message.text or ""

    if texto == "Mis medicamentos":
        respuesta = texto_medicamentos()
    elif texto == "Mi agenda":
        respuesta = texto_agenda()
    elif texto == "Mis contactos":
        respuesta = texto_contactos()
    elif texto == "Noticias":
        respuesta = texto_noticias()
    elif texto == "EMERGENCIA":
        respuesta = procesar_comando("emergencia")
    elif texto == "Ayuda":
        respuesta = procesar_comando("ayuda")
    else:
        respuesta = procesar_comando(texto)

    await responder(
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
            mensaje_no_autorizado(update.effective_chat.id)
        )
        return

    voz = update.message.voice or update.message.audio

    if not voz:
        await update.message.reply_text("No encontré el audio.")
        return

    ruta = (
        f"/tmp/audio_{update.effective_chat.id}_{voz.file_id}.ogg"
    )

    await update.message.reply_text(
        "Escuchando y procesando tu mensaje..."
    )

    try:
        archivo = await context.bot.get_file(voz.file_id)
        await archivo.download_to_drive(ruta)

        texto = await asyncio.to_thread(
            transcribir_audio,
            ruta,
        )

        if not texto:
            await responder(
                update,
                "No escuché bien. Intenta nuevamente.",
                reply_markup=MENU,
            )
            return

        await update.message.reply_text(
            f'Escuché: "{texto}"'
        )

        respuesta = procesar_comando(texto)

        await responder(
            update,
            respuesta,
            reply_markup=MENU,
        )
    except Exception as error:
        logger.exception("Error al procesar audio: %s", error)
        await responder(
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

    if not query.data or not query.data.startswith("tomado:"):
        return

    try:
        medicamento_id = int(query.data.split(":", 1)[1])
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

    mensaje = registrar_toma(medicamento["nombre"])

    await query.edit_message_text(mensaje)

    audio = await asyncio.to_thread(crear_audio, mensaje)

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


async def configurar_aplicacion(app: Application) -> None:
    BOT_REF["app"] = app
    BOT_REF["loop"] = asyncio.get_running_loop()
    sincronizar_alarmas()


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════
def main() -> None:
    if not TELEGRAM_TOKEN:
        raise RuntimeError("Falta TELEGRAM_TOKEN en Railway.")

    if not USUARIOS_AUTORIZADOS:
        logger.warning(
            "USUARIOS_AUTORIZADOS está vacío. Solo /id funcionará."
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

    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_start))
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
        CallbackQueryHandler(manejar_callback)
    )
    app.add_error_handler(manejar_error)

    BOT_REF["app"] = app

    logger.info(
        "Bot iniciado. Zona horaria: %s",
        ZONA_HORARIA_NOMBRE,
    )

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
