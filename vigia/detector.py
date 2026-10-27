"""Detector YOLO con GPU (si está disponible) y salida tipada Deteccion."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

from ultralytics import YOLO

log = logging.getLogger(__name__)

_CAJA = tuple[int, int, int, int]  # x1, y1, x2, y2


@dataclass(frozen=True)
class Deteccion:
    clase: str
    conf: float
    caja: _CAJA

    @property
    def es_persona(self) -> bool:
        return self.clase == "person"


class Detector:
    """Envuelve YOLO11n y devuelve Deteccion(es) limpias."""

    def __init__(self, modelo: str = "yolo11n.pt", conf: float = 0.4,
                 clases: Sequence[str] | None = None):
        self.model = YOLO(modelo)
        self.conf = conf
        self._nombres: dict[int, str] = self.model.names
        self._nombres_a_id = {v: k for k, v in self._nombres.items()}
        self.clases: list[int] | None = None
        self.set_clases(clases)

    # -- api -------------------------------------------------------------
    def nombres(self) -> dict[int, str]:
        return dict(self._nombres)

    def set_clases(self, clases: Sequence[str] | None) -> None:
        """Restringe la detección a ciertas clases (None = todas)."""
        if not clases:
            self.clases = None
            return
        ids = []
        for c in clases:
            if c not in self._nombres_a_id:
                log.warning("Clase desconocida: %s (se ignora)", c)
                continue
            ids.append(self._nombres_a_id[c])
        self.clases = ids or None

    def detectar(self, frame, conf_min: float | None = None) -> list[Deteccion]:
        """Ejecuta inferencia sobre un frame BGR y devuelve detecciones."""
        conf = conf_min if conf_min is not None else self.conf
        args = {"conf": conf, "verbose": False}
        if self.clases:
            args["classes"] = self.clases
        resultados = self.model.predict(frame, **args)
        return self._a_detecciones(resultados)

    def detectar_track(self, frame, conf_min: float | None = None
                       ) -> tuple[list[Deteccion], dict[int, int]]:
        """Detección con ByteTrack. Devuelve (detecciones, {indice_deteccion: id_tracking}).

        Solo las cajas que completan el tracking llevan id asociado.
        """
        conf = conf_min if conf_min is not None else self.conf
        args = {"conf": conf, "verbose": False, "persist": True}
        if self.clases:
            args["classes"] = self.clases
        resultados = self.model.track(frame, **args)
        salida: list[Deteccion] = []
        ids_por_indice: dict[int, int] = {}
        for r in resultados:
            if r.boxes is None:
                continue
            ids = (r.boxes.id.cpu().numpy()
                   if r.boxes.id is not None else None)
            for idx, (caja, conf_v, id_clase) in enumerate(zip(
                    r.boxes.xyxy.cpu().numpy(),
                    r.boxes.conf.cpu().numpy(),
                    r.boxes.cls.cpu().numpy())):
                if ids is not None:
                    ids_por_indice[len(salida)] = int(ids[idx])
                salida.append(Deteccion(
                    clase=self._nombres[int(id_clase)],
                    conf=float(conf_v),
                    caja=tuple(int(v) for v in caja)))
        return salida, ids_por_indice

    def _a_detecciones(self, resultados) -> list[Deteccion]:
        salida: list[Deteccion] = []
        for r in resultados:
            if r.boxes is None:
                continue
            for caja, conf_v, id_clase in zip(
                    r.boxes.xyxy.cpu().numpy(),
                    r.boxes.conf.cpu().numpy(),
                    r.boxes.cls.cpu().numpy()):
                salida.append(Deteccion(
                    clase=self._nombres[int(id_clase)],
                    conf=float(conf_v),
                    caja=tuple(int(v) for v in caja)))
        return salida
