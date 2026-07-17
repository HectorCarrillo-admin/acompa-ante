from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import tempfile
from pathlib import Path

from faster_whisper import WhisperModel
from gtts import gTTS
from telegram import Update

from app.config import Settings
from app.utils import clean_spoken_text

logger = logging.getLogger(__name__)


class VoiceService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        logger.info("Cargando modelo de voz: %s", settings.modelo_voz)
        self.model = WhisperModel(
            settings.modelo_voz,
            device="cpu",
            compute_type="int8",
            cpu_threads=settings.whisper_cpu_threads,
            num_workers=1,
        )

    @staticmethod
    def remove_temp(path: str | None) -> None:
        if not path:
            return
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass

    def _convert(self, source: str) -> str | None:
        temp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        temp.close()
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-i", source, "-ac", "1", "-ar", "16000", "-vn",
                    temp.name,
                ],
                check=True,
                timeout=30,
            )
            return temp.name
        except Exception as exc:
            logger.exception("Error convirtiendo audio: %s", exc)
            self.remove_temp(temp.name)
            return None

    def transcribe(self, source: str) -> str:
        wav = self._convert(source)
        if not wav:
            return ""
        try:
            segments, _ = self.model.transcribe(
                wav,
                language="es",
                beam_size=self.settings.whisper_beam_size,
                best_of=self.settings.whisper_beam_size,
                vad_filter=True,
                condition_on_previous_text=False,
                initial_prompt=(
                    "Acompañante Mayor. Agregar medicamento, quitar medicamento, "
                    "mis medicamentos, ya tomé, agenda, cita, contacto, emergencia, "
                    "noticias, Colombia, mundo, salud, deportes, tecnología, economía."
                ),
                temperature=0.0,
            )
            text = " ".join(
                segment.text.strip()
                for segment in segments
                if segment.text.strip()
            )
            text = self.correct_common_errors(text)
            logger.info("Transcripción: %s", text)
            return text
        finally:
            self.remove_temp(wav)

    @staticmethod
    def correct_common_errors(text: str) -> str:
        text = clean_spoken_text(text)
        replacements = {
            "abregar": "agregar",
            "agragar": "agregar",
            "agregarme": "agregar",
            "mis medicamento": "mis medicamentos",
            "mis contacto": "mis contactos",
            "madrugrada": "madrugada",
        }
        for source, target in replacements.items():
            text = text.replace(source, target)
        return text

    def synthesize(self, text: str) -> str | None:
        temp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
        temp.close()
        try:
            gTTS(text=text, lang="es", slow=False).save(temp.name)
            return temp.name
        except Exception as exc:
            logger.exception("Error generando audio: %s", exc)
            self.remove_temp(temp.name)
            return None

    async def reply(
        self,
        update: Update,
        text: str,
        reply_markup=None,
        with_audio: bool = True,
    ) -> None:
        if not update.message:
            return

        await update.message.reply_text(text, reply_markup=reply_markup)

        if not with_audio:
            return

        audio = await asyncio.to_thread(self.synthesize, text)
        try:
            if audio:
                with open(audio, "rb") as handle:
                    await update.message.reply_voice(voice=handle)
        finally:
            self.remove_temp(audio)
