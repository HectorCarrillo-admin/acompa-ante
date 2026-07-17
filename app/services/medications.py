from __future__ import annotations

import re
from datetime import datetime
from app.config import Settings
from app.db import Database
from app.utils import normalize, now_local, today_local


class MedicationService:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.on_changed = None
        self.on_taken = None

    def list(self) -> list[dict]:
        conn = self.db.connect()
        rows = conn.execute(
            """
            SELECT * FROM medicamentos
            WHERE activo = 1
            ORDER BY hora, nombre
            """
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def find(self, name: str) -> dict | None:
        target = normalize(name)
        for med in self.list():
            current = normalize(med["nombre"])
            if current == target or target in current or current in target:
                return med
        return None

    def taken_today(self, med_id: int) -> bool:
        conn = self.db.connect()
        row = conn.execute(
            """
            SELECT id FROM tomas
            WHERE medicamento_id = ? AND fecha = ?
            """,
            (med_id, today_local(self.settings).isoformat()),
        ).fetchone()
        conn.close()
        return row is not None

    def add(self, name: str, time: str, dose: str = "") -> str:
        name = name.strip().title()
        if not name:
            return "No pude identificar el medicamento."

        conn = self.db.connect()
        existing = conn.execute(
            """
            SELECT id FROM medicamentos
            WHERE lower(nombre) = lower(?) AND activo = 1
            """,
            (name,),
        ).fetchone()
        if existing:
            conn.close()
            return f"{name} ya está registrado."

        conn.execute(
            """
            INSERT INTO medicamentos
            (nombre, dosis, hora, activo, creado_en)
            VALUES (?, ?, ?, 1, ?)
            """,
            (name, dose.strip(), time, now_local(self.settings).isoformat()),
        )
        conn.commit()
        conn.close()

        if self.on_changed:
            self.on_changed()

        return f"Agregué {name} a las {time}."

    def remove(self, name: str) -> str:
        med = self.find(name)
        if not med:
            return "No encontré ese medicamento."

        conn = self.db.connect()
        conn.execute(
            "UPDATE medicamentos SET activo = 0 WHERE id = ?",
            (med["id"],),
        )
        conn.commit()
        conn.close()

        if self.on_changed:
            self.on_changed()

        return f"Quité {med['nombre']} de la lista."

    def mark_taken(self, name: str) -> str:
        med = self.find(name)
        if not med:
            return "No encontré ese medicamento."

        if self.taken_today(med["id"]):
            return f"{med['nombre']} ya está registrado como tomado hoy."

        conn = self.db.connect()
        conn.execute(
            """
            INSERT INTO tomas
            (medicamento_id, fecha, hora_programada, hora_real)
            VALUES (?, ?, ?, ?)
            """,
            (
                med["id"],
                today_local(self.settings).isoformat(),
                med["hora"],
                now_local(self.settings).strftime("%H:%M"),
            ),
        )
        conn.commit()
        conn.close()

        if self.on_taken:
            self.on_taken(med["id"])

        return f"Registré que tomaste {med['nombre']} hoy."

    def summary(self) -> str:
        meds = self.list()
        if not meds:
            return "No tienes medicamentos registrados."

        parts = ["Tus medicamentos actuales son:"]
        for med in meds:
            status = "tomado hoy" if self.taken_today(med["id"]) else "pendiente hoy"
            dose = f", dosis {med['dosis']}" if med["dosis"] else ""
            parts.append(
                f"{med['nombre']}{dose}, a las {med['hora']}, {status}."
            )
        return " ".join(parts)
