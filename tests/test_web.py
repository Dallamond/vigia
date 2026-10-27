"""Tests del dashboard web: no requieren camara ni modelo real."""
import threading
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

import vigia.web as web
from vigia.detector import Deteccion
from vigia.web import Estado


@pytest.fixture(scope="module")
def cliente():
    return TestClient(web.app)


class _CamaraFalsa:
    def __init__(self, disponible=True):
        self.disponible = disponible
        self.alto, self.ancho = 240, 320
        self.lecturas = 0

    def leer(self):
        self.lecturas += 1
        if not self.disponible:
            return False, None
        return True, np.zeros((self.alto, self.ancho, 3), np.uint8)


class _DetectorFalso:
    def __init__(self, error=False):
        self.error = error
        self.llamadas = 0

    def detectar_track(self, frame):
        self.llamadas += 1
        if self.error:
            raise RuntimeError("fallo simulado de inferencia")
        return [], {}


def _lanzar_bucle(estado, cam, detector, parar):
    hilo = threading.Thread(
        target=web.bucle_procesamiento,
        args=(estado,),
        kwargs={"cam": cam, "detector": detector, "parar": parar},
        daemon=True)
    hilo.start()
    return hilo


def test_index_carga(cliente):
    r = cliente.get("/")
    assert r.status_code == 200
    assert "Vigia" in r.text


def test_estado_tiene_el_formato_esperado(cliente):
    r = cliente.get("/estado")
    assert r.status_code == 200
    datos = r.json()
    assert set(datos) == {"fps", "camara_ok", "personas", "objetos"}
    assert datos["fps"] >= 0
    assert isinstance(datos["personas"], int)


def test_eventos_tiene_el_formato_esperado(cliente):
    r = cliente.get("/eventos")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_estado_cuenta_personas_y_objetos():
    estado = Estado()
    estado.actualizar(
        b"jpeg", 12.5,
        [Deteccion("person", 0.9, (0, 0, 10, 10)),
         Deteccion("laptop", 0.8, (0, 0, 10, 10))],
        {7}, True)
    _, fps, dets, ids, ok = estado.snapshot()
    assert fps == 12.5 and ok and len(dets) == 2 and ids == {7}


def test_snapshot_devuelve_copias():
    estado = Estado()
    estado.actualizar(b"", 0.0, [Deteccion("person", 0.9, (0, 0, 1, 1))],
                      {3}, True)
    _, _, dets, ids, _ = estado.snapshot()
    dets.clear()
    ids.clear()
    _, _, dets2, ids2, _ = estado.snapshot()
    assert len(dets2) == 1 and ids2 == {3}


def test_anadir_eventos_conserva_orden_reciente_primero():
    estado = Estado()
    estado.anadir_eventos((("persona_entra", 1),))
    estado.anadir_eventos((("persona_salio", 1),))
    assert [tipo for _, tipo, _ in estado.eventos] == ["persona_salio",
                                                      "persona_entra"]


def test_jpeg_es_una_imagen_valida():
    frame = np.zeros((48, 64, 3), np.uint8)
    jpeg = web._jpeg(frame)
    assert jpeg[:2] == b"\xff\xd8"


def test_bucle_sobrevive_a_un_error_de_inferencia():
    estado = Estado()
    detector = _DetectorFalso(error=True)
    parar = threading.Event()
    hilo = _lanzar_bucle(estado, _CamaraFalsa(), detector, parar)
    try:
        limite = time.monotonic() + 5
        while detector.llamadas < 2 and time.monotonic() < limite:
            time.sleep(0.02)
    finally:
        parar.set()
        hilo.join(2)

    assert not hilo.is_alive(), "el hilo murio con la excepcion"
    assert detector.llamadas >= 2, "dejo de procesar frames tras el error"
    assert estado.snapshot()[4] is False


def test_bucle_marca_camara_ok_con_frame_valido():
    estado = Estado()
    parar = threading.Event()
    hilo = _lanzar_bucle(estado, _CamaraFalsa(), _DetectorFalso(), parar)
    try:
        limite = time.monotonic() + 5
        while not estado.snapshot()[4] and time.monotonic() < limite:
            time.sleep(0.02)
    finally:
        parar.set()
        hilo.join(2)

    jpeg, _, _, _, camara_ok = estado.snapshot()
    assert camara_ok is True
    assert jpeg


def test_bucle_avisa_si_la_camara_esta_caida():
    estado = Estado()
    parar = threading.Event()
    hilo = _lanzar_bucle(estado, _CamaraFalsa(disponible=False),
                         _DetectorFalso(), parar)
    try:
        limite = time.monotonic() + 5
        while estado.snapshot()[0] is None and time.monotonic() < limite:
            time.sleep(0.02)
    finally:
        parar.set()
        hilo.join(2)

    jpeg, _, _, _, camara_ok = estado.snapshot()
    assert camara_ok is False
    assert jpeg
