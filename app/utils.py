from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime

from app.config import Settings


def now_local(settings: Settings) -> datetime:
    return datetime.now(settings.timezone)


def today_local(settings: Settings) -> date:
    return now_local(settings).date()


def normalize(text: str) -> str:
    text = text.lower().strip()
    text = "".join(
        c
        for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"\s+", " ", text)


def clean_spoken_text(text: str) -> str:
    text = normalize(text)
    text = re.sub(r"[,.;:!?¡¿]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("madrugrada", "madrugada")
    text = text.replace("madrujada", "madrugada")
    text = re.sub(r"\balas\b", "a las", text)
    return text
