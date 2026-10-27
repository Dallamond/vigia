"""Tests del modulo detector: usa la imagen de ejemplo de OpenCV/una imagen
generada, y exige que YOLO (en CPU si no hay GPU) detecte algo o al menos
no crashee. No requiere webcam."""
import numpy as np
import pytest

from vigia.detector import Deteccion, Detector

CLASES_MINIMAS = {"person", "laptop", "bottle", "backpack", "cell phone"}


@pytest.fixture(scope="session")
def detector():
    """Carga el modelo una sola vez para toda la sesion de tests."""
    return Detector()


def test_modelo_carga(detector):
    assert detector.nombres() and len(detector.nombres()) > 0


def test_clases_objetivo_validas(detector):
    nombres = detector.nombres()
    for c in CLASES_MINIMAS:
        assert c in nombres.values(), f"falta la clase {c} en el modelo"


def test_deteccion_dataclass():
    det = Deteccion(clase="person", conf=0.9, caja=(0, 0, 10, 10))
    assert det.clase == "person"


def test_detectar_sobre_imagen_sintetica(detector):
    # imagen aleatoria: no debe crashear ni romper tipos
    frame = np.full((480, 640, 3), 128, dtype=np.uint8)
    dets = detector.detectar(frame, conf_min=0.9)  # umbral alto: ruido filtrado
    assert isinstance(dets, list)
    assert all(isinstance(det, Deteccion) for det in dets)


def test_set_clases_traduce_nombres_a_ids():
    d = Detector()
    d.set_clases(["person", "laptop"])
    assert d.clases == sorted([0, 63])


def test_set_clases_ignora_nombres_desconocidos():
    d = Detector()
    d.set_clases(["person", "unicornio"])
    assert d.clases == [0]


def test_set_clases_none_permite_todas():
    d = Detector()
    d.set_clases(["person"])
    d.set_clases(None)
    assert d.clases is None
