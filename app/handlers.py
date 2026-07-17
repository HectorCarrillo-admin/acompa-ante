from __future__ import annotations

import asyncio
from pathlib import Path

from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.alarms import AlarmManager
from app.config import Settings
from app.parser import CommandParser
from app.services.agenda import AgendaService
from app.services.contacts import ContactService
from app.services.medications import MedicationService
from app.services.news import NewsService
from app.voice import VoiceService


MENU = ReplyKeyboardMarkup(
    [
        ["Mis medicamentos", "Mi agenda"],
        ["Mis contactos", "Noticias"],
        ["Noticias Colombia", "Noticias Mundo"],
        ["Noticias Salud", "Noticias Deportes"],
        ["Noticias Tecnología", "Noticias Economía"],
        ["Buenas noticias", "EMERGENCIA"],
        ["Ayuda"],
    ],
    resize_keyboard=True,
)


class BotHandlers:
    def __init__(
        self,
        settings: Settings,
        voice: VoiceService,
        meds: MedicationService,
        agenda: AgendaService,
        contacts: ContactService,
        news: NewsService,
        parser: CommandParser,
        alarms: AlarmManager,
    ) -> None:
        self.settings = settings
        self.voice = voice
        self.meds = meds
        self.agenda = agenda
        self.contacts = contacts
        self.news = news
        self.parser = parser
        self.alarms = alarms

    def authorized(self, update: Update) -> bool:
        chat = update.effective_chat
        return bool(chat and chat.id in self.settings.usuarios_autorizados)

    def unauthorized_text(self, chat_id: int) -> str:
        return (
            "Todavía no estás autorizado para utilizar este asistente.\n\n"
            f"Tu Chat ID es:\n{chat_id}\n\n"
            "Envía este número al administrador."
        )

    async def cmd_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        status = (
            "Tu acceso ya está autorizado."
            if chat_id in self.settings.usuarios_autorizados
            else "Tu acceso todavía no está autorizado. Envía este número al administrador."
        )
        await update.message.reply_text(
            f"Tu Chat ID es:\n\n{chat_id}\n\n{status}"
        )

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.authorized(update):
            await update.message.reply_text(
                self.unauthorized_text(update.effective_chat.id)
            )
            return

        from app.utils import now_local
        now = now_local(self.settings)
        greeting = (
            "Buenos días" if now.hour < 12
            else "Buenas tardes" if now.hour < 18
            else "Buenas noches"
        )
        text = (
            f"{greeting}, {self.settings.nombre_usuario}. "
            f"Son las {now.strftime('%I:%M %p')}. "
            "Puedes escribir, hablar o usar los botones."
        )
        await self.voice.reply(update, text, reply_markup=MENU)

    async def text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.authorized(update):
            await update.message.reply_text(
                self.unauthorized_text(update.effective_chat.id)
            )
            return

        text = update.message.text or ""

        direct = {
            "Mis medicamentos": self.meds.summary,
            "Mi agenda": self.agenda.summary,
            "Mis contactos": self.contacts.summary,
            "Noticias": lambda: self.news.summary("colombia"),
            "Noticias Colombia": lambda: self.news.summary("colombia"),
            "Noticias Mundo": lambda: self.news.summary("mundo"),
            "Noticias Salud": lambda: self.news.summary("salud"),
            "Noticias Deportes": lambda: self.news.summary("deportes"),
            "Noticias Tecnología": lambda: self.news.summary("tecnologia"),
            "Noticias Economía": lambda: self.news.summary("economia"),
            "Buenas noticias": lambda: self.news.summary("buenas"),
        }

        if text in direct:
            response = await asyncio.to_thread(direct[text])
        else:
            response = await asyncio.to_thread(self.parser.process, text)

        await self.voice.reply(update, response, reply_markup=MENU)

    async def voice_message(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        if not self.authorized(update):
            await update.message.reply_text(
                self.unauthorized_text(update.effective_chat.id)
            )
            return

        voice = update.message.voice or update.message.audio
        if not voice:
            await update.message.reply_text("No encontré el audio.")
            return

        temp_path = f"/tmp/audio_{update.effective_chat.id}_{voice.file_id}.ogg"
        await update.message.reply_text("Escuchando y procesando tu mensaje...")

        try:
            telegram_file = await context.bot.get_file(voice.file_id)
            await telegram_file.download_to_drive(temp_path)
            text = await asyncio.to_thread(self.voice.transcribe, temp_path)

            if not text:
                await self.voice.reply(
                    update,
                    "No escuché bien. Intenta nuevamente.",
                    reply_markup=MENU,
                )
                return

            await update.message.reply_text(f'Escuché: "{text}"')
            response = await asyncio.to_thread(self.parser.process, text)
            await self.voice.reply(update, response, reply_markup=MENU)
        finally:
            self.voice.remove_temp(temp_path)

    async def callback(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        query = update.callback_query
        if not query:
            return

        if not self.authorized(update):
            await query.answer("No tienes autorización.", show_alert=True)
            return

        await query.answer()

        if not query.data or not query.data.startswith("tomado:"):
            return

        try:
            med_id = int(query.data.split(":", 1)[1])
        except ValueError:
            await query.edit_message_text("No pude interpretar la confirmación.")
            return

        med = next((m for m in self.meds.list() if m["id"] == med_id), None)
        if not med:
            await query.edit_message_text("Ese medicamento ya no está activo.")
            return

        message = self.meds.mark_taken(med["nombre"])
        await query.edit_message_text(message)

        audio = await asyncio.to_thread(self.voice.synthesize, message)
        try:
            if audio:
                with open(audio, "rb") as handle:
                    await context.bot.send_voice(
                        chat_id=query.message.chat_id,
                        voice=handle,
                    )
        finally:
            self.voice.remove_temp(audio)
