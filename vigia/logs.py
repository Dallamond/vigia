"""Configuracion de logging comun: consola + fichero rotativo.

El dashboard y la ventana de escritorio comparten esto para que, cuando algo
falla de madrugada, quede rastro en `logs/vigia.log` y no solo en una consola
que ya no existe.
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

FORMATO = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_MAX_BYTES = 2_000_000
_COPIAS = 5


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
