"""Carga y valida config.yaml. Devuelve dict con defaults seguros.

Ninguna regla mal escrita debe tumbar el sistema: se avisa en el log y se
usa el valor por defecto (ver docs/01-diseno.md, manejo de errores).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from yaml import safe_load

log = logging.getLogger(__name__)

DEFAULTS = {
    "almacen": "vigia.db",
    "permanencia_seg": 30,
    "timeout_objeto_seg": 10,
    "timeout_persona_seg": 10,
    "vigilar_desaparicion": [],
    "zonas": {},
    "franjas": [],
    "guardar_clips": True,
    "notificar": True,
    # actividades requiere el modelo de pose (descarga extra en primer uso):
    # off por defecto para tests y arranque ligero; se activa en config.yaml
    "actividades": False,
}

_HORA = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _numero(valor, defecto, clave: str):
    if isinstance(valor, bool):
        pass
    elif isinstance(valor, (int, float)):
        return valor
    elif isinstance(valor, str):
        try:
            return float(valor.strip())
        except ValueError:
            pass
    log.warning("config: %s no es numerico (%r); se usa %r",
                clave, valor, defecto)
    return defecto


def _booleano(valor, defecto, clave: str):
    if isinstance(valor, bool):
        return valor
    log.warning("config: %s no es booleano (%r); se usa %r",
                clave, valor, defecto)
    return defecto


def _lista_texto(valor, clave: str):
    if isinstance(valor, list) and all(isinstance(v, str) for v in valor):
        return list(valor)
    log.warning("config: %s debe ser lista de textos (%r); se ignora",
                clave, valor)
    return []


def _zonas(valor):
    if not isinstance(valor, dict):
        log.warning("config: zonas debe ser un mapa nombre -> caja (%r); "
                    "se ignora", valor)
        return {}
    zonas: dict[str, tuple[int, int, int, int]] = {}
    for nombre, caja in valor.items():
        if (not isinstance(caja, (list, tuple)) or len(caja) != 4
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                           for v in caja)):
            log.warning("config: zona %r mal formada (se esperan 4 numeros); "
                        "se ignora", nombre)
            continue
        zonas[str(nombre)] = tuple(int(v) for v in caja)
    return zonas


def _franjas(valor):
    if not isinstance(valor, list):
        log.warning("config: franjas debe ser una lista (%r); se ignora", valor)
        return []
    franjas = []
    for franja in valor:
        if not isinstance(franja, dict):
            log.warning("config: franja %r no es un mapa; se ignora", franja)
            continue
        desde = str(franja.get("desde", ""))
        hasta = str(franja.get("hasta", ""))
        if not (_HORA.match(desde) and _HORA.match(hasta)):
            log.warning("config: franja %r con horas invalidas (HH:MM); "
                        "se ignora", franja)
            continue
        franjas.append({"desde": desde, "hasta": hasta})
    return franjas


def _defaults() -> dict:
    return {k: (v.copy() if isinstance(v, (dict, list)) else v)
            for k, v in DEFAULTS.items()}


def cargar_config(ruta: str | Path = "config.yaml") -> dict:
    p = Path(ruta)
    cfg = _defaults()
    if not p.exists():
        log.info("config.yaml no existe; se usan valores por defecto")
        return cfg
    try:
        usuario = safe_load(p.read_text(encoding="utf-8"))
    except Exception as e:
        log.error("config.yaml ilegible (%s); usando defaults", e)
        return cfg
    if usuario is None:
        return cfg
    if not isinstance(usuario, dict):
        log.error("config.yaml debe ser un mapa clave: valor; usando defaults")
        return cfg

    for k, v in usuario.items():
        if v is not None:
            cfg[k] = v

    if not isinstance(cfg["almacen"], str) or not cfg["almacen"].strip():
        log.warning("config: almacen invalido (%r); se usa %r",
                    cfg["almacen"], DEFAULTS["almacen"])
        cfg["almacen"] = DEFAULTS["almacen"]

    for clave in ("permanencia_seg", "timeout_objeto_seg",
                  "timeout_persona_seg"):
        cfg[clave] = _numero(cfg[clave], DEFAULTS[clave], clave)

    cfg["vigilar_desaparicion"] = _lista_texto(
        cfg["vigilar_desaparicion"], "vigilar_desaparicion")
    cfg["zonas"] = _zonas(cfg["zonas"])
    cfg["franjas"] = _franjas(cfg["franjas"])
    cfg["guardar_clips"] = _booleano(
        cfg["guardar_clips"], DEFAULTS["guardar_clips"], "guardar_clips")
    cfg["notificar"] = _booleano(
        cfg["notificar"], DEFAULTS["notificar"], "notificar")
    if "actividades" not in cfg:
        cfg["actividades"] = DEFAULTS.get("actividades", False)
    cfg["actividades"] = _booleano(
        cfg["actividades"], DEFAULTS.get("actividades", False),
        "actividades")
    return cfg
