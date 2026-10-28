"""Tracking de personas por ID (ByteTrack vía Ultralytics) con motor de
eventos entra/sale, sin depender de YOLO (se alimenta con IDs ya trackeados)."""
from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

# Evento = (tipo, id_tracking)
EVENTO_ENTRA = "persona_entra"
EVENTO_SALIO = "persona_salio"


class _PersonaEstado:
    __slots__ = ("dentro", "primera_vez", "ultima_vez", "anunciada")

    def __init__(self, ahora: float):
        self.dentro = True
        self.primera_vez = ahora
        self.ultima_vez = ahora
        self.anunciada = False


class RastreadorPersonas:
    """A partir de los IDs de personas de cada frame genera eventos:

    - `persona_entra`: aparece un ID nuevo o un ID que ya había salido
    - `persona_salio`: un ID deja de verse durante `timeout_seg`
    """

    def __init__(self, timeout_seg: float = 10.0, confirmar_seg: float = 0.0):
        self.timeout_seg = timeout_seg
        # un ID debe verse este tiempo seguido antes de anunciar `persona_entra`:
        # filtra detecciones fugaces (sombras, falsos positivos de un frame)
        self.confirmar_seg = confirmar_seg
        self._por_id: dict[int, _PersonaEstado] = {}

    def actualizar(self, ids_presentes: set[int]) -> tuple[tuple[str, int], ...]:
        """Pasa los IDs vistos en este frame; devuelve los eventos generados ahora."""
        ahora = time.monotonic()
        eventos: list[tuple[str, int]] = []

        # salidas: sin verse durante timeout. Se borra el estado para que el
        # diccionario no crezca sin limite (ByteTrack puede crear miles de IDs
        # en una sesion larga) y para que una reaparicion vuelva a anunciarse.
        # Si nunca llego a anunciarse la entrada, tampoco se anuncia la salida.
        for pid, st in list(self._por_id.items()):
            if ahora - st.ultima_vez > self.timeout_seg:
                del self._por_id[pid]
                if st.anunciada:
                    eventos.append((EVENTO_SALIO, pid))
                    log.info("persona_salio id=%s", pid)

        # entradas: cualquier ID que no estabamos siguiendo
        for pid in ids_presentes:
            st = self._por_id.get(pid)
            if st is None:
                st = self._por_id[pid] = _PersonaEstado(ahora)
            else:
                st.ultima_vez = ahora
            if not st.anunciada and ahora - st.primera_vez >= self.confirmar_seg:
                st.anunciada = True
                eventos.append((EVENTO_ENTRA, pid))
                log.info("persona_entra id=%s", pid)
        return tuple(eventos)

    def ids_dentro(self) -> set[int]:
        return {pid for pid, st in self._por_id.items() if st.dentro}

    def reset(self) -> None:
        self._por_id.clear()


def _iou(a, b) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    inter = (ix2 - ix1) * (iy2 - iy1)
    union = ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1])
             - inter)
    return inter / union if union > 0 else 0.0


def _distancia_centros(a, b) -> float:
    return ((((a[0] + a[2]) - (b[0] + b[2])) / 2) ** 2
            + (((a[1] + a[3]) - (b[1] + b[3])) / 2) ** 2) ** 0.5


class ReasociadorIds:
    """IDs de persona estables aunque ByteTrack cambie el suyo.

    ByteTrack (sin re-identificacion) crea un ID nuevo cada vez que pierde a
    alguien unos frames (oclusion, silla, baja confianza). Sin esto cada
    cambio es un `persona_entra` falso y reinicia la permanencia. Aqui, un ID
    nuevo que aparece donde acaba de desaparecer otro (solape o cercania de
    cajas, dentro de `ventana_seg`) se considera la misma persona.
    """

    def __init__(self, ventana_seg: float = 8.0, iou_min: float = 0.15,
                 dist_rel: float = 0.6, reloj=time.monotonic):
        self.ventana_seg = ventana_seg
        self.iou_min = iou_min
        self.dist_rel = dist_rel
        self.reloj = reloj
        self._alias: dict[int, int] = {}                 # id crudo -> estable
        self._ultimo: dict[int, tuple[tuple, float]] = {}  # estable -> (caja, t)
        self._raw_visto: dict[int, float] = {}

    def mapear(self, ids_por_indice: dict[int, int], dets) -> dict[int, int]:
        """{indice: id_crudo} -> {indice: id_estable} (solo personas se tocan)."""
        ahora = self.reloj()
        salida: dict[int, int] = {}
        presentes: set[int] = set()
        pendientes: list[int] = []
        for i, raw in ids_por_indice.items():
            if not dets[i].es_persona:
                salida[i] = raw
            elif raw in self._alias:
                salida[i] = self._alias[raw]
                presentes.add(salida[i])
            else:
                pendientes.append(i)
        for i in pendientes:
            raw = ids_por_indice[i]
            caja = dets[i].caja
            mejor, mejor_d = None, float("inf")
            for estable, (caja_vieja, t) in self._ultimo.items():
                if estable in presentes or ahora - t > self.ventana_seg:
                    continue
                d = _distancia_centros(caja_vieja, caja)
                lado = max(caja_vieja[2] - caja_vieja[0],
                           caja_vieja[3] - caja_vieja[1], 1)
                if (_iou(caja_vieja, caja) >= self.iou_min
                        or d <= self.dist_rel * lado) and d < mejor_d:
                    mejor, mejor_d = estable, d
            estable = mejor if mejor is not None else raw
            if mejor is not None:
                log.info("id %s reasociado a id %s", raw, mejor)
            self._alias[raw] = estable
            salida[i] = estable
            presentes.add(estable)
        for i, estable in salida.items():
            if dets[i].es_persona:
                self._ultimo[estable] = (dets[i].caja, ahora)
                self._raw_visto[ids_por_indice[i]] = ahora
        # poda: no guardar historia de personas que ya no estan
        for estable in [e for e, (_, t) in self._ultimo.items()
                        if ahora - t > self.ventana_seg * 4]:
            del self._ultimo[estable]
        for raw in [r for r, t in self._raw_visto.items()
                    if ahora - t > self.ventana_seg * 4]:
            del self._raw_visto[raw]
            self._alias.pop(raw, None)
        return salida

    def reset(self) -> None:
        self._alias.clear()
        self._ultimo.clear()
        self._raw_visto.clear()
