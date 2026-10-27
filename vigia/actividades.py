"""Clasificador de actividades por persona con pose (17 kpts) + objetos.

Actividades: durmiendo, sentado, de pie, usando movil, usando PC.
Heuristica documentada (no ML): nupe, si falla devuelve 'desconocida'.
"""
from __future__ import annotations

from typing import Sequence

from .pose import CADERA_DER, CADERA_IZQ, HOMBRO_DER, HOMBRO_IZQ, PersonaPose, \
    RODILLA_DER, RODILLA_IZQ

# margenes en px sobre frame 480p; se escalan al tamano del frame
_SENTADO = 480.0
_ACT_MINIMA = 3.0  # seg para anunciar una actividad estable


def _caracteristicas(p: PersonaPose) -> dict | None:
    h = p.puntos(HOMBRO_IZQ, HOMBRO_DER)
    c = p.puntos(CADERA_IZQ, CADERA_DER)
    r = p.puntos(RODILLA_IZQ, RODILLA_DER)
    hombros = [x for x in h if x]
    caderas = [x for x in c if x]
    rodillas = [x for x in r if x]
    if len(hombros) < 1 or len(caderas) < 1:
        return None
    hy = sum(x[1] for x in hombros) / len(hombros)
    cy = sum(x[1] for x in caderas) / len(caderas)
    ky = (sum(x[1] for x in rodillas) / len(rodillas)) if rodillas else None
    # anchura vs altura de caja: tumbado es mas ancho que alto
    w = p.caja[2] - p.caja[0]
    box_h = p.caja[3] - p.caja[1]
    return {"hy": hy, "cy": cy, "ky": ky, "w": w, "box_h": box_h}


def clasificar(p: PersonaPose, objetos: Sequence) -> str:
    """Devuelve una etiqueta de actividad para una persona trackeada."""
    try:
        f = _caracteristicas(p)
        if f is None:
            return "desconocida"

        # tumbado/durmiendo: torso horizontal (hombros y caderas a misma altura)
        if abs(f["hy"] - f["cy"]) < 0.5 * max(f["box_h"], 1) \
                and f["w"] > f["box_h"] * 1.05 and f["box_h"] < _SENTADO * 0.4:
            return "durmiendo"

        # sentado: rodillas visibles y elevadas (por encima o cerca de caderas)
        sentado = f["ky"] is not None and f["ky"] < f["cy"] + 10

        # interaccion con objetos de una persona (contacto por solapamiento)
        wx1, wy1, wx2, wy2 = p.caja
        for o in objetos:
            ox1, oy1, ox2, oy2 = o.caja
            if not (ox2 < wx1 or wx2 < ox1 or oy2 < wy1 or wy2 < oy1):
                # solapa: el objeto esta en contacto (zona del torso/estructura)
                if o.clase == "cell phone":
                    return "usando movil"
                if o.clase in {"laptop", "tv", "keyboard", "mouse"}:
                    return "usando PC"

        return "sentado" if sentado else "de pie"
    except Exception:
        return "desconocida"


class EstabilizadorAct:
    """Una actividad solo se anuncia si se mantiene >= min_eman seg."""

    def __init__(self, min_seg: float = _ACT_MINIMA):
        self.min_seg = min_seg
        self.actual = "desconocida"
        self.desde = 0.0
        # sin esto, el primer tick con actividad "desconocida" peta
        self.anunciada = False

    def tick(self, actividad: str, ahora: float) -> str | None:
        """Acepta la lectura del frame; devuelve la actividad si acaba de estabilizar."""
        if actividad != self.actual:
            self.actual = actividad
            self.desde = ahora
            self.anunciada = False
            return None
        if (not self.anunciada
                and actividad != "desconocida"
                and ahora - self.desde >= self.min_seg):
            self.anunciada = True
            return actividad
        return None
