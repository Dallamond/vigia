"""Carga y valida config.yaml. Devuelve dict con defaults seguros."""
from __future__ import annotations

import logging
from pathlib import Path

from yaml import safe_load

log = logging.getLogger(__name__)

DEFAULTS = {
    "almacen": "vigia.db",
    "permanencia_seg": 30,
    "timeout_objeto_seg": 10,
    "vigilar_desaparicion": [],
    "zonas": {},
    "franjas": [],
    "guardar_clips": True,
    "notificar": True,
}


def cargar_config(ruta: str | Path = "config.yaml") -> dict:
    p = Path(ruta)
    cfg = dict(DEFAULTS)
    if not p.exists():
        log.info("config.yaml no existe; se usan valores por defecto")
        return cfg
    try:
        usuario = safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as e:
        log.error("config.yaml ilegible (%s); usando defaults", e)
        return cfg
    for k, v in usuario.items():
        if v is None:
            continue
        cfg[k] = v
    # validar tipos criticos
    if not isinstance(cfg["permanencia_seg"], (int, float)):
        log.error("permanencia_seg no numerico; default 30")
        cfg["permanencia_seg"] = 30
    return cfg
