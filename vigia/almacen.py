"""Almacenamiento de eventos en SQLite sin dependencias externas.

El esquema se migra solo: las bases de datos creadas por versiones anteriores
reciben las columnas nuevas (`miniatura`, `clip`) al abrirse.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import date, datetime, timedelta
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
CREATE INDEX IF NOT EXISTS idx_eventos_tipo_tiempo ON eventos(tipo, marca_sistema);
CREATE TABLE IF NOT EXISTS rostros (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    embedding BLOB NOT NULL,
    creado DATETIME NOT NULL
);
CREATE TABLE IF NOT EXISTS desconocidos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    primera DATETIME NOT NULL,
    ultima DATETIME NOT NULL,
    veces INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS tomas_desconocido (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    desconocido_id INTEGER NOT NULL,
    embedding BLOB NOT NULL,
    foto BLOB,
    creada DATETIME NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tomas_desc ON tomas_desconocido(desconocido_id);
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
    # multicamara: la camara que genero el evento ("0" = camara unica clasica).
    # El DEFAULT hace que las filas antiguas queden como camara 0.
    "camara_id": "TEXT DEFAULT '0'",
    # zona implicada (para filtrar sin analizar el texto de `detalle`)
    "zona": "TEXT",
    # duracion de la estancia (solo `zona_sale`), en segundos
    "duracion_seg": "REAL",
}

_VERSION_ESQUEMA = 4
_FORMATO = "%Y-%m-%d %H:%M:%S"
_COLS = ("id", "marca_sistema", "marca_pantalla", "tipo", "id_tracking",
         "detalle", "miniatura", "clip", "camara_id", "zona", "duracion_seg")


def _fecha_desde(valor: str) -> str:
    """Acepta `AAAA-MM-DD` o `AAAA-MM-DD HH:MM:SS` y devuelve el inicio."""
    texto = str(valor).strip()
    if len(texto) == 10:
        return f"{texto} 00:00:00"
    return texto


def _fecha_hasta(valor: str) -> str:
    texto = str(valor).strip()
    if len(texto) == 10:
        return f"{texto} 23:59:59"
    return texto


def _actividad_de(detalle: str | None) -> str | None:
    """Saca la actividad de un detalle `id 7 usando movil`."""
    if not detalle:
        return None
    partes = str(detalle).strip().split(maxsplit=2)
    if len(partes) == 3 and partes[0] == "id":
        return partes[2].strip() or None
    return str(detalle).strip() or None


def _ocupacion(filas, desde: str, hasta: str
               ) -> tuple[float, int, int]:
    """(ocupacion media, maximo simultaneo, instante del maximo).

    Aproxima la ocupacion emparejando `persona_entra` con su `persona_salio`.
    Una persona que sigue dentro al final de la ventana cuenta hasta `hasta`.
    """
    try:
        t_ini = datetime.strptime(desde, _FORMATO)
        t_fin = datetime.strptime(hasta, _FORMATO)
    except (TypeError, ValueError):
        return 0.0, 0, 0
    intervalos: list[tuple[datetime, datetime]] = []
    abiertos: dict[int, datetime] = {}
    for pid, tipo, cuando in filas:
        try:
            momento = datetime.strptime(cuando, _FORMATO)
        except (TypeError, ValueError):
            continue
        if tipo == "persona_entra":
            abiertos[pid] = momento
        elif tipo == "persona_salio" and pid in abiertos:
            intervalos.append((abiertos.pop(pid), momento))
    for inicio in abiertos.values():
        intervalos.append((inicio, t_fin))
    recortados = []
    for inicio, fin in intervalos:
        inicio = max(inicio, t_ini)
        fin = min(fin, t_fin)
        if fin > inicio:
            recortados.append((inicio, fin))
    ventana = max((t_fin - t_ini).total_seconds(), 1.0)
    persona_seg = sum((fin - inicio).total_seconds()
                      for inicio, fin in recortados)
    eventos = []
    for inicio, fin in recortados:
        eventos.append((inicio, 1))
        eventos.append((fin, -1))
    eventos.sort(key=lambda par: (par[0], -par[1]))  # entradas antes que salidas
    actual = maximo = 0
    instante_max = t_ini
    for momento, delta in eventos:
        actual += delta
        if actual > maximo:
            maximo = actual
            instante_max = momento
    return (persona_seg / ventana, maximo,
            int((instante_max - t_ini).total_seconds()))


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
        if "foto" not in {f[1] for f in self.con.execute(
                "PRAGMA table_info(rostros)")}:
            self.con.execute("ALTER TABLE rostros ADD COLUMN foto BLOB")
        # indices sobre columnas migradas (no pueden ir en _ESQUEMA porque una
        # BD antigua aun no las tiene cuando se ejecuta)
        self.con.execute(
            "CREATE INDEX IF NOT EXISTS idx_eventos_camara "
            "ON eventos(camara_id)")
        self.con.execute(
            "CREATE INDEX IF NOT EXISTS idx_eventos_zona ON eventos(zona)")
        # solo se sube: abrir una BD de una version futura no la "degrada"
        actual = self.con.execute("PRAGMA user_version").fetchone()[0]
        if actual < _VERSION_ESQUEMA:
            self.con.execute(f"PRAGMA user_version={_VERSION_ESQUEMA}")

    # -- escritura --------------------------------------------------------
    def guardar(self, tipo: str, id_tracking: int | None = None,
                detalle: str | None = None,
                miniatura: str | None = None,
                clip: str | None = None,
                cuando: str | None = None,
                zona: str | None = None,
                camara_id: str | int | None = None,
                duracion_seg: float | None = None) -> int:
        """Inserta un evento y devuelve su id (para enlazarle media)."""
        momento = datetime.now() if cuando is None else datetime.strptime(
            cuando, _FORMATO)
        ahora = momento.strftime(_FORMATO)
        pantalla = momento.strftime("%H:%M:%S")
        camara = "0" if camara_id is None else str(camara_id)
        if duracion_seg is not None:
            try:
                duracion_seg = float(duracion_seg)
            except (TypeError, ValueError):
                duracion_seg = None
        with self._lock:
            cur = self.con.execute(
                "INSERT INTO eventos (marca_sistema, marca_pantalla, tipo, "
                "id_tracking, detalle, miniatura, clip, camara_id, zona, "
                "duracion_seg) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ahora, pantalla, tipo, id_tracking, detalle, miniatura, clip,
                 camara, zona, duracion_seg))
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

    def listar_eventos(self, limite: int = 100,
                       antes_id: int | None = None,
                       tipo: str | None = None,
                       camara_id: str | int | None = None,
                       desde: str | None = None,
                       hasta: str | None = None,
                       persona: int | None = None,
                       zona: str | None = None) -> dict:
        """Consulta paginada y filtrable para `GET /eventos`.

        Devuelve `{"eventos": [...], "total": n}`. `total` cuenta todas las
        filas que cumplen los filtros (sin aplicar `limite` ni `antes_id`), lo
        que permite a la GUI pintar "N de M" y pedir la pagina siguiente.
        """
        try:
            limite = max(1, min(int(limite), 500))
        except (TypeError, ValueError):
            limite = 100
        condiciones = ["1=1"]
        args: list = []
        if tipo:
            condiciones.append("tipo = ?")
            args.append(str(tipo))
        if camara_id is not None and str(camara_id) != "":
            condiciones.append("camara_id = ?")
            args.append(str(camara_id))
        if desde:
            condiciones.append("marca_sistema >= ?")
            args.append(_fecha_desde(desde))
        if hasta:
            condiciones.append("marca_sistema <= ?")
            args.append(_fecha_hasta(hasta))
        if persona is not None:
            try:
                condiciones.append("id_tracking = ?")
                args.append(int(persona))
            except (TypeError, ValueError):
                condiciones.append("0")
        if zona:
            # las filas antiguas no tienen columna zona: se busca en detalle
            condiciones.append(
                "(zona = ? OR (zona IS NULL AND detalle LIKE ?))")
            args.extend([str(zona), f"%zona={zona}%"])
        where = " WHERE " + " AND ".join(condiciones)
        total_q = "SELECT COUNT(*) FROM eventos" + where
        q = f"SELECT {', '.join(_COLS)} FROM eventos" + where
        args_consulta = list(args)
        if antes_id is not None:
            try:
                q += " AND id < ?"
                args_consulta.append(int(antes_id))
            except (TypeError, ValueError):
                pass
        q += " ORDER BY id DESC LIMIT ?"
        args_consulta.append(limite)
        with self._lock:
            total = int(self.con.execute(total_q, args).fetchone()[0])
            filas = self.con.execute(q, args_consulta).fetchall()
        return {"eventos": [dict(zip(_COLS, f)) for f in filas],
                "total": total}

    def estadisticas(self, dias: int = 7,
                     ahora: datetime | None = None) -> dict:
        """Agregados para el panel profesional (`GET /estadisticas`)."""
        try:
            dias = max(1, min(int(dias), 365))
        except (TypeError, ValueError):
            dias = 7
        ahora = ahora or datetime.now()
        inicio_dia = ahora.date() - timedelta(days=dias - 1)
        desde = f"{inicio_dia.isoformat()} 00:00:00"
        hasta = ahora.strftime(_FORMATO)

        with self._lock:
            por_tipo = dict(self.con.execute(
                "SELECT tipo, COUNT(*) FROM eventos "
                "WHERE marca_sistema >= ? AND marca_sistema <= ? "
                "GROUP BY tipo ORDER BY COUNT(*) DESC",
                (desde, hasta)).fetchall())
            por_hora = [0] * 24
            for hora, n in self.con.execute(
                    "SELECT CAST(substr(marca_pantalla, 1, 2) AS INTEGER), "
                    "COUNT(*) FROM eventos "
                    "WHERE marca_sistema >= ? AND marca_sistema <= ? "
                    "GROUP BY 1", (desde, hasta)):
                if hora is not None and 0 <= int(hora) < 24:
                    por_hora[int(hora)] = int(n)
            por_dia = [{"dia": d, "n": int(n)} for d, n in self.con.execute(
                "SELECT substr(marca_sistema, 1, 10), COUNT(*) FROM eventos "
                "WHERE marca_sistema >= ? AND marca_sistema <= ? "
                "GROUP BY 1 ORDER BY 1", (desde, hasta))]
            personas = int(self.con.execute(
                "SELECT COUNT(DISTINCT id_tracking) FROM eventos "
                "WHERE marca_sistema >= ? AND marca_sistema <= ? "
                "AND id_tracking IS NOT NULL", (desde, hasta)).fetchone()[0])
            actividades = self.con.execute(
                "SELECT id_tracking, marca_sistema, detalle FROM eventos "
                "WHERE tipo = 'actividad' AND marca_sistema >= ? "
                "AND marca_sistema <= ? ORDER BY id_tracking, id",
                (desde, hasta)).fetchall()
            entradas = self.con.execute(
                "SELECT id_tracking, tipo, marca_sistema FROM eventos "
                "WHERE tipo IN ('persona_entra', 'persona_salio') "
                "AND marca_sistema >= ? AND marca_sistema <= ? "
                "ORDER BY marca_sistema, id", (desde, hasta)).fetchall()

        segundos_actividad: dict[str, float] = {}
        anterior: dict[int, tuple[datetime, str]] = {}
        for pid, cuando, detalle in actividades:
            texto = _actividad_de(detalle)
            if not texto:
                continue
            try:
                momento = datetime.strptime(cuando, _FORMATO)
            except (TypeError, ValueError):
                continue
            if pid in anterior:
                t0, act0 = anterior[pid]
                delta = (momento - t0).total_seconds()
                if 0 <= delta <= 600:  # >10 min = hueco, no tiempo real
                    segundos_actividad[act0] = (
                        segundos_actividad.get(act0, 0.0) + delta)
            anterior[pid] = (momento, texto)
        # el tramo abierto de cada persona cuenta hasta ahora, con tope
        for pid, (t0, act0) in anterior.items():
            delta = (ahora - t0).total_seconds()
            if 0 <= delta <= 600:
                segundos_actividad[act0] = (
                    segundos_actividad.get(act0, 0.0) + delta)

        ocupacion, ocupacion_max, pico_offset = _ocupacion(
            entradas, desde, hasta)
        pico_hora = max(range(24), key=lambda h: por_hora[h])
        pico_dia = max(por_dia, key=lambda d: d["n"]) if por_dia else None
        return {
            "dias": dias,
            "desde": desde,
            "hasta": hasta,
            "total": sum(por_tipo.values()),
            "por_tipo": por_tipo,
            "por_hora": por_hora,
            "por_dia": por_dia,
            "actividades": {k: round(v, 1)
                            for k, v in sorted(segundos_actividad.items(),
                                               key=lambda kv: -kv[1])},
            "personas_distintas": personas,
            "ocupacion_media": round(ocupacion, 2),
            "ocupacion_max": ocupacion_max,
            "picos": {
                "hora": {"hora": pico_hora, "n": por_hora[pico_hora]},
                "dia": pico_dia,
                "personas": {"n": ocupacion_max, "t_seg": pico_offset},
            },
        }

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
    def guardar_rostro(self, nombre: str, embedding: bytes,
                       foto: bytes | None = None) -> int:
        """Registra una identidad conocida (con su foto, si la hay). Devuelve su id."""
        creado = datetime.now().strftime(_FORMATO)
        with self._lock:
            cur = self.con.execute(
                "INSERT INTO rostros (nombre, embedding, creado, foto) "
                "VALUES (?, ?, ?, ?)",
                (nombre, bytes(embedding), creado, foto or None))
            self.con.commit()
            return int(cur.lastrowid or 0)

    def rostros_con_foto(self) -> list[dict]:
        """Muestras de las personas conocidas: id, nombre y si tienen foto."""
        with self._lock:
            filas = self.con.execute(
                "SELECT id, nombre, foto IS NOT NULL AND length(foto) > 0, creado "
                "FROM rostros ORDER BY id").fetchall()
        return [{"id": i, "nombre": n, "foto": bool(f), "creado": c}
                for i, n, f, c in filas]

    def foto_rostro(self, id_rostro: int) -> bytes | None:
        with self._lock:
            fila = self.con.execute("SELECT foto FROM rostros WHERE id = ?",
                                    (int(id_rostro),)).fetchone()
        return bytes(fila[0]) if fila and fila[0] else None

    def renombrar_persona(self, de: str, a: str) -> int:
        """Renombra todas las muestras de una persona. Devuelve cuantas."""
        with self._lock:
            cur = self.con.execute(
                "UPDATE rostros SET nombre = ? WHERE nombre = ?", (a, de))
            self.con.commit()
        return cur.rowcount or 0

    def borrar_persona(self, nombre: str) -> int:
        """Elimina todas las muestras de una persona. Devuelve cuantas."""
        with self._lock:
            cur = self.con.execute("DELETE FROM rostros WHERE nombre = ?",
                                   (nombre,))
            self.con.commit()
        return cur.rowcount or 0

    # -- caras sin identificar (agrupadas por parecido) ----------------------
    def desconocidos_tomas(self) -> list[tuple[int, int, bytes]]:
        """(id_toma, id_grupo, embedding) de todas las tomas guardadas."""
        with self._lock:
            filas = self.con.execute(
                "SELECT id, desconocido_id, embedding FROM tomas_desconocido"
            ).fetchall()
        return [(i, g, bytes(e)) for i, g, e in filas]

    def nuevo_desconocido(self) -> int:
        ahora = datetime.now().strftime(_FORMATO)
        with self._lock:
            cur = self.con.execute(
                "INSERT INTO desconocidos (primera, ultima, veces) VALUES (?, ?, 1)",
                (ahora, ahora))
            self.con.commit()
            return int(cur.lastrowid or 0)

    def tocar_desconocido(self, id_grupo: int) -> None:
        ahora = datetime.now().strftime(_FORMATO)
        with self._lock:
            self.con.execute(
                "UPDATE desconocidos SET ultima = ?, veces = veces + 1 WHERE id = ?",
                (ahora, int(id_grupo)))
            self.con.commit()

    def anadir_toma_desconocido(self, id_grupo: int, embedding: bytes,
                                foto: bytes | None) -> int:
        ahora = datetime.now().strftime(_FORMATO)
        with self._lock:
            cur = self.con.execute(
                "INSERT INTO tomas_desconocido (desconocido_id, embedding, foto, "
                "creada) VALUES (?, ?, ?, ?)",
                (int(id_grupo), bytes(embedding), foto or None, ahora))
            self.con.commit()
            return int(cur.lastrowid or 0)

    def listar_desconocidos(self) -> list[dict]:
        """Grupos de caras sin identificar, los vistos hace poco primero."""
        with self._lock:
            grupos = self.con.execute(
                "SELECT id, primera, ultima, veces FROM desconocidos "
                "ORDER BY ultima DESC").fetchall()
            tomas = self.con.execute(
                "SELECT id, desconocido_id FROM tomas_desconocido "
                "WHERE foto IS NOT NULL ORDER BY id").fetchall()
        por_grupo: dict[int, list[int]] = {}
        for i, g in tomas:
            por_grupo.setdefault(g, []).append(i)
        return [{"id": g, "primera": p, "ultima": u, "veces": v,
                 "tomas": por_grupo.get(g, [])} for g, p, u, v in grupos]

    def foto_toma(self, id_toma: int) -> bytes | None:
        with self._lock:
            fila = self.con.execute(
                "SELECT foto FROM tomas_desconocido WHERE id = ?",
                (int(id_toma),)).fetchone()
        return bytes(fila[0]) if fila and fila[0] else None

    def borrar_desconocido(self, id_grupo: int) -> bool:
        with self._lock:
            self.con.execute(
                "DELETE FROM tomas_desconocido WHERE desconocido_id = ?",
                (int(id_grupo),))
            cur = self.con.execute("DELETE FROM desconocidos WHERE id = ?",
                                   (int(id_grupo),))
            self.con.commit()
        return (cur.rowcount or 0) > 0

    def promover_desconocido(self, id_grupo: int, nombre: str) -> int:
        """Convierte un grupo en persona conocida: cada toma pasa a ser una
        muestra registrada con `nombre`. Devuelve cuantas muestras."""
        creado = datetime.now().strftime(_FORMATO)
        with self._lock:
            tomas = self.con.execute(
                "SELECT embedding, foto FROM tomas_desconocido "
                "WHERE desconocido_id = ? ORDER BY id", (int(id_grupo),)).fetchall()
            if not tomas:
                return 0
            for emb, foto in tomas:
                self.con.execute(
                    "INSERT INTO rostros (nombre, embedding, creado, foto) "
                    "VALUES (?, ?, ?, ?)", (nombre, emb, creado, foto))
            self.con.execute(
                "DELETE FROM tomas_desconocido WHERE desconocido_id = ?",
                (int(id_grupo),))
            self.con.execute("DELETE FROM desconocidos WHERE id = ?",
                             (int(id_grupo),))
            self.con.commit()
        return len(tomas)

    def podar_desconocidos(self, maximo: int) -> None:
        """Conserva solo los `maximo` grupos vistos mas recientemente."""
        with self._lock:
            viejos = [r[0] for r in self.con.execute(
                "SELECT id FROM desconocidos ORDER BY ultima DESC LIMIT -1 OFFSET ?",
                (int(maximo),)).fetchall()]
            for g in viejos:
                self.con.execute(
                    "DELETE FROM tomas_desconocido WHERE desconocido_id = ?", (g,))
                self.con.execute("DELETE FROM desconocidos WHERE id = ?", (g,))
            if viejos:
                self.con.commit()

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
