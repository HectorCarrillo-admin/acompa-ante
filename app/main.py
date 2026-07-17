from __future__ import annotations

import asyncio
import logging

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from app.alarms import AlarmManager
from app.config import get_settings
from app.db import Database
from app.handlers import BotHandlers
from app.parser import CommandParser
from app.services.agenda import AgendaService
from app.services.contacts import ContactService
from app.services.medications import MedicationService
from app.services.news import NewsService
from app.services.notifications import NotificationService
from app.services.weather import WeatherService
from app.voice import VoiceService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    settings = get_settings()

    if not settings.telegram_token:
        raise RuntimeError("Falta TELEGRAM_TOKEN en Railway.")

    db = Database(settings)
    db.initialize()

    voice = VoiceService(settings)
    meds = MedicationService(db, settings)
    agenda = AgendaService(db, settings)
    contacts = ContactService(db, settings)
    news = NewsService(settings)
    weather = WeatherService(settings)
    notifications = NotificationService(settings)

    alarms = AlarmManager(
        settings,
        meds,
        contacts,
        notifications,
        voice,
    )

    parser = CommandParser(
        settings,
        meds,
        agenda,
        contacts,
        news,
        weather,
        notifications,
    )

    handlers = BotHandlers(
        settings,
        voice,
        meds,
        agenda,
        contacts,
        news,
        weather,
        parser,
        alarms,
    )

    alarms.start()

    async def post_init(app: Application) -> None:
        alarms.bind(app, asyncio.get_running_loop())

    app = (
        Application.builder()
        .token(settings.telegram_token)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("id", handlers.cmd_id))
    app.add_handler(CommandHandler("start", handlers.cmd_start))
    app.add_handler(CommandHandler("menu", handlers.cmd_start))
    app.add_handler(
        MessageHandler(
            filters.VOICE | filters.AUDIO,
            handlers.voice_message,
        )
    )
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handlers.text,
        )
    )
    app.add_handler(CallbackQueryHandler(handlers.callback))

    logger.info("Bot iniciado.")
    app.run_polling(drop_pending_updates=True)
