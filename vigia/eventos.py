"""Motor de eventos: convierte detecciones/tracking en eventos con reglas.

Reglas soportadas (config dict):
- permanencia_seg: persona durante X seg -> evento `permanencia`
- zonales (zonas): dict nombre -> (x1, y1, x2, y2); si esta en una zona
  se anota en el detalle
- vigilar_desaparicion: clases; si desaparece > timeout_objeto_seg ->
  `objeto_desaparece`, y reaparece -> `objeto_aparece`
- franjas: [{desde: "22:00", hasta: "08:00"}] — durante franja se exige
  no hacer nada fuera (muto eventos fuera de franja, opcional 1 sola franja)

Todo evento se persiste en el Almacen (SQLite).
"""
from __future__ import annotations

import logging
import time as _time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable

from .almacen import Almacen
from .detector import Deteccion

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Evento:
    tipo: str
    detalle: str | None = None


def _cajas_solapan(caja_a, caja_b) -> bool:
    ax1, ay1, ax2, ay2 = caja_a
    bx1, by1, bx2, by2 = caja_b
    return not (ax2 < bx1 or bx2 < ax1 or ay2 < by1 or by2 < ay1)


class _PermanenciaEstado:
    __slots__ = ("desde", "zona", "anunciada")

    def __init__(self, t):
        self.desde = t
        self.zona: str | None = None
        self.anunciada = False


class MotorEventos:
    def __init__(self, almacen: Almacen, config: dict | None = None,
                 reloj: Callable[[], float] = _time.time):
        cfg = config or {}
        self.almacen = almacen
        self.reloj = reloj
        self.permanencia_seg = float(cfg.get("permanencia_seg", 30))
        self.vigilar: list[str] = list(cfg.get("vigilar_desaparicion", []))
        self.timeout_objeto_seg = float(cfg.get("timeout_objeto_seg", 10))
        self.zonas: dict[str, tuple[int, int, int, int]] = {
            k: tuple(v) for k, v in (cfg.get("zonas") or {}).items()}
        self.franjas: list[tuple[str, str]] = [
            (f.get("desde", "00:00"), f.get("hasta", "23:59"))
            for f in cfg.get("franjas", [])] if cfg.get("franjas") else []
        self._ids: dict[int, _PermanenciaEstado] = {}
        self._objetos_vistos: dict[str, float] = {}
        self._objeto_ausente: dict[str, bool] = {}

    # -- franja horaria ---------------------------------------------------
    def _hora_actual(self) -> str:
        return datetime.now().strftime("%H:%M")

    def _en_franja(self, hora: str) -> bool:
        if not self.franjas:
            return True
        hm = hora.split(":")
        minutos = int(hm[0]) * 60 + int(hm[1])
        for desde, hasta in self.franjas:
            d = int(desde.split(":")[0]) * 60 + int(desde.split(":")[1])
            h = int(hasta.split(":")[0]) * 60 + int(hasta.split(":")[1])
            if d <= h:
                if d <= minutos <= h:
                    return True
            else:  # franja nocturna entre medianoche (ej 22:00-08:00)
                if minutos >= d or minutos <= h:
                    return True
        return False

    def _zona_de(self, caja) -> str | None:
        for nombre, zcaja in self.zonas.items():
            if _cajas_solapan(caja, zcaja):
                return nombre
        if not self.zonas:
            return None
        return None

    # -- api ---------------------------------------------------------------
    def procesar(self, dets: Iterable[Deteccion],
                 ids_persona: dict[int, tuple] | None = None,
                 objetos_extra: Iterable[Deteccion] | None = None
                 ) -> list[Evento]:
        """Inspeccion de un instante: genera, persiste y devuelve eventos.

        - dets: detecciones de persona con caja
        - ids_persona: {id_tracking: caja}
        - objetos_extra: detecciones de objetos (no persona)
        """
        hora = self._hora_actual()
        if not self._en_franja(hora):
            return []
        eventos: list[Evento] = []
        ahora = self.reloj()
        dets_list = list(dets) if not isinstance(dets, Deteccion) else [dets]

        # permanencia por id de tracking
        # sin IDs de tracking se derivan IDs sinteticos de las detecciones
        if not ids_persona:
            personas = [d for d in dets_list
                        if getattr(d, "es_persona", False)]
            ids_persona = {-(i + 1): d.caja for i, d in enumerate(personas)}
        ids_persona = ids_persona or {}
        for pid, caja in ids_persona.items():
            st = self._ids.get(pid)
            if st is None:
                st = _PermanenciaEstado(ahora)
                st.zona = self._zona_de(caja)
                self._ids[pid] = st
                continue
            zona = self._zona_de(caja)
            if zona != st.zona:
                st.zona = zona
                st.desde = ahora
                st.anunciada = False
                continue
            telon = ahora - st.desde
            if telon >= self.permanencia_seg and not st.anunciada:
                detalle = f"permanencia zona={zona}" if zona else "permanencia"
                ev = Evento("permanencia", detalle)
                eventos.append(ev)
                st.anunciada = True

        # ids que ya no estan: limpiar estado para permitir futuros Avisos
        presentes = set(ids_persona)
        for pid in list(self._ids):
            if pid not in presentes:
                del self._ids[pid]

        # aparicion/desaparicion de objetos
        extras = [d for d in (objetos_extra or [])
                  if isinstance(d, Deteccion)]
        por_clase: dict[str, Deteccion] = {}
        for d in extras:
            if d.clase in self.vigilar:
                por_clase[d.clase] = d
        for clase in self.vigilar:
            visto = por_clase.get(clase)
            if visto is not None:
                self._objetos_vistos[clase] = ahora
                if self._objeto_ausente.get(clase):
                    self._objeto_ausente[clase] = False
                    ev = Evento("objeto_aparece", clase)
                    eventos.append(ev)
            else:
                primer_aviso = self._objetos_vistos.get(clase)
                if primer_aviso is None:
                    continue  # nunca visto: no hay nada desaparecido
                fuera = ahora - primer_aviso
                if fuera >= self.timeout_objeto_seg and not self._objeto_ausente.get(clase):
                    self._objeto_ausente[clase] = True
                    ev = Evento("objeto_desaparece", clase)
                    eventos.append(ev)

        # persistir
        for ev in eventos:
            self.almacen.guardar(ev.tipo, id_tracking=None, detalle=ev.detalle)
            log.info("evento %s (%s)", ev.tipo, ev.detalle)
        return eventos
