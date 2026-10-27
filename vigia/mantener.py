"""Mantenimiento periodico: purga de eventos viejos y de media en disco.

Uso manual:  python -m vigia.mantener
Tambien lo llama el bucle del dashboard cada `mantener_cada_min` minutos.
"""
from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

from .almacen import Almacen
from .config import cargar_config

log = logging.getLogger(__name__)

_EXTENSIONES = {".mp4", ".jpg", ".jpeg"}


def purgar_media(carpeta: str | Path, dias: int = 0,
                 max_bytes: int = 0) -> dict:
    """Borra media por antiguedad y, si hace falta, por tamano total.

    - `dias` > 0: borra ficheros con mtime mas antiguo que ese numero de dias.
    - `max_bytes` > 0: si tras lo anterior sigue habiendo demasiado, borra los
      mas antiguos hasta bajar del limite.
    Devuelve {"borrados": n, "liberado": bytes}.
    """
    raiz = Path(carpeta)
    if not raiz.exists():
        return {"borrados": 0, "liberado": 0}

    ficheros = [p for p in raiz.iterdir()
                if p.is_file() and p.suffix.lower() in _EXTENSIONES]
    borrados = 0
    liberado = 0

    def _borrar(p: Path) -> None:
        nonlocal borrados, liberado
        try:
            tam = p.stat().st_size
            p.unlink()
            borrados += 1
            liberado += tam
        except OSError:
            log.exception("no se pudo borrar %s", p)

    if dias and dias > 0:
        limite = time.time() - dias * 86400
        for p in list(ficheros):
            try:
                if p.stat().st_mtime < limite:
                    _borrar(p)
            except OSError:
                continue
        ficheros = [p for p in ficheros if p.exists()]

    if max_bytes and max_bytes > 0:
        restantes = sorted(ficheros, key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in restantes)
        for p in restantes:
            if total <= max_bytes:
                break
            try:
                tam = p.stat().st_size
            except OSError:
                continue
            _borrar(p)
            total -= tam

    if borrados:
        log.info("mantenimiento: %d ficheros borrados (%.1f MB)",
                 borrados, liberado / 1024 / 1024)
    return {"borrados": borrados, "liberado": liberado}


def mantener(almacen: Almacen, clips=None, config: dict | None = None) -> dict:
    """Ejecuta la politica de retencion completa. Devuelve un resumen."""
    cfg = config or {}
    resumen: dict = {}

    dias_eventos = int(cfg.get("retencion_dias", 0) or 0)
    if dias_eventos > 0:
        resumen["eventos"] = almacen.purgar_eventos(dias_eventos)

    if clips is not None:
        resumen.update(purgar_media(
            clips.carpeta,
            dias=int(cfg.get("retencion_clips_dias", 0) or 0),
            max_bytes=int(cfg.get("retencion_clips_mb", 0) or 0) * 1024 * 1024,
        ))
        # los topes duros por numero de ficheros siguen vigentes
        clips.podar()
        clips.podar_miniaturas()
    return resumen


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Purga eventos y clips viejos")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--eventos", type=int, default=None,
                        help="dias de eventos a conservar (por defecto: config)")
    parser.add_argument("--media", type=int, default=None,
                        help="dias de clips/jpg a conservar (por defecto: config)")
    args = parser.parse_args()

    cfg = cargar_config(args.config)
    if args.eventos is not None:
        cfg["retencion_dias"] = args.eventos
    if args.media is not None:
        cfg["retencion_clips_dias"] = args.media

    from .clips import GuardadorClips

    almacen = Almacen(cfg.get("almacen", "vigia.db"))
    clips = GuardadorClips(carpeta="clips")
    try:
        resumen = mantener(almacen, clips, cfg)
        print(f"mantenimiento: {resumen}")
    finally:
        almacen.cerrar()


if __name__ == "__main__":
    main()
