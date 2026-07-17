from __future__ import annotations

from app.config import Settings
from app.db import Database
from app.utils import normalize, now_local, today_local


class AgendaService:
    def __init__(self, db: Database, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    def list(self, date_value: str | None = None) -> list[dict]:
        conn = self.db.connect()
        if date_value:
            rows = conn.execute(
                "SELECT * FROM agenda WHERE fecha = ? ORDER BY hora, id",
                (date_value,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM agenda
                WHERE fecha >= ?
                ORDER BY fecha, hora, id
                """,
                (today_local(self.settings).isoformat(),),
            ).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def add(self, description: str, date_value: str, time_value: str) -> str:
        description = description.strip().title()
        if not description:
            return "No pude identificar el evento."

        conn = self.db.connect()
        conn.execute(
            """
            INSERT INTO agenda
            (fecha, hora, descripcion, creado_en)
            VALUES (?, ?, ?, ?)
            """,
            (
                date_value,
                time_value,
                description,
                now_local(self.settings).isoformat(),
            ),
        )
        conn.commit()
        conn.close()

        return f"Agregué {description} para {date_value} a las {time_value}."

    def remove(self, search: str) -> str:
        target = normalize(search)
        matches = [
            event
            for event in self.list()
            if target in normalize(event["descripcion"])
        ]
        if not matches:
            return "No encontré ese evento."

        event = matches[0]
        conn = self.db.connect()
        conn.execute("DELETE FROM agenda WHERE id = ?", (event["id"],))
        conn.commit()
        conn.close()
        return f"Quité {event['descripcion']} de la agenda."

    def summary(self, date_value: str | None = None) -> str:
        events = self.list(date_value)
        if not events:
            return (
                f"No tienes eventos para {date_value}."
                if date_value
                else "No tienes eventos próximos."
            )

        parts = ["Tu agenda es:"]
        for event in events[:15]:
            parts.append(
                f"{event['descripcion']}, el {event['fecha']} a las {event['hora']}."
            )
        return " ".join(parts)
