"""Apariencia barata para re-identificacion entre IDs (sin modelo extra).

No sustituye a un ReID de verdad: calcula un histograma HSV de la zona del
torso (centro-alto de la caja de la persona). Es barato (~0.1 ms), robusto a
cambios de postura y suficiente para distinguir dos personas con ropa
distinta cuando ByteTrack pierde el ID. Si el recorte no tiene pixeles
devuelve None y el reasociador cae a la geometria de siempre.
"""
from __future__ import annotations

import cv2
import numpy as np

BINS_H = 12
BINS_S = 8


def huella_torso(frame, caja, bins_h: int = BINS_H, bins_s: int = BINS_S):
    """Histograma HSV normalizado del torso, o None si no hay recorte."""
    if frame is None or caja is None:
        return None
    alto, ancho = frame.shape[:2]
    x1, y1, x2, y2 = (int(v) for v in caja)
    w, h = x2 - x1, y2 - y1
    if w < 4 or h < 8:
        return None
    # torso: mitad central en horizontal, de 10% a 55% en vertical (evita
    # cabeza y piernas; con medio cuerpo la zona sigue siendo la chaqueta)
    rx1 = max(0, min(ancho - 1, x1 + int(0.25 * w)))
    rx2 = max(rx1 + 1, min(ancho, x1 + int(0.75 * w)))
    ry1 = max(0, min(alto - 1, y1 + int(0.10 * h)))
    ry2 = max(ry1 + 1, min(alto, y1 + int(0.55 * h)))
    recorte = frame[ry1:ry2, rx1:rx2]
    if recorte.size == 0:
        return None
    hsv = cv2.cvtColor(recorte, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [bins_h, bins_s],
                        [0, 180, 0, 256])
    if hist is None or not np.isfinite(hist).all() or hist.sum() <= 0:
        return None
    return cv2.normalize(hist, hist).flatten().astype(np.float32)


def similitud(h1, h2) -> float:
    """Correlacion de histogramas en [-1, 1]; 0 si falta alguno."""
    if h1 is None or h2 is None:
        return 0.0
    a = np.asarray(h1, dtype=np.float32).ravel()
    b = np.asarray(h2, dtype=np.float32).ravel()
    if a.size != b.size or a.size == 0:
        return 0.0
    a = a - a.mean()
    b = b - b.mean()
    den = float(np.linalg.norm(a) * np.linalg.norm(b))
    if den <= 0:
        return 0.0
    return float(np.clip(a.dot(b) / den, -1.0, 1.0))
