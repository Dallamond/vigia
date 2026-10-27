"""Detector de pose humana (YOLO11n-pose): 17 keypoints + tracking por ID.

Esto alimenta el detector de actividades (estar sentado, dormir...)."""
from __future__ import annotations

import logging

import cv2
import numpy as np
from ultralytics import YOLO

log = logging.getLogger(__name__)

# indices de keypoints COCO (17)
NARIZ, HOMBRO_IZQ, HOMBRO_DER = 0, 5, 6
CADERA_IZQ, CADERA_DER = 11, 12
RODILLA_IZQ, RODILLA_DER = 13, 14
UMBRAL_KPT = 0.3


class PersonaPose:
    """Una persona con su esqueleto en un frame dado."""

    __slots__ = ("track_id", "kpts", "conf", "caja")

    def __init__(self, track_id: int, kpts: np.ndarray, conf: np.ndarray,
                 caja: tuple):
        self.track_id = track_id
        self.kpts = kpts
        self.conf = conf
        self.caja = caja

    def punto(self, idx: int):
        """Devuelve (x, y) del keypoint si es digno de confianza, si no None."""
        try:
            if float(self.conf[idx]) < UMBRAL_KPT:
                return None
            return float(self.kpts[idx][0]), float(self.kpts[idx][1])
        except (IndexError, TypeError):
            return None

    def puntos(self, *indices):
        return [self.punto(i) for i in indices]


class PoseDetector:
    """Envuelve YOLO11n-pose con seguimiento: usar con procesar(frame)."""

    def __init__(self, modelo: str = "yolo11n-pose.pt", conf: float = 0.35):
        self.model = YOLO(modelo)
        self.conf = conf

    def detectar(self, frame) -> list[PersonaPose]:
        resultados = self.model.track(
            frame, conf=self.conf, verbose=False, persist=True)
        salida: list[PersonaPose] = []
        for r in resultados:
            if r.boxes is None or r.keypoints is None:
                continue
            ids = (r.boxes.id.cpu().numpy().astype(int)
                   if r.boxes.id is not None else None)
            for i in range(len(r.boxes)):
                kp_xy = r.keypoints.xy[i].cpu().numpy()
                kp_conf = (r.keypoints.conf[i].cpu().numpy()
                           if r.keypoints.conf is not None
                           else np.zeros(17, dtype=np.float32))
                caja = tuple(int(v) for v in r.boxes.xyxy[i].cpu().numpy())
                track = int(ids[i]) if ids is not None else i
                salida.append(PersonaPose(track, kp_xy, kp_conf, caja))
        return salida


def dibujar_pose(frame, poses) -> None:
    """Dibuja esqueleto simple (caja + puntos) sobre el frame."""
    for p in poses:
        x1, y1, x2, y2 = p.caja
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 200, 200), 1)
        for i in range(0, 17, 2):
            pt = p.punto(i)
            if pt:
                cv2.circle(frame, (int(pt[0]), int(pt[1])), 3, (0, 200, 200), -1)
