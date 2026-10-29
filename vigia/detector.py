"""Detector YOLO con GPU (si está disponible) y salida tipada Deteccion."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Sequence

from ultralytics import YOLO

from .apariencia import huella_torso

log = logging.getLogger(__name__)

_CAJA = tuple[int, int, int, int]  # x1, y1, x2, y2


@dataclass(frozen=True)
class Deteccion:
    clase: str
    conf: float
    caja: _CAJA
    # histograma HSV del torso (solo personas, si `con_huella`); no entra en
    # la comparacion/representacion para no romper usos antiguos
    huella: object = field(default=None, repr=False, compare=False)

    @property
    def es_persona(self) -> bool:
        return self.clase == "person"


class Detector:
    """Envuelve YOLO11n y devuelve Deteccion(es) limpias."""

    def __init__(self, modelo: str = "yolo11n.pt", conf: float = 0.4,
                 clases: Sequence[str] | None = None,
                 con_huella: bool = True, imgsz: int = 640):
        self.model = YOLO(modelo)
        self.conf = conf
        self.imgsz = int(imgsz)
        # la huella del torso es casi gratis y permite reasociar por ropa
        self.con_huella = bool(con_huella)
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

    def _args(self, conf_min: float | None, track: bool) -> dict:
        conf = conf_min if conf_min is not None else self.conf
        args = {"conf": conf, "verbose": False, "imgsz": self.imgsz}
        if self.clases:
            args["classes"] = self.clases
        if track:
            args["persist"] = True
        return args

    def detectar(self, frame, conf_min: float | None = None) -> list[Deteccion]:
        """Ejecuta inferencia sobre un frame BGR y devuelve detecciones."""
        resultados = self.model.predict(frame, **self._args(conf_min, False))
        return self._a_detecciones(resultados, frame)

    def detectar_track(self, frame, conf_min: float | None = None
                       ) -> tuple[list[Deteccion], dict[int, int]]:
        """Detección con ByteTrack. Devuelve (detecciones, {indice_deteccion: id_tracking}).

        Solo las cajas que completan el tracking llevan id asociado.
        """
        resultados = self.model.track(frame, **self._args(conf_min, True))
        try:
            from .tracking import registrar_dimensiones
            alto, ancho = frame.shape[:2]
            registrar_dimensiones(ancho, alto)
        except Exception:
            pass
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
                clase = self._nombres[int(id_clase)]
                caja_t = tuple(int(v) for v in caja)
                huella = (huella_torso(frame, caja_t)
                          if self.con_huella and clase == "person" else None)
                salida.append(Deteccion(
                    clase=clase, conf=float(conf_v), caja=caja_t,
                    huella=huella))
        return salida, ids_por_indice

    def _a_detecciones(self, resultados, frame=None) -> list[Deteccion]:
        salida: list[Deteccion] = []
        for r in resultados:
            if r.boxes is None:
                continue
            for caja, conf_v, id_clase in zip(
                    r.boxes.xyxy.cpu().numpy(),
                    r.boxes.conf.cpu().numpy(),
                    r.boxes.cls.cpu().numpy()):
                clase = self._nombres[int(id_clase)]
                caja_t = tuple(int(v) for v in caja)
                huella = (huella_torso(frame, caja_t)
                          if self.con_huella and frame is not None
                          and clase == "person" else None)
                salida.append(Deteccion(
                    clase=clase, conf=float(conf_v), caja=caja_t,
                    huella=huella))
        return salida
