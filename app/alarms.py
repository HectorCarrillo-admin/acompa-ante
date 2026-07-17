from __future__ import annotations

import asyncio
from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.config import Settings
from app.services.contacts import ContactService
from app.services.medications import MedicationService
from app.services.notifications import NotificationService
from app.utils import now_local
from app.voice import VoiceService


class AlarmManager:
    def __init__(
        self,
        settings: Settings,
        meds: MedicationService,
        contacts: ContactService,
        notifications: NotificationService,
        voice: VoiceService,
    ) -> None:
        self.settings = settings
        self.meds = meds
        self.contacts = contacts
        self.notifications = notifications
        self.voice = voice

        self.scheduler = BackgroundScheduler(
            timezone=settings.timezone_name
        )
        self.app = None
        self.loop = None

        meds.on_changed = self.sync
        meds.on_taken = self.cancel_reminders

    def start(self) -> None:
        if not self.scheduler.running:
            self.scheduler.start()

    def bind(self, app, loop) -> None:
        self.app = app
        self.loop = loop
        self.sync()

    def submit(self, coroutine) -> None:
        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(coroutine, self.loop)

    def cancel_reminders(self, med_id: int) -> None:
        for chat_id in self.settings.usuarios_alarma:
            try:
                self.scheduler.remove_job(f"rec_{med_id}_{chat_id}")
            except Exception:
                pass

    def sync(self) -> None:
        for job in self.scheduler.get_jobs():
            if job.id.startswith("med_"):
                job.remove()

        for med in self.meds.list():
            hour, minute = med["hora"].split(":")
            for chat_id in self.settings.usuarios_alarma:
                self.scheduler.add_job(
                    self.fire,
                    CronTrigger(
                        hour=int(hour),
                        minute=int(minute),
                        timezone=self.settings.timezone_name,
                    ),
                    args=[med["id"], chat_id],
                    id=f"med_{med['id']}_{chat_id}",
                    replace_existing=True,
                )

    def fire(self, med_id: int, chat_id: int) -> None:
        med = next((m for m in self.meds.list() if m["id"] == med_id), None)
        if not med or self.meds.taken_today(med_id):
            return

        text = (
            f"{self.settings.nombre_usuario}, es hora de tomar "
            f"{med['nombre']}."
        )
        if med["dosis"]:
            text += f" La dosis es {med['dosis']}."

        keyboard = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton(
                    f"Ya tomé {med['nombre']}",
                    callback_data=f"tomado:{med_id}",
                )
            ]]
        )

        audio = self.voice.synthesize(text)

        if self.app:
            async def send() -> None:
                try:
                    await self.app.bot.send_message(
                        chat_id=chat_id,
                        text=f"⏰ {text}",
                        reply_markup=keyboard,
                    )
                    if audio:
                        with open(audio, "rb") as handle:
                            await self.app.bot.send_voice(
                                chat_id=chat_id,
                                voice=handle,
                            )
                finally:
                    self.voice.remove_temp(audio)

            self.submit(send())

        self.scheduler.add_job(
            self.warn_unconfirmed,
            "date",
            run_date=now_local(self.settings) + timedelta(minutes=15),
            args=[med_id],
            id=f"rec_{med_id}_{chat_id}",
            replace_existing=True,
        )

    def warn_unconfirmed(self, med_id: int) -> None:
        if self.meds.taken_today(med_id):
            return

        med = next((m for m in self.meds.list() if m["id"] == med_id), None)
        if not med:
            return

        message = (
            f"{self.settings.nombre_usuario} no confirmó que tomó "
            f"{med['nombre']}."
        )

        if self.app and self.settings.familiar_chat_id:
            async def send_family() -> None:
                await self.app.bot.send_message(
                    chat_id=self.settings.familiar_chat_id,
                    text=f"⚠️ {message}",
                )
            self.submit(send_family())

        emergency = self.contacts.emergency()
        if emergency:
            self.notifications.whatsapp(
                emergency["telefono"],
                message,
            )

        if self.settings.telefono_usuario:
            self.notifications.call(
                self.settings.telefono_usuario,
                f"Recuerda tomar {med['nombre']}.",
            )
