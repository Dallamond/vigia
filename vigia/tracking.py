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
    __slots__ = ("dentro", "primera_vez", "ultima_vez")

    def __init__(self, ahora: float):
        self.dentro = True
        self.primera_vez = ahora
        self.ultima_vez = ahora


class RastreadorPersonas:
    """A partir de los IDs de personas de cada frame genera eventos:

    - `persona_entra`: aparece un ID nuevo o un ID que ya había salido
    - `persona_salio`: un ID deja de verse durante `timeout_seg`
    """

    def __init__(self, timeout_seg: float = 10.0):
        self.timeout_seg = timeout_seg
        self._por_id: dict[int, _PersonaEstado] = {}

    def actualizar(self, ids_presentes: set[int]) -> tuple[tuple[str, int], ...]:
        """Pasa los IDs vistos en este frame; devuelve los eventos generados ahora."""
        ahora = time.monotonic()
        eventos: list[tuple[str, int]] = []

        # salidas: sin verse durante timeout. Se borra el estado para que el
        # diccionario no crezca sin limite (ByteTrack puede crear miles de IDs
        # en una sesion larga) y para que una reaparicion vuelva a anunciarse.
        for pid, st in list(self._por_id.items()):
            if ahora - st.ultima_vez > self.timeout_seg:
                del self._por_id[pid]
                eventos.append((EVENTO_SALIO, pid))
                log.info("persona_salio id=%s", pid)

        # entradas: cualquier ID que no estabamos siguiendo
        for pid in ids_presentes:
            st = self._por_id.get(pid)
            if st is None:
                self._por_id[pid] = _PersonaEstado(ahora)
                eventos.append((EVENTO_ENTRA, pid))
                log.info("persona_entra id=%s", pid)
            else:
                st.ultima_vez = ahora
        return tuple(eventos)

    def ids_dentro(self) -> set[int]:
        return {pid for pid, st in self._por_id.items() if st.dentro}

    def reset(self) -> None:
        self._por_id.clear()
