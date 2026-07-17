from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime

import requests

from app.config import Settings
from app.utils import now_local

logger = logging.getLogger(__name__)


@dataclass
class Headline:
    title: str
    published: str | None = None


class NewsService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.feeds = {
            "colombia": settings.rss_colombia,
            "mundo": settings.rss_mundo,
            "salud": settings.rss_salud,
            "deportes": settings.rss_deportes,
            "tecnologia": settings.rss_tecnologia,
            "economia": settings.rss_economia,
            "buenas": settings.rss_buenas_noticias,
        }

    @staticmethod
    def _clean_title(title: str) -> str:
        title = html.unescape(title).strip()
        return re.sub(r"\s+-\s+[^-]+$", "", title)

    def get(self, category: str, limit: int = 5) -> list[Headline]:
        url = self.feeds.get(category, self.feeds["colombia"])
        try:
            response = requests.get(
                url,
                timeout=15,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            response.raise_for_status()
            root = ET.fromstring(response.content)

            headlines: list[Headline] = []
            for item in root.findall(".//item"):
                title = self._clean_title(item.findtext("title", ""))
                if not title:
                    continue
                headlines.append(
                    Headline(
                        title=title,
                        published=item.findtext("pubDate"),
                    )
                )
                if len(headlines) >= limit:
                    break
            return headlines
        except Exception as exc:
            logger.exception("Error consultando noticias: %s", exc)
            return []

    def summary(self, category: str = "colombia") -> str:
        category_names = {
            "colombia": "Colombia",
            "mundo": "el mundo",
            "salud": "salud",
            "deportes": "deportes",
            "tecnologia": "tecnología",
            "economia": "economía",
            "buenas": "buenas noticias",
        }
        headlines = self.get(category)
        if not headlines:
            return "No pude consultar las noticias en este momento."

        date_text = now_local(self.settings).strftime("%d/%m/%Y")
        parts = [
            f"Noticias recientes de {category_names.get(category, category)}, "
            f"consultadas el {date_text}:"
        ]
        for index, headline in enumerate(headlines, start=1):
            parts.append(f"{index}. {headline.title}.")
        return " ".join(parts)
