from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


def parse_ids(raw: str) -> set[int]:
    result: set[int] = set()
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            result.add(int(item))
        except ValueError:
            logger.warning("Chat ID inválido ignorado: %s", item)
    return result


@dataclass(frozen=True)
class Settings:
    telegram_token: str
    nombre_usuario: str
    db_path: str
    timezone_name: str
    timezone: ZoneInfo

    usuarios_autorizados: set[int]
    usuarios_alarma: set[int]
    familiar_chat_id: int | None

    modelo_voz: str
    whisper_cpu_threads: int
    whisper_beam_size: int

    rss_colombia: str
    rss_mundo: str
    rss_salud: str
    rss_deportes: str
    rss_tecnologia: str
    rss_economia: str
    rss_buenas: str

    ciudad_clima: str
    latitud_clima: float | None
    longitud_clima: float | None

    twilio_sid: str
    twilio_token: str
    twilio_numero: str
    telefono_usuario: str
    callmebot_phone: str
    callmebot_key: str


def _float_or_none(value: str) -> float | None:
    value = value.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def get_settings() -> Settings:
    timezone_name = os.getenv("ZONA_HORARIA", "America/Bogota").strip()
    try:
        timezone = ZoneInfo(timezone_name)
    except Exception:
        timezone_name = "America/Bogota"
        timezone = ZoneInfo(timezone_name)

    authorized = parse_ids(os.getenv("USUARIOS_AUTORIZADOS", ""))
    alarm_users = parse_ids(os.getenv("USUARIOS_ALARMA", "")) or set(authorized)

    family_raw = os.getenv("FAMILIAR_CHAT_ID", "").strip()
    try:
        family_id = int(family_raw) if family_raw else None
    except ValueError:
        family_id = None

    google_news = (
        "https://news.google.com/rss/search?"
        "q={query}&hl=es-419&gl=CO&ceid=CO:es-419"
    )

    return Settings(
        telegram_token=os.getenv("TELEGRAM_TOKEN", "").strip(),
        nombre_usuario=os.getenv("NOMBRE_USUARIO", "Usuario").strip(),
        db_path=os.getenv("DB_PATH", "/data/acompanante.db").strip(),
        timezone_name=timezone_name,
        timezone=timezone,
        usuarios_autorizados=authorized,
        usuarios_alarma=alarm_users,
        familiar_chat_id=family_id,
        modelo_voz=os.getenv("MODELO_VOZ", "base").strip(),
        whisper_cpu_threads=int(os.getenv("WHISPER_CPU_THREADS", "2")),
        whisper_beam_size=int(os.getenv("WHISPER_BEAM_SIZE", "3")),
        rss_colombia=os.getenv(
            "RSS_NOTICIAS_COLOMBIA",
            google_news.format(query="Colombia+when:1d"),
        ).strip(),
        rss_mundo=os.getenv(
            "RSS_NOTICIAS_MUNDO",
            google_news.format(query="mundo+internacional+when:1d"),
        ).strip(),
        rss_salud=os.getenv(
            "RSS_NOTICIAS_SALUD",
            google_news.format(query="salud+when:2d"),
        ).strip(),
        rss_deportes=os.getenv(
            "RSS_NOTICIAS_DEPORTES",
            google_news.format(query="deportes+Colombia+when:1d"),
        ).strip(),
        rss_tecnologia=os.getenv(
            "RSS_NOTICIAS_TECNOLOGIA",
            google_news.format(query="tecnologia+when:2d"),
        ).strip(),
        rss_economia=os.getenv(
            "RSS_NOTICIAS_ECONOMIA",
            google_news.format(query="economia+Colombia+when:1d"),
        ).strip(),
        rss_buenas=os.getenv(
            "RSS_BUENAS_NOTICIAS",
            google_news.format(query='"buenas+noticias"+OR+solidaridad+when:7d'),
        ).strip(),
        ciudad_clima=os.getenv("CIUDAD_CLIMA", "Bogotá").strip(),
        latitud_clima=_float_or_none(os.getenv("LATITUD_CLIMA", "")),
        longitud_clima=_float_or_none(os.getenv("LONGITUD_CLIMA", "")),
        twilio_sid=os.getenv("TWILIO_SID", "").strip(),
        twilio_token=os.getenv("TWILIO_TOKEN", "").strip(),
        twilio_numero=os.getenv("TWILIO_NUMERO", "").strip(),
        telefono_usuario=os.getenv("TELEFONO_USUARIO", "").strip(),
        callmebot_phone=os.getenv("CALLMEBOT_PHONE", "").strip(),
        callmebot_key=os.getenv("CALLMEBOT_KEY", "").strip(),
    )
