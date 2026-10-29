"""Configuracion de logging comun: consola + fichero rotativo.

El dashboard y la ventana de escritorio comparten esto para que, cuando algo
falla de madrugada, quede rastro en `logs/vigia.log` y no solo en una consola
que ya no existe.
"""
from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

FORMATO = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_MAX_BYTES = 2_000_000
_COPIAS = 5

log = logging.getLogger(__name__)

# 2026-10-29 18:20:01,123 INFO vigia.web: mensaje
_LINEA = re.compile(
    r"^(?P<t>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d{3})?)\s+"
    r"(?P<nivel>[A-Z]+)\s+(?P<modulo>[^:]+):\s?(?P<msg>.*)$")


def leer_log(carpeta: str | Path = "logs", fichero: str = "vigia.log",
             limite: int = 200, nivel: str | None = None) -> dict:
    """Ultimas `limite` lineas del log, mas recientes primero.

    El parseo es tolerante: una linea que no sigue el formato comun se
    devuelve igualmente (nivel "?" y modulo vacio) para no ocultar trazas de
    errores raros. `nivel` filtra por INFO/WARNING/ERROR/DEBUG/CRITICAL.
    """
    raiz = Path(carpeta)
    destino = raiz / fichero
    try:
        limite = max(1, min(int(limite), 2000))
    except (TypeError, ValueError):
        limite = 200
    nivel = (nivel or "").strip().upper() or None
    rotaciones = 0
    try:
        rotaciones = sum(1 for p in raiz.iterdir()
                         if p.is_file() and p.name.startswith(fichero + "."))
    except OSError:
        rotaciones = 0
    try:
        tamano = destino.stat().st_size
    except OSError:
        tamano = 0
    lineas: list[dict] = []
    try:
        with destino.open("r", encoding="utf-8", errors="replace") as fh:
            for cruda in fh:
                texto = cruda.rstrip("\r\n")
                if not texto:
                    continue
                coincidencia = _LINEA.match(texto)
                if coincidencia:
                    ficha = coincidencia.groupdict()
                    ficha["nivel"] = ficha["nivel"].upper()
                    ficha["modulo"] = ficha["modulo"].strip()
                else:
                    ficha = {"t": "", "nivel": "?", "modulo": "", "msg": texto}
                if nivel and ficha["nivel"] != nivel:
                    continue
                lineas.append(ficha)
    except OSError:
        log.exception("no se pudo leer el log %s", destino)
        return {"lineas": [], "tamano_bytes": tamano,
                "rotaciones": rotaciones}
    return {"lineas": lineas[-limite:][::-1],
            "tamano_bytes": tamano,
            "rotaciones": rotaciones}


def configurar_logging(nivel: int = logging.INFO,
                       carpeta: str | Path = "logs",
                       fichero: str = "vigia.log") -> Path | None:
    """Deja el logging listo y devuelve la ruta del log (None si no se pudo)."""
    raiz = logging.getLogger()
    raiz.setLevel(nivel)
    formato = logging.Formatter(FORMATO)

    if not any(isinstance(h, logging.StreamHandler)
               and not isinstance(h, logging.FileHandler)
               for h in raiz.handlers):
        consola = logging.StreamHandler(sys.stderr)
        consola.setFormatter(formato)
        raiz.addHandler(consola)

    try:
        destino = Path(carpeta) / fichero
        destino.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        raiz.exception("no se pudo crear la carpeta de logs")
        return None

    for h in raiz.handlers:
        if isinstance(h, logging.FileHandler) and \
                Path(h.baseFilename) == destino.resolve():
            return destino

    try:
        fichero_handler = RotatingFileHandler(
            destino, maxBytes=_MAX_BYTES, backupCount=_COPIAS, encoding="utf-8")
    except OSError:
        raiz.exception("no se pudo abrir el log %s", destino)
        return None
    fichero_handler.setFormatter(formato)
    raiz.addHandler(fichero_handler)
    return destino
