"""Rostros: deteccion (YuNet) + identidad (SFace) 100% local, con OpenCV.

- Deteccion de caras cajas+5-ptos
- Identidad por embedding (Tabla `rostros`) y similaridad coseno
- Los archivos onnx se descargan automaticamente la primera vez (~38MB)
"""
from __future__ import annotations

import logging
import urllib.request
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)

_URL_YUNET = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/"
    "face_detection_yunet/face_detection_yunet_2023mar.onnx")
_URL_SFACE = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/"
    "face_recognition_sface/face_recognition_sface_2021dec.onnx")

_SIM_MIN = 0.40   # umbral de recono; con francés cari; SFace default ~0.363-0.50
_TAM = (112, 112)


def _descargar(url: str, destino: Path) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(".tmp")
    log.info("descargando modelo %s -> %s", url.rsplit('/',1)[-1], destino.name)
    urllib.request.urlretrieve(url, tmp)
    tmp.rename(destino)  # garantizado en el filesystem mismo


class CarasDetector:
    """Combina YuNet (boxes) y SFace (embeddings) sobre la misma pasada frame."""

    def __init__(self, carpeta: str = "modelos"):
        self.carpeta = Path(carpeta)
        y = self.carpeta / "face_detection_yunet_2023mar.onnx"
        s = self.carpeta / "face_recognition_sface_2021dec.onnx"
        if not y.exists():
            _descargar(_URL_YUNET, y)
        if not s.exists():
            _descargar(_URL_SFACE, s)
        self._detector = cv2.FaceDetectorYN.create(str(y), "", (320, 320),
                                                   score_threshold=0.6)
        self._recon = cv2.FaceRecognizerSF.create(str(s), "")
        self.conocidos: dict[str, np.ndarray] = {}  # nombre -> embedding

    # ---------------------------------------------------------------- api
    def set_conocidos(self, conocidos: dict[str, np.ndarray]) -> None:
        """Repositorio de identidades: claves = nombre."""
        self.conocidos = dict(conocidos)

    def detectar(self, frame: np.ndarray) -> list[dict]:
        """Deteccion + embedding de cada cara. Devuelve:
        {caja, p5, sim, nombre}
        """
        alto, ancho = frame.shape[:2]
        self._detector.setInputSize((ancho, alto))
        ok_faces, caras = self._detector.detect(frame)
        salida: list[dict] = []
        if caras is None:
            return salida
        for cara in caras:
            x1, y1, x2, y2 = [int(v) for v in cara[:4]]
            p5 = cara[4:14].reshape(5, 2).astype(np.float32)
            sim = float(cara[14])
            alineado = None
            try:
                alineado = self._recon.alignCrop(frame, cara.reshape(1, -1))
            except Exception:
                alineado = None
            if alineado is None:
                continue
            emb = (self._recon.feature(alineado).flatten()
                   .astype(np.float32))
            salida.append({"caja": (x1, y1, x2, y2), "p5": p5, "conf": sim,
                           "emb": emb})
        return salida

    def identificar(self, emb: np.ndarray) -> tuple[str | None, float]:
        """Devuelve (nombre_conocido | None, mejor_similaridad)."""
        if not self.conocidos:
            return None, 0.0
        best_nombre, best_sim = None, -1.0
        for nombre, otro in self.conocidos.items():
            sim = float(np.dot(emb, otro) /
                        (np.linalg.norm(emb) * np.linalg.norm(otro) + 1e-6))
            if sim > best_sim:
                best_sim, best_nombre = sim, nombre
        if best_sim >= _SIM_MIN:
            return best_nombre, best_sim
        return None, best_sim
