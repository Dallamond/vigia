"""Almacenamiento de eventos en SQLite sin dependencias externas.

El esquema se migra solo: las bases de datos creadas por versiones anteriores
reciben las columnas nuevas (`miniatura`, `clip`) al abrirse.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timedelta
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
CREATE TABLE IF NOT EXISTS rostros (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    embedding BLOB NOT NULL,
    creado DATETIME NOT NULL
);
CREATE TABLE IF NOT EXISTS estado_sistema (
    clave TEXT PRIMARY KEY,
    valor TEXT,
    actualizado DATETIME NOT NULL
);
"""

# columnas anadidas despues de la v1: se crean si faltan (migracion simple)
_COLUMNAS_NUEVAS = {
    "miniatura": "TEXT",  # fichero .jpg del momento del evento
    "clip": "TEXT",       # fichero .mp4 asociado (si lo hay)
}

_VERSION_ESQUEMA = 2
_FORMATO = "%Y-%m-%d %H:%M:%S"
_COLS = ("id", "marca_sistema", "marca_pantalla", "tipo", "id_tracking",
         "detalle", "miniatura", "clip")


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
        self._migrar()
        self.con.commit()
        log.info("BD abierta: %s", self.ruta.resolve())

    # -- esquema ----------------------------------------------------------
    def _migrar(self) -> None:
        columnas = {f[1] for f in self.con.execute("PRAGMA table_info(eventos)")}
        for nombre, tipo in _COLUMNAS_NUEVAS.items():
            if nombre not in columnas:
                self.con.execute(
                    f"ALTER TABLE eventos ADD COLUMN {nombre} {tipo}")
                log.info("BD migrada: columna %s anadida", nombre)
        # solo se sube: abrir una BD de una version futura no la "degrada"
        actual = self.con.execute("PRAGMA user_version").fetchone()[0]
        if actual < _VERSION_ESQUEMA:
            self.con.execute(f"PRAGMA user_version={_VERSION_ESQUEMA}")

    # -- escritura --------------------------------------------------------
    def guardar(self, tipo: str, id_tracking: int | None = None,
                detalle: str | None = None,
                miniatura: str | None = None,
                clip: str | None = None,
                cuando: str | None = None) -> int:
        """Inserta un evento y devuelve su id (para enlazarle media)."""
        momento = datetime.now() if cuando is None else datetime.strptime(
            cuando, _FORMATO)
        ahora = momento.strftime(_FORMATO)
        pantalla = momento.strftime("%H:%M:%S")
        with self._lock:
            cur = self.con.execute(
                "INSERT INTO eventos (marca_sistema, marca_pantalla, tipo, "
                "id_tracking, detalle, miniatura, clip) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ahora, pantalla, tipo, id_tracking, detalle, miniatura, clip))
            self.con.commit()
            return int(cur.lastrowid or 0)

    def anotar_media(self, evento_id: int | None, miniatura: str | None = None,
                     clip: str | None = None) -> None:
        """Enlaza la miniatura/clip a un evento ya guardado."""
        if not evento_id or (miniatura is None and clip is None):
            return
        with self._lock:
            self.con.execute(
                "UPDATE eventos SET miniatura = COALESCE(?, miniatura), "
                "clip = COALESCE(?, clip) WHERE id = ?",
                (miniatura, clip, evento_id))
            self.con.commit()

    def renombrar_clip(self, viejo: str, nuevo: str) -> int:
        """Apunta los eventos de un clip al fichero recodificado."""
        with self._lock:
            cur = self.con.execute(
                "UPDATE eventos SET clip = ? WHERE clip = ?", (nuevo, viejo))
            self.con.commit()
            return int(cur.rowcount or 0)

    def limpiar_media_huerfana(self, carpeta: str | Path) -> int:
        """Quita de la BD referencias a ficheros que ya no existen en disco.

        La purga borra media por edad/tamano; sin esto, el historial seguiria
        apuntando a miniaturas y clips borrados (imagenes rotas).
        """
        raiz = Path(carpeta)
        with self._lock:
            filas = self.con.execute(
                "SELECT id, miniatura, clip FROM eventos "
                "WHERE miniatura IS NOT NULL OR clip IS NOT NULL"
            ).fetchall()
            cambios = []
            for evento_id, miniatura, clip in filas:
                mini_ok = not miniatura or (raiz / miniatura).is_file()
                clip_ok = not clip or (raiz / clip).is_file()
                if not mini_ok or not clip_ok:
                    cambios.append((None if not mini_ok else miniatura,
                                    None if not clip_ok else clip,
                                    evento_id))
            if cambios:
                self.con.executemany(
                    "UPDATE eventos SET miniatura = ?, clip = ? WHERE id = ?",
                    cambios)
                self.con.commit()
        if cambios:
            log.info("BD: %d eventos sin media en disco", len(cambios))
        return len(cambios)

    # -- lectura ----------------------------------------------------------
    def historial(self, limite: int = 100, tipo: str | None = None,
                  desde: str | None = None) -> list[dict]:
        """Devuelve los últimos eventos, filtrables por tipo y fecha (ISO)."""
        try:
            limite = int(limite)
        except (TypeError, ValueError):
            limite = 100
        limite = max(1, min(limite, 500))
        q = (f"SELECT {', '.join(_COLS)} FROM eventos WHERE 1=1")
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
        return [dict(zip(_COLS, f)) for f in filas]

    def eventos_del_dia(self, dia: str, limite: int = 2000) -> list[dict]:
        """Eventos de un dia (YYYY-MM-DD) en orden cronologico (timeline)."""
        try:
            datetime.strptime(dia, "%Y-%m-%d")
        except (TypeError, ValueError):
            return []
        limite = max(1, min(int(limite or 2000), 5000))
        with self._lock:
            filas = self.con.execute(
                f"SELECT {', '.join(_COLS)} FROM eventos "
                "WHERE marca_sistema >= ? AND marca_sistema <= ? "
                "ORDER BY marca_sistema ASC, id ASC LIMIT ?",
                (f"{dia} 00:00:00", f"{dia} 23:59:59", limite)).fetchall()
        return [dict(zip(_COLS, f)) for f in filas]

    def eventos_entre(self, desde: str, hasta: str,
                      limite: int = 5000) -> list[dict]:
        """Eventos entre dos instantes (`YYYY-MM-DD HH:MM:SS`), en orden
        cronologico: es la ventana que pinta la linea del tiempo."""
        try:
            datetime.strptime(desde, _FORMATO)
            datetime.strptime(hasta, _FORMATO)
        except (TypeError, ValueError):
            return []
        limite = max(1, min(int(limite or 5000), 20000))
        with self._lock:
            filas = self.con.execute(
                f"SELECT {', '.join(_COLS)} FROM eventos "
                "WHERE marca_sistema >= ? AND marca_sistema <= ? "
                "ORDER BY marca_sistema ASC, id ASC LIMIT ?",
                (desde, hasta, limite)).fetchall()
        return [dict(zip(_COLS, f)) for f in filas]

    def contar(self) -> int:
        with self._lock:
            return int(self.con.execute(
                "SELECT COUNT(*) FROM eventos").fetchone()[0])

    def ultimo_evento(self) -> str | None:
        with self._lock:
            fila = self.con.execute(
                "SELECT marca_sistema FROM eventos ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return fila[0] if fila else None

    def tamano_bytes(self) -> int:
        """Tamano de la BD incluyendo los ficheros auxiliares de WAL."""
        total = 0
        for sufijo in ("", "-wal", "-shm"):
            try:
                total += Path(str(self.ruta) + sufijo).stat().st_size
            except OSError:
                pass
        return total

    # -- mantenimiento ----------------------------------------------------
    def purgar_eventos(self, dias: int) -> int:
        """Borra eventos mas antiguos que `dias`. Devuelve cuantos borro."""
        try:
            dias = int(dias)
        except (TypeError, ValueError):
            return 0
        if dias <= 0:
            return 0
        corte = (datetime.now() - timedelta(days=dias)).strftime(_FORMATO)
        with self._lock:
            cur = self.con.execute(
                "DELETE FROM eventos WHERE marca_sistema < ?", (corte,))
            self.con.commit()
            borrados = int(cur.rowcount or 0)
            if borrados:
                # la conexion es compartida: el checkpoint tambien va bajo lock
                try:
                    self.con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.Error:
                    pass
        if borrados:
            log.info("purgados %d eventos anteriores a %s", borrados, corte)
        return borrados

    def limpiar(self) -> None:
        with self._lock:
            self.con.execute("DELETE FROM eventos")
            self.con.commit()

    # -- rostros (identidad) ------------------------------------------------
    def guardar_rostro(self, nombre: str, embedding: bytes) -> int:
        """Registra una identidad conocida. Devuelve su id."""
        creado = datetime.now().strftime(_FORMATO)
        with self._lock:
            cur = self.con.execute(
                "INSERT INTO rostros (nombre, embedding, creado) VALUES (?, ?, ?)",
                (nombre, bytes(embedding), creado))
            self.con.commit()
            return int(cur.lastrowid or 0)

    def rostros(self) -> list[dict]:
        """Identidades conocidas (id, nombre, embedding como bytes)."""
        with self._lock:
            filas = self.con.execute(
                "SELECT id, nombre, embedding FROM rostros ORDER BY id").fetchall()
        return [{"id": i, "nombre": n, "embedding": e} for i, n, e in filas]

    def renombrar_rostro(self, id_rostro: int, nombre: str) -> bool:
        """Cambia el nombre de una identidad conservando su embedding."""
        with self._lock:
            cur = self.con.execute("UPDATE rostros SET nombre = ? WHERE id = ?",
                                   (nombre, int(id_rostro)))
            self.con.commit()
        return (cur.rowcount or 0) > 0

    def borrar_rostro(self, id_rostro: int) -> bool:
        """Elimina una identidad por id. Devuelve si existia."""
        with self._lock:
            cur = self.con.execute("DELETE FROM rostros WHERE id = ?",
                                   (int(id_rostro),))
            self.con.commit()
        return (cur.rowcount or 0) > 0

    # -- estado del sistema (armado, avisos...) -----------------------------
    def guardar_estado(self, clave: str, valor: str | None) -> None:
        """Guarda un par clave/valor persistente (SOBREVIVE a reinicios)."""
        ahora = datetime.now().strftime(_FORMATO)
        with self._lock:
            self.con.execute(
                "INSERT INTO estado_sistema (clave, valor, actualizado) "
                "VALUES (?, ?, ?) ON CONFLICT(clave) DO UPDATE SET "
                "valor = excluded.valor, actualizado = excluded.actualizado",
                (str(clave), None if valor is None else str(valor), ahora))
            self.con.commit()

    def leer_estado(self, clave: str, defecto: str | None = None
                    ) -> str | None:
        with self._lock:
            fila = self.con.execute(
                "SELECT valor FROM estado_sistema WHERE clave = ?",
                (str(clave),)).fetchone()
        return fila[0] if fila else defecto

    def borrar_estado(self, clave: str) -> None:
        with self._lock:
            self.con.execute("DELETE FROM estado_sistema WHERE clave = ?",
                             (str(clave),))
            self.con.commit()

    def cerrar(self) -> None:
        """Cierra la conexion. Se puede llamar mas de una vez sin fallar."""
        with self._lock:
            if self._cerrado:
                return
            self._cerrado = True
            self.con.close()
            log.info("BD cerrada: %s", self.ruta)
