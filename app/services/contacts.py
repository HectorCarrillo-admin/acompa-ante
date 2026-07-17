from __future__ import annotations

import re

from app.config import Settings
from app.db import Database
from app.utils import normalize, now_local


class ContactService:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    def list(self) -> list[dict]:
        conn = self.db.connect()
        rows = conn.execute(
            """
            SELECT * FROM contactos
            ORDER BY emergencia DESC, nombre
            """
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def find(self, name: str) -> dict | None:
        target = normalize(name)
        for contact in self.list():
            current = normalize(contact["nombre"])
            if current == target or target in current or current in target:
                return contact
        return None

    def add(
        self,
        name: str,
        phone: str,
        relation: str = "",
        emergency: bool = False,
    ) -> str:
        name = name.strip().title()
        phone = phone.strip()

        if not name:
            return "No pude identificar el contacto."
        if not re.fullmatch(r"\+?\d{7,15}", phone):
            return "El número debe tener entre 7 y 15 dígitos."

        conn = self.db.connect()
        existing = conn.execute(
            "SELECT id FROM contactos WHERE lower(nombre) = lower(?)",
            (name,),
        ).fetchone()
        if existing:
            conn.close()
            return f"Ya existe un contacto llamado {name}."

        if emergency:
            conn.execute("UPDATE contactos SET emergencia = 0")

        conn.execute(
            """
            INSERT INTO contactos
            (nombre, telefono, relacion, emergencia, creado_en)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                name,
                phone,
                relation.strip(),
                1 if emergency else 0,
                now_local(self.settings).isoformat(),
            ),
        )
        conn.commit()
        conn.close()

        suffix = " y lo marqué como contacto de emergencia" if emergency else ""
        return f"Agregué a {name}{suffix}."

    def remove(self, name: str) -> str:
        contact = self.find(name)
        if not contact:
            return "No encontré ese contacto."

        conn = self.db.connect()
        conn.execute("DELETE FROM contactos WHERE id = ?", (contact["id"],))
        conn.commit()
        conn.close()
        return f"Quité a {contact['nombre']} de tus contactos."

    def emergency(self) -> dict | None:
        return next((c for c in self.list() if c["emergencia"]), None)

    def summary(self) -> str:
        contacts = self.list()
        if not contacts:
            return "No tienes contactos registrados."

        parts = ["Tus contactos son:"]
        for contact in contacts:
            relation = f", {contact['relacion']}" if contact["relacion"] else ""
            emergency = ", contacto de emergencia" if contact["emergencia"] else ""
            parts.append(
                f"{contact['nombre']}{relation}, teléfono "
                f"{contact['telefono']}{emergency}."
            )
        return " ".join(parts)
