"""Tests del modulo de captura (mock de webcam, no requiere camara real)."""
import numpy as np
import pytest
import cv2

from vigia import captura
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


class _Simulador:
    """Webcam que se puede desenchufar y volver a enchufar desde el test."""

    def __init__(self):
        self.disponible = True
        self.creadas = 0

    def __call__(self, fuente, backend=None):
        self.creadas += 1
        return _CapSimulada(self)


class _CapSimulada:
    def __init__(self, sim):
        self.sim = sim

    def isOpened(self):
        return self.sim.disponible

    def read(self):
        if not self.sim.disponible:
            return False, None
        return True, np.zeros((240, 320, 3), dtype=np.uint8)

    def set(self, *_a):
        return True

    def release(self):
        pass


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


@pytest.fixture
def sim_cv(monkeypatch):
    """cv2 falso con una camara que podemos caer y recuperar."""
    sim = _Simulador()
    fake = type("FakeCV", (), {"VideoCapture": staticmethod(sim),
                               "CAP_PROP_FOURCC": 6,
                               "CAP_PROP_FRAME_WIDTH": 3,
                               "CAP_PROP_FRAME_HEIGHT": 4,
                               "VideoWriter_fourcc": staticmethod(lambda *a: 0)})
    monkeypatch.setattr("vigia.captura.cv2", fake)
    # sin esperas reales: los tests deben ser instantaneos
    monkeypatch.setattr("vigia.captura.time.sleep", lambda *_: None)
    return sim


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


def test_camara_no_disponible_sin_crash(sim_cv):
    sim_cv.disponible = False
    cam = Camara(abrir=False, reintento_segundos=0)
    ok, frame = cam.leer()
    assert ok is False and frame is None


def test_liberar(fake_cv):
    cam = Camara(abrir=True)
    cam.liberar()
    assert not cam.abierta


def test_se_reconecta_sola_tras_caida(sim_cv):
    cam = Camara(abrir=True, reintento_segundos=0)
    assert cam.abierta

    sim_cv.disponible = False  # se desenchufa
    assert cam.leer() == (False, None)
    assert not cam.abierta

    intentos_previos = sim_cv.creadas
    assert cam.leer() == (False, None)  # debe volver a intentar abrir
    assert sim_cv.creadas > intentos_previos, "no reintento abrir la camara"

    sim_cv.disponible = True  # se vuelve a enchufar
    ok, frame = cam.leer()
    assert ok and frame is not None
    assert cam.abierta


def test_no_deja_captura_colgada_al_fallar_apertura(sim_cv):
    sim_cv.disponible = False
    cam = Camara(abrir=False, reintento_segundos=0)
    assert cam.abrir() is False
    assert cam.cap is None
    assert cam.abierta is False


def test_abrir_bloqueante_admite_timeout(sim_cv):
    sim_cv.disponible = False
    cam = Camara(abrir=False, reintento_segundos=0)
    assert cam.abrir_bloqueante(timeout=0.05) is False


def test_detectar_camaras_prueba_solo_las_libres(monkeypatch):
    from vigia import captura  # noqa: F811 (ya importado arriba)

    abiertas = []

    class Cap:
        def __init__(self, i, backend):
            self.i = i
            abiertas.append(i)

        def isOpened(self):
            return self.i in (0, 2)

        def read(self):
            return True, None

        def release(self):
            pass

    monkeypatch.setattr(captura.cv2, "VideoCapture", Cap)
    res = captura.detectar_camaras(maximo=4, ocupada=0)
    assert [c["indice"] for c in res] == [0, 2]
    assert 0 not in abiertas  # la que esta en uso no se reabre


def test_detectar_camaras_tolera_release_roto(monkeypatch):
    """Algunos drivers lanzan cv2.error al liberar: /camaras no debe caerse."""
    class Cap:
        def __init__(self, i, backend):
            self.i = i

        def isOpened(self):
            return False

        def read(self):
            return False, None

        def release(self):
            raise cv2.error("release roto")

    monkeypatch.setattr(captura.cv2, "VideoCapture", Cap)
    assert captura.detectar_camaras(maximo=3) == []


def test_detectar_camaras_tolera_apertura_rota(monkeypatch):
    def revienta(i, backend):
        raise cv2.error("no puedo abrir")

    monkeypatch.setattr(captura.cv2, "VideoCapture", revienta)
    assert captura.detectar_camaras(maximo=2) == []


# -- lector asincrono --------------------------------------------------------
import threading as _threading  # noqa: E402

from vigia.captura import LectorAsincrono  # noqa: E402


class _CamaraLenta:
    """Camara falsa: cada leer() tarda un poco y numera los frames."""

    fuente = 0
    ancho, alto = 4, 4

    def __init__(self):
        self.n = 0
        self.liberada = False

    def leer(self):
        import time as _t
        _t.sleep(0.005)
        self.n += 1
        return True, np.full((4, 4, 3), self.n % 255, np.uint8)

    def cambiar(self, fuente):
        self.fuente = fuente
        return True

    def liberar(self):
        self.liberada = True


def test_lector_asincrono_entrega_siempre_el_ultimo_frame():
    cam = _CamaraLenta()
    lector = LectorAsincrono(cam)
    try:
        ok1, f1 = lector.leer()
        import time as _t
        _t.sleep(0.1)  # el bucle "lento": la camara sigue leyendo mientras tanto
        ok2, f2 = lector.leer()
        assert ok1 and ok2
        assert int(f2[0, 0, 0]) > int(f1[0, 0, 0]) + 1  # salto: se descartaron
    finally:
        lector.liberar()
    assert cam.liberada


def test_lector_asincrono_no_repite_el_mismo_frame():
    class _Congelada(_CamaraLenta):
        def leer(self):
            import time as _t
            _t.sleep(0.05)
            return super().leer()

    lector = LectorAsincrono(_Congelada(), espera_seg=0.001)
    try:
        lector.leer()
        resultados = [lector.leer()[0] for _ in range(3)]
        assert False in resultados  # sin frame nuevo => (False, None), sin bloquear
    finally:
        lector.liberar()


def test_lector_asincrono_delega_cambiar_y_fuente():
    cam = _CamaraLenta()
    lector = LectorAsincrono(cam)
    try:
        assert lector.cambiar(2) is True
        assert lector.fuente == 2
    finally:
        lector.liberar()
