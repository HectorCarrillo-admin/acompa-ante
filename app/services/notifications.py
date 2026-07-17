from __future__ import annotations

import html
import logging
from urllib.parse import quote

import requests

from app.config import Settings

logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.twilio_client = None

        if (
            settings.twilio_sid
            and settings.twilio_token
            and settings.twilio_numero
        ):
            try:
                from twilio.rest import Client
                self.twilio_client = Client(
                    settings.twilio_sid,
                    settings.twilio_token,
                )
            except Exception as exc:
                logger.exception("Twilio no pudo iniciarse: %s", exc)

    def call(self, phone: str, message: str) -> None:
        if not self.twilio_client:
            logger.info("[SIMULADO] Llamada a %s: %s", phone, message)
            return

        try:
            twiml = (
                "<Response>"
                f"<Say language='es-MX'>{html.escape(message)}</Say>"
                "</Response>"
            )
            self.twilio_client.calls.create(
                twiml=twiml,
                to=phone,
                from_=self.settings.twilio_numero,
            )
        except Exception as exc:
            logger.exception("Error de llamada: %s", exc)

    def whatsapp(self, phone: str, message: str) -> None:
        if not self.settings.callmebot_phone or not self.settings.callmebot_key:
            logger.info("[SIMULADO] WhatsApp a %s: %s", phone, message)
            return

        try:
            requests.get(
                (
                    "https://api.callmebot.com/whatsapp.php"
                    f"?phone={phone}"
                    f"&text={quote(message)}"
                    f"&apikey={self.settings.callmebot_key}"
                ),
                timeout=15,
            )
        except Exception as exc:
            logger.exception("Error de WhatsApp: %s", exc)
