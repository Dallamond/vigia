"""Estado en vivo para la GUI (`GET /vivo`).

La GUI dibuja encima del vídeo un canvas con cajas y esqueleto; recalcular
todo por petición seria caro, asi que el hilo de analisis publica aqui sus
resultados y `snapshot_vivo()` devuelve una copia cacheada.

Contrato (ver docs/prompts/NOTAS.md, "Contrato /vivo"):

    {"t": float, "ancho": int, "alto": int, "fps": float,
     "personas": [{"id", "nombre", "caja", "actividad",
                   "actividad_cruda", "desde_seg", "kpts", "zona", "conf"}],
     "objetos": [{"clase", "caja", "conf"}],
     "caras": [{"caja", "nombre"}]}

Coordenadas en pixeles del frame que sirve `/video.mjpg`. `kpts` son 17
puntos `[x, y, conf]` en orden COCO.
"""
from __future__ import annotations

import threading
import time

from fastapi import APIRouter, Response

router = APIRouter()

_CACHE_SEG = 0.15
_lock = threading.Lock()
_personas: dict[int, dict] = {}
_caras: list[dict] = []
_objetos: list[dict] = []
_meta = {"ancho": 0, "alto": 0, "t": 0.0}
_cache: dict | None = None
_cache_t = 0.0


def _ahora() -> float:
    return time.time()


def limpiar() -> None:
    global _cache, _cache_t
    with _lock:
        _personas.clear()
        _caras.clear()
        _objetos.clear()
        _meta["t"] = 0.0
        _cache = None
        _cache_t = 0.0


def publicar_frame(ancho: int, alto: int, objetos=None) -> None:
    with _lock:
        _meta["ancho"] = int(ancho)
        _meta["alto"] = int(alto)
        _meta["t"] = _ahora()
        if objetos is not None:
            _objetos[:] = list(objetos)


def publicar_persona(pid: int, caja, kpts=None, actividad: str | None = None,
                     actividad_cruda: str | None = None,
                     conf: float = 0.0, desde: float | None = None) -> None:
    """Actualiza una persona (se conserva lo que no se pase)."""
    with _lock:
        p = _personas.get(int(pid), {})
        p["id"] = int(pid)
        p["caja"] = [int(v) for v in caja]
        if kpts is not None:
            p["kpts"] = [[round(float(x), 1), round(float(y), 1),
                          round(float(c), 3)] for x, y, c in kpts]
        if actividad is not None:
            p["actividad"] = actividad
            if desde is not None:
                p["desde_seg"] = float(desde)
        if actividad_cruda is not None:
            p["actividad_cruda"] = actividad_cruda
        p["conf"] = float(conf)
        p["t"] = _ahora()
        _personas[int(pid)] = p


def publicar_identidad(pid: int, nombre: str | None) -> None:
    with _lock:
        p = _personas.get(int(pid))
        if p is not None:
            p["nombre"] = nombre


def publicar_caras(caras) -> None:
    with _lock:
        _caras[:] = [{"caja": [int(v) for v in c["caja"]],
                      "nombre": c.get("nombre")} for c in caras]


def olvidar_personas(presentes: set[int]) -> None:
    with _lock:
        for pid in [p for p in _personas if p not in presentes]:
            del _personas[pid]


def _zona_de(caja):
    try:
        from . import web  # import perezoso: evita circular al montar el router
        motor = getattr(web, "_motor_compartido", None)
        if motor is None:
            return None
        for nombre, zona in motor.zonas.items():
            if not getattr(zona, "excluir", False) and zona.contiene(caja):
                return nombre
    except Exception:
        return None
    return None


def _fps_y_t() -> tuple[float, float]:
    try:
        from . import web
        _, fps, _, _, _ = web.estado.snapshot()
        return float(fps), _meta.get("t") or _ahora()
    except Exception:
        return 0.0, _meta.get("t") or _ahora()


def snapshot_vivo() -> dict:
    """Estado consolidado (cacheado ~150 ms)."""
    global _cache, _cache_t
    ahora = _ahora()
    with _lock:
        if _cache is not None and ahora - _cache_t < _CACHE_SEG:
            return _cache
        fps, t = _fps_y_t()
        personas = []
        for pid in sorted(_personas):
            p = dict(_personas[pid])
            p.setdefault("nombre", None)
            p.setdefault("actividad", "desconocida")
            p.setdefault("actividad_cruda", "desconocida")
            p.setdefault("kpts", [])
            p.setdefault("conf", 0.0)
            p["zona"] = _zona_de(p.get("caja")) if p.get("caja") else None
            personas.append({k: p.get(k) for k in (
                "id", "nombre", "caja", "actividad", "actividad_cruda",
                "desde_seg", "kpts", "zona", "conf")})
        _cache = {
            "t": round(float(t), 3),
            "ancho": int(_meta.get("ancho") or 0),
            "alto": int(_meta.get("alto") or 0),
            "fps": round(float(fps), 1),
            "personas": personas,
            "objetos": [dict(o) for o in _objetos],
            "caras": [dict(c) for c in _caras],
        }
        _cache_t = ahora
        return _cache


@router.get("/vivo")
def vivo(response: Response) -> dict:
    """Personas, esqueleto, objetos y caras del ultimo frame analizado."""
    response.headers["Cache-Control"] = "no-store"
    return snapshot_vivo()
