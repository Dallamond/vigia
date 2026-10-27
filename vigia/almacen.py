"""Almacenamiento de eventos en SQLite sin dependencias externas."""
from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS eventos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    marca_sistema DATETIME NOT NULL,
    marca_pantalla TEXT NOT NULL,
    tipo TEXT NOT NULL,
    id_tracking INTEGER,
    detalle TEXT
);
CREATE INDEX IF NOT EXISTS idx_eventos_tiempo ON eventos(marca_sistema);
CREATE INDEX IF NOT EXISTS idx_eventos_tipo ON eventos(tipo);
"""


class Almacen:
    """Encapsula la base de datos de eventos. Hilo-seguro para escritura."""

    def __init__(self, ruta: str | Path = "vigia.db"):
        self.ruta = Path(ruta)
        self._lock = threading.Lock()
        self._cerrado = False
        self.con = sqlite3.connect(str(self.ruta), check_same_thread=False)
        # WAL: lecturas (dashboard) y escrituras (bucle) no se bloquean entre si.
        try:
            self.con.execute("PRAGMA journal_mode=WAL")
            # si otra conexion tiene el candado, espera en vez de fallar
            self.con.execute("PRAGMA busy_timeout=5000")
        except sqlite3.Error as e:
            log.warning("no se pudo activar WAL en %s: %s", self.ruta, e)
        self.con.executescript(_ESQUEMA)
        self.con.commit()
        log.info("BD abierta: %s", self.ruta.resolve())

    def guardar(self, tipo: str, id_tracking: int | None = None,
                detalle: str | None = None) -> None:
        ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        pantalla = datetime.now().strftime("%H:%M:%S")
        with self._lock:
            self.con.execute(
                "INSERT INTO eventos (marca_sistema, marca_pantalla, tipo, id_tracking, detalle) "
                "VALUES (?, ?, ?, ?, ?)",
                (ahora, pantalla, tipo, id_tracking, detalle))
            self.con.commit()

    def historial(self, limite: int = 100, tipo: str | None = None,
                  desde: str | None = None) -> list[dict]:
        """Devuelve los últimos eventos, filtrables por tipo y fecha (ISO)."""
        try:
            limite = int(limite)
        except (TypeError, ValueError):
            limite = 100
        limite = max(1, min(limite, 500))
        vez = "SELECT id, marca_sistema, marca_pantalla, tipo, id_tracking, detalle "
        q = vez + "FROM eventos WHERE 1=1"
        args: list = []
        if tipo:
            q += " AND tipo = ?"
            args.append(tipo)
        if desde:
            q += " AND marca_sistema >= ?"
            args.append(desde)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limite)
        with self._lock:
            filas = self.con.execute(q, args).fetchall()
        cols = ["id", "marca_sistema", "marca_pantalla", "tipo", "id_tracking", "detalle"]
        return [dict(zip(cols, f)) for f in filas]

    def limpiar(self) -> None:
        with self._lock:
            self.con.execute("DELETE FROM eventos")
            self.con.commit()

    def cerrar(self) -> None:
        """Cierra la conexion. Se puede llamar mas de una vez sin fallar."""
        with self._lock:
            if self._cerrado:
                return
            self._cerrado = True
            self.con.close()
            log.info("BD cerrada: %s", self.ruta)
