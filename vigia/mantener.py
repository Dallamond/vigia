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

    # stats una sola vez: si otro hilo borra un fichero entre medias, no peta
    info: list[tuple[Path, float, int]] = []
    try:
        for p in raiz.iterdir():
            if not p.is_file() or p.suffix.lower() not in _EXTENSIONES:
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            info.append((p, st.st_mtime, st.st_size))
    except OSError:
        log.exception("no se pudo leer la carpeta de media %s", raiz)
        return {"borrados": 0, "liberado": 0}

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
        for p, mtime, _tam in info:
            if mtime < limite:
                _borrar(p)

    if max_bytes and max_bytes > 0:
        restantes = [(p, mtime, tam) for p, mtime, tam in info if p.exists()]
        total = sum(tam for _p, _mtime, tam in restantes)
        for p, _mtime, tam in sorted(restantes, key=lambda x: x[1]):
            if total <= max_bytes:
                break
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
        # el historial no debe apuntar a miniaturas/clips ya borrados
        resumen["media_huerfana"] = almacen.limpiar_media_huerfana(
            clips.carpeta)
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
