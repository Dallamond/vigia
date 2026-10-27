"""Tests del modulo de captura (mock de webcam, no requiere camara real)."""
import numpy as np
import pytest

from vigia.captura import Camara


class _FakeCapture:
    """Simula cv2.VideoCapture: 10 frames y luego devuelve False."""

    def __init__(self, fuente, backend=None):
        self.fuente = fuente
        self.abierta = fuente == 0
        self._frames = 10

    def isOpened(self):
        return self.abierta

    def read(self):
        if not self.abierta or self._frames <= 0:
            return False, None
        self._frames -= 1
        return True, np.zeros((240, 320, 3), dtype=np.uint8)

    def set(self, *_a):
        return True

    def release(self):
        self.abierta = False


@pytest.fixture
def fake_cv(monkeypatch):
    fake = type("FakeCV", (), {"VideoCapture": staticmethod(_FakeCapture),
                               "CAP_PROP_FRAME_WIDTH": 3,
                               "CAP_PROP_FRAME_HEIGHT": 4,
                               "CAP_PROP_FPS": 5,
                               "CAP_PROP_FOURCC": 6,
                               "VideoWriter_fourcc": staticmethod(lambda *a: 0)})
    monkeypatch.setattr("vigia.captura.cv2", fake)
    return fake


def test_camara_se_abre_con_indice(fake_cv):
    cam = Camara(abrir=True)
    assert cam.abierta


def test_leer_devuelve_frames(fake_cv):
    cam = Camara(abrir=True)
    ok, frame = cam.leer()
    assert ok and frame is not None
    assert frame.shape == (240, 320, 3)


def test_leer_devuelve_none_al_acabarse(fake_cv):
    cam = Camara(abrir=True)
    for _ in range(10):
        cam.leer()
    ok, frame = cam.leer()
    assert ok is False and frame is None


def test_camara_no_disponible_sin_crash(fake_cv):
    cam = Camara(abrir=True)
    cam.liberar()
    ok, frame = cam.leer()
    assert ok is False and frame is None


def test_liberar(fake_cv):
    cam = Camara(abrir=True)
    cam.liberar()
    assert not cam.abierta
