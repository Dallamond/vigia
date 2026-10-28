"""Carga y valida config.yaml. Devuelve dict con defaults seguros.

Ninguna regla mal escrita debe tumbar el sistema: se avisa en el log y se
usa el valor por defecto (ver docs/01-diseno.md, manejo de errores).
"""
from __future__ import annotations

import copy
import logging
import re
from pathlib import Path

from yaml import safe_load

from .zonas import PRIORIDADES, normalizar_zonas

log = logging.getLogger(__name__)

# raiz del proyecto: las rutas relativas (BD, clips, modelos, config) cuelgan
# de aqui y no del directorio desde el que se lance, para que una tarea
# programada no cree una BD vacia en otro sitio
RAIZ = Path(__file__).resolve().parent.parent


def ruta_proyecto(ruta: str | Path) -> Path:
    """Ruta absoluta; las relativas se resuelven contra la raiz del proyecto."""
    p = Path(ruta)
    return p if p.is_absolute() else RAIZ / p


_PRIORIDADES_DEFECTO = {
    "cara_desconocida": "critico",
    "persona_desconocida": "critico",
    "permanencia": "aviso",
    "objeto_desaparece": "aviso",
    "zona_restringida": "critico",
    "demasiadas_personas": "aviso",
    "merodeo": "aviso",
    "persona_inmovil": "critico",
    "persona_entra": "info",
    "persona_salio": "info",
    "objeto_aparece": "info",
    "persona_reconocida": "info",
    "actividad": "info",
}

DEFAULTS = {
    "almacen": "vigia.db",
    # servicio: las variables VIGIA_HOST / VIGIA_PUERTO siguen ganando
    "host": "127.0.0.1",
    "puerto": 8000,
    "permanencia_seg": 30,
    "timeout_objeto_seg": 10,
    "timeout_persona_seg": 10,
    "vigilar_desaparicion": [],
    "zonas": {},
    "franjas": [],
    "guardar_clips": True,
    "notificar": True,
    # captura (requiere reiniciar el dashboard para aplicarse)
    "ancho": 640,
    "alto": 480,
    # indice de la webcam (0 = la primera); se cambia en caliente desde el panel
    "camara": 0,
    # media: copia JPEG del instante de cada evento
    "miniaturas": True,
    # mantenimiento: 0 dias = no purgar
    "retencion_dias": 30,
    "retencion_clips_dias": 14,
    "retencion_clips_mb": 2048,
    "mantener_cada_min": 60,
    # actividades requiere el modelo de pose (descarga extra en primer uso):
    # off por defecto para tests y arranque ligero; se activa en config.yaml
    "actividades": False,
    # reconocimiento de caras (fase 7): descarga modelos ONNX la primera vez
    "caras": False,
    # --- entradas/salidas de personas ---
    # segundos que un ID debe verse seguido antes de anunciar `persona_entra`
    "confirmar_entrada_seg": 1.0,
    # un ID nuevo que aparece donde acaba de desaparecer otro (en estos
    # segundos) se toma por la misma persona: evita entradas falsas
    "reasociar_seg": 8,
    # --- reglas por zona (bloque 4) ---
    # umbral para conocidos: 0 = nunca disparan permanencia
    "permanencia_conocidos_seg": 0,
    # re-aviso mientras la persona sigue en la zona (0 = no repetir)
    "reaviso_min": 0,
    # merodeo: K re-entradas dentro de la ventana
    "merodeo_entradas": 3,
    "merodeo_ventana_seg": 120,
    # inmovilidad (posible caida): 0 = desactivado
    "inmovil_seg": 0,
    "inmovil_px": 20,
    # maximo de personas global (0 = sin limite); cada zona puede tener el suyo
    "max_personas": 0,
    # --- Telegram (bloque 2). Los secretos van en .env, no aqui ---
    "telegram": False,
    "telegram_prioridades": _PRIORIDADES_DEFECTO,
    "telegram_max_hora": 20,
    "telegram_cooldown_seg": 300,
    # armado automatico por franjas (si es false, solo manual)
    "armado_auto": False,
    "armado_franjas": [],
    # avisos del sistema
    "avisos_camara_seg": 300,
    "avisos_disco_gb": 5,
}

_LIMITES = {
    "puerto": (1, 65535),
    "ancho": (160, 3840),
    "alto": (120, 2160),
    "camara": (0, 9),
    "retencion_dias": (0, 3650),
    "retencion_clips_dias": (0, 3650),
    "retencion_clips_mb": (0, 1024 * 1024),
    "mantener_cada_min": (1, 1440),
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


def _entero(valor, defecto: int, clave: str, minimo: int, maximo: int) -> int:
    numero = _numero(valor, defecto, clave)
    try:
        numero = int(round(float(numero)))
    except (TypeError, ValueError):
        numero = defecto
    return max(minimo, min(numero, maximo))


def _numero_positivo(valor, defecto, clave: str, maximo: float) -> float:
    """Numero > 0; un valor <= 0 o invalido cae al defecto con aviso."""
    numero = _numero(valor, defecto, clave)
    try:
        numero = float(numero)
    except (TypeError, ValueError):
        numero = defecto
    if numero <= 0:
        log.warning("config: %s debe ser mayor que 0 (%r); se usa %r",
                    clave, valor, defecto)
        return float(defecto)
    return min(numero, maximo)


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
    """Zonas nuevas (objetos) o antiguas (`nombre: [x1,y1,x2,y2]`)."""
    return normalizar_zonas(valor, log_errores=True)


def _prioridades(valor):
    """Mapa tipo de evento -> prioridad, completado con los defaults."""
    salida = dict(_PRIORIDADES_DEFECTO)
    if not isinstance(valor, dict):
        log.warning("config: telegram_prioridades debe ser un mapa (%r); "
                    "se usan las prioridades por defecto", valor)
        return salida
    for tipo, prioridad in valor.items():
        texto = str(prioridad).strip().lower()
        if texto not in PRIORIDADES:
            log.warning("config: prioridad %r de %r invalida (info/aviso/"
                        "critico); se ignora", prioridad, tipo)
            continue
        salida[str(tipo)] = texto
    return salida


def _entero_no_negativo(valor, defecto, clave: str, maximo: int = 86400) -> int:
    numero = _numero(valor, defecto, clave)
    try:
        numero = int(round(float(numero)))
    except (TypeError, ValueError):
        numero = defecto
    if numero < 0:
        log.warning("config: %s no puede ser negativo (%r); se usa %r",
                    clave, valor, defecto)
        return defecto
    return min(numero, maximo)


def _host(valor) -> str:
    texto = str(valor or "").strip()
    if not texto:
        log.warning("config: host vacio; se usa %r", DEFAULTS["host"])
        return DEFAULTS["host"]
    return texto


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
    return copy.deepcopy(DEFAULTS)


def cargar_config(ruta: str | Path | None = None) -> dict:
    p = Path(ruta) if ruta is not None else RAIZ / "config.yaml"
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
    for k in usuario:
        if k not in DEFAULTS:
            log.warning("config: clave desconocida %r (se ignora; revisa "
                        "el nombre)", k)

    if not isinstance(cfg["almacen"], str) or not cfg["almacen"].strip():
        log.warning("config: almacen invalido (%r); se usa %r",
                    cfg["almacen"], DEFAULTS["almacen"])
        cfg["almacen"] = DEFAULTS["almacen"]

    for clave in ("permanencia_seg", "timeout_objeto_seg",
                  "timeout_persona_seg"):
        cfg[clave] = _numero_positivo(cfg[clave], DEFAULTS[clave], clave,
                                      86400.0)

    cfg["vigilar_desaparicion"] = _lista_texto(
        cfg["vigilar_desaparicion"], "vigilar_desaparicion")
    cfg["zonas"] = _zonas(cfg["zonas"])
    cfg["franjas"] = _franjas(cfg["franjas"])
    cfg["guardar_clips"] = _booleano(
        cfg["guardar_clips"], DEFAULTS["guardar_clips"], "guardar_clips")
    cfg["notificar"] = _booleano(
        cfg["notificar"], DEFAULTS["notificar"], "notificar")
    cfg["miniaturas"] = _booleano(
        cfg["miniaturas"], DEFAULTS["miniaturas"], "miniaturas")
    for clave, (minimo, maximo) in _LIMITES.items():
        cfg[clave] = _entero(cfg[clave], DEFAULTS[clave], clave, minimo, maximo)
    if "actividades" not in cfg:
        cfg["actividades"] = DEFAULTS.get("actividades", False)
    cfg["actividades"] = _booleano(
        cfg["actividades"], DEFAULTS.get("actividades", False),
        "actividades")
    cfg["caras"] = _booleano(
        cfg["caras"], DEFAULTS.get("caras", False), "caras")

    # --- servicio y bloque 4 ------------------------------------------
    cfg["host"] = _host(cfg.get("host"))
    for clave in ("permanencia_conocidos_seg", "reaviso_min",
                  "merodeo_ventana_seg", "inmovil_seg", "inmovil_px",
                  "max_personas", "telegram_max_hora", "telegram_cooldown_seg",
                  "avisos_camara_seg", "avisos_disco_gb"):
        cfg[clave] = _entero_no_negativo(cfg.get(clave), DEFAULTS[clave], clave)
    cfg["merodeo_entradas"] = max(
        1, _entero_no_negativo(cfg.get("merodeo_entradas"),
                               DEFAULTS["merodeo_entradas"],
                               "merodeo_entradas", maximo=100))
    cfg["telegram"] = _booleano(
        cfg.get("telegram"), DEFAULTS["telegram"], "telegram")
    cfg["telegram_prioridades"] = _prioridades(
        cfg.get("telegram_prioridades"))
    cfg["armado_auto"] = _booleano(
        cfg.get("armado_auto"), DEFAULTS["armado_auto"], "armado_auto")
    cfg["armado_franjas"] = _franjas(cfg.get("armado_franjas") or [])
    return cfg
