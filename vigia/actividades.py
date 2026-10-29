"""Clasificador de actividades por persona con pose (17 kpts) + objetos.

Actividades publicas: `durmiendo`, `tumbado`, `sentado`, `de pie`,
`agachado`, `caminando`, `usando movil`, `usando PC` y `desconocida`.

Heuristica geometrica (no ML) con **features normalizadas por el tamano del
cuerpo**: el mismo umbral vale para una persona cerca y otra al fondo.

- angulo del tronco respecto a la vertical
- angulo cadera-rodilla-tobillo (solo si tobillo plausible)
- altura de cadera relativa a la caja y proporcion de cuerpo por debajo
- aspecto de la caja, hombros/caderas visibles o no
- munecas respecto a hombros/cara; inclinacion de cabeza con nariz/ojos
- contexto de objetos (`laptop`, `keyboard`, `mouse`, `tv`) y cercania

Modo medio cuerpo: si no hay caderas/rodillas fiables NO se inventa "de pie"
ni "sentado"; se decide con hombros/cabeza/brazos y el contexto (mesa,
portatil, teclado) y, si la evidencia es debil, se devuelve `desconocida`.

`persona_caida` se detecta aparte (`DetectorCaidas`): transicion rapida de
de pie/caminando a tumbado + inmovilidad N segundos.
"""
from __future__ import annotations

import math
from collections import Counter, deque
from typing import Sequence

from .pose import (CADERA_DER, CADERA_IZQ, HOMBRO_DER, HOMBRO_IZQ,
                   MUNECA_DER, MUNECA_IZQ, NARIZ, OJO_DER, OJO_IZQ,
                   OREJA_DER, OREJA_IZQ, RODILLA_DER, RODILLA_IZQ,
                   TOBILLO_DER, TOBILLO_IZQ, PersonaPose)

_ACT_MINIMA = 3.0       # seg para anunciar una actividad estable
_VENTANA_VOTO = 2.0     # seg de lecturas que se votan antes de estabilizar

_CONF_KPT = 0.3

# muslo (cadera->rodilla, en vertical) frente al torso (hombro->cadera) a partir
# del cual se considera que las piernas cuelgan: de pie ~1.0, sentado < 0.5
_MUSLO_DE_PIE = 0.55
# alto/ancho de la caja: de pie y entero >~2; sentado ~1-1.6
_ASPECTO_TUMBADO = 1.5  # ancho/alto de la caja
_OBJ_PC = {"laptop", "keyboard", "mouse", "tv"}
_OBJ_MOVIL = {"cell phone"}

# umbrales de movimiento para "caminando": cuerpos/segundo
_VEL_CAMINANDO = 0.35
# una caida debe inmovilizarse este tiempo (config `caida_inmovil_seg`)
_CAIDA_INMOVIL_SEG = 5.0


def _media(puntos):
    pts = [p for p in puntos if p]
    if not pts:
        return None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def _punto(p: PersonaPose, idx: int, caja):
    """Keypoint con confianza y dentro de la caja (con margen).

    Evita el clasico (0,0) que devuelve YOLO cuando no ve el punto: con
    confianza alta pero fuera del cuerpo no debe entrar en la geometria.
    """
    try:
        if idx >= len(p.conf) or float(p.conf[idx]) < _CONF_KPT:
            return None
        x, y = float(p.kpts[idx][0]), float(p.kpts[idx][1])
        if not (math.isfinite(x) and math.isfinite(y)):
            return None
        w = max(caja[2] - caja[0], 1)
        h = max(caja[3] - caja[1], 1)
        mx, my = 0.35 * w + 2, 0.35 * h + 2
        if not (caja[0] - mx <= x <= caja[2] + mx
                and caja[1] - my <= y <= caja[3] + my):
            return None
        return x, y
    except (IndexError, TypeError, ValueError):
        return None


def _angulo(a, b, c) -> float:
    """Angulo en `b` (grados) del triangulo a-b-c."""
    v1 = (a[0] - b[0], a[1] - b[1])
    v2 = (c[0] - b[0], c[1] - b[1])
    n1 = math.hypot(*v1)
    n2 = math.hypot(*v2)
    if n1 < 1e-6 or n2 < 1e-6:
        return 180.0
    cos = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
    return math.degrees(math.acos(cos))


def _angulo_vertical(a, b) -> float:
    """Angulo (grados) del vector a->b respecto a la vertical (0 = vertical)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return 0.0
    return math.degrees(math.atan2(abs(dx), abs(dy)))


def _caracteristicas(p: PersonaPose) -> dict | None:
    caja = p.caja
    x1, y1, x2, y2 = caja
    w = max(x2 - x1, 1)
    box_h = max(y2 - y1, 1)
    hombros = _media([_punto(p, HOMBRO_IZQ, caja),
                      _punto(p, HOMBRO_DER, caja)])
    caderas = _media([_punto(p, CADERA_IZQ, caja),
                      _punto(p, CADERA_DER, caja)])
    rodillas = _media([_punto(p, RODILLA_IZQ, caja),
                       _punto(p, RODILLA_DER, caja)])
    tobillos = _media([_punto(p, TOBILLO_IZQ, caja),
                       _punto(p, TOBILLO_DER, caja)])
    nariz = _punto(p, NARIZ, caja)
    ojos = _media([_punto(p, OJO_IZQ, caja), _punto(p, OJO_DER, caja)])
    orejas = _media([_punto(p, OREJA_IZQ, caja), _punto(p, OREJA_DER, caja)])
    munecas = [q for q in (_punto(p, MUNECA_IZQ, caja),
                           _punto(p, MUNECA_DER, caja)) if q]
    if hombros is None and caderas is None:
        return None
    torso = abs(caderas[1] - hombros[1]) if hombros and caderas else 0.0
    ancho_hombros = abs(
        (_punto(p, HOMBRO_DER, caja) or (0, 0))[0]
        - (_punto(p, HOMBRO_IZQ, caja) or (0, 0))[0]) if hombros else 0.0
    escala = max(torso, ancho_hombros, 0.30 * box_h, 1.0)
    tronco_ang = (_angulo_vertical(hombros, caderas)
                  if hombros and caderas else None)
    cabeza = nariz or ojos
    inclinacion_cabeza = None
    if nariz and ojos:
        inclinacion_cabeza = _angulo_vertical(ojos, nariz)
    return {
        "hombros": hombros, "caderas": caderas, "rodillas": rodillas,
        "tobillos": tobillos, "nariz": nariz, "ojos": ojos,
        "orejas": orejas, "cabeza": cabeza, "munecas": munecas,
        "w": w, "box_h": box_h, "torso": torso, "escala": escala,
        "aspecto": box_h / w, "tronco_ang": tronco_ang,
        "inclinacion_cabeza": inclinacion_cabeza,
        "cuello": (_media([hombros, cabeza])
                   if hombros and cabeza else hombros),
    }


def _tumbado(f: dict) -> bool:
    if f["caderas"] and f["hombros"]:
        if (abs(f["hombros"][1] - f["caderas"][1]) < 0.5 * f["box_h"]
                and f["w"] > f["box_h"] * _ASPECTO_TUMBADO):
            return True
    if f["tronco_ang"] is not None and f["tronco_ang"] >= 60:
        return True
    return False


def _postura(f: dict) -> str:
    """'tumbado' | 'de pie' | 'sentado' | 'agachado' | 'medio_cuerpo'."""
    if _tumbado(f):
        return "tumbado"
    caderas, rodillas = f["caderas"], f["rodillas"]
    if caderas and rodillas:
        muslo = rodillas[1] - caderas[1]
        tobillos = f["tobillos"]
        angulo_rodilla = (_angulo(caderas, rodillas, tobillos)
                          if tobillos else None)
        # rodilla claramente por debajo de la cadera y pierna extendida
        if muslo >= _MUSLO_DE_PIE * max(f["torso"], 1.0):
            if angulo_rodilla is not None and angulo_rodilla < 105:
                return "agachado"
            return "de pie"
        if (muslo < 0.25 * max(f["torso"], 1.0)
                and angulo_rodilla is not None and angulo_rodilla < 95):
            return "agachado"
        return "sentado"
    if caderas:
        # sin rodillas: cuanto cuerpo queda por debajo de la cadera decide
        return _sin_rodillas(f)
    # sin caderas: medio cuerpo; solo la cabeza/hombros no bastan
    return "medio_cuerpo"


def _sin_rodillas(f: dict) -> str:
    """Con cadera pero sin rodilla: cuerpo entero vs medio cuerpo/mesa."""
    # y de la cadera dentro de la caja (0 = arriba, 1 = abajo)
    # no tenemos el y1 original aqui, pero si el alto y la posicion relativa
    alto = f["box_h"]
    cadera_y = f["caderas"][1]
    # el torso ocupa desde hombros; estimamos el borde superior por hombros
    y1 = f["hombros"][1] - 0.15 * alto if f["hombros"] else cadera_y - 0.5 * alto
    debajo = (y1 + alto) - cadera_y
    if debajo > 1.3 * f["escala"]:
        return "de pie"          # se ve el cuerpo entero (piernas cortadas)
    if debajo <= 1.0 * f["escala"]:
        return "sentado"         # cadera al borde inferior: medio cuerpo
    return "medio_cuerpo"


def _movil_en_mano(p: PersonaPose, movil, f: dict) -> bool:
    """El movil esta dentro de la persona y, si se ven las munecas, cerca."""
    cx, cy = (movil.caja[0] + movil.caja[2]) / 2, (movil.caja[1] + movil.caja[3]) / 2
    x1, y1, x2, y2 = p.caja
    if not (x1 <= cx <= x2 and y1 <= cy <= y2):
        return False
    munecas = f["munecas"]
    if not munecas:
        # sin munecas visibles: vale si esta en la mitad superior del cuerpo
        return cy <= y1 + 0.7 * (y2 - y1)
    lado = max(f["escala"], 0.35 * f["box_h"], 1.0)
    return any(math.hypot(mx - cx, my - cy) <= 0.9 * lado
               for mx, my in munecas)


def _movil_por_gesto(f: dict) -> bool:
    """Muñeca junto a la cara + cabeza inclinada (aunque no se vea el movil)."""
    munecas = f["munecas"]
    cabeza = f["cabeza"]
    if not munecas or not cabeza:
        return False
    cerca = [m for m in munecas
             if math.hypot(m[0] - cabeza[0], m[1] - cabeza[1])
             <= 0.75 * f["escala"]]
    if not cerca:
        return False
    incl = f["inclinacion_cabeza"]
    # nariz/ojos disponibles: exigir algo de inclinacion o mirada abajo
    if incl is not None and incl >= 12:
        return True
    if f["nariz"] and f["hombros"]:
        return f["nariz"][1] > f["hombros"][1] - 0.2 * f["escala"]
    return True


def _caja_dentro(obj_caja, persona_caja, margen: float = 0.0) -> bool:
    cx, cy = (obj_caja[0] + obj_caja[2]) / 2, (obj_caja[1] + obj_caja[3]) / 2
    mx = margen * (persona_caja[2] - persona_caja[0])
    my = margen * (persona_caja[3] - persona_caja[1])
    return (persona_caja[0] - mx <= cx <= persona_caja[2] + mx
            and persona_caja[1] - my <= cy <= persona_caja[3] + my)


def _pc_contexto(p: PersonaPose, f: dict, objetos: Sequence) -> bool:
    """Objeto de escritorio dentro/cerca de la persona y a altura de mesa."""
    for o in objetos:
        if o.clase not in _OBJ_PC:
            continue
        if _caja_dentro(o.caja, p.caja, margen=0.08):
            return True
        # solape parcial importante (teclado delante, portatil tapado)
        ix1, iy1 = max(o.caja[0], p.caja[0]), max(o.caja[1], p.caja[1])
        ix2, iy2 = min(o.caja[2], p.caja[2]), min(o.caja[3], p.caja[3])
        if ix2 > ix1 and iy2 > iy1:
            area_o = max((o.caja[2] - o.caja[0]) * (o.caja[3] - o.caja[1]), 1)
            if (ix2 - ix1) * (iy2 - iy1) / area_o >= 0.25:
                return True
    # manos al frente, a la altura del pecho/cadera (teclado)
    hombros, munecas = f["hombros"], f["munecas"]
    if hombros and len(munecas) >= 2:
        y_manos = sum(m[1] for m in munecas) / len(munecas)
        if (hombros[1] + 0.1 * f["escala"] <= y_manos
                <= hombros[1] + 0.9 * f["escala"]):
            return True
    return False


def clasificar(p: PersonaPose, objetos: Sequence,
               contexto: dict | None = None) -> str:
    """Devuelve una etiqueta de actividad para una persona trackeada.

    `contexto` (opcional) lleva `velocidad_rel` (cuerpos/segundo) y
    `distinguir_tumbado`; sin el se mantiene el vocabulario antiguo
    (`durmiendo` para una persona horizontal).
    """
    try:
        f = _caracteristicas(p)
        if f is None:
            return "desconocida"
        ctx = contexto or {}
        postura = _postura(f)
        if postura == "tumbado":
            return ("tumbado" if ctx.get("distinguir_tumbado")
                    else "durmiendo")

        # usando movil: objeto en la mano o gesto de muñeca en la cara
        for o in objetos:
            if o.clase in _OBJ_MOVIL and _movil_en_mano(p, o, f):
                return "usando movil"
        if postura != "de pie" and _movil_por_gesto(f):
            return "usando movil"

        # caminando: desplazamiento sostenido del centro del track
        if (ctx.get("velocidad_rel", 0.0) >= _VEL_CAMINANDO
                and postura in ("de pie", "desconocida", "medio_cuerpo")):
            return "caminando"

        if postura == "de pie":
            return "de pie"
        if postura == "agachado":
            return "agachado"

        # sentado / medio cuerpo / desconocida: el contexto decide
        if postura in ("sentado", "medio_cuerpo"):
            if _pc_contexto(p, f, objetos):
                return "usando PC"
            return "sentado" if postura == "sentado" else "desconocida"
        return "desconocida"
    except Exception:
        return "desconocida"


class EstabilizadorAct:
    """Una actividad solo se anuncia si se mantiene >= min_seg.

    Antes de eso, las lecturas de los ultimos `ventana_seg` se votan: un frame
    suelto distinto (pose mal detectada) ya no reinicia el contador.
    La votacion (empate conserva la actual) es la histeresis: cuesta mas
    entrar en una actividad nueva que permanecer en la que ya estaba.
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


class DetectorCaidas:
    """De pie/caminando -> tumbado + inmovilidad => `persona_caida`.

    No emite actividades: devuelve `True` una sola vez por caida para que el
    modulo la publique como evento por el mismo camino que las actividades.
    """

    def __init__(self, activa: bool = True, inmovil_seg: float = _CAIDA_INMOVIL_SEG,
                 transicion_seg: float = 2.5, movimiento_px: float = 25.0):
        self.activa = bool(activa)
        self.inmovil_seg = float(inmovil_seg)
        self.transicion_seg = float(transicion_seg)
        self.movimiento_px = float(movimiento_px)
        self._estado: dict[int, dict] = {}

    def _olvidar(self, ahora: float) -> None:
        for pid in [p for p, e in self._estado.items()
                    if ahora - e["visto"] > 60]:
            del self._estado[pid]

    def actualizar(self, pid: int, postura: str, caja, ahora: float) -> bool:
        if not self.activa:
            return False
        self._olvidar(ahora)
        cx = (caja[0] + caja[2]) / 2
        cy = (caja[1] + caja[3]) / 2
        e = self._estado.get(pid)
        if e is None:
            e = self._estado[pid] = {
                "visto": ahora, "t_caida": None, "postura": postura,
                "x": cx, "y": cy, "anunciada": False}
            return False
        e["visto"] = ahora
        horizontal = postura in ("tumbado", "durmiendo")
        previa = e["postura"]
        if horizontal and previa in ("de pie", "caminando", "agachado"):
            e["t_caida"] = ahora
            e["x"], e["y"] = cx, cy
        if horizontal and e["t_caida"] is not None:
            movimiento = math.hypot(cx - e["x"], cy - e["y"])
            e["x"], e["y"] = cx, cy
            if movimiento <= self.movimiento_px:
                if (not e["anunciada"]
                        and ahora - e["t_caida"] >= self.inmovil_seg):
                    e["anunciada"] = True
                    e["postura"] = postura
                    return True
            else:
                # se movia: aun no es inmovilidad (puede estar en el suelo
                # moviendose); se reinicia el contador de quietud
                e["t_caida"] = ahora
        elif not horizontal:
            if e["t_caida"] is not None and ahora - e["t_caida"] > 3.0:
                e["t_caida"] = None
                e["anunciada"] = False
        e["postura"] = postura
        return False
