from __future__ import annotations

import sqlite3
from pathlib import Path
from app.config import Settings


class Database:
    def __init__(self, settings: Settings) -> None:
        self.path = settings.db_path
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self) -> None:
        conn = self.connect()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS medicamentos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL COLLATE NOCASE,
                dosis TEXT DEFAULT '',
                hora TEXT NOT NULL,
                activo INTEGER NOT NULL DEFAULT 1,
                creado_en TEXT NOT NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_medicamento_activo
            ON medicamentos(nombre)
            WHERE activo = 1;

            CREATE TABLE IF NOT EXISTS tomas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                medicamento_id INTEGER NOT NULL,
                fecha TEXT NOT NULL,
                hora_programada TEXT NOT NULL,
                hora_real TEXT NOT NULL,
                UNIQUE(medicamento_id, fecha)
            );

            CREATE TABLE IF NOT EXISTS agenda (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha TEXT NOT NULL,
                hora TEXT NOT NULL,
                descripcion TEXT NOT NULL,
                creado_en TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS contactos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL COLLATE NOCASE,
                telefono TEXT NOT NULL,
                relacion TEXT DEFAULT '',
                emergencia INTEGER NOT NULL DEFAULT 0,
                creado_en TEXT NOT NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_contacto_nombre
            ON contactos(nombre);
            """
        )
        conn.commit()
        conn.close()
