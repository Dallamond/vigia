"""Clasificador de actividades por persona con pose (17 kpts) + objetos.

Actividades: durmiendo, sentado, de pie, usando movil, usando PC.
Heuristica documentada (no ML); si falla devuelve 'desconocida'.

Criterio postural (lo que mas falseaba antes): "de pie" exige EVIDENCIA de
piernas extendidas hacia abajo. Que no se vean las rodillas (mesa, encuadre
cortado) ya no significa "de pie": una persona sentada ante una mesa casi
nunca las muestra.
"""
from __future__ import annotations

from collections import Counter, deque
from typing import Sequence

from .pose import CADERA_DER, CADERA_IZQ, HOMBRO_DER, HOMBRO_IZQ, PersonaPose, \
    RODILLA_DER, RODILLA_IZQ

_ACT_MINIMA = 3.0       # seg para anunciar una actividad estable
_VENTANA_VOTO = 2.0     # seg de lecturas que se votan antes de estabilizar

MUNECA_IZQ, MUNECA_DER = 9, 10
TOBILLO_IZQ, TOBILLO_DER = 15, 16

# muslo (cadera->rodilla, en vertical) frente al torso (hombro->cadera) a partir
# del cual se considera que las piernas cuelgan: de pie ~1.0, sentado < 0.5
_MUSLO_DE_PIE = 0.6
# alto/ancho de la caja: de pie y entero >~2; sentado ~1-1.6
_ASPECTO_DE_PIE = 1.6
_ASPECTO_DE_PIE_SIN_PIERNAS = 2.2
_ASPECTO_TUMBADO = 1.5  # ancho/alto de la caja

_OBJ_PC = {"laptop", "keyboard", "mouse"}


def _media(puntos):
    pts = [p for p in puntos if p]
    if not pts:
        return None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def _caracteristicas(p: PersonaPose) -> dict | None:
    hombros = _media(p.puntos(HOMBRO_IZQ, HOMBRO_DER))
    caderas = _media(p.puntos(CADERA_IZQ, CADERA_DER))
    if hombros is None or caderas is None:
        return None
    rodillas = _media(p.puntos(RODILLA_IZQ, RODILLA_DER))
    w = max(p.caja[2] - p.caja[0], 1)
    box_h = max(p.caja[3] - p.caja[1], 1)
    torso = abs(caderas[1] - hombros[1])
    return {"hombros": hombros, "caderas": caderas, "rodillas": rodillas,
            "w": w, "box_h": box_h, "torso": torso,
            "aspecto": box_h / w}


def _postura(f: dict) -> str:
    """'durmiendo' | 'sentado' | 'de pie' a partir de la geometria."""
    # tumbado: torso casi horizontal y caja claramente mas ancha que alta
    if (abs(f["hombros"][1] - f["caderas"][1]) < 0.5 * f["box_h"]
            and f["w"] > f["box_h"] * _ASPECTO_TUMBADO):
        return "durmiendo"
    if f["rodillas"] is not None and f["torso"] > 0:
        muslo = f["rodillas"][1] - f["caderas"][1]
        if muslo >= _MUSLO_DE_PIE * f["torso"] and f["aspecto"] >= 1.3:
            return "de pie"
        return "sentado"
    # sin rodillas: solo si la caja es muy alta (cuerpo entero) se da por de pie
    return "de pie" if f["aspecto"] >= _ASPECTO_DE_PIE_SIN_PIERNAS else "sentado"


def _movil_en_mano(p: PersonaPose, movil, f: dict) -> bool:
    """El movil esta dentro de la persona y, si se ven las munecas, cerca de una."""
    cx, cy = (movil.caja[0] + movil.caja[2]) / 2, (movil.caja[1] + movil.caja[3]) / 2
    x1, y1, x2, y2 = p.caja
    if not (x1 <= cx <= x2 and y1 <= cy <= y2):
        return False
    munecas = [m for m in p.puntos(MUNECA_IZQ, MUNECA_DER) if m]
    if not munecas:
        # sin munecas visibles: vale si esta en la mitad superior del cuerpo
        return cy <= y1 + 0.7 * (y2 - y1)
    lado = max(f["torso"], 0.35 * f["box_h"], 1.0)
    return any(((mx - cx) ** 2 + (my - cy) ** 2) ** 0.5 <= 0.9 * lado
               for mx, my in munecas)


def _caja_dentro(obj_caja, persona_caja) -> bool:
    cx, cy = (obj_caja[0] + obj_caja[2]) / 2, (obj_caja[1] + obj_caja[3]) / 2
    return (persona_caja[0] <= cx <= persona_caja[2]
            and persona_caja[1] <= cy <= persona_caja[3])


def clasificar(p: PersonaPose, objetos: Sequence) -> str:
    """Devuelve una etiqueta de actividad para una persona trackeada."""
    try:
        f = _caracteristicas(p)
        if f is None:
            return "desconocida"
        postura = _postura(f)
        if postura == "durmiendo":
            return "durmiendo"

        for o in objetos:
            if o.clase == "cell phone" and _movil_en_mano(p, o, f):
                return "usando movil"
        if postura != "de pie":
            for o in objetos:
                if o.clase in _OBJ_PC and _caja_dentro(o.caja, p.caja):
                    return "usando PC"
        return postura
    except Exception:
        return "desconocida"


class EstabilizadorAct:
    """Una actividad solo se anuncia si se mantiene >= min_seg.

    Antes de eso, las lecturas de los ultimos `ventana_seg` se votan: un frame
    suelto distinto (pose mal detectada) ya no reinicia el contador.
    """

    def __init__(self, min_seg: float = _ACT_MINIMA,
                 ventana_seg: float = _VENTANA_VOTO):
        self.min_seg = min_seg
        self.ventana_seg = ventana_seg
        self.actual = "desconocida"
        self.desde = 0.0
        # sin esto, el primer tick con actividad "desconocida" peta
        self.anunciada = False
        self._lecturas: deque[tuple[float, str]] = deque()

    def _votar(self, actividad: str, ahora: float) -> str:
        self._lecturas.append((ahora, actividad))
        while self._lecturas and ahora - self._lecturas[0][0] > self.ventana_seg:
            self._lecturas.popleft()
        cuentas = Counter(a for _, a in self._lecturas)
        ganador, n = cuentas.most_common(1)[0]
        # empate o mayoria no clara: se conserva la actividad actual
        if cuentas.get(self.actual, 0) >= n:
            return self.actual
        return ganador

    def tick(self, actividad: str, ahora: float) -> str | None:
        """Acepta la lectura del frame; devuelve la actividad si acaba de estabilizar."""
        actividad = self._votar(actividad, ahora)
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
