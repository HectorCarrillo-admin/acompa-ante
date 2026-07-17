from __future__ import annotations

import re
from datetime import timedelta

from app.config import Settings
from app.services.agenda import AgendaService
from app.services.contacts import ContactService
from app.services.medications import MedicationService
from app.services.news import NewsService
from app.services.notifications import NotificationService
from app.services.weather import WeatherService
from app.utils import clean_spoken_text, normalize, now_local, today_local


HOUR_WORDS = {
    "una": 1, "dos": 2, "tres": 3, "cuatro": 4,
    "cinco": 5, "seis": 6, "siete": 7, "ocho": 8,
    "nueve": 9, "diez": 10, "once": 11, "doce": 12,
}


def parse_time(text: str) -> str | None:
    text = clean_spoken_text(text)

    match = re.search(r"\b([01]?\d|2[0-3])\s*[:h]\s*([0-5]\d)\b", text)
    if match:
        return f"{int(match.group(1)):02d}:{int(match.group(2)):02d}"

    match = re.search(
        r"\b(?:a\s+las|las)\s+(\d{1,2})(?:\s+y\s+(\d{1,2}))?\b",
        text,
    )

    hour = None
    minute = 0

    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
    else:
        for word, value in HOUR_WORDS.items():
            if re.search(rf"\b(?:a\s+las|las)\s+{word}\b", text):
                hour = value
                break
        if hour is None:
            for word, value in HOUR_WORDS.items():
                if re.search(
                    rf"\b{word}\b(?:\s+de\s+la\s+"
                    rf"(?:manana|tarde|noche|madrugada))?\s*$",
                    text,
                ):
                    hour = value
                    break

    if hour is None:
        return None

    if "media" in text:
        minute = 30
    elif "cuarto" in text:
        minute = 15

    if "de la tarde" in text and 1 <= hour <= 11:
        hour += 12
    elif "de la noche" in text and 1 <= hour <= 11:
        hour += 12
    elif "de la madrugada" in text and hour == 12:
        hour = 0
    elif re.search(r"\bpm\b", text) and 1 <= hour <= 11:
        hour += 12
    elif re.search(r"\bam\b", text) and hour == 12:
        hour = 0

    return f"{hour:02d}:{minute:02d}"


def parse_date(text: str, settings: Settings) -> str:
    text = clean_spoken_text(text)
    today = today_local(settings)

    match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    if match:
        return match.group(1)

    if "pasado manana" in text:
        return (today + timedelta(days=2)).isoformat()
    if "manana" in text:
        return (today + timedelta(days=1)).isoformat()
    if "hoy" in text:
        return today.isoformat()

    weekdays = {
        "lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3,
        "viernes": 4, "sabado": 5, "domingo": 6,
    }
    for name, number in weekdays.items():
        if name in text:
            delta = (number - today.weekday()) % 7 or 7
            return (today + timedelta(days=delta)).isoformat()

    return today.isoformat()


class CommandParser:
    def __init__(
        self,
        settings: Settings,
        meds: MedicationService,
        agenda: AgendaService,
        contacts: ContactService,
        news: NewsService,
        weather: WeatherService,
        notifications: NotificationService,
    ) -> None:
        self.settings = settings
        self.meds = meds
        self.agenda = agenda
        self.contacts = contacts
        self.news = news
        self.weather = weather
        self.notifications = notifications

    @staticmethod
    def extract_med_add(text: str) -> str:
        text = clean_spoken_text(text)
        text = re.sub(
            r"^(?:a\s+)?(?:(?:agregar|agrega|anadir|registrar|programar)\s+)+",
            "",
            text,
        )
        text = re.sub(
            r"\b(?:un|una|el|la|medicamento|medicina|pastilla)\b",
            " ",
            text,
            count=2,
        )
        text = re.split(r"\b(?:a\s+las|las)\b", text, maxsplit=1)[0]
        text = re.sub(
            r"\b(?:una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce)"
            r"(?:\s+de\s+la\s+(?:manana|tarde|noche|madrugada))?\s*$",
            "",
            text,
        )
        return re.sub(r"\s+", " ", text).strip().title()

    @staticmethod
    def extract_med_remove(text: str) -> str:
        text = clean_spoken_text(text)
        text = re.sub(
            r"^(?:quitar|quita|eliminar|elimina|borrar|dejar de tomar)\s+",
            "",
            text,
        )
        text = re.sub(
            r"\b(?:el|la|un|una|medicamento|medicina|pastilla)\b",
            " ",
            text,
        )
        return re.sub(r"\s+", " ", text).strip().title()

    def process(self, text: str) -> str:
        original = clean_spoken_text(text)
        t = normalize(original)

        if not t:
            return "No escuché ningún mensaje."

        if t in {"hora", "que hora", "que hora es"}:
            return f"Son las {now_local(self.settings).strftime('%I:%M %p')}."

        if any(x in t for x in ["que dia", "fecha", "hoy es"]):
            return f"Hoy es {now_local(self.settings).strftime('%d/%m/%Y')}."

        if any(x in t for x in ["clima", "tiempo", "como esta el clima"]):
            return self.weather.summary()

        categories = {
            "colombia": ["noticias de colombia", "noticias colombia"],
            "mundo": ["noticias del mundo", "noticias internacionales"],
            "salud": ["noticias de salud", "noticias salud"],
            "deportes": ["noticias de deportes", "noticias deportivas"],
            "tecnologia": ["noticias de tecnologia", "tecnologia"],
            "economia": ["noticias de economia", "economia"],
            "buenas": ["buenas noticias", "noticias positivas"],
        }
        for category, phrases in categories.items():
            if any(phrase in t for phrase in phrases):
                return self.news.summary(category)

        if any(x in t for x in ["noticias", "que esta pasando", "entretenimiento"]):
            return self.news.summary("colombia")

        if any(v in t for v in ["agregar", "agrega", "anadir", "registrar", "programar"])                 and any(n in t for n in ["medicamento", "medicina", "pastilla"]):
            time_value = parse_time(original)
            name = self.extract_med_add(original)
            if not name:
                return "Dime el nombre del medicamento."
            if not time_value:
                return f"Dime la hora. Por ejemplo: agregar {name} a las ocho."
            return self.meds.add(name, time_value)

        if any(x in t for x in [
            "quitar medicamento", "eliminar medicamento",
            "borrar medicamento", "dejar de tomar"
        ]):
            name = self.extract_med_remove(original)
            return self.meds.remove(name) if name else "Dime qué medicamento quieres quitar."

        if any(x in t for x in ["ya tome", "ya me tome", "registrar toma", "marcar como tomado"]):
            for med in self.meds.list():
                if normalize(med["nombre"]) in t:
                    return self.meds.mark_taken(med["nombre"])

            pending = [m for m in self.meds.list() if not self.meds.taken_today(m["id"])]
            if len(pending) == 1:
                return self.meds.mark_taken(pending[0]["nombre"])
            if pending:
                return "Dime cuál tomaste. Los pendientes son: " + ", ".join(
                    m["nombre"] for m in pending
                ) + "."
            return "Todos los medicamentos de hoy ya están tomados."

        if any(x in t for x in [
            "mis medicamentos", "lista de medicamentos",
            "que medicamentos", "que pastillas", "debo tomar"
        ]):
            return self.meds.summary()

        if any(x in t for x in [
            "agregar evento", "agendar evento",
            "agregar cita", "agendar cita"
        ]):
            time_value = parse_time(original)
            date_value = parse_date(original, self.settings)
            description = re.sub(
                r"^(?:agregar|agrega|agendar|registrar)\s+",
                "",
                t,
            )
            description = re.sub(r"\b(?:evento|cita|una|un)\b", " ", description)
            description = re.split(
                r"\b(?:para hoy|para manana|hoy|manana|lunes|martes|miercoles|jueves|viernes|sabado|domingo|a\s+las|las)\b",
                description,
                maxsplit=1,
            )[0]
            description = re.sub(r"\s+", " ", description).strip().title()

            if not description:
                return "Dime el nombre del evento."
            if not time_value:
                return "Dime la hora del evento."
            return self.agenda.add(description, date_value, time_value)

        if any(x in t for x in [
            "quitar evento", "eliminar evento",
            "quitar cita", "eliminar cita"
        ]):
            search = re.sub(
                r"^(?:quitar|eliminar)\s+(?:evento|cita)?\s*",
                "",
                t,
            ).strip()
            return self.agenda.remove(search) if search else "Dime qué evento quieres quitar."

        if any(x in t for x in [
            "mi agenda", "agenda de hoy", "agenda de manana",
            "mis eventos", "que tengo hoy", "que tengo manana"
        ]):
            date_value = None
            if "manana" in t:
                date_value = (today_local(self.settings) + timedelta(days=1)).isoformat()
            elif "hoy" in t:
                date_value = today_local(self.settings).isoformat()
            return self.agenda.summary(date_value)

        if any(x in t for x in [
            "agregar contacto", "anadir contacto", "registrar contacto"
        ]):
            phone_match = re.search(r"(\+?\d{7,15})", t)
            phone = phone_match.group(1) if phone_match else ""
            emergency = "emergencia" in t
            name = re.sub(
                r"^(?:agregar|anadir|registrar)\s+contacto\s+",
                "",
                t,
            )
            name = re.sub(r"\btelefono\b", " ", name)
            name = re.sub(r"\+?\d{7,15}", " ", name)
            name = re.sub(r"\bemergencia\b", " ", name)
            name = re.sub(r"\s+", " ", name).strip().title()

            if not name or not phone:
                return "Di: agregar contacto Ana teléfono 3001234567."
            return self.contacts.add(name, phone, emergency=emergency)

        if any(x in t for x in ["quitar contacto", "eliminar contacto"]):
            name = re.sub(
                r"^(?:quitar|eliminar)\s+contacto\s+",
                "",
                t,
            ).strip()
            return self.contacts.remove(name) if name else "Dime qué contacto quieres quitar."

        if any(x in t for x in ["mis contactos", "lista de contactos", "que contactos"]):
            return self.contacts.summary()

        if t.startswith(("llama a ", "llamar a ", "llamale a ")):
            name = re.sub(r"^(?:llama a|llamar a|llamale a)\s+", "", t).strip()
            contact = self.contacts.find(name)
            if not contact:
                return "No encontré ese contacto."
            self.notifications.call(
                contact["telefono"],
                f"Llamada de {self.settings.nombre_usuario}.",
            )
            return f"Inicié la llamada a {contact['nombre']}."

        if any(x in t for x in [
            "emergencia", "auxilio", "socorro",
            "me cai", "me siento mal", "necesito ayuda"
        ]):
            contact = self.contacts.emergency()
            if not contact:
                return "No tienes un contacto de emergencia configurado."
            message = (
                f"EMERGENCIA: {self.settings.nombre_usuario} "
                "necesita ayuda urgente."
            )
            self.notifications.whatsapp(contact["telefono"], message)
            self.notifications.call(contact["telefono"], message)
            return f"Envié una alerta a {contact['nombre']}."

        if any(x in t for x in ["ayuda", "comandos", "que puedo hacer"]):
            return (
                "Puedes decir: agregar medicamento Aspirina a las ocho de la mañana; "
                "quitar medicamento Aspirina; mis medicamentos; ya tomé Aspirina; "
                "agregar cita médica para mañana a las tres de la tarde; "
                "quitar evento cita médica; mi agenda; "
                "agregar contacto Ana teléfono 3001234567; "
                "mis contactos; noticias de Colombia; noticias del mundo; "
                "noticias de salud; noticias de deportes; noticias de tecnología; "
                "noticias de economía; buenas noticias; clima; emergencia; "
                "o qué hora es."
            )

        return "No entendí el mensaje. Di ayuda para escuchar ejemplos."
