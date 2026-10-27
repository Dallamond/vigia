"""Tests del modulo detector: usa la imagen de ejemplo de OpenCV/una imagen
generada, y exige que YOLO (en CPU si no hay GPU) detecte algo o al menos
no crashee. No requiere webcam."""
import numpy as np
import pytest

from vigia.detector import Deteccion, Detector

CLASES_MINIMAS = {"person", "laptop", "bottle", "backpack", "cell phone"}


def test_modelo_carga():
    d = Detector()
    assert d.nombres() and len(d.nombres()) > 0


def test_clases_objetivo_validas():
    d = Detector()
    nombres = d.nombres()
    for c in CLASES_MINIMAS:
        assert c in nombres.values(), f"falta la clase {c} en el modelo"


def test_deteccion_dataclass():
    det = Deteccion(clase="person", conf=0.9, caja=(0, 0, 10, 10))
    assert det.clase == "person"


def test_detectar_sobre_imagen_sintetica():
    d = Detector()
    # imagen aleatoria: no debe crashear ni romper tipos
    frame = np.full((480, 640, 3), 128, dtype=np.uint8)
    dets = d.detectar(frame, conf_min=0.9)  # umbral alto: ruido filtrado
    assert isinstance(dets, list)
    assert all(isinstance(det, Deteccion) for det in dets)
