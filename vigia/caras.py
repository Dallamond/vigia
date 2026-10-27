"""Rostros: deteccion (YuNet) + identidad (SFace) 100% local, con OpenCV.

- Deteccion de caras: caja + 5 puntos de referencia
- Identidad por embedding (tabla `rostros`) y similitud coseno
- Los .onnx se descargan solos la primera vez (~38 MB)
"""
from __future__ import annotations

import logging
import os
import urllib.request
from pathlib import Path

import cv2
import numpy as np

from .config import RAIZ

log = logging.getLogger(__name__)

_URL_YUNET = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/"
    "face_detection_yunet/face_detection_yunet_2023mar.onnx")
_URL_SFACE = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/"
    "face_recognition_sface/face_recognition_sface_2021dec.onnx")

EMB_DIM = 128    # tamano del embedding de SFace
_SIM_MIN = 0.40  # umbral coseno de SFace (el recomendado por OpenCV: 0.363)
_TIMEOUT_DESCARGA = 30


def _descargar(url: str, destino: Path) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(".tmp")
    log.info("descargando modelo %s -> %s", url.rsplit("/", 1)[-1], destino.name)
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT_DESCARGA) as r, \
                open(tmp, "wb") as f:
            while True:
                trozo = r.read(1 << 20)
                if not trozo:
                    break
                f.write(trozo)
        os.replace(tmp, destino)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def embedding_valido(valores) -> np.ndarray:
    """Vector float32 de EMB_DIM numeros finitos; ValueError si no lo es."""
    try:
        emb = np.asarray(valores, dtype=np.float32).reshape(-1)
    except (TypeError, ValueError) as e:
        raise ValueError("embedding no numerico") from e
    if emb.size != EMB_DIM:
        raise ValueError(f"el embedding debe tener {EMB_DIM} valores")
    if not np.all(np.isfinite(emb)):
        raise ValueError("el embedding contiene NaN o infinitos")
    return emb


class CarasDetector:
    """Combina YuNet (cajas) y SFace (embeddings) sobre el mismo frame."""

    def __init__(self, carpeta: str | Path = RAIZ / "modelos"):
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
        # nombre -> matriz (n_muestras, EMB_DIM) de embeddings normalizados
        self.conocidos: dict[str, np.ndarray] = {}

    # ---------------------------------------------------------------- api
    def set_conocidos(self, conocidos: dict) -> None:
        """Identidades conocidas: nombre -> un embedding o una lista de ellos."""
        nuevos: dict[str, np.ndarray] = {}
        for nombre, muestras in conocidos.items():
            m = np.atleast_2d(np.asarray(muestras, dtype=np.float32))
            m = m[np.all(np.isfinite(m), axis=1)]
            if m.size == 0 or m.shape[1] != EMB_DIM:
                log.warning("rostro %r ignorado: embedding invalido", nombre)
                continue
            norma = np.linalg.norm(m, axis=1, keepdims=True)
            nuevos[nombre] = m / np.maximum(norma, 1e-6)
        self.conocidos = nuevos

    def detectar(self, frame: np.ndarray) -> list[dict]:
        """Caras del frame: {caja, conf, emb}."""
        alto, ancho = frame.shape[:2]
        self._detector.setInputSize((ancho, alto))
        _, caras = self._detector.detect(frame)
        salida: list[dict] = []
        if caras is None:
            return salida
        for cara in caras:
            x1, y1, x2, y2 = [int(v) for v in cara[:4]]
            try:
                alineado = self._recon.alignCrop(frame, cara.reshape(1, -1))
            except Exception:
                continue
            if alineado is None:
                continue
            emb = self._recon.feature(alineado).flatten().astype(np.float32)
            salida.append({"caja": (x1, y1, x2, y2), "conf": float(cara[14]),
                           "emb": emb})
        return salida

    def identificar(self, emb: np.ndarray) -> tuple[str | None, float]:
        """(nombre | None, mejor similitud). Gana la muestra mas parecida."""
        if not self.conocidos:
            return None, 0.0
        emb = np.asarray(emb, dtype=np.float32).reshape(-1)
        if emb.size != EMB_DIM:
            return None, 0.0
        emb = emb / max(float(np.linalg.norm(emb)), 1e-6)
        mejor_nombre, mejor_sim = None, -1.0
        for nombre, muestras in self.conocidos.items():
            sim = float(np.max(muestras @ emb))
            if sim > mejor_sim:
                mejor_nombre, mejor_sim = nombre, sim
        if mejor_sim >= _SIM_MIN:
            return mejor_nombre, mejor_sim
        return None, mejor_sim
