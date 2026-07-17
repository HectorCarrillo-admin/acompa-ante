from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


def parse_ids(value: str) -> set[int]:
    ids: set[int] = set()
    for fragment in value.split(","):
        fragment = fragment.strip()
        if not fragment:
            continue
        try:
            ids.add(int(fragment))
        except ValueError:
            logger.warning("Chat ID inválido ignorado: %s", fragment)
    return ids


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
    rss_buenas_noticias: str

    twilio_sid: str
    twilio_token: str
    twilio_numero: str
    telefono_usuario: str
    callmebot_phone: str
    callmebot_key: str


def get_settings() -> Settings:
    timezone_name = os.getenv("ZONA_HORARIA", "America/Bogota").strip()
    try:
        timezone = ZoneInfo(timezone_name)
    except Exception:
        timezone_name = "America/Bogota"
        timezone = ZoneInfo(timezone_name)

    usuarios_autorizados = parse_ids(
        os.getenv("USUARIOS_AUTORIZADOS", "")
    )
    usuarios_alarma = parse_ids(
        os.getenv("USUARIOS_ALARMA", "")
    ) or set(usuarios_autorizados)

    familiar_raw = os.getenv("FAMILIAR_CHAT_ID", "").strip()
    try:
        familiar_chat_id = int(familiar_raw) if familiar_raw else None
    except ValueError:
        familiar_chat_id = None

    base = "https://news.google.com/rss/search?q={query}&hl=es-419&gl=CO&ceid=CO:es-419"

    return Settings(
        telegram_token=os.getenv("TELEGRAM_TOKEN", "").strip(),
        nombre_usuario=os.getenv("NOMBRE_USUARIO", "Usuario").strip(),
        db_path=os.getenv("DB_PATH", "/data/acompanante.db").strip(),
        timezone_name=timezone_name,
        timezone=timezone,
        usuarios_autorizados=usuarios_autorizados,
        usuarios_alarma=usuarios_alarma,
        familiar_chat_id=familiar_chat_id,
        modelo_voz=os.getenv("MODELO_VOZ", "base").strip(),
        whisper_cpu_threads=int(os.getenv("WHISPER_CPU_THREADS", "2")),
        whisper_beam_size=int(os.getenv("WHISPER_BEAM_SIZE", "3")),
        rss_colombia=os.getenv(
            "RSS_NOTICIAS_COLOMBIA",
            base.format(query="Colombia+when:1d"),
        ).strip(),
        rss_mundo=os.getenv(
            "RSS_NOTICIAS_MUNDO",
            base.format(query="mundo+internacional+when:1d"),
        ).strip(),
        rss_salud=os.getenv(
            "RSS_NOTICIAS_SALUD",
            base.format(query="salud+when:2d"),
        ).strip(),
        rss_deportes=os.getenv(
            "RSS_NOTICIAS_DEPORTES",
            base.format(query="deportes+Colombia+when:1d"),
        ).strip(),
        rss_tecnologia=os.getenv(
            "RSS_NOTICIAS_TECNOLOGIA",
            base.format(query="tecnologia+when:2d"),
        ).strip(),
        rss_economia=os.getenv(
            "RSS_NOTICIAS_ECONOMIA",
            base.format(query="economia+Colombia+when:1d"),
        ).strip(),
        rss_buenas_noticias=os.getenv(
            "RSS_BUENAS_NOTICIAS",
            base.format(query='"buenas+noticias"+OR+solidaridad+when:7d'),
        ).strip(),
        twilio_sid=os.getenv("TWILIO_SID", "").strip(),
        twilio_token=os.getenv("TWILIO_TOKEN", "").strip(),
        twilio_numero=os.getenv("TWILIO_NUMERO", "").strip(),
        telefono_usuario=os.getenv("TELEFONO_USUARIO", "").strip(),
        callmebot_phone=os.getenv("CALLMEBOT_PHONE", "").strip(),
        callmebot_key=os.getenv("CALLMEBOT_KEY", "").strip(),
    )
